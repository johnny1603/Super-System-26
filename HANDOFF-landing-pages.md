# In-code landing pages (2026-08-01)

A new dashboard area for lightweight, conversion-focused landing pages —
separate from the WordPress site `website_agent` builds.

## Current state before this work

Nothing like it existed: no landing-page concept anywhere in the code. One
thing did exist, and it matters — **`onboarding_agent.py:443` already promises
"Landing page" in every standard setup package**. That promise has never been
deliverable. This closes it, more generously (3 included instead of 1).

## Hosting decision

| Option | Verdict |
|---|---|
| A Cloudflare Pages project per client/page | Never on the table — the ~30-project ceiling is exactly what the brief warned about. |
| ONE Cloudflare Pages project | Rejected: a deploy per page edit, leads crossing back to our API, a second platform to operate and debug. |
| **ONE route on the existing Cloud Run app** | **Chosen.** `/lp/{client_slug}/{page_slug}`. Zero new infrastructure, zero new cost, works the moment the migration runs. The project-count ceiling is avoided by not using Pages at all. |

For the **client's own subdomain**, checked rather than assumed:

- **Cloud Run domain mappings — REJECTED.** They require the CLIENT's base
  domain to be verified in Google Search Console **under our Google account**:
  the client adds a TXT record *and* adds us as a verified owner, then we run a
  gcloud command per client. That contradicts "one record, forwarded as-is, no
  understanding required." Also still unavailable in me-west1 (why `proxy/`
  exists).
- **Cloudflare for SaaS — CHOSEN.** One CNAME from the client; Cloudflare
  issues and renews the cert for their hostname. **100 custom hostnames free on
  the Free plan**, $0.10/hostname/month beyond — free to 100 clients. uallak.com
  is already a Cloudflare zone.

## What was built

- `migrations/2026-08-01-landing-pages.sql` — the `landing_pages` table. The
  per-client landing DOMAIN needs no table: it reuses `client_accounts`
  (`platform='landing_domain'`).
- `core/landing_pages.py` — data, the 3-page ceiling, lead attribution, and the
  escaping renderer.
- `core/landing_domains.py` — the custom-domain state machine, DNS instruction
  generator, and end-to-end verification.
- `agents/landing_page_agent.py` — research-grounded copy, the lifecycle, and
  every client-facing message.
- `core/api_server.py` — the public `/lp/...` + `/_uallak-verify` routes
  (registered before the root mount) and the client/admin API.
- `dashboard/client/index.html` — the דפי נחיתה section, 5 languages.
- `agents/support_agent.py` — the website persona (אורי) can now see landing
  pages.

### Three decisions worth knowing

1. **Structured content only — a security boundary, not a style choice.** These
   pages serve from app.uallak.com, *the same origin as the dashboard session
   cookie*. Stored raw HTML would be stored XSS against every logged-in client
   and admin. `content` holds strings; the template escapes them. A "custom
   HTML" feature would have to serve from a different origin.
2. **Verification is end-to-end, not a DNS lookup.** A resolving CNAME with no
   certificate still shows the visitor a browser warning, so we fetch
   `https://{hostname}/_uallak-verify` and check the marker. Needs no
   `dnspython` (not a dependency) and no secret.
3. **A 4th page is blocked and priced by a human.** `request_extra_page()`
   records it, alerts Johnny with "THIS IS A PRICING DECISION — nothing was
   created or quoted", and tells the client the team will reply in chat.
   Nothing in code prices or grants it. Same principle as the ads agents
   refusing to infer a budget.

### Reuse, not rebuild (handoff point 5)

- Lead capture: the SAME `create_lead_capture_token` and
  `POST /api/leads/capture/{token}` from the auto-wiring work. No second
  capture path. Per-page attribution rides on `client_leads.source_detail`
  (`lp:{slug}`) — **no schema change**.
- Copy: `core/competitor_research.py` through the **`"ads"` lens** (a landing
  page and an ad are the same job, so they share the lens *and* the cache
  entry) plus `seo_agent._business_context`. No parallel research or
  copywriting path.
- Clarifications (point 7): every one goes through `dashboard_chat` via
  `_chat()` — the channel website_agent's self-provisioning already uses.
  No email, no standalone form, no out-of-band channel.

## What works now vs what is dormant

