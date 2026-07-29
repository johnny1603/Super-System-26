"""Per-client lead-volume tiers, and uallak's own infrastructure-cost watch.

Two DELIBERATELY separate concerns live here, and they must not be wired to
each other:

1. **Client-facing tier** (`run_volume_scan`, `get_client_volume`) — a
   value-based price per client, set by how many leads their campaigns produce.
   It gates NOTHING: campaign-level attribution ships in every package and
   works identically at every tier. The tier only sets a price.
2. **Internal infra watch** (`get_platform_usage`) — uallak's own database size
   in Supabase against our plan's real ceiling (Supabase caps size, never row
   count). Purely a cost signal for Johnny. A client's tier
   has no relationship to what they cost us to serve, and connecting the two
   would be exactly the infrastructure-cost pricing the brief ruled out.

## The counter is deliberately swappable

`count_client_leads` is the ONE place that answers "how many leads did this
client get". Everything else in this module treats it as a black box, so the
source can be replaced without touching the tier, staging, alerting or
dashboard code.

Today it sums Google Ads + Meta **conversions**, and that is an approximation
with limits worth stating plainly, because the number is heading for an
invoice:

  - It counts PAID campaigns we run. Organic form fills, WhatsApp messages,
    phone calls and walk-ins are invisible to it.
  - A "conversion" is whatever the client configured in their own ad account.
    It may not mean "lead", and the client can change it.
  - GA4's `generate_lead` — which website_agent configures via GTM — is NOT
    readable to us: our Google OAuth scope is Ads, not Analytics.

`count_client_leads` returns `complete: False` and a `notes` list saying so,
and the admin UI shows it. Nothing here pretends the number is authoritative.

## Windows: rolling 30 days for the count, calendar month for the tier

The COUNT is rolling 30 days, because that is the window both ad platforms'
existing (cached, working) performance queries already speak. Inventing
calendar-month GAQL and Insights queries would mean shipping two untested
ad-API calls to obtain a number that is already sitting in a function we call
today — a bad trade, and the brief allowed either window.

The TIER is snapshotted per calendar month, and the BILLABLE tier for a month
is the tier earned in the PREVIOUS month. That is what makes it non-retroactive:
crossing a threshold today can never change what a client owes for today.
"""
import os
from datetime import date, datetime, timedelta, timezone

from core.agent_base import agent_alert, log_step

AGENT_NAME = "lead_volume"

# Tiers themselves live in PRICING (agents/onboarding_agent.py) — the single
# source of truth for every business number. Nothing here hardcodes a price or
# a threshold; see tier_for_lead_count().

_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"],
                                     os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _period_start(when: date = None) -> str:
    """First day of the calendar month, as the period key."""
    today = when or datetime.now(timezone.utc).date()
    return today.replace(day=1).isoformat()


def _previous_period(period: str) -> str:
    current = date.fromisoformat(period)
    return (current - timedelta(days=1)).replace(day=1).isoformat()


# ─── The swappable counter ───────────────────────────────────────────────────

def count_client_leads(client_id: int) -> dict:
    """How many leads this client received in the last 30 days.

    THE seam. Replace the body — not the signature — when a real per-client
    lead record exists (a tracked form endpoint, Meta lead-form ingestion, a
    CRM webhook). Returns:

        {lead_count: int, sources: {...}, complete: bool, notes: [str]}

    `complete` is False whenever the number cannot see every lead the client
    actually got, which today is always. Callers must surface that, never
    quietly round it away.
    """
    total = 0.0
    sources, notes = {}, []

    try:
        from agents.google_ads_agent import get_campaign_performance as google_performance
        google = google_performance(client_id)
        if google.get("connected"):
            value = float((google.get("totals") or {}).get("conversions", 0) or 0)
            sources["google_ads"] = round(value, 1)
            total += value
    except Exception as e:
        notes.append(f"Google Ads count unavailable ({e})")

    try:
        from agents.meta_ads_agent import get_campaign_performance as meta_performance
        meta = meta_performance(client_id)
        if meta.get("connected"):
            value = float((meta.get("totals") or {}).get("conversions", 0) or 0)
            sources["meta"] = round(value, 1)
            total += value
    except Exception as e:
        notes.append(f"Meta count unavailable ({e})")

    notes.append("counts paid Google/Meta conversions only — organic, WhatsApp, "
                 "phone and walk-in leads are not visible to this counter")
    notes.append("a 'conversion' is whatever the client configured in their own "
                 "ad account, and may not mean 'lead'")
    if not sources:
        notes.append("no connected ad account — nothing to count")

    return {
        "lead_count": int(round(total)),
        "sources": sources,
        # Always False today, and honestly so. See the module docstring.
        "complete": False,
        "notes": notes,
        "window": "rolling_30_days",
    }


