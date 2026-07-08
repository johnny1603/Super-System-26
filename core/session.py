import base64
import hashlib
import hmac
import json
import time

from agents.keys_agent import get_key

SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days
OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60


def _sign(payload_b64: bytes) -> str:
    secret = get_key("SESSION_SECRET_KEY").encode()
    return hmac.new(secret, payload_b64, hashlib.sha256).hexdigest()


def _sign_oauth_state(payload_b64: bytes) -> str:
    # Derived secret, NOT the raw session secret — an OAuth state parameter is
    # visible in URLs/logs and must never verify as a session cookie (or vice versa)
    secret = hashlib.sha256(get_key("SESSION_SECRET_KEY").encode() + b":oauth-state").digest()
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
    return _verify(token, _sign)


def create_oauth_state_token(client_id: int) -> str:
    """Short-lived CSRF state for OAuth redirects. Carries the client_id so the
    callback doesn't have to rely on the session cookie surviving the round-trip
    through the provider's consent screen."""
    payload = json.dumps({
        "client_id": client_id,
        "exp": int(time.time()) + OAUTH_STATE_MAX_AGE_SECONDS,
    }).encode()
    payload_b64 = base64.urlsafe_b64encode(payload)
    return f"{payload_b64.decode()}.{_sign_oauth_state(payload_b64)}"


def verify_oauth_state_token(token: str):
    """Returns the client_id from a valid, unexpired OAuth state token - otherwise None."""
    return _verify(token, _sign_oauth_state)


def _verify(token: str, sign_fn):
    if not token or "." not in token:
        return None
    try:
        payload_b64_str, signature = token.split(".", 1)
        payload_b64 = payload_b64_str.encode()
        expected_signature = sign_fn(payload_b64)
        if not hmac.compare_digest(signature, expected_signature):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("client_id")
    except Exception:
        return None
