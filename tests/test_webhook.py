def test_webhook_verification(make_client) -> None:
    client = make_client(verify_token="s3cret")
    params = {"hub.mode": "subscribe", "hub.verify_token": "s3cret", "hub.challenge": "12345"}

    ok = client.get("/webhook", params=params)
    assert ok.status_code == 200
    assert ok.text == "12345"

    bad = client.get("/webhook", params={**params, "hub.verify_token": "wrong"})
    assert bad.status_code == 403
