# uallak — Go-Live Checklist (Domain Switch + PayPal Live)

Purpose: Reference list for the day we switch from the temporary Cloud Run URL
to the real domain, and from PayPal Sandbox to Live. Keep this updated as new
integrations are added.

## 🌐 Domain Switch (temp URL → app.uallak.com)

**The domain is SPLIT (decided 2026-07-28):** `uallak.com` + `www` are the
WordPress marketing site (provisioned through our own website agent on
InstaWP — see `HANDOFF-domain-split.md`); the app lives entirely at
`app.uallak.com`. The app serves no routes on the root domain, so every URL
below is an `app.` URL — sending an OAuth redirect or an email login link to
the bare `uallak.com` now lands on WordPress and fails.

Prerequisite: the `super-system-proxy` service in `proxy/` deployed to a
region that supports domain mappings (me-west1 does not), with
`app.uallak.com` mapped to it. This replaces the earlier "Load Balancer or
region migration" blocker — the proxy is free and needs no LB.

### 1. Core app config

- [ ] Update `PUBLIC_APP_URL` in Cloud Run env vars to `https://app.uallak.com`
      (drives OAuth redirect_uri building in meta_service/tiktok_service/
      google flows AND the links inside login-code/payment emails — one var,
      all of them). The code default is already `https://app.uallak.com`, so
      this is only needed if the var is currently set to the run.app URL.
      **Do not set it until the DNS record is live and serving** — every
      login email and OAuth callback breaks the moment it points at a domain
      that doesn't resolve yet.

### 2. Redirect URIs to update manually, per platform

- [ ] Google Cloud Console — OAuth Client → Authorized redirect URIs. ONE
      OAuth client now serves FIVE separate consents (each its own
      `client_accounts` platform row, different scopes): Google Ads
      (`/api/oauth/google-ads/callback`), GTM
      (`/api/oauth/gtm/callback`), YouTube
      (`/api/oauth/youtube/callback`), Merchant Center
      (`/api/oauth/merchant-center/callback`) — all four redirect URIs must
      be registered on the SAME OAuth client, and all four break together
      if `PUBLIC_APP_URL` and the registered URIs ever drift apart.
- [ ] Meta App Dashboard — Facebook Login for Business → Valid OAuth
      Redirect URIs
- [ ] TikTok Developer Portal — App settings → Redirect URI
- [ ] PayPal Developer Dashboard — return URLs (checkout/webhook), if
      URL-bound

### 3. Third-party app settings referencing the old URL

- [ ] Meta App — Privacy Policy URL, App Domain (currently pointing at temp
      URL)
- [ ] TikTok App — any URL fields set during app creation
- [ ] Any InstaWP / WordPress template settings referencing the temp URL

### 4. Verify after switch

- [ ] Full OAuth connect test — Google, Meta, TikTok — from a real client
      account
- [ ] Email links (login codes, payment confirmations) point to the new
      domain
- [ ] `/login`, `/dashboard`, `/admin`, `/chat`, `/terms` all resolve
      correctly on `app.uallak.com` (i.e. through the proxy)
- [ ] Log in and reload — proves the session cookie survives the proxy hop,
      the one thing a plain `/health` check cannot tell you
- [ ] Run one full proposal build through the proxy — it is the longest
      request in the system and the one most likely to hit a timeout that
      the direct run.app URL never showed

### Explicitly NOT affected by the domain switch

- Cloud Scheduler jobs — they hit the `*.run.app` service URL directly and
  keep working through a domain switch; no need to repoint them (the proxy
  just fronts the same service). Leave them on run.app deliberately: routing
  them through the proxy would add a hop and a failure mode for no benefit.
- HeyGen / ElevenLabs / Higgsfield / InstaWP / Green API — key-based, no
  redirect URIs or stored callback URLs on their side.

## 🔒 Google OAuth app verification (business-decision gate, tracked here)

Three sensitive-scope consents were added this week (GTM, YouTube, Merchant
Center — `.../auth/tagmanager.*`, `.../auth/youtube.*`, `.../auth/content`),
each on top of the existing Google Ads `adwords` scope. Until Google's OAuth
verification review (consent-screen review + scope justifications) is
submitted and approved for ALL of them:

- [ ] Every affected consent screen shows Google's "unverified app" warning
- [ ] Only up to 100 TEST USERS (added in Cloud Console) can actually
      complete these consents — real clients cannot connect until approved
- [ ] Submit the verification request (bundle all three scope additions
      into one review where possible) well before counting on any of
      GTM/YouTube/Merchant Center being client-facing

This is independent of the domain/PayPal switch — it can (and should) be
done well before go-live day, since development/testing needs it too.

## 💳 PayPal: Sandbox → Live

### 1. New Live credentials

- [ ] Create/verify Live PayPal Business account
- [ ] Get Live `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` (different from
      Sandbox)
- [ ] Update Cloud Run env vars with Live credentials
- [ ] **CODE CHANGE (easy to forget — this is not an env var):**
      `core/paypal_service.py` line ~10 hardcodes
      `BASE_URL = "https://api-m.sandbox.paypal.com"` — change to
      `https://api-m.paypal.com` and deploy. Live credentials against the
      sandbox URL fail with `invalid_client`, which looks exactly like the
      known stale-Sandbox-app trap (see CLAUDE.md "Known traps") — don't
      debug the wrong thing on go-live day.

### 2. Webhook

- [ ] Register a new Live webhook in PayPal dashboard (Sandbox and Live
      webhooks are separate)
- [ ] Subscribe to the same event types as Sandbox:
      `BILLING.SUBSCRIPTION.ACTIVATED`, `BILLING.SUBSCRIPTION.RE-ACTIVATED`,
      `PAYMENT.SALE.COMPLETED`, `BILLING.SUBSCRIPTION.CANCELLED`,
      `BILLING.SUBSCRIPTION.PAYMENT.FAILED`, `BILLING.SUBSCRIPTION.SUSPENDED`,
      `INVOICING.INVOICE.PAID`, `INVOICING.INVOICE.CANCELLED`
      (the last two are new as of 2026-07-23 — invoice payment tracking for
      setup fees; **also add them to the SANDBOX webhook now**, not just at
      go-live, so this is actually testable before then)
- [ ] Update `PAYPAL_WEBHOOK_ID` in Cloud Run

### 3. Migration notes (verified against the code)

- Products/billing plans are created dynamically per checkout
  (`create_subscription` → products + `create_plan` on the fly) — nothing to
  migrate or pre-create on the Live account.
- Existing Sandbox subscriptions do NOT carry over to Live. Every
  development-era subscription dies with the switch — fine, those belong to
  the test clients being deleted below, but confirm no real client was ever
  checked out through Sandbox first.

### 4. Verify after switch

- [ ] One real, small live transaction end-to-end before relying on it for
      real clients
- [ ] Confirm webhook events actually arrive (check Cloud Run logs)

## 🧹 Also worth doing on go-live day

- [ ] Delete all test/demo clients created during development
      (Johnny/דני/מיכל/אבי/רון etc.) from Supabase
- [ ] Confirm RLS is enabled on all Supabase tables (re-check via Supabase
      security advisor)
- [ ] Double-check `ADMIN_KEY`/`ADMIN_PASSWORD` are strong and not reused
      anywhere else

---

Add new rows to this list whenever a new integration with its own redirect
URI or sandbox/live split is added.
