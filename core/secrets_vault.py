"""Per-client secrets — encrypted at rest, decrypted only at the moment of use.

## The rule

Every credential in `client_accounts` (OAuth refresh tokens, API keys,
WordPress Application Passwords, vendor logins) is stored ENCRYPTED in
`access_token_enc`, never in plaintext. The ONLY function that decrypts is
`get_client_secret(client_id, service)`. Nothing returns a secret to a browser,
an API response, a log line or a model prompt — screens get
`public_account()`: connected + at most the last 4 characters.

## How

AES-256-GCM (`cryptography`), key = SECRETS_ENCRYPTION_KEY (urlsafe base64 of
32 random bytes; Cloud Run env var / Secret Manager — never in the repo or the
DB). The associated data is "<client_id>:<platform>", so a ciphertext only
decrypts for the client AND service it was written for: a row copied to
another client, or read under the wrong client, fails authentication. That is
the per-client isolation guarantee, enforced by the crypto, not by convention.

Stored format: "v1." + urlsafe_b64(nonce[12] + ciphertext+tag).

## Fail closed

- Key missing/invalid → encrypt() raises VaultUnavailable: the write fails and
  NOTHING is stored (no plaintext fallback). An admin log line + one alert per
  process say exactly why.
- Decrypt failure / ownership mismatch → "" + admin alert: the integration
  reads as not connected rather than using a key it can't prove is this
  client's.

## Transition (until scripts/encrypt_client_secrets.py has run)

Rows written before this change still hold plaintext in `access_token` and an
empty `access_token_enc`. They keep working (read once per row per process
with a loud log line naming the row id, never the value) so deploying the code
before the data migration doesn't take every integration down. The migration
encrypts them; a separate, confirmed step blanks the plaintext.
"""
import base64
import os
import secrets as _stdlib_secrets

SERVICE_NAME = "secrets_vault"
ENV_KEY = "SECRETS_ENCRYPTION_KEY"
PREFIX = "v1."
_NONCE_BYTES = 12

_warned_legacy_rows = set()
_alerted_missing_key = False


class VaultUnavailable(RuntimeError):
    """The encryption key is missing or invalid — refuse, never store plaintext."""


def _admin_log(message: str):
    print(f"[{SERVICE_NAME}] ADMIN: {message}")


def _key() -> bytes:
    global _alerted_missing_key
    raw = os.environ.get(ENV_KEY, "")
    try:
        key = base64.urlsafe_b64decode(raw.encode()) if raw else b""
    except Exception:
        key = b""
    if len(key) != 32:
        reason = "missing" if not raw else "invalid (must be urlsafe base64 of exactly 32 bytes)"
        _admin_log(f"{ENV_KEY} is {reason} — client secrets can NOT be stored or decrypted")
        if not _alerted_missing_key:
            _alerted_missing_key = True
            try:
                from core.agent_base import agent_alert
                agent_alert(SERVICE_NAME, [f"{ENV_KEY} is {reason}: connecting a tool will fail "
                                           f"(nothing is stored in plaintext). Set the env var."])
            except Exception:
                pass
        raise VaultUnavailable(f"{ENV_KEY} {reason}")
    return key


def _aad(client_id: int, platform: str) -> bytes:
    return f"{int(client_id)}:{platform}".encode()


def generate_key() -> str:
    """A fresh key for SECRETS_ENCRYPTION_KEY (run once, store in Secret Manager)."""
    return base64.urlsafe_b64encode(_stdlib_secrets.token_bytes(32)).decode()


