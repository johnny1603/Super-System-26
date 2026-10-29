-- Outreach (Gmail agent package). Safe to run NOW, with GMAIL_SEND_ENABLED off:
-- the Outreach page (core/outreach_tracker) reads these tables to show found
-- businesses, drafts and the client's own marks before sending is switched on.
-- Idempotent — run it again after any edit of this file.
--
-- What breaks if this is NOT run:
-- - the Outreach page shows only drafts (agent_actions) and says the record
--   is not set up yet; "Replied" / "Asked to be removed" cannot be saved;
-- - with the flag on, nothing is sent: outreach_policy.is_suppressed and
--   already_contacted FAIL CLOSED on a missing table, and
--   gmail_outreach.sent_today treats it as "cap reached".
--
-- outreach_suppression has NO integer client_id column on purpose: client
-- deletion clears every table that has one (schema discovery), and a "do not
-- contact" must outlive the lead and the client. Rows are only ever added.
create table if not exists outreach_suppression (
    id            bigint generated always as identity primary key,
    key_type      text not null check (key_type in ('email', 'domain', 'phone', 'place_id')),
    key_value     text not null,
    reason        text not null default '',
    source        text not null default '',
    added_by      text not null default '',
    requested_for text not null default '',     -- free text ("client 27"), NOT a client id
    requested_at  timestamptz not null default now(),
    unique (key_type, key_value)
);

-- Prospecting's discovered contacts (docs/PROSPECTING.md §10). Read by
-- outreach_policy.already_contacted; created here so that check can pass.
create table if not exists prospect_contacts (
    id           bigint generated always as identity primary key,
    client_id    bigint not null,
    place_id     text not null default '',
    email        text not null default '',
    source_url   text not null default '',
    found_at     timestamptz not null default now(),
    contacted_at timestamptz
);
create index if not exists prospect_contacts_client_idx on prospect_contacts (client_id);

-- THE record of every outreach e-mail sent from a client's Gmail
-- (core/gmail_outreach.send_approved): one row per send, written right after
-- users.messages.send succeeds. Also the hard daily cap and the "one message
-- per business" check. Deleted at account closure
-- (google_data_purge.LIVE_GOOGLE_DERIVED_TABLES: it holds Gmail message ids)
-- and by admin delete (integer client_id).
create table if not exists outreach_sends (
    id               bigint generated always as identity primary key,
    client_id        bigint not null,
    to_email         text not null,
    business_name    text not null default '',
    subject          text not null default '',
    body             text not null default '',
    source_url       text not null default '',   -- the business's page we read for the draft
    from_email       text not null default '',
    gmail_message_id text not null default '',
    proposal_id      bigint,                      -- agent_actions row the client approved
    sent_at          timestamptz not null default now()
);
create index if not exists outreach_sends_client_idx on outreach_sends (client_id, sent_at desc);
create index if not exists outreach_sends_to_idx on outreach_sends (client_id, to_email);

-- 'first' = the one approach; 'followup' = the ONE follow-up after 7 days of
-- silence (outreach_policy.followup_refusal). Max one followup per business.
alter table outreach_sends add column if not exists kind text not null default 'first'
    check (kind in ('first', 'followup'));

-- Our OWN data about a found business, never Google Places content
-- (docs/PROSPECTING.md §3): business_name only as written on the business's
-- own site or in our draft; area = the search area WE chose (our query).
alter table prospect_contacts add column if not exists business_name text not null default '';
alter table prospect_contacts add column if not exists area text not null default '';
alter table prospect_contacts add column if not exists source text not null default 'places'
    check (source in ('places', 'chat'));

-- What the CLIENT tells us from their own inbox (we never read it): one row
-- per click, newest wins. 'removed' also writes outreach_suppression first.
create table if not exists outreach_marks (
    id         bigint generated always as identity primary key,
    client_id  bigint not null,
    to_email   text not null,
    mark       text not null check (mark in ('replied', 'removed', 'bounced')),
    marked_by  text not null default 'client',     -- client | staff
    marked_at  timestamptz not null default now()
);
create index if not exists outreach_marks_client_idx on outreach_marks (client_id, to_email, marked_at desc);

-- Service-role only, like every other table here: RLS on, no policies.
alter table outreach_suppression enable row level security;
alter table prospect_contacts enable row level security;
alter table outreach_sends enable row level security;
alter table outreach_marks enable row level security;
