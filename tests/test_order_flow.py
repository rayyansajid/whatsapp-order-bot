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
