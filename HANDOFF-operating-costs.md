# HANDOFF — live cost dashboard (uallak's own operating costs)

## ⚠ Read this first: "real numbers pulled from each provider" is not achievable

The brief asked for real-time costs pulled from each provider. Every service we
hold credentials for was checked. The finding:

> **Not one provider we use exposes a billing figure we can read today.** The
> APIs that exist need separate admin/billing credentials we do not hold, and
> most have no billing endpoint at all.

Building it anyway and calling the numbers "live" would have made this page
actively harmful — it is the one screen whose entire job is telling Johnny the
truth about his spend. So **every line is labelled with how its number was
obtained**, and the page says out loud that nothing here is a billing pull.

| Service | Live billing API? | What we do | Upgrade path |
|---|---|---|---|
| **Anthropic (Claude)** | Admin API **does** expose a cost report — needs an `sk-ant-admin...` key we don't have (only `ANTHROPIC_API_KEY`) | **`measured`** — real token counts from every call × list rates | Wire the Admin API → becomes a genuine billing pull. **Best available upgrade.** |
| **InstaWP** | **No.** v2 API is sites/templates/teams only | **`derived`** — live count of provisioned sites × known per-site cost | None exists. The site count is the honest part. |
| **Supabase** | Management API can read the subscription — needs a **personal access token**; our service key cannot | **`manual`** + real DB size shown beside it | Add a PAT if the plan stops being free |
| **Google Cloud Run** | Cloud Billing API is real — needs a service account with **billing-account IAM**; ours is Drive-scoped | **`manual`** | Grant billing IAM. **Second-best upgrade.** |
| **Cloudflare** | Account billing endpoints are largely deprecated | **`manual`** (free at our scale) | Watch the custom-hostname count in `core/landing_domains.py`, not billing — free tier covers 100 |
| **Green API (WhatsApp)** | **No** — instance state only | **`manual`** | None |
| **PayPal fees** | Transaction Search API **does** report real per-transaction fees | **`manual`** — and **₪0 today**: `paypal_service` is hardcoded to Sandbox | Genuinely wireable, but pointless until the account goes Live |
| **Domains, Google Workspace** | No practical API | **`manual`** | None |
| **Google Ads / Meta / TikTok / YouTube APIs** | n/a | **`none`** — quota-limited, no monetary cost | n/a |
| **Higgsfield / HeyGen / ElevenLabs / SEO tools** | n/a | **`none`** — the CLIENT pays these on their own card | n/a — see `budget_agent` for the client-side picture |

## What already existed (reused, not rebuilt)

The brief asked what's already tracked before building. Three things were, and
all three are read rather than duplicated:

1. **`client_costs` + `core/cost_tracker.py`** — every Claude call's real token
   cost, recorded at the `safe_claude_json_call` / `claude_web_search_call`
   choke points, plus the per-search web fee. `cost_tracker` stays the only
   writer; this feature only reads.
2. **`admin_service.get_overview()`** — already totals those rows into
   `cost_month` / `cost_by_category` for the overview tab. The new page shows the
   same number from the same source, so the two can never disagree.
3. **`lead_volume.get_platform_usage()`** — uallak's real Supabase DB size vs the
   plan ceiling (the Infra Watch work). Shown *beside* the Supabase cost line as
   the usage half of the same story, not re-implemented.

Also reused: `third_party_pricing.THIRD_PARTY_PRICING` (vendor list prices,
re-checked twice a month by `price_monitor_agent`) and
`PRICING["website"]["new_site_hosting"]["cost_monthly_ils"]` for the InstaWP rate.

**Not reused — and the distinction matters:** `agents/budget_agent.py` is ONE
CLIENT's financial picture (their ad spend, their tools, their margin). This is
per-COMPANY. They answer different questions and must not be merged.

## The four labels

