-- Indexes for client deletion (core/client_deletion.py) — 2026-10-19
--
-- Every step of a client deletion filters by client_id (delete, then count
-- what is left), plus lead_messages / client_costs by lead_id. Without an
-- index each of those statements reads the WHOLE table. Several core tables
-- (client_accounts, client_activity, client_communications,
-- client_suggestions, client_agents, client_costs, login_codes) were created
-- outside migrations, so whether they have one is unknown from the repo.
--
-- What this does: for each (table, column) below, creates
-- "<table>_<column>_idx" ONLY when the table exists AND has no index whose
-- FIRST column is that column already (an existing composite index such as
-- client_leads (client_id, created_at) already serves these queries — no
-- duplicate is added). Safe to run any number of times. Changes no data.
--
-- If NOT run: nothing breaks. Deletion (and the dashboard's per-client
-- reads) just stay slower on big tables.
--
-- Note: a plain CREATE INDEX briefly blocks writes to that one table while it
-- builds — seconds on tables this size. Run it at a quiet moment.

-- 1. See first (read-only): what each table has today
select tablename, indexname, indexdef
from pg_indexes
where schemaname = 'public'
  and tablename in ('client_accounts', 'client_costs', 'client_activity', 'client_communications',
                    'client_suggestions', 'client_agents', 'client_leads', 'client_feedback',
                    'client_lead_volume', 'landing_pages', 'admin_agent_chat',
                    'client_target_keywords', 'login_codes', 'leads', 'lead_messages')
order by tablename, indexname;

-- 2. Create the missing ones
do $$
declare
  pair text[];
  t text;
  c text;
begin
  foreach pair slice 1 in array array[
    ['client_accounts', 'client_id'],
    ['client_costs', 'client_id'],
    ['client_costs', 'lead_id'],
    ['client_activity', 'client_id'],
    ['client_communications', 'client_id'],
    ['client_suggestions', 'client_id'],
    ['client_agents', 'client_id'],
    ['client_leads', 'client_id'],
    ['client_feedback', 'client_id'],
    ['client_lead_volume', 'client_id'],
    ['landing_pages', 'client_id'],
    ['admin_agent_chat', 'client_id'],
    ['client_target_keywords', 'client_id'],
    ['login_codes', 'client_id'],
    ['leads', 'client_id'],
    ['lead_messages', 'lead_id']
  ] loop
    t := pair[1];
    c := pair[2];
    if to_regclass('public.' || quote_ident(t)) is null then
      raise notice 'skip %: table does not exist', t;
      continue;
    end if;
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = t and column_name = c) then
      raise notice 'skip %.%: column does not exist', t, c;
      continue;
    end if;
    if exists (select 1
               from pg_index i
               join pg_attribute a on a.attrelid = i.indrelid and a.attnum = i.indkey[0]
               where i.indrelid = ('public.' || quote_ident(t))::regclass and a.attname = c) then
      raise notice 'ok   %.%: already indexed', t, c;
      continue;
    end if;
    execute format('create index if not exists %I on public.%I (%I)', t || '_' || c || '_idx', t, c);
    raise notice 'NEW  %.%: index created', t, c;
  end loop;
end $$;
