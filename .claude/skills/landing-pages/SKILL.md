---
name: landing-pages
description: How uallak's in-code landing pages work — the shared single-route hosting model, the client's-own-subdomain self-service DNS flow (Cloudflare for SaaS), the 3-pages-included ceiling, and lead attribution per page. Use when touching core/landing_pages.py, core/landing_domains.py, agents/landing_page_agent.py, the /lp/ serving routes, or the dashboard's דפי נחיתה section.
---

# Landing pages (in-code, not WordPress)

## The distinction that matters

`website_agent` builds the client's MAIN site: WordPress, InstaWP-hosted, a
full business presence with navigation and depth. A **landing page** is the
opposite shape — one offer, one form, no navigation, fast — and it is built in
code here. The two share **no table, no renderer and no hosting path**. Same
discipline as `leads` vs `client_leads`: similar words, opposite jobs. Don't
"unify" them.

## Hosting: ONE shared route, never a project per page

Every client's pages are served by the existing Cloud Run app at
`/lp/{client_slug}/{page_slug}` (`core/api_server.serve_landing_page`,
registered before the root catch-all mount, same rule as everything else).

**Why not Cloudflare Pages**: the brief flagged its ~30-project ceiling. The
answer is not "one Pages project for everyone" — it is not using Pages at all.
The app already serves static routes; adding a second platform would mean a
deploy per page edit and a second place to debug.

The client segment is `{slugified-business-name}-{client_id}`; the id is parsed
back off the tail with a regex, so there is **no lookup table and a renamed
business keeps working**.

## Custom domains: Cloudflare for SaaS, and why not Cloud Run

Checked, not assumed (2026-08-01):

- **Cloud Run domain mappings — REJECTED.** They require the CLIENT's base
  domain to be verified in **Google Search Console under OUR Google account**:
  the client would add a TXT record *and* add us as a verified owner, then we
  run a gcloud command per client. That contradicts the goal (one record,
  forwarded as-is, no understanding required). Mappings are also still
  unavailable in `me-west1` — the reason `proxy/` exists.
- **Cloudflare for SaaS custom hostnames — CHOSEN.** The client adds exactly
  ONE CNAME (`lp.theirdomain.com` → our fallback origin); Cloudflare issues and
  renews the certificate for their hostname. **100 custom hostnames are
  included free on the Free plan**, $0.10/hostname/month beyond — free to 100
  clients, trivially absorbed after. uallak.com is already a Cloudflare zone.
  (Vendor pricing: `price-monitoring` territory if it ever needs re-checking.)

**The shared URL is a WAITING STATE, never the destination.** The dashboard
says so in those words, and `page_url()` flips every link automatically the
moment verification passes. Don't let a "permanent fallback" framing creep in.

## Verification is END-TO-END, not a DNS lookup

`verify_domain` fetches `https://{hostname}/_uallak-verify` and checks for the
marker body. A resolving CNAME with no certificate yet still shows the visitor
a browser warning, so "the record exists" is the wrong thing to confirm —
**"a visitor can actually load it over HTTPS" is the claim**, and it needs no
`dnspython` (not a dependency) and no secret (the marker proves routing, not
identity; it answers on every hostname that reaches the app).

When verification fails, the code distinguishes **whose side is incomplete**.
If `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ZONE_ID` are unset, the client is told
it's on us and to change nothing — never sent to re-check a correct record.

## Structured content only — a SECURITY boundary

`landing_pages.content` holds STRINGS (headline / subheadline / benefits /
sections / cta), and `render_page()` escapes every one into a fixed template.
Raw HTML is never stored and never rendered, because **these pages serve from
app.uallak.com — the same origin as the client dashboard and its session
cookie**. Stored raw HTML would be stored XSS against every logged-in client
and admin. A "custom HTML" feature would have to serve from a different
origin; do not relax this.

`normalize_content()` is the write-side gate: it caps every field and DROPS
unknown keys (the renderer only reads the known ones, so keeping extras would
imply they do something).

## Lead capture reuses the existing endpoint, unchanged

The form is a plain `<form method="post">` to
`POST /api/leads/capture/{token}` with the SAME `create_lead_capture_token` the
WordPress auto-wiring uses — no second capture path, no second token scheme.
Per-page attribution rides on `client_leads.source_detail` as `lp:{slug}`, so
`lead_counts_by_page()` needed **no schema change**.

