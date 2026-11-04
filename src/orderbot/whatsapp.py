"""WhatsApp Cloud API helpers."""

import hashlib
import hmac
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Checked against developers.facebook.com/docs/graph-api/changelog (2026-11-05):
# v27.0 is the current Graph API version; the live endpoint accepts it (verified with a probe
# request). v21.0 still works but expires 2027-01-21, so this tracks the current version instead.
GRAPH_URL = "https://graph.facebook.com/v27.0"


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
    try:
        response = httpx.post(
            f"{GRAPH_URL}/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": body},
            },
            timeout=10,
        )
    except httpx.HTTPError as exc:
        log.error("WhatsApp send failed: request error (%s)", type(exc).__name__)
        return
    if response.is_error:
        log.error(
            "WhatsApp send failed: HTTP %s %s", response.status_code, _safe_error_message(response)
        )


def _safe_error_message(response: httpx.Response) -> str:
    """Pull just the error message out of Meta's error body (never log headers or the token)."""
    try:
        return str(response.json().get("error", {}).get("message", "unknown error"))
    except ValueError:
        return "non-JSON error response"
