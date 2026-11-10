"""THE Google OAuth scope list of round 1 — the single source of truth
(2026-11-10, docs/GOOGLE-SCOPES.md §One consent).

GOOGLE_SCOPES is EXACTLY the list on the OAuth consent screen in Cloud
Console (project plasma-canyon-229222): the code must request what the
Console declares, nothing more, nothing less. Every client-facing Google
connect (Google Ads, YouTube, Tag Manager) asks for all six on ONE consent
screen via consent_url() — no incremental or partial request
(include_granted_scopes is never sent).

The per-product lists (ADS_SCOPES, YOUTUBE_SCOPES, GTM_SCOPES) are SLICES of
GOOGLE_SCOPES, never a second literal: they say which scopes a feature needs,
so a grant made before 2026-11-10 (Ads-only, YouTube-only) can be checked for
what it lacks.

NOT here, on purpose: Google sign-in (openid + userinfo.email,
core/client_auth — identity only, a separate login flow), our service
account's own drive scope (core/drive_service), and the ROUND 2 scopes
(gmail.send, webmasters.readonly, analytics.readonly, drive.file) — each in
its own module behind a flag that is OFF (core/round2_flags) and requested
only after round 1 is approved.
"""
from urllib.parse import urlencode

ADWORDS = "https://www.googleapis.com/auth/adwords"
YOUTUBE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
TAGMANAGER_EDIT_CONTAINERS = "https://www.googleapis.com/auth/tagmanager.edit.containers"
TAGMANAGER_EDIT_CONTAINERVERSIONS = "https://www.googleapis.com/auth/tagmanager.edit.containerversions"
TAGMANAGER_PUBLISH = "https://www.googleapis.com/auth/tagmanager.publish"

# Exactly the Cloud Console list, in its order. Change it ONLY together with
# the Console (and docs/GOOGLE-SCOPES.md) — tests/test_google_one_consent.py.
GOOGLE_SCOPES = [ADWORDS, YOUTUBE_READONLY, YOUTUBE_UPLOAD,
                 TAGMANAGER_EDIT_CONTAINERS, TAGMANAGER_EDIT_CONTAINERVERSIONS, TAGMANAGER_PUBLISH]

# What each feature needs (slices of the one list)
ADS_SCOPES = [ADWORDS]
YOUTUBE_SCOPES = [YOUTUBE_UPLOAD, YOUTUBE_READONLY]
GTM_SCOPES = [TAGMANAGER_EDIT_CONTAINERS, TAGMANAGER_EDIT_CONTAINERVERSIONS, TAGMANAGER_PUBLISH]
PRODUCT_SCOPES = {"google_ads": ADS_SCOPES, "youtube": YOUTUBE_SCOPES, "youtube_pending": YOUTUBE_SCOPES,
                  "google_tagmanager": GTM_SCOPES}
GOOGLE_PLATFORMS = tuple(PRODUCT_SCOPES)

CONSENT_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
TIMEOUT = 15


def consent_params(redirect_uri: str, state: str) -> dict:
    """The authorization request every Google connect sends: all six scopes,
    offline (a refresh token) and prompt=consent (Google returns a refresh
    token only on a consent screen). No prompt=select_account and no
    login_hint: the browser's own account chooser behaves as before."""
    from agents.keys_agent import get_key
    return {
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }


def consent_url(redirect_uri: str, state: str) -> str:
    return f"{CONSENT_URL}?{urlencode(consent_params(redirect_uri, state))}"


def as_list(granted) -> list:
    """A token response's space-separated `scope` string (or a list) → list."""
    if isinstance(granted, str):
        return granted.split()
    return [str(s) for s in (granted or [])]


def missing(granted, needed=None) -> list:
    """The scopes of `needed` (default: all six) that `granted` lacks."""
    have = set(as_list(granted))
    return [s for s in (GOOGLE_SCOPES if needed is None else needed) if s not in have]


def short(scope: str) -> str:
    return scope.rsplit("/", 1)[-1]


# ─── Admin diagnostic (read-only) ────────────────────────────────────────────

def token_scopes(refresh_token: str) -> list:
    """What a STORED grant actually carries, from Google: a fresh access token
    (no stored state changes) → tokeninfo. Raises on a dead grant."""
    import httpx
    from core.google_ads_service import _access_token
    response = httpx.get(TOKENINFO_URL, params={"access_token": _access_token(refresh_token)}, timeout=TIMEOUT)
    response.raise_for_status()
    return as_list(response.json().get("scope", ""))


def _rows(client_id: int) -> list:
    from agents.client_agent import _db
    return (_db().table("client_accounts").select("*").eq("client_id", int(client_id))
            .in_("platform", list(GOOGLE_PLATFORMS)).order("id", desc=True).execute().data or [])


def _recorded_gtm_scopes(client_id: int):
    from agents.client_agent import _db
    rows = (_db().table("client_activity").select("details").eq("client_id", int(client_id))
            .eq("action_type", "gtm_api_connected").order("created_at", desc=True).limit(1).execute().data or [])
    return (rows[0].get("details") or {}).get("scopes") if rows else None


def _round2() -> dict:
    try:
        from core import round2_flags as r2
        return {"GMAIL_SEND_ENABLED": r2.gmail_send_enabled(), "SEARCH_CONSOLE_ENABLED": r2.search_console_enabled(),
                "ANALYTICS_ENABLED": r2.analytics_enabled(), "DRIVE_FILE_ENABLED": r2.drive_file_enabled()}
    except Exception:
        return {}


def diagnostic(client_id: int, live: bool = True) -> dict:
    """The six scopes the code requests + for one client, per stored Google
    connection, the scopes its grant ACTUALLY has (Google's tokeninfo when
    live, else what was recorded at consent). Never returns a token."""
    from core.secrets_vault import hydrate
    out = {"code_scopes": [short(s) for s in GOOGLE_SCOPES], "client_id": int(client_id),
           "round2_flags": _round2(), "connections": []}
    try:
        rows = _rows(client_id)
    except Exception as e:
        out["error"] = f"connections unreadable ({type(e).__name__})"
        return out
    seen = set()
    for row in rows:
        platform = row.get("platform")
        if platform in seen:
            continue                      # newest row per platform
        seen.add(platform)
        item = {"platform": platform, "status": row.get("status"), "account": str(row.get("account_id") or ""),
                "needed": [short(s) for s in PRODUCT_SCOPES[platform]], "scopes": None, "source": None}
        try:
            token = (hydrate(dict(row)) or {}).get("access_token") or ""
        except Exception:
            token = ""
        if live and token:
            try:
                item["scopes"], item["source"] = token_scopes(token), "google"
            except Exception as e:
                item["error"] = "grant_dead" if "invalid_grant" in str(e) or "Revoked" in type(e).__name__ \
                    else f"tokeninfo_failed:{type(e).__name__}"
        if item["scopes"] is None and platform == "google_tagmanager":
            recorded = _recorded_gtm_scopes(client_id)
            if recorded is not None:
                item["scopes"], item["source"] = as_list(recorded), "recorded_at_consent"
        if item["scopes"] is not None:
            item["missing_for_feature"] = [short(s) for s in missing(item["scopes"], PRODUCT_SCOPES[platform])]
            item["missing_of_six"] = [short(s) for s in missing(item["scopes"])]
            item["scopes"] = [short(s) for s in item["scopes"]]
        out["connections"].append(item)
    out["has_all_six"] = any(c.get("scopes") is not None and not c.get("missing_of_six") for c in out["connections"])
    return out
