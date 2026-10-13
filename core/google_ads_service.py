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
from core.api_call_counters import increment_call_counter

# Bump in one place when Google sunsets this version. Google moved to MONTHLY
# releases in January 2026, so a major version now lives ~6 months, not ~a year
# - this needs checking a few times a year, not annually.
#   v21 sunset 2026-08-05 (this constant sat on it for 13 days, silently
#   breaking every Google Ads call - see AdsApiVersionSunsetError below, which
#   exists so that can never be a quiet failure again)
#   v22 sunsets 2026-10; v23 ~2027-02; v24 ~2027-05; v25 is current.
# Always jump to the NEWEST version, not the next one up - it resets the clock.
ADS_API_VERSION = "v25"
ADS_BASE_URL = f"https://googleads.googleapis.com/{ADS_API_VERSION}"
OAUTH_CONSENT_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")
REDIRECT_PATH = "/api/oauth/google-ads/callback"
TIMEOUT = 20

# Explorer Access allows 2,880 operations/day. Persisted in Supabase
# (core/api_call_counters.py) so the count survives Cloud Run restarts —
# still a best-effort brake (fails open on a DB hiccup), not an exact meter,
# but good enough to keep a runaway loop from burning the developer token's
# reputation during the approval period.
DAILY_OP_LIMIT = 2880

# refresh_token -> (access_token, expires_at_epoch). Access tokens live ~1h;
# in-memory cache is fine (rule 7: no local-file state, memory is allowed).
_access_token_cache = {}


def _count_operation():
    count = increment_call_counter("google_ads", window_days=1)
    if count > DAILY_OP_LIMIT:
        raise RuntimeError(
            f"Google Ads daily operation limit reached ({DAILY_OP_LIMIT}) - refusing call"
        )
    if count > DAILY_OP_LIMIT * 0.8:
        print(f"[google_ads_service] WARNING: {count}/{DAILY_OP_LIMIT} daily ops used")


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


def exchange_code(code: str, redirect_uri_override: str = None) -> dict:
    """Authorization code -> {access_token, refresh_token, expires_in, ...}.

    SHARED by every Google consent on our one OAuth client (GTM, YouTube,
    Merchant Center reuse it). Google requires the redirect_uri here to be
    IDENTICAL to the one the consent URL used — so a caller whose consent
    went to its own callback MUST pass that callback. Before 2026-10-13 this
    always sent the Google Ads callback, and every non-Ads consent failed the
    exchange with 400 redirect_uri_mismatch. Defaults to Google Ads' own."""
    response = httpx.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
            "client_secret": get_key("GOOGLE_OAUTH_CLIENT_SECRET"),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri_override or redirect_uri(),
        },
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        # Google's error body names the cause ("redirect_uri_mismatch",
        # "invalid_grant", ...) and carries no secret; raise_for_status alone
        # drops it, which is how the redirect_uri bug went undiagnosed.
        print(f"[google oauth] token exchange failed: HTTP {response.status_code} "
              f"redirect_uri={redirect_uri_override or redirect_uri()} body={response.text[:500]}")
    response.raise_for_status()
    return response.json()


def revoke_token(refresh_token: str) -> bool:
    """Revoke the client's OAuth grant at Google, so our stored refresh token
    stops working even if a copy survives somewhere. Google returns 200 on
    success and 400 for an already-invalid/revoked token — both mean the grant
    is dead, so both count as success. Never raises: disconnect flows must not
    fail because the token was already dead."""
    try:
        response = httpx.post(
            OAUTH_REVOKE_URL,
            data={"token": refresh_token},
            timeout=TIMEOUT,
        )
        _access_token_cache.pop(refresh_token, None)
        if response.status_code in (200, 400):
            return True
        print(f"[google_ads_service] token revoke returned {response.status_code}: {response.text[:200]}")
        return False
    except Exception as e:
        print(f"[google_ads_service] token revoke failed: {e}")
        return False


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


class AdsApiVersionSunsetError(RuntimeError):
    """ADS_API_VERSION points at a version Google no longer serves.

    This is a PLATFORM-WIDE outage, not one client's auth problem: every Google
    Ads call fails identically until the constant is bumped. It gets its own
    exception type because the v21 sunset (2026-08-05) was invisible for 13 days
    - each call site reported it as that client's failure, so nothing looked
    like "the integration is down". Callers should say so loudly and must never
    present it to a client as their problem.
    """


