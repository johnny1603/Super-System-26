-- Links each client to their sales lead by client_id, ONCE, so the code can
-- stop matching by email (core/lead_tracking.client_sales_lead, 2026-10-18).
--
-- What breaks if this is NOT run: clients whose lead row was only ever found
-- by email lose their proposal/answers in the agents (chat context, package
-- checklist, budget forecast). Each such client raises ONE admin alert
-- ("sales_lead_not_linked") — nothing is guessed.
--
-- Safety rule: a client is linked ONLY when exactly ONE unclaimed, non-upgrade
-- lead carries their email. Anything ambiguous is listed by STEP 1 and left
-- for a human. Idempotent: rows already linked are never touched.

-- STEP 1 (dry run, read-only): what WOULD be linked, and what is ambiguous.
select c.id as client_id,
       min(l.id) as lead_row_id,
       count(l.id) as candidate_leads,
       case when count(l.id) = 1 then 'will link' else 'AMBIGUOUS — link by hand' end as action
from clients c
join leads l
  on lower(l.client_email) = lower(c.email)
 and l.client_id is null
 and (l.answers ->> '_upgrade_request') is null
where c.email is not null and c.email <> ''
  and not exists (select 1 from leads x where x.client_id = c.id
                  and (x.answers ->> '_upgrade_request') is null)
group by c.id
order by c.id;

-- STEP 2 (apply): link the unambiguous ones.
with candidates as (
  select c.id as client_id, min(l.id) as lead_row_id, count(l.id) as n
  from clients c
  join leads l
    on lower(l.client_email) = lower(c.email)
   and l.client_id is null
   and (l.answers ->> '_upgrade_request') is null
  where c.email is not null and c.email <> ''
    and not exists (select 1 from leads x where x.client_id = c.id
                    and (x.answers ->> '_upgrade_request') is null)
  group by c.id
)
update leads l
   set client_id = cand.client_id
  from candidates cand
 where l.id = cand.lead_row_id
   and cand.n = 1
   and l.client_id is null;

-- ROLLBACK: save the STEP 1 output BEFORE running STEP 2 — the 'will link'
-- rows are exactly what STEP 2 changes. To undo one link:
--   update leads set client_id = null where id = <lead row id>;
