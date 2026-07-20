"""uallak's financial aggregator/analyst — sits ON TOP of every other agent,
never duplicates their data collection. For one client (or every active
client, on the weekly scan) it pulls together:

- OUR OWN internal cost + margin — reused straight from admin_service
  (client_costs table), never recomputed here.
- Real ad spend — reused from google_ads_agent / meta_ads_agent's own
  get_campaign_performance (live Google/Meta API data).
- What's actually knowable about CLIENT-PAID external tools (Higgsfield,
  HeyGen, ElevenLabs, SEO tools, WordPress/InstaWP hosting) — pulled via each
  agent's own usage functions, never a second data-collection path.
- The client's own stated forecast from onboarding (marketing_budget,
  market_reality) — matched from the `leads` table.

CONFIDENCE LABELS — every figure below carries one. This is the whole point
of the agent (same rigor already established for market_reality, applied to
real historical data instead of pre-sale speculation): never present an
estimate as a hard fact, and never silently drop a genuine gap.

  HARD     = a real number from our own DB or a live vendor API call.
  ESTIMATE = arithmetic from a REAL usage figure times a published rate/price
             (the usage is real; the rate is a public reference, not this
             client's verified invoice).
  UNKNOWN  = no visibility today, and no honest way to estimate one. Say so.

No LLM call anywhere in the aggregation above — every number is either a
straight read or a documented, labeled calculation. generate_narrative() is a
separate, on-demand, OPTIONAL call that explains numbers already computed
here in plain Hebrew for Johnny; it is never allowed to invent a number of
its own (see NARRATIVE_SYSTEM).

KNOWN GAP (flagged, not silently worked around): the `leads` table has no
`client_id` column, so matching a client to their onboarding intake falls
back to "newest lead row with this email" — the same approximate join
api_server.py's checkout already uses to backfill leads. Adding a nullable
`client_id bigint` column to `leads` in Supabase (and setting it at checkout,
see the try/except in core/api_server.py's checkout handler) would make this
exact; until that column exists, the fallback below is used and the match
method is always reported alongside the data, never hidden.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call
from core.third_party_pricing import THIRD_PARTY_PRICING

AGENT_NAME = "budget_agent"

HARD = "hard"
ESTIMATE = "estimate"
UNKNOWN = "unknown"

# HeyGen avatar video generation: published per-minute API pricing range
# across avatar models. We don't record which avatar model a given video
# used, so this is deliberately a wide range, never collapsed into one
# fabricated number. SOURCE OF TRUTH: core/third_party_pricing.py (checked
# twice a month by agents/price_monitor_agent.py) — don't hardcode a second
# copy here again.
HEYGEN_USD_PER_MIN_RANGE = tuple(THIRD_PARTY_PRICING["heygen"]["generation_usd_per_min_range"])

# SEO tool reference LIST prices for the entry-appropriate plan per
# PRICING["seo_tiers"]. NOT the client's verified invoice — currency,
# discounts, and grandfathered plans can all differ from this. Always
# surfaced as ESTIMATE. Derived from core/third_party_pricing.py — same
# single-source-of-truth note as above.
SEO_TOOL_LIST_PRICE_USD_MONTH = {
    "seoptimer": THIRD_PARTY_PRICING["seoptimer"]["plans"]["diy_seo"],
    "semrush": THIRD_PARTY_PRICING["semrush"]["plans"]["pro"],
    "ahrefs": THIRD_PARTY_PRICING["ahrefs"]["plans"]["lite"],
}

# Weekly scan cadence -> re-alert an ongoing deviation at most this often
# (mirrors website_agent's ISSUE_DEDUP_DAYS idiom), not every run.
DEVIATION_DEDUP_DAYS = 6
DEFAULT_ZERO_CONVERSION_SPEND_FLOOR_ILS = 300
DEFAULT_AD_SPEND_DRIFT_PCT_HIGH = 130
DEFAULT_AD_SPEND_DRIFT_PCT_LOW = 50

# Created lazily — no DB client at import time (api_server imports every agent at startup)
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _log_activity(client_id: int, action_type: str, details: dict, result: dict = None):
    _db().table("client_activity").insert({
        "client_id": client_id, "agent_name": AGENT_NAME,
        "action_type": action_type, "details": details, "result": result or {},
    }).execute()


# ─── Forecast (the client's own stated numbers from onboarding intake) ────────

def _extract_ils_amount(text: str):
    """Best-effort ILS figure from a free-text chat answer ("5000 ש״ח", "כ-
    3000-4000 בחודש", "5k"). Returns the midpoint of a range or a single
    number — or None when the text has no clear number. Deliberately
    conservative: guessing wrong here would corrupt every drift comparison
    downstream, so "no number found" must stay a real, common outcome, never
    silently coerced to 0."""
    if not text:
        return None
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))]
    if not numbers:
        return None
    if re.search(r"\d\s*(?:k|K|אלף)", text):
        numbers = [n * 1000 if n < 1000 else n for n in numbers]
    numbers = [n for n in numbers if n > 0]
    if not numbers or len(numbers) > 2:
        return None  # 0 numbers, or too many (an ambiguous sentence) — don't guess
    return round(sum(numbers) / len(numbers), 2)


def _lead_row(client_id: int) -> tuple:
    """The newest `leads` row for this client, plus how it was matched. See
    the module docstring's KNOWN GAP note — prefers an exact client_id match
    (once that column exists in Supabase) and falls back to the email-based
    approximate match that already exists in api_server.py's checkout."""
    from agents.client_agent import get_client
    db = _db()
    try:
        rows = (db.table("leads").select("*").eq("client_id", client_id)
                .order("created_at", desc=True).limit(1).execute().data)
        if rows:
            return rows[0], "client_id (exact)"
    except Exception:
        pass  # the leads.client_id column doesn't exist yet — fall through to email match

    email = (get_client(client_id).get("email") or "").strip()
    if not email:
        return {}, "not found (client has no email on file)"
    rows = (db.table("leads").select("*").eq("client_email", email)
            .order("created_at", desc=True).limit(1).execute().data or [])
    return (rows[0], "email (approximate — newest lead matching this email)") if rows else ({}, "not found")


def get_forecast(client_id: int) -> dict:
    """The client's own stated budget + the market_reality narrative already
    generated at proposal time — real intake data, but see `source` for how
    reliably it's matched to this specific client (module docstring)."""
    lead, source = _lead_row(client_id)
    if not lead:
        return {"available": False, "source": source}
    answers = lead.get("answers") or {}
    proposal = lead.get("proposal") or {}
    budget_text = answers.get("marketing_budget") or ""
    organic_text = answers.get("organic_budget") or ""
    return {
        "available": True,
        "source": source,
        "stated_marketing_budget_text": budget_text,
        "stated_marketing_budget_ils": _extract_ils_amount(budget_text),
        "stated_organic_budget_text": organic_text,
        "stated_organic_budget_ils": _extract_ils_amount(organic_text),
        "market_reality": proposal.get("market_reality", ""),
        "goals_90_days": proposal.get("goals_90_days") or [],
        "kpis": proposal.get("kpis") or {},
    }


# ─── Real ad spend (reused, never re-fetched independently) ───────────────────

def get_ad_spend(client_id: int) -> dict:
    from agents.google_ads_agent import get_campaign_performance as google_perf
    from agents.meta_ads_agent import get_campaign_performance as meta_perf

    google = google_perf(client_id)
    meta = meta_perf(client_id)
    total = round((google.get("totals", {}).get("cost", 0) if google.get("connected") else 0)
                  + (meta.get("totals", {}).get("cost", 0) if meta.get("connected") else 0), 2)
    return {
        "google_ads": google, "meta_ads": meta,
        "total_ils": total, "period": "last_30_days", "confidence": HARD,
        "source": "live Google Ads / Meta Marketing API (agents.google_ads_agent / meta_ads_agent)",
    }


# ─── Client-paid external tools (the real gap — investigated per tool) ────────

def get_external_client_paid_costs(client_id: int) -> dict:
    """What the client pays each external tool DIRECTLY, split honestly by
    confidence per tool. See the module docstring for what HARD/ESTIMATE/
    UNKNOWN mean here — every branch below explains WHY it landed where it
    did, not just what the number is."""
    from agents.client_agent import get_accounts
    from core.cost_tracker import usd_to_ils

    connected_platforms = {a.get("platform") for a in get_accounts(client_id)
                           if a.get("status") == "active"}
    result = {}

    from agents.media_agent import get_monthly_usage as media_usage, HIGGSFIELD_PLATFORM
    if HIGGSFIELD_PLATFORM in connected_platforms:
        usage = media_usage(client_id)
        result["higgsfield"] = {
            "connected": True, "confidence": UNKNOWN,
            "images_this_month": usage["images_this_month"],
            "videos_this_month": usage["videos_this_month"],
            "credits_used_this_month": usage["credits_total"],
            "note": ("Higgsfield exposes no spend/billing API. Credit usage per "
                     "generation is logged when their job response includes it "
                     "(real, when present), but there is no published $-per-credit "
                     "rate or plan-balance endpoint to convert it to money, or to know "
                     "how close the client is to their plan's cap. Genuine gap, not "
                     "an estimate — do not convert this to ILS."),
        }
    else:
        result["higgsfield"] = {"connected": False}

    from agents.avatar_agent import get_monthly_usage as avatar_usage, HEYGEN_PLATFORM, ELEVENLABS_PLATFORM
    if HEYGEN_PLATFORM in connected_platforms:
        usage = avatar_usage(client_id)
        minutes = usage["minutes_used"]
        low, high = HEYGEN_USD_PER_MIN_RANGE
        result["heygen"] = {
            "connected": True, "confidence": ESTIMATE,
            "minutes_used_this_month": minutes,
            "tier": usage["tier"],
            "estimated_usd_range": [round(minutes * low, 2), round(minutes * high, 2)],
            "estimated_ils_range": [usd_to_ils(minutes * low), usd_to_ils(minutes * high)],
            "note": (f"Minutes are real — HeyGen's own render duration, tracked by "
                     f"avatar_agent. The $ range is a wide ESTIMATE from HeyGen's "
                     f"published per-minute API pricing (${low}-${high}/min depending on "
                     f"avatar model, which isn't recorded per video) — not the client's "
                     f"actual invoice. HeyGen does expose a wallet-balance endpoint "
                     f"(v2/user/remaining_quota) that could give a real current-balance "
                     f"reading, but its semantics/units after the Feb-2026 pay-as-you-go "
                     f"migration weren't verifiable with confidence — flagged as a "
                     f"future one-round check, not wired in here to avoid presenting "
                     f"an unverified number as real."),
        }
    else:
        result["heygen"] = {"connected": False}

    if ELEVENLABS_PLATFORM in connected_platforms:
        result["elevenlabs"] = {
            "connected": True, "confidence": UNKNOWN,
            "note": ("No usage or spend visibility today. ElevenLabs meters characters "
                     "against the client's own plan quota; we never query it. Genuinely "
                     "unknown — not estimated."),
        }
    else:
        result["elevenlabs"] = {"connected": False}

    from agents.seo_agent import get_connected_tool
    tool = get_connected_tool(client_id)
    if tool:
        list_price = SEO_TOOL_LIST_PRICE_USD_MONTH.get(tool)
        checked_at = (THIRD_PARTY_PRICING.get(tool) or {}).get("checked_at", "unknown date")
        result["seo_tool"] = {
            "connected": True, "tool": tool,
            "confidence": ESTIMATE if list_price else UNKNOWN,
            "reference_list_price_usd_month": list_price,
            "reference_list_price_ils_month": usd_to_ils(list_price) if list_price else None,
            "note": (f"{tool}'s published list price for its entry-appropriate plan "
                     f"(checked {checked_at}) — NOT verified against this client's actual "
                     f"invoice, currency, or any discount/grandfathered plan. None of "
                     f"these tools expose a per-account billing API to us."
                     if list_price else f"no reference price on file for '{tool}'."),
        }
    else:
        result["seo_tool"] = {"connected": False}

    return result


def get_client_facing_costs(client_id: int) -> dict:
    """The client-safe subset for the profile page's external-cost-
    transparency report: what THEY pay directly (ad spend + external tools).
    Never our internal cost/margin — that stays admin-only."""
    ad_spend = get_ad_spend(client_id)
    platforms = []
    for platform, label, perf in (("google_ads", "Google Ads", ad_spend["google_ads"]),
                                   ("meta_ads", "Meta (פייסבוק + אינסטגרם)", ad_spend["meta_ads"])):
        if not perf.get("connected"):
            continue
        platforms.append({
            "platform": platform, "label": label,
            "period": perf.get("period", "last_30_days"),
            "error": perf.get("error"),
            "total_spend": (perf.get("totals") or {}).get("cost", 0),
            "confidence": HARD,
            "campaigns": [{"name": c.get("name"), "status": c.get("status"), "cost": c.get("cost")}
                          for c in (perf.get("campaigns") or [])],
        })
    return {
        "currency": "ILS",
        "platforms": platforms,
        "total_spend": round(sum(p["total_spend"] for p in platforms), 2),
        "external_tools": get_external_client_paid_costs(client_id),
    }


# ─── Internal cost gaps (OUR side — real known costs not yet in client_costs) ─

def get_internal_cost_gaps(client_id: int) -> dict:
    """Real internal costs that PRICING already knows the value of, but that
    nothing writes into client_costs yet — so admin_service's official
    cost/margin figures understate our true cost. v1 covers the one gap that
    exists today (InstaWP hosting passthrough); folded into
    our_revenue_and_cost_adjusted in get_financial_picture rather than
    guessed at or silently ignored."""
    from agents.onboarding_agent import PRICING
    from agents.website_agent import is_provisioned_by_us

    gaps = {}
    if is_provisioned_by_us(client_id):
        basis = PRICING["website"]["new_site_hosting"]["cost_monthly_ils"]
        gaps["instawp_hosting"] = {
            "applies": True,
            "expected_monthly_ils": basis,
            "recorded_in_client_costs": False,
            "note": (f"This client's site was provisioned by us on InstaWP — the "
                     f"~{basis} ILS/month hosting cost is real (PRICING's own cost "
                     "basis) but cost_tracker v1 only ever records Claude API calls, "
                     "so nothing writes this into client_costs. Folded into "
                     "our_revenue_and_cost_adjusted below as a read-time patch; the "
                     "real fix is provision_site recording a recurring cost row "
                     "instead — flagged, not built here, to avoid a second untested "
                     "write path on top of an already-large change."),
        }
    return gaps


# ─── The combined picture ───────────────────────────────────────────────────

def get_financial_picture(client_id: int) -> dict:
    """The full financial picture for ONE client. Every section names its own
    confidence; there is no single blended "the number" that hides a mix of
    real data and guesses. Safe to call often — the only network calls are
    the same ones the ad agents already cache (PERF_CACHE_TTL_SECONDS)."""
    from core import admin_service

    admin = admin_service.get_client_admin(client_id)
    if not admin:
        return {"available": False}

    forecast = get_forecast(client_id)
    ad_spend = get_ad_spend(client_id)
    external = get_external_client_paid_costs(client_id)
    gaps = get_internal_cost_gaps(client_id)

    cost_adjustment = round(sum(g["expected_monthly_ils"] for g in gaps.values()
                                if g.get("applies")), 2)
    fee = admin.get("monthly_fee") or 0
    adjusted = None
    if cost_adjustment:
        cost_adjusted = round((admin.get("cost_month") or 0) + cost_adjustment, 2)
        margin_adjusted = round(fee - cost_adjusted, 2) if fee else None
        adjusted = {
            "cost_month_ils": cost_adjusted,
            "margin_month_ils": margin_adjusted,
            "margin_pct": (round(margin_adjusted / fee * 100, 1)
                          if fee and margin_adjusted is not None else None),
            "adjustment_ils": cost_adjustment,
            "confidence": HARD,  # the adjustment is a known PRICING constant, not a guess
            "note": "our_revenue_and_cost plus known real internal costs not yet in client_costs (internal_cost_gaps).",
        }

    drift = None
    budget_ils = forecast.get("stated_marketing_budget_ils") if forecast.get("available") else None
    if budget_ils:
        actual = ad_spend["total_ils"]
        ratio_pct = round(actual / budget_ils * 100, 1)
        drift = {
            "stated_monthly_budget_ils": budget_ils,
            "actual_30d_ad_spend_ils": actual,
            "ratio_pct": ratio_pct,
            "direction": ("over" if ratio_pct > DEFAULT_AD_SPEND_DRIFT_PCT_HIGH
                         else "under" if ratio_pct < DEFAULT_AD_SPEND_DRIFT_PCT_LOW
                         else "on_track"),
            "confidence": ESTIMATE,  # the forecast side is parsed from free chat text
            "note": ("Real 30-day ad spend vs. the client's own stated monthly budget "
                     "from onboarding (parsed from free text — see forecast.source for "
                     "match reliability). The window is a rolling 30 days, not a "
                     "calendar month, so read the ratio as directional, not exact."),
        }

    return {
        "available": True,
        "client_id": client_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "our_revenue_and_cost": {
            "monthly_fee_ils": fee,
            "cost_month_ils": admin.get("cost_month"),
            "cost_by_category": admin.get("cost_by_category"),
            "margin_month_ils": admin.get("margin_month"),
            "margin_pct": admin.get("margin_pct"),
            "confidence": HARD,
            "source": "admin_service.get_client_admin — unchanged, single source of truth for our own numbers",
        },
        "our_revenue_and_cost_adjusted": adjusted,
        "ad_spend": ad_spend,
        "external_client_paid_costs": external,
        "internal_cost_gaps": gaps,
        "forecast": forecast,
        "ad_spend_vs_forecast": drift,
    }


# ─── Trend (real drift over time — recorded by the weekly scan) ───────────────

def _snapshot(client_id: int, picture: dict):
    """One durable point per weekly scan, reusing client_activity (no new
    table) — so get_trend() below is a real recorded time series, not a
    single always-current snapshot re-derived on each call."""
    our = picture["our_revenue_and_cost"]
    _log_activity(client_id, "budget_snapshot_recorded", {
        "monthly_fee_ils": our["monthly_fee_ils"],
        "cost_month_ils": our["cost_month_ils"],
        "margin_month_ils": our["margin_month_ils"],
        "ad_spend_30d_ils": picture["ad_spend"]["total_ils"],
        "ad_spend_vs_forecast_ratio_pct": (picture.get("ad_spend_vs_forecast") or {}).get("ratio_pct"),
    })


def get_trend(client_id: int, points: int = 12) -> dict:
    """Real recorded weekly snapshots, oldest first — for a margin/spend
    sparkline. Empty until weekly scans have actually run a few times; no
    retroactive history exists before this agent shipped, and this function
    says so instead of fabricating a backfilled series."""
    rows = (_db().table("client_activity").select("details,created_at")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", "budget_snapshot_recorded")
            .order("created_at", desc=True).limit(points).execute().data or [])
    rows.reverse()
    return {
        "points": [{"date": r["created_at"], **(r.get("details") or {})} for r in rows],
        "note": "Recorded going forward by the weekly scan — no retroactive history exists before this agent shipped.",
    }


# ─── Deviation alerts (weekly scan) ────────────────────────────────────────────

def _already_alerted(client_id: int, issue_key: str) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DEVIATION_DEDUP_DAYS)).isoformat()
    rows = (_db().table("client_activity").select("id")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", "budget_deviation_flagged")
            .eq("details->>issue_key", issue_key)
            .gte("created_at", cutoff).limit(1).execute().data)
    return bool(rows)