| Piece | Status |
|---|---|
| Pages, copy generation, publish, lead capture + per-page counts, dashboard, 3-page ceiling, 4th-page request | **Works once the migration runs.** No new secrets, no new vendor. |
| DNS instruction generation + the forwardable chat message | **Works now** — the client can add their record immediately. |
| Custom hostname actually serving | **Dormant** until the setup below. Verification reports `uallak_side_pending` and tells the client it's on us — it does not send them to re-check a correct record. |

## Setup runbook (not done — nothing here could run from this machine)

**1. Migration.** Run `migrations/2026-08-01-landing-pages.sql` in the Supabase
SQL editor. Until then every landing endpoint returns `ERR_LANDING_UNAVAILABLE`
and the section shows an honest empty state. Nothing else breaks.

**2. `lp.uallak.com`.** Add a Cloudflare DNS record in the uallak.com zone
pointing at the app, **proxied (orange cloud)**.

> Note this is the OPPOSITE of the grey-cloud rule in
> `HANDOFF-domain-split.md`. Those records must stay unproxied so *Google* can
> issue certificates. This one must be proxied because *Cloudflare* is doing
> the TLS. Both rules are correct for their own record.

**3. Enable Cloudflare for SaaS** on the zone (SSL/TLS → Custom Hostnames), set
the fallback origin to `lp.uallak.com`, then set on Cloud Run:

```bash
gcloud run services update super-system --region me-west1 \
  --update-env-vars CLOUDFLARE_API_TOKEN=<token>,CLOUDFLARE_ZONE_ID=<zone id>
```

The token needs `Zone → SSL and Certificates → Edit` on the uallak.com zone
only. Both names are already registered in `keys_agent.KEYS`, so a missing one
shows up in the startup warning.

**4. The Host-rewrite Worker.** Cloud Run 404s a request whose Host it does not
recognise, and Host-header override is not on Cloudflare's free plan — so a
Worker on the custom hostnames must rewrite Host and map `/{slug}` to the full
path. Roughly:

```js
export default {
  async fetch(request, env) {
    const incoming = new URL(request.url);
    // lp.clientdomain.com/{slug} -> {BACKEND}/lp/{client_segment}/{slug}
    const segment = env.CLIENT_SEGMENTS[incoming.hostname]; // hostname -> "business-42"
    if (!segment) return new Response("Not found", { status: 404 });
    const slug = incoming.pathname.replace(/^\/+/, "") || "index";
    const target = incoming.pathname === "/_uallak-verify"
      ? `${env.BACKEND}/_uallak-verify`
      : `${env.BACKEND}/lp/${segment}/${slug}`;
    // fetch() sets Host from the target URL — that is the whole point
    return fetch(new Request(target + incoming.search, request));
  },
};
```

`env.BACKEND` is the Cloud Run run.app URL (not app.uallak.com — no reason for
a second proxy hop). `CLIENT_SEGMENTS` is the open question below.

## Open items (deliberately not decided here)

- **How the Worker learns hostname → client_segment.** The sketch above uses a
  static map, which means a Worker redeploy per client — acceptable at 5
  clients, wrong at 50. The clean fix is a tiny public endpoint on this app
  (`GET /api/landing-domain-lookup?host=`) that the Worker calls and caches,
  turning the Worker into pure plumbing that never needs redeploying. It is one
  endpoint plus a Worker `caches` call; not built because the Worker itself
  isn't deployed yet and building against an unrun runtime is how the last two
  handoffs accumulated their verification debt.
- **`landing_pages` in the export service.** `_export_dataset` takes one branch
  per exportable dataset; landing-page performance is a plausible export and
  was not added.
- **Analytics beyond lead counts.** Views/conversion rate would need either a
  tracking tag on the page (there is deliberately no JS on it today) or Cloud
  Run request-log parsing. Lead count per page is what `client_leads` can
  honestly support with no new moving parts.

## VERIFICATION STATUS

Written and read carefully; **not executed** — this machine has no usable
Python and none of it has run against a live client row or a live Cloudflare
zone. Same standing caveat as everything shipped from here.

Highest-risk things to check first on the deployed service:

1. **The `/lp/` route resolves at all** — it sits before the root catch-all
   mount, the ordering rule that caused a real outage once. Hit
   `/lp/anything-1/x` and confirm a 404 from the route, not the landing page's
   static HTML.
2. **The capture redirect** — submit a real form on a live page and confirm the
   visitor comes back to `?sent=1` rather than landing on raw JSON. That path
   depends on the referer-host check inside the existing capture endpoint.
3. **`ERR_LANDING_UNAVAILABLE` before the migration** — confirm the dashboard
   section degrades to its empty state rather than erroring loudly.
