"""uallak's first execution agent: reads and controls a client's real Google
Ads campaigns through the account they connected via OAuth in the dashboard.

Phase 1: performance reporting + pause/resume (feeds the support chat).
Phase 2: search-campaign creation (always created PAUSED - a human activates),
daily health scan (auto-pauses technically broken campaigns, live-alerts on
performance problems), and the weekly report email to the team.

The only LLM call here is the weekly report's summary - everything else talks
to the Google Ads API, not to Claude.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

from supabase import create_client as _supabase_client

from core import google_ads_service as gads
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call

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


_YESTERDAY_CONVERSIONS_GAQL = """
    SELECT metrics.conversions
    FROM campaign
    WHERE segments.date DURING YESTERDAY
"""


def get_conversions_yesterday(client_id: int):
    """Total conversions across all campaigns for yesterday, for the daily
    sales-alert email (engagement_agent). None = not connected or fetch
    failed (caller skips silently — a missing celebration email is not an
    incident worth alerting on)."""
    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return None
    try:
        rows = gads.search(conn["access_token"], conn["account_id"], _YESTERDAY_CONVERSIONS_GAQL)
        return round(sum(float(r.get("metrics", {}).get("conversions", 0)) for r in rows), 1)
    except Exception as e:
        log_step(AGENT_NAME, "get_conversions_yesterday", f"client {client_id}: failed ({e})")
        return None


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


# ─── Campaign creation (Phase 2) ──────────────────────────────────────────────

# Hard ceiling on a new campaign's daily budget. SMB clients budget ~3,000
# ILS/month (~100/day); anything above this is almost certainly a typo or a
# unit mistake (monthly figure passed as daily) and must be caught before it
# reaches Google, not after it spends.
MAX_DAILY_BUDGET_ILS = 500

DEFAULT_LOCATIONS = ["2376"]           # geoTargetConstant: Israel
DEFAULT_LANGUAGES = ["1027", "1000"]   # languageConstants: Hebrew, English

# Google's responsive-search-ad limits - validated here so a bad spec fails
# with a readable message instead of a nested API error
RSA_HEADLINE_MAX_CHARS = 30
RSA_DESCRIPTION_MAX_CHARS = 90
KEYWORD_MAX_CHARS = 80


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

    final_url = spec.get("final_url", "")
    if not final_url.startswith(("http://", "https://")):
        errors.append("final_url must start with http:// or https://")

    keywords = spec.get("keywords", [])
    if not 1 <= len(keywords) <= 50:
        errors.append("keywords must have 1-50 entries")
    for kw in keywords:
        text = kw.get("text", "") if isinstance(kw, dict) else kw
        if not text.strip() or len(text) > KEYWORD_MAX_CHARS:
            errors.append(f"keyword '{text[:40]}' is empty or over {KEYWORD_MAX_CHARS} chars")

    headlines = spec.get("headlines", [])
    if not 3 <= len(headlines) <= 15:
        errors.append("headlines must have 3-15 entries")
    for h in headlines:
        if not h.strip() or len(h) > RSA_HEADLINE_MAX_CHARS:
            errors.append(f"headline '{h[:40]}' is empty or over {RSA_HEADLINE_MAX_CHARS} chars")

    descriptions = spec.get("descriptions", [])
    if not 2 <= len(descriptions) <= 4:
        errors.append("descriptions must have 2-4 entries")
    for d in descriptions:
        if not d.strip() or len(d) > RSA_DESCRIPTION_MAX_CHARS:
            errors.append(f"description '{d[:40]}' is empty or over {RSA_DESCRIPTION_MAX_CHARS} chars")

    return errors


def create_search_campaign(client_id: int, spec: dict) -> dict:
    """Create a complete Search campaign (budget + campaign + geo/language
    targeting + ad group + keywords + one responsive search ad) in a single
    atomic mutate. ALWAYS created PAUSED - a human reviews it in the Ads UI
    and activates (or calls resume_campaign) when satisfied.

    spec: {name, daily_budget_ils, final_url, keywords (str or
    {text, match_type}), headlines, descriptions, locations?, languages?}
    """
    from agents.client_agent import log_activity

    errors = _validate_campaign_spec(spec)
    if errors:
        return {"success": False, "errors": errors}

    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"success": False, "errors": ["no connected google_ads account"]}

    cid = conn["account_id"]
    name = spec["name"].strip()
    budget_temp = f"customers/{cid}/campaignBudgets/-1"
    campaign_temp = f"customers/{cid}/campaigns/-2"
    ad_group_temp = f"customers/{cid}/adGroups/-3"

    operations = [
        {"campaignBudgetOperation": {"create": {
            "resourceName": budget_temp,
            "name": f"{name} - budget",
            "amountMicros": str(int(spec["daily_budget_ils"] * 1_000_000)),
            "deliveryMethod": "STANDARD",
            "explicitlyShared": False,
        }}},
        {"campaignOperation": {"create": {
            "resourceName": campaign_temp,
            "name": name,
            "status": "PAUSED",
            "advertisingChannelType": "SEARCH",
            "campaignBudget": budget_temp,
            # Maximize clicks - needs no conversion tracking, right default for
            # a fresh SMB account; bidding upgrades are a human decision later
            "targetSpend": {},
            "networkSettings": {
                "targetGoogleSearch": True,
                "targetSearchNetwork": False,
                "targetContentNetwork": False,
                "targetPartnerSearchNetwork": False,
            },
            # Mandatory declaration since v20 - our SMB campaigns never carry
            # EU political advertising
            "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        }}},
    ]

    for geo_id in spec.get("locations") or DEFAULT_LOCATIONS:
        operations.append({"campaignCriterionOperation": {"create": {
            "campaign": campaign_temp,
            "location": {"geoTargetConstant": f"geoTargetConstants/{geo_id}"},
        }}})
    for lang_id in spec.get("languages") or DEFAULT_LANGUAGES:
        operations.append({"campaignCriterionOperation": {"create": {
            "campaign": campaign_temp,
            "language": {"languageConstant": f"languageConstants/{lang_id}"},
        }}})

    operations.append({"adGroupOperation": {"create": {
        "resourceName": ad_group_temp,
        "name": f"{name} - ad group 1",
        "campaign": campaign_temp,
        "type": "SEARCH_STANDARD",
        "status": "ENABLED",
    }}})

    for kw in spec["keywords"]:
        text, match_type = (kw.get("text", ""), kw.get("match_type", "PHRASE")) if isinstance(kw, dict) else (kw, "PHRASE")
        operations.append({"adGroupCriterionOperation": {"create": {
            "adGroup": ad_group_temp,
            "status": "ENABLED",
            "keyword": {"text": text.strip(), "matchType": match_type},
        }}})

    operations.append({"adGroupAdOperation": {"create": {
        "adGroup": ad_group_temp,
        "status": "ENABLED",
        "ad": {
            "finalUrls": [spec["final_url"]],
            "responsiveSearchAd": {
                "headlines": [{"text": h.strip()} for h in spec["headlines"]],
                "descriptions": [{"text": d.strip()} for d in spec["descriptions"]],
            },
        },
    }}})

    log_step(AGENT_NAME, "create_search_campaign",
             f"client_id={client_id} name='{name}' budget={spec['daily_budget_ils']} ILS/day "
             f"({len(spec['keywords'])} keywords)")
    try:
        response = timed_step(
            AGENT_NAME, "atomic_mutate",
            lambda: gads.atomic_mutate(conn["access_token"], cid, operations),
        )
    except Exception as e:
        agent_alert(AGENT_NAME, [f"campaign creation failed for client {client_id} ('{name}'): {e}"])
        return {"success": False, "errors": [str(e)]}

    campaign_resource = ""
    for op_response in response.get("mutateOperationResponses", []):
        result = op_response.get("campaignResult")
        if result:
            campaign_resource = result.get("resourceName", "")
    campaign_id = campaign_resource.split("/")[-1] if campaign_resource else ""

    _perf_cache.pop(client_id, None)
    log_activity(client_id, AGENT_NAME, "campaign_created",
                 {"name": name, "daily_budget_ils": spec["daily_budget_ils"],
                  "keywords": len(spec["keywords"])},
                 {"campaign_id": campaign_id, "status": "PAUSED"})
    return {"success": True, "campaign_id": campaign_id, "status": "PAUSED",
            "note": "campaign created PAUSED - review it in the Ads UI, then activate"}


# ─── Daily health scan: technical issues + performance alerts (Phase 2) ──────

# Live-alert thresholds. Deliberately coarse for v1 - the goal is catching
# "money is burning" situations, not analytics. Weekly report covers nuance.
PERF_ZERO_CONV_MIN_COST_ILS = 150   # spent this much in 7 days with 0 conversions
PERF_CPL_SPIKE_FACTOR = 2.0         # cost-per-conversion doubled week-over-week
PERF_CPL_MIN_COST_ILS = 100         # ...but only if spend is material
PERF_CTR_DROP_FACTOR = 0.5          # CTR halved week-over-week
PERF_CTR_MIN_IMPRESSIONS = 1000     # ...with enough volume to mean something

ISSUE_DEDUP_DAYS = 3

_ACCOUNT_STATUS_GAQL = "SELECT customer.id, customer.status FROM customer"

_DISAPPROVED_ADS_GAQL = """
    SELECT campaign.id, campaign.name, ad_group_ad.ad.id,
           ad_group_ad.policy_summary.approval_status,
           ad_group_ad.policy_summary.policy_topic_entries
    FROM ad_group_ad
    WHERE ad_group_ad.policy_summary.approval_status = 'DISAPPROVED'
      AND campaign.status = 'ENABLED'
      AND ad_group_ad.status = 'ENABLED'
