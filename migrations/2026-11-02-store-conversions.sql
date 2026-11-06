-- Per-client conversion types + the store connection's catalog row (2026-11-02).
--
-- core/conversion_types.py: what counts as a conversion for each client —
-- form_submit, whatsapp_click, phone_click, store_sales. Existing clients keep
-- form_submit (the column default). conversion_types_confirmed_at is when the
-- client approved the choice on its card; NULL = never chosen, so the agents
-- ask how customers reach the business.
--
-- Shin Sekai (client 27, internal) is set to store_sales ONLY — the owner
-- already gets WhatsApp alerts and does not want WhatsApp / phone / form lead
-- measurement (changed 2026-11-07 from whatsapp_click + store_sales, so a
-- re-run can never put WhatsApp back). Only while it has never been
-- confirmed, so a re-run never overwrites a later choice. No other client
-- changes.
--
-- connection_catalog 'store': included in the website package, no vendor
-- account to open (the chat walks the client through creating a READ key).
--
-- Idempotent: safe to run more than once.

alter table clients add column if not exists conversion_types text[] not null default '{form_submit}';
alter table clients add column if not exists conversion_types_confirmed_at timestamptz;

update clients
   set conversion_types = '{store_sales}',
       conversion_types_confirmed_at = now()
 where id = 27
   and conversion_types_confirmed_at is null;

do $$
begin
  if to_regclass('public.connection_catalog') is not null then
    insert into connection_catalog (key, tier, signup_url, pricing_url, recommended_plan_note, help_notes, enabled)
    values ('store', 'included', '', '', '', '', true)
    on conflict (key) do nothing;
  end if;
end $$;
