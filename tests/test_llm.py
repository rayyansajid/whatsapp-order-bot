import json

import httpx
import pytest

from orderbot.engine import INTERPRET_SCHEMA
from orderbot.llm import LLMError, OpenAICompatibleLLM


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