def _check_deviations(client_id: int, picture: dict, settings: dict) -> list:
    """Returns [(issue_key, message), ...] for meaningful deviations only —
    thresholds are admin-tunable (app_settings, same pattern as
    low_margin_threshold_pct) rather than hardcoded judgment calls nobody can
    adjust without a deploy."""
    issues = []

    drift = picture.get("ad_spend_vs_forecast")
    if drift:
        high = settings.get("ad_spend_drift_pct_high", DEFAULT_AD_SPEND_DRIFT_PCT_HIGH)
        low = settings.get("ad_spend_drift_pct_low", DEFAULT_AD_SPEND_DRIFT_PCT_LOW)
        if drift["ratio_pct"] > high:
            issues.append(("ad_spend_over_budget",
                f"client {client_id}: 30-day ad spend {drift['actual_30d_ad_spend_ils']} ILS is "
                f"{drift['ratio_pct']}% of their stated {drift['stated_monthly_budget_ils']} ILS "
                "monthly budget — meaningfully over"))
        elif drift["ratio_pct"] < low:
            issues.append(("ad_spend_under_budget",
                f"client {client_id}: 30-day ad spend {drift['actual_30d_ad_spend_ils']} ILS is only "
                f"{drift['ratio_pct']}% of their stated {drift['stated_monthly_budget_ils']} ILS "
                "monthly budget — meaningfully under (check pacing on their connected account)"))

    floor = settings.get("zero_conversion_spend_floor_ils", DEFAULT_ZERO_CONVERSION_SPEND_FLOOR_ILS)
    for key, label in (("google_ads", "Google Ads"), ("meta_ads", "Meta")):
        perf = picture["ad_spend"].get(key) or {}
        totals = perf.get("totals") or {}
        if perf.get("connected") and totals.get("cost", 0) >= floor and totals.get("conversions", 0) == 0:
            issues.append((f"{key}_zero_conversions",
                f"client {client_id}: {label} spent {totals['cost']} ILS in the last 30 days with "
                "ZERO recorded conversions — channel underperforming relative to its cost"))

    return issues


