import json

from conftest import FakeLLM


def test_order_flow_saves_confirmed_order(make_client) -> None:
    llm = FakeLLM(
        json.dumps(
            {
                "intent": "order",
                "items": [
                    {"id": "butter-chicken", "quantity": 2},
                    {"id": "garlic-naan", "quantity": 1},
                ],
                "reply": "",
            }
        )
    )
    client = make_client(llm)

    def say(text: str) -> str:
        response = client.post("/api/chat", json={"session_id": "s1", "message": text})
        assert response.status_code == 200
        return response.json()["reply"]

    assert "Saffron Lane Kitchen" in say("hi")
    assert "Butter Chicken" in say("2 butter chicken and a garlic naan please")
    assert "name" in say("done")
    assert "address" in say("Alex")
    summary = say("12 Test Street")
    assert "$35.40" in summary  # 2 × 15.95 + 3.50, free delivery over $30
    assert "12 Test Street" in summary
    assert "Order #1 confirmed" in say("yes")

    assert llm.calls == ["2 butter chicken and a garlic naan please"]
    [order] = client.app.state.store.list_orders()
    assert (order.name, order.address, order.total_cents) == ("Alex", "12 Test Street", 3540)
    assert [(i["id"], i["quantity"]) for i in order.items] == [
        ("butter-chicken", 2),
        ("garlic-naan", 1),
    ]
    assert "Alex" in client.get("/orders").text


def _start_cart(make_client):
    llm = FakeLLM(
        json.dumps({"intent": "order", "items": [{"id": "samosa", "quantity": 1}], "reply": ""})
    )
    client = make_client(llm)

    def say(text: str) -> str:
        return client.post("/api/chat", json={"session_id": "s1", "message": text}).json()["reply"]

    say("order")
    say("one samosa")
    return client, say


def test_natural_wording_finishes_items(make_client) -> None:
    _, say = _start_cart(make_client)
    assert "name" in say("Yeah I think I don't actually need anything else")


def test_natural_wording_confirms_order(make_client) -> None:
    client, say = _start_cart(make_client)
    say("that's all")
    say("Alex")
    say("12 Test Street")
    assert "confirmed" in say("yeah go ahead")
    assert len(client.app.state.store.list_orders()) == 1


def test_name_step_extracts_only_the_name(make_client) -> None:
    _, say = _start_cart(make_client)
    say("that's all")
    assert say("I am Tony Stark") == "Thanks, Tony Stark! What's the delivery address?"

    _, say = _start_cart(make_client)
    say("that's all")
    assert say("Go with Bruce Banner") == "Thanks, Bruce Banner! What's the delivery address?"


def test_address_step_asks_again_when_not_an_address(make_client) -> None:
    _, say = _start_cart(make_client)
    say("that's all")
    say("Tony Stark")
    assert "full address" in say("I live with Tony Stark")
    summary = say("I live at 10880 Malibu Point")
    assert "Deliver to: 10880 Malibu Point" in summary


def test_confirm_intent_during_items_asks_for_name_without_placing_order(make_client) -> None:
    """A "confirm" intent from the LLM during items must not place an order -
    only the confirm step (after name/address) can do that."""
    client, say = _start_cart(make_client)
    client.app.state.engine.llm.responses.append(
        json.dumps(
            {
                "intent": "confirm",
                "items": [],
                "reply": "Your order has been placed and will be delivered shortly!",
            }
        )
    )
    assert "name" in say("confirm my order")
    assert client.app.state.store.list_orders() == []


def test_llm_reply_claiming_order_placed_is_replaced_in_idle(make_client) -> None:
    llm = FakeLLM(
        json.dumps(
            {
                "intent": "other",
                "items": [],
                "reply": "Your order has been placed and will be delivered shortly!",
            }
        )
    )
    client = make_client(llm)
    reply = client.post(
        "/api/chat", json={"session_id": "s1", "message": "is my food coming"}
    ).json()["reply"]
    assert "placed" not in reply
    assert "delivered" not in reply