The `redirect` field is built from **the request's own URL**
(`request.url.replace(query="", scheme="https")`), which is what satisfies the
capture endpoint's same-host-https open-redirect guard on BOTH the shared URL
and the client's own domain, without the route needing to know which it is
serving.

## The 3-page ceiling, and what a 4th does

`MAX_PAGES_PER_CLIENT = 3`, included in every base package **regardless of
tier** (a business decision). Enforced in `landing_pages.create_page` —
**server-side, before any LLM call**, so a client at the limit never burns a
generation to be told no. The dashboard's own check is a courtesy, not the
control.

A 4th page is **blocked, and priced by a human**:
`request_extra_page()` records the request, alerts Johnny (with an explicit
"THIS IS A PRICING DECISION — nothing was created or quoted"), and tells the
client in chat that the team will come back to them. Nothing in code prices,
approves or auto-creates it — the same principle as the ads agents refusing to
infer a budget. Johnny's path after deciding is
`POST /api/landing-pages/admin/create`, which goes through the same ceiling, so
granting an extra page stays a deliberate act.

## Content generation reuses what already exists

No parallel research or copywriting path was built:
- `core/competitor_research.py` through the **`"ads"` lens** — a landing page
  and an ad are the same job (one offer, one action), so they share the lens
  AND the cache entry. A client who had a campaign drafted this week pays
  nothing extra here.
- `seo_agent._business_context` for who the client is — reused, as
  media_agent and website_agent already reuse it.

`COPY_SYSTEM` bans invented facts (prices, guarantees, years in business,
review counts, "מספר 1") and urgency theatre. A fabricated claim on a page
carrying the client's name is the worst failure this agent can produce.

## All clarification goes through the existing chat

Choosing the offer, reviewing copy, confirming DNS — every one surfaces as a
`dashboard_chat` communication via `_chat()`, the same channel
website_agent's self-provisioning uses. **There is deliberately no email, no
standalone form and no out-of-band channel in this feature.**

The website specialist persona (אורי) also reads `landing_pages` via
`dashboard_payload` in `support_agent._website_reads` — a PURE READ (it lists,
it cannot create/publish/price), which keeps the persona path's
read-only-by-construction invariant intact. Adding an action-capable function
to a persona's reads is a bug by definition.

## Endpoints

Public: `GET /lp/{client_segment}/{page_slug}`, `GET /_uallak-verify`.
Client (session): `GET|POST /api/client/landing-pages`,
`POST /api/client/landing-pages/{id}` (edit),
`POST /api/client/landing-pages/{id}/publish`,
`POST /api/client/landing-pages/{id}/regenerate`,
`DELETE /api/client/landing-pages/{id}`,
`POST /api/client/landing-pages/request-extra`,
`POST /api/client/landing-domain`, `POST /api/client/landing-domain/verify`.
Admin (X-Admin-Key): `POST /api/landing-pages/admin/create`.

## Setup required before custom domains work (NOT done)

Phase A (the shared URL) works the moment the migration runs. Phase B needs:

1. **Migration**: `migrations/2026-08-01-landing-pages.sql` in the Supabase SQL
   editor. Until then every landing endpoint answers `ERR_LANDING_UNAVAILABLE`
   and the section shows its honest empty state.
2. **`lp.uallak.com`** — a Cloudflare-proxied (orange cloud) record in the
   uallak.com zone fronting this app. Note this is the OPPOSITE of the
   grey-cloud rule in `HANDOFF-domain-split.md`: those records must stay
   unproxied so Google can issue certs; this one must be proxied because
   Cloudflare is doing the TLS.
3. **Cloudflare for SaaS enabled** on the zone + env vars
   `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ZONE_ID` (add to `keys_agent.KEYS`),
   optional `LANDING_FALLBACK_ORIGIN` (defaults `lp.uallak.com`).
4. **A Host-rewrite Worker.** Cloud Run 404s a request whose Host it doesn't
   recognise, and Host-header override is not on Cloudflare's free plan — so a
   small Worker on the custom hostnames must rewrite Host to the Cloud Run
   service and map `/{slug}` → `/lp/{client_segment}/{slug}`. See
   `HANDOFF-landing-pages.md` for the runbook.

Until 2–4 exist the flow is **honest rather than broken**: clients see their
exact DNS record, verification reports `uallak_side_pending`, and pages keep
serving on the shared URL.