"""

_CAMPAIGN_HEALTH_GAQL = """
    SELECT campaign.id, campaign.name, campaign.primary_status, campaign.primary_status_reasons
    FROM campaign
    WHERE campaign.status = 'ENABLED'
"""


def _all_connections() -> list:
    """Every active google_ads connection across all clients."""
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
    """Alert the team about one detected issue, with dedup."""
    from agents.client_agent import log_activity
    if _already_alerted(client_id, issue_key):
        return False
    agent_alert(AGENT_NAME, [f"client {client_id}: {message}"])
    log_activity(client_id, AGENT_NAME, "ads_issue_detected",
                 {"issue_key": issue_key, "auto_paused": auto_paused}, {"message": message})
    return True


def _fetch_two_window_metrics(conn: dict) -> list:
    """Per-campaign metrics for the last 7 full days and the 7 days before
    them, in one GAQL call (segmented by date, bucketed here)."""
    today = datetime.now(timezone.utc).date()
    last_start, last_end = today - timedelta(days=7), today - timedelta(days=1)
    prev_start = today - timedelta(days=14)  # prev window runs up to last_start - 1

    gaql = f"""
        SELECT campaign.id, campaign.name, campaign.status, segments.date,
               metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
        FROM campaign
        WHERE segments.date BETWEEN '{prev_start}' AND '{last_end}'
    """
    rows = gads.search(conn["access_token"], conn["account_id"], gaql)

    campaigns = {}
    for row in rows:
        campaign, metrics = row.get("campaign", {}), row.get("metrics", {})
        cid = str(campaign.get("id", ""))
        entry = campaigns.setdefault(cid, {
            "id": cid, "name": campaign.get("name", ""), "status": campaign.get("status", ""),
            "last7": {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0},
            "prev7": {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0},
        })
        row_date = row.get("segments", {}).get("date", "")
        window = "last7" if row_date >= str(last_start) else "prev7"
        entry[window]["impressions"] += int(metrics.get("impressions", 0))
        entry[window]["clicks"] += int(metrics.get("clicks", 0))
        entry[window]["cost"] += int(metrics.get("costMicros", 0)) / 1_000_000
        entry[window]["conversions"] += float(metrics.get("conversions", 0))

    for entry in campaigns.values():
        for window in ("last7", "prev7"):
            entry[window]["cost"] = round(entry[window]["cost"], 2)
            entry[window]["conversions"] = round(entry[window]["conversions"], 1)
    return list(campaigns.values())


def _check_performance_problems(client_id: int, campaigns: list) -> list:
    """Performance problems worth an immediate alert (never an auto-pause -
    'working but expensive' is a human decision). Returns issue messages raised."""
    raised = []
    for c in campaigns:
        if c["status"] != "ENABLED":
            continue
        last, prev = c["last7"], c["prev7"]
        label = f"campaign '{c['name']}' ({c['id']})"

        if last["cost"] >= PERF_ZERO_CONV_MIN_COST_ILS and last["conversions"] == 0:
            msg = (f"{label} spent {last['cost']} ILS in the last 7 days with ZERO conversions "
                   f"(previous week: {prev['conversions']} conversions)")
            if _raise_issue(client_id, f"perf_zeroconv_{c['id']}", msg):
                raised.append(msg)

        if (last["conversions"] > 0 and prev["conversions"] > 0
                and last["cost"] >= PERF_CPL_MIN_COST_ILS):
            cpl_last = last["cost"] / last["conversions"]
            cpl_prev = prev["cost"] / prev["conversions"]
            if cpl_prev > 0 and cpl_last > cpl_prev * PERF_CPL_SPIKE_FACTOR:
                msg = (f"{label} cost-per-conversion spiked: {round(cpl_last, 1)} ILS vs "
                       f"{round(cpl_prev, 1)} ILS last week")
                if _raise_issue(client_id, f"perf_cpl_{c['id']}", msg):
                    raised.append(msg)

        if (last["impressions"] >= PERF_CTR_MIN_IMPRESSIONS
                and prev["impressions"] >= PERF_CTR_MIN_IMPRESSIONS):
            ctr_last = last["clicks"] / last["impressions"]
            ctr_prev = prev["clicks"] / prev["impressions"]
            if ctr_prev > 0 and ctr_last < ctr_prev * PERF_CTR_DROP_FACTOR:
                msg = (f"{label} CTR cratered: {round(ctr_last * 100, 2)}% vs "
                       f"{round(ctr_prev * 100, 2)}% last week")
                if _raise_issue(client_id, f"perf_ctr_{c['id']}", msg):
                    raised.append(msg)
    return raised


def run_health_scan() -> dict:
    """Daily scan over every connected account: pause technically broken
    campaigns (disapproved ads), alert on account/eligibility/performance
    problems. Designed for a Cloud Scheduler hit on /api/google-ads/scan."""
    connections = _all_connections()
    log_step(AGENT_NAME, "run_health_scan", f"{len(connections)} connected accounts")
    summary = {"clients_scanned": 0, "issues": [], "campaigns_paused": []}

    for conn in connections:
        client_id = conn["client_id"]
        try:
            # 1. Account-level status (suspended/canceled = nothing serves)
            rows = gads.search(conn["access_token"], conn["account_id"], _ACCOUNT_STATUS_GAQL)
            account_status = (rows[0].get("customer", {}).get("status", "UNKNOWN")) if rows else "UNKNOWN"
            if account_status != "ENABLED":
                msg = f"Google Ads account {conn['account_id']} status is {account_status} - nothing can serve"
                if _raise_issue(client_id, f"account_status_{account_status}", msg):
                    summary["issues"].append(msg)

            # 2. Disapproved ads in enabled campaigns -> pause the campaign.
            # A campaign whose ads are policy-flagged must not keep spending
            # (partially disapproved campaigns still serve remaining ads).
            rows = gads.search(conn["access_token"], conn["account_id"], _DISAPPROVED_ADS_GAQL)
            broken_campaigns = {}
            for row in rows:
                campaign = row.get("campaign", {})
                cid = str(campaign.get("id", ""))
                topics = [t.get("topic", "") for t in
                          row.get("adGroupAd", {}).get("policySummary", {}).get("policyTopicEntries", [])]
                broken = broken_campaigns.setdefault(cid, {"name": campaign.get("name", ""), "ads": 0, "topics": set()})
                broken["ads"] += 1
                broken["topics"].update(t for t in topics if t)
            for cid, broken in broken_campaigns.items():
                pause_result = pause_campaign(client_id, cid)
                paused = pause_result.get("success", False)
                msg = (f"campaign '{broken['name']}' ({cid}) has {broken['ads']} DISAPPROVED ad(s) "
                       f"[policy: {', '.join(sorted(broken['topics'])) or 'unspecified'}] - "
                       f"{'auto-PAUSED' if paused else 'PAUSE FAILED, check manually'}")
                newly_raised = _raise_issue(client_id, f"disapproved_{cid}", msg, auto_paused=paused)
                summary["issues"].append(msg)
                if paused:
                    summary["campaigns_paused"].append(cid)
                    # An auto-paused campaign is an urgent approve/fix decision
                    # for the CLIENT, not just a team alert -> WhatsApp SOS
                    # (gated on newly_raised so the 3-day dedup covers it too)
                    if newly_raised:
                        from agents.engagement_agent import notify_client_urgent
                        notify_client_urgent(
                            client_id,
                            f"⚠️ עדכון דחוף מ-uallak: הקמפיין '{broken['name']}' בגוגל הושהה "
                            "אוטומטית בגלל מודעות שנפסלו. הצוות כבר מטפל בזה - "
                            "פרטים בדשבורד, ואפשר לענות לנו שם בצ'אט.")

            # 3. Campaign eligibility problems (alert only - NOT_ELIGIBLE
            # campaigns don't spend, they need fixing rather than pausing)
            rows = gads.search(conn["access_token"], conn["account_id"], _CAMPAIGN_HEALTH_GAQL)
            for row in rows:
                campaign = row.get("campaign", {})
                primary_status = campaign.get("primaryStatus", "")
                if primary_status in ("NOT_ELIGIBLE", "MISCONFIGURED"):
                    cid = str(campaign.get("id", ""))
                    reasons = ", ".join(campaign.get("primaryStatusReasons", [])) or "no reasons given"
                    msg = f"campaign '{campaign.get('name', '')}' ({cid}) is {primary_status}: {reasons}"
                    if _raise_issue(client_id, f"primary_{cid}_{primary_status}", msg):
                        summary["issues"].append(msg)

            # 4. Performance problems (alert only)
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


# ─── Weekly report (Phase 2) ──────────────────────────────────────────────────

WEEKLY_REPORT_SYSTEM = """You are the performance analyst for uallak, an Israeli marketing agency.
You receive week-over-week Google Ads metrics for all managed client accounts (costs in ILS).

Write for the human team (not clients): what changed, what needs attention, what to try next.
Base every statement strictly on the numbers given - never invent data.

Hard limits: max 5 highlights, max 5 recommendations, each a single short Hebrew sentence.

Return JSON only:
{"highlights": ["Hebrew sentence"], "recommendations": ["Hebrew sentence"]}"""


def _pct_change(new: float, old: float):
    return round((new - old) / old * 100, 1) if old else None


def run_weekly_report(send_email: bool = True) -> dict:
    """Week-over-week performance summary across all connected accounts,
    emailed to the team. Designed for a weekly Cloud Scheduler hit on
    /api/google-ads/weekly-report."""
    from agents.client_agent import get_client

    connections = _all_connections()
    log_step(AGENT_NAME, "run_weekly_report", f"{len(connections)} connected accounts")

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

    report = {
        "platform": "google_ads",
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
        print(f"[google_ads_agent] could not persist weekly report (non-fatal): {e}")

    if send_email:
        from core.email_service import send_google_ads_weekly_report
        send_google_ads_weekly_report(report)

    log_step(AGENT_NAME, "run_weekly_report", f"done - {len(clients_data)} clients in report")
    return report
