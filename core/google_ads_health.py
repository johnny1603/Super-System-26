"""Google Ads grant health: is the client's stored refresh token still alive?

Before this, a revoked or expired Google Ads grant surfaced as
"Google Ads performance fetch failed: Client error '400 Bad Request' for url
'https://oauth2.googleapis.com/token'" — the raw httpx text, re-tried on every
chat turn and dashboard load, the card said "sync failed", the agents said
they couldn't see the numbers, and nothing told anyone that only a new consent
fixes it. Same class of failure as the Tag Manager invalid_grant
(core/gtm_connection), handled the same way:

- google_ads_service._access_token reads the 400 body's error CODE
  (`dead_reason`, never the token, never the body) and raises GrantRevoked.
- google_ads_agent's readers record it here and STOP calling Google for that
  grant (`should_skip`) — one re-probe every RECHECK_SECONDS, never a retry
  loop. Their answer is a fact, `needs_reconnect`, not an error: the
  Performance card shows needs_attention/reconnect, client_state passes it to
  every agent, and agent_actions offers connect_google_ads.
- ONE plain admin alert per client per dead grant, on the transition only
  (the record itself is the durable dedup, so a restart doesn't re-alert).
- Health is bound to the grant: `grant` is google_ads_agent._token_fingerprint
  of the refresh token (a truncated sha256 — not the token, not reversible).
  A reconnect stores a NEW token, so the dead record stops matching by
  construction; the OAuth callback also clears it explicitly, and the first
  successful call afterwards writes ok.

Stored per client in app_settings (`gads_token:<id>`), NOT client_activity —
the client's activity feed shows unknown action types raw. client_deletion
clears the row with the client's other settings (_settings_keys).
"""
import time
from datetime import datetime, timezone

SERVICE_NAME = "google_ads_health"
HEALTH_PREFIX = "gads_token:"

# Google's token-endpoint error codes that mean THIS grant is dead and only a
# new consent fixes it. Not "invalid_client": that is OUR OAuth client's
# credentials (a config problem on our side — reconnecting can't fix it).
DEAD_GRANT_ERRORS = ("invalid_grant", "unauthorized_client")

RECHECK_SECONDS = 6 * 3600    # a dead grant is re-probed at most this often
OK_REWRITE_SECONDS = 3600     # an unchanged "ok" is re-stamped at most hourly
MEMORY_TTL = 60               # per-instance read cache, so a chat turn isn't a DB read

_memory = {}   # client_id -> (time, value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _alert(issue: str):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, [issue])


def dead_reason(status_code: int, body) -> str:
    """Google's error code when a token refresh answer means the grant is
    dead, else "". `body` is the parsed JSON body (or anything else)."""
    if status_code != 400 or not isinstance(body, dict):
        return ""
    code = str(body.get("error") or "")[:60]
    return code if code in DEAD_GRANT_ERRORS else ""


def _age_seconds(iso) -> float:
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - when).total_seconds()
    except Exception:
        return float("inf")


def health(client_id: int) -> dict:
    """{"ok", "reason", "grant", "checked_at", "dead_since", "last_ok_at"} or {}."""
    hit = _memory.get(client_id)
    if hit and hit[0] > time.time() - MEMORY_TTL:
        return hit[1]
    try:
        rows = (_db().table("app_settings").select("value")
                .eq("key", f"{HEALTH_PREFIX}{int(client_id)}").limit(1).execute().data or [])
        value = rows[0].get("value") if rows else {}
        value = value if isinstance(value, dict) else {}
    except Exception:
        return {}   # unreadable: assume nothing (the call itself will tell)
    _memory[client_id] = (time.time(), value)
    return value


def _write(client_id: int, value: dict):
    _memory[client_id] = (time.time(), value)
    try:
        _db().table("app_settings").upsert(
            {"key": f"{HEALTH_PREFIX}{int(client_id)}", "value": value, "updated_at": _now()},
            on_conflict="key").execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] health not stored for client {client_id}: {type(e).__name__}")


def needs_reconnect(client_id: int, grant: str) -> bool:
    """True while THIS grant is recorded dead (a new grant is never)."""
    h = health(client_id)
    return h.get("ok") is False and bool(grant) and h.get("grant") == grant


def should_skip(client_id: int, grant: str) -> bool:
    """Don't call Google with a grant known dead — except one re-probe every
    RECHECK_SECONDS (a grant Google restored on its side recovers by itself)."""
    return needs_reconnect(client_id, grant) and _age_seconds(health(client_id).get("checked_at")) < RECHECK_SECONDS


def record_dead(client_id: int, grant: str, reason: str) -> bool:
    """Record the grant as dead. True (and ONE alert) only on the transition
    to dead for this grant; a re-probe that finds it still dead just
    re-stamps checked_at."""
    previous = health(client_id)
    already = previous.get("ok") is False and previous.get("grant") == grant
    now = _now()
    _write(client_id, {"ok": False, "reason": reason, "grant": grant, "checked_at": now,
                       "dead_since": previous.get("dead_since") if already else now,
                       "last_ok_at": previous.get("last_ok_at")})
    if already:
        return False
    _alert(f"client {client_id}: Google Ads connection needs reconnect — Google no longer "
           f"accepts the stored access ({reason}: revoked, expired or replaced). We stopped "
           f"calling Google for it; the client's dashboard card and agents now offer a "
           f"reconnect button.")
    return True


def record_ok(client_id: int, grant: str):
    previous = health(client_id)
    if (previous.get("ok") is True and previous.get("grant") == grant
            and _age_seconds(previous.get("checked_at")) < OK_REWRITE_SECONDS):
        return
    now = _now()
    _write(client_id, {"ok": True, "reason": "", "grant": grant, "checked_at": now,
                       "dead_since": None, "last_ok_at": now})


def clear(client_id: int):
    """After a successful reconnect (the OAuth callback): nothing is known
    about the new grant yet; the next call records it."""
    _memory.pop(client_id, None)
    try:
        _db().table("app_settings").delete().eq("key", f"{HEALTH_PREFIX}{int(client_id)}").execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] health not cleared for client {client_id}: {type(e).__name__}")
