-- Control Room: Johnny's private conversations with a client's agent personas.
--
-- Its own table ON PURPOSE, not client_communications / client_activity:
-- both of those are exported to the CLIENT in their data archive
-- (api_server._build_client_export) and read by the client's chat history
-- endpoints. Nothing about these conversations may ever reach the client, so
-- they live where no client-facing code path reads.
--
-- Offboarding deletes a client's rows (api_server, after the main purge).

create table if not exists admin_agent_chat (
    id          bigserial primary key,
    client_id   bigint not null,
    persona     text   not null,                 -- 'general' | support_agent.PERSONAS key
    role        text   not null check (role in ('admin', 'agent')),
    content     text   not null,
    created_at  timestamptz not null default now()
);

create index if not exists admin_agent_chat_thread_idx
    on admin_agent_chat (client_id, persona, created_at desc);

-- Service-role only, like every other table here: RLS on, no policies.
alter table admin_agent_chat enable row level security;
