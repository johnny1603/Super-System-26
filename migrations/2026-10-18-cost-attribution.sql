-- Cost attribution + metering categories (core/cost_tracker.py). Idempotent.
--
-- What breaks if this is NOT run: nothing is lost. record_cost falls back to
-- the old columns and keeps lead_id / attribution / cost_category inside
-- `details` (logged once as an ADMIN line); the admin monthly view still
-- works from `details`. But link_lead_costs (sales-chat costs moving to the
-- client on signup) needs the lead_id COLUMN and does nothing until this runs.

alter table client_costs add column if not exists lead_id text;
alter table client_costs add column if not exists attribution text;    -- client | lead | internal | unattributed
alter table client_costs add column if not exists cost_category text;  -- chat | proposal | content | media | seo | infra

create index if not exists client_costs_lead_id_idx on client_costs (lead_id);
create index if not exists client_costs_created_at_idx on client_costs (created_at);

-- ROLLBACK:
--   alter table client_costs drop column if exists cost_category;
--   alter table client_costs drop column if exists attribution;
--   alter table client_costs drop column if exists lead_id;
