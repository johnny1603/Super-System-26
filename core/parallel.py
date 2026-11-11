"""Run independent reads at the same time (2026-11-11, docs/CHAT-SPEED.md).

The client chat used to load its context ONE READ AFTER ANOTHER: the ads
platforms, the site, the store, then ~20 client_state sections, each a
network round trip (several to outside providers with 20-60 s timeouts) —
so a cache miss cost the SUM of them. `gather` runs them together and costs
the slowest one.

- Every task runs in its own copy of the caller's contextvars (the chat
  timing record, a Drive scope), exactly like model_routing's router.
- Never raises: each result is (True, value) or (False, exception).
- No deadline: a slow provider is still waited for (cutting it short would
  change what the chat answers from — a decision left to the owner).
- `single_flight`: two threads asking for the SAME client's data at once
  share one call instead of both missing the getter's own cache (running
  the manager's reads and client_state together would otherwise hit Google
  Ads / Meta / WordPress twice for one message).
"""
import concurrent.futures
import contextvars
import functools
import threading

MAX_WORKERS = 32
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="ctx")
# A second pool for work started FROM a task of the first (client_state's
# sections inside the chat's account read): nested waits on one pool can
# starve it.
_inner_pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="ctx-in")
_local = threading.local()


def _run(fn):
    _local.inside = True
    try:
        return fn()
    finally:
        _local.inside = False


def submit(fn):
    """A future for fn() in a copy of the current context."""
    pool = _inner_pool if getattr(_local, "inside", False) else _pool
    return pool.submit(contextvars.copy_context().run, _run, fn)


def gather(tasks: dict) -> dict:
    """{name: callable} → {name: (ok, value_or_exception)}, all run at once."""
    futures = {name: submit(fn) for name, fn in tasks.items()}
    out = {}
    for name, future in futures.items():
        try:
            out[name] = (True, future.result())
        except Exception as e:      # noqa: BLE001 — the caller decides per read
            out[name] = (False, e)
    return out


# ─── One call per (function, client) at a time ──────────────────────────────

_flights = {}
_flights_lock = threading.Lock()


def single_flight(fn):
    """For a getter whose first argument is client_id: concurrent calls for
    the same client wait for the one already running and get its result
    (or its exception). Sequential calls are untouched — the getter's own
    cache answers those."""
    @functools.wraps(fn)
    def wrapper(client_id, *args, **kwargs):
        if args or kwargs:
            return fn(client_id, *args, **kwargs)     # only the plain per-client read is shared
        key = (fn.__module__, fn.__qualname__, client_id)
        with _flights_lock:
            flight = _flights.get(key)
            leader = flight is None
            if leader:
                flight = _flights[key] = {"done": threading.Event(), "value": None, "error": None}
        if not leader:
            flight["done"].wait()
            if flight["error"] is not None:
                raise flight["error"]
            return flight["value"]
        try:
            flight["value"] = fn(client_id)
            return flight["value"]
        except Exception as e:
            flight["error"] = e
            raise
        finally:
            with _flights_lock:
                _flights.pop(key, None)
            flight["done"].set()
    return wrapper
