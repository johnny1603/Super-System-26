"""Low-level Google Tag Manager API plumbing: OAuth2 consent + container/
workspace/tag/trigger/version HTTP calls. Business logic lives in
agents/website_agent.py's conversion-tracking functions — this module only
talks HTTP, same split as google_ads_service/meta_service.

Auth model (2026-11-10, ONE consent): every Google connect — this one,
Google Ads, YouTube — requests the same six scopes on one screen
(core/google_scopes.GOOGLE_SCOPES, exactly the Cloud Console list; Google's
verification flagged the old per-product lists as a mismatch). Tag Manager
keeps its own callback and its own client_accounts row:
platform='google_tagmanager', account_id=container API path
('accounts/X/containers/Y' — every API call needs the path, so the path IS
the id; the human-facing GTM-XXXXXX public id goes in the activity log),
access_token=refresh token. A grant made before 2026-11-10 may lack these
scopes: website_agent.gtm_missing_scopes catches it before any write, and the
chat offers ONE "one more permission" card (connect_tag_manager) that re-runs
this consent.

SCOPE/VERIFICATION REALITY (checked 2026-07-23): the scopes below are
Google "SENSITIVE" scopes (NOT "restricted" — no third-party security
assessment needed, unlike e.g. Gmail scopes). Consequence: they must be
added to the OAuth consent screen in Google Cloud Console and the app's
verification must be RE-SUBMITTED with a justification for them. Until that
review passes, consent shows the "unverified app" warning and only test
users (up to 100) can connect — fine for development, a real timeline gate
before client-facing rollout. Same class of process the adwords scope went
through, not a new tier of pain.

Each scope, by the API method that requires it (Tag Manager API v2
reference, checked 2026-10-17) — docs/GOOGLE-SCOPES.md has the full map:
tagmanager.edit.containers — accounts.list, containers.list, containers.get,
  workspaces.list, workspaces.getStatus, versions.live (read) and
  workspaces.create / workspaces.delete / workspaces.sync /
  workspaces.triggers.list+create+update / workspaces.tags.list+create+update /
  workspaces.variables.list+create+update (the store purchase path) /
  triggers.delete / tags.delete (write; every object is looked up by name and
  reused or updated before anything is created; the deletes only remove what
  this flow itself just created, after a failed run — or, on the client's
  "undo my tracking" card, entities named "uallak — ..." and nothing else).
  Also versions.get (read: the rollback point). docs/GTM-SAFETY.md.
tagmanager.edit.containerversions — workspaces.create_version. Missing
  until 2026-10-17, so every publish_workspace call would have failed with
  403; grants made before then lack it and must reconnect
  (website_agent.gtm_missing_scopes).
tagmanager.publish — versions.publish: create_version alone does NOT make
  config live, and without it every "configured" conversion trigger would
  sit in a draft forever.

VERIFICATION STATUS: written against Google's Tag Manager API v2 reference,
never run with a live grant — same accepted MVP state as every other
service module before its first real key.
"""
import os
import re
from urllib.parse import urlencode

import httpx

from agents.keys_agent import get_key
# Deliberate reuse: token exchange/refresh are scope-agnostic OAuth calls -
# one implementation, not a second copy (they only need our client id/secret
# plus the code/refresh token). The exchange also needs THIS consent's
# redirect_uri, though — see exchange_code below.
from core.google_ads_service import OAUTH_CONSENT_URL, _access_token  # noqa: F401
from core.google_ads_service import exchange_code as _google_exchange_code

GTM_BASE_URL = "https://tagmanager.googleapis.com/tagmanager/v2"
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")
REDIRECT_PATH = "/api/oauth/gtm/callback"
# What Tag Manager NEEDS — a slice of THE one list (core/google_scopes; the
# consent requests all six on one screen, 2026-11-10)
from core import google_scopes  # noqa: E402
GTM_SCOPES = google_scopes.GTM_SCOPES
TIMEOUT = 30

# Which scope each Tag Manager write needs (Tag Manager API v2 reference):
# freezing a workspace into a version needs edit.containerversions, making a
# version live needs publish, everything else (reads, workspaces, triggers,
# tags, variables, deletes) edit.containers.
SCOPE_FOR_ACTION = {":create_version": google_scopes.TAGMANAGER_EDIT_CONTAINERVERSIONS,
                    ":publish": google_scopes.TAGMANAGER_PUBLISH}


def required_scope(path: str) -> str:
    """The scope a Tag Manager call on `path` needs."""
    for suffix, scope in SCOPE_FOR_ACTION.items():
        if str(path).endswith(suffix):
            return scope
    return google_scopes.TAGMANAGER_EDIT_CONTAINERS


def missing_scopes(granted) -> list:
    """GTM_SCOPES not present in a grant. `granted` is the token response's
    space-separated "scope" string or a list; None/"" = unknown -> all of
    them (a grant we never recorded is treated as not proven)."""
    have = set(granted.split() if isinstance(granted, str) else (granted or []))
    return [s for s in GTM_SCOPES if s not in have]


def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def exchange_code(code: str) -> dict:
    # Must send THIS consent's callback — Google rejects a mismatch (see
    # google_ads_service.exchange_code)
    return _google_exchange_code(code, redirect_uri())


def build_consent_url(state: str) -> str:
    # THE six scopes, one screen (core/google_scopes) — never GTM_SCOPES alone
    return google_scopes.consent_url(redirect_uri(), state)


API_NAME = "tagmanager.googleapis.com"


class GtmApiError(RuntimeError):
    """A non-200 from the Tag Manager API, with what a caller needs to decide
    what it means: the HTTP status and Google's own reason code (e.g.
    SERVICE_DISABLED = the Tag Manager API is not enabled in the OAuth
    client's Cloud project; ACCESS_TOKEN_SCOPE_INSUFFICIENT; PERMISSION_DENIED).
    The message keeps the old "GTM GET <path> failed: <status> ..." shape."""

    def __init__(self, method: str, path: str, response: httpx.Response):
        self.status = response.status_code
        self.api = API_NAME
        self.google_reason = _google_reason(response)
        super().__init__(f"GTM {method} {path} failed: {self.status} {response.text[:300]}")


