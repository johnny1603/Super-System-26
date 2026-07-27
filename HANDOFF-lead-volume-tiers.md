# HANDOFF — lead-volume pricing tiers + internal infra watch

## ⚠️ Read this first: the counter is a placeholder, by decision

The brief assumed the `leads` table could answer "how many leads did this
client get this month". It can't, and it never could: **`leads` is uallak's own
sales funnel.** A row is a prospect who came through *our* sales chat, and
`leads.client_id` (written only in `mark_converted`) records which uallak
client that prospect *became*. Every client has at most one lead row, ever —
the conversation that sold them. Counting it per client per month returns 1 in
their signup month and 0 forever after.

No table anywhere stores leads delivered *to* a client. So the agreed shape was:
**build the whole tier machinery against one swappable counter**, and decide the
real lead source separately.

`core/lead_volume.count_client_leads(client_id)` is that seam. Replace its body
— not its signature — when a real per-client lead record exists. Nothing else
in the feature needs to change.

## What the counter returns today, and why it's marked incomplete

It sums Google Ads + Meta **conversions** from the two agents'
`get_campaign_performance` calls. Four limits, all surfaced in the UI rather
than buried:

1. Counts **paid campaigns we run**. Organic form fills, WhatsApp messages,
   phone calls and walk-ins are invisible.
2. A "conversion" is whatever the client configured **in their own ad account**.
   It may not mean "lead".
3. The client can change their conversion actions, and therefore their own
   count.
4. GA4's `generate_lead` — which `website_agent` configures via GTM — is **not
   readable to us**: our Google OAuth scope is Ads, not Analytics.

`count_client_leads` always returns `complete: False` with a `notes` list, and
the admin drawer prints the caveat next to the number. Do not remove that until
the counter can actually see every lead.

## Windows — the question the brief asked me to answer

**The count is rolling 30 days. The tier is snapshotted per calendar month.**

The count window was chosen on evidence, not preference: both ad platforms'
existing, cached, working performance queries speak `LAST_30_DAYS`
(`_PERFORMANCE_GAQL ... DURING LAST_30_DAYS`, Meta `date_preset="last_30d"`).
Getting a calendar-month figure would have meant writing two new ad-API queries
that cannot be tested from this machine, to obtain a number already sitting in
a function we call today. The brief allowed either window; this one is free and
already proven.

The tier is snapshotted monthly because billing needs a boundary:

- `client_lead_volume` holds one row per client per calendar month.
- `tier_key` = latest observed tier. `peak_tier_key` = highest observed that
  month — the one that counts, so a client who hit 400 leads and drifted back
  to 280 still earned the 301–700 tier.
- **The billable tier for a month is the PREVIOUS month's `peak_tier_key`.**
  That is what makes it non-retroactive: crossing a threshold today can never
  change what a client owes today.

## Tiers

In `PRICING["lead_volume_tiers"]` (`agents/onboarding_agent.py`) — the single
source of truth, per CLAUDE.md. `tier_for_lead_count()` next to it is the only
place the boundaries are evaluated; nothing else re-implements them, and the
stored table holds tier *keys*, not prices, so a price change needs no data
migration.

| Monthly leads | Price | key |
|---|---|---|
| 0–100 | ₪0 (included) | `included` |
| 101–300 | ₪29 | `growth` |
| 301–700 | ₪59 | `scale` |
| 701+ | ₪99 | `high` |

**Boundary note:** the brief's table wrote "301–700" and "700+", which overlap
at exactly 700. Resolved as 301–700 → ₪59 and 701+ → ₪99.

**Gates nothing.** Campaign-level attribution ships in every package and behaves
identically at every tier. The tier sets a price and nothing else — there is no
feature check anywhere in this code, deliberately.

## Billing: alert-only, nothing automatic

Per the agreed decision, crossing a threshold **alerts you and stops**. No
PayPal call, no client-facing change, no email to the client.

That also sidesteps a real constraint: PayPal subscription revision
(`revise_subscription_plan`) **requires the client to approve via a redirect**.
An "automatic" tier bill would silently not apply to any client who ignored the
approval link. If you later want automation, that approval step is the design
problem to solve first.

The alert fires **once per client per tier, ever** (`client_activity` dedup on
`details->>tier_key`, the same idiom the platform scans use), and its text says
the count is partial so a tier decision is never made on a number whose limits
you've forgotten.

## Step 3 — internal infra watch, deliberately disconnected

`get_platform_usage()` tracks uallak's own `leads` + `lead_messages` row growth
against an admin-configurable budget (`supabase_row_budget`,
`supabase_row_warn_pct` in settings), and alerts past the threshold.

It is **not** joined to client tiers anywhere, and shouldn't be — a client's
tier is value-based and says nothing about what they cost us to serve. Wiring
them together would reintroduce exactly the infrastructure-cost pricing the
brief ruled out.

**Honest limit:** Supabase's real ceiling is database *size*, and the plan's
actual limit is not readable without the Supabase Management API, which we
don't integrate. This tracks row count as a proxy against a number you
configure, and the UI says so rather than displaying a fabricated "% of plan".

## Deploy

1. **Run `migrations/2026-07-28-lead-volume-tiers.sql`** (after the lead-tracking
   migration). Until then the scan errors and the drawer shows its
   "not measured yet" state; nothing else is affected.
2. **Add the scheduler job.** The scan deliberately runs on a schedule rather
   than on dashboard load — the Google Ads daily operation cap and Meta's
   rolling threshold are real (see the api-quotas skill), and a per-page-load
   lookup would burn them. `get_client_volume` reads only the stored snapshot.

```bash
gcloud scheduler jobs create http lead-volume-scan --schedule="0 6 * * *" \
  --uri="{SERVICE_URL}/api/leads/volume-scan" --http-method=GET \
  --update-headers=X-Admin-Key={ADMIN_KEY}
```

3. Optionally set `supabase_row_budget` in admin settings to match your actual
   plan. The default (400,000 rows) is a placeholder, not a measured limit.

## Files

| file | what |
|---|---|
| `core/lead_volume.py` | the counter seam, tier staging, crossing alerts, infra watch |
| `agents/onboarding_agent.py` | `PRICING["lead_volume_tiers"]` + `tier_for_lead_count()` |
| `migrations/2026-07-28-lead-volume-tiers.sql` | `client_lead_volume` table |
| `core/admin_service.py` | `lead_volume` in the client-detail payload; two new settings |
| `core/api_server.py` | `/api/leads/volume-scan`, `/api/admin/platform-usage` |
| `dashboard/admin/index.html` | tier section in the client drawer; infra usage in settings |

## Verification status

The dashboard code was exercised against a stubbed DOM (tier section rendered
for measured / included / never-measured / lookup-failed cases; settings and
usage panels loaded). The Python is unverified by execution as usual — no
Python on the dev machine. The genuinely untested paths are the two ad-API
reads inside `count_client_leads`, which reuse functions already in production
use, and the `client_lead_volume` upsert, whose first real run is the first
scheduled scan.