def encrypt(plaintext: str, client_id: int, platform: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = _stdlib_secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(_key()).encrypt(nonce, (plaintext or "").encode(), _aad(client_id, platform))
    return PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode()


def _decrypt(token: str, client_id: int, platform: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not token.startswith(PREFIX):
        raise ValueError("unknown ciphertext format")
    blob = base64.urlsafe_b64decode(token[len(PREFIX):].encode())
    nonce, sealed = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    return AESGCM(_key()).decrypt(nonce, sealed, _aad(client_id, platform)).decode()


def last4(secret: str) -> str:
    """At most the last 4 characters — and none for a short secret, where 4
    characters would be most of it."""
    secret = secret or ""
    return secret[-4:] if len(secret) >= 12 else ""


def fingerprint(plaintext: str) -> str:
    """Keyed fingerprint (HMAC-SHA256 under the master key, truncated): lets
    us see that two clients hold the SAME key without decrypting anyone's
    row, and without a fingerprint being useful to anyone who reads the DB."""
    import hashlib
    import hmac
    return hmac.new(_key(), (plaintext or "").encode(), hashlib.sha256).hexdigest()[:32]


def sealed_fields(plaintext: str, client_id: int, platform: str) -> dict:
    """The columns a write stores. Plaintext column is always blanked."""
    if not plaintext:
        return {"access_token": "", "access_token_enc": None, "secret_last4": None,
                "secret_fingerprint": None}
    return {"access_token": "", "access_token_enc": encrypt(plaintext, client_id, platform),
            "secret_last4": last4(plaintext), "secret_fingerprint": fingerprint(plaintext)}


def _alert(client_id: int, issue: str, message: str):
    _admin_log(message)
    try:
        from core.chat_fallback import alert_once
        alert_once(SERVICE_NAME, client_id, issue, message, 1)
    except Exception:
        pass


def get_client_secret(client_id: int, service: str, row: dict = None,
                      any_status: bool = False) -> str:
    """THE decrypt. Returns the plaintext secret for (client_id, service), or
    "" when there is none or it can't be proven to be this client's.

    `row`: a client_accounts row the caller already read (saves a query); it
    is still checked to belong to client_id/service. Without it, the newest
    active row is read (any status when any_status=True)."""
    if not client_id or not service:
        return ""
    if row is None:
        from agents.client_agent import _db
        q = (_db().table("client_accounts").select("*")
             .eq("client_id", client_id).eq("platform", service))
        if not any_status:
            q = q.eq("status", "active")
        rows = q.order("id", desc=True).limit(1).execute().data or []
        row = rows[0] if rows else {}
    if not row:
        return ""
    # Ownership guard: the row must be THIS client's, for THIS service
    if int(row.get("client_id") or 0) != int(client_id) or row.get("platform") != service:
        _alert(client_id, f"secret_owner_mismatch:{service}",
               f"client {client_id}: refused a {service} credential belonging to row "
               f"{row.get('id')} (client {row.get('client_id')}, {row.get('platform')}) — aborted")
        return ""
    sealed = row.get("access_token_enc")
    if sealed:
        try:
            return _decrypt(sealed, client_id, service)
        except VaultUnavailable:
            return ""
        except Exception:
            _alert(client_id, f"secret_decrypt_failed:{service}",
                   f"client {client_id}: {service} credential (row {row.get('id')}) failed to decrypt "
                   f"for this client — not used")
            return ""
    legacy = row.get("access_token") or ""
    if legacy:
        if row.get("id") not in _warned_legacy_rows:
            _warned_legacy_rows.add(row.get("id"))
            _admin_log(f"legacy PLAINTEXT credential read: client_accounts row {row.get('id')} "
                       f"({service}) — run scripts/encrypt_client_secrets.py")
        return legacy
    return ""


# Services whose key the CLIENT pays for (their own vendor account). A job for
# client X may only ever run on client X's own key.
CLIENT_PAID_SERVICES = ("higgsfield", "heygen", "elevenlabs", "creatomate", "uppbeat",
                        "manychat", "make")


def _is_internal(client_id: int) -> bool:
    """Internal accounts (Johnny's own, e.g. Shin Sekai) are the billing-
    exempt ones — the ONLY accounts that may run on the owner's key."""
    try:
        from agents.client_agent import get_client
        return bool((get_client(client_id) or {}).get("billing_exempt"))
    except Exception:
        return False  # unknown -> treat as a normal client (stricter)


def use_vendor_key(client_id: int, service: str, job: str) -> str:
    """The key for a client-paid job, or "" (abort). On top of
    get_client_secret's ownership check, refuses a key that ANOTHER client
    also holds (same fingerprint) — one client's account must never run
    another's work — with ONE exception: when this client AND every other
    holder are internal accounts, i.e. the owner's own key, which is
    allowed and logged as such. A normal client can never reach it."""
    from agents.client_agent import _db
    rows = (_db().table("client_accounts").select("*")
            .eq("client_id", client_id).eq("platform", service).eq("status", "active")
            .order("id", desc=True).limit(1).execute().data or [])
    row = rows[0] if rows else {}
    key = get_client_secret(client_id, service, row=row) if row else ""
    if not key:
        return ""
    fp = row.get("secret_fingerprint")
    if not fp:
        try:
            fp = fingerprint(key)  # legacy row, written before fingerprints
        except VaultUnavailable:
            return ""
    others = (_db().table("client_accounts").select("client_id")
              .eq("platform", service).eq("secret_fingerprint", fp)
              .neq("client_id", client_id).execute().data or [])
    other_ids = sorted({r["client_id"] for r in others})
    if other_ids:
        if _is_internal(client_id) and all(_is_internal(o) for o in other_ids):
            _admin_log(f"client {client_id}: {job} runs on an OWNER key shared with internal "
                       f"accounts {other_ids} ({service}) — internal use, logged")
        else:
            _alert(client_id, f"shared_vendor_key:{service}",
                   f"client {client_id}: {job} ABORTED — their {service} key is also stored for "
                   f"client(s) {other_ids}. One client's account must never run another's work; "
                   f"have each client connect their own key.")
            return ""
    elif _is_internal(client_id):
        _admin_log(f"client {client_id}: {job} on internal account ({service})")
    return key


def hydrate(row: dict) -> dict:
    """A client_accounts row ready for an integration to USE: access_token is
    filled through get_client_secret (for the row's own client + platform) and
    the ciphertext is dropped. Only call this right where the credential is
    used — never on a row headed for a screen or a log."""
    if not row:
        return row
    out = {k: v for k, v in row.items() if k not in ("access_token_enc", "secret_fingerprint")}
    out["access_token"] = get_client_secret(row.get("client_id"), row.get("platform"), row=row)
    return out


SECRET_COLUMNS = ("access_token", "access_token_enc", "secret_fingerprint")


def public_account(row: dict) -> dict:
    """What any screen may see about a connection: no secret, ever."""
    if not row:
        return row
    out = {k: v for k, v in row.items() if k not in SECRET_COLUMNS}
    out["has_secret"] = bool(row.get("access_token_enc") or row.get("access_token"))
    out["secret_last4"] = row.get("secret_last4") or ""
    return out
