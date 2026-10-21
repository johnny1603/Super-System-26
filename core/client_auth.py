"""Client credentials: passwords, Google sign-in, and the no-billing account.

## What existed before this module (and still does)

Client auth is CUSTOM, not Supabase Auth: `clients` rows are created by
`/api/checkout` (sales chat, BEFORE PayPal - status `pending_payment`, flipped
to `active` once the subscription is verified), and login was passwordless - a
6-digit code emailed to the address on file (`login_codes`), then an
HMAC-signed `session` cookie (core/session.py). That code login is untouched
and stays the fallback for everyone.

## What this adds

- **Passwords** (stdlib scrypt - no new dependency). A password can only ever
  be SET with a fresh emailed code (`set_password_with_code`): first-time
  setup after payment, "forgot password", and an admin-triggered reset are all
  that one path, so there is exactly one way a password comes into existence
  and it always proves the client owns the inbox. That same proof is what
  marks the email verified (`email_verified_at`).
  Changing a password from the profile page needs the CURRENT password instead.
- **Google sign-in** (openid + email only - non-sensitive scopes, so no
  app-verification review). It signs in an EXISTING client whose email matches
  the verified Google email; it never creates one. The FIRST time a Google
  identity reaches an account, it is linked only after the emailed login code
  is entered (start_google_link / complete_google_link) — the code is the
  security gate. After that the stable `sub` signs in directly, so a later
  Gmail-address change still works. A different `sub` is never re-linked.
- **session_version**: changing/resetting a password bumps it, which kills every
  other session cookie (tokens are stateless, so this is the revocation lever).
- **billing_exempt** accounts (admin-created, e.g. uallak's own business): no
  PayPal at all, and an explicit services GRANT recorded as an activity row
  (`account_created_by_admin`) that `client_journey._chosen_package` reads in
  place of the checkout package - so the connection checklist, the interview
  gate, website self-provisioning and the feature catalogue all work for real,
  from one definition, instead of a fake $0 subscription.

Needs `migrations/2026-10-13-client-auth.sql`. Until it runs, password and
Google sign-in fail CLOSED with ERR_AUTH_NOT_READY and code login keeps working.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

SERVICE_NAME = "client_auth"

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256          # scrypt input is unbounded; a hard stop against abuse
MAX_PASSWORD_ATTEMPTS = 5          # then a lockout window, per client
PASSWORD_LOCKOUT_MINUTES = 15

# scrypt cost: n=2^14, r=8, p=1 is the long-standing interactive-login baseline
# (~16MB, tens of ms). Stored alongside the hash so it can be raised later
# without invalidating existing passwords.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1

# The admin "New Client" form offers exactly these - the keys of
# client_journey.CONNECTION_STEPS, mapped to a service token that step matches.
GRANTABLE_SERVICES = {
    "website": "website",
    "google_ads": "google ads",
    "meta": "meta",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "media": "media",
    "avatar": "avatar",
}

# THE Google sign-in scope list (one constant; docs/GOOGLE-SCOPES.md mirrors
# it). `profile` was dropped 2026-10-17: nothing reads name/picture — sign-in
# uses only sub, email and email_verified (find_client_for_google, link_google).
# The full userinfo.email URL, not the "email" alias: Google treats them as the
# same scope, but the Console lists the URL and verification wants an exact
# string match between code and Console (2026-10-21).
GOOGLE_LOGIN_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
GOOGLE_LOGIN_CALLBACK_PATH = "/api/oauth/google-login/callback"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class AuthNotReadyError(RuntimeError):
    """The client-auth migration hasn't been applied - the columns are missing."""


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _update_client(client_id: int, values: dict):
    try:
        _db().table("clients").update(values).eq("id", client_id).execute()
    except Exception as e:
        # A missing column is the only expected failure here; never let it
        # pass silently as "password saved" when nothing was written.
        raise AuthNotReadyError(f"clients update failed - has "
                                f"migrations/2026-10-13-client-auth.sql been run? ({e})")


# ─── Hashing ─────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                            p=_SCRYPT_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(digest)}"


