import base64
import hashlib
import hmac
import json
import time

from agents.keys_agent import get_key

SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _sign(payload_b64: bytes) -> str:
    secret = get_key("SESSION_SECRET_KEY").encode()
    return hmac.new(secret, payload_b64, hashlib.sha256).hexdigest()


def create_session_token(client_id: int) -> str:
    """HMAC-signed session token, stdlib-only (no extra dependency). Format is
    base64(payload_json).signature - the payload carries its own expiry so
    verification doesn't depend on anything but the shared secret."""
    payload = json.dumps({
        "client_id": client_id,
        "exp": int(time.time()) + SESSION_MAX_AGE_SECONDS,
    }).encode()
    payload_b64 = base64.urlsafe_b64encode(payload)
    signature = _sign(payload_b64)
    return f"{payload_b64.decode()}.{signature}"


def verify_session_token(token: str):
    """Returns the client_id if the token is well-formed, correctly signed, and
    not expired - otherwise None. Never raises on malformed/tampered input."""
    if not token or "." not in token:
        return None
    try:
        payload_b64_str, signature = token.split(".", 1)
        payload_b64 = payload_b64_str.encode()
        expected_signature = _sign(payload_b64)
        if not hmac.compare_digest(signature, expected_signature):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("client_id")
    except Exception:
        return None
