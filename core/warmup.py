"""Page-driven warm-up (2026-11-10, docs/WARMUP.md).

The client dashboard (and the admin pages) ping GET /api/warmup/ on load,
every `warmup_interval_seconds` (admin setting, default 240) while the tab is
visible, and when the tab becomes visible again — so the instance is awake
before the client types, and a page left open across a deploy reloads itself
(when the chat is idle) onto the new version.

Owner's rules: NO min instances, NO --no-cpu-throttling, NO Cloud Run
setting change, NO scheduler job. Only open pages ping.

The ping itself touches nothing: no Supabase, no Drive, no model — it
answers {"ok": true, "version": <Cloud Run revision>} from memory. The
interval is a SEPARATE call (/api/warmup/config, the 60 s-cached setting),
made once per page load. Both are rate-limited per IP in memory.
"""
import os
import threading
import time

INTERVAL_DEFAULT = 240
INTERVAL_MIN, INTERVAL_MAX = 60, 3600
RATE_WINDOW_S = 60
RATE_MAX = 30            # per IP per minute — a page needs ~1; many tabs / a NAT office fit
_TRACK_MAX = 5000        # remembered IPs before the table is cleared (memory stays bounded)

_hits = {}
_lock = threading.Lock()


def version() -> str:
    """The running build: Cloud Run sets K_REVISION per deploy (the same on
    every instance of one revision). Locally: "dev"."""
    return os.environ.get("K_REVISION") or "dev"


def allow(ip: str, now: float = None) -> bool:
    """Sliding one-minute window per IP, in memory, no I/O."""
    now = time.monotonic() if now is None else now
    key = ip or "-"
    with _lock:
        if len(_hits) > _TRACK_MAX:
            _hits.clear()
        recent = [t for t in _hits.get(key, ()) if now - t < RATE_WINDOW_S]
        if len(recent) >= RATE_MAX:
            _hits[key] = recent
            return False
        recent.append(now)
        _hits[key] = recent
        return True


def ping() -> dict:
    return {"ok": True, "version": version()}


def interval_seconds() -> int:
    """The admin setting, clamped; any failure → the default."""
    try:
        from core import admin_service
        value = int(float(admin_service.get_setting("warmup_interval_seconds", INTERVAL_DEFAULT)))
    except Exception:
        value = INTERVAL_DEFAULT
    return max(INTERVAL_MIN, min(value, INTERVAL_MAX))
