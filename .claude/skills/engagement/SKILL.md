---
name: engagement
description: How uallak's proactive engagement engine works — weekly client suggestions (Israeli calendar + trends + performance), the pending-approval flow, sales-alert emails, the WhatsApp (Green API) SOS channel, and the chat persona. Use when touching agents/engagement_agent.py, core/israel_calendar.py, core/whatsapp_service.py, the /api/engagement* or /api/client/suggestions* endpoints, or the dashboard's pending-approvals/chat-persona UI.
---

# Proactive engagement engine

## Philosophy

The system drives the relationship instead of waiting to be asked: suggestions
arrive weekly, holiday prep starts weeks ahead, wins are celebrated same-day,
and genuinely urgent things chase the client on WhatsApp. Everything lands as
an approve/reject decision — nothing executes without the client's tap.

**The notification ladder** (respect it when adding notifications):
dashboard feed = ambient · dashboard chat push = worth noticing · email =
important (sales alerts, reports) · **WhatsApp = can't wait**. WhatsApp is
sacred: a noisy channel gets muted and the real SOS dies with it. Never wire
routine events to it.

## Weekly suggestions (the core loop)

`engagement_agent.run_weekly_engagement()` (Cloud Scheduler →
`GET /api/engagement/weekly`, X-Admin-Key): per active client, ONE
`safe_claude_json_call` combining three angles —

1. **Israeli calendar** (`core/israel_calendar.py`): a static, human-verified
   table (dates cross-checked against hebcal, 2026-07) of chagim + commercial
   seasons, each with `lead_days` (suggestion appears that many days BEFORE),
   industry tags, and a Hebrew marketing angle. `kind: "sensitive"` events
   (Yom Kippur, Yom HaZikaron) are tone-DOWN advisories, never promos.
   Deliberately not a Hebrew-calendar library — nothing runs locally, so a
   readable table beats untestable date arithmetic. `horizon_warning()`
   alerts ~90 days before the table runs dry; extend it against hebcal.
   (Generalized from the Shin Sekai Instagram seasonal calendar concept.)
2. **Trends**: Claude's own confident industry knowledge — same
   knowledge-not-paid-tools reasoning as the sales chat's `market_reality`.
   The prompt forbids fabricated statistics and "viral this week" claims.
3. **Performance**: a concrete tweak grounded strictly in the compact
   last-30d totals passed in — only when the client has connected accounts.

Output → `client_suggestions` rows (`status='pending'`) → the dashboard's
"ממתין לאישור שלך" area + a chat push via `log_communication`. The client
approves/rejects in place (`POST /api/client/suggestions/{id}/decide`,
ownership-checked). **An approval fires `agent_alert` — v1 fulfillment is
human**; wire approved suggestions into execution agents as those grow.
Repeat-avoidance: the prompt gets the client's last 10 suggestion titles.

## Daily sales alerts

`run_daily_engagement()` (→ `GET /api/engagement/daily`, morning): yesterday's
conversions via `get_conversions_yesterday()` in google_ads_agent (GAQL,
`segments.date DURING YESTERDAY`) and meta_ads_agent (insights
`date_preset=yesterday`, CONVERSION_ACTION_TYPES sum). Any conversions → a
celebration email (`email_service.send_sales_alert`, distinct from weekly
reports), deduped per-day via `client_activity` `sales_alert_sent` rows.
Fetch failures return None and are skipped silently — a missing celebration
is not an incident.

## WhatsApp SOS (Green API)

`core/whatsapp_service.py`: `send_whatsapp(phone, message)` — Israeli phone
normalization (05X → 9725X…@c.us), never raises, fails safe when
unconfigured. Credentials in keys_agent KEYS: `GREEN_API_INSTANCE_ID` +
`GREEN_API_TOKEN` (one Green API instance = one WhatsApp number, linked by QR
in their console; Johnny has an existing account). Optional
`GREEN_API_BASE_URL` for instances with a dedicated subdomain (shown in the
console, e.g. `https://1103.api.green-api.com`).

**Instance topology (decided 2026-07-16): ONE Green API instance for
everything** — production and any testing share it; no separate dev instance
until the cost is justified. Be careful testing sends: they go out on the
real business WhatsApp number.

