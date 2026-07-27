# HANDOFF — uallak.com domain split (marketing site + app)

**Target state**

| Destination | Serves | How |
|---|---|---|
| `uallak.com` + `www.uallak.com` | WordPress marketing site | InstaWP site provisioned through our own `website_agent`, custom domain mapped in the InstaWP dashboard |
| `app.uallak.com` | the existing Super System app, unchanged | `super-system-proxy` (this repo's `proxy/`) in a mapping-capable region, reverse-proxying to `super-system` in me-west1 |

Nothing in the app itself moves, changes region, or gets rewritten. The only
app-side change is which URL it calls itself (`PUBLIC_APP_URL`).

---

## What is already done in this repo

- **`proxy/`** — the complete reverse-proxy service (`main.py`,
  `requirements.txt`, `Dockerfile`). Not deployed.
- **`PUBLIC_APP_URL` defaults repointed** from `https://uallak.com` to
  `https://app.uallak.com` in all nine places that read it:
  `core/api_server.py`, `google_ads_service.py`, `gtm_service.py`,
  `email_service.py`, `merchant_center_service.py`, `meta_service.py`,
  `paypal_service.py`, `tiktok_service.py`, `youtube_service.py`.
  There were **no hardcoded `*.run.app` URLs anywhere in the codebase** — every
  self-referential URL already flowed through this one env var, so this is the
  complete change. (`GMAIL_USER = johnny_support@uallak.com` in
  `email_service.py` is an email address, not a web URL — unaffected by the
  split, it depends on uallak.com's MX records only.)
- **`marketing-site/uallak-pages.json`** — the marketing content, ready to POST
  to `/api/website/populate`. See `marketing-site/README.md`.
- Docs updated: `GO-LIVE-CHECKLIST.md`, `.claude/skills/deploy/SKILL.md`,
  `.claude/skills/google-ads/SKILL.md`.

## What is NOT done, and why

This dev machine has no gcloud, no Python and no access to `ADMIN_KEY` /
`INSTAWP_API_KEY`, so **neither the provisioning call nor the proxy deploy
could be executed from here.** Both are runbooks below, to run in Cloud Shell
(gcloud) and against the deployed API (curl). Every command is written out in
full — nothing is left to improvise.

---

## STEP 1 — WordPress for uallak.com, via our own website agent

### 1.0 Prerequisites (check these first — the call fails cheaply if any is missing)

The `provision_site` flow refuses immediately unless the one-time InstaWP
master-template setup from the `website` skill is done:

- [ ] uallak master template exists on InstaWP and is saved as a template
- [ ] Cloud Run env on `super-system`: `INSTAWP_API_KEY`,
      `WEBSITE_TEMPLATE_APP_PASSWORD`, `WEBSITE_TEMPLATE_SLUG`,
      `WEBSITE_TEMPLATE_WP_USERNAME` (defaults to `uallak`)

Verify in one shot:

```bash
gcloud run services describe super-system --region me-west1 \
  --project super-system-500410 \
  --format="value(spec.template.spec.containers[0].env)" | tr ',' '\n' | grep -i -E 'INSTAWP|WEBSITE_TEMPLATE'
```

If that comes back empty, stop — the master template has to be built first
(the `website` skill's "One-time manual setup" section is the checklist).

### 1.1 uallak needs a `clients` row

`provision_site(client_id, ...)` is client-scoped: it stores the resulting
WordPress connection as a `client_accounts` row keyed by `client_id`. Treating
uallak as its own client (your instruction) therefore means giving uallak a
real row. In Supabase → `clients`, insert one:

- `business_name`: `uallak`
- `email`: `johnny_support@uallak.com`
- status/other columns: match whatever an existing client row uses

Note the returned `id` — everything below calls it `$UALLAK_CLIENT_ID`.

**Two consequences to accept deliberately**, because this row is a real client
row and the system will treat it as one:
1. It will appear in the admin dashboard's client list and in cross-client
   scans (the daily website health scan will scan uallak.com — which is
   actually useful).
2. It has no subscription and no proposal, so the **self-service** provisioning
   button would refuse it (`ERR_WEBSITE_NOT_IN_PACKAGE`, fail-closed billing
   gate). That is why the runbook uses the **admin** endpoint below — which
   skips that gate by design, for exactly this manual-override case.

### 1.2 Provision the site

```bash
export SERVICE_URL="https://super-system-21220555911.me-west1.run.app"
export ADMIN_KEY="<the X-Admin-Key value from Cloud Run env>"
export UALLAK_CLIENT_ID=<id from 1.1>

curl -sS -X POST "$SERVICE_URL/api/website/provision" \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"client_id\": $UALLAK_CLIENT_ID, \"site_name\": \"uallak\", \"industry_hint\": \"שיווק דיגיטלי\"}"
```

This clones the master template with `is_reserved: true` — **billable from
that moment** (~$5/mo InstaWP Starter). It can take minutes; let it finish.

> **If it times out, do NOT just re-run it.** Open the InstaWP dashboard and
> check for a half-created site first — a reserved site bills until deleted.
> This is the one genuinely expensive mistake available in this runbook.

On success the response carries the new `wp_url` (an `*.instawp.xyz`
subdomain) and the site is already stored as uallak's `wordpress` connection,
with a freshly minted per-site Application Password. No credentials to copy
anywhere.

### 1.3 Load the marketing content

```bash
# put the real client_id into the payload, then POST it
sed "s/\"client_id\": 0/\"client_id\": $UALLAK_CLIENT_ID/" \
  marketing-site/uallak-pages.json > /tmp/uallak-pages.json

curl -sS -X POST "$SERVICE_URL/api/website/populate" \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/uallak-pages.json
```

Eight pages, all created as **drafts** (the agent's standing rule — a human
publishes). Review them in wp-admin and publish. See
`marketing-site/README.md` for what each page is and the two content
decisions worth knowing about.

### 1.4 Point uallak.com at the site (manual, InstaWP side)

Custom-domain mapping is a manual InstaWP-dashboard step — automating InstaWP's
domain API is explicitly deferred in the `website` skill, and this one site
isn't a reason to build it.

In the InstaWP dashboard → the new site → **Domains** → add `uallak.com` and
`www.uallak.com`. InstaWP then displays the DNS records it needs and issues
free SSL once they resolve.

**The records it asks for are read off that screen — they are not predictable
from here**, and they are per-site. Expect the shape to be either an `A` record
to the site's IP or a `CNAME` to an InstaWP hostname, plus the `www` variant.
Copy them verbatim into Cloudflare.

> **Cloudflare: set these to "DNS only" (grey cloud), not proxied**, at least
> until InstaWP reports SSL as issued. A proxied record breaks the HTTP-01
> certificate challenge and the site will serve a certificate error.

---

## STEP 2 — app.uallak.com via the proxy

### 2.0 Why a proxy at all

`gcloud run domain-mappings` is not offered in `me-west1`. The two documented
alternatives were an HTTPS Load Balancer (costs money, deferred for budget
reasons) or moving the service out of Tel Aviv (latency + migration risk). A
second Cloud Run service in a mapping-capable region that forwards to
me-west1 costs nothing extra beyond its own request time, and touches the app
not at all.

**Region: `europe-west1` (Belgium).** It supports domain mappings and is the
closest such region to Israel — roughly 60–70 ms each way, versus 150 ms+ for
`us-central1`. Since every request now makes the visitor→proxy→me-west1 trip,
that difference is the whole cost of this workaround. Don't pick us-central1.

### 2.1 Verify domain ownership (do this first — the mapping refuses without it)

`app.uallak.com` can only be mapped if `uallak.com` is verified for the Google
account running the command. Check:

```bash
gcloud domains list-user-verified
```

If `uallak.com` is not listed, verify it at
https://search.google.com/search-console — it will ask for a `TXT` record on
`uallak.com`. Add it in Cloudflare (this one is unrelated to the InstaWP
records and coexists with them fine).

### 2.2 Deploy the proxy

From Cloud Shell, in the repo root after pulling latest:

```bash
gcloud config set project super-system-500410

gcloud run deploy super-system-proxy \
  --source proxy \
  --region europe-west1 \
  --allow-unauthenticated \
  --timeout 300 \
  --concurrency 200 \
  --memory 512Mi \
  --set-env-vars BACKEND_URL=https://super-system-21220555911.me-west1.run.app,PUBLIC_ORIGIN=https://app.uallak.com
```

`--source proxy` builds from the `proxy/` directory only — its own Dockerfile
and its own three dependencies. It never pulls in the app.

Smoke-test it on its own run.app URL, before any DNS exists:

```bash
PROXY_URL=$(gcloud run services describe super-system-proxy \
  --region europe-west1 --format='value(status.url)')

curl -sS "$PROXY_URL/_proxy/health"     # -> ok        (the proxy itself)
curl -sS "$PROXY_URL/health"            # -> the APP's /health, through the proxy
curl -sSI "$PROXY_URL/login" | head -20 # -> 200 + the login page's headers
```

If the second one returns the app's health payload, the proxy works.
`--concurrency 200`: every request here is spent idle waiting on me-west1, so
one instance should carry many at once — the default of 80 would scale out
sooner and more expensively than needed.

### 2.3 Map the domain

```bash
gcloud beta run domain-mappings create \
  --service super-system-proxy \
  --domain app.uallak.com \
  --region europe-west1
```

The command prints the DNS record(s) to create. For a subdomain this is
normally a single **`CNAME` → `ghs.googlehosted.com.`**, but take the values
from the command's own output rather than from this document. To re-print them
later:

```bash
gcloud beta run domain-mappings describe \
  --domain app.uallak.com --region europe-west1 \
  --format="value(status.resourceRecords)"
```

> **Cloudflare: "DNS only" (grey cloud) here too.** Google issues and renews
> the managed certificate for app.uallak.com itself, and needs to see the real
> request to do it. Proxying breaks issuance, and once issued adds a second TLS
> hop for no gain.

Certificate provisioning takes ~15 minutes to a few hours after the record
resolves. `gcloud beta run domain-mappings describe --domain app.uallak.com
--region europe-west1` shows the status.

---

## STEP 3 — DNS records to add in Cloudflare

Two of the three come from a screen/command rather than from here, and both are
noted as such above. Consolidated:

| Name | Type | Value | Source | Proxy status |
|---|---|---|---|---|
| `uallak.com` | A or CNAME | *from the InstaWP Domains screen* | Step 1.4 | **DNS only** |
| `www` | A or CNAME | *from the InstaWP Domains screen* | Step 1.4 | **DNS only** |
| `app` | CNAME | `ghs.googlehosted.com.` *(confirm against the command output)* | Step 2.3 | **DNS only** |
| `uallak.com` | TXT | *Google Search Console verification string* | Step 2.1 | n/a |

Existing `MX` / mail records for uallak.com are untouched by any of this —
`johnny_support@uallak.com` keeps working throughout.

---

## STEP 4 — after DNS resolves (order matters)

Do these **only once `https://app.uallak.com/health` actually answers**. Setting
them earlier points live login emails and OAuth callbacks at a domain that
doesn't resolve yet.

1. **Set the env var**, so the app builds URLs with the new domain:
   ```bash
   gcloud run services update super-system --region me-west1 \
     --update-env-vars PUBLIC_APP_URL=https://app.uallak.com
   ```
2. **Re-register every OAuth redirect URI** as `https://app.uallak.com/...` —
   the full per-platform list is section 2 of `GO-LIVE-CHECKLIST.md` (one
   Google OAuth client with four callbacks, Meta, TikTok, PayPal). These break
   the moment `PUBLIC_APP_URL` and the registered URIs disagree.
3. **Verify**, per `GO-LIVE-CHECKLIST.md` section 4 — in particular log in and
   reload (proves the session cookie survives the proxy hop) and run one full
   proposal build (the longest request in the system, and the one most likely
   to expose a timeout the direct run.app URL never showed).

**Leave the Cloud Scheduler jobs pointing at the run.app URL.** They work
today, and routing them through the proxy would add a hop and a failure mode
for no benefit.

---

## Known open items (deliberately not decided here)

- **`app.uallak.com/` still serves the app's own landing page**, which is now
  the same marketing message as the WordPress home page — two URLs, one
  message, i.e. duplicate content competing in search. The clean fix is a
  redirect from the app's `/` to `https://uallak.com/` (or a `noindex` on it),
  but that changes app behaviour and is a call to make once the WordPress site
  is actually live. Not changed.
- **Proxy cold starts.** Deployed with `min-instances=0` (implicit), so an idle
  proxy adds a container start to the first request. Add
  `--min-instances=1` if that's noticeable — it costs a few dollars a month.
- **The proxy is HTTP-only, on purpose.** The app has no WebSocket or SSE
  endpoint (verified — nothing in the codebase opens one). If one is ever
  added, this proxy needs a websocket route or that feature will silently fail
  on app.uallak.com while working on the run.app URL.
- **The proxy has never been run** — no Python on the dev machine. It was
  written against the httpx/Starlette APIs by careful reading, same
  verification status as any other change shipped from this machine. The
  smoke tests in 2.2 are the real check, and they run before any DNS points
  at it.
