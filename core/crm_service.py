"""Low-level HTTP for external CRMs the CLIENT owns. Business logic lives in
agents/crm_agent.py — this module only talks HTTP, same split as
meta/tiktok/youtube/wordpress services.

## Why these two vendors, and not the obvious third (investigated 2026-08-03)

The brief suggested HubSpot and Zoho. Only one of those actually fits a
"paste your API key" card:

- **HubSpot** — legacy API keys were SUNSET in November 2022. The modern
  equivalent is a **Private App access token**: static, never expires, sent as
  `Authorization: Bearer`. (HubSpot also shipped a "Service Key" for data-only
  system-to-system access in 2026; it is presented the same way, so a client
  pasting either one works here.)
- **Pipedrive** — a real static **API token**, sent as `x-api-token`, against
  the company's OWN subdomain (`https://{company}.pipedrive.com`). That domain
  is why this vendor needs a second field; see `extra_field` below.
- **Zoho — deliberately NOT supported.** It is OAuth 2.0 only; lifelong auth
  tokens were retired for security. Supporting it means app registration, a
  consent + callback flow, per-datacenter API domains (.com/.eu/.in/.com.au)
  and hourly access-token refresh — the same shape as our Google/Meta/TikTok
  integrations, not a paste-a-key card. It is a real future handoff, not a
  line item. See .claude/skills/crm/SKILL.md.

## Adding a vendor

One entry in `VENDORS`, with a `verify` and a `push` callable. Nothing outside
this module knows any vendor's name, endpoints or field names — crm_agent, the
API layer and the dashboard all drive off this registry. That is the whole
extensibility story; keep it that way.

## What is deliberately NOT here

No DB access, no Supabase, no decisions about WHEN to push. This module is
given a credential and a lead dict and returns what happened.
"""
import httpx

TIMEOUT = 20

HUBSPOT_BASE = "https://api.hubapi.com"
PIPEDRIVE_BASE = "https://{domain}.pipedrive.com/api/v2"


class CRMError(Exception):
    """A CRM call failed. `permanent` separates "this will never work" (bad
    credential, rejected field) from "try again later" (timeout, 5xx, rate
    limit) — the retry pass uses it to avoid hammering a credential that a
    client has revoked."""

    def __init__(self, message, vendor="", status=None, permanent=False):
        super().__init__(message)
        self.vendor = vendor
        self.status = status
        self.permanent = permanent


def _is_permanent(status) -> bool:
    """4xx means we sent something wrong or the credential is dead — retrying
    an identical request cannot fix either. 429 is the exception: it is a 4xx
    that explicitly means "later"."""
    return bool(status and 400 <= status < 500 and status != 429)


def _request(method: str, url: str, vendor: str, **kwargs) -> dict:
    try:
        response = httpx.request(method, url, timeout=TIMEOUT, **kwargs)
    except Exception as e:
        # Network/timeout: transient by definition, so retryable.
        raise CRMError(f"{vendor} unreachable: {e}", vendor=vendor) from e
    if response.status_code >= 400:
        raise CRMError(f"{vendor} {method} failed: {response.status_code} "
                       f"{response.text[:300]}",
                       vendor=vendor, status=response.status_code,
                       permanent=_is_permanent(response.status_code))
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


# ─── HubSpot ──────────────────────────────────────────────────────────────────

def _hubspot_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _hubspot_verify(token: str, extra: str = "") -> dict:
    """Cheapest call that proves the token works AND carries contact scope: a
    1-row contact list. A token without `crm.objects.contacts` returns 403 here,
    which is exactly what we want to tell the client at connect time rather than
    discovering on their first real lead."""
    _request("GET", f"{HUBSPOT_BASE}/crm/v3/objects/contacts",
             "hubspot", headers=_hubspot_headers(token), params={"limit": 1})
    return {"ok": True, "account_label": "HubSpot"}


def _hubspot_push(token: str, extra: str, lead: dict) -> dict:
    """Create a CONTACT. HubSpot dedupes on email: a second lead from the same
    address returns 409, which we report as an honest 'already there' rather
    than an error — the client's CRM is in the state they want either way."""
    properties = {}
    if lead.get("email"):
        properties["email"] = lead["email"]
    if lead.get("phone"):
        properties["phone"] = lead["phone"]
    first, last = _split_name(lead.get("name"))
    if first:
        properties["firstname"] = first
    if last:
        properties["lastname"] = last
    if lead.get("message"):
        # A standard HubSpot contact property, so this needs no custom-field setup
        properties["message"] = lead["message"][:60000]
    if not properties:
        raise CRMError("nothing to send — lead has no name, email or phone",
                       vendor="hubspot", permanent=True)

    try:
        created = _request("POST", f"{HUBSPOT_BASE}/crm/v3/objects/contacts",
                           "hubspot", headers=_hubspot_headers(token),
                           json={"properties": properties})
    except CRMError as e:
        if e.status == 409:
            return {"external_id": "", "note": "contact already exists in HubSpot"}
        raise
    return {"external_id": str(created.get("id") or ""), "note": ""}


# ─── Pipedrive ────────────────────────────────────────────────────────────────

def _pipedrive_headers(token: str) -> dict:
    return {"x-api-token": token, "Content-Type": "application/json"}


def _pipedrive_base(domain: str) -> str:
    domain = (domain or "").strip().lower()
    # Accept what a client is likely to paste: bare name, full host, or a URL.
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.split("/")[0]
    if domain.endswith(".pipedrive.com"):
        domain = domain[: -len(".pipedrive.com")]
    if not domain or not all(ch.isalnum() or ch == "-" for ch in domain):
        raise CRMError("invalid Pipedrive company domain", vendor="pipedrive",
                       permanent=True)
    return PIPEDRIVE_BASE.format(domain=domain)


