"""Low-level Google Tag Manager API plumbing: OAuth2 consent + container/
workspace/tag/trigger/version HTTP calls. Business logic lives in
agents/website_agent.py's conversion-tracking functions — this module only
talks HTTP, same split as google_ads_service/meta_service.

Auth model — a SEPARATE consent from Google Ads, on purpose:
- Same OAuth client (GOOGLE_OAUTH_CLIENT_ID/SECRET), different scopes, its
  own client_accounts row: platform='google_tagmanager',
  account_id=container API path ('accounts/X/containers/Y' — every API call
  needs the path, so the path IS the id; the human-facing GTM-XXXXXX public
  id goes in the activity log), access_token=refresh token.
- Separate because: (a) a scope is baked into the grant at consent time, so
  appending GTM scopes to the Ads consent would force every EXISTING Ads
  client to reconnect for no benefit; (b) only clients whose SITE we manage
  need this at all — bundling would put a scarier consent screen in front
  of every ads-only client.

SCOPE/VERIFICATION REALITY (checked 2026-07-23): both scopes below are
Google "SENSITIVE" scopes (NOT "restricted" — no third-party security
assessment needed, unlike e.g. Gmail scopes). Consequence: they must be
added to the OAuth consent screen in Google Cloud Console and the app's
verification must be RE-SUBMITTED with a justification for them. Until that
review passes, consent shows the "unverified app" warning and only test
users (up to 100) can connect — fine for development, a real timeline gate
before client-facing rollout. Same class of process the adwords scope went
through, not a new tier of pain.

tagmanager.edit.containers — read+write workspaces/tags/triggers.
tagmanager.publish — create_version alone does NOT make config live;
publishing a container version is its own scope, and without it every
"configured" conversion trigger would sit in a draft forever.

VERIFICATION STATUS: written against Google's Tag Manager API v2 reference,
never run with a live grant — same accepted MVP state as every other
service module before its first real key.
"""
import os
from urllib.parse import urlencode

import httpx

from agents.keys_agent import get_key
# Deliberate reuse: token exchange/refresh are scope-agnostic OAuth calls -
# one implementation, not a second copy (they only need our client id/secret
# plus the code/refresh token).
from core.google_ads_service import OAUTH_CONSENT_URL, _access_token, exchange_code  # noqa: F401

GTM_BASE_URL = "https://tagmanager.googleapis.com/tagmanager/v2"
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://uallak.com")
REDIRECT_PATH = "/api/oauth/gtm/callback"
GTM_SCOPES = [
    "https://www.googleapis.com/auth/tagmanager.edit.containers",
    "https://www.googleapis.com/auth/tagmanager.publish",
]
TIMEOUT = 30


def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def build_consent_url(state: str) -> str:
    params = {
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(GTM_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH_CONSENT_URL}?{urlencode(params)}"


def _get(refresh_token: str, path: str, params: dict = None) -> dict:
    response = httpx.get(f"{GTM_BASE_URL}/{path.lstrip('/')}",
                         headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                         params=params or {}, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"GTM GET {path} failed: {response.status_code} {response.text[:300]}")
    return response.json()


def _post(refresh_token: str, path: str, json_body: dict = None, params: dict = None) -> dict:
    response = httpx.post(f"{GTM_BASE_URL}/{path.lstrip('/')}",
                          headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                          json=json_body or {}, params=params or {}, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"GTM POST {path} failed: {response.status_code} {response.text[:300]}")
    return response.json()


# ─── Discovery ────────────────────────────────────────────────────────────────

def list_accounts(refresh_token: str) -> list:
    return _get(refresh_token, "accounts").get("account", [])


def list_containers(refresh_token: str, account_path: str) -> list:
    """Containers under one account. Each carries `path` (the API id used
    everywhere) and `publicId` (the GTM-XXXXXX the site snippet shows)."""
    return _get(refresh_token, f"{account_path}/containers").get("container", [])


def find_container_by_public_id(refresh_token: str, public_id: str) -> dict:
    """The container matching a GTM-XXXXXX public id across every account the
    grant can see — how the callback links the consent to the container that
    is actually installed on the client's site."""
    for account in list_accounts(refresh_token):
        for container in list_containers(refresh_token, account["path"]):
            if container.get("publicId") == public_id:
                return container
    return {}


def default_workspace_path(refresh_token: str, container_path: str) -> str:
    """GTM edits happen in a workspace. Every container has at least one
    ('Default Workspace') — first one wins, matching first-asset MVP style."""
    workspaces = _get(refresh_token, f"{container_path}/workspaces").get("workspace", [])
    if not workspaces:
        raise RuntimeError(f"container {container_path} has no workspaces")
    return workspaces[0]["path"]


# ─── Read (verification) ─────────────────────────────────────────────────────

def list_workspace_tags(refresh_token: str, workspace_path: str) -> list:
    return _get(refresh_token, f"{workspace_path}/tags").get("tag", [])


def list_workspace_triggers(refresh_token: str, workspace_path: str) -> list:
    return _get(refresh_token, f"{workspace_path}/triggers").get("trigger", [])


def get_live_version(refresh_token: str, container_path: str) -> dict:
    """The PUBLISHED container version — what actually runs on the site.
    Workspace contents are drafts; only this is real. 404/empty means nothing
    was ever published (a fresh container)."""
    try:
        return _get(refresh_token, f"{container_path}/versions:live")
    except RuntimeError as e:
        if "404" in str(e):
            return {}
        raise


# ─── Write (conversion trigger + tag + publish) ──────────────────────────────

def create_form_submit_trigger(refresh_token: str, workspace_path: str,
                               name: str = "uallak — form submission") -> dict:
    """GTM's built-in Form Submission trigger, all forms. Known real-world
    limit (documented, not hidden): it catches standard HTML form submits;
    heavily-AJAXed form builders that never fire a submit event need a
    custom-event trigger instead — that's the v2 follow-up, not v1."""
    return _post(refresh_token, f"{workspace_path}/triggers", json_body={
        "name": name,
        "type": "formSubmission",
        "waitForTags": {"type": "boolean", "value": "false"},
        "checkValidation": {"type": "boolean", "value": "false"},
    })


def create_ga4_lead_event_tag(refresh_token: str, workspace_path: str,
                              ga4_measurement_id: str, trigger_id: str,
                              name: str = "uallak — GA4 generate_lead") -> dict:
    """GA4 event tag (type gaawe) firing 'generate_lead' on the given
    trigger. generate_lead is GA4's own recommended-event name for this —
    Google Ads can then import it as a conversion action directly.
    measurementIdOverride keeps the tag self-contained (no dependency on a
    separate Google tag existing in the container)."""
    return _post(refresh_token, f"{workspace_path}/tags", json_body={
        "name": name,
        "type": "gaawe",
        "parameter": [
            {"type": "template", "key": "measurementIdOverride", "value": ga4_measurement_id},
            {"type": "template", "key": "eventName", "value": "generate_lead"},
        ],
        "firingTriggerId": [trigger_id],
    })


def publish_workspace(refresh_token: str, workspace_path: str,
                      version_name: str = "uallak conversion tracking") -> dict:
    """Workspace → version → LIVE. Two calls because that's GTM's model:
    create_version freezes the workspace into a version, publish makes it
    the live one. Returns the publish response (contains containerVersion)."""
    created = _post(refresh_token, f"{workspace_path}:create_version",
                    json_body={"name": version_name})
    version_path = (created.get("containerVersion") or {}).get("path")
    if not version_path:
        raise RuntimeError(f"create_version returned no version path: {str(created)[:300]}")
    return _post(refresh_token, f"{version_path}:publish")
