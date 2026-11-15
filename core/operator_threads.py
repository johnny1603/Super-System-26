"""איציק's conversations: archive the current thread, read an archived one,
delete an archived one (2026-11-16, docs/OPERATOR.md §Conversations).

operator_messages holds ONE endless thread. This module splits it into
conversations by TIME, so it needs no migration: the current conversation is
every row since `current_started_at`; an archived conversation is the rows
between its `started_at` and `ended_at`. The bookkeeping is one app_settings row,
`operator_threads`.

- **Archive first.** "New conversation" archives the current one: nothing is
  deleted, it is listed with its title (the owner's first line), date and size,
  and can be opened read-only. איציק's memory (`operator_agent.history`) then
  starts empty.
- **Delete only an archived conversation**, and only with the typed phrase
  "מחק" (the page asks to archive first). It removes that conversation's
  MESSAGE rows. Saved reports (kind report) are kept: they are records.
- **The audit log is never touched.** Archive and delete are each written to
  the `alerts` audit (source "operator", operator_actions.audit) BEFORE they
  happen; no audit row = nothing happens. The action audit, the approval
  gates, the three tiers and the emailed codes do not live in
  operator_messages, and nothing here reads or writes them.
"""
import uuid
from datetime import datetime, timezone

KEY = "operator_threads"
CONFIRM_PHRASE = "מחק"
ARCHIVE_MAX = 200
TITLE_MAX = 80


class ThreadError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table() -> str:
    from core.operator_agent import TABLE
    return TABLE


def state() -> dict:
    try:
        rows = _db().table("app_settings").select("value").eq("key", KEY).limit(1).execute().data or []
    except Exception:
        rows = []
    value = (rows[0].get("value") if rows else None) or {}
    return {"current_started_at": str(value.get("current_started_at") or ""),
            "archived": [a for a in (value.get("archived") or []) if isinstance(a, dict) and a.get("id")]}


def _save(value: dict):
    _db().table("app_settings").upsert({"key": KEY, "value": value, "updated_at": _now()},
                                       on_conflict="key").execute()


def current_started_at() -> str:
    """Where the current conversation begins ("" = the whole table)."""
    return state()["current_started_at"]


def _rows_between(start: str, end: str, columns: str = "id, created_at, role, kind, content, cards") -> list:
    query = _db().table(_table()).select(columns)
    if start:
        query = query.gte("created_at", start)
    if end:
        query = query.lt("created_at", end)
    return query.order("id").limit(2000).execute().data or []


def _title(rows: list) -> str:
    first = next((r.get("content") or "" for r in rows if r.get("role") == "owner" and r.get("kind") == "message"), "")
    first = " ".join(str(first).split())
    return (first[:TITLE_MAX] + "…") if len(first) > TITLE_MAX else (first or "שיחה")


def archive(who: str) -> dict:
    """Close the current conversation into the archive. Nothing is deleted."""
    from core import operator_actions
    value = state()
    now = _now()
    rows = _rows_between(value["current_started_at"], now, "id, created_at, role, kind, content")
    if not [r for r in rows if r.get("kind") == "message"]:
        raise ThreadError("ERR_NOTHING_TO_ARCHIVE")
    entry = {"id": uuid.uuid4().hex[:12], "started_at": value["current_started_at"] or rows[0].get("created_at", ""),
             "ended_at": now, "title": _title(rows), "count": len(rows)}
    operator_actions.audit("conversation_archived", who, f"{entry['id']}: {entry['count']} rows, {entry['title']}")
    value["archived"] = ([entry] + value["archived"])[:ARCHIVE_MAX]
    value["current_started_at"] = now
    _save(value)
    return entry


def archived() -> list:
    return state()["archived"]


def read(thread_id: str) -> dict:
    entry = next((a for a in archived() if a["id"] == str(thread_id)), None)
    if not entry:
        raise ThreadError("ERR_NO_SUCH_CONVERSATION")
    return {**entry, "messages": _rows_between(entry.get("started_at", ""), entry.get("ended_at", ""))}


def delete(thread_id: str, confirm: str, who: str) -> dict:
    """Delete ONE archived conversation's messages. The typed phrase is required;
    the current conversation can't be deleted (archive it first); reports and
    the audit log stay."""
    from core import operator_actions
    if str(confirm or "").strip() != CONFIRM_PHRASE:
        raise ThreadError("ERR_CONFIRM_PHRASE")
    value = state()
    entry = next((a for a in value["archived"] if a["id"] == str(thread_id)), None)
    if not entry:
        raise ThreadError("ERR_NO_SUCH_CONVERSATION")
    if not entry.get("ended_at"):
        raise ThreadError("ERR_NOT_ARCHIVED")
    operator_actions.audit("conversation_deleted", who,
                           f"{entry['id']}: {entry.get('count', 0)} rows from {entry.get('started_at')} to "
                           f"{entry['ended_at']}, {entry.get('title', '')} (reports kept)")
    query = _db().table(_table()).delete().eq("kind", "message").lt("created_at", entry["ended_at"])
    if entry.get("started_at"):
        query = query.gte("created_at", entry["started_at"])
    query.execute()
    value["archived"] = [a for a in value["archived"] if a["id"] != entry["id"]]
    _save(value)
    return {"deleted": entry["id"], "title": entry.get("title", "")}
