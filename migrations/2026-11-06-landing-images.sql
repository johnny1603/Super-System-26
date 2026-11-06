-- The client's own photos on landing pages + the stuck-action reaper (2026-11-06).
--
-- landing_page_images (core/landing_images.py): a photo from the client's
-- Drive media folder, COPIED here — resized, WebP, metadata dropped, at most
-- 350 KB. Pages serve it from /lp-media/{client_id}/{id}-{key}.webp, never
-- from Drive. Rows are immutable and client-scoped: a page version names
-- image ids, so every version (and a revert to it) shows exactly its photos.
-- One copy per (client, Drive file, file content): the same photo reused on
-- another version or page is not stored twice. Deleted with the client
-- (core/client_deletion).
--
-- agent_actions_running_idx: the reaper (GET /api/actions/reap, every 5
-- minutes) reads the rows still running, oldest first.
--
-- Until this runs: choosing photos for a page fails plainly on the card
-- ("didn't change the page") and nothing else changes; pages without photos
-- work as before.
--
-- Every statement is idempotent.

create table if not exists landing_page_images (
  id              bigserial primary key,
  client_id       bigint not null,
  key             text not null,
  sha256          text not null,
  mime            text not null default 'image/webp',
  width           int not null default 0,
  height          int not null default 0,
  size_bytes      int not null default 0,
  data_b64        text not null,
  source_file_id  text not null default '',
  source_md5      text not null default '',
  alt             text not null default '',
  created_at      timestamptz not null default now()
);
create index if not exists landing_page_images_client_idx
  on landing_page_images (client_id, id);
create index if not exists landing_page_images_source_idx
  on landing_page_images (client_id, source_file_id, source_md5);

alter table landing_page_images enable row level security;

create index if not exists agent_actions_running_idx
  on agent_actions (status, decided_at);
