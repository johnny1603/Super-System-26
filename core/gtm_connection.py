"""The client's Google Tag Manager connection as the client sees it: WHICH
container is connected, whether it is the one their website actually loads,
and whether the stored grant still works.

Before this, the OAuth callback silently stored the container it guessed
(the one detected on the site, else the first one the login could see), the
card said "מחובר ✓" whatever happened to the token afterwards, and nothing
told anyone that the chosen container and the site's snippet could differ.

- container_options / select_container: the picker. Listed live through the
  client's OWN stored grant (accounts.list + containers.list need only
  tagmanager.edit.containers, which every GTM grant has), so changing the
  container never needs a new consent while that login still has access.
  A pick is accepted only if it is in that live list — nothing the browser
  sends is trusted.
- connection_view: container name + GTM-XXXX, the site match, token state.
  One containers.get call (also the token-health probe) and one homepage
  fetch, each cached VIEW_TTL per client — the card loads it AFTER the
  dashboard, so a slow Google or site never blocks the page.
- Token health is stored per client in app_settings (`gtm_token:<id>`), not
  client_activity: the client's activity feed shows unknown action types
  raw. client_deletion clears the row with the client's other settings.
- publish_block_reason: configure_lead_conversion refuses to publish into a
  container the site does not load.
"""
import time
from datetime import datetime, timezone

SERVICE_NAME = "gtm_connection"
VIEW_TTL = 600
HEALTH_WRITE_EVERY = 3600   # an unchanged "ok" is re-stamped at most hourly
HEALTH_PREFIX = "gtm_token:"

_view_cache = {}   # client_id -> (time, view)
_site_cache = {}   # client_id -> (time, site dict)

# Google reasons that mean the stored grant itself is dead (revoked, expired,
# or issued to another OAuth client) — only a new consent fixes it
_TOKEN_DEAD = ("invalid_grant", "unauthorized_client", "invalid_client")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _connection(client_id: int) -> dict:
    from agents.website_agent import _gtm_connection
    return _gtm_connection(client_id)


def forget(client_id: int):
    _view_cache.pop(client_id, None)
    _site_cache.pop(client_id, None)


# ─── Token health (app_settings, never a token) ──────────────────────────────

def health(client_id: int) -> dict:
    """{"ok": bool, "reason": str, "checked_at": iso, "last_ok_at": iso} or {}."""
    try:
        rows = (_db().table("app_settings").select("value")
                .eq("key", f"{HEALTH_PREFIX}{int(client_id)}").limit(1).execute().data or [])
        value = rows[0].get("value") if rows else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _record_health(client_id: int, ok: bool, reason: str = ""):
    previous = health(client_id)
    now = _now()
    if (previous.get("ok") == ok and previous.get("reason", "") == reason and ok
            and _age_seconds(previous.get("checked_at")) < HEALTH_WRITE_EVERY):
        return
    if previous.get("ok") == ok and not ok and previous.get("reason", "") == reason:
        return   # still dead for the same reason — keep the first failure time
    value = {"ok": ok, "reason": reason, "checked_at": now,
             "last_ok_at": now if ok else previous.get("last_ok_at")}
    try:
        _db().table("app_settings").upsert(
            {"key": f"{HEALTH_PREFIX}{int(client_id)}", "value": value, "updated_at": now},
            on_conflict="key").execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] token health not stored for client {client_id}: {type(e).__name__}")


def _age_seconds(iso) -> float:
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - when).total_seconds()
    except Exception:
        return float("inf")


def _failure(e: Exception) -> dict:
    from core.performance import _classify_google_failure
    failure = _classify_google_failure(e, "tagmanager.googleapis.com")
    google_reason = failure.get("google_reason") or ""
    if google_reason in _TOKEN_DEAD:
        state = "token_dead"
    elif failure.get("http_status") in (403, 404) and failure.get("reason") != "access_pending":
        state = "container_unavailable"   # the login lost access, or the container is gone
    elif failure.get("reason") == "access_pending":
        state = "access_pending"          # our side: API not enabled / access not approved
    else:
        state = "unavailable"             # temporary or unknown — nothing is concluded
    return {**failure, "state": state}


def _warn(message: str, **fields):
    from core.performance import _warn as perf_warn
    perf_warn(message, **fields)


# ─── What the site loads ─────────────────────────────────────────────────────

def site_gtm(client_id: int, refresh: bool = False) -> dict:
    """{"status": no_site | found | none | unknown, "gtm_id": "GTM-…" | None}.
    'none' = the homepage was read and has no GTM snippet; 'unknown' = it
    could not be read (that is not proof of anything)."""
    hit = _site_cache.get(client_id)
    if hit and not refresh and hit[0] > time.time() - VIEW_TTL:
        return hit[1]
    from agents import website_agent
    from core import wordpress_service as wp
    site = website_agent._get_connection(client_id)
    site_url = (site or {}).get("account_id") or ""
    if not site_url:
        result = {"status": "no_site", "gtm_id": None}
    else:
        try:
            detected = website_agent._detect_tracking_tags(wp.fetch_homepage_html(site_url))
            gtm_id = detected.get("gtm_container_id")
            result = {"status": "found" if gtm_id else "none", "gtm_id": gtm_id}
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: homepage read failed ({type(e).__name__})")
            result = {"status": "unknown", "gtm_id": None}
    _site_cache[client_id] = (time.time(), result)
    return result


