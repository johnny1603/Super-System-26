"""Persistent API call counters, shared by core/google_ads_service.py (fixed
daily window, Explorer Access's 2,880 ops/day) and core/meta_service.py
(rolling 15-day window, the 500-call Full Access qualification threshold).

Replaces the old in-memory dict counters in both files, which reset on every
Cloud Run restart (deploy, scale-to-zero) - useless as real trackers of a
window that spans hours or weeks when the container doesn't live that long.
Backed by the `api_call_counters` table + `increment_api_call_counter` SQL
function (day-granularity rows; see .claude/skills/api-quotas/SKILL.md for
the schema and setup SQL).

One round trip per counted call: increment_call_counter() atomically upserts
today's row and returns the SUM over the trailing `window_days` days
(including today) - window_days=1 gives a plain daily counter (Google),
window_days=15 gives Meta's rolling accumulation.
"""
import os
import time

# Created lazily - no DB client at import time (api_server imports every
# service-backed agent at startup; core/*_service.py must stay side-effect-free)
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def increment_call_counter(platform: str, window_days: int = 1) -> int:
    """Increment today's row for `platform` and return the window total (the
    number that actually matters against whatever limit/threshold is being
    tracked).

    FAILS OPEN: if Supabase is unreachable, logs a warning and returns 0
    rather than blocking real Ads/Graph API traffic on a persistence hiccup -
    this is a best-effort safety net, not the source of truth for billing or
    the limit itself (Google/Meta enforce the real limit server-side
    regardless of what this counter says)."""
    today = time.strftime("%Y-%m-%d")
    try:
        result = _db().rpc("increment_api_call_counter", {
            "p_platform": platform,
            "p_date": today,
            "p_window_days": window_days,
        }).execute()
        return result.data or 0
    except Exception as e:
        print(f"[api_call_counters] could not persist/read counter for "
              f"{platform} (non-fatal, failing open): {e}")
        return 0
