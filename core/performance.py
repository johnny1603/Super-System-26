"""The client dashboard's "ביצועים" (Performance) page — and the ONE channel
status resolver that future alerts reuse.

## The status model

Every channel card is exactly one of:
- not_in_plan        — the package doesn't include it (neutral, not an error)
- not_connected      — in the plan, no active connection (a connect button
                       where one exists)
- connected_no_data  — connected, nothing reported yet ("last checked" shown)
- active             — key numbers + when the data was pulled
- needs_attention    — connection error, missing scope, sync failure, setup
                       left half-done (a client-safe reason CODE; the page
                       words it, raw errors never leave this module)

`channel_statuses(client_id)` is that resolution, once. Reused, not
reimplemented: client_journey.required_connections (what the package
includes), website_agent.gtm_missing_scopes (the same reconnect signal as the
dashboard card), website_agent.get_conversion_tracking_status, the ads
agents' daily readers, the TikTok/YouTube engagement summaries, client_leads.

## Honesty rules

- Only data stored or readable today. No analysis, scoring or AI summary.
- Never a fake zero: a connected channel with nothing reported is
  connected_no_data and carries NO numbers.
- Leads are OUR client_leads rows (people who contacted the client), per
  channel by their recorded source_platform. Not the `leads` table — that is
  uallak's own sales funnel (CLAUDE.md).
"""
import time
from datetime import date, datetime, timedelta, timezone

SERVICE_NAME = "performance"

CHANNELS = ("google_ads", "meta", "tiktok", "organic_seo", "youtube",
            "merchant_center", "website_tracking", "leads")
PAID_CHANNELS = ("google_ads", "meta")
WINDOWS = (7, 30, 90)

# client_leads.source_platform values per paid channel (lead_tracking's classifier)
LEAD_SOURCE_PLATFORM = {"google_ads": "google_ads", "meta": "meta"}

# The dashboard card each "connect" action points at
CONNECT_TARGET = {"google_ads": "gadsBtn", "meta": "metaBtn", "tiktok": "tiktokBtn",
                  "youtube": "youtubeBtn", "merchant_center": "merchantBtn",
                  "organic_seo": "siteBtn", "website_tracking": "gtmLink"}

# Reads that have no cache of their own (TikTok/YouTube engagement, the GTM
# live-version read) — and that alert on failure — are cached here so a page
# reload doesn't re-query them
_LOCAL_CACHE_TTL_SECONDS = 900
_local_cache = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cached(key: tuple, fetch):
    hit = _local_cache.get(key)
    if hit and hit[0] > time.time() - _LOCAL_CACHE_TTL_SECONDS:
        return hit[1], hit[2]
    value = fetch()
    pulled = _now()
    _local_cache[key] = (time.time(), value, pulled)
    return value, pulled


def _card(status: str, **extra) -> dict:
    return {"status": status, **extra}


# ─── Plan ────────────────────────────────────────────────────────────────────

def _plan(client_id: int) -> dict:
    from core.client_journey import required_connections
    try:
        checklist = required_connections(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] plan read failed for client {client_id}: {e}")
        checklist = {"steps": [], "services": [], "resolved": False}
    steps = {s["key"] for s in checklist.get("steps", [])}
    services = checklist.get("services", [])

    def has(*tokens):
        return any(any(t in s for t in tokens) for s in services)
    return {
        "resolved": bool(checklist.get("resolved")),
        "google_ads": "google_ads" in steps,
        "meta": "meta" in steps,
        "tiktok": "tiktok" in steps,
        "youtube": "youtube" in steps,
        "organic_seo": has("seo", "organic", "אורגני"),
        "merchant_center": has("merchant", "shopping"),
        "website_tracking": "website" in steps,
        "leads": True,   # every package captures leads (landing page + site form)
    }


# ─── Leads (client_leads) ────────────────────────────────────────────────────

def _lead_rows(client_id: int, since: date) -> dict:
    """{"rows": [...], "by_channel": bool}. by_channel False when the
    attribution column isn't there (migration 2026-08-02 not run)."""
    from agents.client_agent import _db
    q = (lambda cols: _db().table("client_leads").select(cols)
         .eq("client_id", client_id).gte("created_at", since.isoformat())
         .limit(5000).execute().data or [])
    try:
        return {"rows": q("created_at,source_platform"), "by_channel": True}
    except Exception:
        try:
            return {"rows": q("created_at"), "by_channel": False}
        except Exception as e:
            print(f"[{SERVICE_NAME}] client_leads unreadable for client {client_id}: {e}")
            return {"rows": [], "by_channel": False, "error": True}


def _count_leads(rows: list, start: date, end: date, platform: str = None) -> int:
    lo, hi = start.isoformat(), (end + timedelta(days=1)).isoformat()
    return sum(1 for r in rows
               if lo <= (r.get("created_at") or "") < hi
               and (platform is None or r.get("source_platform") == platform))


# ─── Paid windows ────────────────────────────────────────────────────────────

