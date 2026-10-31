-- The client's chosen account manager (core/manager_identity.py) — the ONE
-- stored value behind the concierge's name and face everywhere: chat, cards,
-- emails, WhatsApp notifications and the daily login greeting.
-- Idempotent — run it again after any edit of this file.
--
-- NULL = never chosen: core/manager_identity.default_key resolves it (the old
-- female companion -> 'elinor', everyone else -> 'johnny'). No backfill on
-- purpose, so the dashboard can still offer the picker to existing clients.
--
-- What breaks if this is NOT run: nothing crashes — every reader falls back to
-- the default manager — but the client's choice cannot be saved
-- (/api/client/profile answers ERR_MANAGER_NOT_SAVED) and the picker keeps
-- reappearing on every visit.
alter table clients add column if not exists manager_persona text;

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'clients_manager_persona_check') then
        alter table clients add constraint clients_manager_persona_check
            check (manager_persona is null or manager_persona in ('johnny', 'nissim', 'elinor'));
    end if;
end $$;
