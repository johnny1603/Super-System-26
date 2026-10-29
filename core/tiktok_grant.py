"""TikTok grant record: does the client's stored TikTok grant still work, and
does it carry video.upload (the inbox-upload scope)?

Why it exists: grants made before 2026-10-29 were for video.publish, not
video.upload, and nothing stored which scopes a grant had. Without a record
the chat would offer "send to your TikTok drafts", the click would fail at
TikTok, and the client would get a generic error. Now:

- The OAuth callback writes the scopes TikTok says it granted (`record_scopes`).
- Every token refresh writes them again (TikTok's refresh response carries
  `scope`), so an OLD grant is learned the first time it is used — the
  send_tiktok_to_inbox proposal does that one refresh before showing a card.
- A refresh refused with a dead-grant code → `record_dead`; an upload refused
  with a scope error → `record_missing_upload`.

`state()` is "ok" | "missing_upload" | "dead" | "unknown" (no record yet).
Bad states make the Performance card needs_attention/reconnect, which
client_state passes on and agent_actions turns into the connect_tiktok card.

There is no fingerprint binding (TikTok rotates the refresh token on every
refresh): the callback OVERWRITES the record for the new grant instead.
ONE plain admin alert on the transition to a bad state. Stored per client in
app_settings `tiktok_grant:<id>` (scope names only — never a token);
client_deletion clears it and deletion_guard covers it.
"""
import time
from datetime import datetime, timezone

SERVICE_NAME = "tiktok_grant"
RECORD_PREFIX = "tiktok_grant:"
UPLOAD_SCOPE = "video.upload"   # = tiktok_service.UPLOAD_SCOPE (kept import-free)
BAD_STATES = ("missing_upload", "dead")
MEMORY_TTL = 60

_memory = {}   # client_id -> (time, value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _alert(issue: str):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, [issue])


def record(client_id: int) -> dict:
    hit = _memory.get(client_id)
    if hit and hit[0] > time.time() - MEMORY_TTL:
        return hit[1]
    try:
        rows = (_db().table("app_settings").select("value")
                .eq("key", f"{RECORD_PREFIX}{int(client_id)}").limit(1).execute().data or [])
        value = rows[0].get("value") if rows else {}
        value = value if isinstance(value, dict) else {}
    except Exception:
        return {}   # unreadable: unknown (the call itself will tell)
    _memory[client_id] = (time.time(), value)
    return value


def state(client_id: int) -> str:
    return record(client_id).get("state") or "unknown"


def needs_reconnect(client_id: int) -> bool:
    return state(client_id) in BAD_STATES


def _write(client_id: int, value: dict):
    previous = record(client_id).get("state")
    _memory[client_id] = (time.time(), value)
    try:
        _db().table("app_settings").upsert(
            {"key": f"{RECORD_PREFIX}{int(client_id)}", "value": value, "updated_at": _now()},
            on_conflict="key").execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] record not stored for client {client_id}: {type(e).__name__}")
    if value["state"] in BAD_STATES and previous != value["state"]:
        why = ("the connected account never granted video.upload (an older connection made for "
               "video.publish, or the box was unticked)" if value["state"] == "missing_upload"
               else f"TikTok no longer accepts the stored access ({value.get('reason') or 'revoked'})")
        _alert(f"client {client_id}: TikTok connection needs reconnect — {why}. The client's "
               f"dashboard card and chat now offer a reconnect button.")


def record_scopes(client_id: int, granted) -> str:
    """From a token response's comma-separated `scope`. Empty/missing →
    nothing learned, the record is left as it was. Returns the state."""
    scopes = sorted({s.strip() for s in str(granted or "").split(",") if s.strip()})
    if not scopes:
        return state(client_id)
    new_state = "ok" if UPLOAD_SCOPE in scopes else "missing_upload"
    previous = record(client_id)
    if previous.get("state") == new_state and previous.get("scopes") == scopes:
        return new_state   # unchanged: no write on every refresh
    _write(client_id, {"state": new_state, "scopes": scopes, "reason": "", "checked_at": _now()})
    return new_state


def record_missing_upload(client_id: int):
    previous = record(client_id)
    _write(client_id, {"state": "missing_upload", "scopes": previous.get("scopes") or [],
                       "reason": "scope_error_on_upload", "checked_at": _now()})


def record_dead(client_id: int, reason: str):
    previous = record(client_id)
    _write(client_id, {"state": "dead", "scopes": previous.get("scopes") or [],
                       "reason": str(reason or "")[:60], "checked_at": _now()})


def clear(client_id: int):
    _memory.pop(client_id, None)
    try:
        _db().table("app_settings").delete().eq("key", f"{RECORD_PREFIX}{int(client_id)}").execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] record not cleared for client {client_id}: {type(e).__name__}")
