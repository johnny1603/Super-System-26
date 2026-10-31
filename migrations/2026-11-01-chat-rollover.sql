-- Daily chat rollover (core/chat_rollover).
-- Hand-apply in the Supabase SQL editor. Idempotent.
--
-- One row per (client, chat channel, Israel date): the dated summary that
-- becomes the agent's memory when the window opens fresh at 08:00. The chat
-- messages themselves are NOT moved or deleted — they stay in
-- client_communications, before window_started_at.
--
-- status 'failed' = the summary failed twice that day: the old window was kept
-- and the admin alerted; the row stops further tries that day.
-- Without this table nothing rolls over and the chat works exactly as before.

create table if not exists chat_summaries (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  client_id bigint not null,
  channel text not null,
  window_date date not null,
  status text not null check (status in ('done', 'failed')),
  window_started_at timestamptz null,
  archived_from timestamptz null,
  archived_to timestamptz null,
  messages_archived integer not null default 0,
  summary jsonb not null default '{}'::jsonb,
  history_chars integer not null default 0,
  context_chars integer not null default 0,
  model text null,
  input_tokens integer not null default 0,
  output_tokens integer not null default 0,
  cost_ils numeric(12, 4) not null default 0,
  error text null,
  unique (client_id, channel, window_date)
);

create index if not exists chat_summaries_window_idx on chat_summaries (client_id, channel, window_date desc);

-- Server-side only (service key); never exposed to the browser's anon key.
alter table chat_summaries enable row level security;