# ─── Tier resolution + monthly staging ───────────────────────────────────────

def tier_for_lead_count(lead_count: int) -> dict:
    from agents.onboarding_agent import tier_for_lead_count as resolve
    return resolve(lead_count)


def _tier_rank(tier_key: str) -> int:
    """Position on the ladder, so 'higher tier' is a comparison and not a
    price guess."""
    from agents.onboarding_agent import PRICING
    for index, tier in enumerate(PRICING["lead_volume_tiers"]):
        if tier["key"] == tier_key:
            return index
    return 0


def _volume_row(client_id: int, period: str) -> dict:
    rows = (_db().table("client_lead_volume").select("*")
            .eq("client_id", client_id).eq("period", period)
            .limit(1).execute().data or [])
    return rows[0] if rows else {}


def _already_alerted(client_id: int, tier_key: str) -> bool:
    """One alert per client per tier, ever — 'the FIRST time a client crosses
    into a new tier'. Same client_activity dedup idiom the platform scans use."""
    rows = (
        _db().table("client_activity").select("id")
        .eq("client_id", client_id)
        .eq("agent_name", AGENT_NAME)
        .eq("action_type", "lead_tier_crossed")
        .eq("details->>tier_key", tier_key)
        .limit(1).execute().data or []
    )
    return bool(rows)


def record_client_volume(client_id: int, client_name: str = "") -> dict:
    """Count, resolve the tier, upsert this month's row, and alert once on a
    first-time crossing into a higher tier."""
    from agents.client_agent import log_activity

    counted = count_client_leads(client_id)
    tier = tier_for_lead_count(counted["lead_count"])
    period = _period_start()
    existing = _volume_row(client_id, period)

    # peak, not latest: a client who hit 400 leads and drifted back to 280
    # still earned the 301-700 tier for this month.
    peak_key = existing.get("peak_tier_key") or tier["key"]
    if _tier_rank(tier["key"]) > _tier_rank(peak_key):
        peak_key = tier["key"]

    row = {
        "client_id": client_id,
        "period": period,
        "lead_count": counted["lead_count"],
        "tier_key": tier["key"],
        "peak_tier_key": peak_key,
        "source": ",".join(counted["sources"].keys()) or "none",
        "complete": counted["complete"],
        "counted_at": _now(),
    }
    # Upsert on the (client_id, period) unique key rather than read-then-write:
    # the scan is daily and single-threaded today, but a manual re-run alongside
    # the scheduled one would otherwise race into a duplicate-key error.
    _db().table("client_lead_volume").upsert(row, on_conflict="client_id,period").execute()

    crossed = None
    # Admin-side only, by design: the brief is explicit that Johnny decides how
    # and whether to tell the client, so nothing here touches PayPal or the
    # client dashboard.
    peak_tier = next((t for t in _all_tiers() if t["key"] == peak_key), None)
    if peak_tier and _tier_rank(peak_key) > 0 and not _already_alerted(client_id, peak_key):
        crossed = peak_key
        log_activity(client_id, AGENT_NAME, "lead_tier_crossed",
                     {"tier_key": peak_key, "lead_count": counted["lead_count"],
                      "monthly_fee": peak_tier["monthly_fee"], "period": period})
        agent_alert(AGENT_NAME, [
            f"client {client_id}"
            + (f" ({client_name})" if client_name else "")
            + f" crossed into lead tier '{peak_key}' "
              f"(₪{peak_tier['monthly_fee']}/mo) with {counted['lead_count']} leads "
              f"in the last 30 days. Billable from the NEXT cycle, and only if you "
              f"choose to — nothing was charged automatically. "
              f"NOTE: count is paid Google/Meta conversions only, not a complete "
              f"lead count."
        ])

    return {**row, "tier": tier, "counted": counted, "crossed_into": crossed}


def _all_tiers() -> list:
    from agents.onboarding_agent import PRICING
    return PRICING["lead_volume_tiers"]


def get_client_volume(client_id: int) -> dict:
    """What the admin client drawer shows. Reads the stored snapshot only —
    never calls an ad API, so opening the drawer can't burn API quota."""
    period = _period_start()
    current = _volume_row(client_id, period)
    previous = _volume_row(client_id, _previous_period(period))
    tiers = {tier["key"]: tier for tier in _all_tiers()}
    included = _all_tiers()[0]

    current_key = current.get("tier_key") or included["key"]
    # Staged, never retroactive: this month is billed on LAST month's earned
    # tier, so crossing a threshold today cannot change today's price.
    billable_key = previous.get("peak_tier_key") or included["key"]

    return {
        "period": period,
        "measured": bool(current),
        "lead_count": current.get("lead_count", 0),
        "window": "rolling_30_days",
        "current_tier": tiers.get(current_key, included),
        "peak_tier": tiers.get(current.get("peak_tier_key") or current_key, included),
        "billable_tier": tiers.get(billable_key, included),
        "billable_from_next_cycle": tiers.get(current.get("peak_tier_key") or current_key, included),
        "source": current.get("source", ""),
        "complete": bool(current.get("complete")),
        "counted_at": current.get("counted_at"),
    }


