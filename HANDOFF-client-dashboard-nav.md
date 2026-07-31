# HANDOFF — Client dashboard navigation (leads, personal area, support, exports, media)

Five sections behind a real nav bar, replacing the single unstructured scroll.

## ⚠️ Run the migration BEFORE deploying

`migrations/2026-07-31-client-leads.sql`, in the Supabase SQL editor. Every
statement is idempotent.

Without it: the לידים section shows its "being set up" state, the public
capture endpoint drops every submission, and the leads export 500s. **Nothing
else breaks** — no existing code path reads `client_leads`, and the offboarding
export/purge both tolerate the table being absent.

## What the investigation found

Stated up front because two of the five items were not what the brief assumed:

- **There was no client-facing leads view to reuse.** The brief refers to "the
  earlier handoff", but `HANDOFF-crm-leads.md` is the ADMIN leads tab over
  uallak's OWN funnel. Grep confirmed: no `client_leads` table, no client leads
  endpoint, no UI. `core/lead_volume.count_client_leads()` is a documented
  placeholder that approximates the number from Google/Meta ad conversions and
  always reports `complete: False`. So section 1 is entirely net-new, including
  the question of where the rows come from.
- **Business and contact details already existed and were simply never shown.**
  `clients.business_name`, `business_tax_id`, `address` and `phone` are
  collected at checkout and were exposed to nobody but the admin — not even to
  the person they describe. `/api/dashboard` deliberately omitted them.
- **The WhatsApp number was already configured**, in exactly one place:
  `core/admin_service.py` `DEFAULT_SETTINGS["whatsapp_number"] = "972504493725"`,
  overridable per-deploy through the `app_settings` table and editable from the
  admin dashboard's settings tab. The landing page (`dashboard/landing/index.html`,
  two hardcoded `wa.me/972504493725` links) is a pre-existing second copy; the
  new support section reads the setting rather than becoming a third.
- **Nothing in the repo generated files.** No reportlab, openpyxl, xlsxwriter or
  fpdf in `requirements.txt` — verified by grep. The only export was
  `/api/client/data-export` (raw JSON).
- **Every media asset was already indexed.** `media_agent`, `avatar_agent` and
  `content_docs_agent` each write a `client_activity` row carrying
  `result.file_id` and `result.link`. The media hub reads that index; it stores
  nothing of its own and duplicates no storage.

## The nav

Five views swapped in place (`showView()` in `dashboard/client/index.html`), not
five pages. The chat windows, upgrade panel and welcome tour are page-level
singletons — a real navigation would kill an open agent conversation
mid-sentence. `/dashboard/#leads` deep-links; each section fetches once, lazily,
on first open.

| view | what it is |
|---|---|
| בית | the previous page, unchanged |
| לידים | `client_leads` — filters, WhatsApp quick-send, status, notes |
| מדיה | every asset ever generated, not just the approvals queue |
| אזור אישי | business info, contact info, package, billing history |
| תמיכה | one-tap WhatsApp to a human |

## 1. Leads — and the honest answer to "where do they come from"

`client_leads` is a new table and a new direction of travel. `leads` is the
conversation that sold US a client; `client_leads` is a person who contacted
THAT client. The two modules share no code and no table, and CLAUDE.md now says
so at the table list.

Rows arrive through `POST /api/leads/capture/{token}` — public by necessity,
since it is called from a form on the client's own public website. The token is
HMAC-signed with a derived secret (`core/session.create_lead_capture_token`) and
carries the client_id itself, so there is no lookup table and a tampered token
verifies as nothing. It grants exactly one capability: create a lead row for
that client. It reads nothing.

The leads section shows each client their own capture URL and a paste-ready form
snippet, so the section explains itself instead of sitting empty with no reason
given.

**Three limits worth stating:**

1. **No ingestion is wired to a live site yet.** The endpoint exists and works;
   installing the form on a client's WordPress site is a per-client step nobody
   has taken. Until then the section shows its honest empty state.
2. **`multipart/form-data` posts are not supported** — only JSON and
   `application/x-www-form-urlencoded`. Starlette's `request.form()` asserts on
   `python-multipart`, which is not in `requirements.txt`, so the body is parsed
   by hand. A contact form only needs multipart if it uploads files.
