-- Anthropic credits widget (core/anthropic_credits, docs/ANTHROPIC-CREDITS.md)
-- The balance the owner types after each top-up in the Anthropic Console.
-- Idempotent: safe to run more than once.
--
-- Until this runs: the admin widget still shows the measured Claude spend
-- (24 h / 7 d / daily burn) and says the table is missing; entering a balance
-- answers 503, and no remaining / days-left estimate or threshold notice exists.

create table if not exists anthropic_credit_balances (
    id bigserial primary key,
    balance_usd numeric(12, 2) not null check (balance_usd >= 0),
    entered_at timestamptz not null default now(),
    entered_by text,
    note text
);

create index if not exists anthropic_credit_balances_entered_at_idx
    on anthropic_credit_balances (entered_at desc);

-- Server-side only (service key); nothing reads it through the public API
alter table anthropic_credit_balances enable row level security;
