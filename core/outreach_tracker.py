"""The Outreach page — one row per business a client's Gmail agent found,
drafted for, or wrote to — and its export. Client view, admin view (all
clients, filter by client) and the agents' snapshot (client_state.outreach).

STATUS IS DERIVED AT READ TIME from the rows that already exist, never stored
(the lead_tracking "dropped_off" idiom): a parallel status column would drift
from the send record invisibly.

  prospect_contacts   found (our own data only — see gmail_outreach.record_found)
  agent_actions       draft pending approval (open card) / approved (running) /
                      failed (the send itself errored)
  outreach_sends      sent; no response = 7 days after the last send with no mark
  outreach_marks      what the CLIENT tells us from their own inbox: replied,
                      asked to be removed, bounced. We hold gmail.send only and
                      never read the mailbox — these buttons are the only way
                      we learn about a reply.
  outreach_suppression  any address/domain on the do-not-contact list shows
                      as removed, whoever asked.

Precedence: removed > bounced/failed > replied > sent/no response > approved >
draft pending > found.

GOOGLE PLACES TERMS (docs/PROSPECTING.md §3): nothing here is Places content.
The business name is as written on the business's own site / in our draft;
"area" is the search area WE chose; the source column says "Google Maps"
only as the label of how the business was found. So the export carries no
Places content either.

TENANT ISOLATION: every client-facing read is built for ONE client_id (the
session's), and the export's row selection (`keys`) is applied AFTER that —
a key from another client simply matches nothing.
"""
from datetime import datetime, timedelta, timezone

from core import outreach_policy as policy

STATUSES = ("found", "draft_pending", "approved", "sent", "no_response", "replied", "removed", "failed")
STATUS_HE = {
    "found": "נמצא", "draft_pending": "טיוטה ממתינה לאישור", "approved": "אושר, בשליחה",
    "sent": "נשלח", "no_response": "אין תגובה", "replied": "ענו", "removed": "ביקשו להסיר",
    "failed": "חזר / נכשל",
}
SOURCE_HE = {"places": "חיפוש ב-Google Maps", "chat": "שיחה עם הסוכן"}
FOLLOWUP_HE = {"none": "", "pending": "מעקב ממתין לאישור", "sent": "נשלח מעקב"}
ACTIONS = ("send_outreach_email", "send_outreach_followup")
NO_RESPONSE_DAYS = policy.FOLLOWUP_AFTER_DAYS
ROW_LIMIT = 2000
MARKABLE = {"replied": ("sent", "no_response", "failed", "replied"),
            "bounced": ("sent", "no_response", "replied", "failed"),
            "removed": tuple(s for s in STATUSES if s != "removed")}
DELETABLE = ("found", "draft_pending")

# The export: plain Hebrew headers, nothing else (no ids, no tokens, no body)
COLUMNS = [("business_name", "שם העסק"), ("area", "עיר / אזור"), ("source_label", "מקור"),
           ("to_email", "המייל שאליו פנינו"), ("source_url", "העמוד באתר העסק"),
           ("subject", "נושא"), ("sent_date", "תאריך שליחה"), ("status_label", "סטטוס"),
           ("followup_label", "מעקב"), ("gmail_link", "קישור ב-Gmail")]
ADMIN_COLUMNS = [("client_label", "לקוח")] + COLUMNS


class OutreachError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ─── Seams (patched by tests) ────────────────────────────────────────────────

def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sending_enabled() -> bool:
    return policy.sending_enabled()


# ─── Dates (Israel format) ───────────────────────────────────────────────────

