"""The YouTube daily ceilings, visible BEFORE they are hit (2026-11-28,
docs/PAID-MEDIA.md §3.7).

One Google Cloud project = one quota, shared by EVERY client. The figures, as
the owner confirmed them on 2026-11-28 — do not bring back older ones:

- videos.insert: ~1,600 units until 4 December 2025, ~100 after, and since
  1 June 2026 its OWN bucket — 100 calls a day per project, 1 unit each.
  **The 1,600-unit / six-uploads-a-day figure is dead. It is not the limit.**
- search.list: since the same 1 June 2026 change its OWN bucket too —
  100 calls a day per project. It no longer competes with uploads.
- everything else (channels, playlistItems, videos.list ...): the general
  10,000 units/day pool, 1 unit a call. Not counted here.

Raising either bucket needs the YouTube API compliance audit. The project's
quota page in the Cloud Console is the final word; this code never read it.

What is counted is OUR OWN tally (core/api_call_counters: one row per bucket
per day, incremented in core/youtube_service before each call) — Google
exposes no "calls left" endpoint. Calls made outside this system are not seen.

THE DAY IS GOOGLE'S: quotas reset at midnight Pacific Time, so the tally's day
is the Pacific date (`pacific_day`), never UTC or the server's.

- `status()`              per bucket: used / limit / remaining today.
- `require(n, bucket=)`   QuotaExceeded when fewer than n calls are left —
                          asked BEFORE anything is produced or spent.
- `count_call(bucket)`    counts one call on Google's day (youtube_service).
- `after_call(bucket, n)` the owner's alert at ALERT_SHARE, once a day.

The counter FAILS OPEN (api_call_counters returns 0 when the database is
down): on such a day nothing is counted, the alert does not fire and the page
says the tally is unknown.
"""
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "youtube_quota"
ALERT_SHARE = 0.5
UPLOAD, SEARCH = "upload", "search"
# bucket -> (api_call_counters platform, Google's documented calls/day, our brake's name in youtube_service)
BUCKETS = {UPLOAD: ("youtube", 100, "DAILY_UPLOAD_LIMIT"),
           SEARCH: ("youtube_search", 100, "DAILY_SEARCH_LIMIT")}
LABEL = {UPLOAD: "uploads", SEARCH: "searches"}


class QuotaExceeded(Exception):
    """Not enough calls left today in one bucket: .status says how many."""

    def __init__(self, status: dict, needed: int, bucket: str = UPLOAD):
        self.status, self.needed, self.bucket = status, needed, bucket
        super().__init__(f"YouTube {LABEL[bucket]} today: {status.get('used')} of {status.get('limit')} used, "
                         f"{status.get('remaining')} left, {needed} needed")


def limit(bucket: str = UPLOAD) -> int:
    """Where OUR code stops: youtube_service's brake, under Google's cap."""
    from core import youtube_service
    return int(getattr(youtube_service, BUCKETS[bucket][2]))


def _nth_sunday(year: int, month: int, n: int) -> datetime:
    first = datetime(year, month, 1, tzinfo=timezone.utc)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def pacific_day(now: datetime = None) -> str:
    """Today's date in US Pacific Time — the clock Google's quota resets on.
    The US rule, computed here (no tz database needed): daylight time from the
    second Sunday of March 02:00 PST to the first Sunday of November 02:00 PDT."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    starts = _nth_sunday(now.year, 3, 2) + timedelta(hours=10)      # 02:00 PST = 10:00 UTC
    ends = _nth_sunday(now.year, 11, 1) + timedelta(hours=9)        # 02:00 PDT = 09:00 UTC
    offset = 7 if starts <= now < ends else 8
    return (now - timedelta(hours=offset)).strftime("%Y-%m-%d")


def count_call(bucket: str) -> int:
    """Count ONE call in a bucket on Google's day; the day's total (0 when the
    counter could not be written — it fails open)."""
    from core.api_call_counters import increment_call_counter
    return increment_call_counter(BUCKETS[bucket][0], window_days=1, day=pacific_day())


def _count(platform: str):
    """Today's count for one counter, or None when it cannot be read."""
    try:
        from core import api_call_counters
        rows = (api_call_counters._db().table("api_call_counters").select("count")
                .eq("platform", platform).eq("call_date", pacific_day()).limit(1).execute().data or [])
        return int(rows[0].get("count") or 0) if rows else 0
    except Exception as e:
        print(f"[{SERVICE_NAME}] counter {platform} unreadable ({type(e).__name__})")
        return None


def _bucket(bucket: str, count) -> dict:
    platform, google_cap, _name = BUCKETS[bucket]
    used, cap = count(platform), limit(bucket)
    known = used is not None
    return {"known": known, "used": used, "limit": cap, "google_cap": google_cap,
            "remaining": max(0, cap - used) if known else None,
            "share": round(used / cap, 2) if known and cap else None,
            "alert_at": max(1, int(cap * ALERT_SHARE))}


def status(count=_count) -> dict:
    """The upload bucket's fields at the top level ({"known", "used", "limit",
    "google_cap", "remaining", "share", "alert_at"}) plus "search": the same
    for search.list, "day" and "source". `known` False = the tally could not
    be read: used/remaining are None, never a guessed zero."""
    return {**_bucket(UPLOAD, count), "search": _bucket(SEARCH, count),
            "day": pacific_day(), "resets": "midnight Pacific Time",
            "source": "our own count of calls (api_call_counters), not a figure from Google"}


def require(needed: int = 1, count=_count, bucket: str = UPLOAD) -> dict:
    """The bucket's status when `needed` calls still fit today; QuotaExceeded
    when they do not. An unreadable tally is NOT a refusal (the counter itself
    fails open) — it is logged, and the call's own brake still stands."""
    now = _bucket(bucket, count)
    if now["known"] and now["remaining"] < int(needed):
        raise QuotaExceeded(now, int(needed), bucket)
    if not now["known"]:
        print(f"[{SERVICE_NAME}] {bucket} tally unreadable — {needed} call(s) allowed without a quota check")
    return now


def after_call(bucket: str, count: int, alert=None):
    """Called with the day's count right after it was incremented. ONE alert
    a day per bucket: on the call whose count IS the threshold (the increment
    is atomic, so exactly one call sees that number). Never raises."""
    try:
        cap = limit(bucket)
        half = max(1, int(cap * ALERT_SHARE))
        if int(count or 0) != half:
            return
        if alert is None:
            from core.agent_base import agent_alert
            alert = lambda text: agent_alert(SERVICE_NAME, [text])      # noqa: E731
        alert(f"YouTube {LABEL[bucket]} on {pacific_day()} (Pacific): half of the daily limit is used ({half} of "
              f"{cap}; the limit is shared by all clients). Remaining calls and the live count: /admin/connections/. "
              f"Raising the limit needs the YouTube API compliance audit.")
    except Exception as e:
        print(f"[{SERVICE_NAME}] alert not sent ({type(e).__name__})")


def after_upload(count: int, alert=None):
    after_call(UPLOAD, count, alert)
