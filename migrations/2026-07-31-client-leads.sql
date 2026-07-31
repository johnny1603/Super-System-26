-- Client-facing leads: the leads a CLIENT receives from their own marketing.
--
-- This is NOT the `leads` table. `leads` is uallak's OWN acquisition funnel —
-- one row per prospect who landed on our sales chat, i.e. the conversation
-- that sold a client to us. This table is the opposite direction: people who
-- contacted OUR CLIENT. Conflating the two is the single most expensive
-- misreading in this codebase (see CLAUDE.md), hence the deliberately
-- different table name and this comment.
--
-- Until this runs: GET /api/client/leads returns an honest empty state and
-- POST /api/leads/capture/{token} 500s. Nothing else in the app breaks — no
-- existing code path reads this table. In particular core/lead_volume.py's
-- count_client_leads() is deliberately NOT switched over to it yet (that
-- number feeds a price; it must not start reading a table that is empty until
-- capture is actually wired into a client's site — see the handoff).
--
-- Every statement is idempotent.

create table if not exists client_leads (
  id            bigserial primary key,
  client_id     bigint not null,
  -- Contact details, all optional: a phone-only lead from a click-to-call is
  -- as real as a fully filled form, and rejecting it would lose the lead.
  name          text not null default '',
  phone         text not null default '',
  email         text not null default '',
  message       text not null default '',
  -- Where it came from. `source` is a coarse channel the UI filters on;
  -- `source_detail` is free text (page URL, campaign name, form id).
  source        text not null default 'unknown',
  source_detail text not null default '',
  -- The client's own pipeline state. Only these five are accepted server-side.
  status        text not null default 'new',
  -- The client's private notes on this lead. Free text, capped in code.
  notes         text not null default '',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- The list view is always "this client's leads, newest first", optionally
-- narrowed by status — these two indexes cover both shapes.
create index if not exists client_leads_client_created_idx
  on client_leads (client_id, created_at desc);
create index if not exists client_leads_client_status_idx
  on client_leads (client_id, status);

-- Offboarding: _ARCHIVE_PURGE_TABLES in core/api_server.py purges per-client
-- rows on closure/transfer, and client_leads is in that list — the export
-- built just before the purge carries the leads with it, so a leaving client
-- keeps the contact details of people who approached THEIR business. That is
-- their data, not ours.
