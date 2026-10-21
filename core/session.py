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


def create_session_token(client_id: int, session_version: int = 0) -> str:
    """HMAC-signed session token, stdlib-only (no extra dependency). Format is
    base64(payload_json).signature - the payload carries its own expiry so
    verification doesn't depend on anything but the shared secret.

    `sv` is the client's clients.session_version at issue time. Tokens are
    stateless, so bumping that column (password change/reset) is how every
    OTHER device gets logged out - _require_session compares the two."""
    payload = json.dumps({
        "client_id": client_id,
        "sv": int(session_version or 0),
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


def session_token_version(token: str) -> int:
    """The `sv` a valid token was issued with; tokens minted before
    session_version existed carry none and count as 0."""
    payload = _verify(token, _sign)
    return int(payload.get("sv", 0)) if payload else -1


def _sign_google_login(payload_b64: bytes) -> str:
    # Derived secret, same reasoning as the OAuth state above. Separate from it
    # because this state carries NO client_id: it is a sign-in, not a connect.
    secret = hashlib.sha256(get_key("SESSION_SECRET_KEY").encode() + b":google-login").digest()
    return hmac.new(secret, payload_b64, hashlib.sha256).hexdigest()


def create_google_login_state(nonce: str) -> str:
    """CSRF state for Google sign-in. The nonce is ALSO set as a short-lived
    cookie; the callback requires both to match, which binds the consent round
    trip to the browser that started it (a forged callback link can't log a
    victim into the attacker's account)."""
    payload = json.dumps({"nonce": nonce, "exp": int(time.time()) + OAUTH_STATE_MAX_AGE_SECONDS}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload)
    return f"{payload_b64.decode()}.{_sign_google_login(payload_b64)}"


def verify_google_login_state(token: str, cookie_nonce: str) -> bool:
    payload = _verify(token, _sign_google_login)
    return bool(payload and cookie_nonce and hmac.compare_digest(str(payload.get("nonce", "")), cookie_nonce))


GOOGLE_LINK_MAX_AGE_SECONDS = 10 * 60


def _sign_google_link(payload_b64: bytes) -> str:
    # Derived secret: a pending-link token must never verify as a session, a
    # sign-in state, or anything else signed from the same base secret
    secret = hashlib.sha256(get_key("SESSION_SECRET_KEY").encode() + b":google-link").digest()
    return hmac.new(secret, payload_b64, hashlib.sha256).hexdigest()


def create_google_link_token(client_id: int, sub: str, code_id, nonce: str) -> str:
    """The pending first-Google-link step (core/client_auth.complete_google_link):
    which client, which Google identity, and WHICH emailed code row — set as a
    short-lived HttpOnly cookie, never put in a URL. Bound to the sign-in nonce
    cookie, so it only works in the browser that did the Google round trip.
    Grants nothing by itself: a session is set only after the code checks out."""
    payload = json.dumps({"client_id": int(client_id), "sub": str(sub), "code_id": code_id,
                          "nonce": nonce, "purpose": "google_link",
                          "exp": int(time.time()) + GOOGLE_LINK_MAX_AGE_SECONDS}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload)
    return f"{payload_b64.decode()}.{_sign_google_link(payload_b64)}"


def verify_google_link_token(token: str, cookie_nonce: str):
    """{"client_id", "sub", "code_id"} for a valid, unexpired token whose nonce
    matches the cookie — otherwise None."""
    payload = _verify(token, _sign_google_link)
    if not (payload and payload.get("purpose") == "google_link" and cookie_nonce
            and hmac.compare_digest(str(payload.get("nonce", "")), cookie_nonce)):
        return None
    return {"client_id": payload.get("client_id"), "sub": payload.get("sub"),
            "code_id": payload.get("code_id")}


WP_CONNECT_STATE_MAX_AGE_SECONDS = 30 * 60  # may include logging in to wp-admin first


def _sign_wp_connect(payload_b64: bytes) -> str:
    # Derived secret: this state rides inside the success/reject URLs we hand
    # to the client's WordPress site, so it must never verify as anything else
    secret = hashlib.sha256(get_key("SESSION_SECRET_KEY").encode() + b":wp-connect").digest()
    return hmac.new(secret, payload_b64, hashlib.sha256).hexdigest()


def create_wp_connect_state(client_id: int, site_url: str, nonce: str) -> str:
    """State for WordPress one-click connect. Pins the client AND the site we
    sent them to — the callback stores credentials for THAT site only, never a
    site_url taken from the query string — and a nonce that is also a cookie,
    so a forged callback link can't attach someone else's site to a victim."""
    payload = json.dumps({"client_id": client_id, "site": site_url, "nonce": nonce,
                          "exp": int(time.time()) + WP_CONNECT_STATE_MAX_AGE_SECONDS}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload)
    return f"{payload_b64.decode()}.{_sign_wp_connect(payload_b64)}"


def verify_wp_connect_state(token: str, cookie_nonce: str):
    """(client_id, site_url) for a valid state whose nonce matches the cookie,
    otherwise None."""
    payload = _verify(token, _sign_wp_connect)
    if not (payload and cookie_nonce
            and hmac.compare_digest(str(payload.get("nonce", "")), cookie_nonce)):
        return None
    return payload.get("client_id"), payload.get("site", "")


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
