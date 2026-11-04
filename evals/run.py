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
from orderbot.llm import LLMError, llm_from_env
from orderbot.store import OrderStore

CASES_DIR = Path(__file__).parent / "cases"
RESULTS_DIR = Path(__file__).parent / "results"
DELAY_SECONDS = 2.0  # stays under free-tier rate limits for Gemini/Groq

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
    try:
        raw = engine.llm.complete_json(system, case.message, INTERPRET_SCHEMA)
        data = json.loads(raw)
    except LLMError as exc:
        return CaseResult(case, "", [], error=str(exc))
    return CaseResult(case, str(data.get("intent") or ""), data.get("items") or [])


def render_report(provider: str, results_by_language: dict[str, list[CaseResult]]) -> str:
    lines = [f"# Language eval — {provider} — {date.today().isoformat()}", ""]
    lines.append(NATIVE_REVIEW_NOTE)
    lines.append("")
    lines.append("## Accuracy by language")
    lines.append("")
    lines.append("| Language | Passed | Total | Accuracy |")
    lines.append("| --- | --- | --- | --- |")
    all_misses: list[tuple[str, CaseResult]] = []
    for language, results in results_by_language.items():
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        accuracy = (passed / total * 100) if total else 0.0
        lines.append(f"| {language} | {passed} | {total} | {accuracy:.0f}% |")
        all_misses.extend((language, r) for r in results if not r.passed)

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
                time.sleep(DELAY_SECONDS)
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
