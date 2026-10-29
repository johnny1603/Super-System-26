"""Search Console READ-ONLY plumbing for Google verification ROUND 2 —
behind SEARCH_CONSOLE_ENABLED, OFF by default. docs/GOOGLE-ROUND2.md is the
source of truth for the scope, the justification and the demo.

While the flag is off (the default, and the state while round 1 is under
review): build_consent_url raises SearchConsoleDisabled, the OAuth routes
answer 404, the dashboard card stays hidden, and
rank_tracking.SearchConsoleProvider reports "not connected".

Auth model — its own consent: client_accounts row platform='search_console',
account_id=the chosen property (siteUrl, e.g. "sc-domain:example.co.il" or
"https://example.co.il/"), access_token=refresh token.

Two read calls, nothing else: sites.list (the client's verified properties)
and searchanalytics.query (queries with clicks, impressions, average
position). No write method exists in the code, and webmasters.readonly could
not perform one.

VERIFICATION STATUS: written against the Search Console API v3 reference,
never run with a live grant.
"""
import os
from urllib.parse import quote, urlencode

import httpx

from agents.keys_agent import get_key
from core.google_ads_service import OAUTH_CONSENT_URL, _access_token  # noqa: F401
from core.google_ads_service import exchange_code as _google_exchange_code


def enabled() -> bool:
    """SEARCH_CONSOLE_ENABLED, read at call time (core/round2_flags)."""
    from core.round2_flags import search_console_enabled
    return search_console_enabled()


PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")
REDIRECT_PATH = "/api/oauth/search-console/callback"
# THE Search Console scope list (round 2; docs/GOOGLE-ROUND2.md mirrors it)
SEARCH_CONSOLE_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
API_BASE = "https://www.googleapis.com/webmasters/v3"
TIMEOUT = 30
# Permission levels Google reports for a property the user has really verified
# (or was granted); "siteUnverifiedUser" is listed but cannot be read
VERIFIED_LEVELS = ("siteOwner", "siteFullUser", "siteRestrictedUser")


class SearchConsoleDisabled(RuntimeError):
    """SEARCH_CONSOLE_ENABLED is off: no consent, no read."""


def _require_enabled():
    if not enabled():
        raise SearchConsoleDisabled("SEARCH_CONSOLE_ENABLED is off")


def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def build_consent_url(state: str) -> str:
    _require_enabled()
    params = {
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SEARCH_CONSOLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH_CONSENT_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    _require_enabled()
    return _google_exchange_code(code, redirect_uri())


def _get(refresh_token: str, path: str) -> dict:
    response = httpx.get(f"{API_BASE}/{path}", headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                         timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"Search Console GET {path} failed: {response.status_code} {response.text[:300]}")
    return response.json()


def _post(refresh_token: str, path: str, body: dict) -> dict:
    response = httpx.post(f"{API_BASE}/{path}", headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                          json=body, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"Search Console POST {path} failed: {response.status_code} {response.text[:300]}")
    return response.json()


def list_verified_sites(refresh_token: str) -> list:
    """sites.list → [{"site_url", "permission"}] for properties the account can read."""
    _require_enabled()
    entries = _get(refresh_token, "sites").get("siteEntry") or []
    return [{"site_url": e.get("siteUrl", ""), "permission": e.get("permissionLevel", "")}
            for e in entries if e.get("permissionLevel") in VERIFIED_LEVELS and e.get("siteUrl")]


def query_by_keyword(refresh_token: str, site_url: str, start_date: str, end_date: str,
                     row_limit: int = 1000) -> list:
    """searchanalytics.query by query → [{"query", "clicks", "impressions", "position"}]."""
    _require_enabled()
    data = _post(refresh_token, f"sites/{quote(site_url, safe='')}/searchAnalytics/query",
                 {"startDate": start_date, "endDate": end_date, "dimensions": ["query"],
                  "rowLimit": row_limit, "dataState": "final"})
    return [{"query": (r.get("keys") or [""])[0], "clicks": r.get("clicks", 0),
             "impressions": r.get("impressions", 0), "position": r.get("position")}
            for r in data.get("rows") or []]


def site_matches_domain(site_url: str, domain: str) -> bool:
    """Does a property cover the client's site domain (www. ignored)?"""
    if not domain:
        return False
    domain = domain.lower().removeprefix("www.")
    site = site_url.lower()
    if site.startswith("sc-domain:"):
        root = site.split(":", 1)[1]
        return domain == root or domain.endswith("." + root)
    host = site.split("://", 1)[-1].split("/", 1)[0].removeprefix("www.")
    return host == domain
