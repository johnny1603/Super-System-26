---
name: website
description: How uallak's website agent works — WordPress REST integration for editing/publishing on a client's site (Application Password auth, no OAuth), SEO basics, plugin installs, AND Phase-2 provisioning of NEW WordPress sites on InstaWP with hosting cost passthrough in PRICING. Use when touching agents/website_agent.py, core/wordpress_service.py, core/instawp_service.py, any /api/website* endpoint, or the website pricing in onboarding_agent.
---

# Website agent (existing WordPress sites + new-site provisioning)

## Scope split

- **Phase 1 (BUILT):** control a client's existing WordPress site — publish/edit
  posts and pages, basic SEO fixes (slug, excerpt, media alt text), install a
  free SEO plugin, health-scan connections. `agents/website_agent.py` +
  `core/wordpress_service.py`.
- **Phase 2 (BUILT, 2026-07 — business decision: WordPress ONLY, no
  static/Wix/Webflow route):** provision NEW WordPress sites on InstaWP for
  clients who have none, then reuse every Phase-1 tool to populate them.
  `core/instawp_service.py` + `provision_site`/`populate_site` in the agent +
  the hosting passthrough in `PRICING["website"]["new_site_hosting"]`.

## Auth model — Application Password, NOT OAuth

WordPress core (since 5.6) ships **Application Passwords**: a per-app
24-character password the client creates in wp-admin → Users → Profile →
Application Passwords. Every request is plain HTTP Basic auth over HTTPS.
There is no consent redirect, so the dashboard card ("האתר שלך (WordPress)")
opens an inline form (site URL + WP username + app password) →
`POST /api/website/connect` (session-gated, plain `def`) →
`website_agent.connect_site()` validates against the live site
(`wp/v2/users/me?context=edit` + root index) before storing.

Storage — ONE `client_accounts` row: `platform='wordpress'`,
`account_id`=normalized site URL (`https://…`, no trailing slash),
`access_token`=`username:app_password` (WP usernames can't contain `:`, so
the split is safe). Same plaintext-at-rest MVP debt as Google/Meta tokens.

Application Passwords are revocable in wp-admin at any time — the daily scan
exists because a client (or their old webmaster) can kill the connection
without telling us.

## File map

- `core/wordpress_service.py` — HTTP only (mirrors meta_service):
  `rest_get`/`rest_post` primitives against `{site}/wp-json/…`,
  `WordPressError` (HTTP status + WP error code, `is_auth_error()`),
  `normalize_site_url`, site info + SEO-plugin detection (REST namespaces:
  `yoast/v1` → yoast, `rankmath/v1` → rank_math — free, no extra call),
  posts/pages CRUD, media alt-text update, `upload_media_from_url` (WP can't
  fetch a URL itself — we download ≤10 MB and re-upload), plugin
  list/install/activate (`wp/v2/plugins`, core since WP 5.5).
- `agents/website_agent.py` — business logic per house blueprint, **no LLM
  calls** (pipe pattern like meta_content_agent): `connect_site`,
  `is_connected`, `get_site_overview` (5-min cache; feeds support chat),
  `publish_content` (**defaults to `status='draft'`** — human reviews and
  publishes, same principle as campaigns created PAUSED), `update_content`
  (EDITABLE_FIELDS whitelist: title/content/excerpt/slug/status/featured_media),
  `update_alt_text`, `install_seo_plugin` (free Yoast from wordpress.org,
  no-op if any SEO plugin detected), `run_health_scan` (daily; alerts on dead
  credentials/unreachable sites, 3-day dedup via `website_issue_detected`
  activity rows).
- `core/instawp_service.py` — HTTP only, InstaWP control plane (Phase 2):
  create-from-template, task polling, delete (failure cleanup only).
- `agents/support_agent.py` — injects `website_overview` into the LLM payload
  when connected; `consult_platform_agent` answers "website"/"wordpress"/"site".
- `dashboard/client/index.html` — the connect card + form
  (`connectWebsite()`), `wordpress` in the connections check, activity labels,
  PLUS the "אין לי אתר — הקימו לי אחד" self-provision button/flow
  (`createNewSite()`, `handleSiteProvisionStatus()`, polls the existing
  `/api/dashboard` payload's `website_provision_status` field rather than a
  dedicated status endpoint).

## Endpoints

