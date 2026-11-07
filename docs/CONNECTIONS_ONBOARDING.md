# Connections onboarding through the chat (2026-11-01)

Code: `core/connection_catalog.py`. Tests: `tests/test_connection_catalog.py`.

## The rule

Every connection card on the client dashboard has exactly **one** button, "חבר".
It opens that connection's agent chat (`PERSONA` map) and runs one turn of
`POST /api/client/connect-chat {key, step}`:

| step | what the client sees |
|---|---|
| `start` | one question + two chips: "יש לי חשבון" / "אין לי עדיין" |
| `has` | the card's existing connect flow (OAuth redirect, the key field on the card, the site check; Google Ads first shows its staff-access consent as a confirmation card; ManyChat goes to the team on WhatsApp). The store has no chips: one click (below) |
| `none` | sign-up link + short Hebrew steps; **paid tier** adds the plan link, one recommended plan and "paid directly to the vendor, never to uallak" + a "סיימתי" chip. The full-website system (`website`) offers our own build instead, because uallak hosts and bills it (Step 0: InstaWP on our workspace, passed through in PRICING) |
| `done` | verifies (`client_accounts` platform or `vendor_tools`) → "מחובר ✓"; not yet → ONE reminder after 2 days (`app_settings conn_reminder:<client>:<key>`, sent by `GET /api/connections/reminders`, X-Admin-Key, daily). Connections the system cannot verify go to the team |

Included-tier keys answer `show: false`: nothing appears.

## Links: the table, nothing else

`connection_catalog` (key, tier, signup_url, pricing_url, recommended_plan_note,
help_notes, enabled) is the only place a vendor link lives. The module has no
URL; `connection_help.for_agent` / `strip_foreign_links` (the concierge) read
the same rows. Only `https://` links reach a client. A missing row, a disabled
row or an empty sign-up link → human support (WhatsApp link + one staff alert),
never a guess. The owner edits rows at `/admin/connections/` (no deploy, audited).

## Keys

Keys are pasted only in the card's own field (`/api/client/tools/{tool}/connect`
→ encrypted `client_accounts`). The chat flow never receives, echoes or logs a
key; staff viewing as the client cannot run it.

## The online store — no technical work, ever (2026-11-07)

The store is the exception to the two-chip flow above. Details: docs/STORE.md.

- **Default: nothing to connect.** Order totals come from data we already
  have: our own purchase records (the purchase measurement we publish in their
  Tag Manager also tells us order number, total and currency) and, once
  Analytics is approved, GA4's ecommerce numbers. With neither, אורי says so
  in one line and offers to set up purchase measurement (`configure_conversion`).
- **Deeper order data (one order, statuses, refunds): ONE click.** The store
  card is hidden until it's needed (connected, or the chat offers
  `connect_store`). It has one button, "חיבור החנות בקליק אחד", and one line:
  read-only, revocable. The click opens the store's own approval screen
  (WooCommerce's built-in authorization); the client logs in to their store
  and presses Approve; the store sends a read-only key to our server. The
  `store` chat turn has no chips and no steps.
- **Never:** a key field, a key in the chat, or wp-admin menus walked
  through. The old "create a REST key" steps (`STORE_STEPS`) are gone.
- **Team fallback only:** `/admin/connections/` → "חנות — חיבור ידני": a read
  key the team has, or the website connection (`POST /api/admin/store/connect`,
  admin session).
- **Future, not built:** for sites we build or host (InstaWP or similar), we
  connect the store from our side with no client action at all; a small uallak
  WordPress plugin could do the same for any site later.

## Setup

1. Run `migrations/2026-11-01-connection-catalog.sql` in Supabase (idempotent;
   comment-free copy for pasting: `catalog-for-supabase.txt`, gitignored).
2. Scheduler — one command in Cloud Shell, from the repo root, creates or
   updates every job (chat-rollover, chat-rollover-catchup,
   connection-reminders daily 10:00, landing-hosting-costs monthly and PAUSED):
   `bash scripts/schedule_jobs.sh`
   It reads the admin key from Secret Manager itself and never prints it;
   `--dry-run` shows the commands with the key masked. The routes are GET.
3. Until the table exists every "אין לי" goes to human support; "יש לי" works.

## Seed links verified live (2026-11-01)

Verified on the vendor's own site: Google Ads, YouTube, Tag Manager, Google
account sign-up (Drive, Gmail), Search Console, Analytics, Merchant Center,
TikTok, ManyChat (sign-up + pricing), ElevenLabs (sign-up + pricing),
Creatomate (sign-up + pricing), Higgsfield pricing, Uppbeat pricing.

Seeded empty + disabled (fill in on `/admin/connections/`):
- **Higgsfield sign-up**: no public sign-up URL (sign-up is a dialog).
- **Uppbeat sign-up**: `/sign-up` is a 404; sign-up is a dialog.
- **Meta**: not in the seed list; no link verified.
