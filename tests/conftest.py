import pytest
from fastapi.testclient import TestClient

from orderbot.config import Settings
from orderbot.main import create_app


class FakeLLM:
    """Returns canned JSON responses in order and records the prompts it saw."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def complete_json(self, system: str, user: str, schema: dict) -> str:
        self.calls.append(user)
        return self.responses.pop(0)


@pytest.fixture
def make_client(tmp_path):
    def _make(llm: FakeLLM | None = None, **settings) -> TestClient:
        cfg = Settings(db_path=str(tmp_path / "orders.db"), **settings)
        return TestClient(create_app(cfg, llm or FakeLLM()))

    return _make