def _google_reason(response: httpx.Response) -> str:
    """Google's machine-readable reason from an error body ("" if none)."""
    try:
        error = response.json().get("error") or {}
    except Exception:
        return ""
    for detail in error.get("details") or []:
        if detail.get("reason"):
            return str(detail["reason"])
    for item in error.get("errors") or []:
        if item.get("reason"):
            return str(item["reason"])
    return str(error.get("status") or "")


def _get(refresh_token: str, path: str, params: dict = None) -> dict:
    response = httpx.get(f"{GTM_BASE_URL}/{path.lstrip('/')}",
                         headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                         params=params or {}, timeout=TIMEOUT)
    if response.status_code != 200:
        raise GtmApiError("GET", path, response)
    return response.json()


def _post(refresh_token: str, path: str, json_body: dict = None, params: dict = None) -> dict:
    response = httpx.post(f"{GTM_BASE_URL}/{path.lstrip('/')}",
                          headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                          json=json_body or {}, params=params or {}, timeout=TIMEOUT)
    if response.status_code != 200:
        raise GtmApiError("POST", path, response)
    return response.json()


def _delete(refresh_token: str, path: str):
    response = httpx.delete(f"{GTM_BASE_URL}/{path.lstrip('/')}",
                            headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                            timeout=TIMEOUT)
    if response.status_code not in (200, 204):
        raise GtmApiError("DELETE", path, response)


class GtmCompilerError(RuntimeError):
    """create_version / publish answered compilerError: GTM could not build
    the container (e.g. a broken tag someone else left in it). Nothing went
    live."""


class GtmWorkspaceBusy(RuntimeError):
    """No workspace of our own could be created (free GTM containers allow 3)
    and the default one holds someone's unpublished changes — versioning it
    would publish their drafts too, so we stop."""


# Every trigger / tag / variable this system writes carries OUR_PREFIX — and
# NOTHING without it is ever created, changed, deleted or published by us
# (docs/GTM-SAFETY.md). ensure_entity refuses a body without it, and
# publish_workspace refuses a workspace holding any change to another name.
from core.gtm_plan import OUR_PREFIX, is_ours  # noqa: E402,F401


class GtmForeignEntity(RuntimeError):
    """A write or publish would touch an entity NOT named "uallak — ..." —
    someone else's tag, trigger or variable. Nothing is written or published."""


# ─── Discovery ────────────────────────────────────────────────────────────────

def list_accounts(refresh_token: str) -> list:
    return _get(refresh_token, "accounts").get("account", [])


def list_containers(refresh_token: str, account_path: str) -> list:
    """Containers under one account. Each carries `path` (the API id used
    everywhere) and `publicId` (the GTM-XXXXXX the site snippet shows)."""
    return _get(refresh_token, f"{account_path}/containers").get("container", [])


def get_container(refresh_token: str, container_path: str) -> dict:
    """One container (name, publicId, ...) — also the cheapest live proof that
    the stored grant still works for THIS container (tagmanager.edit.containers)."""
    return _get(refresh_token, container_path)


def find_container_by_public_id(refresh_token: str, public_id: str) -> dict:
    """The container matching a GTM-XXXXXX public id across every account the
    grant can see — how the callback links the consent to the container that
    is actually installed on the client's site."""
    for account in list_accounts(refresh_token):
        for container in list_containers(refresh_token, account["path"]):
            if container.get("publicId") == public_id:
                return container
    return {}


def list_workspaces(refresh_token: str, container_path: str) -> list:
    return _get(refresh_token, f"{container_path}/workspaces").get("workspace", [])


def default_workspace_path(refresh_token: str, container_path: str) -> str:
    """GTM edits happen in a workspace. Every container has at least one
    ('Default Workspace') — first one wins, matching first-asset MVP style."""
    workspaces = _get(refresh_token, f"{container_path}/workspaces").get("workspace", [])
    if not workspaces:
        raise RuntimeError(f"container {container_path} has no workspaces")
    return workspaces[0]["path"]


def workspace_has_changes(refresh_token: str, workspace_path: str) -> bool:
    """workspaces.getStatus: any unpublished change or merge conflict."""
    status = _get(refresh_token, f"{workspace_path}/status")
    return bool(status.get("workspaceChange") or status.get("mergeConflict"))


WORKSPACE_NAME = "uallak lead tracking"
# The revert's own workspace (undo: deletes only "uallak — ..." entities)
REMOVAL_WORKSPACE_NAME = "uallak tracking removal"
_WORKSPACE_DESCRIPTION = {
    WORKSPACE_NAME: "Created by uallak to add lead tracking; removed by Tag Manager when the version is created.",
    REMOVAL_WORKSPACE_NAME: "Created by uallak to remove its own tracking (entities named 'uallak — ...' only); "
                            "removed by Tag Manager when the version is created.",
}


