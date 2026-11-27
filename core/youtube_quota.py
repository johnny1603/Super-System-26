"""The YouTube upload ceiling, visible BEFORE it is hit (2026-11-28,
docs/PAID-MEDIA.md §3.7).

One Google Cloud project = one quota, shared by EVERY client. What an upload
costs has changed more than once (.claude/skills/youtube, core/youtube_service
header): ~1,600 units of the 10,000/day pool until December 2025, ~100 after,
and current sources describe videos.insert in a bucket of its own —
GOOGLE_UPLOADS_PER_DAY calls a day. The number that binds is the one on the
project's quota page in the Cloud Console; the constant below is what we
believe it shows, never what Google confirmed to this code. Raising it needs
the YouTube API compliance audit.

What is counted is OUR OWN tally (core/api_call_counters, platform "youtube":
one row per day, incremented by youtube_service.upload_video_stream before
each upload) — not a reading from Google, which exposes no "units left" call.
The counter's day is the server's (UTC); Google's resets at midnight Pacific.

- `status()`    used / limit / remaining uploads today, and searches.
- `require(n)`  QuotaExceeded when fewer than n uploads are left — callers ask
                BEFORE producing anything (core/media_order_actions).
- `after_upload(count)`  the owner's alert at ALERT_SHARE of the limit, once a day.

The counter FAILS OPEN (api_call_counters returns 0 when the database is
down): on such a day nothing is counted, the alert does not fire and the page
says the tally is unknown.
"""
import time

SERVICE_NAME = "youtube_quota"
GOOGLE_UPLOADS_PER_DAY = 100      # videos.insert calls/day per project, as documented (Console = the truth)
ALERT_SHARE = 0.5
SEARCH_UNITS = 100                # one search.list, from the separate 10,000-unit pool
UNITS_PER_DAY = 10000
PLATFORM, SEARCH_PLATFORM = "youtube", "youtube_search"


class QuotaExceeded(Exception):
    """Not enough uploads left today: .status says how many."""

    def __init__(self, status: dict, needed: int):
        self.status, self.needed = status, needed
        super().__init__(f"YouTube uploads today: {status.get('used')} of {status.get('limit')} used, "
                         f"{status.get('remaining')} left, {needed} needed")


def limit() -> int:
    """Where OUR code stops uploading: youtube_service's brake, under Google's cap."""
    from core import youtube_service
    return int(youtube_service.DAILY_UPLOAD_LIMIT)


def _today() -> str:
    return time.strftime("%Y-%m-%d")              # the same day the counter writes


def _count(platform: str):
    """Today's count for one counter, or None when it cannot be read."""
    try:
        from core import api_call_counters
        rows = (api_call_counters._db().table("api_call_counters").select("count")
                .eq("platform", platform).eq("call_date", _today()).limit(1).execute().data or [])
        return int(rows[0].get("count") or 0) if rows else 0
    except Exception as e:
        print(f"[{SERVICE_NAME}] counter {platform} unreadable ({type(e).__name__})")
        return None


def status(count=_count) -> dict:
    """{"day", "known", "used", "limit", "google_cap", "remaining", "share",
    "alert_at", "searches", "search_units", "source"}. `known` False = the
    tally could not be read: used/remaining are None, never a guessed zero."""
    used, searches, cap = count(PLATFORM), count(SEARCH_PLATFORM), limit()
    known = used is not None
    return {"day": _today(), "known": known, "used": used, "limit": cap, "google_cap": GOOGLE_UPLOADS_PER_DAY,
            "remaining": max(0, cap - used) if known else None,
            "share": round(used / cap, 2) if known and cap else None,
            "alert_at": int(cap * ALERT_SHARE),
            "searches": searches, "search_units": searches * SEARCH_UNITS if searches is not None else None,
            "units_per_day": UNITS_PER_DAY,
            "source": "our own count of uploads (api_call_counters), not a figure from Google"}


def require(needed: int = 1, count=_count) -> dict:
    """The status when `needed` uploads still fit today; QuotaExceeded when
    they do not. An unreadable tally is NOT a refusal (the counter itself
    fails open) — it is logged, and the upload's own brake still stands."""
    now = status(count)
    if now["known"] and now["remaining"] < int(needed):
        raise QuotaExceeded(now, int(needed))
    if not now["known"]:
        print(f"[{SERVICE_NAME}] tally unreadable — {needed} upload(s) allowed without a quota check")
    return now


def after_upload(count: int, alert=None):
    """Called with the day's count right after it was incremented. ONE alert
    a day: on the upload whose count IS the threshold (the increment is
    atomic, so exactly one upload sees that number). Never raises."""
    try:
        cap = limit()
        if int(count or 0) != max(1, int(cap * ALERT_SHARE)):
            return
        if alert is None:
            from core.agent_base import agent_alert
            alert = lambda text: agent_alert(SERVICE_NAME, [text])      # noqa: E731
        alert(f"YouTube uploads on {_today()}: half of the daily limit is used ({int(cap * ALERT_SHARE)} of {cap}; "
              f"the limit is shared by all clients). Remaining uploads and the live count: /admin/connections/. "
              f"Raising the limit needs the YouTube API compliance audit.")
    except Exception as e:
        print(f"[{SERVICE_NAME}] alert not sent ({type(e).__name__})")
