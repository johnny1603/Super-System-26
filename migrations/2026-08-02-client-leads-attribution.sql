-- Traffic attribution on the leads a CLIENT receives.
--
-- WHY NEW COLUMNS RATHER THAN REUSING THE ONES ON `leads`:
-- the utm_*/click_ids/source_* columns already exist — but on `leads`, which
-- is uallak's OWN acquisition funnel (one row per prospect who landed on our
-- sales chat). `client_leads` is the opposite direction: people who contacted
-- our CLIENT. CLAUDE.md calls conflating the two "the single most expensive
-- misreading in this codebase", and the two tables deliberately never join.
-- So the columns cannot be reused; they are MIRRORED here with identical
-- names, types and semantics so the two tables stay legible to each other and
-- so core/lead_tracking.classify_source() can serve both unchanged.
--
-- Until this runs: capture still works and leads are still stored — the insert
-- simply carries no attribution, and every classified value is dropped by
-- PostgREST as an unknown column... which would in fact ERROR. So
-- core/client_leads.capture_lead() writes these fields only when it can, and
-- retries without them on failure (see _insert_lead there). Nothing is lost;
-- attribution is simply absent until this migration lands.
--
-- Every statement is idempotent.

-- The campaign tags a tracked link carries. Same five fields as `leads`.
alter table client_leads add column if not exists utm_source   text;
alter table client_leads add column if not exists utm_medium   text;
alter table client_leads add column if not exists utm_campaign text;
alter table client_leads add column if not exists utm_content  text;
alter table client_leads add column if not exists utm_term     text;

-- The classifier's verdict (core/lead_tracking.classify_source — shared with
-- `leads`, not a second implementation):
--   source_platform   google_ads | meta | tiktok | microsoft_ads | organic |
--                     referral | direct
--   source_confidence click_id | utm | referrer | none — how much the platform
--                     above is actually worth. `none` reports as `direct` and
--                     is never upgraded to a guess.
alter table client_leads add column if not exists source_platform   text;
alter table client_leads add column if not exists source_confidence text;

-- Raw evidence, kept so a classification can always be re-derived or argued
-- with later. click_ids is a jsonb map ({"gclid": "..."}) exactly like `leads`.
alter table client_leads add column if not exists referrer     text;
alter table client_leads add column if not exists landing_path text;
alter table client_leads add column if not exists click_ids    jsonb default '{}'::jsonb;

-- "How many of this client's leads came from paid vs organic" is the question
-- this whole migration exists to answer, and it is always scoped to one client.
create index if not exists client_leads_client_platform_idx
  on client_leads (client_id, source_platform);

-- NOTE: rows created before this migration keep source_platform NULL on
-- purpose. That is "we did not measure this", which is a different and more
-- honest statement than "direct" — anything reading these must render the two
-- differently rather than defaulting NULL to direct.