def lead_workspace(refresh_token: str, container_path: str, name: str = WORKSPACE_NAME) -> tuple:
    """(workspace_path, ours). A NEW workspace is based on the latest
    container version, so the version made from it carries only our trigger
    and tag — never drafts someone left in the default workspace — and GTM
    deletes it when the version is created. If one can't be created (the
    workspace limit), the default workspace is used only when it holds no
    unpublished change; otherwise GtmWorkspaceBusy. Auth/permission errors
    (401/403) are re-raised: the default workspace would fail the same way.

    Our own workspace left over from an earlier run (it carries our name) is
    REUSED, never duplicated: synced to the live version first, and replaced
    by a fresh one only when the sync reports a conflict."""
    workspaces = _get(refresh_token, f"{container_path}/workspaces").get("workspace", [])
    mine = next((w for w in workspaces if (w.get("name") or "") == name and w.get("path")), {})
    if mine:
        try:
            synced = _post(refresh_token, f"{mine['path']}:sync")
            if not synced.get("mergeConflict") and not (synced.get("syncStatus") or {}).get("mergeConflict") \
                    and not (synced.get("syncStatus") or {}).get("syncError"):
                return mine["path"], True
        except GtmApiError as e:
            if e.status in (401, 403):
                raise
        try:
            delete_workspace(refresh_token, mine["path"])
        except GtmApiError as e:
            if e.status in (401, 403):
                raise
    try:
        created = _post(refresh_token, f"{container_path}/workspaces",
                        json_body={"name": name,
                                   "description": _WORKSPACE_DESCRIPTION.get(name, "Created by uallak.")})
        if created.get("path"):
            return created["path"], True
    except GtmApiError as e:
        if e.status in (401, 403):
            raise
    path = default_workspace_path(refresh_token, container_path)
    if workspace_has_changes(refresh_token, path):
        raise GtmWorkspaceBusy(f"{path} has unpublished changes and no new workspace could be created")
    return path, False


def delete_workspace(refresh_token: str, workspace_path: str):
    _delete(refresh_token, workspace_path)


def delete_entity(refresh_token: str, entity_path: str):
    """A trigger or tag we created in a shared workspace (cleanup after a
    failed run, so no half-done draft stays behind)."""
    _delete(refresh_token, entity_path)


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
    except GtmApiError as e:
        if e.status == 404:
            return {}
        raise


# ─── Write (conversion trigger + tag + publish) ──────────────────────────────

# GTM refuses a second entity with the same name in a workspace ("Found entity
# with duplicate name"), and a workspace is based on the LIVE version — so a
# trigger or tag that an earlier run published (or left in a draft) is already
# there. Every object this setup writes is therefore looked up by NAME first
# and reused (or updated to our spec), never created twice — the store purchase
# path's Custom JavaScript variables too (ensure_entity("variables")).
TRIGGER_NAME = "uallak — form submission"
TAG_NAME = "uallak — GA4 generate_lead"
_LIST_KEY = {"triggers": "trigger", "tags": "tag", "variables": "variable"}


def _put(refresh_token: str, path: str, json_body: dict = None, params: dict = None) -> dict:
    response = httpx.put(f"{GTM_BASE_URL}/{path.lstrip('/')}",
                         headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                         json=json_body or {}, params=params or {}, timeout=TIMEOUT)
    if response.status_code != 200:
        raise GtmApiError("PUT", path, response)
    return response.json()


def find_named(items: list, name: str) -> dict:
    """The entity called exactly `name` ({} if none)."""
    return next((i for i in items or [] if (i.get("name") or "") == name), {})


def list_entities(refresh_token: str, workspace_path: str, kind: str) -> list:
    return _get(refresh_token, f"{workspace_path}/{kind}").get(_LIST_KEY[kind], [])


def _is_duplicate_name(error: Exception) -> bool:
    return isinstance(error, GtmApiError) and error.status == 400 and "duplicate name" in str(error).lower()


def ensure_entity(refresh_token: str, workspace_path: str, kind: str, body: dict, matches) -> tuple:
    """(entity, outcome) for a trigger / tag / variable named body['name']:
    'reused' when one with that name already matches our spec, 'updated' when
    one exists but differs (rewritten to our spec, with its fingerprint so a
    concurrent edit is never clobbered), 'created' otherwise. A create that
    still meets a duplicate name (someone added it in between) re-reads and
    takes that path instead of failing. A body not named "uallak — ..." is
    refused before any call (GtmForeignEntity): found by exact name, so the
    entity it would update carries our prefix too."""
    if not is_ours(body.get("name")):
        raise GtmForeignEntity(f"refusing to write {kind} {body.get('name')!r}: not a uallak entity")
    existing = find_named(list_entities(refresh_token, workspace_path, kind), body["name"])
    if not existing:
        try:
            return _post(refresh_token, f"{workspace_path}/{kind}", json_body=body), "created"
        except GtmApiError as e:
            if not _is_duplicate_name(e):
                raise
            existing = find_named(list_entities(refresh_token, workspace_path, kind), body["name"])
            if not existing:
                raise
    if matches(existing):
        return existing, "reused"
    params = {"fingerprint": existing["fingerprint"]} if existing.get("fingerprint") else None
    return _put(refresh_token, existing["path"], json_body=body, params=params), "updated"


def form_submit_trigger_body(name: str = TRIGGER_NAME) -> dict:
    """GTM's built-in Form Submission trigger, all forms. Known real-world
    limit (documented, not hidden): it catches standard HTML form submits;
    heavily-AJAXed form builders that never fire a submit event need a
    custom-event trigger instead — that's the v2 follow-up, not v1."""
    return {"name": name, "type": "formSubmission",
            "waitForTags": {"type": "boolean", "value": "false"},
            "checkValidation": {"type": "boolean", "value": "false"}}


def ga4_lead_tag_body(ga4_measurement_id: str, trigger_id: str, name: str = TAG_NAME) -> dict:
    """GA4 event tag (type gaawe) firing 'generate_lead' on the given
    trigger. generate_lead is GA4's own recommended-event name for this —
    Google Ads can then import it as a conversion action directly.
    measurementIdOverride keeps the tag self-contained (no dependency on a
    separate Google tag existing in the container)."""
    return {"name": name, "type": "gaawe",
            "parameter": [
                {"type": "template", "key": "measurementIdOverride", "value": ga4_measurement_id},
                {"type": "template", "key": "eventName", "value": "generate_lead"},
            ],
            "firingTriggerId": [str(trigger_id)]}


