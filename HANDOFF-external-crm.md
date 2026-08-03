# HANDOFF — external CRM connection for clients

## ⚠ The brief's suggested pair doesn't work as described

The brief proposed HubSpot and Zoho behind a "paste your API key" flow. Only one
of those is a paste-a-key CRM:

- **HubSpot** — legacy API keys were **sunset in November 2022**. The modern
  equivalent is a **Private App access token**: static, never expires, sent as
  `Authorization: Bearer`. (HubSpot also shipped a "Service Key" for data-only
  system-to-system access in 2026; a client pasting either works.)
- **Pipedrive** — a genuine static **API token** (`x-api-token`), against the
  company's own subdomain. Needs a second field for that domain.
- **Zoho** — **OAuth 2.0 only.** Lifelong auth tokens were retired for security.
  Supporting it means app registration, a consent + callback flow, per-datacenter
  API domains (.com/.eu/.in/.com.au) and hourly access-token refresh. That is the
  same shape as our Google/Meta/TikTok integrations — a separate handoff, not a
  line item.

**Scope confirmed with Johnny before building**: HubSpot + Pipedrive, Pipedrive
mapped as person + lead, failures recorded and retried.

## What was built

### The registry is the whole extensibility story

`core/crm_service.VENDORS` is the ONLY place a vendor is named — endpoints, field
names, credential labels, and the Hebrew "where do I find my key" help text.
`crm_agent`, the API layer and the dashboard all drive off `supported_vendors()`.
**Adding a CRM is one dict entry with a `verify` and a `push` callable**: no
endpoint change, no dashboard change.

Split follows the house convention: `core/crm_service.py` is HTTP only (no DB, no
decisions about *when* to push); `agents/crm_agent.py` owns the business logic.

### Field mapping

| Vendor | Creates | Notes |
|---|---|---|
| HubSpot | Contact (`POST /crm/v3/objects/contacts`) | Dedupes on email — a 409 is reported as "already there", not an error |
| Pipedrive | **Person, then a Lead linked to it** (`/api/v2/persons`, `/api/v2/leads`) | Two calls on purpose: a person alone lands in People, not the Leads Inbox where a new enquiry is expected. If the second call fails the person still exists and the error says which half completed |

Both map onto what `capture_lead` already collects: name / phone / email / message.

### Credential storage — reused, not invented

Investigated the existing pattern and reused it exactly: ONE `client_accounts`
row via `client_agent.upsert_account`, the same helper WordPress, TikTok and
media already use.

| platform | account_id | access_token |
|---|---|---|
| `crm` | vendor key | credential, or `credential::extra` |

**The vendor is in `account_id`, not the platform string.** `upsert_account`
dedupes per platform, so switching HubSpot → Pipedrive replaces the row and the
old credential is gone. Encoding the vendor into the platform (`crm_hubspot`)
would leave the previous vendor's key in the table forever. The `::` composite
for Pipedrive's domain is `tiktok_service`'s idiom for its token pair.

Disconnect uses `remove_accounts` — it **deletes the row**; a status flip would
leave a live credential at rest. Credentials are verified against the live CRM
*before* being stored, so a typo or a token missing contact-write scope fails at
connect time, not silently on the client's first real lead.

### Sync: our copy is the record, the push is a side effect

`capture_lead()` stores the lead and the endpoint answers the customer **before**
any CRM call runs — the push is dispatched through FastAPI `BackgroundTasks`. A
slow, broken or revoked CRM cannot delay a form submit or lose an enquiry. Same
principle as `client_leads._insert_lead` keeping the lead when the attribution
columns are missing.

`sync_lead()` never raises. Outcomes land on the lead row and failures are
retried by `retry_failed_syncs()` — up to 4 attempts within 14 days.
`CRMError.permanent` separates "will never work" (4xx except 429) from "try
later" (timeout, 5xx, 429), so a revoked credential alerts immediately instead of
burning retries against a wall. **Every alert says the same thing: the lead IS
saved in uallak, only the CRM copy is missing.**

The retry pass **only touches rows already marked `failed`**. A lead with no
status — captured before this feature, or while the migration was pending — is
deliberately left alone rather than mass-pushed into a client's CRM by surprise.

### Client flow, through the persona

The card sits in the dashboard's **לידים** section under the existing "how do
leads get here?" panel, rendered entirely from the server registry (the dashboard
hardcodes no CRM name). Session-gated self-service, same shape as
`/api/website/connect` and `/api/media/connect`.

Guidance runs through the **website persona (אורי)** — he owns the client's web
presence and lead capture, so the CRM their leads flow into is his subject.
`_website_reads` now includes `external_crm`, his `data_notes` carry the
optional/additive framing, and the card has a button that opens his chat window.
He is explicitly told never to improvise setup steps for an unsupported CRM.
The persona path stays read-only by construction: `get_status()` reads a stored
row plus the registry — it verifies nothing and pushes nothing.

### Opt-in, verified

A client with no CRM connected sees **zero change**. `core/client_leads.py` is
untouched apart from the endpoint's guarded dispatch; every `crm_agent` entry
point returns early with `reason="no_crm"`; the dashboard card hides itself if
the status endpoint is unreachable.

## Needs you

1. **Run `migrations/2026-08-03-crm-sync.sql`** in the Supabase SQL editor. Until
   then: connecting works, pushes happen and successes really do reach the
   client's CRM — but nothing is recorded, so failures are never retried. This is
   detected and surfaced (stats hidden, alerts say so), never silent.
2. **Create the retry scheduler job** (also in the crm skill):
   ```
   gcloud scheduler jobs create http crm-retry-syncs --schedule="0 */6 * * *" \
     --uri="{SERVICE_URL}/api/crm/retry-syncs" --http-method=GET \
     --update-headers=X-Admin-Key={ADMIN_KEY}
   ```

No new env vars — credentials are per-client, in `client_accounts`.

## Deferred / not built

Zoho and other OAuth-only CRMs (above — needs its own handoff); **backfilling a
client's existing leads on connect** (deliberate: no surprise bulk writes into
someone's CRM, and it would need its own opt-in); two-way sync (we only push —
reading their CRM back has its own conflict rules); custom field mapping
(standard fields only); credential encryption at rest (same accepted MVP debt as
Google/Meta/TikTok).

## Not verified live

No Python on this dev machine, and **neither CRM has been called with a real
token**. The vendor request/response shapes are written against current public
API docs — the likeliest one-round fix on first real use is a response-shape
detail (Pipedrive wraps payloads in `data`, handled defensively; HubSpot's 409
body). The dashboard card was exercised against a stubbed DOM with 17 cases
including the ugly ones: migration pending (no fabricated stats), bad key (server
message surfaced), endpoint down (card silently hidden), two-step disconnect, and
that Pipedrive's second field appears while HubSpot's does not. Python was
structurally checked, not executed.

**First real use should confirm**, in order: the migration applies; a HubSpot
private-app token connects and a test lead appears as a contact; a Pipedrive
token + domain connects and a test lead appears in the Leads Inbox (not just
People); a deliberately-revoked token produces a permanent-failure alert rather
than four retries.
