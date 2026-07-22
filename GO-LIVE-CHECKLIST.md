# uallak — Go-Live Checklist (Domain Switch + PayPal Live)

Purpose: Reference list for the day we switch from the temporary Cloud Run URL
to uallak.com, and from PayPal Sandbox to Live. Keep this updated as new
integrations are added.

## 🌐 Domain Switch (temp URL → uallak.com)

Prerequisite: Load Balancer set up in me-west1 (or region migration) — the
actual domain-connection blocker, decided to defer until budget allows.

### 1. Core app config

- [ ] Update `PUBLIC_APP_URL` in Cloud Run env vars to `https://uallak.com`
      (drives OAuth redirect_uri building in meta_service/tiktok_service/
      google flows AND the links inside login-code/payment emails — one var,
      all of them)

### 2. Redirect URIs to update manually, per platform

- [ ] Google Cloud Console — OAuth Client → Authorized redirect URIs
      (Google Ads connection)
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
      correctly on the new domain

### Explicitly NOT affected by the domain switch

- Cloud Scheduler jobs — they hit the `*.run.app` service URL directly and
  keep working through a domain switch; no need to repoint them (the LB
  domain just fronts the same service).
- HeyGen / ElevenLabs / Higgsfield / InstaWP / Green API — key-based, no
  redirect URIs or stored callback URLs on their side.

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
      `BILLING.SUBSCRIPTION.PAYMENT.FAILED`, `BILLING.SUBSCRIPTION.SUSPENDED`
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