def _form_trigger_matches(trigger: dict) -> bool:
    return (trigger.get("type") == "formSubmission" and not trigger.get("filter")
            and not trigger.get("autoEventFilter"))


def _ga4_event_tag_matcher(ga4_measurement_id: str, trigger_id, event_name: str):
    """Reused only if it already fires on THIS trigger with this measurement
    id and event, and is not paused."""
    def matches(tag):
        params = {p.get("key"): p.get("value") for p in (tag.get("parameter") or [])}
        return (tag.get("type") == "gaawe" and not tag.get("paused")
                and params.get("measurementIdOverride") == ga4_measurement_id
                and params.get("eventName") == event_name
                and str(trigger_id) in [str(t) for t in (tag.get("firingTriggerId") or [])])
    return matches


def ensure_form_submit_trigger(refresh_token: str, workspace_path: str) -> tuple:
    """(trigger, outcome) — our form-submission trigger, reused if present."""
    return ensure_entity(refresh_token, workspace_path, "triggers", form_submit_trigger_body(),
                         _form_trigger_matches)


def ensure_ga4_lead_event_tag(refresh_token: str, workspace_path: str,
                              ga4_measurement_id: str, trigger_id: str) -> tuple:
    """(tag, outcome) — our GA4 generate_lead tag, reused only if it already
    fires on THIS trigger with this measurement id and is not paused;
    otherwise rewritten so the GA4 tag is attached to the trigger."""
    return ensure_entity(refresh_token, workspace_path, "tags",
                         ga4_lead_tag_body(ga4_measurement_id, trigger_id),
                         _ga4_event_tag_matcher(ga4_measurement_id, trigger_id, "generate_lead"))


# ─── Click conversions (2026-11-02, core/conversion_types) ───────────────────
# A WhatsApp or phone click is measured with GTM's Just Links trigger
# (type linkClick) filtered on the built-in {{Click URL}} variable, plus a GA4
# event tag. Same scope as everything above (tagmanager.edit.containers covers
# workspaces.built_in_variables.list/create). Event names match the landing
# pages' own dataLayer events (docs/LANDING_PAGES.md §3), so a client's reports
# show ONE name per kind of click wherever it happened.
CLICK_CONVERSIONS = {
    "whatsapp_click": {"trigger": "uallak — WhatsApp click", "tag": "uallak — GA4 click_whatsapp",
                       "event": "click_whatsapp",
                       # wa.me, api.whatsapp.com (with or without www) and the whatsapp:// app link
                       "pattern": r"^(https?://(www\.)?(wa\.me|api\.whatsapp\.com)/|whatsapp:)"},
    "phone_click": {"trigger": "uallak — phone click", "tag": "uallak — GA4 click_to_call",
                    "event": "click_to_call", "pattern": r"^tel:"},
}
CLICK_EVENT_NAMES = {spec["event"]: kind for kind, spec in CLICK_CONVERSIONS.items()}


def ensure_click_url_variable(refresh_token: str, workspace_path: str) -> str:
    """The built-in Click URL variable must be enabled for a link filter to
    compile. 'reused' when it already is, 'created' otherwise."""
    enabled = _get(refresh_token, f"{workspace_path}/built_in_variables").get("builtInVariable") or []
    if any(v.get("type") == "clickUrl" for v in enabled):
        return "reused"
    try:
        _post(refresh_token, f"{workspace_path}/built_in_variables", params={"type": "clickUrl"})
    except GtmApiError as e:
        # Enabled in between by someone else: already there is what we need
        if e.status == 400 and "already" in str(e).lower():
            return "reused"
        raise
    return "created"


def click_trigger_body(kind: str) -> dict:
    spec = CLICK_CONVERSIONS[kind]
    return {"name": spec["trigger"], "type": "linkClick",
            "filter": [{"type": "matchRegex", "parameter": [
                {"type": "template", "key": "arg0", "value": "{{Click URL}}"},
                {"type": "template", "key": "arg1", "value": spec["pattern"]},
                {"type": "boolean", "key": "ignore_case", "value": "true"}]}],
            "waitForTags": {"type": "boolean", "value": "false"},
            "checkValidation": {"type": "boolean", "value": "false"}}


def _click_trigger_matches(kind: str):
    pattern = CLICK_CONVERSIONS[kind]["pattern"]

    def matches(trigger):
        if trigger.get("type") != "linkClick":
            return False
        for f in trigger.get("filter") or []:
            params = {p.get("key"): p.get("value") for p in (f.get("parameter") or [])}
            if (f.get("type") == "matchRegex" and params.get("arg0") == "{{Click URL}}"
                    and params.get("arg1") == pattern):
                return True
        return False
    return matches


def ensure_click_trigger(refresh_token: str, workspace_path: str, kind: str) -> tuple:
    """(trigger, outcome) — our WhatsApp / phone click trigger, by name."""
    return ensure_entity(refresh_token, workspace_path, "triggers", click_trigger_body(kind),
                         _click_trigger_matches(kind))


def ensure_ga4_event_tag(refresh_token: str, workspace_path: str, ga4_measurement_id: str,
                         trigger_id: str, event_name: str, name: str) -> tuple:
    """(tag, outcome) — a GA4 event tag on one trigger, by name (the click
    counterpart of ensure_ga4_lead_event_tag)."""
    return ensure_entity(refresh_token, workspace_path, "tags",
                         ga4_event_tag_body(ga4_measurement_id, trigger_id, event_name, name),
                         _ga4_event_tag_matcher(ga4_measurement_id, trigger_id, event_name))


def ga4_event_tag_body(ga4_measurement_id: str, trigger_id, event_name: str, name: str) -> dict:
    body = ga4_lead_tag_body(ga4_measurement_id, trigger_id, name=name)
    body["parameter"] = [p if p["key"] != "eventName" else {**p, "value": event_name}
                         for p in body["parameter"]]
    return body


