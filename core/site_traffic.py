"""Website traffic from the client's own Google Analytics 4 property — the
reader behind the Performance page's website section, client_state's
`website_traffic` and the morning briefing's visitors line. Round 2, behind
ANALYTICS_ENABLED (core/round2_flags), OFF by default: while off, traffic()
answers {"enabled": False} without reading anything.

## What is read (core/analytics_service.run_report, all reads)

  A: last 7 / previous 7 / last 30 / previous 30 days   (activeUsers, sessions)
  B: last 90 / previous 90 days, yesterday, same weekday a week earlier
  C: today so far
  D: sessions per default channel group, last 30 days   (traffic sources)
  E: the first day the property recorded any session    (has-data boundary)

Windows end YESTERDAY (today is incomplete), exactly like the ads daily series;
GA4's relative dates ("7daysAgo", "yesterday") are resolved in the property's
own time zone by Google.

## Honesty rules

- "No data yet" is NOT zero. A period that ends before the property's first
  recorded session is None (shown "—"); only a period inside the property's
  life can be 0 visitors. No session ever recorded → no numbers at all
  (performance: connected_no_data).
- Every comparison is computed HERE (`compare`), never by a model, and only
  when BOTH periods lie entirely inside the property's recorded life —
  otherwise `change` is None and nobody states a trend.
- The has-data boundary is compared to calendar dates in Israel time; the
  property's own time zone may differ by a day around midnight, which can
  only make a comparison be withheld, never invented.

Caching: the ads daily-series discipline — one result per client for
CACHE_TTL_SECONDS, a failure for FAILURE_CACHE_TTL_SECONDS only, never raises,
one admin alert per failure kind (alert_once).
"""
import time
from datetime import date, datetime, timedelta, timezone

SERVICE_NAME = "site_traffic"
PLATFORM = "google_analytics"
CACHE_TTL_SECONDS = 3600
FAILURE_CACHE_TTL_SECONDS = 120
FAILURE_ALERT_DAYS = 1
WINDOWS = (7, 30, 90)
SOURCES_DAYS = 30
METRICS = ("activeUsers", "sessions")
ISRAEL_UTC_OFFSET_HOURS = 3    # same coarse offset as engagement_agent

# GA4's default channel groups → the five buckets the page shows (+ other)
SOURCE_GROUPS = {
    "organic": ("Organic Search", "Organic Shopping", "Organic Video"),
    "paid": ("Paid Search", "Paid Social", "Paid Shopping", "Paid Video", "Paid Other",
             "Display", "Cross-network", "Audio"),
    "direct": ("Direct",),
    "social": ("Organic Social",),
    "referral": ("Referral", "Affiliates"),
}
SOURCE_KEYS = ("organic", "paid", "direct", "social", "referral", "other")

_cache = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=ISRAEL_UTC_OFFSET_HOURS)).date()


def forget(client_id: int):
    _cache.pop(client_id, None)


# ─── Pure logic (tested directly) ────────────────────────────────────────────

def compare(current, previous) -> dict:
    """{"direction": up|down|same, "change_pct": int|None} — or None when
    either side has no data. previous 0 → current > 0 is "up" with no
    percentage (there is no honest % from zero)."""
    if current is None or previous is None:
        return None
    if current == previous:
        return {"direction": "same", "change_pct": 0}
    direction = "up" if current > previous else "down"
    if previous == 0:
        return {"direction": direction, "change_pct": None}
    return {"direction": direction, "change_pct": round((current - previous) * 100 / previous)}


def _period(start: date, end: date, first_data: date, values: dict) -> dict:
    """The period's figures, or None when it ends before the first recorded
    session (no data — not zero). `complete` = it starts inside that life."""
    if first_data is None or end < first_data:
        return None
    return {"users": int(values.get("activeUsers", 0)), "sessions": int(values.get("sessions", 0)),
            "from": start.isoformat(), "to": end.isoformat(), "complete": start >= first_data}


def _change(current: dict, previous: dict) -> dict:
    """Comparison only when both periods are complete (entirely recorded)."""
    if not (current and previous and current["complete"] and previous["complete"]):
        return None
    return {"users": compare(current["users"], previous["users"]),
            "sessions": compare(current["sessions"], previous["sessions"])}


def group_sources(rows: list) -> dict:
    """[(channel_group, sessions)] → {organic, paid, direct, social, referral, other}."""
    lookup = {name: key for key, names in SOURCE_GROUPS.items() for name in names}
    out = {k: 0 for k in SOURCE_KEYS}
    for name, sessions in rows:
        out[lookup.get(name, "other")] += int(sessions)
    return out


