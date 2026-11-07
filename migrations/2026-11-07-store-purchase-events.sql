-- Our own purchase records (core/store_purchases, docs/STORE.md).
-- One row per client + order number: order number, total, currency. Nothing
-- about the buyer. Idempotent: safe to run more than once.
create table if not exists store_purchase_events (
  id bigserial primary key,
  client_id bigint not null references clients(id) on delete cascade,
  order_ref text not null,
  value numeric(14, 2),
  currency text,
  created_at timestamptz not null default now()
);
create unique index if not exists store_purchase_events_client_order
  on store_purchase_events (client_id, order_ref);
create index if not exists store_purchase_events_client_time
  on store_purchase_events (client_id, created_at);
alter table store_purchase_events enable row level security;
