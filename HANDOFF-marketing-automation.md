# Marketing automation (ManyChat / Make) — a deliberately manual service

Shipped 2026-08-08. Read this before touching `core/automation_service.py`, the two
automation cards in the client dashboard, or the automation section of the admin drawer.

## Why this one is not built like the others

Every other integration in the dashboard (Google Ads, Meta, TikTok, YouTube, Merchant
Center, WordPress, Higgsfield, HeyGen/ElevenLabs) is a **self-service connect flow** with
a price the system already knows. ManyChat and Make are neither, on purpose:

- **No fixed price.** Johnny runs a personal needs assessment with the client over the
  existing WhatsApp support channel, and only then prices and builds the automation
  himself. Nothing in the system may quote a number for it.
- **No self-service setup.** No OAuth, no client-facing credential form, no "connect"
  button anywhere.

So the whole feature is: explain it, route to a human, and give Johnny a place to keep
the login details the client hands him.

## The three surfaces

**1. Client dashboard — informational cards only** (`dashboard/client/index.html`, in the
connections grid). Two cards with no connect action. Each has:
- a paragraph (`.conn-note`, grey — NOT `.lock-note`, which is accent-orange and reads as
  a warning at paragraph length) saying this is built to fit and priced after a conversation
- **"דברו איתנו על זה"** → `askAboutAutomation(vendor)` opens WhatsApp with a prefilled
  message. The number comes from `/api/client/support` (the same `app_settings` value the
  תמיכה view uses), cached in `supportLinkPromise` so the home view doesn't refetch per click
- **"יש לי שאלה — בצ׳אט"** → `chatAboutAutomation(vendor)` opens the **general concierge**
  window and sends the question. Deliberately not a specialist: automation is not ads, site
  or media, and the specialists' "stay in your lane" rule already redirects them here

**2. Support chat — `_AUTOMATION_RULE` in `agents/support_agent.py`.** The load-bearing
line is: **never set `upgrade_request` for an automation question.** That field routes to
`build_proposal`, which would invent a price for something priced by hand. The rule also
carries the two-step instruction the client gets if they want to proceed, including the
signup warning below.

**3. Admin drawer — the credentials** (`dashboard/admin/index.html`,
`loadAutomationSection`). Username + password per vendor, a copy button for each, a direct
link to the vendor's login page, and a signup link while nothing is stored. This is the one
place in the system that shows a stored password in the clear — that is the entire point of
the card, and it is admin-session gated.

## The Google warning is operational, not stylistic

`automation_service.SIGNUP_RULE_HE` tells the client to open their ManyChat/Make account
with **an email and password, not "Sign in with Google"**. Johnny signs in to that account
later to build and maintain the automation; a Google-federated account turns that into a
fight with Google's own auth. Keep this wording in one place — the support prompt quotes it.

## Storage

No new table. One `client_accounts` row per vendor, reusing the pattern
`seo_agent.connect_tool` and the WordPress Application Password already use:

| column | holds |
|---|---|
| `platform` | vendor slug (`manychat` / `make`) |
| `account_id` | username / email |
| `access_token` | password |

`upsert_account` replaces the row rather than stacking a second one, so corrected
credentials can't leave a stale password behind. `remove_accounts` deletes it outright.

**Credentials are admin-only.** `/api/client/dashboard` already strips stored tokens before
it answers the browser (it returns only `platform` + `status`), and nothing in
`automation_service` is reachable from a client-session endpoint. Keep it that way: the
three endpoints are `GET`/`POST` `/api/admin/clients/{id}/automation` and
`DELETE /api/admin/clients/{id}/automation/{vendor}`, all behind `_require_admin`.

## Adding a vendor

One entry in `automation_service.VENDORS` (slug, name, login_url, signup_url, Hebrew
one-liner). The admin UI and the support prompt read from it. The client-side card is the
one thing that is NOT generated from it — the cards are static HTML like every other
connection card, because they need `data-i18n` keys in five languages.

## Where it shows up in a proposal

`build_proposal` may **mention** automation in `goals_90_days` when it genuinely fits the
business (they handle enquiries in DMs, or they described losing leads to slow follow-up) —
strictly as a possibility to explore with the team, never with a price, and never in
`recommended_services`, `setup_fee_breakdown`, `monthly_breakdown` or any total. The
generic `PRICING["automation"]` setup fee is a different thing: that is ordinary
bot/CRM-wiring scope inside a package, and it stays priced as it always was.