def build(today: date, first_data: date, ranges: dict, today_values: dict, sources: list) -> dict:
    """Assemble the figures from parsed report values. `ranges` maps range
    name → {"activeUsers", "sessions"} (absent = 0 when inside the property's
    life). Pure: no I/O."""
    yesterday = today - timedelta(days=1)
    get = lambda name: ranges.get(name) or {}
    windows = []
    for n in WINDOWS:
        cur_start = yesterday - timedelta(days=n - 1)
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=n - 1)
        current = _period(cur_start, yesterday, first_data, get(f"cur{n}"))
        previous = _period(prev_start, prev_end, first_data, get(f"prev{n}"))
        windows.append({"days": n, "current": current, "previous": previous,
                        "change": _change(current, previous)})
    y = _period(yesterday, yesterday, first_data, get("yesterday"))
    week_ago = yesterday - timedelta(days=7)
    y_prev = _period(week_ago, week_ago, first_data, get("week_ago"))
    today_period = _period(today, today, first_data, today_values or {})
    src = None
    if sources is not None and first_data is not None:
        grouped = group_sources(sources)
        src = {"days": SOURCES_DAYS, "sessions": grouped, "total": sum(grouped.values())}
    return {"first_data_date": first_data.isoformat() if first_data else None,
            "today": today_period, "yesterday": y, "same_day_last_week": y_prev,
            "yesterday_change": _change(y, y_prev), "windows": windows, "sources": src}


# ─── Report requests ─────────────────────────────────────────────────────────

def _range(name: str, start: str, end: str) -> dict:
    return {"name": name, "startDate": start, "endDate": end}


REQUEST_A = [_range("cur7", "7daysAgo", "yesterday"), _range("prev7", "14daysAgo", "8daysAgo"),
             _range("cur30", "30daysAgo", "yesterday"), _range("prev30", "60daysAgo", "31daysAgo")]
REQUEST_B = [_range("cur90", "90daysAgo", "yesterday"), _range("prev90", "180daysAgo", "91daysAgo"),
             _range("yesterday", "yesterday", "yesterday"), _range("week_ago", "8daysAgo", "8daysAgo")]


def _metric_values(row: dict) -> dict:
    values = row.get("metricValues") or []
    return {m: float((values[i] or {}).get("value") or 0) if i < len(values) else 0.0
            for i, m in enumerate(METRICS)}


def parse_ranges(report: dict, ranges: list) -> dict:
    """runReport with several named ranges → {name: {metric: value}}."""
    out = {}
    for row in report.get("rows") or []:
        dims = row.get("dimensionValues") or []
        name = (dims[0] or {}).get("value") if dims else (ranges[0]["name"] if len(ranges) == 1 else "")
        if name:
            out[name] = _metric_values(row)
    return out


def _first_data_date(refresh_token: str, prop: str) -> date:
    from core import analytics_service as ga
    report = ga.run_report(refresh_token, prop, [_range("all", "2015-08-14", "today")], ["sessions"],
                           dimensions=["date"], limit=1,
                           order_bys=[{"dimension": {"dimensionName": "date"}, "desc": False}])
    rows = report.get("rows") or []
    if not rows:
        return None
    raw = ((rows[0].get("dimensionValues") or [{}])[0] or {}).get("value") or ""
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except (ValueError, IndexError):
        return None


def _read(refresh_token: str, prop: str) -> dict:
    from core import analytics_service as ga
    first = _first_data_date(refresh_token, prop)
    if first is None:
        return build(_today(), None, {}, {}, None)
    ranges = parse_ranges(ga.run_report(refresh_token, prop, REQUEST_A, list(METRICS), keep_empty_rows=True),
                          REQUEST_A)
    ranges.update(parse_ranges(ga.run_report(refresh_token, prop, REQUEST_B, list(METRICS),
                                             keep_empty_rows=True), REQUEST_B))
    today_report = ga.run_report(refresh_token, prop, [_range("today", "today", "today")], list(METRICS),
                                 keep_empty_rows=True)
    today_values = parse_ranges(today_report, [_range("today", "", "")]).get("today", {})
    src_report = ga.run_report(refresh_token, prop, [_range("src", f"{SOURCES_DAYS}daysAgo", "yesterday")],
                               ["sessions"], dimensions=["sessionDefaultChannelGroup"])
    sources = [(((r.get("dimensionValues") or [{}])[0] or {}).get("value") or "",
                float(((r.get("metricValues") or [{}])[0] or {}).get("value") or 0))
               for r in src_report.get("rows") or []]
    return build(_today(), first, ranges, today_values, sources)


# ─── The reader ──────────────────────────────────────────────────────────────

def _row(client_id: int) -> dict:
    from agents.client_agent import get_accounts
    return next((a for a in get_accounts(client_id) if a.get("platform") == PLATFORM), {})


