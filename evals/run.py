"""Run the labeled messages in evals/cases/ through a real LLM provider and
score intent + item extraction against the current menu.

Usage: uv run python -m evals.run --provider gemini|groq|anthropic

Calls a real provider (needs the matching API key in the environment, e.g.
via `uv run --env-file .env`), so this is kept out of the pytest suite.
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from orderbot.catalog import Catalog
from orderbot.config import Settings
from orderbot.engine import INTERPRET_SCHEMA, ConversationEngine, Session
from orderbot.llm import LLMError, LLMUnavailableError, llm_from_env
from orderbot.store import OrderStore

CASES_DIR = Path(__file__).parent / "cases"
RESULTS_DIR = Path(__file__).parent / "results"
DEFAULT_DELAY_SECONDS = 2.0  # stays under free-tier rate limits for Gemini/Groq

RATE_LIMIT_WAIT_SECONDS = 30.0
RATE_LIMIT_RETRIES = 3

NATIVE_REVIEW_NOTE = (
    "**Needs native review before trusting these scores:** Urdu and Roman Urdu cases "
    "(native Urdu speaker), and Arabic, French and Spanish cases (native check needed)."
)


@dataclass
class Case:
    message: str
    expected_intent: str
    expected_items: list[dict]


@dataclass
class CaseResult:
    case: Case
    actual_intent: str
    actual_items: list[dict]
    error: str = ""
    rate_limited: bool = False
    prompt_chars: int = 0

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        return self.actual_intent == self.case.expected_intent and _same_items(
            self.case.expected_items, self.actual_items
        )


def _same_items(expected: list[dict], actual: list[dict]) -> bool:
    def key(items: list[dict]) -> list[tuple[str, int]]:
        return sorted((str(i.get("id")), int(i.get("quantity", 0))) for i in items)

    return key(expected) == key(actual)


def load_cases(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [
        Case(c["message"], c["expected"]["intent"], c["expected"].get("items") or []) for c in raw
    ]


def run_case(engine: ConversationEngine, case: Case) -> CaseResult:
    # A fresh "items" session per case: these messages are evaluated independently,
    # not as a multi-turn conversation, and that's the step order/finish/question
    # parsing matters for.
    system = engine._system_prompt(Session(state="items"))
    prompt_chars = len(system) + len(case.message)
    attempt = 0
    while True:
        try:
            raw = engine.llm.complete_json(system, case.message, INTERPRET_SCHEMA)
            data = json.loads(raw)
        except LLMUnavailableError as exc:
            if attempt >= RATE_LIMIT_RETRIES:
                return CaseResult(
                    case, "", [], error=str(exc), rate_limited=True, prompt_chars=prompt_chars
                )
            attempt += 1
            print(
                f"  rate limited, waiting {RATE_LIMIT_WAIT_SECONDS:.0f}s "
                f"(attempt {attempt}/{RATE_LIMIT_RETRIES})"
            )
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
            continue
        except LLMError as exc:
            return CaseResult(case, "", [], error=str(exc), prompt_chars=prompt_chars)
        return CaseResult(
            case,
            str(data.get("intent") or ""),
            data.get("items") or [],
            prompt_chars=prompt_chars,
        )


def render_report(provider: str, results_by_language: dict[str, list[CaseResult]]) -> str:
    lines = [f"# Language eval — {provider} — {date.today().isoformat()}", ""]
    lines.append(NATIVE_REVIEW_NOTE)
    lines.append("")

    all_results = [r for results in results_by_language.values() for r in results]
    scored = [r for r in all_results if not r.rate_limited]
    if scored:
        avg_prompt_chars = sum(r.prompt_chars for r in scored) / len(scored)
        lines.append(
            f"Approximate prompt size: ~{avg_prompt_chars:.0f} characters/call "
            f"(~{avg_prompt_chars / 4:.0f} tokens, rough estimate at 4 chars/token)."
        )
        lines.append("")

    lines.append("## Accuracy by language")
    lines.append("")
    lines.append("| Language | Passed | Scored | Accuracy | Rate-limited (not scored) |")
    lines.append("| --- | --- | --- | --- | --- |")
    all_misses: list[tuple[str, CaseResult]] = []
    all_rate_limited: list[tuple[str, CaseResult]] = []
    for language, results in results_by_language.items():
        rate_limited = [r for r in results if r.rate_limited]
        scored_results = [r for r in results if not r.rate_limited]
        passed = sum(1 for r in scored_results if r.passed)
        total = len(scored_results)
        accuracy = (passed / total * 100) if total else 0.0
        lines.append(f"| {language} | {passed} | {total} | {accuracy:.0f}% | {len(rate_limited)} |")
        all_misses.extend((language, r) for r in scored_results if not r.passed)
        all_rate_limited.extend((language, r) for r in rate_limited)

    lines.append("")
    lines.append("## Rate-limited (not scored)")
    lines.append("")
    if not all_rate_limited:
        lines.append("None.")
    for language, result in all_rate_limited:
        lines.append(f"- {language}: {result.case.message!r} — gave up after retries")

    lines.append("")
    lines.append("## Misses")
    lines.append("")
    if not all_misses:
        lines.append("None.")
    for language, result in all_misses:
        lines.append(f"### {language}: {result.case.message!r}")
        lines.append(
            f"- Expected: intent={result.case.expected_intent!r} items={result.case.expected_items}"
        )
        if result.error:
            lines.append(f"- Actual: error={result.error!r}")
        else:
            lines.append(f"- Actual: intent={result.actual_intent!r} items={result.actual_items}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=["gemini", "groq", "anthropic"])
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"seconds to wait between cases (default: {DEFAULT_DELAY_SECONDS})",
    )
    args = parser.parse_args(argv)

    # Case messages include Urdu/Arabic text; Windows consoles default to a
    # non-UTF-8 codepage and raise UnicodeEncodeError on them otherwise.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        reconfigure(encoding="utf-8", errors="replace")

    os.environ["LLM_PROVIDER"] = args.provider
    llm = llm_from_env()
    settings = Settings.from_env()
    catalog = Catalog.load(settings.data_dir)
    engine = ConversationEngine(catalog, llm, OrderStore(":memory:"))

    case_files = sorted(CASES_DIR.glob("*.yaml"))
    results_by_language: dict[str, list[CaseResult]] = {}
    for path in case_files:
        cases = load_cases(path)
        results = []
        for i, case in enumerate(cases):
            results.append(run_case(engine, case))
            if i < len(cases) - 1:
                time.sleep(args.delay)
        results_by_language[path.stem] = results

    report = render_report(args.provider, results_by_language)
    print(report)

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"{args.provider}-{date.today().isoformat()}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\nWritten to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
