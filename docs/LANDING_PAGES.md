# Landing pages, owned and hosted by uallak (2026-11-01)

**Decision (owner):** landing pages are a uallak capability, fully owned and
hosted here. Complex multi-page websites stay on WordPress via InstaWP and are
NOT replaced — nothing used for full websites is touched.

## 0. Inventory (Step 0, read from the code, not assumed)

**Landing pages today** — already in-code, already ours:
- Code: `core/landing_pages.py` (data + renderer), `core/landing_domains.py`
  (client's own subdomain via Cloudflare for SaaS), `agents/landing_page_agent.py`
  (copy + lifecycle), routes in `core/api_server.py` (`/lp/...`, `/_uallak-verify`,
  `/api/client/landing-pages*`, `/api/client/landing-domain*`,
  `POST /api/landing-pages/admin/create`), the dashboard's "דפי נחיתה" section.
- Data: table `landing_pages` (id, client_id, slug, title, goal, content jsonb,
  status draft|published, copy_source, created_at, updated_at); the custom
  domain is a `client_accounts` row with platform `landing_domain`; leads are
  `client_leads` rows with `source_detail = 'lp:<slug>'` via
  `POST /api/leads/capture/{token}`.
- Already wired: `client_state` (landing section), `client_deletion`
  (`landing_pages`), ownership export, the website persona's read.
- Accounts / billing: no third-party tool. Hosting is our Cloud Run service;
  AI copy is our Claude usage (cost rows `claude_landing_page`). The custom
  domain path needs `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ZONE_ID` and a Worker —
  **not set up yet**, so every page today serves on `app.uallak.com/lp/...`.
- Which clients/pages exist: not readable from this machine (no production
  credentials). After deploy, `/admin/landing/` lists every client's pages.

**InstaWP** — used ONLY for full websites:
- `website_agent.provision_site` (Phase 2: a NEW WordPress site, admin-triggered
  or the client's self-provision), `core/instawp_service.py`.
- uallak's own workspace key `INSTAWP_API_KEY`; hosting billed to uallak
  (~$5/month Starter per site) and passed through in `PRICING`.
- Also referenced by `operating_costs`, `budget_agent`, `php_exposure`,
  `third_party_pricing` (all about full sites).
- **Not used for landing pages at all.** There are no landing pages, forms or
  leads on InstaWP to migrate, and nothing of InstaWP is removed.

## 1. Hosting

uallak serves every page from the existing Cloud Run service (project
`super-system-500410`, region `me-west1`) on ONE shared route — no project per
page. Pages are static HTML from a fixed, vetted template: inline CSS, no web
fonts, no JavaScript (except the client's Tag Manager on their own domain),
mobile-first, `lang="he" dir="rtl"`, light/dark.

**Custom client subdomains with SSL — the real way from our setup:**
- Cloud Run domain mappings are **not available in me-west1** (the reason
  `proxy/` exists), and would need each client's domain verified under our
  Google account. Not usable.
- **Chosen: Cloudflare for SaaS** (already coded in `core/landing_domains.py`):
  the client adds ONE CNAME `lp.theirdomain` → `lp.uallak.com`; Cloudflare
  issues and renews the certificate. A small Worker (`proxy/landing-worker.js`)
  forwards `<their-host>/<path>` to `app.uallak.com/lp-host/<path>` with the
  hostname in `x-uallak-lp-host`; the app resolves the client from that hostname
  (verified rows only, `landing_domains.client_for_hostname`). Cost: per the
  existing note in `core/landing_domains.py`, custom hostnames are included up to
  a limit on Cloudflare's plan and charged per hostname beyond — re-check on
  Cloudflare's pricing page before relying on it.
- Alternative if Cloudflare is ever unwanted: a Google Cloud global external
  Application Load Balancer with a serverless NEG pointing at the me-west1
  service and Certificate Manager certificates per client hostname. Correct,
  but it is a standing hourly-billed resource plus per-certificate fees — more
  expensive than the Cloudflare path for this use. Not built.
- **Fallback (works today):** `https://app.uallak.com/lp/<client>/<page>`.

Setup still needed for custom domains (owner, once): `lp.uallak.com` proxied in
the uallak.com zone; Cloudflare for SaaS enabled with `lp.uallak.com` as the
fallback origin; env `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ZONE_ID`; deploy
`proxy/landing-worker.js` on the custom hostnames.

## 2. Flow: propose → preview card → approve → publish → live

1. The agent writes the page from the client's real data (business context,
   cached competitor research through the `ads` lens) — never an empty form.
   The call/WhatsApp buttons come from the phone the client already gave us.
2. Every write is a NEW row in `landing_page_versions` (generated, rewrite,
   client edit, revert, migrated). Nothing is overwritten.
3. A `publish_landing_page` card appears in the website agent's (אורי) chat,
   pinned to that exact version, with a session-gated preview link
   (`/api/client/landing-pages/{id}/preview?version=N`: noindex, no Tag Manager,
   the form cannot submit).
4. The client's click publishes that version (`published_version`). A newer
   card for the same page replaces the older one. The dashboard's "העלאה
   לאוויר" button only opens this card.
5. The done line in the chat carries the live URL. Editing a live page never
   changes what visitors see until the next card is approved.
6. Revert: "גרסאות" → "חזרה לגרסה הזו" — one tap; it becomes a new version and,
   on a live page, goes live at once (the tap is the approval).
7. **Everything above works from the chat too (2026-11-02, `docs/CHAT_PARITY.md`).**
   - The manager (or אורי) proposes `create_landing_page` with a concrete title
     and goal built from the client's real data, asking at most one question.
   - The client's click runs THE builder (`landing_page_agent.create_page`, the
     one the "+ דף נחיתה חדש" button calls). It writes a draft, and the publish
     card from step 3 appears in the same thread the client asked in
     (`persona=`).
   - The rest of the lifecycle has its own cards, each calling the dashboard
     button's own function: `edit_landing_page`, `revert_landing_page`,
     `unpublish_landing_page`, `delete_landing_page`, `connect_landing_domain`
     and `check_landing_domain`.
   - Results and versions per page come from `client_state.landing_pages`.
   - A landing-page ask is never a team request.

## 3. Tracking

- **Our own counters, everywhere** (`core/landing_tracking.py`, table
  `landing_page_stats`, atomic RPC `landing_stat_bump`): views (on serve),
  call / WhatsApp / store clicks (through counting redirects
  `/lp/<client>/<page>/go/<kind>` whose target comes only from the live
  version's validated contact).
- **The client's Tag Manager, on their own domain only**: the container they
  connected (recorded at connect time, no Google call per visit) loads when the
  request came through the Worker with their verified hostname. It never loads
  on `app.uallak.com`: a third-party container on our origin could act as a
  logged-in client or admin. dataLayer events (GA4 recommended names):
  `page_view`, `generate_lead` (form submit), `click_to_call`,
  `click_whatsapp`, `begin_checkout` (store click).
- **Purchase, honestly:** the purchase happens on the store's own site, not on
  the landing page. We record the click to the store (`begin_checkout`); the
  `purchase` event belongs to the store's own tracking.
- No Tag Manager connected → after publishing, the existing
  `connect_tag_manager` card is offered in the same chat.
- Form submissions → `client_leads` (`source_detail lp:<slug>`, `created_at`,
  plus utm/click-id attribution rendered server-side).

## 4. Compliance

- Every page links to its own privacy and terms pages
  (`/lp/<client>/privacy`, `/terms`): fixed Hebrew text naming the business
  as the one collecting the enquiry and uallak as host. Never model-written.
- A consent line under the form, linking the privacy page.
- Sanitized twice: tags, control/bidi characters and script URLs stripped on
  the way in (`sanitize_text`, `normalize_content`, `normalize_contact`, https
  only for links); every value HTML-escaped on the way out. No raw HTML is
  ever stored.

## 5. Integration

- `client_state.landing_pages.pages[]`: id, status, live URL, leads, 30-day
  views/calls/WhatsApp, unpublished changes.
- Ads: `google_ads_agent.draft_campaign_spec` / `meta_ads_agent.draft_campaign_spec`
  take `landing_page_id`; with no site connected they fall back to the newest
  live landing page (`landing_page_agent.ad_destination`).
- Performance page: `performance_page()["landing_pages"]` per page.

## 6. Limits (invisible to the client)

- Pages per client: `MAX_PAGES_PER_CLIENT` (3, unchanged); live at once:
  `MAX_ACTIVE_PAGES` (3); rendered size: `MAX_PAGE_BYTES` (64 KB). Refusals
  are plain chat lines; the admin gets ONE alert when a client reaches the
  live limit.
- Form: honeypot field, signed render time (min 3 s, max 2 days, bound to the
  client), ≤ 5 submits per IP per client per 10 min (per instance), plus the
  existing daily per-client cap in `client_leads`. A dropped submit is answered
  like a stored one.

## 7. Cost metering (ILS, existing cost tracker)

- Generation: every AI call for a page runs inside
  `cost_action("landing_page_generate", ref="lp:<page id>")`.
- Hosting: no bill splits per page, so it is an ESTIMATE: last month's bytes
  and requests per page × rates the owner sets on `/admin/landing/`; recorded
  once a month per page (`landing_hosting`, meter `infra`,
  `details.estimate = true`). Unset rates → nothing recorded, the page says so.
  Monthly job: `GET /api/landing/hosting-costs` (X-Admin-Key), or the button.
- `/admin/landing/` shows cost per page and per client.

## 8. Admin

`/admin/landing/`: every client's pages, status, versions, URL, 30-day numbers,
cost; "הורדה מהאוויר" (instant — the next visit is a 404; the client is told in
אורי's chat; audited); hosting rates; backup + migration.

## 9. Migration plan

There is nothing on InstaWP to migrate (§0). The migration is our own: from
single-content pages to versioned pages.
1. Run `migrations/2026-11-01-landing-own.sql` in Supabase (idempotent). Until
   then everything keeps working as before (versions/counters switch off by
   themselves).
2. Deploy, and verify one page live on `app.uallak.com/lp/...` (desktop + phone).
3. `/admin/landing/` → "גיבוי" (downloads the JSON; sha256 stored) → "העברה
   לגרסאות": each page without versions gets version 1 equal to what is live;
   `published_version` points at it. Refuses without the matching backup.
   Idempotent; deletes nothing.

## 10. Removal plan (separate step — waits for explicit approval)

Scope: ONLY the landing-page usage of the old tool. Finding (§0): InstaWP was
never used for landing pages, so **there is nothing to remove**. InstaWP,
`INSTAWP_API_KEY`, `core/instawp_service.py`, `website_agent.provision_site`,
the PRICING pass-through and every full-website path stay exactly as they are.
If a landing-page use of InstaWP is ever found (e.g. a WordPress page used as
a landing page by hand), the procedure is: back it up (page HTML + its form's
leads export), rebuild it here as a landing page, verify live, point ads at
the new URL, and only then ask the owner before deleting anything on WordPress.

## Tests

`tests/test_landing_own.py`: publish/revert, sanitization, tracking events,
form → leads + spam, per-client isolation, unpublish, limits, the publish card,
the backup-first migration of a sample page, hosting estimate, route order.
