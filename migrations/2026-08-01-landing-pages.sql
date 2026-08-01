-- Landing pages: lightweight, code-built, conversion-focused pages, served by
-- THIS app from a single shared route (/lp/{client_slug}/{page_slug}).
--
-- Deliberately NOT WordPress. website_agent builds the client's main site (a
-- full business presence, WordPress, InstaWP-hosted). A landing page is the
-- opposite shape: one offer, one form, no navigation, fast. The two never
-- share a table, a renderer or a hosting path — same discipline as
-- leads/client_leads.
--
-- CONTENT IS STRUCTURED, NEVER RAW HTML. `content` holds headline/sections/
-- cta strings that core/landing_pages.py escapes into a fixed template. This
-- is a SECURITY boundary, not a style choice: these pages are served from
-- app.uallak.com, the same origin as the client dashboard and its session
-- cookie, so stored raw HTML would be stored XSS against every logged-in user.
-- If you ever add a "custom HTML" feature, it must serve from a different
-- origin — do not relax this here.
--
-- Until this runs: the Landing Pages dashboard section shows an honest empty
-- state and every /api/landing-pages* call returns ERR_LANDING_UNAVAILABLE.
-- Nothing else in the app breaks — no existing code path reads this table.
--
-- Every statement is idempotent.

create table if not exists landing_pages (
  id            bigserial primary key,
  client_id     bigint not null,
  -- URL segment, unique per client: /lp/{client_slug}/{slug}
  slug          text not null,
  title         text not null default '',
  -- Which offer/campaign this page is for — the client answers this in chat
  -- (never a separate form), and the copy generator reads it.
  goal          text not null default '',
  -- Structured content ONLY (see the header note). Rendered through a fixed
  -- template with every value escaped.
  content       jsonb not null default '{}'::jsonb,
  -- draft = built, not publicly reachable; published = live at its URL.
  -- Same draft-first principle as website_agent.publish_content.
  status        text not null default 'draft',
  -- Set when the copy came from the generator, for the review-in-chat flow
  copy_source   text not null default '',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- The public route looks up exactly one page by (client, slug) on every
-- request, so this index is the hot path — and it enforces the uniqueness the
-- URL scheme depends on.
create unique index if not exists landing_pages_client_slug_idx
  on landing_pages (client_id, slug);

-- "This client's pages, newest first" — the dashboard list and the 3-page
-- limit check both read it.
create index if not exists landing_pages_client_created_idx
  on landing_pages (client_id, created_at desc);

-- The per-client landing DOMAIN is deliberately NOT a table here: it reuses
-- the existing client_accounts shape (platform='landing_domain',
-- account_id=the hostname, status='pending'|'active'), exactly like every
-- other per-client external resource. No migration needed for it.
