---
name: crm
description: How uallak syncs a client's captured leads into an external CRM the CLIENT owns (HubSpot, Pipedrive) — the vendor registry, paste-a-key auth, why Zoho is excluded, credential storage, and the never-lose-a-lead failure model. Use when touching core/crm_service.py, agents/crm_agent.py, any /api/client/crm* endpoint, or the CRM card in the client dashboard's leads section.
---

# External CRM sync (client-owned, opt-in)

## Opt-in and additive — the rule that constrains everything else

A client with **no CRM connected sees zero change**. `core/client_leads.py` is
untouched except for one guarded dispatch in the capture endpoint; every entry
point in `crm_agent` returns early when there is no connection. If a change here
would alter behaviour for a client without a CRM, it is wrong.

**Our copy of the lead is the record; the CRM push is a side effect.**
`capture_lead()` stores the row and the endpoint answers the customer BEFORE any
CRM call runs (FastAPI `BackgroundTasks`). A slow, broken or revoked CRM can
never delay a form submit or lose an enquiry — same principle as
`client_leads._insert_lead` keeping the lead when attribution columns are absent.

## Which CRMs, and why not the obvious third (investigated 2026-08-03)

| Vendor | Auth | Status |
|---|---|---|
| **HubSpot** | **Private App access token** — static, never expires, `Authorization: Bearer`. Legacy API keys were **sunset Nov 2022** and do not work. (A 2026 "Service Key" for data-only access is pasted the same way and also works.) | Supported |
| **Pipedrive** | **API token**, `x-api-token` header, against the company's own subdomain `https://{company}.pipedrive.com/api/v2` | Supported — needs a **second field** (the company domain) |
| **Zoho** | **OAuth 2.0 only.** Lifelong auth tokens were retired; needs app registration, consent + callback flow, per-datacenter domains (.com/.eu/.in/.com.au), hourly token refresh | **Deliberately NOT supported** |

Zoho is the same shape as our Google/Meta/TikTok OAuth integrations, not a
paste-a-key card. It is a real future handoff, not a line item — do not add it
to `VENDORS` without building the OAuth flow first.

## The registry is the extension point

`core/crm_service.VENDORS` is the ONLY place a vendor is named. Endpoints, field
names, credential labels and the Hebrew "where do I find my key" help text all
live there; `crm_agent`, the API layer and the dashboard all drive off
`supported_vendors()`. **Adding a vendor is one dict entry with a `verify` and a
`push` callable — no dashboard change, no endpoint change.** Keep it that way.

Field mapping today: HubSpot creates a **contact** (`POST /crm/v3/objects/contacts`,
dedupes on email — a 409 is reported as "already there", not an error). Pipedrive
creates a **person then a lead** linked to it, so it lands in their Leads Inbox
rather than only the People list; if the second call fails the person still
exists and the error says so.

## Credentials — the existing pattern, not a new one

ONE `client_accounts` row via `client_agent.upsert_account`:

| platform | account_id | access_token |
|---|---|---|
| `crm` | vendor key (`hubspot` / `pipedrive`) | credential, or `credential::extra` |

**The vendor lives in `account_id`, not in the platform string.**
`upsert_account` dedupes per platform, so switching vendors REPLACES the row and
the old credential is gone. Encoding the vendor into the platform
(`crm_hubspot`) would leave the previous vendor's key in the table forever.
The `::` composite for Pipedrive's domain is the same idiom `tiktok_service`
uses for its token pair.

Disconnect goes through `remove_accounts` — it **deletes the row**. Flipping a
status would leave a live credential at rest. Same accepted MVP debt as every
other integration: tokens are not encrypted at rest.

Credentials are **verified against the live CRM before being stored**, so a typo
or a token missing contact-write scope fails at connect time rather than silently
on the client's first real lead.

## Failure model

`sync_lead()` never raises. Outcomes are recorded on the lead row
(`crm_sync_status` / `crm_error` / `crm_attempts`) and failures are retried by
`retry_failed_syncs()` up to `MAX_SYNC_ATTEMPTS` (4) within `RETRY_WINDOW_DAYS`
(14).

`CRMError.permanent` separates "this will never work" (4xx except 429 — dead
credential, rejected field) from "try later" (timeout, 5xx, rate limit).
Permanent failures alert immediately instead of burning retries against a wall.
Every alert says the same thing: **the lead IS saved in uallak, only the CRM copy
is missing.**

The retry pass **only touches rows already marked `failed`** — a lead with no
status (captured before this feature, or while the migration was pending) is
deliberately left alone rather than mass-pushed into a client's CRM by surprise.
There is no backfill of historical leads; that would need its own decision.

## Migration

`migrations/2026-08-03-crm-sync.sql` adds the status columns to `client_leads`.
**Until it is run**: connecting works, pushes happen and successes really do
reach the client's CRM — but nothing is recorded, so failures are never retried.
Detected and surfaced (the card hides its stats, alerts say so), never silent.

## Client flow

The card lives in the dashboard's **לידים** section, under the existing
"how do leads get here?" panel, rendered entirely from the server registry.
`GET /api/client/crm`, `POST /api/client/crm/connect`,
`POST /api/client/crm/disconnect` — all session-gated client self-service, the
same shape as `/api/website/connect` and `/api/media/connect`.

Guidance runs through the **website persona (אורי)**, who owns the client's web
presence and lead capture: `_website_reads` includes `external_crm`, his
`data_notes` explain the optional/additive framing, and the card has a button
that opens his chat window. He is told never to improvise setup steps for an
unsupported CRM — say it isn't supported yet and pass it to the team. The
persona path stays read-only by construction: `get_status()` reads a stored row
and the registry; it verifies nothing and pushes nothing.

## Scheduler

```
gcloud scheduler jobs create http crm-retry-syncs --schedule="0 */6 * * *" \
  --uri="{SERVICE_URL}/api/crm/retry-syncs" --http-method=GET \
  --update-headers=X-Admin-Key={ADMIN_KEY}
```

## Deferred / not built

Zoho and every other OAuth-only CRM (above); backfilling a client's existing
leads on connect (deliberate — no surprise bulk writes into their CRM);
two-way sync (we only push — reading their CRM back is a different feature with
its own conflict rules); custom field mapping (standard fields only); per-vendor
rate-limit handling beyond treating 429 as retryable; credential encryption at
rest (same accepted debt as Google/Meta/TikTok).
