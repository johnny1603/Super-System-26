-- Switch uallak's own Supabase footprint watch from a ROW-COUNT proxy to the
-- real ceiling: database SIZE.
-- Run in the Supabase SQL editor. Idempotent — safe to re-run.
--
-- Why: Supabase caps database SIZE, never row count, on every plan tier. The
-- old supabase_row_budget (400,000) was an admitted placeholder — see the note
-- it replaced in core/admin_service.py. Verified in the dashboard on
-- 2026-07-30: org is on the FREE plan, whose ceiling is 0.5 GB per project,
-- with 25.84 MB then in use.
--
-- Until this runs, get_platform_usage() reports measured=false and the admin
-- drawer shows "not measured yet" instead of a percentage. Nothing else in the
-- app depends on it — no scan, alert or client-facing path breaks.

-- pg_database_size() is not reachable through PostgREST's table API, so it is
-- exposed as an RPC. security definer + a pinned search_path so the function
-- cannot be hijacked by a caller-controlled schema; execute is granted to
-- service_role ONLY (the key the app uses), never to anon/authenticated, so
-- infrastructure size is not readable from a browser.
create or replace function public.db_size_bytes()
returns bigint
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select pg_database_size(current_database());
$$;

revoke all on function public.db_size_bytes() from public;
revoke all on function public.db_size_bytes() from anon;
revoke all on function public.db_size_bytes() from authenticated;
grant execute on function public.db_size_bytes() to service_role;

-- Carry over a customised warn threshold, if one was ever saved, so renaming
-- supabase_row_warn_pct -> supabase_warn_pct doesn't silently reset it to 80.
insert into app_settings (key, value, updated_at)
select 'supabase_warn_pct', value, now()
from app_settings
where key = 'supabase_row_warn_pct'
on conflict (key) do nothing;

-- Both old keys are dead once the app reads the new ones: supabase_row_budget
-- measured a limit that does not exist, and supabase_row_warn_pct has been
-- copied above. update_settings() whitelists on DEFAULT_SETTINGS, so these can
-- never be written again — this only clears the stale rows.
delete from app_settings where key in ('supabase_row_budget', 'supabase_row_warn_pct');
