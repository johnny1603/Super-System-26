-- Feature announcements (core/feature_announcements.py).
--
-- Why a table and not app_settings: this is a LIST that grows — one row per
-- thing Johnny decides is ready to tell clients about, each with its own
-- feature key, note, status and publish time. app_settings is a flat
-- key->value store whose writable keys are whitelisted in code, which is the
-- wrong shape (same reasoning as migrations/2026-08-03-operating-costs.sql).
--
-- The FEATURE itself is never described here — `feature_key` points at an entry
-- in core/feature_catalog.py, which is what decides who the announcement is
-- relevant to and which persona delivers it. This table only carries what a
-- human typed and whether it is live.
--
-- Delivery is deduped per client per announcement in client_activity
-- (action_type = 'feature_announcement_sent', details.announcement_id), the
-- same activity-row pattern every other login moment uses — no "sent to" table.
--
-- IF THIS HASN'T BEEN RUN: nothing breaks. The admin screen shows a banner
-- naming this file, listing announcements returns empty, and no client is ever
-- told anything. Logins, the interview and every other login moment are
-- unaffected.

create table if not exists feature_announcements (
  id           bigserial primary key,
  feature_key  text not null,          -- must exist in feature_catalog.FEATURES
  title        text not null default '',
  note         text not null default '',  -- Johnny's own words; the LLM rewrites per client
  status       text not null default 'draft',  -- 'draft' | 'live' | 'archived'
  created_at   timestamptz not null default now(),
  published_at timestamptz
);

create index if not exists feature_announcements_status_idx
  on feature_announcements (status, created_at);

comment on table feature_announcements is
  'Admin-authored "this shipped, tell the relevant clients" items. Johnny decides '
  'when something is ready — nothing here is created automatically by a deploy.';

comment on column feature_announcements.feature_key is
  'Key of an entry in core/feature_catalog.FEATURES. That entry decides which '
  'clients are relevant and which persona speaks. An unknown key reaches nobody.';

comment on column feature_announcements.note is
  'What Johnny wants said, in his words. Never sent verbatim: the login-moment '
  'LLM rewrites it per client in the owning persona voice.';
