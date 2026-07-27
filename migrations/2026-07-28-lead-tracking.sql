-- Lead tracking + source attribution (CRM dashboard).
-- Run once in the Supabase SQL editor. Every statement is idempotent, so a
-- partial run can simply be re-run.
--
-- The app tolerates this NOT having been run yet only in the sense that it
-- fails loudly rather than corrupting anything: /api/lead/start will error and
-- the chat falls back to the old proposal-time-only lead row. Run it before
-- deploying the code that depends on it.

-- ─── leads: attribution, lifecycle, and the client link ─────────────────────

-- The browser-held key. Random, not the sequential `id`, because the sales
-- chat is public: a guessable key would let anyone append to or read a
-- stranger's transcript.
alter table leads add column if not exists lead_id text;
create unique index if not exists leads_lead_id_key on leads (lead_id);

-- Two code paths have assumed this column exists for a while and silently
-- swallowed the failure (core/api_server.py checkout backfill,
-- agents/budget_agent.py _lead_row). Both start working the moment it exists.
alter table leads add column if not exists client_id bigint;

-- in_progress | converted | declined. `dropped_off` is deliberately NOT a
-- stored value — it is derived from last_activity_at at read time
-- (core/lead_tracking.py), so a prospect who returns tomorrow needs no job to
-- un-flip their status.
alter table leads add column if not exists status text default 'in_progress';

alter table leads add column if not exists first_contact_at timestamptz;
alter table leads add column if not exists last_activity_at timestamptz;
alter table leads add column if not exists converted_at timestamptz;

-- Sent to /api/onboarding since the i18n work but never stored until now.
alter table leads add column if not exists language text;

alter table leads add column if not exists utm_source text;
alter table leads add column if not exists utm_medium text;
alter table leads add column if not exists utm_campaign text;
alter table leads add column if not exists utm_content text;
alter table leads add column if not exists utm_term text;

-- google_ads | meta | tiktok | microsoft_ads | organic | referral | direct
alter table leads add column if not exists source_platform text;
-- click_id | utm | referrer | none — how much the platform above is worth
alter table leads add column if not exists source_confidence text;
alter table leads add column if not exists source_detail text;

alter table leads add column if not exists referrer text;
alter table leads add column if not exists landing_path text;
-- {gclid|gbraid|wbraid|fbclid|ttclid|msclkid: value} — only the ones present
alter table leads add column if not exists click_ids jsonb default '{}'::jsonb;

create index if not exists leads_status_idx on leads (status);
create index if not exists leads_source_platform_idx on leads (source_platform);
create index if not exists leads_client_id_idx on leads (client_id);

-- ─── lead_messages: the sales-chat transcript ───────────────────────────────
-- Until now the sales conversation existed only in the prospect's browser DOM
-- (dashboard/onboarding/index.html downloadTranscript scrapes it off screen).

create table if not exists lead_messages (
  id          bigserial primary key,
  lead_id     text not null,
  role        text not null check (role in ('user', 'assistant')),
  content     text not null,
  created_at  timestamptz not null default now()
);

create index if not exists lead_messages_lead_idx on lead_messages (lead_id, created_at);

-- Service-role key bypasses RLS; enabling it with no policy means nothing
-- else can read these. Matches the posture of the other tables.
alter table lead_messages enable row level security;

-- ─── Backfill for rows that predate all of the above ────────────────────────

-- Give historical leads a key so they open in the CRM drawer like any other.
update leads set lead_id = gen_random_uuid()::text where lead_id is null;

update leads
   set first_contact_at = coalesce(first_contact_at, created_at),
       last_activity_at = coalesce(last_activity_at, created_at),
       status           = coalesce(status, 'in_progress')
 where first_contact_at is null or last_activity_at is null or status is null;

-- Approximate, and knowingly so: matching a historical lead to the client it
-- became by email is the same approximation budget_agent._lead_row already
-- falls back to. It is strictly better than leaving every past conversion
-- showing as a dropped-off lead.
update leads l
   set client_id    = c.id,
       status       = 'converted',
       converted_at = coalesce(l.converted_at, l.created_at)
  from clients c
 where l.client_id is null
   and coalesce(l.client_email, '') <> ''
   and lower(l.client_email) = lower(c.email);

-- Historical rows keep source_platform NULL on purpose. The CRM renders that
-- as "לא ידוע" (unknown) rather than "direct" — we genuinely do not know where
-- those people came from, and saying "direct" would be inventing attribution.
