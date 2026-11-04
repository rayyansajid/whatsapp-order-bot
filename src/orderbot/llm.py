"""LLM provider interface, the Anthropic provider and an OpenAI-compatible provider."""

import json
import logging
import os
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-5-5"

# Values from the providers' official docs (checked 2026-11-01):
# https://ai.google.dev/gemini-api/docs/openai and https://console.groq.com/docs/openai
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-20b"


class LLMError(RuntimeError):
    pass


class LLMUnavailableError(LLMError):
    """The provider is rate limiting us or timed out. Worth a friendly 'try again' reply."""


class LLM(Protocol):
    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        """Return a JSON string that matches `schema`."""
        ...


class AnthropicLLM:
    """Claude via the Anthropic SDK. The API key is read from ANTHROPIC_API_KEY."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic  # imported lazily so the app starts without a key

            self._client = anthropic.Anthropic()
        return self._client

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        response = self._get_client().messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": schema},
            },
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise LLMError(f"LLM stopped early: {response.stop_reason}")
        text = "".join(b.text for b in response.content if b.type == "text")
        if not text:
            raise LLMError("LLM returned no text")
        return text


class OpenAICompatibleLLM:
    """Any OpenAI-compatible /chat/completions endpoint (Gemini, Groq).

    `json_mode` picks how JSON is requested: "json_object" (Groq's JSON mode) or
    "json_schema" (structured output, which Gemini's compatibility layer documents).
    The schema is always put in the prompt too, and the reply is validated here.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        json_mode: str = "json_object",
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.json_mode = json_mode
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/", timeout=timeout, transport=transport
        )

    def __repr__(self) -> str:  # never show the key
        return f"OpenAICompatibleLLM(base_url={self._client.base_url!s}, model={self.model})"

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        if not self._api_key:
            raise LLMError("No API key configured for the LLM provider")
        if self.json_mode == "json_schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": schema, "strict": True},
            }
        else:
            response_format = {"type": "json_object"}
        prompt = (
            f"{system}\n\nRespond with a single JSON object only, matching this JSON schema:\n"
            f"{json.dumps(schema)}"
        )
        try:
            resp = self._client.post(
                "chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user},
                    ],
                    "response_format": response_format,
                    "temperature": 0,
                },
            )
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError("LLM request timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc

        if resp.status_code == 429:
            raise LLMUnavailableError("LLM rate limit reached")
        if resp.status_code >= 400:
            raise LLMError(f"LLM returned HTTP {resp.status_code}")

        try:
            text = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(text)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM returned a malformed response") from exc
        _validate(data, schema)
        return json.dumps(data)


def _validate(data: Any, schema: dict[str, Any]) -> None:
    """Minimal check against the schema subset the engine uses (object/array/string/integer)."""
    kind = schema.get("type")
    ok = {
        "object": isinstance(data, dict),
        "array": isinstance(data, list),
        "string": isinstance(data, str),
        "integer": isinstance(data, int) and not isinstance(data, bool),
    }.get(kind, True)
    if not ok:
        raise LLMError(f"LLM JSON has wrong type, expected {kind}")
    if "enum" in schema and data not in schema["enum"]:
        raise LLMError("LLM JSON value not in enum")
    if kind == "object":
        for key in schema.get("required", []):
            if key not in data:
                raise LLMError(f"LLM JSON missing field {key!r}")
        for key, sub in schema.get("properties", {}).items():
            if key in data:
                _validate(data[key], sub)
    elif kind == "array" and "items" in schema:
        for item in data:
            _validate(item, schema["items"])


def llm_from_env() -> LLM:
    """Build the provider chosen by LLM_PROVIDER (gemini | groq | anthropic, default gemini).

    A missing key doesn't stop the app: the call raises LLMError and the engine falls back.
    """
    env = os.environ.get
    provider = (env("LLM_PROVIDER") or "gemini").strip().lower()
    override = env("LLM_MODEL") or None
    if provider == "anthropic":
        log.info("LLM provider active: anthropic")
        return AnthropicLLM(model=override)
    if provider == "groq":
        log.info("LLM provider active: groq")
        return OpenAICompatibleLLM(
            GROQ_BASE_URL, override or GROQ_DEFAULT_MODEL, env("GROQ_API_KEY", "")
        )
    if provider != "gemini":
        log.warning("Unknown LLM_PROVIDER %r, using gemini", provider)
    log.info("LLM provider active: gemini")
    return OpenAICompatibleLLM(
        GEMINI_BASE_URL,
        override or GEMINI_DEFAULT_MODEL,
        env("GEMINI_API_KEY", ""),
        json_mode="json_schema",
    )