def _active_clients() -> list:
    rows = (_db().table("clients").select("id").eq("status", "active").execute().data or [])
    return [r["id"] for r in rows]


def run_weekly_scan() -> dict:
    """Cron entry point (GET /api/budget/scan): snapshots every active
    client's financial picture for trend history, and raises deduped alerts
    on meaningful deviations. Never raises — one client's failure must not
    stop the rest of the scan."""
    settings = None
    try:
        from core import admin_service
        settings = admin_service.get_settings()
    except Exception as e:
        log_step(AGENT_NAME, "weekly_scan", f"settings fetch failed, using defaults: {e}")
        settings = {}

    summary = {"clients_scanned": 0, "alerts_raised": 0, "errors": 0}
    for client_id in _active_clients():
        try:
            picture = get_financial_picture(client_id)
            if not picture.get("available"):
                continue
            _snapshot(client_id, picture)
            summary["clients_scanned"] += 1
            for issue_key, message in _check_deviations(client_id, picture, settings):
                if _already_alerted(client_id, issue_key):
                    continue
                _log_activity(client_id, "budget_deviation_flagged", {"issue_key": issue_key})
                agent_alert(AGENT_NAME, [message])
                summary["alerts_raised"] += 1
        except Exception as e:
            summary["errors"] += 1
            log_step(AGENT_NAME, "weekly_scan", f"client {client_id}: {e}")
    log_step(AGENT_NAME, "weekly_scan", f"done — {summary}")
    return summary


