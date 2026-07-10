"""Low-level Meta (Facebook + Instagram) plumbing: OAuth2 flow + Graph API HTTP.

Auth model (mirrors the Google Ads integration — see .claude/skills/meta/SKILL.md):
- Each client authorizes THEIR OWN Meta assets via OAuth ("Connect Now" in the
  dashboard). ONE consent covers both APIs we use:
  * Marketing API (paid campaigns)      — needs the ad account + a long-lived USER token
  * Pages/Instagram Graph API (organic) — needs the Page + a PAGE token
- Tokens live per-client in client_accounts, one row per asset:
  platform='meta_ads'       account_id=ad account id ('act_...'), access_token=long-lived
                            user token (~60 days; the ads health scan auto-refreshes it)
  platform='meta_page'      account_id=Page id, access_token=Page token (does not expire
                            when derived from a long-lived user token)
  platform='meta_instagram' account_id=IG business account id, access_token=same Page token
- META_APP_ID / META_APP_SECRET are ours, one pair for the whole system — the
  equivalent of GOOGLE_OAUTH_CLIENT_ID/SECRET.
- Access-tier reality: until the app passes App Review + Business Verification
  (Full Access), everything here only works on assets where WE are admins —
  uallak's own Page/ad account. The code path is identical either way; client
  accounts start working the moment Full Access is granted, no rework.

Business logic lives in agents/meta_ads_agent.py and agents/meta_content_agent.py —
this module only talks HTTP. Agents pass RELATIVE Graph paths (no host/version)
to graph_get/graph_post/graph_delete so a version bump stays a one-line change here.
"""
import json
import os
import time
from urllib.parse import urlencode

import httpx

from agents.keys_agent import get_key

# Bump in one place when Meta sunsets this version (they release ~3/year,
# each lives ~2 years)
GRAPH_API_VERSION = "v23.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
OAUTH_DIALOG_URL = f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://uallak.com")
REDIRECT_PATH = "/api/oauth/meta/callback"
TIMEOUT = 30

# One consent asks for everything both agents need — splitting into two consents
# would double the connect friction for zero security gain (same app, same user).
OAUTH_SCOPES = [
    # Marketing API (meta_ads_agent)
    "ads_management",
    "ads_read",
    "business_management",
    # Pages (meta_content_agent)
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "pages_manage_engagement",
    "pages_read_user_content",
    "pages_messaging",
    "read_insights",
    # Instagram (meta_content_agent)
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_comments",
    "instagram_manage_insights",
]


class MetaGraphError(RuntimeError):
    """Graph API error with Meta's error code attached, so callers can tell an
    expired token (client must reconnect) from a plain bad request."""

    def __init__(self, message, code=None, subcode=None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode


# 190 = invalid/expired access token, 102 = API session issue — both mean the
# stored token is dead and the client has to go through Connect again
TOKEN_ERROR_CODES = {102, 190}


def is_token_error(exc) -> bool:
    return isinstance(exc, MetaGraphError) and exc.code in TOKEN_ERROR_CODES


# Full Access to the Marketing API requires 500+ Marketing API calls in the
# trailing 15 days just to QUALIFY to apply. This in-memory counter (resets on
# restart, best-effort like the Google op guard) makes the accumulation visible
# in the logs while we build against our own accounts.
_marketing_call_counter = {"date": "", "count": 0}


def _count_marketing_call():
    today = time.strftime("%Y-%m-%d")
    if _marketing_call_counter["date"] != today:
        _marketing_call_counter["date"] = today
        _marketing_call_counter["count"] = 0
    _marketing_call_counter["count"] += 1
    if _marketing_call_counter["count"] % 25 == 0:
        print(f"[meta_service] {_marketing_call_counter['count']} Marketing API calls today "
              "(500+/15 days needed to qualify for Full Access review)")


def _raise_graph_error(response: httpx.Response):
    """Graph errors carry the useful message in error.message — surface it with
    the numeric code instead of a bare status."""
    try:
        err = response.json().get("error", {})
    except Exception:
        raise MetaGraphError(f"{response.status_code}: {response.text[:300]}")
    subcode = err.get("error_subcode")
    raise MetaGraphError(
        f"{response.status_code} {err.get('type', '')} (code {err.get('code')}"
        f"{f'/{subcode}' if subcode else ''}): {err.get('message', '')}",
        code=err.get("code"),
        subcode=subcode,
    )


def _headers(access_token: str) -> dict:
    # Bearer header, not a query param — keeps tokens out of URLs and logs
    return {"Authorization": f"Bearer {access_token}"}


def graph_get(path: str, access_token: str, params: dict = None, marketing: bool = False):
    """GET {GRAPH_BASE_URL}/{path}. Dict/list param values are JSON-encoded the
    way the Graph API expects (targeting, filtering, time_range...)."""
    if marketing:
        _count_marketing_call()
    encoded = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
               for k, v in (params or {}).items()}
    response = httpx.get(f"{GRAPH_BASE_URL}/{path.lstrip('/')}",
                         headers=_headers(access_token), params=encoded, timeout=TIMEOUT)
    if response.status_code != 200:
        _raise_graph_error(response)
    return response.json()


def graph_post(path: str, access_token: str, data: dict = None, marketing: bool = False):
    if marketing:
        _count_marketing_call()
    encoded = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
               for k, v in (data or {}).items()}
    response = httpx.post(f"{GRAPH_BASE_URL}/{path.lstrip('/')}",
                          headers=_headers(access_token), data=encoded, timeout=TIMEOUT)
    if response.status_code != 200:
        _raise_graph_error(response)
    return response.json()


