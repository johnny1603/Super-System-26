"""Admin-authored "this shipped — tell the relevant clients" items.

## The shape of the thing

Johnny flags a feature as ready to announce. The system then works out WHICH
clients it actually applies to, and tells each of them once, at their next
login, in the voice of the specialist who owns that feature.

Three deliberate constraints:

1. **Admin-triggered, never automatic.** Nothing here is created by a deploy or
   a commit. A row exists because a human decided clients should hear about it,
   and it only reaches anyone once its status is `live`.
2. **Targeted, never broadcast.** Relevance is decided by
   `core/feature_catalog.is_relevant` — the same catalogue the first-login
   interview explains the product from. A client with no YouTube add-on is
   never told about a YouTube feature.
3. **Once per client per announcement, forever.** Not once a day like the
   ordinary login moment: an announcement is a one-time event. Dedup is a
   `client_activity` row carrying the announcement id, the same activity-row
   pattern every other login moment uses — no "delivered to" table.

The wording is NOT stored per client. `note` holds what Johnny typed; the
login-moment LLM rewrites it for each client in the owning persona's voice, so
twenty clients don't receive the same paste.

Needs `migrations/2026-08-08-feature-announcements.sql`. Until that runs, every
read here returns empty and every write reports the failure — nothing 500s and
no client is told anything.
"""
import os
from datetime import datetime, timezone

from core.agent_base import log_step
from core.feature_catalog import feature

SERVICE_NAME = "feature_announcements"

SENT_ACTION = "feature_announcement_sent"
STATUSES = ("draft", "live", "archived")

_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"],
                                     os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def table_ready() -> bool:
    """Whether the migration has been applied. The admin screen shows a banner
    naming the file when this is False, rather than an empty list that looks
    like 'no announcements yet'."""
    try:
        _db().table("feature_announcements").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def list_announcements(status: str = None, limit: int = 100) -> list:
    try:
        query = (_db().table("feature_announcements").select("*")
                 .order("created_at", desc=True).limit(limit))
        if status:
            query = query.eq("status", status)
        rows = query.execute().data or []
    except Exception as e:
        print(f"[{SERVICE_NAME}] list failed (migration not run?): {e}")
        return []
    for row in rows:
        entry = feature(row.get("feature_key", ""))
        # Surfaced so the admin list shows what a row actually points at — an
        # orphaned feature_key (feature renamed/removed) is visible rather than
        # silently reaching nobody.
        row["feature_name"] = entry.get("name_he", "")
        row["feature_known"] = bool(entry)
        row["persona"] = entry.get("persona", "general")
    return rows


def create_announcement(feature_key: str, title: str, note: str,
                        status: str = "draft") -> dict:
    if not feature(feature_key):
        return {"success": False,
                "errors": [f"unknown feature key: {feature_key} (see core/feature_catalog.py)"]}
    if status not in STATUSES:
        return {"success": False, "errors": [f"status must be one of {STATUSES}"]}
    values = {
        "feature_key": feature_key,
        "title": (title or "").strip()[:200],
        "note": (note or "").strip()[:2000],
        "status": status,
        "published_at": datetime.now(timezone.utc).isoformat() if status == "live" else None,
    }
    if not values["note"]:
        return {"success": False, "errors": ["note is required — it is what the message is written from"]}
    try:
        row = _db().table("feature_announcements").insert(values).execute().data
    except Exception as e:
        print(f"[{SERVICE_NAME}] create failed (migration not run?): {e}")
        return {"success": False, "errors": [f"could not save: {e}"]}
    log_step(SERVICE_NAME, "created", f"{feature_key} ({status})")
    return {"success": True, "data": (row or [{}])[0]}


def set_status(announcement_id: int, status: str) -> dict:
    """Publish / unpublish / archive. Going live stamps published_at the FIRST
    time only — re-publishing something that was paused must not make it look
    newly shipped, and must never re-send it to a client who already got it
    (dedup is per announcement id, so it can't)."""
    if status not in STATUSES:
        return {"success": False, "errors": [f"status must be one of {STATUSES}"]}
    try:
        existing = (_db().table("feature_announcements").select("published_at")
                    .eq("id", announcement_id).limit(1).execute().data or [])
        values = {"status": status}
        if status == "live" and not (existing[0].get("published_at") if existing else None):
            values["published_at"] = datetime.now(timezone.utc).isoformat()
        row = (_db().table("feature_announcements").update(values)
               .eq("id", announcement_id).execute().data)
    except Exception as e:
        print(f"[{SERVICE_NAME}] status change failed: {e}")
        return {"success": False, "errors": [f"could not update: {e}"]}
    log_step(SERVICE_NAME, "status_changed", f"#{announcement_id} -> {status}")
    return {"success": True, "data": (row or [{}])[0]}


def delete_announcement(announcement_id: int) -> dict:
    try:
        removed = (_db().table("feature_announcements").delete()
                   .eq("id", announcement_id).execute().data)
    except Exception as e:
        return {"success": False, "errors": [f"could not delete: {e}"]}
    return {"success": True, "removed": len(removed or [])}


def already_sent_ids(client_id: int) -> set:
    """Announcement ids this client has already been told about. Read from
    client_activity rather than a join table — the activity log is already the
    record of everything said to a client, and a second store would drift."""
    try:
        rows = (_db().table("client_activity").select("details")
                .eq("client_id", client_id).eq("action_type", SENT_ACTION)
                .limit(200).execute().data or [])
    except Exception as e:
        # Fail CLOSED: if we cannot prove what was already sent, say nothing.
        # Repeating an announcement is worse than delaying it.
        print(f"[{SERVICE_NAME}] dedup read failed for client {client_id}: {e}")
        return None
    return {(row.get("details") or {}).get("announcement_id") for row in rows}


def next_for_client(client_id: int) -> dict:
    """The ONE announcement to deliver to this client now, or {}.

    One at a time on purpose: a client returning after a quiet month should not
    be met with five product messages at once. The rest wait for their next
    logins, oldest first.
    """
    from core.feature_catalog import is_relevant, _client_signals

    live = list_announcements(status="live", limit=50)
    if not live:
        return {}
    sent = already_sent_ids(client_id)
    if sent is None:
        return {}

    # One package/connections read for the whole loop rather than per candidate.
    signals = _client_signals(client_id)
    pending = [a for a in reversed(live)  # reversed => oldest first
               if a["id"] not in sent
               and is_relevant(a.get("feature_key", ""), client_id, signals=signals)]
    return pending[0] if pending else {}


def mark_sent(client_id: int, announcement: dict) -> None:
    from agents.client_agent import log_activity
    log_activity(client_id, SERVICE_NAME, SENT_ACTION,
                 {"announcement_id": announcement.get("id"),
                  "feature_key": announcement.get("feature_key", "")}, {})