# ─── Store purchase (2026-11-07, conversion type store_sales) ────────────────
# WHERE an order came from: ONE GA4 'purchase' event when a buyer reaches the
# WooCommerce order-received ("thank you") page. Same scopes as above —
# variables are workspaces.variables.list/create/update under
# tagmanager.edit.containers; nothing new is requested.
#
# - The order id comes from the page address WooCommerce itself builds:
#   /<endpoint>/<id>/ (pretty permalinks) or ?<endpoint>=<id> (plain ones).
#   The endpoint slug is the store's own setting (default "order-received").
# - Total and currency: the dataLayer's ecommerce object when the store pushes
#   one for THIS order (GA4 or the older Enhanced Ecommerce shape), else the
#   total WooCommerce prints on that page. Missing → sent without; never guessed.
# - ONCE PER ORDER: "first seen" remembers the last 50 order ids in the
#   buyer's browser (localStorage) and caches its answer for the page load, so
#   a refresh, a back-button or a reopened tab never fires a second purchase.
#   transaction_id also lets GA4 drop a duplicate on its side.
# - Nothing is installed on the site itself: these are Tag Manager objects in
#   the client's own container, published only after the client's approval.
# - The SALE itself is never counted from this event: revenue and orders come
#   from the store (woocommerce_service.SALE_STATUSES). A thank-you page also
#   shows for an order still awaiting payment, so this event is attribution.
PURCHASE = {"trigger": "uallak — purchase (order received)", "tag": "uallak — GA4 purchase",
            "event": "purchase",
            "vars": {"order_id": "uallak — order id", "total": "uallak — order total",
                     "currency": "uallak — order currency", "first_seen": "uallak — order first seen"}}
PURCHASE_EVENT = PURCHASE["event"]
SEEN_STORAGE_KEY = "uallak_orders_seen"
SEEN_KEEP = 50
# The slug rule lives with the conversion types (no HTTP deps): only
# [a-z0-9-] ever reaches the JavaScript below — store data is data
from core.conversion_types import DEFAULT_ORDER_RECEIVED_ENDPOINT, clean_endpoint  # noqa: E402


def _js_order_id(slug: str) -> str:
    return ("function(){var m=(document.location.pathname+document.location.search)"
            ".match(/(?:\\/|[?&])" + clean_endpoint(slug) + "(?:\\/|=)(\\d+)/);"
            "return m?m[1]:undefined;}")


def _js_ecommerce() -> str:
    # The newest dataLayer ecommerce object for THIS order (or one carrying no id)
    v = PURCHASE["vars"]
    return ("var id={{" + v["order_id"] + "}};var dl=window.dataLayer||[];var e=null;"
            "for(var i=dl.length-1;i>=0&&!e;i--){var x=dl[i]&&dl[i].ecommerce;if(!x)continue;"
            "var tid=x.transaction_id||(x.purchase&&x.purchase.actionField&&x.purchase.actionField.id);"
            "if(!tid||String(tid)===String(id))e=x;}")


def _js_total() -> str:
    return ("function(){" + _js_ecommerce() +
            "if(e){var v=e.value!=null?e.value:(e.purchase&&e.purchase.actionField?e.purchase.actionField.revenue:null);"
            "if(v!=null&&!isNaN(parseFloat(v)))return parseFloat(v);}"
            "var el=document.querySelector('.woocommerce-order-overview__total .amount');"
            "if(!el)return undefined;var t=(el.textContent||'').replace(/[^\\d.,]/g,'');"
            "var c=t.lastIndexOf(','),d=t.lastIndexOf('.');"
            "if(c>d&&t.length-c-1<=2){t=t.replace(/\\./g,'').replace(',','.');}else{t=t.replace(/,/g,'');}"
            "var n=parseFloat(t);return isNaN(n)?undefined:n;}")


def _js_currency() -> str:
    return ("function(){" + _js_ecommerce() +
            "if(e&&(e.currency||e.currencyCode))return String(e.currency||e.currencyCode).toUpperCase();"
            "var el=document.querySelector('.woocommerce-order-overview__total .woocommerce-Price-currencySymbol');"
            "var s=el?(el.textContent||'').trim():'';"
            "var map={'\\u20aa':'ILS','$':'USD','\\u20ac':'EUR','\\u00a3':'GBP'};"
            "return map[s]||undefined;}")


def _js_first_seen() -> str:
    v = PURCHASE["vars"]
    return ("function(){var id={{" + v["order_id"] + "}};if(!id)return 'no';"
            "var w=window.__uallakOrders=window.__uallakOrders||{};if(w[id])return w[id];"
            "var seen=[];try{seen=JSON.parse(window.localStorage.getItem('" + SEEN_STORAGE_KEY + "')||'[]')||[];}"
            "catch(x){seen=[];}var r=seen.indexOf(id)>-1?'no':'yes';"
            "if(r==='yes'){seen.push(id);if(seen.length>" + str(SEEN_KEEP) + ")seen=seen.slice(-" + str(SEEN_KEEP) + ");"
            "try{window.localStorage.setItem('" + SEEN_STORAGE_KEY + "',JSON.stringify(seen));}catch(x){}}"
            "w[id]=r;return r;}")


def purchase_variable_bodies(endpoint: str = DEFAULT_ORDER_RECEIVED_ENDPOINT) -> list:
    """The four Custom JavaScript variables, in creation order (first_seen
    reads order_id, so order_id goes first)."""
    v = PURCHASE["vars"]
    code = [(v["order_id"], _js_order_id(endpoint)), (v["total"], _js_total()),
            (v["currency"], _js_currency()), (v["first_seen"], _js_first_seen())]
    return [{"name": name, "type": "jsm", "parameter": [{"type": "template", "key": "javascript", "value": js}]}
            for name, js in code]


def _variable_matches(body: dict):
    def matches(var):
        have = {p.get("key"): p.get("value") for p in (var.get("parameter") or [])}
        return var.get("type") == "jsm" and have.get("javascript") == body["parameter"][0]["value"]
    return matches