def match_status(public_id: str, site: dict) -> str:
    """match | mismatch | no_gtm_on_site | no_site | unknown."""
    if site.get("status") == "no_site":
        return "no_site"
    if site.get("status") == "none":
        return "no_gtm_on_site"
    if site.get("status") != "found" or not public_id:
        return "unknown"
    return "match" if site["gtm_id"].upper() == public_id.upper() else "mismatch"


# ─── The connection view (card + Control Room) ───────────────────────────────

def _recorded_container(client_id: int, path: str) -> dict:
    """Name / public id as recorded at connect or selection time — the
    fallback when Google can't be asked right now."""
    try:
        rows = (_db().table("client_activity").select("details,action_type")
                .eq("client_id", client_id).eq("agent_name", "website_agent")
                .in_("action_type", ["gtm_container_selected", "gtm_api_connected"])
                .order("created_at", desc=True).limit(20).execute().data or [])
    except Exception:
        return {}
    for row in rows:
        details = row.get("details") or {}
        if details.get("container_path") == path:
            return {"name": details.get("container_name", ""), "public_id": details.get("public_id", ""),
                    "account_name": details.get("account_name", "")}
    return {}


def connection_view(client_id: int, refresh: bool = False) -> dict:
    hit = _view_cache.get(client_id)
    if hit and not refresh and hit[0] > time.time() - VIEW_TTL:
        return hit[1]
    from core import gtm_service as gtm
    conn = _connection(client_id)
    if not conn.get("access_token") or not conn.get("account_id"):
        return {"connected": False}
    path = conn["account_id"]
    container = _recorded_container(client_id, path)
    token = {"state": "ok"}
    try:
        live = gtm.get_container(conn["access_token"], path)
        container = {**container, "name": live.get("name") or container.get("name", ""),
                     "public_id": live.get("publicId") or container.get("public_id", "")}
        _record_health(client_id, True)
    except Exception as e:
        failure = _failure(e)
        token = {"state": failure["state"]}
        _warn(f"gtm connection check failed for client {client_id}: {failure['api']} "
              f"HTTP {failure['http_status']} {failure['google_reason'] or type(e).__name__} "
              f"-> {failure['state']}", client_id=client_id, channel="gtm_connection",
              state=failure["state"], http_status=failure["http_status"],
              google_reason=failure["google_reason"])
        if failure["state"] in ("token_dead", "container_unavailable"):
            _record_health(client_id, False, failure["state"])
    site = site_gtm(client_id, refresh=refresh)
    view = {"connected": True, "container_path": path,
            "container": {"name": container.get("name", ""), "public_id": container.get("public_id", ""),
                          "account_name": container.get("account_name", "")},
            "site": site, "match": match_status(container.get("public_id", ""), site),
            "token": token, "checked_at": _now()}
    _view_cache[client_id] = (time.time(), view)
    return view


def publish_block_reason(client_id: int) -> str:
    """Why configure_lead_conversion must not publish right now ('' = go).
    Checked fresh: the container the site loads can change at any time."""
    view = connection_view(client_id, refresh=True)
    if not view.get("connected"):
        return ""
    if view["token"]["state"] == "token_dead":
        return "gtm_token_dead"
    if view["match"] == "mismatch":
        return "gtm_container_mismatch"
    return ""


# ─── The picker ──────────────────────────────────────────────────────────────

def container_options(client_id: int) -> dict:
    """Every container the client's stored login can reach, grouped by
    account, plus the current one. {"success": bool, "accounts": [...],
    "current": path, "site_gtm_id": str|None, "state": str}."""
    from core import gtm_service as gtm
    conn = _connection(client_id)
    if not conn.get("access_token"):
        return {"success": False, "state": "not_connected", "accounts": []}
    try:
        accounts = []
        for account in gtm.list_accounts(conn["access_token"]):
            containers = gtm.list_containers(conn["access_token"], account["path"])
            accounts.append({
                "account_name": account.get("name", ""),
                "containers": [{"path": c["path"], "name": c.get("name", ""),
                                "public_id": c.get("publicId", "")} for c in containers],
            })
    except Exception as e:
        failure = _failure(e)
        _warn(f"gtm container list failed for client {client_id}: HTTP {failure['http_status']} "
              f"{failure['google_reason'] or type(e).__name__} -> {failure['state']}",
              client_id=client_id, channel="gtm_connection", state=failure["state"])
        if failure["state"] == "token_dead":
            _record_health(client_id, False, "token_dead")
            forget(client_id)
        return {"success": False, "state": failure["state"], "accounts": []}
    _record_health(client_id, True)
    site = site_gtm(client_id)
    return {"success": True, "state": "ok", "accounts": accounts,
            "current": conn.get("account_id", ""), "site_gtm_id": site.get("gtm_id")}


def select_container(client_id: int, container_path: str) -> dict:
    from agents.client_agent import log_activity
    options = container_options(client_id)
    if not options.get("success"):
        return {"success": False, "code": "ERR_GTM_LIST_FAILED", "state": options.get("state")}
    chosen, account_name = None, ""
    for account in options["accounts"]:
        for container in account["containers"]:
            if container["path"] == container_path:
                chosen, account_name = container, account["account_name"]
    if not chosen:
        return {"success": False, "code": "ERR_GTM_CONTAINER_NOT_ACCESSIBLE"}
    (_db().table("client_accounts").update({"account_id": chosen["path"]})
     .eq("client_id", client_id).eq("platform", "google_tagmanager").execute())
    log_activity(client_id, "website_agent", "gtm_container_selected",
                 {"container_path": chosen["path"], "public_id": chosen["public_id"],
                  "container_name": chosen["name"], "account_name": account_name}, {})
    forget(client_id)
    return {"success": True, "view": connection_view(client_id, refresh=True)}
