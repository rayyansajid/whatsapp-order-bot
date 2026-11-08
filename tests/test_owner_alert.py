import json
import threading

from conftest import FakeLLM

from orderbot import whatsapp


def _place_order(client) -> None:
    def say(text: str) -> str:
        return client.post("/api/chat", json={"session_id": "s1", "message": text}).json()["reply"]

    say("hi")
    say("2 butter chicken")
    say("done")
    say("Alex")
    say("12 Test Street")
    say("yes")


def _order_llm() -> FakeLLM:
    return FakeLLM(
        json.dumps(
            {
                "intent": "order",
                "items": [{"id": "butter-chicken", "quantity": 2}],
                "notes": "",
                "is_allergy": False,
                "reply": "",
            }
        )
    )


def test_owner_alert_sent_when_number_configured(make_client, monkeypatch) -> None:
    calls = []
    sent = threading.Event()

    def fake_send_text(token: str, phone_id: str, to: str, body: str) -> None:
        calls.append((token, phone_id, to, body))
        sent.set()

    monkeypatch.setattr(whatsapp, "send_text", fake_send_text)

    client = make_client(
        _order_llm(),
        owner_whatsapp_number="19998887777",
        whatsapp_token="tok",
        whatsapp_phone_id="pid",
    )
    _place_order(client)

    assert sent.wait(timeout=2)
    token, phone_id, to, body = calls[0]
    assert (token, phone_id, to) == ("tok", "pid", "19998887777")
    assert "New order #1" in body
    assert "2 × Butter Chicken" in body
    assert "Alex" in body
    assert "12 Test Street" in body


def test_owner_alert_not_sent_when_number_unset(make_client, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(whatsapp, "send_text", lambda *a: calls.append(a))

    client = make_client(_order_llm())
    _place_order(client)

    assert calls == []
