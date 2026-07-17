"""Low-level adapters for the client-paid SEO research tools.

Pricing model (PRICING["seo_tiers"] in onboarding_agent — the single source of
truth): the client pays the tool subscription DIRECTLY (SEOptimer / SEMrush /
Ahrefs by budget tier) and uallak operates it for them. This module talks to
whichever tool the client actually pays for — it never picks or bills a tool.

The per-client API key lives in client_accounts (platform='seo_tool',
account_id=tool slug, access_token=API key) — connected by ADMIN via
POST /api/seo/connect-tool, since none of these tools has a client-facing
OAuth flow. Business logic lives in agents/seo_agent.py; this module is HTTP
only (mirrors google_ads_service / meta_service).

Common interface: get_research(tool, api_key, domain) returns ONE normalized
dict regardless of tool: {"tool", "domain", "overview", "top_keywords",
"competitors", "backlinks", "errors"} — partial failures land in "errors"
instead of raising, because a half-successful research bundle is still worth
using (and the seo_agent falls back to Claude web-search research only when a
bundle produced nothing at all).

VERIFICATION STATUS: written against the public API docs (SEMrush analytics
v3 CSV reports, Ahrefs API v3 JSON) but NOT yet exercised with a live key —
first run against a real client key is a required test. SEOptimer (the entry
tier) has no adapter yet: its audit API is a paid white-label add-on with
docs behind the paywall — wire it when the first Level-A client's key exists;
until then seo_agent's Claude fallback covers that tier.

Cost discipline: every HTTP call here consumes the CLIENT'S paid API units
(SEMrush bills per result line). Small display limits, a daily call cap
(api_call_counters, fails open like the ads services), and seo_agent's 7-day
research cache keep consumption boring.
"""
import time

import httpx

from core.api_call_counters import increment_call_counter

SEMRUSH_BASE_URL = "https://api.semrush.com/"
SEMRUSH_ANALYTICS_URL = "https://api.semrush.com/analytics/v1/"
AHREFS_BASE_URL = "https://api.ahrefs.com/v3"
SEMRUSH_DATABASE = "il"  # Israeli SERP database — our clients' market
AHREFS_COUNTRY = "il"
TIMEOUT = 30

SUPPORTED_TOOLS = ("seoptimer", "semrush", "ahrefs")
# Tools with a working adapter below; the rest fall back to Claude research
IMPLEMENTED_TOOLS = ("semrush", "ahrefs")

KEYWORD_LIMIT = 20
COMPETITOR_LIMIT = 10

# Runaway-loop brake, not a billing meter (the tools meter units server-side).
# A full research bundle is ~4 calls; 200/day allows ~50 bundles across all
# clients — far above the weekly cycle's real needs.
DAILY_CALL_LIMIT = 200


def _count_call(tool: str):
    count = increment_call_counter(f"seo_{tool}", window_days=1)
    if count > DAILY_CALL_LIMIT:
        raise RuntimeError(f"SEO tool daily call cap reached ({DAILY_CALL_LIMIT}) - refusing call")


# ─── SEMrush (analytics API — CSV-style responses, ';' separated) ────────────