- **`measured`** — computed from usage we actually recorded. Claude only. The
  row itself states the caveat: our arithmetic on real usage, *not* Anthropic's
  billing — it ignores discounts, prompt-caching credits, and the real USD→ILS
  rate on the day (`USD_TO_ILS` is a fixed 3.40).
- **`derived`** — a real live count × a known rate. InstaWP sites only.
- **`manual`** — a human typed it, with the date they last confirmed it. Ages
  visibly; past 120 days the age turns yellow.
- **`none`** — genuinely free at our scale. Listed on purpose, so "why isn't
  Meta in here?" is answered on the page instead of in someone's head.

**A `manual` row nobody has filled in shows "not set", never ₪0** — "nobody
entered this" and "this is free" are different facts and only one is good news.
Unset services are named in a banner and the total says it is understated.

## The total is two halves, deliberately not blended

- **Fixed monthly** — recurring subscriptions (yearly ÷ 12).
- **Variable month-to-date** — this month's Claude usage, incomplete by
  definition until the month ends.

Both are shown, and the sum is labelled "month-to-date", not "monthly cost".
Adding a full month's subscriptions to three days of API usage and calling it a
monthly figure is exactly the confident-wrong number this codebase bans
elsewhere. **There is deliberately no end-of-month projection** — one month of
partial data doesn't support one honestly.

## Files

- `migrations/2026-08-03-operating-costs.sql` — **must be run by hand** in the
  Supabase SQL editor. Until then the page still works: measured and derived
  numbers are real, every manual row says "not set", and a banner names this file.
- `core/operating_costs.py` — the service catalog (`SERVICES`) and the one entry
  point `get_operating_costs()`, plus `set_manual_cost()`.
- `core/api_server.py` — `GET /api/admin/operating-costs`,
  `PUT /api/admin/operating-costs/{service_key}` (both admin-session gated).
- `dashboard/admin/index.html` — the "עלויות תפעול" tab.

**Admin-only, never client-facing.** Both endpoints go through `_require_admin`
(the browser session cookie), like the rest of the admin dashboard. Nothing in
the client dashboard reads any of it.

## Extending it

Adding a service = one dict in `SERVICES`. The catalog is the only place that
knows what exists, so the API, totals, categories and UI all pick it up. Rules:

- A new `manual` key needs nothing else — `set_manual_cost` derives its
  whitelist from `SERVICES`, so only manual rows are ever writable. **A measured
  or derived number must never become hand-overridable**, or the label stops
  meaning anything.
- If a provider ever exposes real billing, add a **new `live` label**. Do not
  quietly relabel `manual` as live.
- Keep it in sync with `agents/keys_agent.py` `KEYS` by hand — every credential
  there that implies a paid service should appear here, so the page can answer
  "is that all of it?" honestly.

## Known limitations (all visible in the UI, none hidden)

1. **The InstaWP site count can overcount.** It counts distinct clients with a
   `website_provisioned` activity row; no deprovision flow exists, so a site
   deleted by hand in InstaWP's console is still counted. Stated in the row.
2. **Claude cost is list-price arithmetic**, not an invoice (above).
3. **No history.** This is a current-state view; there are no monthly cost
   snapshots to trend against. `budget_agent`'s weekly snapshot pattern
   (`client_activity` rows, no new table) is the obvious model if trending is
   wanted later — deliberately not built, since one data point isn't a trend.
4. **Nothing alerts.** No threshold, no notification — Johnny reads the page.
   Wiring a monthly "spend jumped X%" alert would need #3 first.

## Not verified live

No Python on this dev machine, so nothing was executed. The admin dashboard's
renderer was exercised against a stubbed DOM with 20 cases — including the ugly
states (migration not run, nothing entered, Supabase unmeasured, Claude read
failed) and HTML escaping — plus a structural check of the Python. **The
migration has not been run and the endpoints have not been hit**, so first real
use should confirm: the migration applies cleanly, the unset→set→total flow
updates, and the provisioned-site count matches what InstaWP's console shows.