def _window_sums(days: list, start: date, end: date) -> dict:
    lo, hi = start.isoformat(), end.isoformat()
    picked = [d for d in days if lo <= (d.get("date") or "") <= hi]
    return {
        "spend": round(sum(d["cost"] for d in picked), 2),
        "impressions": sum(d["impressions"] for d in picked),
        "clicks": sum(d["clicks"] for d in picked),
        "conversions": round(sum(d["conversions"] for d in picked), 1),
    }


def _paid_windows(daily: dict, leads: dict, channel: str) -> list:
    until = date.fromisoformat(daily["until"])
    out = []
    for n in WINDOWS:
        cur_start, cur_end = until - timedelta(days=n - 1), until
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=n - 1)
        periods = {}
        for name, (start, end) in (("current", (cur_start, cur_end)),
                                   ("previous", (prev_start, prev_end))):
            sums = _window_sums(daily["days"], start, end)
            if leads["by_channel"]:
                lead_count = _count_leads(leads["rows"], start, end, LEAD_SOURCE_PLATFORM[channel])
                sums["leads"] = lead_count
                # Cost per lead from OUR leads; None (shown as —), never 0, when
                # there were no leads to divide by
                sums["cost_per_lead"] = round(sums["spend"] / lead_count, 2) if lead_count else None
            else:
                sums["leads"] = None
                sums["cost_per_lead"] = None
            sums["from"], sums["to"] = start.isoformat(), end.isoformat()
            periods[name] = sums
        out.append({"days": n, **periods})
    return out


# ─── Per-channel resolution ──────────────────────────────────────────────────

def _active_platforms(accounts: list) -> set:
    return {a.get("platform") for a in accounts if a.get("status") == "active"}


def _ads_card(client_id: int, channel: str, platforms: set, accounts: list) -> dict:
    if channel == "google_ads":
        from agents.google_ads_agent import PENDING_STATUS, get_daily_metrics
        if any(a.get("platform") == "google_ads" and a.get("status") == PENDING_STATUS for a in accounts):
            return _card("needs_attention", reason="choose_account", last_checked=_now())
        if "google_ads" not in platforms:
            return _card("not_connected")
    else:
        from agents.meta_ads_agent import get_daily_metrics
        if not platforms & {"meta_ads", "meta_page"}:
            return _card("not_connected")
        if "meta_ads" not in platforms:
            # Page connected, no ad account: nothing paid to report
            return _card("connected_no_data", note="page_only", last_checked=_now())
    daily = get_daily_metrics(client_id)
    if not daily.get("connected"):
        return _card("not_connected")
    if daily.get("error"):
        return _card("needs_attention", reason="sync_failed", last_checked=_now())
    pulled = daily.get("pulled_at") or _now()
    if not any(d["impressions"] or d["clicks"] or d["cost"] or d["conversions"] for d in daily["days"]):
        return _card("connected_no_data", last_checked=pulled)
    last30 = _window_sums(daily["days"], date.fromisoformat(daily["until"]) - timedelta(days=29),
                          date.fromisoformat(daily["until"]))
    return _card("active", data_pulled_at=pulled, key_numbers=last30, _daily=daily)


def _engagement_card(client_id: int, channel: str, platforms: set) -> dict:
    platform = "tiktok" if channel == "tiktok" else "youtube"
    if platform not in platforms:
        return _card("not_connected")
    if channel == "tiktok":
        from agents.tiktok_content_agent import get_engagement_summary
    else:
        from agents.youtube_content_agent import get_engagement_summary
    summary, pulled = _cached((channel, client_id), lambda: get_engagement_summary(client_id))
    if not summary.get("connected"):
        return _card("not_connected")
    if summary.get("error"):
        return _card("needs_attention", reason="sync_failed", last_checked=pulled)
    if not summary.get("videos"):
        return _card("connected_no_data", last_checked=pulled)
    numbers = {k: summary.get(k) for k in ("videos", "views", "likes", "comments", "shares")
               if summary.get(k) is not None}
    return _card("active", data_pulled_at=pulled, key_numbers=numbers,
                 period_days=summary.get("period_days"))


def _tracking_card(client_id: int, platforms: set) -> dict:
    if "google_tagmanager" not in platforms:
        return _card("not_connected")
    from agents.website_agent import get_conversion_tracking_status, gtm_missing_scopes
    try:
        if gtm_missing_scopes(client_id):
            return _card("needs_attention", reason="reconnect", last_checked=_now())
        status, pulled = _cached(("gtm", client_id), lambda: get_conversion_tracking_status(client_id))
    except Exception as e:
        print(f"[{SERVICE_NAME}] tracking status failed for client {client_id}: {e}")
        return _card("needs_attention", reason="sync_failed", last_checked=_now())
    if status.get("configured_live"):
        return _card("active", data_pulled_at=pulled,
                     key_numbers={"lead_event_live": True,
                                  "lead_event_tags": len(status.get("lead_event_tags") or [])})
    return _card("connected_no_data", last_checked=pulled)


