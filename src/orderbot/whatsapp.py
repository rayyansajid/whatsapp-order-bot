"""WhatsApp Cloud API helpers."""

import hashlib
import hmac
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v21.0"


def signature_is_valid(app_secret: str, body: bytes, header: str | None) -> bool:
    """Check Meta's X-Hub-Signature-256 header (sha256 HMAC of the raw body)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def extract_text_messages(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (message_id, sender, text) for each incoming text message."""
    found = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            for msg in (change.get("value") or {}).get("messages") or []:
                if msg.get("type") == "text" and msg.get("from"):
                    body = (msg.get("text") or {}).get("body", "")
                    found.append((msg.get("id", ""), msg["from"], body))
    return found


def send_text(token: str, phone_id: str, to: str, body: str) -> None:
    if not token or not phone_id:
        log.warning("WHATSAPP_TOKEN or WHATSAPP_PHONE_ID not set; reply not sent")
        return
    response = httpx.post(
        f"{GRAPH_URL}/{phone_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        },
        timeout=10,
    )
    if response.is_error:
        log.error("WhatsApp send failed: %s %s", response.status_code, response.text)
