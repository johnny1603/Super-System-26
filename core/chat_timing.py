"""Per-message timing for the client chat (2026-11-09) — MEASUREMENT ONLY.

One "turn" = one answered batch of client messages (core/chat_turns). Stages:
  queue      waiting for the conversation's lock + the collection window
  guard      chat_guard + storing the inbound message + the code-only answers
  context    everything inside the agent that is not a model call (DB reads,
             client_state snapshot, store/platform reads, prompt building)
  model      every Claude call made during the turn (router, main turn, web
             search, retries) — each one listed with model, tokens, ms
  post       vendor scrub + storing the reply
  total      wall clock, request in → reply out

Plumbing: a ContextVar holds the open turn, so claude_json._complete /
claude_web_search_call add their calls without any signature change, and a
call made outside a chat turn records nothing. `stage(name)` is a context
manager for code-side stages. `finish()` builds the row; the route stores it
via BackgroundTasks (never on the reply's path). Table `chat_turn_timings`
(migrations/2026-11-09-chat-timing-frustration.sql); without it the row is
printed as one `[chat_timing]` log line (ids + numbers only, never message
text) so Cloud Logging still has the data.
"""
import contextvars
import json
import time
from contextlib import contextmanager

TABLE = "chat_turn_timings"
_turn = contextvars.ContextVar("chat_turn_timing", default=None)


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def start(client_id: int, persona: str, channel: str = ""):
    """Open a turn for this request (contextvar token returned for finish)."""
    state = {"client_id": client_id, "persona": persona or "general", "channel": channel,
             "t0": time.perf_counter(), "stages": {}, "calls": [], "flags": {}}
    return _turn.set(state)


def current():
    return _turn.get()


def add_stage(name: str, seconds: float) -> None:
    state = _turn.get()
    if state is not None:
        state["stages"][name] = state["stages"].get(name, 0) + _ms(seconds)


@contextmanager
def stage(name: str):
    t = time.perf_counter()
    try:
        yield
    finally:
        add_stage(name, time.perf_counter() - t)


def note_model_call(model: str, seconds: float, input_tokens=0, output_tokens=0,
                    cache_read=0, cache_write=0, category: str = "", kind: str = "json") -> None:
    """Called by core/claude_json for every completed (or failed) API call."""
    state = _turn.get()
    if state is None:
        return
    state["calls"].append({"model": model, "ms": _ms(seconds), "in": int(input_tokens or 0),
                           "out": int(output_tokens or 0), "cache_read": int(cache_read or 0),
                           "cache_write": int(cache_write or 0), "category": category or "", "kind": kind})


def flag(name: str, value) -> None:
    state = _turn.get()
    if state is not None:
        state["flags"][name] = value


def finish(token, **extra) -> dict:
    """Close the turn and return the row to store (never raises)."""
    try:
        state = _turn.get() or {}
        total = _ms(time.perf_counter() - state.get("t0", time.perf_counter()))
        stages = dict(state.get("stages") or {})
        calls = list(state.get("calls") or [])
        model_ms = sum(c["ms"] for c in calls)
        agent_ms = stages.pop("agent", None)
        if agent_ms is not None:
            # inside the agent, whatever is not a model call is context work
            stages["context"] = stages.get("context", 0) + max(agent_ms - model_ms, 0)
        stages["model"] = model_ms
        named = sum(stages.values())
        if total > named:
            stages["other"] = total - named
        dominant = max(stages, key=stages.get) if stages else ""
        row = {"client_id": state.get("client_id"), "persona": state.get("persona"),
               "channel": state.get("channel"), "total_ms": total,
               "queue_ms": stages.get("queue", 0), "context_ms": stages.get("context", 0),
               "model_ms": model_ms, "post_ms": stages.get("post", 0),
               "model_calls": len(calls), "dominant": dominant,
               "stages": {"stages": stages, "calls": calls[:12], "flags": state.get("flags") or {}}}
        flags = state.get("flags") or {}
        row["merged_messages"] = int(flags.get("merged_messages") or 1)
        row["restarted"] = bool(flags.get("restarted"))
        row["outcome"] = str(flags.get("outcome") or "answered")
        row.update({k: v for k, v in extra.items() if k in ("merged_messages", "restarted", "outcome")})
        return row
    except Exception as e:
        return {"error": type(e).__name__}
    finally:
        try:
            _turn.reset(token)
        except Exception:
            pass


def store(row: dict) -> None:
    """BackgroundTasks target. Table first; a log line when it's missing."""
    if not row or row.get("error") or not row.get("client_id"):
        return
    try:
        from agents.client_agent import _db
        _db().table(TABLE).insert(row).execute()
    except Exception as e:
        slim = {k: row.get(k) for k in ("client_id", "persona", "total_ms", "queue_ms", "context_ms",
                                         "model_ms", "post_ms", "model_calls", "dominant")}
        flags = (row.get("stages") or {}).get("flags") or {}
        slim.update({k: flags.get(k) for k in ("route", "route_model", "message_chars") if k in flags})
        print(f"[chat_timing] {json.dumps(slim)} (not stored: {type(e).__name__})")


# ─── Grouped stats (route chosen, persona, short vs long) — 2026-11-11 ──────
# The route (core/chat_route: simple / read / work, the tier and the model)
# and the message length ride in the existing `stages` jsonb (flags), so no
# column and no migration. Rows from before 2026-11-11 have no route: "—".

SHORT_CHARS = 40          # "thanks", "yes", a quick question
SLOW_MS = 60000


def _median(values: list) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else int((ordered[mid - 1] + ordered[mid]) / 2)


def row_flags(row: dict) -> dict:
    return ((row.get("stages") or {}).get("flags") or {}) if isinstance(row.get("stages"), dict) else {}


