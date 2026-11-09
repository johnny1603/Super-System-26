"""One conversation, one turn at a time (2026-11-09).

Before: every send was its own request and its own model turn, in parallel —
three quick messages = three answers, interleaved, each blind to the others.

Now, per conversation (client × channel):
- SERIALIZED: a turn holds the conversation's lock while it runs. In-process
  (threading) always; across Cloud Run instances too through a lease row in
  `chat_turn_locks` (migrations/2026-11-09-chat-timing-frustration.sql) —
  without the table it degrades to in-process only and says so once in the
  log.
- COLLECTED: the turn that gets the lock waits until no new message has
  arrived for `chat_collect_window_seconds` (app_settings, default 3, 0 = off,
  capped at WINDOW_MAX_TOTAL in all), then answers every message that is
  still unanswered as ONE combined message.
- COVERED: each turn records the last inbound message id it answered
  (`covered_through`). A request whose own message was already answered by
  someone else's turn returns `covered` — the dashboard shows no second reply
  (its typing indicator just stops) because the answer is already in the
  window.
- MID-TURN MESSAGE: when the model is done, newer unanswered messages are
  checked. No card / background job came out of the turn → it restarts ONCE
  with everything combined. A card exists (or the restart was used) → this
  turn's reply is stored as is, and the newer messages get their own turn
  right after (their requests are waiting on the lock). Never two replies to
  the same message, never interleaved.

Messages are stored BEFORE any of this (api_server), so nothing is ever lost
to the coordination; every failure here degrades to "answer my own message".
"""
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

LOCK_TABLE = "chat_turn_locks"
WINDOW_DEFAULT = 3.0
WINDOW_CAP = 8.0
WINDOW_MAX_TOTAL = 10.0       # the collection wait never exceeds this, however chatty
LOCK_WAIT_MAX = 150.0         # a waiting request gives up and answers alone after this
LEASE_SECONDS = 180           # a crashed holder's lease expires
POLL_SECONDS = 0.4
MAX_BATCH = 8

_guard = threading.Lock()
_locks = {}            # (client_id, channel) -> threading.Lock
_arrivals = {}         # (client_id, channel) -> monotonic time of the latest message
_covered = {}          # (client_id, channel) -> last inbound id answered (in-process copy)
_db_lease_ok = [True]  # flips off once when the lease table is missing
_HOLDER = f"{socket.gethostname()}:{os.getpid()}"


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _key(client_id, channel):
    return (int(client_id), str(channel))


_window_cache = [0.0, WINDOW_DEFAULT]   # (read at, value) — one settings read a minute, not per message


def window_seconds() -> float:
    if time.monotonic() - _window_cache[0] < 60:
        return _window_cache[1]
    try:
        from core import admin_service
        rows = (admin_service._db().table("app_settings").select("value")
                .eq("key", "chat_collect_window_seconds").limit(1).execute().data) or []
        value = float(rows[0]["value"]) if rows else float(
            admin_service.DEFAULT_SETTINGS.get("chat_collect_window_seconds", WINDOW_DEFAULT))
    except Exception:
        value = WINDOW_DEFAULT
    value = max(0.0, min(value, WINDOW_CAP))
    _window_cache[0], _window_cache[1] = time.monotonic(), value
    return value


def note_arrival(client_id, channel) -> None:
    with _guard:
        _arrivals[_key(client_id, channel)] = time.monotonic()


# ─── Locking ────────────────────────────────────────────────────────────────

def _local_lock(key) -> threading.Lock:
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def _expired(value, now) -> bool:
    if not value:
        return True
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when < now
    except ValueError:
        return True


def _lease_take(key, token) -> bool:
    """True = we hold the cross-instance lease (or the table is missing)."""
    if not _db_lease_ok[0]:
        return True
    client_id, channel = key
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(seconds=LEASE_SECONDS)).isoformat()
    try:
        rows = (_db().table(LOCK_TABLE).select("*").eq("client_id", client_id).eq("channel", channel)
                .limit(1).execute().data) or []
        if not rows:
            try:
                _db().table(LOCK_TABLE).insert({"client_id": client_id, "channel": channel, "holder": token,
                                                "expires_at": expires}).execute()
                return True
            except Exception:
                return False          # someone inserted first: retry the loop
        row = rows[0]
        free = not row.get("holder") or _expired(row.get("expires_at"), now)
        if not free:
            return False
        # compare-and-set on the holder we saw, so two instances can't both win
        q = _db().table(LOCK_TABLE).update({"holder": token, "expires_at": expires}) \
            .eq("client_id", client_id).eq("channel", channel)
        q = q.eq("holder", row["holder"]) if row.get("holder") else q.is_("holder", "null")
        return bool(q.execute().data)
    except Exception as e:
        _db_lease_ok[0] = False
        print(f"[chat_turns] lease table unavailable ({type(e).__name__}) — in-process serialization only")
        return True


