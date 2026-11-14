"""Alert hygiene (2026-11-15, docs/SYSTEM-REVIEW.md Part 0).

Two rules for the admin `alerts` table, both about noise, neither about
hiding anything:

1. ONE OPEN ALERT PER KEY. The key is the source plus the alert's text with
   the parts that change between repeats removed (timestamps, long ids).
   While an alert with that key is still open, the same alert is not stored
   again - the owner already has it in front of him. Once he resolves it, a
   recurrence opens a new one. This is the sink-side guard under every
   agent's own time-window dedup (chat_fallback.alert_once), which re-alerts
   a condition that simply persists (daily / weekly) and can double-fire when
   two parallel reads miss each other's marker row.

2. AN ALERT ABOUT A CONNECTION CLOSES ITSELF WHEN THE CONNECTION WORKS.
   `resolve_for_client` marks the open alerts of the given sources that name
   "client <id>" as resolved. Called on the dead -> ok transition of a grant
   and from the OAuth callbacks, so nobody is nudged about a reconnect that
   already happened.

Audit rows (bulk_delete.AUDIT_SOURCES) are history: never deduped, never
auto-resolved. Nothing here raises - alerts are written from error paths.
"""
import hashlib
import re
from datetime import datetime, timezone

SERVICE_NAME = "alert_hygiene"
OPEN_SCAN_LIMIT = 60          # newest open alerts of one source compared for a duplicate
RESOLVE_SCAN_LIMIT = 300

_ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")
_LONG_ID = re.compile(r"\b[0-9a-f]{12,}\b|\b\d{7,}\b", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


def _audit_sources() -> tuple:
    try:
        from core.bulk_delete import AUDIT_SOURCES
        return tuple(AUDIT_SOURCES)
    except Exception:
        return ("client_deleted", "bulk_delete", "admin_view", "operator", "credits")


def normalize(text) -> str:
    """The alert text without what differs between two reports of the SAME
    problem. Short numbers stay: "client 27" and "HTTP 401" are the identity."""
    text = _ISO_TIME.sub("<time>", str(text or ""))
    text = _LONG_ID.sub("<id>", text)
    return _SPACE.sub(" ", text).strip().lower()


def alert_key(source: str, issues) -> str:
    body = "\n".join(normalize(i) for i in (issues or []))
    return hashlib.sha256(f"{source}\n{body}".encode("utf-8")).hexdigest()[:24]


def open_duplicate(db, source: str, issues):
    """The id of an OPEN alert with the same key, else None. Any doubt
    (unreadable table, audit source, empty alert) = None, so the alert is
    stored: a duplicate is noise, a lost alert is a blind spot."""
    if not issues or source in _audit_sources():
        return None
    try:
        rows = (db.table("alerts").select("id,issues").eq("source", source).eq("status", "open")
                .order("created_at", desc=True).limit(OPEN_SCAN_LIMIT).execute().data or [])
    except Exception as e:
        print(f"[{SERVICE_NAME}] duplicate check skipped ({type(e).__name__})")
        return None
    wanted = alert_key(source, issues)
    for row in rows:
        if alert_key(source, row.get("issues") or []) == wanted:
            return row.get("id")
    return None


def _names_client(issues, client_id: int) -> bool:
    rx = re.compile(rf"\bclient[ _]?(id[=: ]*)?{int(client_id)}\b", re.IGNORECASE)
    return any(rx.search(str(i)) for i in (issues or []))


def resolve_for_client(client_id: int, sources, contains=(), db=None) -> int:
    """Resolve the OPEN alerts from `sources` that name this client (and, when
    `contains` is given, carry at least one of those fragments). Returns how
    many were closed. Never raises."""
    sources = [s for s in (sources or []) if s not in _audit_sources()]
    if not client_id or not sources:
        return 0
    try:
        if db is None:
            from agents.client_agent import _db
            db = _db()
        rows = (db.table("alerts").select("id,issues,source").in_("source", sources).eq("status", "open")
                .order("created_at", desc=True).limit(RESOLVE_SCAN_LIMIT).execute().data or [])
        fragments = [str(c).lower() for c in (contains or [])]
        ids = []
        for row in rows:
            issues = row.get("issues") or []
            if not _names_client(issues, client_id):
                continue
            text = " ".join(str(i) for i in issues).lower()
            if fragments and not any(f in text for f in fragments):
                continue
            ids.append(row["id"])
        if not ids:
            return 0
        (db.table("alerts").update({"status": "resolved",
                                    "resolved_at": datetime.now(timezone.utc).isoformat()})
         .in_("id", ids).eq("status", "open").execute())
        print(f"[{SERVICE_NAME}] client {client_id}: auto-resolved {len(ids)} alert(s) from "
              f"{', '.join(sources)} - the connection works now")
        return len(ids)
    except Exception as e:
        print(f"[{SERVICE_NAME}] auto-resolve skipped for client {client_id} ({type(e).__name__})")
        return 0


# What each connection's "works now" closes: (alert sources, text fragments)
CONNECTION_ALERTS = {
    "google_ads": (("google_ads_health", "google_ads_agent"),
                   ("needs reconnect", "fetch failed", "listing the google ads accounts")),
    "gtm": (("performance", "gtm_connection", "website_agent"),
            ("מדידה באתר", "tag manager", "tagmanager", "gtm")),
    "tiktok": (("tiktok_grant", "tiktok_content_agent", "tiktok_oauth"),
               ("reconnect", "renew", "no open_id", "grant")),
    "analytics": (("site_traffic",), ("traffic read failed",)),
}


def connection_works(client_id: int, connection: str) -> int:
    """The connection was just PROVEN working (a successful live call, or a
    fresh consent stored): close its open alerts for this client."""
    spec = CONNECTION_ALERTS.get(connection)
    return resolve_for_client(client_id, spec[0], spec[1]) if spec else 0