def _check_hash(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def password_problem(password: str) -> str:
    """'' when acceptable, else an ERR_ code the UI localizes."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return "ERR_PASSWORD_TOO_SHORT"
    if len(password) > MAX_PASSWORD_LENGTH:
        return "ERR_PASSWORD_TOO_LONG"
    return ""


def has_password(client: dict) -> bool:
    return bool((client or {}).get("password_hash"))


# ─── Login code check (shared with /api/login/verify-code's semantics) ───────

def consume_login_code(client: dict, code: str, max_attempts: int) -> bool:
    """True and burns the code when `code` is the client's current unexpired
    login code. Wrong guesses count against that code exactly like the
    code-login endpoint, so routing a code through here is never a way around
    its attempt limit."""
    from agents.client_agent import (get_active_login_code, increment_login_code_attempts,
                                     mark_login_code_used)
    row = get_active_login_code(client["id"])
    if not row or row.get("failed_attempts", 0) >= max_attempts:
        return False
    expires_at = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    if expires_at < _now():
        return False
    if not hmac.compare_digest(str(row["code"]), (code or "").strip()):
        if increment_login_code_attempts(row["id"], row.get("failed_attempts", 0)) >= max_attempts:
            mark_login_code_used(row["id"])
        return False
    mark_login_code_used(row["id"])
    return True


# ─── Setting / changing / resetting ──────────────────────────────────────────

def set_password_with_code(client: dict, code: str, new_password: str, max_attempts: int,
                           name: str = "", phone: str = "") -> dict:
    """The ONE way a password is created or replaced without knowing the old
    one: post-payment setup, forgot-password, and admin-triggered reset all land
    here. The emailed code is the proof of inbox ownership, which is also what
    makes the email verified. Optional name/phone come from the signup form
    (checkout already collected them; this lets the client correct them)."""
    problem = password_problem(new_password)
    if problem:
        return {"success": False, "code": problem}
    if not consume_login_code(client, code, max_attempts):
        return {"success": False, "code": "ERR_INVALID_CODE"}
    values = {
        "password_hash": hash_password(new_password),
        "password_set_at": _now().isoformat(),
        "email_verified_at": client.get("email_verified_at") or _now().isoformat(),
        "failed_password_attempts": 0,
        "password_locked_until": None,
        "session_version": int(client.get("session_version") or 0) + 1,
    }
    if name.strip():
        values["name"] = name.strip()[:200]
    if phone.strip():
        values["phone"] = phone.strip()[:40]
    _update_client(client["id"], values)
    _log(client["id"], "password_set", {"via": "email_code"})
    return {"success": True, "session_version": values["session_version"]}


def change_password(client: dict, current_password: str, new_password: str) -> dict:
    if not has_password(client):
        return {"success": False, "code": "ERR_NO_PASSWORD_SET"}
    if not _check_hash(current_password or "", client["password_hash"]):
        return {"success": False, "code": "ERR_WRONG_PASSWORD"}
    problem = password_problem(new_password)
    if problem:
        return {"success": False, "code": problem}
    version = int(client.get("session_version") or 0) + 1
    _update_client(client["id"], {
        "password_hash": hash_password(new_password),
        "password_set_at": _now().isoformat(),
        "session_version": version,
    })
    _log(client["id"], "password_changed", {"via": "profile"})
    return {"success": True, "session_version": version}


def admin_reset_password(client: dict) -> dict:
    """Clears the password and ends every session; the client sets a new one
    through the emailed-code path. The admin never chooses or sees a password."""
    version = int(client.get("session_version") or 0) + 1
    _update_client(client["id"], {
        "password_hash": None, "password_set_at": None,
        "failed_password_attempts": 0, "password_locked_until": None,
        "session_version": version,
    })
    _log(client["id"], "password_reset_by_admin", {})
    return {"success": True}


def wipe_credentials(client_id: int):
    """Offboarding tombstone companion: no credential outlives the account.
    Separate from the PII update so a missing column can't fail that one."""
    try:
        _db().table("clients").update({"password_hash": None, "google_sub": None}).eq(
            "id", client_id).execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] credential wipe skipped for client {client_id}: {e}")


# ─── Password login ──────────────────────────────────────────────────────────

