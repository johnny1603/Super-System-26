-- Client chat speed report — last 48 hours (docs/CHAT-SPEED.md). READ-ONLY: SELECT only.
-- Paste into the Supabase SQL editor and run. Needs chat_turn_timings
-- (migrations/2026-11-09-chat-timing-frustration.sql); if that table is missing,
-- the numbers are only in the [chat_timing] Cloud Run log lines.
--
-- Message length: rows written after the 2026-11-11 deploy carry it in
-- stages->'flags'->>'message_chars'. For older rows it is approximated from the
-- client's latest inbound message stored before the turn finished (the turn's own
-- message is logged before its timing row is written). "short" = under 40 chars.

with t as (
    select tt.*,
           coalesce(nullif(tt.stages->'flags'->>'message_chars', '')::int,
                    (select char_length(c.content)
                       from client_communications c
                      where c.client_id = tt.client_id
                        and c.direction = 'inbound'
                        and c.created_at <= tt.created_at
                        and c.created_at >= tt.created_at - interval '10 minutes'
                      order by c.created_at desc
                      limit 1)) as chars,
           coalesce(tt.stages->'flags'->>'route', 'before routing') as route,
           coalesce(tt.stages->'flags'->>'route_model',
                    tt.stages->'calls'->0->>'model') as model
      from chat_turn_timings tt
     where tt.created_at >= now() - interval '48 hours'
)
select grp, key, count(*) as turns,
       round(avg(total_ms)) as avg_ms,
       percentile_cont(0.5) within group (order by total_ms) as median_ms,
       percentile_cont(0.9) within group (order by total_ms) as p90_ms,
       round(avg(queue_ms)) as queue_ms, round(avg(context_ms)) as context_ms,
       round(avg(model_ms)) as model_ms, round(avg(post_ms)) as post_ms,
       round(avg(model_calls), 2) as model_calls,
       mode() within group (order by dominant) as dominant,
       round(100.0 * count(*) filter (where chars < 40 and total_ms > 60000)
             / nullif(count(*) filter (where chars < 40), 0), 1) as short_over_60s_pct
  from (
        select 'persona' as grp, coalesce(persona, '?') as key, * from t
        union all
        select 'length', case when chars is null then 'unknown'
                              when chars < 40 then 'short (<40)' else 'long' end, * from t
        union all
        select 'route', route || ' : ' || coalesce(model, '?'), * from t
        union all
        select 'all', 'all', * from t
       ) g
 group by grp, key
 order by grp, turns desc;
