"""Low-level Google Merchant Center plumbing: OAuth2 consent + account/
data-source status via the MERCHANT API (Google's Content API for Shopping
successor — Content API sunsets 2026-08-18, so this is built against the
new API from day one rather than something already dying). Business logic
lives in agents/google_ads_agent.py's Merchant Center section — this module
only talks HTTP, same split as every other *_service module.

Auth model — a SEPARATE consent from Google Ads, same reasoning as GTM/
YouTube (see gtm_service.py's docstring): a grant's scopes are fixed at
consent time, so bundling into the Ads consent would force every existing
Ads client to reconnect, and only clients who actually sell products need
this. It's also technically a DIFFERENT scope entirely (`.../auth/content`,
not `.../auth/adwords`), so it could never have been silently bundled
anyway. platform='merchant_center', account_id=Merchant Center id (numeric
string), access_token=refresh token.

ACCOUNT DISCOVERY — a deliberate design choice, not an oversight: rather
than guess at a "list accounts this OAuth grant can see" endpoint (Merchant
Center's account-discovery shape has historically been about aggregator/
sub-account RELATIONSHIPS, not a clean "accounts owned by this user" list,
and the brand-new Merchant API's exact equivalent isn't confidently known
here), the client TYPES their own numeric Merchant Center id (visible in
their own Merchant Center account settings) — same self-service-input
pattern already used for WordPress/Higgsfield. The callback then simply
VERIFIES the grant can actually read that account id before storing it,
rather than trusting an unverified discovery call.

SCOPE/VERIFICATION REALITY (checked 2026-07-23): `.../auth/content` is a
sensitive scope requiring OAuth app verification (consent-screen review +
scope justification, same class of process as the GTM/YouTube scopes added
earlier this week) before real (non-test-user) clients can connect.

VERIFICATION STATUS — HIGHEST RISK integration in this codebase: written
against v1beta when the Merchant API was weeks old; moved to v1 on
2026-10-13 (v1beta was shut down 2026-02-28) with the two paths checked
against the v1 reference, but still NOT verified against a live call. Smoke-test every function here against a real Merchant Center account
before relying on it — more important here than the usual "docs-derived"
caveat elsewhere in this codebase.
"""
import os
from urllib.parse import urlencode

import httpx

from agents.keys_agent import get_key
# Deliberate reuse (same as gtm_service/youtube_service): token exchange/
# refresh are scope-agnostic OAuth calls — one implementation, not a third copy.
from core.google_ads_service import OAUTH_CONSENT_URL, _access_token  # noqa: F401
from core.google_ads_service import exchange_code as _google_exchange_code

# v1 (GA), not v1beta: Google shut v1beta down on 2026-02-28. Path shapes are
# unchanged between the two (checked against the v1 reference 2026-10-13):
# GET accounts/v1/accounts/{id}, GET datasources/v1/accounts/{id}/dataSources.
ACCOUNTS_BASE = "https://merchantapi.googleapis.com/accounts/v1"
DATASOURCES_BASE = "https://merchantapi.googleapis.com/datasources/v1"
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")
REDIRECT_PATH = "/api/oauth/merchant-center/callback"
# THE Merchant Center scope list (one constant; docs/GOOGLE-SCOPES.md mirrors
# it). content is the Merchant API's only OAuth scope — there is no read-only one.
MERCHANT_SCOPES = ["https://www.googleapis.com/auth/content"]
TIMEOUT = 30


def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def exchange_code(code: str) -> dict:
    # Must send THIS consent's callback — Google rejects a mismatch (see
    # google_ads_service.exchange_code)
    return _google_exchange_code(code, redirect_uri())


