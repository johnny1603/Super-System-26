-- PROPOSAL — NOT APPLIED. Review before running in the Supabase SQL editor.
--
-- The client's AGREED target keywords for the Performance page's organic
-- section (core/rank_tracking.py). Today no structured store exists: keyword
-- themes live only as free text in seo_agent's strategy and the cached
-- research, and the sales chat's organic_target_phrases answer is one
-- free-text field on the lead row.
--
-- What breaks if this is NOT run: nothing. rank_tracking.get_target_keywords
-- catches the missing table and the page shows "no target keywords yet".
--
-- Proposed admin edit screen (NOT built): a "מילות מפתח יעד" panel in the
-- Control Room per client — list, add, remove, set target_position (default 1),
-- with a one-click "import from the sales chat answer" that splits
-- organic_target_phrases into rows for the admin to confirm. The internal soft
-- cap (TARGET_KEYWORDS_SOFT_CAP, default 30) would show there as a warning
-- only; it is never shown to the client and blocks nothing.

create table if not exists client_target_keywords (
    id bigint generated always as identity primary key,
    client_id bigint not null references clients(id) on delete cascade,
    keyword text not null,
    target_position int not null default 1 check (target_position >= 1),
    created_at timestamptz not null default now(),
    unique (client_id, keyword)
);

create index if not exists client_target_keywords_client_idx
    on client_target_keywords (client_id);
