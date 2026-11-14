"""Read the chat's context while the client is still typing (2026-11-15,
docs/CHAT-SPEED.md §Prewarm).

A full chat turn waits for its slowest context read (the account snapshot,
the Google / Meta / website reads — live vendor calls with their own caches).
Those reads do not depend on the message. So the dashboard tells us the
moment the client focuses a chat box or starts typing, and we do the SAME
reads then; by the time the message is sent the caches are warm and the turn
goes almost straight to the model.

- The same readers the turn uses (support_agent._manager_platform_reads,
  client_state.snapshot). No model call, no write, nothing new is fetched
  that a turn would not have fetched a few seconds later.
- At most one run per client per MIN_INTERVAL_SECONDS on this instance, and
  never two at once for one client.
- Answers stay exactly as deep and as fresh: this only moves WHEN the reads
  happen. The caches' own TTLs decide freshness, as before.
- Kill switch: admin setting `chat_prewarm_enabled` (default on).
- Honest limit: the caches are per Cloud Run instance. With one instance
  serving (the usual case) the turn finds them warm; if the message lands on
  another instance it reads as it always did — never slower than before.
"""
import threading
import time

SERVICE_NAME = "chat_prewarm"
SETTING = "chat_prewarm_enabled"
MIN_INTERVAL_SECONDS = 90

_guard = threading.Lock()
_last = {}        # client_id -> monotonic time of the last run that started
_running = set()


def enabled() -> bool:
    try:
        from core import admin_service
        value = admin_service.get_setting(SETTING, True)
    except Exception:
        return True
    return str(value).strip().lower() not in ("0", "false", "off", "no")


def _reads(client_id: int) -> dict:
    """The context reads of a full turn. Each one swallows its own failure —
    a prewarm never raises and never alerts beyond what the reader itself does."""
    from core import parallel

    def snapshot():
        from core import client_state
        return client_state.snapshot(client_id)

    def platforms():
        from agents import support_agent
        return support_agent._manager_platform_reads(client_id)

    def knowledge():
        from agents import support_agent
        return support_agent._client_knowledge(client_id, "general")
    return parallel.gather({"snapshot": snapshot, "platforms": platforms, "knowledge": knowledge})


def run(client_id: int) -> dict:
    """{"ran": bool, "why": str, "seconds": float}. Never raises."""
    client_id = int(client_id)
    if not enabled():
        return {"ran": False, "why": "off"}
    now = time.monotonic()
    with _guard:
        if client_id in _running:
            return {"ran": False, "why": "already_running"}
        if now - _last.get(client_id, -MIN_INTERVAL_SECONDS - 1) < MIN_INTERVAL_SECONDS:
            return {"ran": False, "why": "recent"}
        _last[client_id] = now
        _running.add(client_id)
        if len(_last) > 5000:
            for old in sorted(_last, key=_last.get)[:2500]:
                _last.pop(old, None)
    started = time.perf_counter()
    try:
        got = _reads(client_id)
        failed = sorted(name for name, (ok, _value) in got.items() if not ok)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: prewarm failed ({type(e).__name__})")
        failed = ["all"]
    finally:
        with _guard:
            _running.discard(client_id)
    seconds = round(time.perf_counter() - started, 2)
    print(f"[{SERVICE_NAME}] client {client_id}: context read in {seconds}s"
          + (f" (not read: {', '.join(failed)})" if failed else ""))
    return {"ran": True, "why": "", "seconds": seconds}
