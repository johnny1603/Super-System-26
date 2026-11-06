-- Chat timestamps (2026-11-07, docs/CHAT_TIMESTAMPS.md).
--
-- clients.timezone: the IANA zone the dashboard chat shows times in
-- (default Asia/Jerusalem). Before this runs, the chat uses the browser's
-- own zone — nothing breaks, the times are just the viewer's local time.
--
-- client_communications.created_at already exists (every chat read orders by
-- it). The two statements below only make sure of it: added WITHOUT a default
-- first, so if it were ever missing no old row gets a made-up time (no
-- backfill), then the default for new rows. On the existing column both are
-- no-ops in effect.
--
-- agent_actions already has created_at / decided_at / decided_by
-- (migrations/2026-10-21-agent-actions.sql) — nothing to add for the cards.
--
-- Idempotent: safe to run more than once.

alter table clients add column if not exists timezone text not null default 'Asia/Jerusalem';

alter table client_communications add column if not exists created_at timestamptz;
alter table client_communications alter column created_at set default now();
