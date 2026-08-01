-- Client feedback on uallak itself — the answer to "are you happy with the
-- platform, and is there anything you'd pass to the dev team?"
--
-- Why a table and not client_activity: this is the one thing in the system
-- addressed to US rather than describing the client's business. It needs to be
-- READ AS A LIST by a human (Johnny), across clients, newest first, with a
-- reviewed/unreviewed state — none of which client_activity's per-client,
-- append-only shape supports well. Every other thing this handoff touches
-- (interview facts, login moments, journey milestones) deliberately does NOT
-- get a table; they read and write rows that already exist.
--
-- Until this runs: the weekly feedback ASK still happens in chat, but
-- store_feedback() logs a warning and drops the answer. Nothing else breaks.
--
-- Every statement is idempotent.

create table if not exists client_feedback (
  id          bigserial primary key,
  client_id   bigint not null,
  -- 1-5 when the client gave one, null when they only wrote prose. Never
  -- inferred from the text: a made-up score would poison the only honest
  -- satisfaction signal we have.
  rating      int,
  -- The client's own words, verbatim, in whatever language they wrote.
  message     text not null default '',
  -- 'weekly_checkin' (the scheduled ask) | 'unprompted' (they just said it)
  source      text not null default 'weekly_checkin',
  -- Johnny's triage state. Deliberately not a workflow — just seen/not seen.
  reviewed    boolean not null default false,
  created_at  timestamptz not null default now()
);

-- The admin read is always "everything, newest first, unreviewed first" —
-- one index covers it.
create index if not exists client_feedback_review_idx
  on client_feedback (reviewed, created_at desc);

-- The dedup check is "has this client been asked/answered in the last 7 days"
create index if not exists client_feedback_client_created_idx
  on client_feedback (client_id, created_at desc);