def _ads_error_message(response: httpx.Response) -> str:
    """Google Ads REST errors carry the useful message nested in JSON - surface
    it instead of a bare status code."""
    try:
        err = response.json().get("error", {})
        details = err.get("details", [{}])[0].get("errors", [{}])[0].get("message", "")
        return f"{response.status_code} {err.get('status', '')}: {details or err.get('message', '')}"
    except Exception:
        return f"{response.status_code}: {response.text[:300]}"


def _is_version_sunset(response: httpx.Response, message: str) -> bool:
    """Google sunsets a version in TWO phases and this has to catch both, since
    the first phase is what you hit on sunset day and the second is what you hit
    weeks later (both observed on v21):
      1. blocked  - HTTP 400, "Version vNN is deprecated. Requests to this
                    version will be blocked."
      2. removed  - the path itself stops existing: HTTP 404 with an EMPTY body
                    (no Ads error envelope, because nothing routed the request).
    A live version behaves differently on both counts - a genuine auth failure
    is 401 WITH the error envelope - so neither branch fires on a real
    client-side error. Both branches mean ADS_API_VERSION is what's wrong."""
    if "deprecated" in (message or "").lower():
        return True
    return response.status_code == 404 and '"error"' not in (response.text or "")


def _check(response: httpx.Response, what: str) -> dict:
    """Shared non-200 handling for every Ads call: raise the version-sunset
    error when the version is the problem, otherwise a normal RuntimeError
    carrying Google's own message. Returns the parsed body on success."""
    if response.status_code != 200:
        message = _ads_error_message(response)
        if _is_version_sunset(response, message):
            raise AdsApiVersionSunsetError(
                f"Google Ads API {ADS_API_VERSION} is no longer served by Google - "
                f"EVERY Google Ads call is failing until ADS_API_VERSION in "
                f"core/google_ads_service.py is bumped to the current version. "
                f"Google said: {message}"
            )
        raise RuntimeError(f"{what} failed - {message}")
    return response.json()


def list_accessible_customers(refresh_token: str) -> list:
    """Customer IDs (as plain digit strings) the authorizing user can access."""
    _count_operation()
    response = httpx.get(
        f"{ADS_BASE_URL}/customers:listAccessibleCustomers",
        headers=_headers(refresh_token),
        timeout=TIMEOUT,
    )
    resource_names = _check(response, "listAccessibleCustomers").get("resourceNames", [])
    return [name.split("/")[-1] for name in resource_names]


# listAccessibleCustomers returns EVERY account the login can reach — enabled,
# suspended, cancelled, never-finished setups and manager (MCC) accounts alike,
# in no meaningful order. Picking one needs each account's own facts first.
DESCRIBE_MAX_ACCOUNTS = 20
_DESCRIBE_GAQL = ("SELECT customer.id, customer.descriptive_name, customer.status, "
                  "customer.manager, customer.currency_code FROM customer")


def format_customer_id(customer_id: str) -> str:
    """1234567890 -> 123-456-7890, the way Google Ads itself shows it."""
    digits = "".join(ch for ch in str(customer_id or "") if ch.isdigit())
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else digits


def describe_customers(refresh_token: str, customer_ids: list) -> list:
    """Name, status and manager flag for each accessible account — one GAQL
    operation per account (capped at DESCRIBE_MAX_ACCOUNTS). An account Google
    refuses to query (not yet enabled, cancelled, suspended, or outside the
    login-customer-id's reach) comes back with status NOT_ENABLED /
    UNREADABLE instead of raising, so one dead account never hides the
    others. A version sunset still raises: that is an outage, not an answer."""
    accounts = []
    for customer_id in list(customer_ids)[:DESCRIBE_MAX_ACCOUNTS]:
        try:
            rows = search(refresh_token, customer_id, _DESCRIBE_GAQL)
            customer = (rows[0] if rows else {}).get("customer", {})
            accounts.append({
                "id": str(customer_id),
                "name": customer.get("descriptiveName", ""),
                "status": customer.get("status", "UNKNOWN"),
                "manager": bool(customer.get("manager")),
                "currency": customer.get("currencyCode", ""),
            })
        except AdsApiVersionSunsetError:
            raise
        except Exception as e:
            message = str(e)
            not_enabled = ("not yet enabled" in message or "deactivated" in message
                           or "CUSTOMER_NOT_ENABLED" in message)
            accounts.append({"id": str(customer_id), "name": "", "manager": False, "currency": "",
                             "status": "NOT_ENABLED" if not_enabled else "UNREADABLE",
                             "error": message[:200]})
    return accounts


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
    return _check(response, "GAQL search").get("results", [])


