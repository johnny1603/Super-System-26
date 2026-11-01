-- Landing pages, fully owned by uallak (docs/LANDING_PAGES.md, 2026-11-01).
--
-- Builds on migrations/2026-08-01-landing-pages.sql (run that first).
-- Adds: a version per edit with one-tap revert, which version is LIVE, page
-- contact links (call / WhatsApp / store), and a per-page daily counter for
-- views and clicks (the Performance page and the hosting-cost estimate).
--
-- Until this runs: pages keep working exactly as before — the live page is
-- served from landing_pages.content, edits are not versioned, and the
-- counters are skipped silently (core/landing_pages falls back on its own).
-- After it runs: open /admin/landing/ → "גיבוי והעברה" to back up and
-- create version 1 for every existing page (core/landing_admin.migrate).
--
-- Every statement is idempotent.

alter table landing_pages add column if not exists current_version int not null default 0;
alter table landing_pages add column if not exists published_version int;
alter table landing_pages add column if not exists published_at timestamptz;
alter table landing_pages add column if not exists unpublished_reason text not null default '';
alter table landing_pages add column if not exists contact jsonb not null default '{}'::jsonb;

-- One row per edit (generated, client edit, revert, migration). Never updated,
-- never deleted while the page exists; revert copies an old row into a NEW one.
create table if not exists landing_page_versions (
  id          bigserial primary key,
  page_id     bigint not null references landing_pages(id) on delete cascade,
  client_id   bigint not null,
  version     int not null,
  title       text not null default '',
  content     jsonb not null default '{}'::jsonb,
  contact     jsonb not null default '{}'::jsonb,
  source      text not null default '',
  size_bytes  int not null default 0,
  created_at  timestamptz not null default now()
);
create unique index if not exists landing_page_versions_page_version_idx
  on landing_page_versions (page_id, version);
create index if not exists landing_page_versions_client_idx
  on landing_page_versions (client_id);

-- Counters per page per day. Leads are NOT here: they are client_leads rows
-- (source_detail 'lp:<slug>', created_at), the one record of a lead.
create table if not exists landing_page_stats (
  page_id     bigint not null,
  client_id   bigint not null,
  day         date not null,
  views       int not null default 0,
  calls       int not null default 0,
  whatsapp    int not null default 0,
  store       int not null default 0,
  bytes       bigint not null default 0,
  primary key (page_id, day)
);
create index if not exists landing_page_stats_client_day_idx
  on landing_page_stats (client_id, day);

-- Atomic increment (a read-modify-write from the app would lose counts under
-- concurrent visits). p_field is checked against a fixed list.
create or replace function landing_stat_bump(p_page bigint, p_client bigint, p_field text, p_bytes bigint)
returns void language plpgsql as $$
begin
  if p_field not in ('views', 'calls', 'whatsapp', 'store') then
    raise exception 'bad field';
  end if;
  insert into landing_page_stats (page_id, client_id, day) values (p_page, p_client, current_date)
    on conflict (page_id, day) do nothing;
  execute format('update landing_page_stats set %I = %I + 1, bytes = bytes + $1 '
                 'where page_id = $2 and day = current_date', p_field, p_field)
    using coalesce(p_bytes, 0), p_page;
end $$;
