"""uallak's Meta (Facebook + Instagram) PAID campaigns agent — the Marketing API
side of the Meta integration. Organic content (posts, comments, DMs) lives in
agents/meta_content_agent.py; both share the OAuth connection made in the
dashboard and the HTTP plumbing in core/meta_service.py.

Scope (Google agent's Phase 1+2 combined): performance reporting for the support
chat, pause/resume, link-campaign creation (always created PAUSED — a human
activates), daily health scan (token refresh, account status, auto-pause of
policy-flagged campaigns, performance alerts), and the weekly report email.

Meta = one bundled platform group in our pricing (FB+IG together) — insights
from the ad account already cover both placements, so one report covers both.

The only LLM call here is the weekly report's summary — everything else talks
to the Graph API, not to Claude.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

from supabase import create_client as _supabase_client

from core import meta_service as meta
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call

AGENT_NAME = "meta_ads_agent"
PLATFORM = "meta_ads"
PAGE_PLATFORM = "meta_page"  # ad creatives must be published "as" a Page

# client_id -> (fetched_at, result). Support chat may ask about campaigns
# several times per conversation — don't burn API calls on every message.
PERF_CACHE_TTL_SECONDS = 300
_perf_cache = {}

# Meta reports every action type separately ("actions" list) — these are the
# ones that count as a conversion for our SMB clients, summed into one number
# comparable to Google's metrics.conversions.
CONVERSION_ACTION_TYPES = {
    "lead",
    "purchase",
    "complete_registration",
    "submit_application",
    "schedule",
    "contact",
    "onsite_conversion.lead_grouped",
    "onsite_conversion.purchase",
    "onsite_conversion.messaging_conversation_started_7d",
}

# Meta's numeric account_status values, mapped for readable alerts
ACCOUNT_STATUS_LABELS = {
    1: "ACTIVE", 2: "DISABLED", 3: "UNSETTLED", 7: "PENDING_RISK_REVIEW",
    8: "PENDING_SETTLEMENT", 9: "IN_GRACE_PERIOD", 100: "PENDING_CLOSURE",
    101: "CLOSED",
}

# Created lazily — no DB client at import time (api_server imports every agent at startup)
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        _db_instance = _supabase_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _db_instance


def _get_connection(client_id: int, platform: str = PLATFORM) -> dict:
    """The client's active row from client_accounts. For meta_ads:
    account_id = 'act_...' ad account id, access_token = long-lived USER token."""
    result = (
        _db().table("client_accounts")
        .select("*")
        .eq("client_id", client_id)
        .eq("platform", platform)
        .eq("status", "active")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def is_connected(client_id: int) -> bool:
    conn = _get_connection(client_id)
    return bool(conn.get("access_token") and conn.get("account_id"))


def _conversions_from_actions(actions: list) -> float:
    return sum(float(a.get("value", 0)) for a in (actions or [])
               if a.get("action_type") in CONVERSION_ACTION_TYPES)


def get_campaign_performance(client_id: int, force_refresh: bool = False) -> dict:
    """Last-30-days campaign metrics (Facebook + Instagram combined — Meta is
    one platform group in our pricing). Always returns a well-formed dict —
    never raises into a client-facing flow."""
    cached = _perf_cache.get(client_id)
    if cached and not force_refresh and cached[0] > time.time() - PERF_CACHE_TTL_SECONDS:
        return cached[1]

    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"connected": False}

    log_step(AGENT_NAME, "get_campaign_performance",
             f"client_id={client_id} ad_account={conn['account_id']}")
    try:
        # Insights only return campaigns that have data — merge with the full
        # campaign list so paused/new campaigns still show up with zeros
        campaign_rows = timed_step(
            AGENT_NAME, "campaigns_fetch",
            lambda: meta.get_campaigns(conn["access_token"], conn["account_id"]),
        )
        insight_rows = timed_step(
            AGENT_NAME, "insights_fetch",
            lambda: meta.get_campaign_insights(conn["access_token"], conn["account_id"],
                                               date_preset="last_30d"),
        )
    except Exception as e:
        agent_alert(AGENT_NAME, [f"performance fetch failed for client {client_id}: {e}"])
        return {"connected": True, "error": str(e), "campaigns": []}

    insights = {row.get("campaign_id", ""): row for row in insight_rows}
    campaigns = []
    for row in campaign_rows:
        cid = str(row.get("id", ""))
        ins = insights.get(cid, {})
        campaigns.append({
            "id": cid,
            "name": row.get("name", ""),
            "status": row.get("status", ""),
            "impressions": int(ins.get("impressions", 0)),
            "clicks": int(ins.get("clicks", 0)),
            # spend is in the ad account's own currency (ILS for our clients)
            "cost": round(float(ins.get("spend", 0)), 2),
            "conversions": round(_conversions_from_actions(ins.get("actions")), 1),
        })

    result = {
        "connected": True,
        "period": "last_30_days",
        "ad_account_id": conn["account_id"],
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
        return {"success": False, "error": "no connected meta_ads account"}

    log_step(AGENT_NAME, action, f"client_id={client_id} campaign_id={campaign_id}")
    try:
        timed_step(
            AGENT_NAME, "campaign_status_mutate",
            lambda: meta.set_campaign_status(conn["access_token"], campaign_id, status),
        )
    except Exception as e:
        agent_alert(AGENT_NAME, [f"{action} failed for client {client_id}, campaign {campaign_id}: {e}"])
        return {"success": False, "error": str(e)}

    _perf_cache.pop(client_id, None)  # status just changed — don't serve stale data
    log_activity(client_id, AGENT_NAME, action, {"campaign_id": campaign_id}, {"status": status})
    return {"success": True, "campaign_id": campaign_id, "status": status}


def pause_campaign(client_id: int, campaign_id: str) -> dict:
    return _set_campaign_status(client_id, campaign_id, "PAUSED", "campaign_paused")


def resume_campaign(client_id: int, campaign_id: str) -> dict:
    return _set_campaign_status(client_id, campaign_id, "ACTIVE", "campaign_resumed")


# ─── Campaign creation ────────────────────────────────────────────────────────

# Same safety ceiling as the Google agent: SMB clients budget ~3,000 ILS/month
# (~100/day) — anything above this is almost certainly a monthly figure passed
# as daily and must be caught before it reaches Meta, not after it spends.
MAX_DAILY_BUDGET_ILS = 500

# Meta truncates (doesn't reject) over-long ad text — enforce sane limits here
# so nothing ships half-cut. These follow Meta's display recommendations.
PRIMARY_TEXT_MAX_CHARS = 500
HEADLINE_MAX_CHARS = 60
DESCRIPTION_MAX_CHARS = 60

DEFAULT_COUNTRIES = ["IL"]


def _validate_campaign_spec(spec: dict) -> list:
    errors = []
    if not (spec.get("name") or "").strip():
        errors.append("name is required")

    budget = spec.get("daily_budget_ils", 0)
    if not isinstance(budget, (int, float)) or budget <= 0:
        errors.append("daily_budget_ils must be a positive number")
    elif budget > MAX_DAILY_BUDGET_ILS:
        errors.append(f"daily_budget_ils {budget} exceeds the {MAX_DAILY_BUDGET_ILS} ILS/day safety cap "
                      "(is this a monthly figure passed as daily?)")

    if not (spec.get("final_url") or "").startswith(("http://", "https://")):
        errors.append("final_url must start with http:// or https://")

    primary_text = (spec.get("primary_text") or "").strip()
    if not primary_text or len(primary_text) > PRIMARY_TEXT_MAX_CHARS:
        errors.append(f"primary_text is required and must be under {PRIMARY_TEXT_MAX_CHARS} chars")

    headline = (spec.get("headline") or "").strip()
    if not headline or len(headline) > HEADLINE_MAX_CHARS:
        errors.append(f"headline is required and must be under {HEADLINE_MAX_CHARS} chars")

    if len(spec.get("description") or "") > DESCRIPTION_MAX_CHARS:
        errors.append(f"description must be under {DESCRIPTION_MAX_CHARS} chars")

    image_url = spec.get("image_url") or ""
    if image_url and not image_url.startswith(("http://", "https://")):
        errors.append("image_url must be a public http(s) URL")

    return errors


def create_link_campaign(client_id: int, spec: dict) -> dict:
    """Create a complete Traffic campaign (campaign + ad set targeting Israel +
    link-ad creative published as the client's Page + one ad). ALWAYS created
    PAUSED — a human reviews it in Ads Manager and activates (or calls
    resume_campaign) when satisfied.

    Unlike Google there is no atomic multi-resource mutate — resources are
    created sequentially, and on any failure the campaign is deleted (Meta
    cascades the delete to its ad sets and ads) so nothing half-built survives.

    spec: {name, daily_budget_ils, final_url, primary_text, headline,
    description?, image_url?, countries?}
    """
    from agents.client_agent import log_activity

    errors = _validate_campaign_spec(spec)
    if errors:
        return {"success": False, "errors": errors}

    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"success": False, "errors": ["no connected meta_ads account"]}
    page_conn = _get_connection(client_id, PAGE_PLATFORM)
    if not page_conn.get("account_id"):
        return {"success": False, "errors": ["no connected Facebook Page — "
                                             "Meta link ads must be published as a Page"]}

    token, act = conn["access_token"], conn["account_id"]
    name = spec["name"].strip()

    log_step(AGENT_NAME, "create_link_campaign",
             f"client_id={client_id} name='{name}' budget={spec['daily_budget_ils']} ILS/day")

    campaign_id = creative_id = None
    try:
        campaign_id = timed_step(
            AGENT_NAME, "create_campaign",
            lambda: meta.graph_post(f"{act}/campaigns", token, data={
                "name": name,
                "objective": "OUTCOME_TRAFFIC",
                "status": "PAUSED",
                # Mandatory declaration — our SMB campaigns never fall under
                # housing/employment/credit/politics special categories
                "special_ad_categories": [],
            }, marketing=True),
        )["id"]

        adset_id = timed_step(
            AGENT_NAME, "create_adset",
            lambda: meta.graph_post(f"{act}/adsets", token, data={
                "name": f"{name} - ad set 1",
                "campaign_id": campaign_id,
                # daily_budget is in the account currency's MINOR units (agorot)
                "daily_budget": int(spec["daily_budget_ils"] * 100),
                "billing_event": "IMPRESSIONS",
                # Link clicks need no conversion tracking — right default for a
                # fresh SMB account; optimization upgrades are a human decision
                "optimization_goal": "LINK_CLICKS",
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                # Placements stay automatic (Advantage+): Meta serves across FB
                # AND Instagram from this one ad set — our bundled platform group
                "targeting": {"geo_locations": {"countries": spec.get("countries") or DEFAULT_COUNTRIES}},
                "status": "PAUSED",
            }, marketing=True),
        )["id"]

        link_data = {
            "link": spec["final_url"],
            "message": spec["primary_text"].strip(),
            "name": spec["headline"].strip(),
        }
        if spec.get("description"):
            link_data["description"] = spec["description"].strip()
        if spec.get("image_url"):
            link_data["picture"] = spec["image_url"]

        creative_id = timed_step(
            AGENT_NAME, "create_creative",
            lambda: meta.graph_post(f"{act}/adcreatives", token, data={
                "name": f"{name} - creative 1",
                "object_story_spec": {"page_id": page_conn["account_id"], "link_data": link_data},
            }, marketing=True),
        )["id"]

        timed_step(
            AGENT_NAME, "create_ad",
            lambda: meta.graph_post(f"{act}/ads", token, data={
                "name": f"{name} - ad 1",
                "adset_id": adset_id,
                "creative": {"creative_id": creative_id},
                "status": "PAUSED",
            }, marketing=True),
        )
    except Exception as e:
        # No atomic mutate on Meta — clean up so a mid-sequence failure doesn't
        # strand a half-built campaign (campaign delete cascades to ad sets/ads)
        for leftover_id, label in ((campaign_id, "campaign"), (creative_id, "creative")):
            if leftover_id:
                try:
                    meta.graph_delete(f"{leftover_id}", token, marketing=True)
                except Exception as cleanup_err:
                    print(f"[{AGENT_NAME}] cleanup of {label} {leftover_id} failed: {cleanup_err}")
        agent_alert(AGENT_NAME, [f"campaign creation failed for client {client_id} ('{name}'): {e}"])
        return {"success": False, "errors": [str(e)]}

    _perf_cache.pop(client_id, None)
    log_activity(client_id, AGENT_NAME, "campaign_created",
                 {"name": name, "daily_budget_ils": spec["daily_budget_ils"]},
                 {"campaign_id": campaign_id, "status": "PAUSED"})
    return {"success": True, "campaign_id": campaign_id, "status": "PAUSED",
            "note": "campaign created PAUSED - review it in Ads Manager, then activate"}


# ─── Daily health scan: tokens + technical issues + performance alerts ───────

# Same live-alert thresholds as the Google agent — deliberately coarse for v1,
# the goal is catching "money is burning" situations, not analytics.
PERF_ZERO_CONV_MIN_COST_ILS = 150   # spent this much in 7 days with 0 conversions
PERF_CPL_SPIKE_FACTOR = 2.0         # cost-per-conversion doubled week-over-week
PERF_CPL_MIN_COST_ILS = 100         # ...but only if spend is material
PERF_CTR_DROP_FACTOR = 0.5          # CTR halved week-over-week
PERF_CTR_MIN_IMPRESSIONS = 1000     # ...with enough volume to mean something

ISSUE_DEDUP_DAYS = 3

# The long-lived user token lives ~60 days. Re-exchange it well before expiry
# so a connected client never silently goes dark and has to reconnect.
TOKEN_REFRESH_WINDOW_DAYS = 10


def _all_connections() -> list:
    """Every active meta_ads connection across all clients."""
    result = (
        _db().table("client_accounts")
        .select("*")
        .eq("platform", PLATFORM)
        .eq("status", "active")
        .execute()
    )
    return [c for c in (result.data or []) if c.get("access_token") and c.get("account_id")]


def _already_alerted(client_id: int, issue_key: str) -> bool:
    """Durable dedup via client_activity (survives redeploys, unlike memory):
    was this exact issue alerted within the last ISSUE_DEDUP_DAYS days?"""
    from agents.client_agent import get_activity
    cutoff = datetime.now(timezone.utc) - timedelta(days=ISSUE_DEDUP_DAYS)
    for entry in get_activity(client_id, limit=50):
        if entry.get("action_type") != "ads_issue_detected":
            continue
        if (entry.get("details") or {}).get("issue_key") != issue_key:
            continue
        try:
            created = datetime.fromisoformat(entry["created_at"].replace("Z", "+00:00"))
            if created >= cutoff:
                return True
        except Exception:
            pass
    return False


def _raise_issue(client_id: int, issue_key: str, message: str, auto_paused: bool = False):
    """Alert the team about one detected issue, with dedup. Issue keys are
    'meta_'-prefixed so they can never collide with Google agent keys in the
    shared ads_issue_detected activity rows."""
    from agents.client_agent import log_activity
    if _already_alerted(client_id, issue_key):
        return False
    agent_alert(AGENT_NAME, [f"client {client_id}: {message}"])
    log_activity(client_id, AGENT_NAME, "ads_issue_detected",
                 {"issue_key": issue_key, "auto_paused": auto_paused}, {"message": message})
    return True


def _refresh_token_if_needed(conn: dict) -> dict:
    """Keep the ~60-day user token alive: introspect it, and if it expires
    within TOKEN_REFRESH_WINDOW_DAYS re-exchange it for a fresh one (Meta
    returns a new 60-day token for a still-valid long-lived token). Returns the
    (possibly updated) connection. Raises if the token is already dead."""
    from agents.client_agent import upsert_account

    info = meta.debug_token(conn["access_token"])
    if not info.get("is_valid", False):
        raise meta.MetaGraphError("stored Meta token is no longer valid", code=190)

    expires_at = info.get("expires_at", 0)  # 0 = never expires
    if not expires_at:
        return conn
    days_left = (expires_at - time.time()) / 86400
    if days_left > TOKEN_REFRESH_WINDOW_DAYS:
        return conn

    log_step(AGENT_NAME, "token_refresh",
             f"client_id={conn['client_id']} token expires in {days_left:.1f} days - refreshing")
    fresh = meta.exchange_long_lived(conn["access_token"])["access_token"]
    upsert_account(conn["client_id"], PLATFORM, conn["account_id"], fresh, "active")
    return {**conn, "access_token": fresh}


def _fetch_two_window_metrics(conn: dict) -> list:
    """Per-campaign metrics for the last 7 full days and the 7 days before
    them, in one insights call (daily breakdown, bucketed here), joined with
    the campaign list for status (insights don't carry it)."""
    today = datetime.now(timezone.utc).date()
    last_start, last_end = today - timedelta(days=7), today - timedelta(days=1)
    prev_start = today - timedelta(days=14)  # prev window runs up to last_start - 1

    statuses = {str(c.get("id", "")): (c.get("name", ""), c.get("status", ""))
                for c in meta.get_campaigns(conn["access_token"], conn["account_id"])}
    rows = meta.get_campaign_insights(
        conn["access_token"], conn["account_id"],
        time_range={"since": str(prev_start), "until": str(last_end)},
        time_increment=1,
    )

    campaigns = {}
    for row in rows:
        cid = str(row.get("campaign_id", ""))
        name, status = statuses.get(cid, (row.get("campaign_name", ""), ""))
        entry = campaigns.setdefault(cid, {
            "id": cid, "name": name, "status": status,
            "last7": {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0},
            "prev7": {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0},
        })
        window = "last7" if row.get("date_start", "") >= str(last_start) else "prev7"
        entry[window]["impressions"] += int(row.get("impressions", 0))
        entry[window]["clicks"] += int(row.get("clicks", 0))
        entry[window]["cost"] += float(row.get("spend", 0))
        entry[window]["conversions"] += _conversions_from_actions(row.get("actions"))

    for entry in campaigns.values():
        for window in ("last7", "prev7"):
            entry[window]["cost"] = round(entry[window]["cost"], 2)
            entry[window]["conversions"] = round(entry[window]["conversions"], 1)
    return list(campaigns.values())


def _check_performance_problems(client_id: int, campaigns: list) -> list:
    """Performance problems worth an immediate alert (never an auto-pause —
    'working but expensive' is a human decision). Returns issue messages raised."""
    raised = []
    for c in campaigns:
        if c["status"] != "ACTIVE":
            continue
        last, prev = c["last7"], c["prev7"]
        label = f"campaign '{c['name']}' ({c['id']})"

        if last["cost"] >= PERF_ZERO_CONV_MIN_COST_ILS and last["conversions"] == 0:
            msg = (f"{label} spent {last['cost']} ILS in the last 7 days with ZERO conversions "
                   f"(previous week: {prev['conversions']} conversions)")
            if _raise_issue(client_id, f"meta_perf_zeroconv_{c['id']}", msg):
                raised.append(msg)

        if (last["conversions"] > 0 and prev["conversions"] > 0
                and last["cost"] >= PERF_CPL_MIN_COST_ILS):
            cpl_last = last["cost"] / last["conversions"]
            cpl_prev = prev["cost"] / prev["conversions"]
            if cpl_prev > 0 and cpl_last > cpl_prev * PERF_CPL_SPIKE_FACTOR:
                msg = (f"{label} cost-per-conversion spiked: {round(cpl_last, 1)} ILS vs "
                       f"{round(cpl_prev, 1)} ILS last week")
                if _raise_issue(client_id, f"meta_perf_cpl_{c['id']}", msg):
                    raised.append(msg)

        if (last["impressions"] >= PERF_CTR_MIN_IMPRESSIONS
                and prev["impressions"] >= PERF_CTR_MIN_IMPRESSIONS):
            ctr_last = last["clicks"] / last["impressions"]
            ctr_prev = prev["clicks"] / prev["impressions"]
            if ctr_prev > 0 and ctr_last < ctr_prev * PERF_CTR_DROP_FACTOR:
                msg = (f"{label} CTR cratered: {round(ctr_last * 100, 2)}% vs "
                       f"{round(ctr_prev * 100, 2)}% last week")
                if _raise_issue(client_id, f"meta_perf_ctr_{c['id']}", msg):
                    raised.append(msg)
    return raised


def run_health_scan() -> dict:
    """Daily scan over every connected Meta ad account: refresh aging tokens,
    pause policy-flagged campaigns, alert on account/performance problems.
    Designed for a Cloud Scheduler hit on /api/meta-ads/scan."""
    connections = _all_connections()
    log_step(AGENT_NAME, "run_health_scan", f"{len(connections)} connected accounts")
    summary = {"clients_scanned": 0, "issues": [], "campaigns_paused": []}

    for conn in connections:
        client_id = conn["client_id"]
        try:
            # 0. Token upkeep — everything else is pointless with a dead token
            try:
                conn = _refresh_token_if_needed(conn)
            except Exception as e:
                msg = (f"Meta token for account {conn['account_id']} is expired or could not be "
                       f"refreshed ({e}) - the client must reconnect Meta in the dashboard")
                if _raise_issue(client_id, "meta_token_dead", msg):
                    summary["issues"].append(msg)
                continue

            # 1. Account-level status (disabled/unsettled = nothing serves)
            overview = meta.get_account_overview(conn["access_token"], conn["account_id"])
            status_code = overview.get("account_status", 1)
            if status_code != 1:
                status_label = ACCOUNT_STATUS_LABELS.get(status_code, f"code {status_code}")
                msg = (f"Meta ad account {conn['account_id']} status is {status_label} "
                       f"(disable_reason={overview.get('disable_reason', 0)}) - nothing can serve")
                if _raise_issue(client_id, f"meta_account_status_{status_code}", msg):
                    summary["issues"].append(msg)

            # 2. Disapproved/flagged ads in active campaigns -> pause the campaign.
            # A campaign with policy-flagged ads must not keep spending.
            broken_campaigns = {}
            for ad in meta.get_flagged_ads(conn["access_token"], conn["account_id"]):
                campaign = ad.get("campaign") or {}
                cid = str(campaign.get("id", ""))
                if not cid:
                    continue
                broken = broken_campaigns.setdefault(
                    cid, {"name": campaign.get("name", ""), "ads": 0, "statuses": set()})
                broken["ads"] += 1
                broken["statuses"].add(ad.get("effective_status", ""))
            for cid, broken in broken_campaigns.items():
                pause_result = pause_campaign(client_id, cid)
                paused = pause_result.get("success", False)
                msg = (f"campaign '{broken['name']}' ({cid}) has {broken['ads']} flagged ad(s) "
                       f"[{', '.join(sorted(broken['statuses']))}] - "
                       f"{'auto-PAUSED' if paused else 'PAUSE FAILED, check manually'}")
                _raise_issue(client_id, f"meta_disapproved_{cid}", msg, auto_paused=paused)
                summary["issues"].append(msg)
                if paused:
                    summary["campaigns_paused"].append(cid)

            # 3. Performance problems (alert only)
            campaigns = _fetch_two_window_metrics(conn)
            summary["issues"].extend(_check_performance_problems(client_id, campaigns))

            summary["clients_scanned"] += 1
        except Exception as e:
            agent_alert(AGENT_NAME, [f"health scan failed for client {client_id}: {e}"])
            summary["issues"].append(f"client {client_id}: scan failed - {e}")

    log_step(AGENT_NAME, "run_health_scan",
             f"done - {summary['clients_scanned']} scanned, {len(summary['issues'])} issues, "
             f"{len(summary['campaigns_paused'])} campaigns paused")
    return summary


# ─── Weekly report ────────────────────────────────────────────────────────────

WEEKLY_REPORT_SYSTEM = """You are the performance analyst for uallak, an Israeli marketing agency.
You receive week-over-week Meta (Facebook + Instagram) metrics for all managed client
accounts (costs in ILS), possibly with organic engagement numbers per client.

Write for the human team (not clients): what changed, what needs attention, what to try next.
Base every statement strictly on the numbers given - never invent data.

Hard limits: max 5 highlights, max 5 recommendations, each a single short Hebrew sentence.

Return JSON only:
{"highlights": ["Hebrew sentence"], "recommendations": ["Hebrew sentence"]}"""


def _pct_change(new: float, old: float):
    return round((new - old) / old * 100, 1) if old else None


def run_weekly_report(send_email: bool = True) -> dict:
    """Week-over-week Meta performance summary across all connected accounts —
    paid campaigns plus organic engagement (from meta_content_agent) for clients
    with a connected Page. Emailed to the team; designed for a weekly Cloud
    Scheduler hit on /api/meta-ads/weekly-report."""
    from agents.client_agent import get_client
    from agents.meta_content_agent import get_engagement_summary, page_connected_client_ids

    connections = _all_connections()
    ads_client_ids = {c["client_id"] for c in connections}
    # Clients with only a Page connected (no ad account yet) still get their
    # organic engagement into the report — Meta is one bundled platform group
    page_only_ids = [cid for cid in page_connected_client_ids() if cid not in ads_client_ids]
    log_step(AGENT_NAME, "run_weekly_report",
             f"{len(connections)} ad accounts, {len(page_only_ids)} page-only clients")

    clients_data = []
    for conn in connections:
        client_id = conn["client_id"]
        try:
            campaigns = _fetch_two_window_metrics(conn)
        except Exception as e:
            agent_alert(AGENT_NAME, [f"weekly report fetch failed for client {client_id}: {e}"])
            continue

        totals = {}
        for window in ("last7", "prev7"):
            totals[window] = {
                "impressions": sum(c[window]["impressions"] for c in campaigns),
                "clicks": sum(c[window]["clicks"] for c in campaigns),
                "cost": round(sum(c[window]["cost"] for c in campaigns), 2),
                "conversions": round(sum(c[window]["conversions"] for c in campaigns), 1),
            }
        client = get_client(client_id)
        clients_data.append({
            "client_id": client_id,
            "client_name": client.get("name", f"#{client_id}"),
            "customer_id": conn["account_id"],
            "totals_last7": totals["last7"],
            "totals_prev7": totals["prev7"],
            "cost_change_pct": _pct_change(totals["last7"]["cost"], totals["prev7"]["cost"]),
            "campaigns": campaigns,
        })

    for client_id in page_only_ids:
        client = get_client(client_id)
        clients_data.append({
            "client_id": client_id,
            "client_name": client.get("name", f"#{client_id}"),
            "customer_id": "(page only)",
            "totals_last7": {"impressions": 0, "clicks": 0, "cost": 0, "conversions": 0},
            "totals_prev7": {"impressions": 0, "clicks": 0, "cost": 0, "conversions": 0},
            "cost_change_pct": None,
            "campaigns": [],
        })

    # Organic engagement garnish per client — non-fatal, the ads tables stand alone
    for entry in clients_data:
        try:
            engagement = get_engagement_summary(entry["client_id"])
            if engagement.get("connected"):
                entry["engagement"] = {k: v for k, v in engagement.items()
                                       if k in ("facebook", "instagram")}
        except Exception as e:
            print(f"[{AGENT_NAME}] engagement fetch failed for client {entry['client_id']} (non-fatal): {e}")

    report = {
        "platform": "meta",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "clients": clients_data,
        "highlights": [],
        "recommendations": [],
    }

    if clients_data:
        try:
            summary = timed_step(
                AGENT_NAME, "weekly_report_llm",
                lambda: safe_claude_json_call(
                    WEEKLY_REPORT_SYSTEM,
                    json.dumps(clients_data, ensure_ascii=False),
                    max_tokens=1000,
                    cost_category="claude_analysis",
                ),
            )
            report["highlights"] = summary.get("highlights", [])[:5]
            report["recommendations"] = summary.get("recommendations", [])[:5]
        except ClaudeJSONError as e:
            # Tables still go out - the LLM garnish is optional
            agent_alert(AGENT_NAME, [f"weekly report LLM summary failed: {e}"])

    # Durable copy for the admin dashboard's reports tab - non-fatal on failure
    try:
        _db().table("weekly_reports").insert({"report": report}).execute()
    except Exception as e:
        print(f"[{AGENT_NAME}] could not persist weekly report (non-fatal): {e}")

    if send_email:
        from core.email_service import send_meta_weekly_report
        send_meta_weekly_report(report)

    log_step(AGENT_NAME, "run_weekly_report", f"done - {len(clients_data)} clients in report")
    return report
