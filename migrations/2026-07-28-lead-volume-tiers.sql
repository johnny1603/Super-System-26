-- Per-client lead-volume tiers (CRM pricing add-on).
-- Run in the Supabase SQL editor, AFTER 2026-07-28-lead-tracking.sql.
-- Idempotent — a partial run can be re-run.
--
-- Until this runs, the tier scan errors and the admin client drawer shows its
-- "not measured yet" state. Nothing else in the app depends on it.

create table if not exists client_lead_volume (
  id             bigserial primary key,
  client_id      bigint not null,
  -- First day of the calendar month. The COUNT inside it is a rolling 30-day
  -- number (that is the window both ad platforms' existing queries speak); the
  -- TIER is what got snapshotted for this month. See core/lead_volume.py.
  period         date not null,
  lead_count     integer not null default 0,
  -- Latest observed tier this month.
  tier_key       text not null default 'included',
  -- Highest tier observed this month. This is the one that counts: a client
  -- who hit 400 leads and drifted back to 280 still earned the 301-700 tier.
  -- Next month's billable tier is read from THIS column, which is what makes
  -- the staging non-retroactive.
  peak_tier_key  text not null default 'included',
  -- Which counters produced the number ('google_ads,meta', 'none', ...).
  source         text,
  -- False whenever the count cannot see every lead the client actually got —
  -- which is always, today. The admin UI surfaces it rather than rounding it
  -- away, because this number is heading for an invoice.
  complete       boolean not null default false,
  counted_at     timestamptz not null default now(),
  unique (client_id, period)
);

create index if not exists client_lead_volume_client_idx on client_lead_volume (client_id, period desc);

-- Service-role key bypasses RLS; enabled with no policy means nothing else
-- reads it. Same posture as the other tables.
alter table client_lead_volume enable row level security;

-- Tier prices and thresholds are NOT stored here. They live in PRICING
-- (agents/onboarding_agent.py), the single source of truth for every business
-- number — this table stores only which tier key was observed, so a price
-- change never needs a data migration.