def graph_delete(path: str, access_token: str, marketing: bool = False):
    if marketing:
        _count_marketing_call()
    response = httpx.delete(f"{GRAPH_BASE_URL}/{path.lstrip('/')}",
                            headers=_headers(access_token), timeout=TIMEOUT)
    if response.status_code != 200:
        _raise_graph_error(response)
    return response.json()


# ─── OAuth flow ───────────────────────────────────────────────────────────────

def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def build_consent_url(state: str) -> str:
    params = {
        "client_id": get_key("META_APP_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": ",".join(OAUTH_SCOPES),
        "state": state,
    }
    return f"{OAUTH_DIALOG_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    """Authorization code -> {access_token, ...} (a SHORT-lived user token, ~1-2h).
    Always follow with exchange_long_lived — never store the short token."""
    response = httpx.get(
        f"{GRAPH_BASE_URL}/oauth/access_token",
        params={
            "client_id": get_key("META_APP_ID"),
            "client_secret": get_key("META_APP_SECRET"),
            "redirect_uri": redirect_uri(),
            "code": code,
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        _raise_graph_error(response)
    return response.json()


def exchange_long_lived(user_token: str) -> dict:
    """Short-lived (or aging long-lived) user token -> fresh ~60-day long-lived
    token. Also how the health scan refreshes tokens before they expire —
    re-exchanging a still-valid long-lived token returns a fresh one."""
    response = httpx.get(
        f"{GRAPH_BASE_URL}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": get_key("META_APP_ID"),
            "client_secret": get_key("META_APP_SECRET"),
            "fb_exchange_token": user_token,
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        _raise_graph_error(response)
    return response.json()


def debug_token(access_token: str) -> dict:
    """Token introspection via the app access token ('{app_id}|{app_secret}').
    Returns Meta's data dict: is_valid, expires_at (epoch seconds, 0 = never —
    Page tokens derived from a long-lived user token don't expire), scopes..."""
    app_token = f"{get_key('META_APP_ID')}|{get_key('META_APP_SECRET')}"
    response = httpx.get(
        f"{GRAPH_BASE_URL}/debug_token",
        params={"input_token": access_token, "access_token": app_token},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        _raise_graph_error(response)
    return response.json().get("data", {})


def get_ad_accounts(user_token: str) -> list:
    """Ad accounts the authorizing user can access. 'id' comes back with the
    'act_' prefix — store and use it as-is."""
    return graph_get(
        "me/adaccounts", user_token,
        params={"fields": "id,name,account_status,currency", "limit": 50},
        marketing=True,
    ).get("data", [])


def get_pages(user_token: str) -> list:
    """Facebook Pages the user manages, each with its own Page access token and
    (when linked) the Instagram business account id."""
    return graph_get(
        "me/accounts", user_token,
        params={"fields": "id,name,access_token,instagram_business_account", "limit": 50},
    ).get("data", [])


# ─── Marketing API helpers (paid campaigns) ───────────────────────────────────

def get_campaigns(user_token: str, ad_account_id: str) -> list:
    return graph_get(
        f"{ad_account_id}/campaigns", user_token,
        params={"fields": "id,name,status,effective_status", "limit": 100},
        marketing=True,
    ).get("data", [])


def get_campaign_insights(user_token: str, ad_account_id: str,
                          date_preset: str = None, time_range: dict = None,
                          time_increment: int = None) -> list:
    """Campaign-level insight rows. Pass date_preset (e.g. 'last_30d') OR
    time_range {'since','until'}; time_increment=1 adds a per-day breakdown
    (row field 'date_start'). Numeric metrics arrive as JSON strings — cast them.
    'spend' is in the ad account's own currency (ILS for our clients)."""
    params = {
        "level": "campaign",
        "fields": "campaign_id,campaign_name,impressions,clicks,spend,actions",
        "limit": 500,
    }
    if date_preset:
        params["date_preset"] = date_preset
    if time_range:
        params["time_range"] = time_range
    if time_increment:
        params["time_increment"] = time_increment
    return graph_get(f"{ad_account_id}/insights", user_token,
                     params=params, marketing=True).get("data", [])


def get_flagged_ads(user_token: str, ad_account_id: str) -> list:
    """Ads Meta has disapproved or flagged with policy issues, with their
    campaign attached for grouping."""
    return graph_get(
        f"{ad_account_id}/ads", user_token,
        params={
            "fields": "id,name,effective_status,ad_review_feedback,campaign{id,name}",
            "filtering": [{"field": "effective_status", "operator": "IN",
                           "value": ["DISAPPROVED", "WITH_ISSUES"]}],
            "limit": 100,
        },
        marketing=True,
    ).get("data", [])


def get_account_overview(user_token: str, ad_account_id: str) -> dict:
    return graph_get(f"{ad_account_id}", user_token,
                     params={"fields": "account_status,disable_reason,currency,name"},
                     marketing=True)


def set_campaign_status(user_token: str, campaign_id: str, status: str) -> dict:
    """status must be 'PAUSED' or 'ACTIVE' (Meta's ACTIVE = Google's ENABLED)."""
    if status not in ("PAUSED", "ACTIVE"):
        raise ValueError(f"Invalid campaign status: {status}")
    return graph_post(f"{campaign_id}", user_token, data={"status": status}, marketing=True)
