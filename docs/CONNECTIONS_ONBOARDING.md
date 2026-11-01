# Connections onboarding through the chat (2026-11-01)

Code: `core/connection_catalog.py`. Tests: `tests/test_connection_catalog.py`.

## The rule

Every connection card on the client dashboard has exactly **one** button, "חבר".
It opens that connection's agent chat (`PERSONA` map) and runs one turn of
`POST /api/client/connect-chat {key, step}`:

| step | what the client sees |
|---|---|
| `start` | one question + two chips: "יש לי חשבון" / "אין לי עדיין" |
| `has` | the card's existing connect flow (OAuth redirect, the key field on the card, the site check; Google Ads first shows its staff-access consent as a confirmation card; ManyChat goes to the team on WhatsApp) |
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

## Setup

1. Run `migrations/2026-11-01-connection-catalog.sql` in Supabase (idempotent;
   comment-free copy for pasting: `catalog-for-supabase.txt`, gitignored).
2. Scheduler, daily:
   `gcloud scheduler jobs create http connection-reminders --schedule="0 10 * * *" --time-zone="Asia/Jerusalem" --uri="{SERVICE_URL}/api/connections/reminders" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}`
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