def build_consent_url(state: str) -> str:
    params = {
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(MERCHANT_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH_CONSENT_URL}?{urlencode(params)}"


class MerchantAPIError(RuntimeError):
    """A Merchant API (or token refresh) failure, classified so callers can
    tell the client WHY in plain words instead of one generic "failed":

    - not_registered: OUR Cloud project isn't registered with the Merchant
      API (v1 blocks every call from an unregistered project with 401
      "GCP project ... is not registered with the merchant account"). A
      one-time fix on our side — see register_gcp().
    - api_disabled:   the Merchant API isn't enabled in our Cloud project.
    - consent_expired: the stored refresh token no longer works (revoked,
      expired, wrong scope) — the client must approve again.
    - no_access:      the approved Google account can't see this merchant id
      (wrong number, or a different Google login than the one with access).
    - unavailable:    timeout / 5xx / anything else — worth retrying.
    """
    def __init__(self, reason: str, status: int, message: str):
        super().__init__(f"[{reason}] {status} {message}")
        self.reason = reason
        self.status = status


def _classify(status: int, text: str) -> str:
    lowered = text.lower()
    if "not registered" in lowered:
        return "not_registered"
    if status == 403 and ("service_disabled" in lowered or "has not been used in project" in lowered
                          or "is disabled" in lowered):
        return "api_disabled"
    if status == 401:
        return "consent_expired"
    if status in (403, 404):
        return "no_access"
    return "unavailable"


def _bearer(refresh_token: str) -> dict:
    try:
        token = _access_token(refresh_token)
    except httpx.HTTPStatusError as e:
        # invalid_grant & co: the grant itself is dead, not the merchant id
        raise MerchantAPIError("consent_expired", e.response.status_code, e.response.text[:300]) from e
    except httpx.HTTPError as e:
        raise MerchantAPIError("unavailable", 0, f"token refresh: {e}") from e
    return {"Authorization": f"Bearer {token}"}


def _request(method: str, refresh_token: str, base: str, path: str,
             params: dict = None, json: dict = None) -> dict:
    headers = _bearer(refresh_token)
    try:
        response = httpx.request(method, f"{base}/{path.lstrip('/')}", headers=headers,
                                 params=params or {}, json=json, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise MerchantAPIError("unavailable", 0, f"{method} {path}: {e}") from e
    if response.status_code != 200:
        raise MerchantAPIError(_classify(response.status_code, response.text),
                               response.status_code,
                               f"Merchant API {method} {path}: {response.text[:300]}")
    return response.json()


def _get(refresh_token: str, base: str, path: str, params: dict = None) -> dict:
    return _request("GET", refresh_token, base, path, params=params)


def get_account(refresh_token: str, merchant_id: str) -> dict:
    """Verifies the grant can actually read this merchant id AND returns its
    basic info — the OAuth callback's verification step (see module
    docstring for why we don't trust an unverified discovery call)."""
    return _get(refresh_token, ACCOUNTS_BASE, f"accounts/{merchant_id}")


def list_data_sources(refresh_token: str, merchant_id: str) -> list:
    """Every product feed configured on the account (file feed, API feed,
    scheduled fetch, Google Sheets, etc.) with its input/processing state —
    THE feed-health surface: whether a feed exists at all, and whether it's
    actually processing successfully. Works regardless of WHERE the feed
    data originates (Shopify, a manually-maintained Sheet, a platform we
    don't manage) — Merchant Center account access is independent of our
    own website/WooCommerce capabilities (see the merchant-center skill for
    why this isn't blocked by that gap)."""
    return _get(refresh_token, DATASOURCES_BASE, f"accounts/{merchant_id}/dataSources").get("dataSources", [])


def register_gcp(refresh_token: str, merchant_id: str, developer_email: str = "") -> dict:
    """ONE-TIME setup, not a per-client step: links OUR Google Cloud project
    to a Merchant Center account so the Merchant API stops answering every
    call with 401 "not registered" (docs: merchant/api/guides/quickstart/
    registration, checked 2026-10-17). Rules from that page:
    - once per Cloud project; a project is registered to ONE merchant
      account at a time (a second call elsewhere -> ALREADY_REGISTERED), so
      register uallak's OWN Merchant Center account, never a client's;
    - the token's Google account must be ADMIN on that merchant account;
    - production account with a verified homepage, not a test account;
    - developer_email must be a real Google account (not a service account);
      if it isn't already a user there, the invite must be accepted (14 days)
      before registration completes;
    - calls start working ~5 minutes after.
    Whether a project registered to OUR account can then read CLIENTS'
    accounts over their OAuth grant is the documented third-party/agency
    model, but not confirmed against a live call here."""
    body = {"developerEmail": developer_email} if developer_email else {}
    return _request("POST", refresh_token, ACCOUNTS_BASE,
                    f"accounts/{merchant_id}/developerRegistration:registerGcp", json=body)
