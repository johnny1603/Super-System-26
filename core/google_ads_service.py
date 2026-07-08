"""Low-level Google Ads plumbing: OAuth2 consent/token flow + REST API calls.

Auth model (deliberate — see .claude/skills/google-ads/SKILL.md):
- Each client authorizes THEIR OWN Google Ads account via OAuth2 ("Connect Now"
  in the dashboard). We store the per-client refresh token in client_accounts.
- Service accounts are NOT used: the Google Ads API only accepts them through
  Workspace domain-wide delegation, which doesn't fit standalone client accounts.
- The developer token (GOOGLE_ADS_DEVELOPER_TOKEN) is ours, one for the whole
  system; the OAuth client (GOOGLE_OAUTH_CLIENT_ID/SECRET) is ours; the refresh
  token is the client's. All three go on every API call.

Business logic lives in agents/google_ads_agent.py — this module only talks HTTP.
"""
import os
import time
from urllib.parse import urlencode

import httpx

from agents.keys_agent import get_key

# Bump in one place when Google sunsets this version (they release ~3/year,
# each lives ~a year)
ADS_API_VERSION = "v21"
ADS_BASE_URL = f"https://googleads.googleapis.com/{ADS_API_VERSION}"
OAUTH_CONSENT_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://uallak.com")
REDIRECT_PATH = "/api/oauth/google-ads/callback"
TIMEOUT = 20

# Explorer Access allows 2,880 operations/day. This in-memory guard resets on
# every container restart, so it's a best-effort brake, not an exact meter —
# good enough to keep a runaway loop from burning the developer token's
# reputation during the approval period.
DAILY_OP_LIMIT = 2880
_op_counter = {"date": "", "count": 0}

# refresh_token -> (access_token, expires_at_epoch). Access tokens live ~1h;
# in-memory cache is fine (rule 7: no local-file state, memory is allowed).
_access_token_cache = {}


def _count_operation():
    today = time.strftime("%Y-%m-%d")
    if _op_counter["date"] != today:
        _op_counter["date"] = today
        _op_counter["count"] = 0
    _op_counter["count"] += 1
    if _op_counter["count"] > DAILY_OP_LIMIT:
        raise RuntimeError(
            f"Google Ads daily operation limit reached ({DAILY_OP_LIMIT}) - refusing call"
        )
    if _op_counter["count"] > DAILY_OP_LIMIT * 0.8:
        print(f"[google_ads_service] WARNING: {_op_counter['count']}/{DAILY_OP_LIMIT} daily ops used")


def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def build_consent_url(state: str) -> str:
    """URL for Google's OAuth consent screen. access_type=offline + prompt=consent
    are required to get a refresh token every time (without prompt=consent Google
    only returns one on the very first authorization)."""
    params = {
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": ADS_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH_CONSENT_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    """Authorization code -> {access_token, refresh_token, expires_in, ...}."""
    response = httpx.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
            "client_secret": get_key("GOOGLE_OAUTH_CLIENT_SECRET"),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(),
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _access_token(refresh_token: str) -> str:
    cached = _access_token_cache.get(refresh_token)
    if cached and cached[1] > time.time():
        return cached[0]

    response = httpx.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
            "client_secret": get_key("GOOGLE_OAUTH_CLIENT_SECRET"),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    # 60s safety margin so we never send a token that expires mid-request
    _access_token_cache[refresh_token] = (data["access_token"], time.time() + data.get("expires_in", 3600) - 60)
    return data["access_token"]


def _headers(refresh_token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {_access_token(refresh_token)}",
        "developer-token": get_key("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "Content-Type": "application/json",
    }
    # Only needed when the authorized user reaches the account through an MCC
    # (manager) account - harmless to omit for directly-owned accounts
    login_customer_id = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id.replace("-", "")
    return headers


def _ads_error_message(response: httpx.Response) -> str:
    """Google Ads REST errors carry the useful message nested in JSON - surface
    it instead of a bare status code."""
    try:
        err = response.json().get("error", {})
        details = err.get("details", [{}])[0].get("errors", [{}])[0].get("message", "")
        return f"{response.status_code} {err.get('status', '')}: {details or err.get('message', '')}"
    except Exception:
        return f"{response.status_code}: {response.text[:300]}"


def list_accessible_customers(refresh_token: str) -> list:
    """Customer IDs (as plain digit strings) the authorizing user can access."""
    _count_operation()
    response = httpx.get(
        f"{ADS_BASE_URL}/customers:listAccessibleCustomers",
        headers=_headers(refresh_token),
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"listAccessibleCustomers failed - {_ads_error_message(response)}")
    resource_names = response.json().get("resourceNames", [])
    return [name.split("/")[-1] for name in resource_names]


def search(refresh_token: str, customer_id: str, gaql: str) -> list:
    """Run a GAQL query, return the raw result rows. Single page (10k rows) —
    plenty for per-client campaign reporting."""
    _count_operation()
    response = httpx.post(
        f"{ADS_BASE_URL}/customers/{customer_id}/googleAds:search",
        headers=_headers(refresh_token),
        json={"query": gaql},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"GAQL search failed - {_ads_error_message(response)}")
    return response.json().get("results", [])


def set_campaign_status(refresh_token: str, customer_id: str, campaign_id: str, status: str) -> dict:
    """status must be 'PAUSED' or 'ENABLED'."""
    if status not in ("PAUSED", "ENABLED"):
        raise ValueError(f"Invalid campaign status: {status}")
    _count_operation()
    response = httpx.post(
        f"{ADS_BASE_URL}/customers/{customer_id}/campaigns:mutate",
        headers=_headers(refresh_token),
        json={
            "operations": [{
                "update": {
                    "resourceName": f"customers/{customer_id}/campaigns/{campaign_id}",
                    "status": status,
                },
                "updateMask": "status",
            }]
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"campaign mutate failed - {_ads_error_message(response)}")
    return response.json()