def ensure_purchase_variables(refresh_token: str, workspace_path: str,
                              endpoint: str = DEFAULT_ORDER_RECEIVED_ENDPOINT) -> dict:
    """{name: (variable, outcome)} — each found by name, reused or updated."""
    return {body["name"]: ensure_entity(refresh_token, workspace_path, "variables", body, _variable_matches(body))
            for body in purchase_variable_bodies(endpoint)}


def purchase_trigger_body() -> dict:
    """DOM Ready (the page's total is printed by then), only when the order
    on this page was never seen in this browser."""
    return {"name": PURCHASE["trigger"], "type": "domReady",
            "filter": [{"type": "equals", "parameter": [
                {"type": "template", "key": "arg0", "value": "{{" + PURCHASE["vars"]["first_seen"] + "}}"},
                {"type": "template", "key": "arg1", "value": "yes"}]}]}


def _purchase_trigger_matches(trigger: dict) -> bool:
    if trigger.get("type") != "domReady":
        return False
    want = purchase_trigger_body()["filter"][0]
    for f in trigger.get("filter") or []:
        if f.get("type") == want["type"] and \
                {p.get("key"): p.get("value") for p in (f.get("parameter") or [])} == \
                {p["key"]: p["value"] for p in want["parameter"]}:
            return True
    return False


def ensure_purchase_trigger(refresh_token: str, workspace_path: str) -> tuple:
    return ensure_entity(refresh_token, workspace_path, "triggers", purchase_trigger_body(),
                         _purchase_trigger_matches)


def purchase_tag_body(ga4_measurement_id: str, trigger_id: str) -> dict:
    v = PURCHASE["vars"]
    body = ga4_lead_tag_body(ga4_measurement_id, trigger_id, name=PURCHASE["tag"])
    body["parameter"] = [p if p["key"] != "eventName" else {**p, "value": PURCHASE_EVENT}
                         for p in body["parameter"]]
    body["parameter"].append({"type": "list", "key": "eventSettingsTable", "list": [
        {"type": "map", "map": [{"type": "template", "key": "parameter", "value": key},
                                {"type": "template", "key": "parameterValue", "value": "{{" + var + "}}"}]}
        for key, var in (("transaction_id", v["order_id"]), ("value", v["total"]), ("currency", v["currency"]))]})
    return body


def _event_settings(tag: dict) -> dict:
    for p in tag.get("parameter") or []:
        if p.get("key") == "eventSettingsTable":
            out = {}
            for row in p.get("list") or []:
                m = {x.get("key"): x.get("value") for x in (row.get("map") or [])}
                out[m.get("parameter")] = m.get("parameterValue")
            return out
    return {}


def _purchase_tag_matcher(ga4_measurement_id: str, trigger_id):
    want = _event_settings(purchase_tag_body(ga4_measurement_id, trigger_id))
    base = _ga4_event_tag_matcher(ga4_measurement_id, trigger_id, PURCHASE_EVENT)
    return lambda tag: base(tag) and _event_settings(tag) == want


def ensure_purchase_tag(refresh_token: str, workspace_path: str, ga4_measurement_id: str,
                        trigger_id: str) -> tuple:
    return ensure_entity(refresh_token, workspace_path, "tags",
                         purchase_tag_body(ga4_measurement_id, trigger_id),
                         _purchase_tag_matcher(ga4_measurement_id, trigger_id))


def purchase_live(version: dict) -> dict:
    """Verification against a PUBLISHED version: our trigger (by name, with
    its once-per-order filter), the four variables, and an unpaused GA4
    purchase tag firing on that trigger with transaction_id/value/currency.
    {"live": bool, "missing": [...]}."""
    missing = []
    triggers = [t for t in version.get("trigger") or [] if t.get("name") == PURCHASE["trigger"]]
    trigger = next((t for t in triggers if _purchase_trigger_matches(t)), None)
    if not trigger:
        missing.append("trigger")
    names = {v.get("name") for v in version.get("variable") or [] if v.get("type") == "jsm"}
    missing += [f"variable:{n}" for n in PURCHASE["vars"].values() if n not in names]
    tag_ok = False
    for tag in version.get("tag") or []:
        params = {p.get("key"): p.get("value") for p in (tag.get("parameter") or [])}
        if (tag.get("type") == "gaawe" and not tag.get("paused") and params.get("eventName") == PURCHASE_EVENT
                and trigger and str(trigger.get("triggerId")) in [str(x) for x in tag.get("firingTriggerId") or []]
                and {"transaction_id", "value", "currency"} <= set(_event_settings(tag))):
            tag_ok = True
    if not tag_ok:
        missing.append("tag")
    return {"live": not missing, "missing": missing}


# ─── Our own purchase record (2026-11-07, core/store_purchases) ──────────────
# A Custom HTML tag on the SAME purchase trigger (once per order): it sends
# the order number, total and currency — the three values the GA4 tag sends —
# to OUR endpoint, so אורי can answer "how many orders / how much" with no
# store login and no Analytics permission. Same Tag Manager scopes as above;
# nothing new is requested. Nothing about the buyer is read or sent.
# text/plain via sendBeacon: no preflight, never delays the thank-you page.
PURCHASE_BEACON = {"tag": "uallak — purchase record", "path": "/api/store/purchase/"}


def purchase_beacon_html(endpoint_url: str) -> str:
    url = str(endpoint_url or "")
    if not re.fullmatch(r"https://[A-Za-z0-9.\-]+(:\d+)?/api/store/purchase/[A-Za-z0-9_=\-]+\.[0-9a-f]{64}", url):
        raise ValueError("the purchase record address must be our https endpoint with its token")
    v = PURCHASE["vars"]
    return ("<script>(function(){try{var o={{" + v["order_id"] + "}};if(!o)return;"
            "var d=JSON.stringify({o:String(o),v:{{" + v["total"] + "}},c:{{" + v["currency"] + "}}});"
            "var u='" + url + "';"
            "if(navigator.sendBeacon&&navigator.sendBeacon(u,d))return;"
            "var x=new XMLHttpRequest();x.open('POST',u,true);x.setRequestHeader('Content-Type','text/plain');"
            "x.send(d);}catch(e){}})();</script>")