def traffic(client_id: int, force_refresh: bool = False) -> dict:
    """{"enabled", "connected", ...figures} — never raises.
    Not connected → {"enabled": True, "connected": False}. A login with several
    properties and none chosen yet → "choose_property". A failed read →
    "error" + "reason" (performance._classify_google_failure codes)."""
    from core.round2_flags import analytics_enabled
    if not analytics_enabled():
        return {"enabled": False, "connected": False}
    cached = _cache.get(client_id)
    if cached and not force_refresh:
        ttl = FAILURE_CACHE_TTL_SECONDS if cached[1].get("error") else CACHE_TTL_SECONDS
        if cached[0] > time.time() - ttl:
            return cached[1]
    try:
        row = _row(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] accounts read failed for client {client_id}: {type(e).__name__}")
        return {"enabled": True, "connected": True, "error": True, "reason": "sync_failed"}
    if not row:
        return {"enabled": True, "connected": False}
    prop = row.get("account_id") or ""
    if row.get("status") != "active" or not prop.startswith("properties/"):
        return {"enabled": True, "connected": True, "choose_property": True}

    from core.secrets_vault import get_client_secret
    try:
        figures = _read(get_client_secret(client_id, PLATFORM, row=row), prop)
    except Exception as e:
        from core.performance import _classify_google_failure, _warn
        failure = _classify_google_failure(e, "analyticsdata.googleapis.com")
        _warn(f"website_traffic read failed for client {client_id}: {failure['api']} "
              f"HTTP {failure['http_status']} {failure['google_reason'] or type(e).__name__} "
              f"-> {failure['reason']}", client_id=client_id, channel="website_traffic", **failure)
        try:
            from core.chat_fallback import alert_once
            alert_once(SERVICE_NAME, client_id, f"{PLATFORM}:{failure['reason']}",
                       f"client {client_id}: Google Analytics traffic read failed: {failure['api']} "
                       f"HTTP {failure['http_status']} {failure['google_reason'] or type(e).__name__} "
                       f"(card shows: {failure['reason']})", FAILURE_ALERT_DAYS)
        except Exception:
            pass
        failed = {"enabled": True, "connected": True, "error": True, "reason": failure["reason"]}
        _cache[client_id] = (time.time(), failed)
        return failed
    result = {"enabled": True, "connected": True, "property": prop, "pulled_at": _now(), **figures}
    _cache[client_id] = (time.time(), result)
    return result


# ─── Read-outs for agents and the briefing ───────────────────────────────────

def for_agent(t: dict) -> dict:
    """client_state's section: the same figures, with the comparisons already
    computed — agents state them, never compute them."""
    if not t.get("enabled"):
        return {"available": False, "why": "not_enabled"}
    if not t.get("connected"):
        return {"available": False, "why": "not_connected"}
    if t.get("choose_property"):
        return {"available": False, "why": "property_not_chosen"}
    if t.get("error"):
        return {"available": False, "why": t.get("reason") or "sync_failed"}
    if not t.get("first_data_date"):
        return {"available": True, "has_data": False}
    windows = {}
    for w in t.get("windows") or []:
        windows[f"last_{w['days']}_days"] = {
            "visitors": (w["current"] or {}).get("users"),
            "previous_period_visitors": (w["previous"] or {}).get("users"),
            "trend": ((w.get("change") or {}).get("users")),
        }
    return {"available": True, "has_data": True, "pulled_at": t.get("pulled_at"),
            "visitors_today_so_far": (t.get("today") or {}).get("users"),
            "visitors_yesterday": (t.get("yesterday") or {}).get("users"),
            "visitors_same_day_last_week": (t.get("same_day_last_week") or {}).get("users"),
            "yesterday_vs_same_day_last_week": ((t.get("yesterday_change") or {}).get("users")),
            "windows": windows,
            "sessions_by_source_last_30_days": (t.get("sources") or {}).get("sessions"),
            "rule": "trend is computed by code; state it only when it is not null"}


def briefing_fact(t: dict) -> dict:
    """{"visitors_yesterday", "direction", "change_pct", "compared_to"} or None.
    Only when yesterday has data; direction only when the same weekday a week
    earlier is fully recorded too."""
    if not (t.get("enabled") and t.get("connected")) or t.get("error") or not t.get("first_data_date"):
        return None
    y = t.get("yesterday")
    if not y:
        return None
    change = (t.get("yesterday_change") or {}).get("users")
    fact = {"visitors_yesterday": y["users"]}
    if change:
        fact.update({"direction": change["direction"], "change_pct": change["change_pct"],
                     "compared_to": "same_day_last_week",
                     "visitors_same_day_last_week": (t.get("same_day_last_week") or {}).get("users")})
    return fact
