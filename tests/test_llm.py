import json

import httpx
import pytest

from orderbot.engine import INTERPRET_SCHEMA
from orderbot.llm import LLMError, LLMUnavailableError, OpenAICompatibleLLM


def make_llm(content: str) -> OpenAICompatibleLLM:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return OpenAICompatibleLLM(
        "https://example.test/v1", "test-model", "test-key", transport=httpx.MockTransport(handler)
    )


def test_parses_valid_json_response():
    content = json.dumps(
        {"intent": "order", "items": [{"id": "butter-chicken", "quantity": 2}], "reply": ""}
    )
    result = json.loads(
        make_llm(content).complete_json("sys", "2 butter chicken", INTERPRET_SCHEMA)
    )
    assert result["items"] == [{"id": "butter-chicken", "quantity": 2}]


def test_invalid_response_raises_llm_error():
    with pytest.raises(LLMError):
        make_llm('{"intent": "order"}').complete_json("sys", "hi", INTERPRET_SCHEMA)


def test_429_then_success_retries_and_returns_result(monkeypatch):
    monkeypatch.setattr("orderbot.llm.time.sleep", lambda _seconds: None)
    content = json.dumps({"intent": "order", "items": [], "reply": ""})
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    llm = OpenAICompatibleLLM(
        "https://example.test/v1",
        "test-model",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    result = json.loads(llm.complete_json("sys", "hi", INTERPRET_SCHEMA))
    assert result["intent"] == "order"
    assert calls["count"] == 2


def test_repeated_429s_raise_llm_unavailable_error(monkeypatch):
    monkeypatch.setattr("orderbot.llm.time.sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    llm = OpenAICompatibleLLM(
        "https://example.test/v1",
        "test-model",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMUnavailableError):
        llm.complete_json("sys", "hi", INTERPRET_SCHEMA)