def purchase_beacon_tag_body(endpoint_url: str, trigger_id: str) -> dict:
    return {"name": PURCHASE_BEACON["tag"], "type": "html",
            "parameter": [{"type": "template", "key": "html", "value": purchase_beacon_html(endpoint_url)},
                          {"type": "boolean", "key": "supportDocumentWrite", "value": "false"}],
            "firingTriggerId": [str(trigger_id)], "tagFiringOption": "oncePerEvent"}


def _beacon_html(tag: dict) -> str:
    return next((p.get("value") or "" for p in tag.get("parameter") or [] if p.get("key") == "html"), "")


def _beacon_tag_matcher(endpoint_url: str, trigger_id):
    want = purchase_beacon_html(endpoint_url)

    def matches(tag):
        return (tag.get("type") == "html" and not tag.get("paused") and _beacon_html(tag) == want
                and str(trigger_id) in [str(t) for t in (tag.get("firingTriggerId") or [])])
    return matches


def ensure_purchase_beacon_tag(refresh_token: str, workspace_path: str, endpoint_url: str,
                               trigger_id: str) -> tuple:
    return ensure_entity(refresh_token, workspace_path, "tags",
                         purchase_beacon_tag_body(endpoint_url, trigger_id),
                         _beacon_tag_matcher(endpoint_url, trigger_id))


def purchase_beacon_live(version: dict) -> dict:
    """The record tag in a PUBLISHED version: unpaused Custom HTML, our name,
    posting to our endpoint, firing on the once-per-order purchase trigger."""
    trigger = next((t for t in version.get("trigger") or []
                    if t.get("name") == PURCHASE["trigger"] and _purchase_trigger_matches(t)), None)
    for tag in version.get("tag") or []:
        if (tag.get("name") == PURCHASE_BEACON["tag"] and tag.get("type") == "html" and not tag.get("paused")
                and PURCHASE_BEACON["path"] in _beacon_html(tag) and trigger
                and str(trigger.get("triggerId")) in [str(x) for x in tag.get("firingTriggerId") or []]):
            return {"live": True, "missing": []}
    return {"live": False, "missing": ["record_tag" if trigger else "trigger"]}


VERSION_NAME = "uallak conversion tracking"
REVERT_VERSION_NAME = "uallak tracking removed"
# Entity kinds a workspace change can carry (workspaces.getStatus → Entity)
_CHANGE_KEYS = ("tag", "trigger", "variable", "folder", "client", "zone", "customTemplate",
                "transformation", "gtagConfig")


def foreign_changes(changes) -> list:
    """The workspace changes that touch anything NOT ours, as short labels
    ("tag: Facebook pixel"). The one allowed exception is the built-in
    Click URL variable our click triggers need (a GTM switch, not a named
    entity). An unreadable change counts as foreign — never assumed safe."""
    out = []
    for change in changes or []:
        if not isinstance(change, dict):
            out.append("unreadable change")
            continue
        if "builtInVariable" in change:
            builtin = change.get("builtInVariable") or {}
            kind = builtin.get("type") if isinstance(builtin, dict) else ""
            if kind != "clickUrl":
                out.append(f"built-in variable: {kind or '?'}")
            continue
        key = next((k for k in _CHANGE_KEYS if isinstance(change.get(k), dict)), "")
        if not key:
            out.append("unreadable change")
            continue
        name = change[key].get("name")
        if not is_ours(name):
            out.append(f"{key}: {name or '(no name)'}")
    return out


def workspace_foreign_changes(refresh_token: str, workspace_path: str) -> list:
    """foreign_changes of what this workspace would publish (getStatus, read-only)."""
    status = _get(refresh_token, f"{workspace_path}/status")
    return foreign_changes(status.get("workspaceChange") or [])


def publish_workspace(refresh_token: str, workspace_path: str,
                      version_name: str = VERSION_NAME, notes: str = "") -> dict:
    """Workspace → version → LIVE. Two calls because that's GTM's model:
    create_version freezes the workspace into a version, publish makes it
    the live one. Returns the publish response (contains containerVersion).

    Guard (docs/GTM-SAFETY.md): the workspace's own change list is read first
    and ANY change to an entity not named "uallak — ..." stops the publish
    (GtmForeignEntity) — whatever path led here, someone else's tag is never
    frozen into a version by us."""
    foreign = workspace_foreign_changes(refresh_token, workspace_path)
    if foreign:
        raise GtmForeignEntity(f"{workspace_path} holds changes that are not ours: {foreign[:5]}")
    created = _post(refresh_token, f"{workspace_path}:create_version",
                    json_body={"name": version_name,
                               "notes": notes or "uallak: conversion triggers + GA4 event tags "
                                                 "(form / WhatsApp / phone / store purchase)"})
    if created.get("compilerError"):
        raise GtmCompilerError("create_version: Tag Manager could not compile the workspace")
    version_path = (created.get("containerVersion") or {}).get("path")
    if not version_path:
        raise RuntimeError(f"create_version returned no version path: {str(created)[:300]}")
    published = _post(refresh_token, f"{version_path}:publish")
    if published.get("compilerError"):
        raise GtmCompilerError(f"publish: Tag Manager could not compile {version_path}")
    return published


# ─── Dry run + rollback (2026-11-11, docs/GTM-SAFETY.md) ─────────────────────
# Same scopes as above: versions.get / versions.live are reads under
# tagmanager.edit.containers, versions.publish is tagmanager.publish (the
# ":publish" suffix in SCOPE_FOR_ACTION). Nothing new is requested.