def _parse(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _israel(dt: datetime) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Asia/Jerusalem"))
    except Exception:
        # No tz database in the container: Israel's rule — summer time from the
        # Friday before the last Sunday of March to the last Sunday of October
        def last_sunday(year, month):
            day = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
            return day - timedelta(days=(day.weekday() + 1) % 7)
        start = last_sunday(dt.year, 3) - timedelta(days=2)
        end = last_sunday(dt.year, 10)
        return dt.astimezone(timezone(timedelta(hours=3 if start <= dt < end else 2)))


def israel_date(value) -> str:
    dt = _parse(value)
    return _israel(dt).strftime("%d/%m/%Y") if dt else ""


def gmail_link(from_email: str, message_id: str) -> str:
    """Opens the sent message in the client's OWN Gmail (their browser,
    their login). A link only — we never read the mailbox."""
    if not message_id:
        return ""
    from urllib.parse import quote
    account = f"?authuser={quote(from_email)}" if from_email else ""
    return f"https://mail.google.com/mail/u/{account}#all/{quote(message_id)}"


# ─── Reading ─────────────────────────────────────────────────────────────────

def _read(table: str, client_id, gaps: list, extra=None) -> list:
    try:
        query = _db().table(table).select("*")
        if client_id is not None:
            query = query.eq("client_id", int(client_id))
        if extra:
            query = extra(query)
        return query.limit(ROW_LIMIT).execute().data or []
    except Exception as e:
        print(f"[outreach_tracker] {table} unreadable: {type(e).__name__}")
        gaps.append(table)
        return []


def _suppressed(emails: set, gaps: list) -> set:
    """Which of these addresses are on the do-not-contact list (by address or
    by the address's domain). Unreadable → all of them (fail closed)."""
    if not emails:
        return set()
    from core.domains import email_domain
    domains = {email_domain(e) for e in emails} - {""}
    try:
        rows = (_db().table(policy.SUPPRESSION_TABLE).select("key_type,key_value")
                .in_("key_value", sorted(emails | domains)).limit(ROW_LIMIT).execute().data or [])
    except Exception:
        gaps.append(policy.SUPPRESSION_TABLE)
        return set(emails)
    by_email = {r["key_value"] for r in rows if r.get("key_type") == "email"}
    by_domain = {r["key_value"] for r in rows if r.get("key_type") == "domain"}
    return {e for e in emails if e in by_email or email_domain(e) in by_domain}


def _action_expired(row: dict, now: datetime) -> bool:
    from core.agent_actions import PROPOSAL_TTL_HOURS
    created = _parse(row.get("created_at"))
    return not created or now - created > timedelta(hours=PROPOSAL_TTL_HOURS)


def collect(client_id=None, now: datetime = None) -> dict:
    """{"rows", "gaps"} — client_id None = every client (admin only)."""
    now = now or _now()
    gaps = []
    found = _read("prospect_contacts", client_id, gaps)
    sends = _read("outreach_sends", client_id, gaps)
    marks = _read(policy.MARKS_TABLE, client_id, gaps)
    actions = _read("agent_actions", client_id, gaps, extra=lambda q: q.in_("action", list(ACTIONS)))

    biz = {}

    def entry(cid, email):
        email = (email or "").strip().lower()
        key = (int(cid or 0), email)
        if key not in biz:
            biz[key] = {"client_id": int(cid or 0), "to_email": email, "business_name": "", "area": "",
                        "source": "chat", "source_url": "", "subject": "", "found_at": "", "sent_at": "",
                        "from_email": "", "message_id": "", "followup": "none", "followup_sent_at": "",
                        "draft_id": None, "approved": False, "send_failed": False, "mark": "",
                        "mark_at": "", "place_id": ""}
        return biz[key]

    for r in found:
        if not r.get("email"):
            continue
        b = entry(r.get("client_id"), r.get("email"))
        b.update({"business_name": b["business_name"] or r.get("business_name") or "",
                  "area": r.get("area") or "", "source": r.get("source") or "places",
                  "source_url": b["source_url"] or r.get("source_url") or "",
                  "found_at": r.get("found_at") or "", "place_id": r.get("place_id") or ""})

    for r in sorted(actions, key=lambda a: str(a.get("created_at") or "")):
        params = r.get("params") or {}
        if not params.get("to_email"):
            continue
        status = r.get("status")
        is_followup = r.get("action") == "send_outreach_followup"
        live = ((status in ("proposed", "awaiting_staff") and not _action_expired(r, now))
                or status == "running" or (status == "failed" and r.get("outcome") == "send_error"))
        if not live and (int(r.get("client_id") or 0),
                         str(params["to_email"]).strip().lower()) not in biz:
            continue   # a cancelled / expired draft alone is not a business on the page
        b = entry(r.get("client_id"), params["to_email"])
        if status in ("proposed", "awaiting_staff") and not _action_expired(r, now):
            if is_followup:
                b["followup"] = "pending" if b["followup"] != "sent" else "sent"
            else:
                b["draft_id"] = r.get("id")
        elif status == "running" and not is_followup:
            b["approved"] = True
        elif status == "failed" and r.get("outcome") == "send_error" and not is_followup:
            b["send_failed"] = True
        if not is_followup:
            b["business_name"] = b["business_name"] or params.get("business_name") or ""
            b["subject"] = b["subject"] or params.get("subject") or ""
            b["source_url"] = b["source_url"] or params.get("source_url") or ""

    for r in sorted(sends, key=lambda s: str(s.get("sent_at") or "")):
        b = entry(r.get("client_id"), r.get("to_email"))
        if (r.get("kind") or "first") == "followup":
            b["followup"], b["followup_sent_at"] = "sent", r.get("sent_at") or ""
            continue
        b.update({"sent_at": r.get("sent_at") or "", "subject": r.get("subject") or b["subject"],
                  "business_name": r.get("business_name") or b["business_name"],
                  "source_url": r.get("source_url") or b["source_url"],
                  "from_email": r.get("from_email") or "", "message_id": r.get("gmail_message_id") or "",
                  "send_failed": False})

    for r in sorted(marks, key=lambda m: str(m.get("marked_at") or "")):
        b = entry(r.get("client_id"), r.get("to_email"))
        b["mark"], b["mark_at"] = r.get("mark") or "", r.get("marked_at") or ""

    blocked = _suppressed({e for (_, e) in biz if e}, gaps)
    rows = [_row(b, b["to_email"] in blocked, now) for b in biz.values() if b["to_email"]]
    rows.sort(key=lambda r: (r["sent_at"] or r["found_at"] or "", r["to_email"]), reverse=True)
    return {"rows": rows, "gaps": sorted(set(gaps))}


def _status(b: dict, suppressed: bool, now: datetime) -> str:
    if b["mark"] == "removed" or suppressed:
        return "removed"
    if b["mark"] == "bounced" or (b["send_failed"] and not b["sent_at"]):
        return "failed"
    if b["mark"] == "replied":
        return "replied"
    if b["sent_at"]:
        last = _parse(b["followup_sent_at"] or b["sent_at"])
        return "no_response" if last and now - last >= timedelta(days=NO_RESPONSE_DAYS) else "sent"
    if b["approved"]:
        return "approved"
    if b["draft_id"]:
        return "draft_pending"
    return "found"


def _row(b: dict, suppressed: bool, now: datetime) -> dict:
    status = _status(b, suppressed, now)
    return {
        "key": b["to_email"], "client_id": b["client_id"],
        "business_name": b["business_name"], "area": b["area"],
        "source": b["source"], "source_label": SOURCE_HE.get(b["source"], ""),
        "to_email": b["to_email"], "source_url": b["source_url"], "subject": b["subject"],
        "sent_at": b["sent_at"], "sent_date": israel_date(b["sent_at"]),
        "found_at": b["found_at"], "found_date": israel_date(b["found_at"]),
        "gmail_link": gmail_link(b["from_email"], b["message_id"]),
        "status": status, "status_label": STATUS_HE[status],
        "followup": b["followup"], "followup_label": FOLLOWUP_HE[b["followup"]],
        "followup_date": israel_date(b["followup_sent_at"]),
        "draft_id": b["draft_id"],
        "place_id": b["place_id"],   # the one Places field we may keep; suppressed on "removed"
        "can_mark": [m for m, allowed in MARKABLE.items() if status in allowed],
        "can_delete": status in DELETABLE,
        # The agent may PROPOSE one follow-up (client approves it, like the first)
        "followup_due": status == "no_response" and b["followup"] == "none",
    }


def counters(rows: list) -> dict:
    by = {s: 0 for s in STATUSES}
    for r in rows:
        by[r["status"]] += 1
    return {"found": len(rows), "sent": sum(1 for r in rows if r["sent_at"]),
            "replied": by["replied"], "removed": by["removed"], "by_status": by}


def page(client_id: int) -> dict:
    """Everything the client's Outreach page shows."""
    from core import gmail_agent_plan as plan
    from core.gmail_outreach import sent_today
    data = collect(client_id)
    sending = _sending_enabled()
    return {"rows": data["rows"], "counters": counters(data["rows"]), "gaps": data["gaps"],
            "sending_enabled": sending,
            "cap": {"sent_today": sent_today(client_id) if sending else 0,
                    "limit": plan.MESSAGES_PER_CLIENT_PER_DAY},
            "no_response_days": NO_RESPONSE_DAYS}


def for_agent(client_id: int) -> dict:
    """client_state.outreach: counts, open drafts, and the businesses a
    follow-up may be PROPOSED for (the card is still the client's click)."""
    data = collect(client_id)
    if data["gaps"]:
        # Never a fake zero to a model: the section is left out instead
        raise RuntimeError(f"outreach record unreadable: {data['gaps']}")
    rows = data["rows"]
    return {"counters": {k: v for k, v in counters(rows).items() if k != "by_status"},
            "drafts_waiting": sum(1 for r in rows if r["status"] == "draft_pending"),
            "sending_enabled": _sending_enabled(),
            "followup_due": [{"business_name": r["business_name"], "to_email": r["to_email"],
                              "first_subject": r["subject"], "sent_date": r["sent_date"]}
                             for r in rows if r["followup_due"]][:10]}


# ─── Filtering + export ──────────────────────────────────────────────────────

def filter_rows(rows: list, status: str = "", q: str = "", keys=None, client_id=None) -> list:
    out = rows
    if client_id is not None:
        out = [r for r in out if r["client_id"] == int(client_id)]
    if status:
        out = [r for r in out if r["status"] == status]
    if q:
        needle = q.strip().lower()
        out = [r for r in out if needle in " ".join(
            str(r.get(k) or "") for k in ("business_name", "area", "to_email", "subject")).lower()]
    if keys:
        # "email" (one client's page) or "client_id|email" (the admin list,
        # where two clients may have written to the same address)
        wanted = {str(k).strip().lower() for k in keys}
        out = [r for r in out if r["key"] in wanted or f"{r['client_id']}|{r['key']}" in wanted]
    return out


def export_table(rows: list, admin: bool = False, client_names: dict = None) -> tuple:
    """(columns, rows) holding ONLY the export columns."""
    columns = ADMIN_COLUMNS if admin else COLUMNS
    names = client_names or {}
    out = []
    for r in rows:
        line = {key: r.get(key, "") for key, _ in COLUMNS}
        if admin:
            line["client_label"] = f"{names.get(r['client_id'], '')} (#{r['client_id']})".strip()
        out.append(line)
    return columns, out


def summary_lines(rows: list, cap: dict = None) -> list:
    c = counters(rows)
    lines = [f"נמצאו: {c['found']}", f"נשלחו: {c['sent']}", f"ענו: {c['replied']}",
             f"ביקשו להסיר: {c['removed']}"]
    if cap:
        lines.append(f"נשלחו היום: {cap.get('sent_today', 0)} מתוך {cap.get('limit', 0)}")
    return lines


# ─── The client's marks and deletions ────────────────────────────────────────

def _cancel_open_cards(client_id: int, email: str, outcome: str):
    from core import agent_actions as aa
    try:
        rows = (_db().table(aa.TABLE).select("id,action,params,status").eq("client_id", int(client_id))
                .in_("status", list(aa.OPEN_STATUSES)).in_("action", list(ACTIONS)).execute().data or [])
    except Exception:
        return
    for r in rows:
        if (r.get("params") or {}).get("to_email") == email:
            aa._claim(r["id"], aa.OPEN_STATUSES, {"status": "cancelled", "finished_at": _now().isoformat(),
                                                  "outcome": outcome})


def _suppress_business(client_id: int, row: dict, by: str):
    """Do-not-contact FIRST: the address, the business's own domain (only
    when the address is on the same domain as its site — never gmail.com),
    and its place_id when we have one. Raises when it cannot be written."""
    from urllib.parse import urlsplit

    from core.domains import email_domain, registered_domain
    email = row["to_email"]
    note = f"client {int(client_id)}"
    policy.suppress("email", email, "asked to be removed (marked by the client)", "outreach_page", by, note)
    site_domain = registered_domain(urlsplit(row.get("source_url") or "").hostname or "")
    if site_domain and email_domain(email) == site_domain:
        policy.suppress("domain", site_domain, "asked to be removed (marked by the client)", "outreach_page",
                        by, note)
    if row.get("place_id"):
        policy.suppress("place_id", row["place_id"], "asked to be removed (marked by the client)",
                        "outreach_page", by, note)


def mark(client_id: int, keys: list, mark_as: str, by: str = "client") -> dict:
    """One click (or a bulk selection) → the client's mark per business.
    'removed' writes the do-not-contact list FIRST — if that fails, nothing is
    marked — then cancels any open draft/follow-up card for the business."""
    if mark_as not in policy.MARKS:
        raise OutreachError("bad_mark")
    data = collect(client_id)
    if policy.MARKS_TABLE in data["gaps"]:
        raise OutreachError("not_ready")
    rows = {r["key"]: r for r in data["rows"]}
    done, failed = [], []
    for key in dict.fromkeys(str(k).strip().lower() for k in keys or []):
        row = rows.get(key)
        if not row:
            failed.append({"key": key, "code": "not_found"})   # another client's key looks the same
            continue
        if row["status"] not in MARKABLE[mark_as]:
            failed.append({"key": key, "code": "not_markable"})
            continue
        try:
            if mark_as == "removed":
                _suppress_business(client_id, row, by)
            _db().table(policy.MARKS_TABLE).insert({
                "client_id": int(client_id), "to_email": key, "mark": mark_as,
                "marked_by": by, "marked_at": _now().isoformat()}).execute()
        except Exception as e:
            print(f"[outreach_tracker] client {client_id}: mark {mark_as} failed: {type(e).__name__}")
            failed.append({"key": key, "code": "not_saved"})
            continue
        if mark_as == "removed":
            _cancel_open_cards(client_id, key, "business_removed")
        done.append(key)
    return {"done": done, "failed": failed}


def delete(client_id: int, keys: list) -> dict:
    """Remove found / draft rows the client doesn't want. A business we
    already wrote to stays: its send record is what enforces "one message
    per business" and is the evidence of what was sent."""
    data = collect(client_id)
    rows = {r["key"]: r for r in data["rows"]}
    done, failed = [], []
    for key in dict.fromkeys(str(k).strip().lower() for k in keys or []):
        row = rows.get(key)
        if not row:
            failed.append({"key": key, "code": "not_found"})
            continue
        if not row["can_delete"]:
            failed.append({"key": key, "code": "has_send_record"})
            continue
        try:
            (_db().table("prospect_contacts").delete().eq("client_id", int(client_id))
             .eq("email", key).execute())
        except Exception as e:
            if "prospect_contacts" not in data["gaps"]:
                print(f"[outreach_tracker] client {client_id}: delete failed: {type(e).__name__}")
                failed.append({"key": key, "code": "not_saved"})
                continue
        _cancel_open_cards(client_id, key, "deleted_by_client")
        done.append(key)
    return {"done": done, "failed": failed}