3. **`count_client_leads()` was deliberately NOT switched to read this table.**
   That number feeds a *price* (`core/lead_volume.py`'s tiers). Pointing it at a
   table that stays empty until someone installs a form would drop every client
   to tier 1 on the next scan. Switch it over — that is exactly what the seam is
   for — once at least one client is actually capturing.

**Defences**, since this codebase has no rate limiting anywhere: every field is
length-capped, and `MAX_LEADS_PER_DAY` (500/client/day) caps row creation. Past
the cap the request still returns 200 — a real customer must never see an error
from a contact form — but the row is dropped and an alert fires.

Ownership is enforced on every read AND every write: `list_leads`, `set_status`
and `set_note` all filter on `client_id`, so a guessed lead id matches nothing
and returns the same 404 as a nonexistent one.

## 2. Personal area

Read-only, from the new `GET /api/client/account`. Changing a tax ID mid-contract
is a conversation, not a form field, so the panel points at the chat and the
support section instead of offering an edit box. Billing history is the existing
`/api/client/billing`, rendered into both the home card and this section from one
fetch so they can never disagree.

## 3. Human support

`GET /api/client/support` reads `admin_service.get_settings()["whatsapp_number"]`
— the same value the admin settings tab writes. Change it there, it changes here,
no deploy. Falls back to the in-code default if `app_settings` is unreadable,
because "no way to reach a human" is the worst possible failure for this section.
The number renders as `050-449-3725` and the wa.me link is pre-filled with the
client's name.

## 4. Exports — PDF, Excel, Google Docs

`GET /api/client/export/{dataset}?format=xlsx|pdf` and
`POST /api/client/export/{dataset}/google-doc`, for datasets `leads`, `media`,
`activity` and `billing`. Export bars appear under every section that shows data.
Adding a dataset is one branch in `_export_dataset` — the three renderers all
consume the same `{title, columns, rows}` shape.

- **Excel** is a real `.xlsx`, written by hand in `core/export_service.py` with
  stdlib `zipfile` (an xlsx is a zip of five XML parts). No new dependency. Cell
  text uses `inlineStr`, which keeps Hebrew and leading-zero phone numbers
  intact; real numbers still go in as numbers so a column can be summed.
- **PDF opens a print dialog rather than downloading a file.** This is a
  deliberate trade, not a shortcut: a server-side PDF of Hebrew needs an embedded
  Hebrew TTF plus bidi shaping, neither of which exists in the container, and
  getting it wrong produces boxes or reversed words. The browser already does
  both correctly, in the client's own locale. `print_html()` returns a
  print-styled page that calls `window.print()`; the client picks "Save as PDF".
- **Google Docs — the blocker, stated rather than skipped.** The Doc created is
  real and editable, via the existing `drive_service.upload_google_doc()` (the
  same call `content_docs_agent` uses). But it is created **by our service
  account, in uallak's Drive**, then shared with the client's email as a writer —
  **not in the client's own Drive**, as the brief asked for.

  **Why:** no client-side Google OAuth in this codebase carries a Drive scope.
  The four client Google flows (Ads, GTM, YouTube, Merchant Center) request
  advertising scopes only; the only Drive credential in the system is the
  service account, which by design has no user Drive of its own. Creating a file
  in the client's Drive requires a new consent screen with
  `drive.file`, a fresh grant from every client, and token storage in
  `client_accounts` — a whole OAuth integration, not a format option.

  **What the client gets today:** a real Google Doc they can open, edit, and
  "Make a copy" into their own Drive in two clicks. **What is still missing:**
  the Doc being born in their Drive, owned by them, without the copy step.
  Closing it is the follow-up; the `google-ads` skill documents the OAuth
  patterns to reuse.

  Requires `GOOGLE_SERVICE_ACCOUNT_JSON` + `DRIVE_MEDIA_FOLDER_ID`; returns a
  clear 503 and a translated message when either is unset.

## 5. Media assets hub

Reads `client_activity` filtered by the five asset action_types — deliberately a
direct query rather than paging `get_activity()`, so a client with a busy
activity feed doesn't have their oldest media fall off the end of a "last N rows"
read. The hub promises everything ever created and has to mean it.

Assets whose Drive upload failed after generation (the agents alert on that path
and keep the activity row) are shown and flagged, not hidden — hiding them would
make the library quietly incomplete.

## Files

| file | what |
|---|---|
| `migrations/2026-07-31-client-leads.sql` | the `client_leads` table (run first) |
| `core/client_leads.py` | capture, list/filter, status, notes, wa.me normalization |
| `core/export_service.py` | xlsx writer, print view, Google Doc HTML |
| `core/session.py` | `create_/verify_lead_capture_token` (non-expiring, derived secret) |
| `core/api_server.py` | all the endpoints above; `client_leads` added to the offboarding purge + export |
| `dashboard/client/index.html` | the nav, four new views, ~90 new i18n keys × 5 languages |
| `CLAUDE.md` | `client_leads` in the table list; the two new core modules |

## Verification status

Same as everything shipped from this machine: **no Python was executed** — there
is none on the dev box. The Python was verified by reading.

The dashboard JavaScript **was** executed: `<script>` parsed with `vm.Script`,
then every new function driven against a stubbed DOM and stubbed `fetch` with
adversarial fixtures (a lead carrying `<script>alert(1)</script>`, a lead with no
name and no phone, an asset of an unknown kind, a null timestamp, an empty tax
ID). All ten pass with no runtime errors; XSS escaping, the missing-WhatsApp-button
path, and the unknown-kind fallback are asserted explicitly.

**Smoke path after deploy:**

1. Run the migration.
2. Open `/dashboard/#leads` → the section loads with its empty state and shows a
   capture URL.
3. `curl -X POST "<capture_url>" -d "name=בדיקה&phone=0501234567&message=שלום"`
   → reload the section; the lead is there with a working WhatsApp button.
4. Change its status and type a note; reload — both persisted, the nav badge
   dropped by one.
5. Export it as Excel (opens in Excel with Hebrew intact) and as PDF (print
   dialog appears).
6. `/dashboard/#support` → the number reads 050-449-3725 and the button opens
   WhatsApp pre-filled.
