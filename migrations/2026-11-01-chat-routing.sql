-- Per-turn model routing log (core/model_routing).
-- Hand-apply in the Supabase SQL editor. Idempotent.
--
-- One row per agent-chat turn: which tier/model answered, why, whether it was
-- escalated one tier up, its tokens and its cost in ILS. Read by the admin
-- page /admin/models/. Without this table routing still works — only the log
-- (and so the admin view) is empty.

create table if not exists chat_turn_routes (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  client_id bigint null,
  role text not null,
  tier text not null check (tier in ('simple', 'standard', 'heavy')),
  model text not null,
  routed_tier text null,
  reason text null,
  source text null,
  escalated boolean not null default false,
  escalation_cause text null,
  input_tokens integer not null default 0,
  output_tokens integer not null default 0,
  cache_read_tokens integer not null default 0,
  cache_write_tokens integer not null default 0,
  cost_ils numeric(12, 4) not null default 0,
  router_cost_ils numeric(12, 4) not null default 0
);

create index if not exists chat_turn_routes_created_idx on chat_turn_routes (created_at);
create index if not exists chat_turn_routes_client_idx on chat_turn_routes (client_id);

-- Server-side only (service key); never exposed to the browser's anon key.
alter table chat_turn_routes enable row level security;