def verify_password_login(client: dict, password: str) -> str:
    """'' on success, else an ERR_ code. Per-client lockout after
    MAX_PASSWORD_ATTEMPTS wrong tries; the lockout is reported as a plain
    invalid-credentials error so it reveals nothing about the account."""
    if not has_password(client):
        return "ERR_INVALID_CREDENTIALS"
    locked_until = client.get("password_locked_until")
    if locked_until and datetime.fromisoformat(locked_until.replace("Z", "+00:00")) > _now():
        return "ERR_INVALID_CREDENTIALS"
    if _check_hash(password or "", client["password_hash"]):
        if client.get("failed_password_attempts"):
            _update_client(client["id"], {"failed_password_attempts": 0, "password_locked_until": None})
        return ""
    attempts = int(client.get("failed_password_attempts") or 0) + 1
    values = {"failed_password_attempts": attempts}
    if attempts >= MAX_PASSWORD_ATTEMPTS:
        values = {"failed_password_attempts": 0,
                  "password_locked_until": (_now() + timedelta(minutes=PASSWORD_LOCKOUT_MINUTES)).isoformat()}
        _log(client["id"], "password_locked", {"minutes": PASSWORD_LOCKOUT_MINUTES})
    _update_client(client["id"], values)
    return "ERR_INVALID_CREDENTIALS"


def mark_email_verified(client: dict):
    """A successful code or Google login proves the inbox too. Best-effort: a
    missing column must never block a login that is otherwise valid."""
    if client.get("email_verified_at"):
        return
    try:
        _db().table("clients").update({"email_verified_at": _now().isoformat()}).eq(
            "id", client["id"]).execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] email_verified_at not stored (migration pending?): {e}")


# ─── Google sign-in ──────────────────────────────────────────────────────────

def _public_app_url() -> str:
    return os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com").rstrip("/")


def google_login_redirect_uri() -> str:
    return f"{_public_app_url()}{GOOGLE_LOGIN_CALLBACK_PATH}"


def google_login_url(state: str) -> str:
    from agents.keys_agent import get_key
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": google_login_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(GOOGLE_LOGIN_SCOPES),
        "prompt": "select_account",
        "state": state,
    })


def google_identity(code: str) -> dict:
    """Authorization code -> {sub, email, email_verified}. The access
    token is used once for userinfo and discarded; nothing is stored but `sub`."""
    from agents.keys_agent import get_key
    token = httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "client_secret": get_key("GOOGLE_OAUTH_CLIENT_SECRET"),
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": google_login_redirect_uri(),
    }, timeout=20)
    token.raise_for_status()
    info = httpx.get(GOOGLE_USERINFO_URL, timeout=20,
                     headers={"Authorization": f"Bearer {token.json()['access_token']}"})
    info.raise_for_status()
    return info.json()


def find_client_for_google(identity: dict) -> dict:
    """An already-linked `sub` wins; otherwise the client whose email equals the
    VERIFIED Google email. An unverified Google email matches nothing - that is
    the whole defence against someone registering a Google account under a
    client's address."""
    sub = identity.get("sub")
    if sub:
        try:
            rows = _db().table("clients").select("*").eq("google_sub", sub).limit(1).execute().data
            if rows:
                return rows[0]
        except Exception as e:
            raise AuthNotReadyError(f"google_sub lookup failed - migration pending? ({e})")
    email = (identity.get("email") or "").strip()
    if not (email and identity.get("email_verified") in (True, "true")):
        return {}
    from agents.client_agent import get_client_by_email
    client = get_client_by_email(email) or get_client_by_email(email.lower())
    # Never hijack a different Google account's existing link
    if client and client.get("google_sub") and client["google_sub"] != sub:
        return {}
    return client or {}


def mask_email(email: str) -> str:
    """First character + domain ("d***@example.com") — enough to recognise
    which address it was, never the address itself."""
    email = (email or "").strip()
    if not email:
        return "(none)"
    if "@" not in email:
        return email[:1] + "***"
    local, domain = email.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


NO_ACCOUNT_ALERT_WINDOW = 3600
_no_account_alerted = {}   # (email hash, reason) -> last alert time (per instance)