def plan_entities(live: dict, pending: list, ga4_measurement_id: str,
                  endpoint_slug: str = DEFAULT_ORDER_RECEIVED_ENDPOINT, record_url: str = "") -> list:
    """READ-ONLY: what configure_lead_conversion would do to each entity,
    computed against the LIVE version with the SAME body builders and the
    SAME "is it already ours and right" checks the write path uses (a new
    workspace is based on the live version). [{kind, name, action}] with
    action create / update / reuse; the built-in Click URL switch, when a
    click type is pending, as kind built_in_variable (enable / reuse)."""
    triggers, tags, variables = live.get("trigger") or [], live.get("tag") or [], live.get("variable") or []
    out = []

    def step(kind, items, body, matches):
        existing = find_named(items, body["name"])
        out.append({"kind": kind, "name": body["name"],
                    "action": "create" if not existing else ("reuse" if matches(existing) else "update")})
        return existing

    if any(k in CLICK_CONVERSIONS for k in pending):
        enabled = any(v.get("type") == "clickUrl" for v in live.get("builtInVariable") or [])
        out.append({"kind": "built_in_variable", "name": "Click URL", "action": "reuse" if enabled else "enable"})
    for kind in pending:
        if kind == "store_sales":
            for body in purchase_variable_bodies(endpoint_slug):
                step("variable", variables, body, _variable_matches(body))
            trigger = step("trigger", triggers, purchase_trigger_body(), _purchase_trigger_matches)
            tid = (trigger or {}).get("triggerId") or "(new)"
            step("tag", tags, purchase_tag_body(ga4_measurement_id, tid),
                 _purchase_tag_matcher(ga4_measurement_id, tid))
            step("tag", tags, purchase_beacon_tag_body(record_url, tid), _beacon_tag_matcher(record_url, tid))
        elif kind == "form_submit":
            trigger = step("trigger", triggers, form_submit_trigger_body(), _form_trigger_matches)
            tid = (trigger or {}).get("triggerId") or "(new)"
            step("tag", tags, ga4_lead_tag_body(ga4_measurement_id, tid),
                 _ga4_event_tag_matcher(ga4_measurement_id, tid, "generate_lead"))
        elif kind in CLICK_CONVERSIONS:
            spec = CLICK_CONVERSIONS[kind]
            trigger = step("trigger", triggers, click_trigger_body(kind), _click_trigger_matches(kind))
            tid = (trigger or {}).get("triggerId") or "(new)"
            step("tag", tags, ga4_event_tag_body(ga4_measurement_id, tid, spec["event"], spec["tag"]),
                 _ga4_event_tag_matcher(ga4_measurement_id, tid, spec["event"]))
    return out


def get_version(refresh_token: str, container_path: str, version_id: str) -> dict:
    """One saved container version (its tags / triggers / variables)."""
    return _get(refresh_token, f"{container_path}/versions/{version_id}")


def publish_version(refresh_token: str, container_path: str, version_id: str) -> dict:
    """Make an EXISTING version live again (the rollback: the version saved
    before our publish). Nothing is edited — GTM just switches what is live."""
    if not re.fullmatch(r"\d+", str(version_id or "")):
        raise ValueError("a version id is digits")
    published = _post(refresh_token, f"{container_path}/versions/{version_id}:publish")
    if published.get("compilerError"):
        raise GtmCompilerError(f"publish: Tag Manager could not compile version {version_id}")
    return published


def ours_in(version: dict) -> list:
    """[{kind, name}] of every "uallak — ..." trigger / tag / variable in a version or workspace."""
    return [{"kind": kind, "name": e.get("name")} for kind in ("tag", "trigger", "variable")
            for e in version.get(kind) or [] if is_ours(e.get("name"))]


def references_to_ours(version: dict) -> list:
    """Entities NOT ours that depend on ours (a tag firing or blocked on our
    trigger, anything reading a {{uallak — ...}} variable). Removing ours
    would break them, so the removal refuses instead."""
    import json
    our_triggers = {str(t.get("triggerId")) for t in version.get("trigger") or [] if is_ours(t.get("name"))}
    out = []
    for kind in ("tag", "trigger", "variable"):
        for entity in version.get(kind) or []:
            if is_ours(entity.get("name")):
                continue
            fired = {str(x) for x in (entity.get("firingTriggerId") or []) + (entity.get("blockingTriggerId") or [])}
            if (kind == "tag" and fired & our_triggers) or \
                    ("{{" + OUR_PREFIX) in json.dumps(entity, ensure_ascii=False):
                out.append(f"{kind}: {entity.get('name') or '(no name)'}")
    return out


# Deletion order: tags first (they point at triggers), then triggers, then
# variables — the purchase variables newest-first (first_seen reads order_id)
_REMOVE_ORDER = {"tags": 0, "triggers": 1, "variables": 2}
_VAR_ORDER = {name: i for i, name in enumerate(reversed(list(PURCHASE["vars"].values())))}


def remove_ours(refresh_token: str, workspace_path: str) -> list:
    """Delete, in THIS workspace, every entity named "uallak — ..." and
    nothing else. Each name is re-checked right before its delete. Returns
    [{kind, name}] removed."""
    removed = []
    for kind in sorted(_REMOVE_ORDER, key=_REMOVE_ORDER.get):
        items = [e for e in list_entities(refresh_token, workspace_path, kind) if is_ours(e.get("name"))]
        if kind == "variables":
            items.sort(key=lambda e: _VAR_ORDER.get(e.get("name"), -1))
        for entity in items:
            if not is_ours(entity.get("name")) or not entity.get("path"):
                continue
            delete_entity(refresh_token, entity["path"])
            removed.append({"kind": _LIST_KEY[kind], "name": entity.get("name")})
    return removed
