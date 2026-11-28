"""The Google Ads daily operations ceiling, visible BEFORE it is hit (2026-11-28).

The same shape as core/youtube_quota: one Google Cloud project = one ceiling,
shared by EVERY client.

The figures, as the owner stated them on 2026-11-28:

- Since 9 September 2026 the access level belongs to the Google Cloud PROJECT
  (super-system-500410), not to a developer token. The developer-token header
  is optional: core/google_ads_service sends it when one is configured and
  never requires it.
- The project is at EXPLORER access: 2,880 operations a day, against
  production advertiser accounts. Basic (15,000 a day) is an upgrade that
  waits for brand verification. It is NOT a prerequisite: nothing in the code
  may refuse, or say "not connected", because Basic is missing.

THE ONE CONSTANT: `OPERATIONS_PER_DAY`. The day Basic is approved it becomes
15_000 here and nowhere else.

What counts as an operation: one per get / search request, and one per
operation INSIDE a mutate request (a campaign created in one atomic mutate of
25 operations is 25, not 1). core/google_ads_service counts exactly that.

What is counted is OUR OWN tally (core/api_call_counters, platform
"google_ads", one row a day) — Google exposes no "operations left" endpoint.
Operations made outside this system are not seen. THE DAY IS THE SERVER'S
DATE (UTC on Cloud Run), as this counter always was; Google's own reset time
is not read anywhere, so near midnight the two can disagree.

- `status()`        used / limit / remaining today.
- `require(n)`      QuotaExceeded when fewer than n operations are left —
                    asked BEFORE anything is produced or sent.
- `spend(n)`        counts n operations, alerts the owner at half, and raises
                    QuotaExceeded instead of letting the call go out.

The counter FAILS OPEN (api_call_counters returns 0 when the database is
down): on such a day nothing is counted, the alert does not fire and the
admin page says the tally is unknown — never a guessed zero.
"""
import time

SERVICE_NAME = "google_ads_quota"
PLATFORM = "google_ads"                 # the api_call_counters row
ACCESS_LEVEL = "explorer"
OPERATIONS_PER_DAY = 2880               # Explorer. 15_000 the day Basic is approved — change it HERE only.
ALERT_SHARE = 0.5


class QuotaExceeded(RuntimeError):
    """Not enough operations left today: .status says how many. A RuntimeError,
    so every existing `except` around a Google Ads call keeps working."""

    def __init__(self, status: dict, needed: int):
        self.status, self.needed = status, needed
        super().__init__(f"Google Ads operations today: {status.get('used')} of {status.get('limit')} used, "
                         f"{status.get('remaining')} left, {needed} needed (the daily limit is shared by all clients)")


def day() -> str:
    """The tally's day: the server's date, the same one api_call_counters writes."""
    return time.strftime("%Y-%m-%d")


def _count():
    """Today's count, or None when it cannot be read."""
    try:
        from core import api_call_counters
        rows = (api_call_counters._db().table("api_call_counters").select("count")
                .eq("platform", PLATFORM).eq("call_date", day()).limit(1).execute().data or [])
        return int(rows[0].get("count") or 0) if rows else 0
    except Exception as e:
        print(f"[{SERVICE_NAME}] counter unreadable ({type(e).__name__})")
        return None


def alert_at() -> int:
    return max(1, int(OPERATIONS_PER_DAY * ALERT_SHARE))


def status(count=_count) -> dict:
    """{"known", "used", "limit", "remaining", "share", "alert_at", "day",
    "access_level", "source"}. `known` False = the tally could not be read:
    used/remaining are None, never a guessed zero."""
    used = count()
    known = used is not None
    return {"known": known, "used": used, "limit": OPERATIONS_PER_DAY,
            "remaining": max(0, OPERATIONS_PER_DAY - used) if known else None,
            "share": round(used / OPERATIONS_PER_DAY, 2) if known else None,
            "alert_at": alert_at(), "day": day(), "access_level": ACCESS_LEVEL,
            "source": "our own count of operations (api_call_counters), not a figure from Google"}


def require(needed: int = 1, count=_count) -> dict:
    """The status when `needed` operations still fit today; QuotaExceeded when
    they do not. An unreadable tally is NOT a refusal (the counter itself
    fails open) — it is logged, and `spend` still stands behind it."""
    now = status(count)
    if now["known"] and now["remaining"] < int(needed):
        raise QuotaExceeded(now, int(needed))
    if not now["known"]:
        print(f"[{SERVICE_NAME}] tally unreadable — {needed} operation(s) allowed without a quota check")
    return now


def _increment() -> int:
    from core.api_call_counters import increment_call_counter
    return int(increment_call_counter(PLATFORM, window_days=1, day=day()) or 0)


def after_call(count: int, alert=None):
    """Called with the day's count right after it was incremented. ONE alert
    a day: on the operation whose count IS the threshold (the increment is
    atomic, so exactly one operation sees that number). Never raises."""
    try:
        half = alert_at()
        if int(count or 0) != half:
            return
        if alert is None:
            from core.agent_base import agent_alert
            alert = lambda text: agent_alert(SERVICE_NAME, [text])      # noqa: E731
        alert(f"Google Ads operations on {day()}: half of the daily limit is used ({half} of {OPERATIONS_PER_DAY}; "
              f"the limit is shared by all clients, access level: {ACCESS_LEVEL}). Remaining operations and the live "
              f"count: /admin/connections/. The limit rises to 15,000 when Basic access is approved "
              f"(OPERATIONS_PER_DAY in core/google_ads_quota.py).")
    except Exception as e:
        print(f"[{SERVICE_NAME}] alert not sent ({type(e).__name__})")


def spend(operations: int = 1, count=_count, increment=None, alert=None) -> int:
    """Count `operations` on today's tally and return the day's total. Raises
    QuotaExceeded INSTEAD of letting the call go out:
    - more than one operation (a mutate): asked first, so a request that does
      not fit is refused whole and nothing of it is counted;
    - always: when the tally passes the limit.
    A counter that could not be written returns 0 and the call goes out."""
    operations = max(1, int(operations or 1))
    if operations > 1:
        require(operations, count)
    increment = increment or _increment
    total = 0
    for _ in range(operations):
        total = int(increment() or 0)
        after_call(total, alert)
    if total > OPERATIONS_PER_DAY:
        used = total - operations
        raise QuotaExceeded({"known": True, "used": used, "limit": OPERATIONS_PER_DAY,
                             "remaining": max(0, OPERATIONS_PER_DAY - used)}, operations)
    return total