def _semrush_rows(url: str, params: dict) -> list:
    """One SEMrush report → list of dicts. SEMrush returns CSV-like text
    (first line = column names) and errors as an 'ERROR nn :: message' body
    with HTTP 200, so both get handled here."""
    _count_call("semrush")
    response = httpx.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    body = response.text.strip()
    if body.startswith("ERROR"):
        raise RuntimeError(f"semrush: {body[:200]}")
    lines = [line for line in body.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    headers = [h.strip() for h in lines[0].split(";")]
    return [dict(zip(headers, [v.strip() for v in line.split(";")])) for line in lines[1:]]


def semrush_research(api_key: str, domain: str) -> dict:
    result = {"tool": "semrush", "domain": domain, "overview": {},
              "top_keywords": [], "competitors": [], "backlinks": {}, "errors": []}

    try:
        rows = _semrush_rows(SEMRUSH_BASE_URL, {
            "key": api_key, "type": "domain_ranks", "domain": domain,
            "export_columns": "Dn,Rk,Or,Ot,Oc", "database": SEMRUSH_DATABASE})
        if rows:
            row = rows[0]
            result["overview"] = {
                "rank": row.get("Rk"), "organic_keywords": row.get("Or"),
                "organic_traffic": row.get("Ot"), "organic_cost": row.get("Oc")}
    except Exception as e:
        result["errors"].append(f"overview: {e}")

    try:
        rows = _semrush_rows(SEMRUSH_BASE_URL, {
            "key": api_key, "type": "domain_organic", "domain": domain,
            "export_columns": "Ph,Po,Nq,Cp,Tr", "database": SEMRUSH_DATABASE,
            "display_limit": KEYWORD_LIMIT, "display_sort": "tr_desc"})
        result["top_keywords"] = [
            {"keyword": r.get("Ph"), "position": r.get("Po"),
             "monthly_volume": r.get("Nq"), "cpc": r.get("Cp"),
             "traffic_share": r.get("Tr")} for r in rows]
    except Exception as e:
        result["errors"].append(f"keywords: {e}")

    try:
        rows = _semrush_rows(SEMRUSH_BASE_URL, {
            "key": api_key, "type": "domain_organic_organic", "domain": domain,
            "export_columns": "Dn,Cr,Np,Or", "database": SEMRUSH_DATABASE,
            "display_limit": COMPETITOR_LIMIT})
        result["competitors"] = [
            {"domain": r.get("Dn"), "competition_level": r.get("Cr"),
             "common_keywords": r.get("Np"), "organic_keywords": r.get("Or")}
            for r in rows]
    except Exception as e:
        result["errors"].append(f"competitors: {e}")

    try:
        rows = _semrush_rows(SEMRUSH_ANALYTICS_URL, {
            "key": api_key, "type": "backlinks_overview", "target": domain,
            "target_type": "root_domain", "export_columns": "ascore,total,domains_num"})
        if rows:
            row = rows[0]
            result["backlinks"] = {
                "authority_score": row.get("ascore"),
                "total_backlinks": row.get("total"),
                "referring_domains": row.get("domains_num")}
    except Exception as e:
        result["errors"].append(f"backlinks: {e}")

    return result


# ─── Ahrefs (API v3 — JSON, Bearer auth) ──────────────────────────────────────

def _ahrefs_get(api_key: str, path: str, params: dict) -> dict:
    _count_call("ahrefs")
    response = httpx.get(
        f"{AHREFS_BASE_URL}/{path.lstrip('/')}",
        params=params,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"ahrefs {path}: {response.status_code} {response.text[:200]}")
    return response.json()


def ahrefs_research(api_key: str, domain: str) -> dict:
    result = {"tool": "ahrefs", "domain": domain, "overview": {},
              "top_keywords": [], "competitors": [], "backlinks": {}, "errors": []}
    today = time.strftime("%Y-%m-%d")

    try:
        data = _ahrefs_get(api_key, "site-explorer/domain-rating",
                           {"target": domain, "date": today})
        rating = data.get("domain_rating") or {}
        result["overview"] = {"domain_rating": rating.get("domain_rating"),
                              "ahrefs_rank": rating.get("ahrefs_rank")}
    except Exception as e:
        result["errors"].append(f"overview: {e}")

    try:
        data = _ahrefs_get(api_key, "site-explorer/organic-keywords", {
            "target": domain, "country": AHREFS_COUNTRY, "date": today,
            "select": "keyword,best_position,volume", "limit": KEYWORD_LIMIT})
        result["top_keywords"] = [
            {"keyword": row.get("keyword"), "position": row.get("best_position"),
             "monthly_volume": row.get("volume")}
            for row in (data.get("keywords") or [])]
    except Exception as e:
        result["errors"].append(f"keywords: {e}")

    try:
        data = _ahrefs_get(api_key, "site-explorer/organic-competitors", {
            "target": domain, "country": AHREFS_COUNTRY, "date": today,
            "select": "domain,common_keywords", "limit": COMPETITOR_LIMIT})
        result["competitors"] = [
            {"domain": row.get("domain"), "common_keywords": row.get("common_keywords")}
            for row in (data.get("competitors") or [])]
    except Exception as e:
        result["errors"].append(f"competitors: {e}")

    try:
        data = _ahrefs_get(api_key, "site-explorer/backlinks-stats",
                           {"target": domain, "date": today})
        metrics = data.get("metrics") or {}
        result["backlinks"] = {"total_backlinks": metrics.get("live"),
                               "referring_domains": metrics.get("live_refdomains")}
    except Exception as e:
        result["errors"].append(f"backlinks: {e}")

    return result


# ─── Common interface ─────────────────────────────────────────────────────────

def get_research(tool: str, api_key: str, domain: str) -> dict:
    """The one entry point seo_agent uses. Unsupported/unimplemented tools
    return supported=False so the caller falls back to Claude research —
    never raises for that case."""
    tool = (tool or "").strip().lower()
    if tool == "semrush":
        research = semrush_research(api_key, domain)
    elif tool == "ahrefs":
        research = ahrefs_research(api_key, domain)
    else:
        return {"supported": False, "tool": tool,
                "reason": f"no API adapter for '{tool}' yet (implemented: {IMPLEMENTED_TOOLS})"}
    research["supported"] = True
    # "Nothing at all came back" is the fallback trigger; partial data is kept
    research["usable"] = bool(research["overview"] or research["top_keywords"]
                              or research["competitors"] or research["backlinks"])
    return research
