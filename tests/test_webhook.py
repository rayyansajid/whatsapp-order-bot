from orderbot import whatsapp


def test_extract_text_messages_parses_incoming_payload_and_ignores_status_updates() -> None:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "102290129340398",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550783881",
                                "phone_number_id": "106540352242922",
                            },
                            "contacts": [
                                {"profile": {"name": "Sheena Nelson"}, "wa_id": "16505551234"}
                            ],
                            "messages": [
                                {
                                    "from": "16505551234",
                                    "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5",
                                    "timestamp": "1749416383",
                                    "type": "text",
                                    "text": {"body": "Does it come in another color?"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            },
            {
                "id": "102290129340398",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550783881",
                                "phone_number_id": "106540352242922",
                            },
                            "statuses": [
                                {
                                    "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgARGBI3MTE5MjVB",
                                    "status": "delivered",
                                    "timestamp": "1750263773",
                                    "recipient_id": "16505551234",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            },
        ],
    }

    result = whatsapp.extract_text_messages(payload)

    assert result == [
        (
            "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5",
            "16505551234",
            "Does it come in another color?",
        )
    ]


def test_webhook_verification(make_client) -> None:
    client = make_client(verify_token="s3cret")
    params = {"hub.mode": "subscribe", "hub.verify_token": "s3cret", "hub.challenge": "12345"}

    ok = client.get("/webhook", params=params)
    assert ok.status_code == 200
    assert ok.text == "12345"

    bad = client.get("/webhook", params={**params, "hub.verify_token": "wrong"})
    assert bad.status_code == 403