# ─── Optional narrative (on-demand LLM call — explains, never invents) ────────

NARRATIVE_SYSTEM = """You are uallak's financial analyst, writing a short internal note for
Johnny (the agency owner) about ONE client's money picture. Same rigor as the sales-chat's
market_reality reasoning, but grounded entirely in the real historical numbers you're given —
never speculation.

You will receive a JSON object where sections carry an explicit "confidence" field: "hard" (a
real recorded/API number), "estimate" (arithmetic from a real usage figure times a published
rate — not this client's verified invoice), or "unknown" (no visibility, stated honestly).

HARD RULES:
- NEVER invent or infer a number that isn't in the data you were given. If something is
  missing or unknown, say it's missing — do not estimate a replacement figure yourself.
- When you cite a number, carry its confidence naturally into the sentence (e.g. "Google Ads
  spend is real: ...", "the HeyGen figure is a rough estimate: ...", "Higgsfield spend is
  simply unknown to us").
- Hebrew output, 3-6 sentences total, plain and direct — an internal ops note for Johnny, not
  client-facing copy, no marketing tone.
- If nothing meaningful stands out (numbers roughly on track, no real gaps worth a look),
  say so briefly instead of manufacturing a concern.

Return JSON only:
{"narrative": "Hebrew text, 3-6 sentences"}"""

_NARRATIVE_FALLBACK = {"narrative": ""}


def generate_narrative(client_id: int) -> dict:
    picture = get_financial_picture(client_id)
    if not picture.get("available"):
        return _NARRATIVE_FALLBACK
    try:
        return timed_step(
            AGENT_NAME, "narrative_llm",
            lambda: safe_claude_json_call(
                NARRATIVE_SYSTEM, json.dumps(picture, ensure_ascii=False, default=str),
                max_tokens=700, client_id=client_id, cost_category="claude_budget"))
    except ClaudeJSONError as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: narrative generation failed: {e}"])
        return _NARRATIVE_FALLBACK
