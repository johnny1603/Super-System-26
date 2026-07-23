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

VERIFICATION STATUS — HIGHEST RISK integration in this codebase: the
Merchant API is WEEKS old (v1beta) at the time of writing, endpoint shapes
below are best-effort from its documentation structure (modular
sub-services: accounts, datasources, reports), NOT verified against a live
call. Smoke-test every function here against a real Merchant Center account
before relying on it — more important here than the usual "docs-derived"
caveat elsewhere in this codebase.
"""
import os
from urllib.parse import urlencode

import httpx

from agents.keys_agent import get_key
# Deliberate reuse (same as gtm_service/youtube_service): token exchange/
# refresh are scope-agnostic OAuth calls — one implementation, not a third copy.
from core.google_ads_service import OAUTH_CONSENT_URL, _access_token, exchange_code  # noqa: F401

ACCOUNTS_BASE = "https://merchantapi.googleapis.com/accounts/v1beta"
DATASOURCES_BASE = "https://merchantapi.googleapis.com/datasources/v1beta"
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://uallak.com")
REDIRECT_PATH = "/api/oauth/merchant-center/callback"
MERCHANT_SCOPE = "https://www.googleapis.com/auth/content"
TIMEOUT = 30


def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def build_consent_url(state: str) -> str:
    params = {
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": MERCHANT_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH_CONSENT_URL}?{urlencode(params)}"


def _get(refresh_token: str, base: str, path: str, params: dict = None) -> dict:
    response = httpx.get(f"{base}/{path.lstrip('/')}",
                         headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                         params=params or {}, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"Merchant API GET {path} failed: {response.status_code} {response.text[:300]}")
    return response.json()


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
