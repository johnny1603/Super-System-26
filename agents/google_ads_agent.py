"""uallak's first execution agent: reads and controls a client's real Google
Ads campaigns through the account they connected via OAuth in the dashboard.

Phase 1 (Explorer Access limits): performance reporting + pause/resume.
Phase 2 (needs Basic Access approval): campaign creation, budget/bidding changes.

No LLM calls here - this agent talks to the Google Ads API, not to Claude.
The support agent feeds this agent's output into its own LLM prompt.
"""
import os
import time

from supabase import create_client as _supabase_client

from core import google_ads_service as gads
from core.agent_base import agent_alert, log_step, timed_step

AGENT_NAME = "google_ads_agent"
PLATFORM = "google_ads"

# client_id -> (fetched_at, result). Support chat may ask about campaigns
# several times per conversation - don't burn a daily-capped API operation
# on every message.
PERF_CACHE_TTL_SECONDS = 300
_perf_cache = {}

_PERFORMANCE_GAQL = """
    SELECT campaign.id, campaign.name, campaign.status,
           metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
    FROM campaign
    WHERE segments.date DURING LAST_30_DAYS
    ORDER BY metrics.cost_micros DESC
"""

# Created lazily - no DB client at import time (api_server imports every agent at startup)
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        _db_instance = _supabase_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _db_instance


def _get_connection(client_id: int) -> dict:
    """The client's active google_ads row from client_accounts:
    account_id = Google Ads customer ID, access_token = OAuth refresh token."""
    result = (
        _db().table("client_accounts")
        .select("*")
        .eq("client_id", client_id)
        .eq("platform", PLATFORM)
        .eq("status", "active")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def is_connected(client_id: int) -> bool:
    conn = _get_connection(client_id)
    return bool(conn.get("access_token") and conn.get("account_id"))


def get_campaign_performance(client_id: int, force_refresh: bool = False) -> dict:
    """Last-30-days campaign metrics for the client's connected account.
    Always returns a well-formed dict - never raises into a client-facing flow."""
    cached = _perf_cache.get(client_id)
    if cached and not force_refresh and cached[0] > time.time() - PERF_CACHE_TTL_SECONDS:
        return cached[1]

    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"connected": False}

    log_step(AGENT_NAME, "get_campaign_performance", f"client_id={client_id} customer_id={conn['account_id']}")
    try:
        rows = timed_step(
            AGENT_NAME, "gaql_search",
            lambda: gads.search(conn["access_token"], conn["account_id"], _PERFORMANCE_GAQL),
        )
    except Exception as e:
        agent_alert(AGENT_NAME, [f"performance fetch failed for client {client_id}: {e}"])
        return {"connected": True, "error": str(e), "campaigns": []}

    campaigns = []
    for row in rows:
        campaign, metrics = row.get("campaign", {}), row.get("metrics", {})
        campaigns.append({
            "id": str(campaign.get("id", "")),
            "name": campaign.get("name", ""),
            "status": campaign.get("status", ""),
            "impressions": int(metrics.get("impressions", 0)),
            "clicks": int(metrics.get("clicks", 0)),
            # cost is in the ad account's own currency (ILS for our clients)
            "cost": round(int(metrics.get("costMicros", 0)) / 1_000_000, 2),
            "conversions": round(float(metrics.get("conversions", 0)), 1),
        })

    result = {
        "connected": True,
        "period": "last_30_days",
        "customer_id": conn["account_id"],
        "campaigns": campaigns,
        "totals": {
            "impressions": sum(c["impressions"] for c in campaigns),
            "clicks": sum(c["clicks"] for c in campaigns),
            "cost": round(sum(c["cost"] for c in campaigns), 2),
            "conversions": round(sum(c["conversions"] for c in campaigns), 1),
        },
    }
    _perf_cache[client_id] = (time.time(), result)
    return result


def _set_campaign_status(client_id: int, campaign_id: str, status: str, action: str) -> dict:
    from agents.client_agent import log_activity

    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"success": False, "error": "no connected google_ads account"}

    log_step(AGENT_NAME, action, f"client_id={client_id} campaign_id={campaign_id}")
    try:
        timed_step(
            AGENT_NAME, "campaign_mutate",
            lambda: gads.set_campaign_status(conn["access_token"], conn["account_id"], campaign_id, status),
        )
    except Exception as e:
        agent_alert(AGENT_NAME, [f"{action} failed for client {client_id}, campaign {campaign_id}: {e}"])
        return {"success": False, "error": str(e)}

    _perf_cache.pop(client_id, None)  # status just changed - don't serve stale data
    log_activity(client_id, AGENT_NAME, action, {"campaign_id": campaign_id}, {"status": status})
    return {"success": True, "campaign_id": campaign_id, "status": status}


def pause_campaign(client_id: int, campaign_id: str) -> dict:
    return _set_campaign_status(client_id, campaign_id, "PAUSED", "campaign_paused")


def resume_campaign(client_id: int, campaign_id: str) -> dict:
    return _set_campaign_status(client_id, campaign_id, "ENABLED", "campaign_resumed")