`engagement_agent.notify_client_urgent(client_id, message_he)` is the ONLY
proper entry point: WhatsApp + always a dashboard-chat fallback copy +
`agent_alert` when a configured send fails + activity log. Exposed for manual
use as `POST /api/notify/whatsapp` (admin).

**SOS triggers wired today** (the full approved list — additions need the
same one-per-incident dedup discipline):
1. Campaign auto-paused by an ads health scan (Google/Meta, gated on the
   scans' 3-day issue dedup).
2. Failed purchase/checkout charge — PayPal webhook events
   `BILLING.SUBSCRIPTION.PAYMENT.FAILED` / `PAYMENT.SALE.DENIED` /
   `BILLING.SUBSCRIPTION.SUSPENDED` → `notify_payment_failure(client_id,
   event_type)`, deduped per calendar day (PayPal retries re-fire the
   webhook). This covers the only checkout that exists (uallak's own PayPal
   flow); when client-webshop e-commerce lands (WooCommerce — deferred), its
   failed checkouts must call the SAME function, not a parallel path.
   Requires the failure event types to be subscribed on the webhook in the
   PayPal developer dashboard (same webhook as activation events).

## Chat persona

The support chat's identity is a bot character (דניאל/דנה, inline SVGs in
`dashboard/client/index.html`), matched to the business owner via
`clients.owner_gender` — set ONLY by the client's own one-tap picker inside
the chat panel (`POST /api/client/profile`). **Never infer gender from the
client's name** — no data means show the picker, not a guess. Future
client-facing AI avatars (out of scope for now) should reuse `owner_gender`
and the suggestion/approval pipe rather than inventing parallel ones.

## Setup SQL (run once in Supabase)

```sql
create table if not exists client_suggestions (
  id bigint generated always as identity primary key,
  client_id bigint not null,
  created_at timestamptz not null default now(),
  kind text not null default 'content_idea',      -- promotion|content_idea|campaign_tweak|homework
  title text not null,
  body text not null,
  source text not null default 'general',         -- holiday|trend|performance|general
  context jsonb not null default '{}'::jsonb,     -- e.g. {"event_slug": "rosh_hashana"}
  status text not null default 'pending',         -- pending|approved|rejected
  decided_at timestamptz
);
create index if not exists client_suggestions_client_status
  on client_suggestions (client_id, status);

alter table clients add column if not exists owner_gender text;
```

## Scheduler jobs

```
gcloud scheduler jobs create http engagement-weekly --schedule="0 9 * * 0" \
  --uri="{SERVICE_URL}/api/engagement/weekly" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}
gcloud scheduler jobs create http engagement-daily --schedule="45 7 * * *" \
  --uri="{SERVICE_URL}/api/engagement/daily" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}
```

(Weekly on Sunday morning — start of the Israeli work week; daily at 07:45,
after the ad platforms settle yesterday's numbers and before the 08:00 team
scans.)

## Gotchas

- `/api/engagement/weekly` is a plain `def` making one LLM call per active
  client sequentially — fine at current scale; parallelize (threadpool) before
  the client count makes the request approach Cloud Run's timeout.
- Suggestion pushes go through the dashboard chat (`log_communication`
  outbound) — they appear in the chat history the support agent LLM sees, so
  it can answer "what did you suggest?" naturally.
- The israel_calendar table is STATIC — extending it is expected maintenance
  (the weekly run alerts when <90 days of horizon remain). Verify any new
  dates against hebcal; sensitive days matter as much as promo days.
- Costs: each weekly suggestion call is tagged `cost_category='engagement_weekly'`
  with client_id in client_costs — margin per client stays visible.
- WhatsApp reception (client replies) is NOT built — Green API webhooks are a
  future addition; today replies land in the client's normal WhatsApp and a
  human sees them on the linked phone.

## Deferred / not built

Inbound WhatsApp webhooks, per-industry event packs beyond the tag hints,
auto-execution of approved suggestions (v1 is team-fulfilled), client AI
avatars (explicitly future — architecture note: reuse owner_gender + the
suggestion pipe), suggestion snooze/edit (approve/reject only for v1).
