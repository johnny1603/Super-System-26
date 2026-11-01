"""איציק's presence on every admin page (dashboard/assets/itzik-chat.js).

The minimised avatar carries a small badge when something needs the owner:
  - new open alerts since the owner last opened the chat (`since`, kept by the
    browser) — the audit sources are records, not alerts, and never count;
  - a pending approval: a code change waiting for the owner (proposed, or a
    PR waiting for merge+deploy);
  - a post-deploy problem: the serving revision is a recent deploy with
    errors (cloud_ops.deploy_watch), or a change whose deploy failed.

When the recent deploy looks wrong (deploy_watch.suspicious, a rollback
target exists) `proactive` carries ONE line in איציק's voice with the
one-tap rollback. Its `signature` names the revision pair: the page shows a
signature at most once per browser session, so the line comes back only when
something new happens (another deploy, another rollback target).

summarize() is pure (tested); snapshot() does the reads. Admin-only: the
route is /api/admin/operator/presence behind _operator_admin.
"""
import time
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "operator_presence"
PENDING_STATUSES = ("proposed", "pr_open")
DEFAULT_LOOKBACK_HOURS = 24
MAX_LOOKBACK_DAYS = 7
ALERTS_SCAN_LIMIT = 200
CLOUD_CACHE_SECONDS = 60      # every admin page polls this; the log read is the costly part

_cloud_cache = {"at": 0.0, "watch": None}


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _since(value) -> str:
    """The browser's "last opened" time, clamped; anything unreadable → 24 h ago."""
    now = datetime.now(timezone.utc)
    floor = now - timedelta(days=MAX_LOOKBACK_DAYS)
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        at = now - timedelta(hours=DEFAULT_LOOKBACK_HOURS)
    return min(max(at, floor), now).isoformat()


def summarize(watch, changes: list, new_alerts: int) -> dict:
    """What the avatar shows: badge count, the Hebrew lines behind it, and
    the proactive rollback line (or None)."""
    watch = watch or {}
    changes = changes or []
    items = []
    new_alerts = max(0, int(new_alerts or 0))
    if new_alerts:
        items.append({"kind": "alerts", "count": new_alerts,
                      "text_he": "התראה חדשה אחת" if new_alerts == 1 else f"{new_alerts} התראות חדשות"})
    pending = [c for c in changes if c.get("status") in PENDING_STATUSES]
    if pending:
        items.append({"kind": "approval", "count": len(pending),
                      "text_he": "שינוי אחד מחכה לאישור שלך" if len(pending) == 1
                      else f"{len(pending)} שינויים מחכים לאישור שלך"})
    failed = [c for c in changes if c.get("status") == "deploy_failed"]
    if failed:
        items.append({"kind": "deploy_failed", "count": len(failed),
                      "text_he": f"פריסה של שינוי #{failed[0].get('id')} נכשלה"})
    errors = watch.get("errors") or 0
    if watch.get("recent_deploy") and errors:
        items.append({"kind": "deploy_errors", "count": 1,
                      "text_he": f"{errors} שגיאות מאז הפריסה האחרונה"})
    proactive = None
    if watch.get("suspicious") and watch.get("rollback_to"):
        proactive = {
            "kind": "rollback",
            "revision": watch["rollback_to"],
            "signature": f"rollback|{watch.get('revision', '')}|{watch['rollback_to']}",
            "text_he": (f"בדקתי את הגרסה שעלתה עכשיו ({watch.get('revision', '?')}): {errors} שגיאות מאז "
                        f"שעלתה. אני ממליץ לחזור לגרסה הקודמת ({watch['rollback_to']}) — לחיצה אחת, "
                        "ואפשר לחזור קדימה באותה קלות."),
        }
    return {"badge": sum(i["count"] for i in items), "items": items, "proactive": proactive}


def _watch():
    from core import cloud_ops
    if time.time() - _cloud_cache["at"] < CLOUD_CACHE_SECONDS:
        return _cloud_cache["watch"]
    try:
        watch = cloud_ops.deploy_watch()
    except cloud_ops.CloudOpsUnavailable:
        watch = None
    except Exception as e:
        print(f"[{SERVICE_NAME}] deploy watch failed: {type(e).__name__}")
        watch = None
    _cloud_cache.update(at=time.time(), watch=watch)
    return watch


def _changes() -> list:
    from core import operator_changes
    try:
        return operator_changes.recent(8)
    except Exception:
        return []


def _new_alerts(since: str) -> int:
    from core.bulk_delete import AUDIT_SOURCES
    try:
        rows = (_db().table("alerts").select("id, source").eq("status", "open").gt("created_at", since)
                .limit(ALERTS_SCAN_LIMIT).execute().data or [])
    except Exception:
        return 0
    return sum(1 for r in rows if r.get("source") not in AUDIT_SOURCES)


def snapshot(since=None) -> dict:
    from core.operator_agent import DISPLAY_NAME
    since = _since(since)
    out = summarize(_watch(), _changes(), _new_alerts(since))
    return {"name": DISPLAY_NAME, "since": since, **out}