def _lease_release(key, token, covered_through=None) -> None:
    if not _db_lease_ok[0]:
        return
    client_id, channel = key
    update = {"holder": None, "expires_at": None}
    if covered_through:
        update["covered_through"] = int(covered_through)
    try:
        _db().table(LOCK_TABLE).update(update).eq("client_id", client_id).eq("channel", channel) \
            .eq("holder", token).execute()
    except Exception:
        pass


def _db_covered(key) -> int:
    if not _db_lease_ok[0]:
        return 0
    try:
        rows = (_db().table(LOCK_TABLE).select("covered_through").eq("client_id", key[0])
                .eq("channel", key[1]).limit(1).execute().data) or []
        return int((rows[0] if rows else {}).get("covered_through") or 0)
    except Exception:
        return 0


def covered_through(key) -> int:
    return max(_covered.get(key, 0), _db_covered(key))


# ─── Unanswered messages ────────────────────────────────────────────────────

def _inbound_after(client_id, channel, after_id: int) -> list:
    """Inbound rows on this channel with id > after_id, oldest first."""
    from agents.client_agent import get_communications
    rows = get_communications(client_id, limit=MAX_BATCH * 3, channel=channel)
    rows = [r for r in rows if r.get("direction") == "inbound" and int(r.get("id") or 0) > after_id]
    return sorted(rows, key=lambda r: int(r.get("id") or 0))[-MAX_BATCH:]


def combined_text(rows: list) -> str:
    texts = [str(r.get("content") or "").strip() for r in rows if str(r.get("content") or "").strip()]
    return "\n".join(texts)


class Turn:
    """with chat_turns.Turn(client_id, channel, own_row) as turn:
           if turn.covered: return covered-response
           message = turn.message ... run agent ...
           if turn.should_restart(result): message = turn.message; run again
           turn.done()"""

    def __init__(self, client_id, channel, own_row: dict, own_text: str):
        self.key = _key(client_id, channel)
        self.client_id, self.channel = self.key
        self.own_id = int((own_row or {}).get("id") or 0)
        self.own_text = own_text
        self.token = f"{_HOLDER}:{uuid.uuid4().hex[:8]}"
        self.lock = _local_lock(self.key)
        self.locked = self.leased = False
        self.covered = False
        self.batch = []
        self.restarted = False
        self.queue_s = 0.0
        self.message = own_text

    # -- enter: lock, covered check, collection window, batch --
    def __enter__(self):
        t0 = time.monotonic()
        try:
            self.locked = self.lock.acquire(timeout=LOCK_WAIT_MAX)
            if self.locked:
                deadline = t0 + LOCK_WAIT_MAX
                while not _lease_take(self.key, self.token):
                    if time.monotonic() > deadline:
                        break
                    time.sleep(POLL_SECONDS)
                else:
                    self.leased = True
            if self.own_id and covered_through(self.key) >= self.own_id:
                self.covered = True
                return self
            self._collect(t0)
        except Exception as e:
            print(f"[chat_turns] coordination degraded for client {self.client_id}: {type(e).__name__}")
            self.batch, self.message = [], self.own_text
        finally:
            self.queue_s = time.monotonic() - t0
        return self

    def _collect(self, t0):
        window = window_seconds()
        if window > 0:
            while True:
                last = _arrivals.get(self.key, 0)
                quiet = time.monotonic() - last
                if quiet >= window or time.monotonic() - t0 >= WINDOW_MAX_TOTAL:
                    break
                time.sleep(min(window - quiet, 0.5))
        self._read_batch()

    def _read_batch(self):
        if not self.own_id:
            self.batch, self.message = [], self.own_text
            return
        base = max(covered_through(self.key), self.own_id - 1)
        rows = _inbound_after(self.client_id, self.channel, base)
        if not any(int(r.get("id") or 0) == self.own_id for r in rows):
            rows = [{"id": self.own_id, "content": self.own_text}] + rows
        self.batch = rows
        self.message = combined_text(rows) or self.own_text

    @property
    def merged(self) -> int:
        return max(len(self.batch), 1)

    @property
    def last_id(self) -> int:
        return max([int(r.get("id") or 0) for r in self.batch] + [self.own_id])

    def newer(self) -> list:
        try:
            return _inbound_after(self.client_id, self.channel, self.last_id)
        except Exception:
            return []

    def should_restart(self, result: dict) -> bool:
        """New messages arrived while the model worked, and nothing was set in
        motion (no card, no background job) → ONE restart with everything."""
        if self.restarted or not self.own_id:
            return False
        if (result or {}).get("action") or (result or {}).get("poll"):
            return False
        extra = self.newer()
        if not extra:
            return False
        self.restarted = True
        self.batch = self.batch + extra
        self.message = combined_text(self.batch) or self.own_text
        return True

    def done(self):
        """Mark this turn's messages answered (call right after the reply is stored)."""
        with _guard:
            _covered[self.key] = max(_covered.get(self.key, 0), self.last_id)

    def __exit__(self, *exc):
        try:
            if self.leased:
                _lease_release(self.key, self.token, _covered.get(self.key) if not self.covered else None)
        finally:
            if self.locked:
                self.lock.release()
        return False