def google_no_account_diagnosis(identity: dict, locked_out=None) -> dict:
    """WHY a Google sign-in found no account — the same two lookups as
    find_client_for_google, without its refusals, so the alert says what
    actually matched. `locked_out(client) -> bool` is the callback's own
    lockout check. Never raises; never the full email, a token or a code."""
    sub = identity.get("sub") or ""
    email = (identity.get("email") or "").strip()
    verified = identity.get("email_verified") in (True, "true")
    by_sub, by_email = {}, {}
    try:
        if sub:
            rows = _db().table("clients").select("*").eq("google_sub", sub).limit(1).execute().data
            by_sub = rows[0] if rows else {}
    except Exception:
        pass
    try:
        if email:
            from agents.client_agent import get_client_by_email
            by_email = get_client_by_email(email) or get_client_by_email(email.lower()) or {}
    except Exception:
        pass
    client = by_sub or by_email
    locked = bool(client) and bool(locked_out and locked_out(client))
    linked_elsewhere = bool(by_email.get("google_sub")) and by_email.get("google_sub") != sub
    if client and locked:
        reason = "account_closed_or_being_deleted"
    elif linked_elsewhere and not by_sub:
        reason = "account_linked_to_another_google_account"
    elif by_email and not verified and not by_sub:
        reason = "google_email_not_verified"
    elif not client:
        reason = "no_client_with_this_email"
    else:
        reason = "unexpected"
    return {"email": mask_email(email), "email_verified": verified,
            "matched_by_email": bool(by_email), "matched_by_sub": bool(by_sub),
            "client_id": client.get("id"), "client_status": client.get("status") if client else None,
            "reason": reason, "_key": (hashlib.sha256(email.lower().encode()).hexdigest()[:16], reason)}


def no_account_alert_text(diagnosis: dict):
    """The admin alert line, or None while the same address + reason was
    already alerted within NO_ACCOUNT_ALERT_WINDOW (a client retrying the
    button five times is one alert, not five)."""
    key = diagnosis.get("_key")
    now = time.time()
    if key and now - _no_account_alerted.get(key, 0) < NO_ACCOUNT_ALERT_WINDOW:
        return None
    if key:
        _no_account_alerted[key] = now
    who = f"client {diagnosis['client_id']}" if diagnosis.get("client_id") else "no client"
    return (f"Google sign-in found no account ({diagnosis['reason']}) — {who}; "
            f"google_email={diagnosis['email']} email_verified={diagnosis['email_verified']} "
            f"matched_by_email={diagnosis['matched_by_email']} matched_by_sub={diagnosis['matched_by_sub']} "
            f"client_status={diagnosis['client_status']}")


def link_google(client: dict, identity: dict):
    if client.get("google_sub") == identity.get("sub"):
        return
    _update_client(client["id"], {"google_sub": identity["sub"]})
    _log(client["id"], "google_login_linked", {"google_email": identity.get("email", "")})


# ─── First Google sign-in: the emailed code is the gate (2026-10-22) ─────────
# A verified Google email matching a client's address used to sign in AND link
# on the spot. Product decision: the first link of a Google identity to an
# account must also prove the account's INBOX — the same emailed 6-digit code
# as code login (login_codes: same expiry, same attempt limit). Until that code
# is entered, nothing is linked and no session is set. Once linked, Google
# sign-in is one click again. No new scope, no new consent.

GOOGLE_LINK_CODE_MINUTES = 10


def google_callback_outcome(client: dict, identity: dict) -> str:
    """What the callback does with a client find_client_for_google returned:
    'login' (this exact Google identity is already linked), 'link_code' (an
    email match not yet linked — prove the inbox first), or 'refuse' (no
    client; find_client_for_google already returns none for an account linked
    to a DIFFERENT Google identity)."""
    if not client:
        return "refuse"
    sub = identity.get("sub")
    if sub and client.get("google_sub") == sub:
        return "login"
    if client.get("google_sub"):
        return "refuse"    # linked elsewhere — never re-linked from here
    return "link_code"


def start_google_link(client: dict, identity: dict) -> dict:
    """Emails the standard login code (purpose google_link) and returns what
    the pending token must carry. The code is never returned, logged or put in
    an alert."""
    from agents.client_agent import create_login_code
    from core.email_service import send_login_code
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = (_now() + timedelta(minutes=GOOGLE_LINK_CODE_MINUTES)).isoformat()
    row = create_login_code(client["id"], code, expires_at)
    if not row.get("id"):
        raise RuntimeError("login code not stored")
    send_login_code(client["email"], client.get("name", ""), code, client["id"], purpose="google_link")
    _log(client["id"], "google_link_code_sent", {})
    return {"client_id": client["id"], "sub": identity["sub"], "code_id": row["id"]}


