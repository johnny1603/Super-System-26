# Claude credits widget (admin only, 2026-11-09)

Code: `core/anthropic_credits.py`, `dashboard/assets/credits-widget.js`.
Tests: `tests/test_anthropic_credits.py`.

## What the owner sees

On every admin page (main admin, Control Room tabs, `/admin/operator/`,
`/admin/models/`, `/admin/landing/`, `/admin/connections/`), in the header:

- a status dot + "estimated remaining · days left";
- a permanent **הטענת קרדיט** link → `https://console.anthropic.com/settings/billing/`
  (new tab, `rel="noopener noreferrer"`).

Clicking the indicator opens the panel:

| Line | Where it comes from |
|---|---|
| Last balance entered (+ time) | `anthropic_credit_balances`, the owner's own entry |
| Spend since then | measured: `client_costs` Claude rows since that timestamp |
| Estimated remaining | balance − spend since |
| Last 24 h / 7 d | measured |
| Average daily burn | last 7 days ÷ 7 |
| Days left | remaining ÷ burn ("—" when nothing was spent this week) |

Plus the **balance after top-up** field (USD; the server stamps the time),
the yellow / red thresholds, the last 5 entries, and the note: "Estimate based
on our own measurement; the Anthropic Console is the source of truth. Enabling
Auto reload there is recommended."

Nothing is drawn without the admin session (the API answers 401 → the script
draws nothing). No client page loads the script (tested).

## The measurement — and its limits

- No Anthropic call, no new key. "Claude spend" = every `client_costs` row
  with token counts (`details.input_tokens` / `output_tokens`) or web searches —
  what `core/claude_json`'s two choke points and
  `cost_tracker.record_claude_response` write. USD = `details.usd` (list prices
  in `cost_tracker.MODEL_PRICES_USD_PER_MTOK`, cache multipliers, web search
  fee). Drive egress, Places, landing hosting and client-paid media are other
  vendors and are excluded.
- It is an estimate: list prices (no discounts or tax), and any use of the
  same Anthropic account outside this app (the Console workbench, another key)
  is invisible. Enter the Console's real balance after each top-up to reset the
  drift. A model missing from the price table is priced as Sonnet 4.6.

## Status and notices

- green / yellow (days left < `yellow_days`, default 7) / red (< `red_days`,
  default 2, or nothing left). Grey = no balance entered yet. Thresholds are
  edited in the panel (`app_settings` `anthropic_credits:settings`).
- **One notice per crossing into a worse status**: an `alerts` row (source
  `anthropic_credits`, shown with the other admin alerts) and an email to
  `ADMIN_EMAIL` (`email_service.send_owner_notice`; needs `GMAIL_APP_PASSWORD`,
  as every email does). The announced level is kept per balance entry
  (`anthropic_credits:alert`): a new top-up starts a new cycle, and a recovery
  (slower burn, new thresholds) re-arms it.
- Checked on every widget load and by the hourly scheduler job
  `credits-check` (`GET /api/credits/check`, X-Admin-Key), so the notice
  arrives with no admin page open.
- **Warn only.** Nothing stops, slows or reroutes any agent.

## Audit

Each balance entry and each threshold change writes an audit row to `alerts`
(source `credits`, status resolved): who (admin session fingerprint + IP),
when, the new value and the previous one. `credits` is in
`bulk_delete.AUDIT_SOURCES`, so these rows are protected from bulk delete.

## Setup

1. Run `migrations/2026-11-09-anthropic-credits.sql` in the Supabase SQL editor
   (idempotent; comment-free copy `migrations/anthropic-credits-for-supabase.txt`,
   gitignored). Until then the widget shows the measured spend and says the
   table is missing; entering a balance answers 503.
2. Deploy.
3. `bash scripts/schedule_jobs.sh` (Cloud Shell) creates the hourly
   `credits-check` job.
4. Enter the Console's current balance once in the panel.

## Routes

| Route | Guard | Does |
|---|---|---|
| `GET /api/admin/credits` | admin session | estimate + one-notice check + last 5 entries |
| `POST /api/admin/credits/balance` | admin session | `{balance_usd, note}` → entry + audit |
| `PUT /api/admin/credits/thresholds` | admin session | `{yellow_days, red_days}` (0 < red < yellow ≤ 90) + audit |
| `GET /api/credits/check` | X-Admin-Key | hourly job: status, days left, notified |
