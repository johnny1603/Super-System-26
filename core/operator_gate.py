"""The second gate for Tier 3 (code + deploy) — docs/OPERATOR.md.

The admin dashboard itself sits behind one shared password. That password
alone must never be enough to ship code, so every Tier 3 step (open the pull
request; merge + deploy) also needs a one-time code emailed to
OPERATOR_APPROVAL_EMAIL — an address that should be the owner's own mailbox,
not the support mailbox the app sends from.

A code is:
- bound to ONE action: its purpose, the exact subject (change id, and for a
  merge the PR head SHA) and the admin session that asked for it — a code for
  "open PR #a" cannot merge, and a code for one session is dead in another;
- 6 digits, valid CODE_TTL_SECONDS, at most MAX_ATTEMPTS wrong tries, then
  burnt;
- single-use: consumed by DELETING its row and checking the delete returned
  it, so two simultaneous approvals cannot both pass;
- stored only as a salted hash.

Tier 3 is off entirely unless OPERATOR_CODE_ENABLED is on AND the approval
address and mail credentials exist (enabled()). Off = every Tier 3 route
refuses before anything is written or sent.
"""
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone

SERVICE_NAME = "operator_gate"
PREFIX = "operator_gate:"
CODE_TTL_SECONDS = 10 * 60
MAX_ATTEMPTS = 5
PURPOSES = ("open_pr", "merge_deploy")
_TRUE = ("1", "true", "yes", "on")


class GateError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def approval_email() -> str:
    return os.environ.get("OPERATOR_APPROVAL_EMAIL", "").strip()


def enabled() -> dict:
    """{"ok": bool, "missing": [...]} — what still blocks Tier 3."""
    missing = []
    if os.environ.get("OPERATOR_CODE_ENABLED", "").strip().lower() not in _TRUE:
        missing.append("OPERATOR_CODE_ENABLED")
    if "@" not in approval_email():
        missing.append("OPERATOR_APPROVAL_EMAIL")
    if not os.environ.get("GMAIL_APP_PASSWORD"):
        missing.append("GMAIL_APP_PASSWORD")
    return {"ok": not missing, "missing": missing}


def masked_email() -> str:
    email = approval_email()
    if "@" not in email:
        return ""
    user, domain = email.split("@", 1)
    return f"{user[:2]}***@{domain}"


def binding(purpose: str, subject: str, admin_fp: str) -> str:
    return hashlib.sha256(f"{purpose}|{subject}|{admin_fp}".encode()).hexdigest()


def _hash(salt: str, code: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def request_code(purpose: str, subject: str, admin_fp: str, summary_he: str) -> dict:
    """Email a fresh code for exactly this action. Returns the gate id (the
    code itself is never returned or logged)."""
    if not enabled()["ok"]:
        raise GateError("ERR_GATE_DISABLED")
    if purpose not in PURPOSES or not subject or not admin_fp:
        raise GateError("ERR_GATE_BAD_REQUEST")
    gate_id = secrets.token_hex(8)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    salt = secrets.token_hex(8)
    row = {"binding": binding(purpose, subject, admin_fp), "salt": salt, "hash": _hash(salt, code),
           "exp": time.time() + CODE_TTL_SECONDS, "attempts": 0, "purpose": purpose, "created_at": _now()}
    try:
        _db().table("app_settings").upsert({"key": PREFIX + gate_id, "value": row, "updated_at": _now()},
                                           on_conflict="key").execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] gate row not stored: {type(e).__name__}")
        raise GateError("ERR_GATE_UNAVAILABLE")
    from core.email_service import send_operator_code
    if not send_operator_code(approval_email(), code, summary_he, CODE_TTL_SECONDS // 60):
        _consume(gate_id)
        raise GateError("ERR_GATE_EMAIL_FAILED")
    return {"gate_id": gate_id, "sent_to": masked_email(), "expires_in_minutes": CODE_TTL_SECONDS // 60}


def _read(gate_id: str) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", PREFIX + gate_id).limit(1)
            .execute().data or [])
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _consume(gate_id: str) -> bool:
    """Delete the row; True only for the ONE caller whose delete removed it."""
    try:
        deleted = _db().table("app_settings").delete().eq("key", PREFIX + gate_id).execute().data or []
        return bool(deleted)
    except Exception:
        return False


def verify(gate_id: str, code: str, purpose: str, subject: str, admin_fp: str):
    """Raises GateError unless `code` is the live, unused code for exactly
    this action and this admin session. Success consumes it."""
    if not enabled()["ok"]:
        raise GateError("ERR_GATE_DISABLED")
    gate_id = str(gate_id or "")
    code = str(code or "").strip()
    if not (len(gate_id) == 16 and all(c in "0123456789abcdef" for c in gate_id)) \
            or not (len(code) == 6 and code.isdigit()):
        raise GateError("ERR_GATE_WRONG")
    try:
        row = _read(gate_id)
    except Exception:
        raise GateError("ERR_GATE_UNAVAILABLE")
    if not row:
        raise GateError("ERR_GATE_EXPIRED")
    if row.get("exp", 0) < time.time():
        _consume(gate_id)
        raise GateError("ERR_GATE_EXPIRED")
    if not hmac.compare_digest(row.get("binding", ""), binding(purpose, subject, admin_fp)):
        raise GateError("ERR_GATE_MISMATCH")
    if not hmac.compare_digest(row.get("hash", ""), _hash(row.get("salt", ""), code)):
        attempts = int(row.get("attempts", 0)) + 1
        if attempts >= MAX_ATTEMPTS:
            _consume(gate_id)
            raise GateError("ERR_GATE_LOCKED")
        try:
            _db().table("app_settings").upsert({"key": PREFIX + gate_id, "value": {**row, "attempts": attempts},
                                                "updated_at": _now()}, on_conflict="key").execute()
        except Exception:
            _consume(gate_id)      # can't count attempts → burn the code rather than allow guessing
        raise GateError("ERR_GATE_WRONG")
    if not _consume(gate_id):
        raise GateError("ERR_GATE_EXPIRED")    # someone else used it first
