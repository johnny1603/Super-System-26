import base64
import hashlib
import hmac
import json
import time

from agents.keys_agent import get_key

SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days
OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60
ADMIN_SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # 7 days - shorter than client sessions on purpose


def _sign(payload_b64: bytes) -> str:
    secret = get_key("SESSION_SECRET_KEY").encode()
    return hmac.new(secret, payload_b64, hashlib.sha256).hexdigest()


def _sign_oauth_state(payload_b64: bytes) -> str:
    # Derived secret, NOT the raw session secret — an OAuth state parameter is
    # visible in URLs/logs and must never verify as a session cookie (or vice versa)
    secret = hashlib.sha256(get_key("SESSION_SECRET_KEY").encode() + b":oauth-state").digest()
    return hmac.new(secret, payload_b64, hashlib.sha256).hexdigest()


def _sign_admin(payload_b64: bytes) -> str:
    # Derived secret again: a client session cookie must never verify as an
    # admin session, even though both are signed with the same base secret
    secret = hashlib.sha256(get_key("SESSION_SECRET_KEY").encode() + b":admin-session").digest()
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
    payload = _verify(token, _sign)
    return payload.get("client_id") if payload else None


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
    payload = _verify(token, _sign_oauth_state)
    return payload.get("client_id") if payload else None


def _sign_lead_capture(payload_b64: bytes) -> str:
    # Derived secret again, for the same reason as the other two: this token is
    # pasted into a PUBLIC web page (the client's own site form), so it must
    # never verify as a session or an admin cookie no matter who reads it.
    secret = hashlib.sha256(get_key("SESSION_SECRET_KEY").encode() + b":lead-capture").digest()
    return hmac.new(secret, payload_b64, hashlib.sha256).hexdigest()


def create_lead_capture_token(client_id: int) -> str:
    """Identifies a client to the PUBLIC lead-capture endpoint
    (POST /api/leads/capture/{token}), embedded in a form on their own website.

    Deliberately NON-EXPIRING - unlike every other token here. A form snippet
    pasted into a site is not re-pasted on a schedule, and an expiry would
    silently stop capturing the client's customers one day with no signal. The
    token grants exactly one capability: create a lead row for this client. It
    reads nothing. Rotating it means changing SESSION_SECRET_KEY, which
    rotates every session too - so treat a leaked token as spam exposure
    (bounded by client_leads.MAX_LEADS_PER_DAY), not as account compromise."""
    payload = json.dumps({"client_id": client_id, "purpose": "lead_capture"}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload)
    return f"{payload_b64.decode()}.{_sign_lead_capture(payload_b64)}"


def verify_lead_capture_token(token: str):
    """Returns the client_id for a well-signed capture token, otherwise None.
    Does NOT go through _verify(): that helper rejects a payload without a
    future `exp`, and these tokens deliberately have none."""
    if not token or "." not in token:
        return None
    try:
        payload_b64_str, signature = token.split(".", 1)
        payload_b64 = payload_b64_str.encode()
        if not hmac.compare_digest(signature, _sign_lead_capture(payload_b64)):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if payload.get("purpose") != "lead_capture":
            return None
        return payload.get("client_id")
    except Exception:
        return None


def create_admin_session_token() -> str:
    """Session for the admin dashboard - authenticated by ADMIN_PASSWORD at
    login, then carried in an 'admin_session' cookie."""
    payload = json.dumps({
        "admin": True,
        "exp": int(time.time()) + ADMIN_SESSION_MAX_AGE_SECONDS,
    }).encode()
    payload_b64 = base64.urlsafe_b64encode(payload)
    return f"{payload_b64.decode()}.{_sign_admin(payload_b64)}"


def verify_admin_session_token(token: str) -> bool:
    payload = _verify(token, _sign_admin)
    return bool(payload and payload.get("admin") is True)


def _verify(token: str, sign_fn):
    """Returns the decoded payload dict for a well-signed, unexpired token -
    otherwise None. Never raises on malformed/tampered input."""
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
        return payload
    except Exception:
        return None
