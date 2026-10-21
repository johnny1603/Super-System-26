-- Agent action layer (core/agent_actions): every action an agent PROPOSES in a
-- chat, and what the human who approved or cancelled it caused. One row per
-- proposal; its status moves forward only (compare-and-set in code), which is
-- what makes a double-clicked "Approve" run the action once.
--
-- Deliberately holds NO Google-sourced data: action name, whitelisted params
-- (a suggestion id, a GA4 measurement id from the client's public site), who
-- decided, when, and a short outcome CODE. Campaign/video ids and error texts
-- go to the chat history and the (redacted) logs, which the closure and delete
-- purges already cover. Admin delete clears these rows through schema
-- discovery (client_deletion.client_tables: an integer client_id column).
--
-- Until this has run: agents still answer from client_state, but cannot offer
-- an action card — propose() fails closed and the chat says the team will do
-- it. Nothing else breaks.

create table if not exists agent_actions (
    id           bigserial primary key,
    client_id    bigint not null,
    action       text   not null,                 -- agent_actions.ACTIONS key
    params       jsonb  not null default '{}'::jsonb,
    origin       text   not null check (origin in ('client_chat', 'admin_chat')),
    persona      text   not null default 'general',
    approver     text   not null check (approver in ('client', 'admin')),
    status       text   not null check (status in ('proposed', 'awaiting_staff', 'running',
                                                   'done', 'failed', 'cancelled', 'expired')),
    created_at   timestamptz not null default now(),
    decided_at   timestamptz,
    decided_by   text,                            -- 'client' | 'admin'
    finished_at  timestamptz,
    outcome      text                             -- short code, never a Google payload
);

create index if not exists agent_actions_open_idx
    on agent_actions (client_id, status, created_at desc);

-- Service-role only, like every other table here: RLS on, no policies.
alter table agent_actions enable row level security;