def group_stats(rows: list) -> dict:
    """avg / median / p90 of total_ms, the stage averages, model calls per
    message, the models that ran, and the dominant stage — for one group."""
    totals = [int(r.get("total_ms") or 0) for r in rows]
    n = len(rows)
    if not n:
        return {"turns": 0}
    models, doms = {}, {}
    for r in rows:
        for c in ((r.get("stages") or {}).get("calls") or []):
            models[c.get("model") or "?"] = models.get(c.get("model") or "?", 0) + 1
        doms[r.get("dominant") or ""] = doms.get(r.get("dominant") or "", 0) + 1
    return {"turns": n, "avg_ms": int(sum(totals) / n), "median_ms": _median(totals), "p90_ms": _p90(totals),
            **{f"avg_{k}_ms": int(sum(int(r.get(f"{k}_ms") or 0) for r in rows) / n)
               for k in ("queue", "context", "model", "post")},
            "avg_model_calls": round(sum(int(r.get("model_calls") or 0) for r in rows) / n, 2),
            "models": models, "dominant": max(doms, key=doms.get)}


def grouped(rows: list) -> dict:
    """By route chosen, by persona, short vs long, and how many SHORT
    messages took more than a minute."""
    def by(key_fn):
        groups = {}
        for r in rows:
            groups.setdefault(key_fn(r), []).append(r)
        return {k: group_stats(v) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}

    def length(r):
        chars = row_flags(r).get("message_chars")
        if chars is None:
            return "unknown"
        return "short" if int(chars) < SHORT_CHARS else "long"
    short = [r for r in rows if length(r) == "short"]
    slow_short = sum(1 for r in short if int(r.get("total_ms") or 0) > SLOW_MS)
    return {"by_route": by(lambda r: f"{row_flags(r).get('route') or '—'}:{row_flags(r).get('route_model') or '—'}"),
            "by_persona": by(lambda r: r.get("persona") or "general"),
            "by_length": by(length),
            "short_over_60s_pct": round(100.0 * slow_short / len(short), 1) if short else None,
            "short_turns": len(short)}


# ─── Admin read ─────────────────────────────────────────────────────────────

def _p90(values: list) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]


def summary(days: int = 7, limit: int = 2000) -> dict:
    """Average / p90 per client, the slowest recent turns, and which stage
    dominates — for /admin/models/. Read-only."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        from agents.client_agent import _db
        rows = (_db().table(TABLE).select("*").gte("created_at", since)
                .order("created_at", desc=True).limit(limit).execute().data) or []
        # a `covered` request was answered by another turn's row — counting it
        # too would double that turn
        rows = [r for r in rows if r.get("outcome") != "covered"]
    except Exception as e:
        return {"available": False, "reason": type(e).__name__, "per_client": [], "slowest": [],
                "dominant": {}, "overall": {}}
    names = {}
    try:
        from agents.client_agent import _db
        ids = sorted({r["client_id"] for r in rows if r.get("client_id")})
        if ids:
            for c in (_db().table("clients").select("id,name,business_name").in_("id", ids).execute().data or []):
                names[c["id"]] = c.get("business_name") or c.get("name") or ""
    except Exception:
        pass
    per = {}
    for r in rows:
        per.setdefault(r.get("client_id"), []).append(r)
    per_client = []
    for cid, items in per.items():
        totals = [int(i.get("total_ms") or 0) for i in items]
        doms = {}
        for i in items:
            doms[i.get("dominant") or ""] = doms.get(i.get("dominant") or "", 0) + 1
        per_client.append({"client_id": cid, "name": names.get(cid, ""), "turns": len(items),
                           "avg_ms": int(sum(totals) / len(totals)), "p90_ms": _p90(totals),
                           "avg_model_ms": int(sum(int(i.get("model_ms") or 0) for i in items) / len(items)),
                           "avg_context_ms": int(sum(int(i.get("context_ms") or 0) for i in items) / len(items)),
                           "avg_queue_ms": int(sum(int(i.get("queue_ms") or 0) for i in items) / len(items)),
                           "dominant": max(doms, key=doms.get)})
    per_client.sort(key=lambda x: -x["p90_ms"])
    slowest = sorted(rows, key=lambda r: -int(r.get("total_ms") or 0))[:20]
    dominant = {}
    for r in rows:
        dominant[r.get("dominant") or ""] = dominant.get(r.get("dominant") or "", 0) + 1
    totals = [int(r.get("total_ms") or 0) for r in rows]
    stage_avg = {}
    for r in rows:
        for k, v in ((r.get("stages") or {}).get("stages") or {}).items():
            stage_avg[k] = stage_avg.get(k, 0) + int(v or 0)
    overall = {"turns": len(rows), "avg_ms": int(sum(totals) / len(totals)) if totals else 0,
               "p90_ms": _p90(totals),
               "stage_avg_ms": {k: int(v / len(rows)) for k, v in stage_avg.items()} if rows else {}}
    return {"available": True, "days": days, "overall": overall, "per_client": per_client,
            "groups": grouped(rows),
            "slowest": [{"created_at": r.get("created_at"), "client_id": r.get("client_id"),
                         "name": names.get(r.get("client_id"), ""), "persona": r.get("persona"),
                         "route": row_flags(r).get("route") or "", "route_model": row_flags(r).get("route_model") or "",
                         "message_chars": row_flags(r).get("message_chars"),
                         "total_ms": r.get("total_ms"), "dominant": r.get("dominant"),
                         "model_calls": r.get("model_calls"), "merged_messages": r.get("merged_messages"),
                         "stages": (r.get("stages") or {}).get("stages") or {},
                         "calls": (r.get("stages") or {}).get("calls") or []} for r in slowest],
            "dominant": dominant}