def _leads_card(leads: dict) -> dict:
    if leads.get("error"):
        return _card("needs_attention", reason="sync_failed", last_checked=_now())
    today = date.today()
    counts = {f"last_{n}_days": _count_leads(leads["rows"], today - timedelta(days=n - 1), today)
              for n in WINDOWS}
    if not leads["rows"]:
        return _card("connected_no_data", last_checked=_now())
    return _card("active", data_pulled_at=_now(), key_numbers=counts)


def _organic_card(platforms: set, organic: dict) -> dict:
    if "wordpress" not in platforms:
        return _card("not_connected")
    if organic.get("tracking_connected") and any(r.get("current_position") is not None
                                                 for r in organic.get("rows", [])):
        return _card("active", data_pulled_at=_now(), key_numbers=organic.get("summary"))
    return _card("connected_no_data", last_checked=_now())


def _merchant_card(platforms: set) -> dict:
    if "merchant_center_pending" in platforms and "merchant_center" not in platforms:
        return _card("needs_attention", reason="finish_setup", last_checked=_now())
    if "merchant_center" not in platforms:
        return _card("not_connected")
    # No Merchant Center metrics reader exists — connected is all we can say
    return _card("connected_no_data", last_checked=_now())


def channel_statuses(client_id: int, _organic: dict = None, _leads: dict = None,
                     _keep_daily: bool = False) -> dict:
    """THE status resolution for every channel. Never raises: a channel whose
    read throws becomes needs_attention/sync_failed, never a broken page.
    The underscore params let performance_page share reads it already made."""
    from agents.client_agent import get_accounts
    try:
        accounts = get_accounts(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] accounts read failed for client {client_id}: {e}")
        accounts = []
    platforms = _active_platforms(accounts)
    plan = _plan(client_id)
    leads = _leads if _leads is not None else _lead_rows(client_id, date.today() - timedelta(days=180))
    if _organic is None:
        from core.rank_tracking import organic_table
        _organic = organic_table(client_id)

    resolvers = {
        "google_ads": lambda: _ads_card(client_id, "google_ads", platforms, accounts),
        "meta": lambda: _ads_card(client_id, "meta", platforms, accounts),
        "tiktok": lambda: _engagement_card(client_id, "tiktok", platforms),
        "youtube": lambda: _engagement_card(client_id, "youtube", platforms),
        "organic_seo": lambda: _organic_card(platforms, _organic),
        "merchant_center": lambda: _merchant_card(platforms),
        "website_tracking": lambda: _tracking_card(client_id, platforms),
        "leads": lambda: _leads_card(leads),
    }
    connected_any = {
        "google_ads": bool(platforms & {"google_ads"}),
        "meta": bool(platforms & {"meta_ads", "meta_page"}),
        "tiktok": "tiktok" in platforms, "youtube": "youtube" in platforms,
        "organic_seo": False,
        "merchant_center": bool(platforms & {"merchant_center", "merchant_center_pending"}),
        "website_tracking": "google_tagmanager" in platforms, "leads": True,
    }
    cards = {}
    for channel in CHANNELS:
        # A live connection always counts as in plan; with an unreadable
        # package nothing is ASSUMED owed (not_in_plan stays neutral)
        in_plan = connected_any[channel] or (plan["resolved"] and plan[channel]) or channel == "leads"
        if not in_plan:
            cards[channel] = _card("not_in_plan")
            continue
        try:
            card = resolvers[channel]()
        except Exception as e:
            print(f"[{SERVICE_NAME}] {channel} status failed for client {client_id}: {e}")
            card = _card("needs_attention", reason="sync_failed", last_checked=_now())
        if card["status"] == "not_connected" and channel in CONNECT_TARGET:
            card["connect"] = CONNECT_TARGET[channel]
        if not _keep_daily:
            card.pop("_daily", None)
        cards[channel] = card
    return {"channels": cards, "plan_resolved": plan["resolved"], "generated_at": _now()}


# ─── The page ────────────────────────────────────────────────────────────────

def performance_page(client_id: int) -> dict:
    """Everything the page renders, always well-formed (even all zero)."""
    from core.rank_tracking import organic_table
    leads = _lead_rows(client_id, date.today() - timedelta(days=180))
    try:
        organic = organic_table(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] organic table failed for client {client_id}: {e}")
        organic = {"keywords_available": False, "tracking_connected": False, "rows": [],
                   "summary": {"at_target": 0, "total": 0}}
    statuses = channel_statuses(client_id, _organic=organic, _leads=leads, _keep_daily=True)

    paid = []
    for channel in PAID_CHANNELS:
        card = statuses["channels"].get(channel, {})
        daily = card.pop("_daily", None)
        if card.get("status") == "active" and daily:
            paid.append({"channel": channel, "data_pulled_at": card.get("data_pulled_at"),
                         "leads_by_channel": leads["by_channel"],
                         "windows": _paid_windows(daily, leads, channel)})
    for card in statuses["channels"].values():
        card.pop("_daily", None)

    return {"channels": statuses["channels"], "plan_resolved": statuses["plan_resolved"],
            "paid": paid, "organic": organic, "generated_at": statuses["generated_at"]}
