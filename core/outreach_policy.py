"""The rules every outreach message obeys (docs/PROSPECTING.md §6) — in code,
so no prompt and no caller can skip them.

- The FIRST message to a business is a CONSENT REQUEST (the Communications
  Law's one-time approach to a business), ONE per business, ever, unless it
  replied.
- The opt-out line is mandatory: `with_opt_out` appends it, `validate_draft`
  refuses a draft without it.
- Personalized, never generic: the business's name AND a specific hook that
  appears both in the message and in what we actually read about the
  business; and not a near-copy of a draft already written for this client.
- The SUPPRESSION list ("do not contact") is checked before ANY send, by
  e-mail, e-mail domain, phone and place_id. Its table has NO integer
  client_id column on purpose: client deletion clears every table that has
  one (schema discovery), and a refusal must outlive the lead and the client.
  Rows are only ever added; nothing here deletes one.
- Sending itself is DISABLED until Google approves gmail.send: the switch is
  GMAIL_SEND_ENABLED (core/gmail_service, off by default), read through
  `sending_enabled()`. `check_before_send` still runs every other check
  first, so those are exercised either way.
"""
import difflib
import re
from datetime import datetime, timezone

from core.domains import email_domain, registered_domain


def sending_enabled() -> bool:
    """Flips only when gmail.send is approved AND GMAIL_SEND_ENABLED is set."""
    from core.round2_flags import gmail_send_enabled
    return gmail_send_enabled()



SIMILARITY_MAX = 0.8         # a draft this close to an earlier one is "generic"
HOOK_MIN_CHARS = 8
SUBJECT_MAX = 120
BODY_MAX = 1500

OPT_OUT = {
    "he": "אם אינכם מעוניינים בפניות נוספות מאיתנו, השיבו \"הסר\" ולא נפנה אליכם שוב.",
    "en": "If you'd rather not hear from us again, reply \"remove\" and we will not contact you again.",
}
SUPPRESSION_TABLE = "outreach_suppression"
KEY_TYPES = ("email", "domain", "phone", "place_id")


class SendRefused(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── The message ─────────────────────────────────────────────────────────────

def opt_out_line(language: str) -> str:
    return OPT_OUT.get(language) or OPT_OUT["he"]


def with_opt_out(body: str, language: str) -> str:
    line = opt_out_line(language)
    body = (body or "").rstrip()
    return body if line in body else f"{body}\n\n{line}"


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def validate_draft(subject: str, body: str, hook: str, business_name: str, source_text: str,
                   language: str, previous_bodies: list) -> list:
    """Problems ([] = OK). `source_text` is what we actually read about the
    business (its site text + its category); the hook must come from it."""
    problems = []
    if not subject or len(subject) > SUBJECT_MAX:
        problems.append("subject missing or too long")
    if not body or len(body) > BODY_MAX + 200:
        problems.append("body missing or too long")
    if opt_out_line(language) not in (body or ""):
        problems.append("the opt-out line is missing")
    if not business_name or _norm(business_name) not in _norm(body):
        problems.append("the message does not name the business")
    hook_n = _norm(hook)
    if len(hook_n) < HOOK_MIN_CHARS or hook_n not in _norm(body) or hook_n not in _norm(source_text):
        problems.append("no specific detail about this business that we actually read")
    core_body = _norm((body or "").replace(business_name or "", ""))
    for earlier in previous_bodies or []:
        if difflib.SequenceMatcher(None, core_body, _norm(earlier)).ratio() > SIMILARITY_MAX:
            problems.append("too similar to a message already written — it reads generic")
            break
    return problems


# ─── Suppression ("do not contact") ─────────────────────────────────────────

def suppression_keys(email: str = "", phone: str = "", place_id: str = "") -> list:
    keys = []
    if email:
        keys += [("email", email.strip().lower()), ("domain", email_domain(email))]
    if phone:
        keys.append(("phone", re.sub(r"\D", "", phone)))
    if place_id:
        keys.append(("place_id", place_id))
    return [(t, v) for t, v in keys if v]


def is_suppressed(email: str = "", phone: str = "", place_id: str = "") -> bool:
    """Fail CLOSED: an unreadable list means "do not send"."""
    keys = suppression_keys(email, phone, place_id)
    try:
        for key_type, value in keys:
            rows = (_db().table(SUPPRESSION_TABLE).select("id").eq("key_type", key_type)
                    .eq("key_value", value).limit(1).execute().data or [])
            if rows:
                return True
    except Exception as e:
        print(f"[outreach_policy] suppression list unreadable — treating as suppressed: {type(e).__name__}")
        return True
    return False


def suppress(key_type: str, key_value: str, reason: str, source: str, added_by: str,
             requested_for: str = "") -> dict:
    """Add one entry (idempotent). `requested_for` is free text ("client 27",
    "uallak") — deliberately not an integer client_id (see the docstring)."""
    if key_type not in KEY_TYPES:
        raise ValueError(f"key_type must be one of {KEY_TYPES}")
    value = key_value.strip().lower() if key_type in ("email", "domain") else key_value.strip()
    if key_type == "domain":
        value = registered_domain(value)
    if key_type == "phone":
        value = re.sub(r"\D", "", value)
    if not value:
        raise ValueError("empty key_value")
    if is_suppressed(**{"email": value} if key_type == "email" else
                     {"phone": value} if key_type == "phone" else
                     {"place_id": value} if key_type == "place_id" else {}) and key_type != "domain":
        return {"added": False, "already": True}
    row = {"key_type": key_type, "key_value": value, "reason": (reason or "")[:300],
           "source": (source or "")[:300], "added_by": added_by[:60],
           "requested_for": (requested_for or "")[:60], "requested_at": _now()}
    _db().table(SUPPRESSION_TABLE).insert(row).execute()
    return {"added": True}


# ─── Before ANY send ─────────────────────────────────────────────────────────

def already_contacted(client_id: int, place_id: str, email: str) -> bool:
    """One message per business, ever: a done send to this place or address —
    from prospecting's contacts or from a recorded Gmail send
    (core/gmail_outreach, table outreach_sends)."""
    try:
        rows = (_db().table("prospect_contacts").select("contacted_at,email,place_id")
                .eq("client_id", int(client_id)).execute().data or [])
        sent = ((_db().table("outreach_sends").select("id").eq("client_id", int(client_id))
                 .eq("to_email", email.lower()).limit(1).execute().data or []) if email else [])
    except Exception:
        return True   # can't prove it's the first message → don't send
    if sent:
        return True
    return any(r.get("contacted_at") and ((place_id and r.get("place_id") == place_id) or
                                          (email and r.get("email") == email.lower()))
               for r in rows)


def check_before_send(client_id: int, params: dict, language: str = "he"):
    """Every rule, in order; raises SendRefused(code). The suppression check
    is FIRST and runs whatever else is wrong."""
    from core import gmail_agent_plan as plan
    email, place_id = params.get("to_email", ""), params.get("place_id", "")
    if is_suppressed(email=email, place_id=place_id):
        raise SendRefused("suppressed", "this business asked not to be contacted")
    if opt_out_line(language) not in params.get("body", ""):
        raise SendRefused("no_opt_out", "the opt-out line is missing")
    if already_contacted(client_id, place_id, email):
        raise SendRefused("already_contacted", "one message per business")
    if not plan.entitled(client_id):
        raise SendRefused("not_entitled")
    try:
        plan.take_message(client_id)
    except plan.CapReached as e:
        raise SendRefused("daily_cap", e.which)
    if not sending_enabled():
        raise SendRefused("send_disabled", "sending is not enabled until Google approves gmail.send")
