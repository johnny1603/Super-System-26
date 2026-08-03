-- uallak's OWN recurring operating costs (core/operating_costs.py).
--
-- Why a table and not app_settings: this is a LIST that grows (one row per
-- paid service), each with its own amount, cadence, note and "when did Johnny
-- last confirm this number" timestamp. app_settings is a flat key->value store
-- whose writable keys are whitelisted in code — wrong shape for this.
--
-- Only MANUALLY MAINTAINED numbers live here. Costs the system can measure
-- (Claude API tokens) or derive from a live count (InstaWP sites) are never
-- written here — they are computed at read time, so they can never go stale.
-- The service catalog itself lives in code (operating_costs.SERVICES); this
-- table only carries the amounts a human typed.
--
-- IF THIS HASN'T BEEN RUN: the operating-costs view still works. Measured and
-- derived rows show real numbers; every manual row reports "not set yet" and
-- the UI shows a banner naming this file. Nothing 500s, nothing shows a
-- fabricated 0.

create table if not exists operating_costs (
  service_key  text primary key,
  amount_ils   numeric not null default 0,
  cadence      text not null default 'monthly',   -- 'monthly' | 'yearly'
  note         text default '',
  confirmed_at timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

comment on table operating_costs is
  'Manually maintained recurring costs for uallak itself. Amounts a human typed; '
  'measured/derived costs are computed at read time and never stored here.';

comment on column operating_costs.confirmed_at is
  'When a human last CONFIRMED this number is still right — the UI ages it and '
  'nudges for a re-check. Updated on every write, including a no-change re-save.';
