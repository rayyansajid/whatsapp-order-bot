"""LLM provider interface and the Anthropic implementation."""

import os
from typing import Any, Protocol

DEFAULT_MODEL = "claude-haiku-5-5"


class LLMError(RuntimeError):
    pass


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