def _pipedrive_verify(token: str, extra: str = "") -> dict:
    base = _pipedrive_base(extra)
    _request("GET", f"{base}/persons", "pipedrive",
             headers=_pipedrive_headers(token), params={"limit": 1})
    return {"ok": True, "account_label": f"Pipedrive ({_pipedrive_base(extra).split('//')[1].split('.')[0]})"}


def _pipedrive_push(token: str, extra: str, lead: dict) -> dict:
    """Person FIRST, then a Lead linked to it.

    Two calls on purpose: a Person alone lands in their People list, which is
    not where anyone looks for a new enquiry. The Lead is what shows up in the
    Leads Inbox. If the second call fails the Person still exists, so the
    contact is never lost — the error says which half completed.
    """
    base = _pipedrive_base(extra)
    name = (lead.get("name") or "").strip() or lead.get("email") or lead.get("phone")
    if not name:
        raise CRMError("nothing to send — lead has no name, email or phone",
                       vendor="pipedrive", permanent=True)

    person_body = {"name": name[:255]}
    if lead.get("email"):
        person_body["emails"] = [{"value": lead["email"], "primary": True}]
    if lead.get("phone"):
        person_body["phones"] = [{"value": lead["phone"], "primary": True}]
    person = _request("POST", f"{base}/persons", "pipedrive",
                      headers=_pipedrive_headers(token), json=person_body)
    person_id = ((person.get("data") or {}).get("id")) or person.get("id")
    if not person_id:
        raise CRMError("Pipedrive created no person id", vendor="pipedrive")

    try:
        lead_body = {"title": f"{name[:200]} — uallak", "person_id": person_id}
        created = _request("POST", f"{base}/leads", "pipedrive",
                           headers=_pipedrive_headers(token), json=lead_body)
    except CRMError as e:
        # Half-done is a real outcome and is reported as one, never swallowed:
        # the contact IS in their CRM, the inbox item is not.
        raise CRMError(f"person {person_id} created but lead was not: {e}",
                       vendor="pipedrive", status=e.status,
                       permanent=e.permanent) from e

    created_id = ((created.get("data") or {}).get("id")) or created.get("id")
    return {"external_id": str(created_id or person_id), "note": ""}


# ─── Registry (the only place a vendor is named) ──────────────────────────────

def _split_name(full_name: str) -> tuple:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


VENDORS = {
    "hubspot": {
        "label": "HubSpot",
        "label_he": "HubSpot",
        "credential_label_he": "Private App Access Token",
        # Shown on the connection card so a client knows where to click. Kept
        # here rather than in the dashboard so the UI never hardcodes a vendor.
        "help_he": ("ב-HubSpot: Settings ← Integrations ← Private Apps ← צור אפליקציה "
                    "עם הרשאת crm.objects.contacts.write והעתק את ה-Access Token. "
                    "מפתחות API הישנים של HubSpot בוטלו ואינם עובדים יותר."),
        "extra_field": "",
        "creates": "contact",
        "verify": _hubspot_verify,
        "push": _hubspot_push,
    },
    "pipedrive": {
        "label": "Pipedrive",
        "label_he": "Pipedrive",
        "credential_label_he": "API Token",
        "help_he": ("ב-Pipedrive: שם החשבון (למעלה מימין) ← Company settings ← "
                    "Personal preferences ← API. בנוסף צריך את שם הדומיין שלכם — "
                    "החלק שלפני pipedrive.com בכתובת."),
        # The company subdomain. A vendor with no extra field leaves this "".
        "extra_field": "domain",
        "extra_label_he": "דומיין החברה ב-Pipedrive",
        "creates": "person + lead",
        "verify": _pipedrive_verify,
        "push": _pipedrive_push,
    },
}


def supported_vendors() -> list:
    """The registry as plain data for the API/dashboard — never the callables."""
    return [{
        "key": key,
        "label": vendor["label"],
        "label_he": vendor["label_he"],
        "credential_label_he": vendor["credential_label_he"],
        "help_he": vendor["help_he"],
        "extra_field": vendor["extra_field"],
        "extra_label_he": vendor.get("extra_label_he", ""),
        "creates": vendor["creates"],
    } for key, vendor in VENDORS.items()]


def verify_credentials(vendor: str, credential: str, extra: str = "") -> dict:
    """Prove the credential works BEFORE it is stored. Raises CRMError."""
    entry = VENDORS.get(vendor)
    if not entry:
        raise CRMError(f"unsupported CRM: {vendor}", vendor=vendor, permanent=True)
    if not (credential or "").strip():
        raise CRMError("missing credential", vendor=vendor, permanent=True)
    if entry["extra_field"] and not (extra or "").strip():
        raise CRMError(f"missing {entry['extra_field']}", vendor=vendor, permanent=True)
    return entry["verify"](credential.strip(), (extra or "").strip())


def push_lead(vendor: str, credential: str, extra: str, lead: dict) -> dict:
    """Create the lead in the client's CRM. Returns {"external_id", "note"}.
    Raises CRMError — the CALLER decides what a failure means."""
    entry = VENDORS.get(vendor)
    if not entry:
        raise CRMError(f"unsupported CRM: {vendor}", vendor=vendor, permanent=True)
    return entry["push"](credential.strip(), (extra or "").strip(), lead)