def complete_google_link(client: dict, sub: str, code_id, code: str, max_attempts: int) -> dict:
    """The code step. Single-use by construction: the pending token names the
    login_codes row it was issued with, and only while that row is the
    client's ACTIVE code (unused, not superseded by a newer code, not burnt by
    too many wrong guesses) can it succeed. A correct code burns the row, so
    replaying the same pending token can never link twice.
    -> {"success": True} | {"success": False, "code": ERR_INVALID_CODE (try
    again) | ERR_LINK_EXPIRED (start over with Google) | ERR_GOOGLE_LINKED_ELSEWHERE}"""
    from agents.client_agent import get_active_login_code
    if client.get("google_sub") and client["google_sub"] != sub:
        return {"success": False, "code": "ERR_GOOGLE_LINKED_ELSEWHERE"}
    active = get_active_login_code(client["id"])
    if not active or str(active.get("id")) != str(code_id) or active.get("failed_attempts", 0) >= max_attempts:
        return {"success": False, "code": "ERR_LINK_EXPIRED"}
    expires_at = datetime.fromisoformat(str(active["expires_at"]).replace("Z", "+00:00"))
    if expires_at < _now():
        return {"success": False, "code": "ERR_LINK_EXPIRED"}
    if not consume_login_code(client, code, max_attempts):
        still = get_active_login_code(client["id"])
        expired = not still or str(still.get("id")) != str(code_id)
        return {"success": False, "code": "ERR_LINK_EXPIRED" if expired else "ERR_INVALID_CODE"}
    link_google(client, {"sub": sub})
    mark_email_verified(client)
    _log(client["id"], "google_link_verified", {})
    return {"success": True}


# ─── Admin-created, billing-exempt accounts ──────────────────────────────────

def create_exempt_client(name: str, email: str, phone: str, business_name: str,
                         services: list, builds_site: bool, language: str = "he") -> dict:
    """An internal account with NO billing - created by Johnny, never by the
    public. `services` must be keys of GRANTABLE_SERVICES; they become the
    package the rest of the system reads (see module docstring)."""
    from agents.client_agent import get_client_by_email
    email = (email or "").strip()
    if not (name.strip() and email and "@" in email):
        return {"success": False, "code": "ERR_NAME_EMAIL_REQUIRED"}
    if get_client_by_email(email) or get_client_by_email(email.lower()):
        return {"success": False, "code": "ERR_EMAIL_EXISTS"}
    unknown = [s for s in services if s not in GRANTABLE_SERVICES]
    if unknown:
        return {"success": False, "code": "ERR_UNKNOWN_SERVICE", "unknown": unknown}

    row = {
        "name": name.strip(), "email": email, "phone": (phone or "").strip(),
        "business_name": (business_name or "").strip(),
        "package": "חשבון פנימי — ללא חיוב",
        "status": "active", "onboarding_completed": False,
        "billing_exempt": True,
        "language": language if language in ("he", "en", "fr", "ar", "ru") else "he",
    }
    try:
        created = _db().table("clients").insert(row).execute().data[0]
    except Exception as e:
        # Do NOT retry without billing_exempt: an internal account that lands
        # in revenue reporting as a $0 paying client is the exact misreading
        # this flag exists to prevent.
        raise AuthNotReadyError(f"exempt client insert failed - migration pending? ({e})")

    _log(created["id"], "account_created_by_admin", {
        "billing_exempt": True,
        "services": [GRANTABLE_SERVICES[s] for s in services],
        "service_keys": list(services),
        "builds_site": bool(builds_site),
    })
    return {"success": True, "client": created}


def exempt_package(client_id: int) -> dict:
    """The package-shaped grant for an admin-created account, or {} when the
    client has none. Same shape `client_journey.required_connections` and
    `website_agent._package_includes_hosting` read from a real proposal."""
    rows = (_db().table("client_activity").select("details")
            .eq("client_id", client_id).eq("agent_name", SERVICE_NAME)
            .eq("action_type", "account_created_by_admin")
            .order("created_at", desc=True).limit(1).execute().data or [])
    if not rows:
        return {}
    details = rows[0].get("details") or {}
    breakdown = {}
    if details.get("builds_site"):
        from agents.onboarding_agent import PRICING
        breakdown[PRICING["website"]["new_site_hosting"]["label_he"]] = 0
    return {"id": "admin_grant", "name": "חשבון פנימי — ללא חיוב",
            "recommended_services": details.get("services") or [],
            "monthly_breakdown": breakdown, "billing_exempt": True}


def _log(client_id: int, action: str, details: dict):
    try:
        from agents.client_agent import log_activity
        log_activity(client_id, SERVICE_NAME, action, details, {})
    except Exception as e:
        print(f"[{SERVICE_NAME}] activity log failed (non-fatal): {e}")