def run_volume_scan() -> dict:
    """Daily job: refresh every active client's lead volume and tier.

    Costs two cached ad-API reads per connected client per day — deliberately a
    scheduled scan rather than an on-page-load lookup, because the Google Ads
    daily operation cap and Meta's rolling threshold are real (see the
    api-quotas skill) and the admin dashboard would otherwise burn them on
    every refresh.
    """
    log_step(AGENT_NAME, "volume_scan", "starting")
    clients = (_db().table("clients").select("id, name, status")
               .eq("status", "active").limit(1000).execute().data or [])

    summary = {"clients_scanned": 0, "crossings": [], "errors": []}
    for client in clients:
        try:
            result = record_client_volume(client["id"], client.get("name", ""))
            summary["clients_scanned"] += 1
            if result.get("crossed_into"):
                summary["crossings"].append(
                    {"client_id": client["id"], "tier": result["crossed_into"],
                     "lead_count": result["lead_count"]})
        except Exception as e:
            summary["errors"].append({"client_id": client["id"], "error": str(e)})

    infra = get_platform_usage(alert_if_over=True)
    log_step(AGENT_NAME, "volume_scan",
             f"{summary['clients_scanned']} clients, {len(summary['crossings'])} crossings")
    return {**summary, "infrastructure": infra}


# ─── Internal only: uallak's own Supabase footprint ──────────────────────────

def _row_count(table: str) -> int:
    try:
        return (_db().table(table).select("id", count="exact")
                .limit(1).execute().count) or 0
    except Exception:
        return 0


def _db_size_bytes():
    """Real database size straight from Postgres, via the db_size_bytes() RPC
    (migrations/2026-07-30-supabase-size-watch.sql).

    None — never 0 — when the migration hasn't been applied or the call fails,
    so the caller can say "not measured" instead of showing 0%, which would
    read as "plenty of room left"."""
    try:
        value = _db().rpc("db_size_bytes", {}).execute().data
        return int(value) if value is not None else None
    except Exception:
        return None


def get_platform_usage(alert_if_over: bool = False) -> dict:
    """uallak's own Supabase footprint vs the plan's real ceiling.

    Internal cost signal only — deliberately NOT connected to the client tiers
    above (see module docstring).

    Measures database SIZE, which is what Supabase actually caps (it enforces
    no row limit on any tier). Row counts are still reported alongside, as
    context for what is filling the database, but they are not the budget.
    """
    from core.admin_service import get_settings

    settings = get_settings()
    budget_mb = float(settings.get("supabase_size_budget_mb") or 0)
    warn_pct = float(settings.get("supabase_warn_pct") or 80)

    size_bytes = _db_size_bytes()
    # Decimal MB, matching how the budget is stored (see admin_service).
    used_mb = round(size_bytes / 1_000_000, 2) if size_bytes is not None else None
    used_pct = (round(used_mb / budget_mb * 100, 1)
                if used_mb is not None and budget_mb else None)

    counts = {table: _row_count(table) for table in ("leads", "lead_messages")}

    usage = {
        "row_counts": counts,
        "total_rows": sum(counts.values()),
        "db_size_mb": used_mb,
        "size_budget_mb": budget_mb,
        "used_pct": used_pct,
        "warn_pct": warn_pct,
        "over_threshold": bool(used_pct is not None and used_pct >= warn_pct),
        "measured": used_mb is not None,
        "measures": (
            "database size as reported by Postgres (pg_database_size), against "
            "the Supabase plan's real per-project ceiling"
            if used_mb is not None else
            "database size unavailable — run "
            "migrations/2026-07-30-supabase-size-watch.sql"
        ),
    }

    if alert_if_over and usage["over_threshold"]:
        agent_alert(AGENT_NAME, [
            f"uallak's Supabase database is at {used_pct}% of the plan ceiling "
            f"({used_mb:,.1f} MB of {budget_mb:,.0f} MB). This is uallak's own "
            f"infrastructure cost, unrelated to any client's pricing tier. "
            f"Raise supabase_size_budget_mb in settings after a plan upgrade."
        ])
    return usage
