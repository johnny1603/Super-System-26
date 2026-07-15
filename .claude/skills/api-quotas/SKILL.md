---
name: api-quotas
description: How uallak persists API call counters (Google Ads daily op limit, Meta Marketing API 15-day rolling threshold) across Cloud Run restarts. Use when touching core/api_call_counters.py, the counting calls in core/google_ads_service.py / core/meta_service.py, or the api_call_counters Supabase table.
---

# API call counters (persistent, survives restarts)

## Why this exists

Google Ads' Explorer Access allows 2,880 operations/day; Meta's Marketing API
needs 500+ calls in the trailing 15 days just to qualify for Full Access
review. Both were originally tracked with a plain in-memory dict in their
respective service files — worthless as real trackers because Cloud Run
restarts on every deploy and can scale to zero, wiping the counter well
within either window. Fixed 2026-07: both now persist to one Supabase table
via `core/api_call_counters.py`.

## Design

One shared function, `increment_call_counter(platform, window_days)` in
`core/api_call_counters.py`: atomically upserts today's row for `platform`
(one Postgres RPC round trip, race-safe under Cloud Run's concurrent
instances via `ON CONFLICT ... DO UPDATE`) and returns the SUM of counts over
the trailing `window_days` days including today.

- `core/google_ads_service.py._count_operation()` calls it with
  `window_days=1` — a plain daily counter, same semantics as the old code
  (raise over `DAILY_OP_LIMIT`, warn at 80%).
- `core/meta_service.py._count_marketing_call()` calls it with
  `window_days=MARKETING_CALL_ROLLING_WINDOW_DAYS` (15) — a genuine rolling
  sum. The OLD counter reset every day and was mislabeled as "trailing 15
  days" in its own comment; it never actually measured that window. This is
  the real fix, not just persistence.

**Fails open by design**: if Supabase is unreachable, `increment_call_counter`
logs a warning and returns 0 rather than raising — a persistence hiccup must
never block real Ads/Meta traffic. This means the safety brake goes dark
during a Supabase outage; both platforms enforce the real limit server-side
regardless, so this is a soft internal guard, not the actual backstop.

## Schema + setup SQL

Run once in the Supabase SQL editor:

```sql
create table if not exists api_call_counters (
  platform text not null,
  call_date date not null,
  count integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key (platform, call_date)
);

create or replace function increment_api_call_counter(
  p_platform text,
  p_date date,
  p_window_days integer default 1
)
returns integer
language plpgsql
as $$
declare
  window_total integer;
begin
  insert into api_call_counters (platform, call_date, count, updated_at)
  values (p_platform, p_date, 1, now())
  on conflict (platform, call_date)
  do update set count = api_call_counters.count + 1, updated_at = now();

  select coalesce(sum(count), 0) into window_total
  from api_call_counters
  where platform = p_platform
    and call_date between p_date - (p_window_days - 1) and p_date;

  return window_total;
end;
$$;
```

`platform` values in use: `google_ads`, `meta_marketing`. Rows accumulate
indefinitely (a row per platform per calendar day) — cheap at this volume;
no cleanup job exists yet (see Deferred).

## Gotchas

- The RPC does the increment AND the window-sum read in ONE round trip by
  design — don't split this into a separate increment call + a separate
  select, that doubles Supabase latency on every single counted Ads/Meta
  call (some of which sit on the client-facing support-chat path, gated by
  a 5-min cache upstream but still worth keeping cheap).
- `call_date` is a plain `date`, using Cloud Run's server timezone (UTC) via
  Python's `time.strftime("%Y-%m-%d")` in `api_call_counters.py` — not
  Israel time. Both Google's daily reset and Meta's window are approximate
  anyway (best-effort, not the platforms' own authoritative counters), so
  this is an accepted simplification, not a bug to chase.
- `increment_api_call_counter` is `plpgsql` (needs the `insert` to commit
  before the `select` reads the just-written row within the same function
  call) — don't rewrite it as a single `sql`-language function with a
  returning-clause shortcut; that path doesn't have a straightforward way to
  also aggregate the window sum in one statement.

## Deferred / not built

Row cleanup/archival for old `api_call_counters` rows (harmless growth for a
long time at 2 rows/day), a dashboard/admin view of current usage against
each limit (today it's log-line-only, same as before), applying this same
pattern to any future TikTok integration's quota.
