"""Google Analytics 4 READ-ONLY plumbing for Google verification ROUND 2 —
behind ANALYTICS_ENABLED, OFF by default. docs/GOOGLE-ROUND2.md is the source
of truth for the scope, the justification and the demo. Mirrors
core/search_console_service.py.

While the flag is off (the default): build_consent_url raises
AnalyticsDisabled, every reader here raises before any HTTP call, the OAuth
routes and the property endpoints answer 404, the dashboard card stays
hidden, and core/site_traffic reports "not enabled" without reading anything.

Auth model — its own consent: client_accounts row platform='google_analytics',
account_id = the chosen GA4 property ("properties/123456789"), stored like
the GTM container choice (the row's account_id; the client picks it from
the properties their own login can reach), access_token = refresh token.
Status 'pending' until a property is chosen when the login reaches more than
one.

Two kinds of call, both reads, nothing else:
  - Admin API accountSummaries.list — which GA4 properties the login can read;
  - Data API properties.runReport — users / sessions per date range and per
    default channel group.
analytics.readonly cannot write, and no write method exists in this module.

VERIFICATION STATUS: written against the GA4 Admin API v1beta and Data API
v1beta references, never run with a live grant.
"""
import os
from urllib.parse import urlencode

import httpx

from agents.keys_agent import get_key
from core.google_ads_service import OAUTH_CONSENT_URL, _access_token  # noqa: F401
from core.google_ads_service import exchange_code as _google_exchange_code


def enabled() -> bool:
    """ANALYTICS_ENABLED, read at call time (core/round2_flags)."""
    from core.round2_flags import analytics_enabled
    return analytics_enabled()


PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")
REDIRECT_PATH = "/api/oauth/analytics/callback"
# THE Analytics scope list (round 2; docs/GOOGLE-ROUND2.md mirrors it).
# analytics.readonly is the narrowest scope that can read reports: the
# Analytics APIs have no per-report or per-property scope.
ANALYTICS_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"
DATA_API = "https://analyticsdata.googleapis.com/v1beta"
TIMEOUT = 30
MAX_DATE_RANGES = 4    # the Data API's own limit per runReport request


class AnalyticsDisabled(RuntimeError):
    """ANALYTICS_ENABLED is off: no consent, no read."""


class AnalyticsReadError(RuntimeError):
    """A failed Analytics call, with what performance._classify_google_failure
    reads (status, google_reason, api) — never a token."""

    def __init__(self, message, status=None, google_reason="", api=""):
        super().__init__(message)
        self.status, self.google_reason, self.api = status, google_reason, api


def _require_enabled():
    if not enabled():
        raise AnalyticsDisabled("ANALYTICS_ENABLED is off")


def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def build_consent_url(state: str) -> str:
    _require_enabled()
    params = {
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(ANALYTICS_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH_CONSENT_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    _require_enabled()
    return _google_exchange_code(code, redirect_uri())


def _fail(response, api: str, what: str):
    reason = ""
    try:
        error = (response.json() or {}).get("error") or {}
        reason = error.get("status") or ""
        for detail in error.get("details") or []:
            reason = detail.get("reason") or reason
    except Exception:
        pass
    raise AnalyticsReadError(f"Analytics {what} failed: HTTP {response.status_code} {reason}",
                             status=response.status_code, google_reason=reason, api=api)


def list_properties(refresh_token: str) -> list:
    """accountSummaries.list → [{"property": "properties/N", "name", "account_name"}]
    — every GA4 property this login can read."""
    _require_enabled()
    out, page_token = [], ""
    for _ in range(10):
        params = {"pageSize": 200}
        if page_token:
            params["pageToken"] = page_token
        response = httpx.get(f"{ADMIN_API}/accountSummaries", params=params,
                             headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                             timeout=TIMEOUT)
        if response.status_code != 200:
            _fail(response, "analyticsadmin.googleapis.com", "accountSummaries.list")
        body = response.json() or {}
        for account in body.get("accountSummaries") or []:
            for prop in account.get("propertySummaries") or []:
                if str(prop.get("property") or "").startswith("properties/"):
                    out.append({"property": prop["property"], "name": prop.get("displayName", ""),
                                "account_name": account.get("displayName", "")})
        page_token = body.get("nextPageToken") or ""
        if not page_token:
            break
    return out


def run_report(refresh_token: str, prop: str, date_ranges: list, metrics: list,
               dimensions: list = None, order_bys: list = None, limit: int = None,
               keep_empty_rows: bool = False) -> dict:
    """properties.runReport — the raw JSON. date_ranges: [{"startDate","endDate",
    "name"}] (at most MAX_DATE_RANGES); with several ranges Google adds a
    "dateRange" dimension whose value is each range's name."""
    _require_enabled()
    if not str(prop or "").startswith("properties/"):
        raise ValueError("not a GA4 property")
    if not 1 <= len(date_ranges) <= MAX_DATE_RANGES:
        raise ValueError("1-4 date ranges per request")
    body = {"dateRanges": date_ranges, "metrics": [{"name": m} for m in metrics]}
    if dimensions:
        body["dimensions"] = [{"name": d} for d in dimensions]
    if order_bys:
        body["orderBys"] = order_bys
    if limit:
        body["limit"] = limit
    if keep_empty_rows:
        body["keepEmptyRows"] = True
    response = httpx.post(f"{DATA_API}/{prop}:runReport", json=body,
                          headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                          timeout=TIMEOUT)
    if response.status_code != 200:
        _fail(response, "analyticsdata.googleapis.com", "runReport")
    return response.json() or {}
