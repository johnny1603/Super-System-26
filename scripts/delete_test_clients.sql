-- TEST DATA ONLY — 10 throwaway clients for testing client deletion.
-- Run in the Supabase SQL editor, in the numbered parts, one at a time.
-- Every row is tagged: client emails are delete-test-N@example.invalid
-- (".invalid" can never be a real address, so nothing can ever be emailed).
-- Nothing here touches a real client.

-- ── PART 1: create the clients + activity rows ──────────────────────────────
insert into clients (name, email, phone, package, status, onboarding_completed)
select 'DELETE TEST ' || n, 'delete-test-' || n || '@example.invalid', '', 'test', 'active', true
from generate_series(1, 10) n
where not exists (select 1 from clients where email = 'delete-test-' || n || '@example.invalid');

-- 30 activity rows each; client 1 gets 1,500 more (more than one 1,000-row page)
insert into client_activity (client_id, agent_name, action_type, details, result)
select c.id, 'delete_test', 'test_action', jsonb_build_object('i', g), '{}'::jsonb
from clients c, generate_series(1, 30) g
where c.email like 'delete-test-%@example.invalid';
insert into client_activity (client_id, agent_name, action_type, details, result)
select c.id, 'delete_test', 'test_bulk', jsonb_build_object('i', g), '{}'::jsonb
from clients c, generate_series(1, 1500) g
where c.email = 'delete-test-1@example.invalid';

-- 10 chat messages each
insert into client_communications (client_id, direction, channel, content)
select c.id, 'inbound', 'chat', 'delete test message ' || g
from clients c, generate_series(1, 10) g
where c.email like 'delete-test-%@example.invalid';

-- 5 cost rows each; client 2 gets 1,200 more (tests the cost paging)
insert into client_costs (client_id, category, amount, details)
select c.id, 'claude_api', 0.5, jsonb_build_object('usd', 0.15, 'delete_test', true)
from clients c, generate_series(1, 5) g
where c.email like 'delete-test-%@example.invalid';
insert into client_costs (client_id, category, amount, details)
select c.id, 'claude_api', 0.01, jsonb_build_object('usd', 0.003, 'delete_test', true)
from clients c, generate_series(1, 1200) g
where c.email = 'delete-test-2@example.invalid';

-- one used login code + one assigned agent each
insert into login_codes (client_id, code, expires_at, used)
select c.id, '000000', now() - interval '1 day', true
from clients c where c.email like 'delete-test-%@example.invalid';
insert into client_agents (client_id, agent_name, status)
select c.id, 'delete_test_agent', 'active'
from clients c where c.email like 'delete-test-%@example.invalid';

-- Optional: a sales-funnel lead + transcript for clients 3 and 4. Wrapped so
-- a column difference in `leads` only skips this part.
do $$
begin
  insert into leads (lead_id, client_id, status)
  select 'delete-test-lead-' || c.id, c.id, 'converted'
  from clients c where c.email in ('delete-test-3@example.invalid', 'delete-test-4@example.invalid')
    and not exists (select 1 from leads l where l.lead_id = 'delete-test-lead-' || c.id);
  insert into lead_messages (lead_id, role, content)
  select 'delete-test-lead-' || c.id, 'user', 'delete test transcript line ' || g
  from clients c, generate_series(1, 5) g
  where c.email in ('delete-test-3@example.invalid', 'delete-test-4@example.invalid');
exception when others then
  raise notice 'leads part skipped: %', sqlerrm;
end $$;

-- ── PART 2: BEFORE deleting — note these two results ────────────────────────
select id, name from clients where email like 'delete-test-%@example.invalid' order by id;
select round(sum(amount)::numeric, 4) as all_costs_total from client_costs;

-- ── PART 3: AFTER deleting — everything below must be 0, and the cost total
-- must equal PART 2's (deleted clients' costs become anonymous aggregates) ──
with gone as (
  select split_part(key, ':', 2)::bigint as id from app_settings
  where key like 'client_deletion:%' and value->>'deleted' = 'true'
)
select 'clients' as tbl, count(*) from clients where email like 'delete-test-%@example.invalid'
union all select 'client_activity', count(*) from client_activity where client_id in (select id from gone)
union all select 'client_communications', count(*) from client_communications where client_id in (select id from gone)
union all select 'client_costs', count(*) from client_costs where client_id in (select id from gone)
union all select 'login_codes', count(*) from login_codes where client_id in (select id from gone)
union all select 'client_agents', count(*) from client_agents where client_id in (select id from gone)
union all select 'client_accounts', count(*) from client_accounts where client_id in (select id from gone)
union all select 'leads', count(*) from leads where lead_id like 'delete-test-lead-%'
union all select 'lead_messages', count(*) from lead_messages where lead_id like 'delete-test-lead-%';

select round(sum(amount)::numeric, 4) as all_costs_total from client_costs;