Client-facing: `POST /api/website/connect` (session cookie), `POST
/api/website/self-provision` (session cookie, no body — see "Self-service
provisioning" below).
Admin/scheduler (X-Admin-Key): `POST /api/website/publish`,
`POST /api/website/update`, `POST /api/website/alt-text`,
`POST /api/website/install-seo-plugin`, `POST /api/website/provision`
(manual-override path: optional `site_name`/`logo_url`/`industry_hint`),
`POST /api/website/populate`, `GET /api/website/overview?client_id=`,
`GET /api/website/standards?client_id=`, `POST /api/website/brand` (re-run
brand identity when a logo arrives), `GET /api/website/scan` (daily).

Scheduler job (same pattern as the other scans):

```
gcloud scheduler jobs create http website-scan --schedule="30 7 * * *" \
  --uri="{SERVICE_URL}/api/website/scan" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}
```

## Standing quality rules (every site we build OR edit)

Machine-enforced — don't route around them:

- **Accessible/SEO-valid HTML on every publish/update**:
  `content_quality_issues()` gates `publish_content` and `update_content` —
  no `<h1>` in the body (the title IS the H1), hierarchy starts at `<h2>`
  with no level jumps, non-empty alt on every `<img>`, label/aria-label on
  every form field, excerpt required on create (core WP's meta-description
  surface). Content generators must produce HTML that passes; the errors
  name the violated rule.
- **Accessibility plugin (Israeli standard 5568)** on every site:
  `install_accessibility_plugin` tries `pojo-accessibility` then
  `wp-accessibility-helper` (both free) — same auto-install pattern as Yoast.
- **Site standards check** (`run_standards_check`, auto-run post-provision +
  `GET /api/website/standards`): both plugins present (auto-installs),
  required pages exist (about/services/contact/legal — Hebrew or English
  slugs/titles; report-only, never auto-creates empty pages), active plugin
  count within the speed budget (`MAX_ACTIVE_PLUGINS`=8), alt-text sample on
  recent media.
- **Images are served as WebP**: `upload_media_from_url` converts JPEG/PNG/BMP/
  TIFF to WebP (quality 82, Pillow) before uploading; GIF/SVG/video pass
  through. Conversion failure falls back to the original file — never fail a
  publish over image format. Keep this rule when adding any new media path.
- **Brand identity, zero design questionnaire**: `apply_brand_identity` is
  THE extension point — logo URL present → Pillow extracts the dominant
  brand colors; no logo → neutral-by-industry palette (`NEUTRAL_PALETTES`),
  never blocking and NEVER asking the client design questions (industry/tone
  already live in the sales-chat data). Future logo/media-generation agents
  call this same function (or `POST /api/website/brand`) with their generated
  logo — do not build them a parallel path. v1 records the palette
  (activity log `website_brand_identity`) for content/site work to consume;
  automated theme re-skinning from the palette is deferred.

Template-time rules (can't be REST-verified — enforced via the master-template
checklist below): genuinely mobile-first theme (tested on a real phone, not
just "technically responsive"), true RTL layout with Hebrew-appropriate fonts
(Heebo/Assistant/Rubik class — not a mechanical LTR flip), minimal preinstalled
plugins.

## Cost discipline (the design rule for this agent)

Everything Phase 1 does is FREE: core WP REST API + free wordpress.org
plugins. Deliberately unreachable from the code: paid plugin/theme licenses,
hosting, domains — anything with a price tag is a client-billed decision
(same principle as ad spend), never an automatic install. `install_plugin`
only accepts wordpress.org slugs for this reason.

## Gotchas

- **Basic auth requires HTTPS and can be disabled** — some hosts/security
  plugins block Application Passwords entirely (`connect_site` then fails
  with 401 even with correct credentials). The client-facing error already
  hints at this; the fix is a setting on THEIR site/host.
- **The WP user's role caps what we can do** — an Editor can post but can't
  install plugins (`install_plugins` capability, admins only; on multisite,
  super-admin only). `connect_site` records `can_manage_plugins` in the
  activity log at connect time.
- **Titles/content come back as objects** — `{"rendered": "…"}` (and
  `context=edit` adds `raw`). Cast before displaying; overview already does.
- **Yoast/Rank Math meta fields are NOT writable via core REST** — detection
  is easy (namespaces) but writing meta title/description needs the plugin's
  own endpoints or `register_meta` glue. Phase 1 handles core fields only
  (slug, excerpt, alt text); plugin-meta writing is deferred.
- **Blocking httpx** → every endpoint touching WP must be plain `def`
  (threadpool), never `async def` — same rule as Google/Meta.
- The Supabase JSON-field dedup filter is `details->>issue_key` (same idiom
  as the ads scans' issue keys).

## Phase 2 — provisioning NEW sites (InstaWP)

Decision (2026-07): WordPress only, on **InstaWP** per-site managed hosting
(verified current: production plans $5 Starter → $45 Elite /mo, billed daily,
custom domains + free SSL, API v2 with Bearer token). Cost basis in PRICING
assumes Starter.

**Flow** (`provision_site(client_id, site_name)` — reachable two ways: the
admin override `POST /api/website/provision` (X-Admin-Key, custom
site_name/logo_url/industry_hint), and the client's own self-service
`POST /api/website/self-provision` — see "Self-service provisioning" below
for why the client path is safe despite this being a real-money trigger):

1. `POST {api}/sites/template` with `template_slug` +
   **`is_reserved: true`** (= permanent = BILLABLE from this moment) →
   returns `wp_url`, `wp_username`, `wp_password`, `s_hash`, and — when not
   pool-served — `task_id`, polled via `GET tasks/{id}/status`.
2. **Credential rotation** — the trick that avoids InstaWP's undocumented
   command API entirely: Application Passwords live in the site DB, so every
   clone INHERITS the master template's known app password. The agent uses it
   once to mint a per-site password (`POST wp/v2/users/me/application-passwords`,
   plaintext returned exactly once), then deletes every other app password on
   the clone. Stored as the standard Phase-1 row: `platform='wordpress'`,
   `account_id`=site URL, `access_token`='user:per-site-password'.
3. Failure at any step → alert + `DELETE sites/{id}` cleanup (a reserved site
   bills until deleted; if cleanup also fails a second alert says to delete it
   manually).
4. `populate_site(client_id, items)` (`POST /api/website/populate`) pipes
   already-generated initial content (landing/base pages, the 10 setup
   articles) through Phase-1 `publish_content` — drafts by default, per-item
   alerting, nothing rebuilt.

**One-time manual setup (required before first provision):**

1. Create the uallak **master template site** in InstaWP: a genuinely
   mobile-first Hebrew/RTL theme (verify on a real phone; Hebrew fonts of the
   Heebo/Assistant/Rubik class, not a flipped LTR theme), the required page
   skeleton (בית, אודות, שירותים, צור קשר, תקנון/פרטיות — the standards
   check verifies these survive cloning), minimal plugins (free Yoast +
   `pojo-accessibility` preinstalled saves two API installs per site), admin
   user (default name `uallak`), and an Application Password for that user,
   created in wp-admin.
2. Save a template from it; put its slug in `WEBSITE_TEMPLATE_SLUG`.
3. Env vars on Cloud Run: `INSTAWP_API_KEY` +
   `WEBSITE_TEMPLATE_APP_PASSWORD` (both in keys_agent KEYS),
   `WEBSITE_TEMPLATE_SLUG` + `WEBSITE_TEMPLATE_WP_USERNAME` (plain env,
   username defaults to `uallak`).

**Cost passthrough (PRICING["website"]["new_site_hosting"]):** cost basis
25 NIS/mo (Starter ~$5 + FX buffer) → client pays **50 NIS/mo** ("אחסון
ותשתית אתר") as an extra monthly_breakdown line INCLUDED in
monthly_management_total — only on packages that build a NEW site, never on
fix-existing work. Encoded in build_proposal's BUDGET PYRAMID #5; the
monthly-line whitelist in #1 names this as the only allowed non-platform
line. Because benefit_value = 2×monthly_management_total (numeric QA), the
two benefit months also waive the hosting line (~50 NIS of real cost
absorbed per client — accepted). The client's DOMAIN stays client-paid
directly (honest_note mentions it for new-site packages).

**Phase-2 gotchas:**

- `is_reserved: true` is the money switch — never "retry" a timeout without
  checking the InstaWP dashboard for a half-created site first. It IS now
  reachable from a client-facing flow (see below) — but only through the
  one narrow, parameter-free entry point, never a general-purpose one.
- provision_site refuses when the client already has an active `wordpress`
  row (would orphan a paid site) — disconnect deliberately first.
- InstaWP responses wrap in `{status, message, data}`; the service unwraps
  `data` and treats `status: false` as an error even on HTTP 200.
- Custom-domain mapping (client's own domain → provisioned site) is a MANUAL
  InstaWP-dashboard step for now — clients launch on the `*.instawp.xyz`
  subdomain until done; automating the mapping API is deferred.

## Self-service provisioning (client dashboard, 2026-07)

**Business decision (2026-07):** the dashboard's website card now offers
"אין לי אתר — הקימו לי אחד" alongside the existing "חבר עכשיו" (connect
existing) option, so a client with no site can self-serve instead of waiting
on an admin to notice and manually call `/api/website/provision`. This
reverses the earlier blanket "never client-facing" stance — the reasoning
for why that's now considered safe:

- **`request_self_provision(client_id, background_tasks)`** is a genuinely
  narrower entry point, not the admin endpoint reused with a session check:
  no site_name/logo_url/industry_hint params exist for a client to touch.
  Business info is already on file from onboarding; no logo → the
  zero-design-questionnaire neutral-palette fallback (see
  `apply_brand_identity`) applies automatically, same as it would for an
  admin-triggered call made with no extra input.
- Guarded against accidental double-billing: refuses if `is_connected()` is
  already true, and refuses a second click while a request is still
  in-flight (`_provision_state_from_activity` reads the client's own recent
  `client_activity` rows for an unresolved `website_provision_requested`).
- Runs via `background_tasks` (duck-typed exactly like
  `engagement_agent._dispatch_approved` — this module still imports no
  fastapi) since real provisioning takes real minutes; the dashboard shows a
  "בהקמה..." state and polls the existing `/api/dashboard` payload's new
  `website_provision_status` field (`None | 'requested' | 'failed'`, derived
  from the same `client_activity` rows, no new table or endpoint) until it
  resolves, then reports success/failure via dashboard chat either way.

**What v1 self-service does NOT do:** it does not call `populate_site` —
there is no existing function anywhere in the codebase that auto-generates
the initial page copy / article batch from onboarding answers with zero
human input (populate_site takes ALREADY-GENERATED items; today those are
hand-assembled per client, admin-side). So a self-provisioned site launches
with exactly what `provision_site` alone produces: the master template's
generic Hebrew page skeleton (בית/אודות/שירותים/צור קשר/תקנון) and the
neutral-by-industry color palette — NOT personalized page copy. This is
identical to what an admin-triggered `provision_site` call with no extra
input produces today; self-service didn't lower the bar, it just removed
the human bottleneck for reaching that same starting point. Building real
zero-touch content generation (personalized base-page copy, an initial
article batch) is a separate, not-yet-built capability — don't assume it's
covered because this section exists.

**Billing entitlement IS verified (2026-07-22 fix), best-effort and fail-CLOSED.**
`_package_includes_hosting(client_id)` gates `request_self_provision` before
anything else runs:

1. Reads the client's own `client_activity` (`agent_name='paypal_service'`,
   newest first) for the ORIGINAL `subscription_created` row (skips
   `upgrade: true` rows — `get_upgrade_tiers()`'s fixed ladder never includes
   website hosting, so an upgrade row can never confirm or deny it) and
   pulls its `package_id`. Stops at a `subscription_cancelled` row exactly
   like `_client_subscription_info` does.
2. Looks up the matching `leads` row via `budget_agent._lead_row(client_id)`
   (exact `client_id` match, falling back to an email match — reused as-is,
   not reimplemented; see that function's own docstring for the known
   `leads.client_id` column gap).
3. Finds the package in `lead.proposal.packages` whose `id` matches, and
   checks whether its `monthly_breakdown` dict contains the EXACT key
   `PRICING["website"]["new_site_hosting"]["label_he"]` — the literal string
   the onboarding prompt is instructed to write whenever a package builds a
   new site (BUDGET PYRAMID #5, point 5).

**Any gap in that chain returns False** (no checkout row found, no matching
lead, package_id not in the stored proposal, key genuinely absent) — the
client sees `ERR_WEBSITE_NOT_IN_PACKAGE` ("this isn't included in your
package — talk to us in the chat") rather than silently being allowed
through. This is a best-effort structural check, not a perfect one: it
trusts the LLM-authored `monthly_breakdown` to use the exact PRICING label
(the onboarding prompt instructs this, but LLM output isn't literally
guaranteed) and it can't help a client whose lead row is missing/unmatched
for unrelated reasons (e.g. a manually-created client with no proposal at
all) — such a client is correctly blocked here too, and needs an admin to
provision manually via `/api/website/provision` instead, same as before this
fix existed. Follows the `{"code": "ERR_X"}` server error-code pattern (see
the i18n skill) — `ERR_WEBSITE_ALREADY_CONNECTED` and
`ERR_WEBSITE_PROVISION_IN_PROGRESS` cover the other two failure branches.

## Deferred / not built

Automated theme re-skinning from the brand palette (apply_brand_identity
records it; applying it to theme CSS/customizer is the deferred half),
custom-domain mapping automation, plan auto-upgrades (Starter → Plus when a
site outgrows it — watch disk/CPU manually for now), writing Yoast/Rank Math
meta fields, WooCommerce anything, comment moderation on WP sites,
site-speed/uptime monitoring beyond the daily credential scan, media library
cleanup, multi-site (one WP row per client), token encryption at rest (same
accepted MVP debt as Google/Meta).

NOTE for content generators (populate_site / publish_content callers): every
item must now carry an `excerpt` and pass `content_quality_issues` (h2-start
hierarchy, alt text, labeled form fields) — generation prompts must bake
these rules in, or publishes fail with named rule violations.
