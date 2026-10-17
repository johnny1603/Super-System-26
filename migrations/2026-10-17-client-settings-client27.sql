-- Per-client settings (core/client_settings) need NO schema change: they live
-- in app_settings as one row per client, key 'client_settings:<id>'.
--
-- This file only pins the decision of 2026-10-17: client 27 never gets
-- scheduled ("auto") articles — nothing is written without a request in chat.
-- Billing-exempt accounts already default to OFF in code; this row makes it
-- explicit for 27 whatever its tier. Idempotent: an existing row (e.g. set
-- from the Control Room) is left alone.
--
-- If not run: client 27 follows its tier default — OFF if it is
-- billing_exempt, ON (the pre-existing behaviour) if it is a paying account.

insert into app_settings (key, value, updated_at)
values ('client_settings:27', '{"auto_articles": false}', now())
on conflict (key) do nothing;