def atomic_mutate(refresh_token: str, customer_id: str, mutate_operations: list) -> dict:
    """googleAds:mutate — several resources created/updated in ONE atomic call.
    Operations may reference each other through temporary resource IDs (negative
    numbers, e.g. customers/{cid}/campaignBudgets/-1). All-or-nothing: a failure
    anywhere rolls back everything, so we never strand a half-built campaign."""
    _count_operation()
    response = httpx.post(
        f"{ADS_BASE_URL}/customers/{customer_id}/googleAds:mutate",
        headers=_headers(refresh_token),
        json={"mutateOperations": mutate_operations},
        timeout=TIMEOUT,
    )
    return _check(response, "atomic mutate")


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
    return _check(response, "campaign mutate")


# ─── Optimization mutates (autonomous doctrine — see the google-ads skill) ────
# Field masks: REST/JSON uses lowerCamelCase field paths ("amountMicros", not
# "amount_micros") per Google's JSON mapping rule. This is the least-verified
# detail in this module — the existing masks in this file are single words
# ("status"), identical in both casings, so they prove nothing either way.
# First live run: check the error text for FIELD_MASK errors before assuming a
# deeper problem.

def update_campaign_budget(refresh_token: str, customer_id: str,
                           budget_id: str, amount_micros: int) -> dict:
    """Change an existing campaign budget's daily amount. The DAILY figure is
    ours to move; the client's approved MONTHLY total is the ceiling the caller
    must respect (guardrail 1 — this function does not know about it)."""
    _count_operation()
    response = httpx.post(
        f"{ADS_BASE_URL}/customers/{customer_id}/campaignBudgets:mutate",
        headers=_headers(refresh_token),
        json={
            "operations": [{
                "update": {
                    "resourceName": f"customers/{customer_id}/campaignBudgets/{budget_id}",
                    "amountMicros": str(int(amount_micros)),
                },
                "updateMask": "amountMicros",
            }]
        },
        timeout=TIMEOUT,
    )
    return _check(response, "campaign budget mutate")


def set_campaign_bid_ceiling(refresh_token: str, customer_id: str,
                             campaign_id: str, ceiling_micros: int) -> dict:
    """Set the max-CPC ceiling on a Maximize Clicks campaign. This is the ONLY
    bid lever available under targetSpend — per-keyword bids do not apply to an
    automated strategy, so there is deliberately no set_keyword_bid()."""
    _count_operation()
    response = httpx.post(
        f"{ADS_BASE_URL}/customers/{customer_id}/campaigns:mutate",
        headers=_headers(refresh_token),
        json={
            "operations": [{
                "update": {
                    "resourceName": f"customers/{customer_id}/campaigns/{campaign_id}",
                    "targetSpend": {"cpcBidCeilingMicros": str(int(ceiling_micros))},
                },
                "updateMask": "targetSpend.cpcBidCeilingMicros",
            }]
        },
        timeout=TIMEOUT,
    )
    return _check(response, "campaign bid ceiling mutate")


def mutate_ad_group_criteria(refresh_token: str, customer_id: str, operations: list) -> dict:
    """Keyword-level changes: pause an underperformer (update + status mask),
    or add a winner (create). Caller builds the operations."""
    _count_operation()
    response = httpx.post(
        f"{ADS_BASE_URL}/customers/{customer_id}/adGroupCriteria:mutate",
        headers=_headers(refresh_token),
        json={"operations": operations},
        timeout=TIMEOUT,
    )
    return _check(response, "ad group criteria mutate")


def mutate_ad_group_ads(refresh_token: str, customer_id: str, operations: list) -> dict:
    """Ad-level changes. NOTE: a Responsive Search Ad's headlines/descriptions
    are effectively IMMUTABLE — iterating copy means create-a-new-ad (+ pause or
    remove the old one), never an in-place update of the text."""
    _count_operation()
    response = httpx.post(
        f"{ADS_BASE_URL}/customers/{customer_id}/adGroupAds:mutate",
        headers=_headers(refresh_token),
        json={"operations": operations},
        timeout=TIMEOUT,
    )
    return _check(response, "ad group ads mutate")
