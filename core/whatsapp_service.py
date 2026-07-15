"""WhatsApp sending via Green API — the SOS channel of the notification
ladder (dashboard = ambient, email = important, WhatsApp = can't wait).

Green API model: one INSTANCE = one connected WhatsApp number (Johnny's
existing Green API account; the instance is linked to a phone by QR scan in
their console). Two credentials, both in keys_agent KEYS:
- GREEN_API_INSTANCE_ID   — the idInstance number from the console
- GREEN_API_TOKEN         — the apiTokenInstance for that instance
Optional GREEN_API_BASE_URL — newer instances get a dedicated subdomain
(e.g. https://1103.api.green-api.com), shown in the console next to the
instance; default is the generic host.

SEND DISCIPLINE: this channel is for genuinely urgent, client-facing events
only (failed automation with a customer waiting, urgent approve/reject).
Never wire it to routine notifications — a noisy WhatsApp channel gets
muted and then the real SOS dies with it. Routine belongs in the dashboard
feed and email.

Fails safe: unconfigured or errored sends log + return False — an urgent
notification must never crash the flow that triggered it (the caller
usually also has the dashboard/email fallback).
"""
import os
import re

import httpx

TIMEOUT = 20


def is_configured() -> bool:
    return bool(os.environ.get("GREEN_API_INSTANCE_ID") and os.environ.get("GREEN_API_TOKEN"))


def _chat_id(phone: str) -> str:
    """Israeli-first phone normalization → Green API chatId.
    '050-123-4567' → '972501234567@c.us'; already-international numbers
    (with or without +) pass through."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        raise ValueError("empty phone number")
    if digits.startswith("0"):          # local Israeli format
        digits = "972" + digits[1:]
    return f"{digits}@c.us"


def send_whatsapp(phone: str, message: str) -> bool:
    """Send a text message. Returns False (never raises) when unconfigured,
    the phone is unusable, or Green API errors."""
    if not is_configured():
        print("[whatsapp_service] GREEN_API_INSTANCE_ID/GREEN_API_TOKEN not set — "
              f"WhatsApp NOT sent (message was: {message[:80]}...)")
        return False
    try:
        chat_id = _chat_id(phone)
    except ValueError:
        print("[whatsapp_service] no usable phone number — WhatsApp not sent")
        return False

    instance = os.environ["GREEN_API_INSTANCE_ID"]
    token = os.environ["GREEN_API_TOKEN"]
    base = os.environ.get("GREEN_API_BASE_URL", "https://api.green-api.com").rstrip("/")
    try:
        response = httpx.post(
            f"{base}/waInstance{instance}/sendMessage/{token}",
            json={"chatId": chat_id, "message": message},
            timeout=TIMEOUT,
        )
        if response.status_code != 200:
            print(f"[whatsapp_service] send failed ({response.status_code}): "
                  f"{response.text[:200]}")
            return False
        print(f"[whatsapp_service] WhatsApp sent to {chat_id}")
        return True
    except Exception as e:
        print(f"[whatsapp_service] send error (non-fatal): {e}")
        return False
