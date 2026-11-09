-- Client chat: per-message timing, one-turn-at-a-time locks, frustration alerts
-- (core/chat_timing, core/chat_turns, core/frustration — docs/CHAT-TURNS.md).
-- Idempotent: safe to run more than once.
--
-- Until this runs:
-- - chat_turn_timings missing: each turn's timing is only a [chat_timing] log
--   line in Cloud Logging; /admin/models/ says the table is missing.
-- - chat_turn_locks missing: turns are serialized per Cloud Run INSTANCE only
--   (two instances could still answer the same conversation at once), and the
--   "already answered" mark lives in memory (lost on restart).
-- - client_frustration_alerts missing: a confirmed incident becomes ONE row in
--   alerts (source 'frustration') + the email; the bell says the table is missing.

create table if not exists chat_turn_timings (
    id bigserial primary key,
    created_at timestamptz not null default now(),
    client_id bigint not null,
    persona text,
    channel text,
    total_ms integer not null default 0,
    queue_ms integer not null default 0,
    context_ms integer not null default 0,
    model_ms integer not null default 0,
    post_ms integer not null default 0,
    model_calls integer not null default 0,
    dominant text,
    merged_messages integer not null default 1,
    restarted boolean not null default false,
    outcome text,
    stages jsonb not null default '{}'::jsonb
);

create index if not exists chat_turn_timings_created_idx on chat_turn_timings (created_at desc);
create index if not exists chat_turn_timings_client_idx on chat_turn_timings (client_id, created_at desc);

create table if not exists chat_turn_locks (
    client_id bigint not null,
    channel text not null,
    holder text,
    expires_at timestamptz,
    covered_through bigint not null default 0,
    primary key (client_id, channel)
);

create table if not exists client_frustration_alerts (
    id bigserial primary key,
    client_id bigint not null,
    client_name text,
    channel text not null,
    persona text,
    severity text not null default 'medium' check (severity in ('medium', 'high')),
    reasons jsonb not null default '[]'::jsonb,
    summary text,
    messages jsonb not null default '[]'::jsonb,
    suggested_action text,
    status text not null default 'open' check (status in ('open', 'handled')),
    message_count integer not null default 1,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    handled_at timestamptz,
    handled_by text
);

create index if not exists client_frustration_alerts_open_idx
    on client_frustration_alerts (client_id, channel, status, last_seen_at desc);
create index if not exists client_frustration_alerts_seen_idx
    on client_frustration_alerts (last_seen_at desc);

-- Server-side only (service key); nothing reads these through the public API
alter table chat_turn_timings enable row level security;
alter table chat_turn_locks enable row level security;
alter table client_frustration_alerts enable row level security;
