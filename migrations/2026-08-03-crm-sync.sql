-- External CRM sync status per captured lead (agents/crm_agent.py).
--
-- Additive and nullable by design. `core/client_leads._insert_lead` already
-- proves the rule this follows: a pending migration must NEVER cost us a real
-- customer enquiry. Capture writes the lead with the base columns only, and the
-- CRM push happens afterwards in a background task — so if these columns do not
-- exist yet, leads are still captured normally and the sync status update is the
-- only thing that no-ops (loudly, via an alert).
--
-- IF THIS HASN'T BEEN RUN: connecting a CRM works, pushes are attempted, and
-- successes reach the client's CRM — but nothing is recorded, so the retry pass
-- has nothing to find and a failed push is not retried. The CRM card says so.

alter table client_leads add column if not exists crm_sync_status text;
alter table client_leads add column if not exists crm_synced_at   timestamptz;
alter table client_leads add column if not exists crm_external_id text;
alter table client_leads add column if not exists crm_error       text;
alter table client_leads add column if not exists crm_attempts    integer not null default 0;

comment on column client_leads.crm_sync_status is
  'null = never attempted (no CRM connected, or migration newer than the row); '
  'synced | failed | skipped. Set by agents/crm_agent, never by the capture path.';

comment on column client_leads.crm_attempts is
  'How many push attempts this lead has had. The retry pass gives up after '
  'crm_agent.MAX_SYNC_ATTEMPTS so one permanently-bad lead cannot be retried forever.';

-- The retry pass asks one question: which of this client's leads still need a
-- push? Partial index keeps that cheap as client_leads grows, since the vast
-- majority of rows are 'synced' or null.
create index if not exists client_leads_crm_retry_idx
  on client_leads (client_id, crm_sync_status)
  where crm_sync_status = 'failed';
