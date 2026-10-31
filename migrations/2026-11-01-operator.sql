-- The owner's operator agent (core/operator_agent, docs/OPERATOR.md).
-- Hand-apply in the Supabase SQL editor. Idempotent.
--
-- Without this table the operator still answers, runs Tier 2 actions and
-- handles Tier 3 changes (those live in app_settings + alerts) — it only has
-- no memory of earlier messages and saved reports are not kept.
--
-- client_id = the first client a turn touched (null for general turns), so
-- a client's deletion removes the operator rows about that client too; rows
-- older than 30 days are pruned by the operator itself.

create table if not exists operator_messages (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  role text not null check (role in ('owner', 'operator')),
  kind text not null default 'message' check (kind in ('message', 'report')),
  content text not null,
  cards jsonb not null default '[]'::jsonb,
  meta jsonb not null default '{}'::jsonb,
  client_id bigint null
);

create index if not exists operator_messages_created_idx on operator_messages (created_at);
create index if not exists operator_messages_client_idx on operator_messages (client_id);

-- Server-side only (service key); never exposed to the browser's anon key.
alter table operator_messages enable row level security;
