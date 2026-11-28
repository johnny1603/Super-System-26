"""How long the intake really takes, and an alert when it takes too long (2026-11-28).

The landing page states a time for the first characterisation. Nothing
measured or enforced it. This module does both, so the number that is
eventually written comes from production and has something behind it.

THREE ALERTS to the owner (each once per occurrence):
  1. a proposal build that passed `PROPOSAL_ALERT_SECONDS`, or never finished;
  2. a brand brief draft that does not exist `DRAFT_DUE_HOURS` after it was queued;
  3. a draft that has waited for the manager's approval more than `APPROVAL_DUE_HOURS`.

WHAT IS MEASURED:
  - every proposal build: start, seconds, finished or failed — kept here
    (app_settings `intake_sla`, the newest `KEEP`), written around the ONE call
    in `/api/onboarding`;
  - brief drafts and approvals: NOT stored again — read from what
    core/brand_brief already records (`draft_job.queued_at/finished_at`, the
    drafted and approved versions' times).

`sweep()` rides the every-minute scheduler request (at most once per
`SWEEP_EVERY_S` per instance). `report()` is the admin read: counts, median,
90th percentile, slowest — and it says how many samples each figure rests on.

The store is one row for all proposals, updated read-modify-write: two builds
finishing in the same second can lose one sample. Accepted at this volume; the
alert for a slow build does not depend on the store.
"""
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "intake_sla"
STORE_KEY = "intake_sla"
BRIEF_PREFIX = "brand_brief:"
PROPOSAL_ALERT_SECONDS = 240     # the chat page shows "almost there" at 200 s; past this the prospect is staring at a spinner
PROPOSAL_LOST_SECONDS = 420      # started and never reported back: the request died (the platform cuts a request near 300 s)
DRAFT_DUE_HOURS = 1
APPROVAL_DUE_HOURS = 24
KEEP = 300
SWEEP_EVERY_S = 600
_last_sweep = 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db():
    from agents.client_agent import _db as db
    return db()


# ─── Doors (patched in tests) ────────────────────────────────────────────────

def _read_store() -> dict:
    rows = _db().table("app_settings").select("value").eq("key", STORE_KEY).limit(1).execute().data or []
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _write_store(value: dict):
    _db().table("app_settings").upsert({"key": STORE_KEY, "value": value, "updated_at": _now().isoformat()},
                                       on_conflict="key").execute()


def _brief_rows() -> list:
    return _db().table("app_settings").select("key,value").like("key", f"{BRIEF_PREFIX}%").limit(2000).execute().data or []


def _alert(lines: list):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, lines)


def _when(text):
    try:
        out = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        return out if out.tzinfo else out.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _update(change) -> None:
    """Read, change, write — never raises (a measurement never breaks a sale)."""
    try:
        value = _read_store()
        change(value)
        value["proposals"] = (value.get("proposals") or [])[-KEEP:]
        alerted = value.get("alerted") or {}
        value["alerted"] = dict(sorted(alerted.items(), key=lambda kv: kv[1])[-500:])
        _write_store(value)
    except Exception as e:
        print(f"[{SERVICE_NAME}] store not updated ({type(e).__name__})")


# ─── 1. The proposal build ───────────────────────────────────────────────────

@contextmanager
def proposal(lead_id: str = ""):
    """Wraps the proposal build: records the start, then the seconds and the
    outcome; alerts when it passed the threshold. The body's own exception is
    re-raised untouched."""
    run_id, started, t0 = uuid.uuid4().hex[:10], _now().isoformat(), time.monotonic()
    _update(lambda v: v.setdefault("proposals", []).append({"id": run_id, "started_at": started, "lead": str(lead_id or "")[:40]}))
    ok = False
    try:
        yield
        ok = True
    finally:
        seconds = round(time.monotonic() - t0, 1)

        def close(v):
            for row in v.get("proposals") or []:
                if row.get("id") == run_id:
                    row.update(seconds=seconds, ok=ok)
                    return
            v.setdefault("proposals", []).append({"id": run_id, "started_at": started, "seconds": seconds, "ok": ok})
        _update(close)
        if seconds > PROPOSAL_ALERT_SECONDS:
            try:
                _alert([f"a sales-chat proposal took {int(seconds)} seconds to build (the alert line is {PROPOSAL_ALERT_SECONDS}) "
                        f"and {'finished' if ok else 'FAILED'}. The prospect waited on the progress screen the whole time. "
                        f"Lead {str(lead_id or 'unknown')[:40]}. Times of all builds: /admin/connections/."])
            except Exception as e:
                print(f"[{SERVICE_NAME}] slow proposal not alerted ({type(e).__name__})")


# ─── 2 + 3. The brand brief (read from its own record) ───────────────────────

def _client_of(key: str):
    try:
        return int(str(key)[len(BRIEF_PREFIX):])
    except ValueError:
        return None


def brief_times(rows: list = None, now: datetime = None) -> list:
    """Per client with a brief row: when the draft was queued, how long it took
    to exist, when it was approved and how long that took — each a measurement
    or None. `waiting_hours` = a draft that is not approved yet."""
    now = now or _now()
    out = []
    for row in (_brief_rows() if rows is None else rows):
        client_id, value = _client_of(row.get("key")), row.get("value") if isinstance(row.get("value"), dict) else {}
        if client_id is None:
            continue
        job, versions = value.get("draft_job") or {}, value.get("versions") or []
        queued, finished = _when(job.get("queued_at")), _when(job.get("finished_at"))
        drafted = next((v for v in versions if v.get("kind") == "drafted"), None)
        approved = next((v for v in versions if v.get("status") == "approved"), None)
        first = _when((versions[0] if versions else {}).get("at"))
        item = {"client_id": client_id, "queued_at": job.get("queued_at"), "job_state": job.get("state"), "attempts": job.get("attempts"),
                "draft_seconds": round((finished - queued).total_seconds(), 1) if queued and finished and finished >= queued else None,
                "has_brief": bool(versions), "approved": bool(approved), "approval_hours": None, "waiting_hours": None,
                "draft_overdue_hours": None}
        if approved and first and _when(approved.get("at")):
            item["approval_hours"] = round((_when(approved["at"]) - first).total_seconds() / 3600, 1)
        elif versions and first and (versions[-1].get("status") or "draft") != "approved":
            item["waiting_hours"] = round((now - first).total_seconds() / 3600, 1)
        if not versions and queued and job.get("state") in ("queued", "running", "failed"):
            item["draft_overdue_hours"] = round((now - queued).total_seconds() / 3600, 1)
        item["drafted_by_intake"] = bool(drafted)
        out.append(item)
    return out


# ─── The sweep (alerts) ──────────────────────────────────────────────────────

def sweep(now: datetime = None, force: bool = False) -> dict:
    """Raise what is due, each once. Never raises."""
    global _last_sweep
    if not force and time.monotonic() - _last_sweep < SWEEP_EVERY_S:
        return {"skipped": "recent"}
    _last_sweep = time.monotonic()
    now = now or _now()
    try:
        store, briefs = _read_store(), brief_times(now=now)
    except Exception as e:
        return {"error": type(e).__name__}
    alerted, lines, lost = dict(store.get("alerted") or {}), [], []
    for row in store.get("proposals") or []:
        started = _when(row.get("started_at"))
        if "seconds" not in row and started and (now - started).total_seconds() > PROPOSAL_LOST_SECONDS:
            lost.append(row["id"])
            lines.append((f"proposal:{row['id']}",
                          f"a sales-chat proposal that started at {row['started_at'][:16]} UTC never reported back "
                          f"(more than {PROPOSAL_LOST_SECONDS} seconds): the request most likely died and the prospect got no "
                          f"proposal. Lead {row.get('lead') or 'unknown'}."))
    for item in briefs:
        cid = item["client_id"]
        if (item["draft_overdue_hours"] or 0) >= DRAFT_DUE_HOURS:
            lines.append((f"draft:{cid}:{item['queued_at']}",
                          f"client {cid}: the brand brief draft was queued more than {DRAFT_DUE_HOURS} hour(s) ago and does not "
                          f"exist (job state {item['job_state']}, attempts {item['attempts'] or 0}). The agents are working "
                          f"without a brief for this client. Draft it by hand from the brief page in the Control Room."))
        if (item["waiting_hours"] or 0) >= APPROVAL_DUE_HOURS:
            lines.append((f"approval:{cid}",
                          f"client {cid}: the brand brief has been a DRAFT waiting for your approval for more than "
                          f"{APPROVAL_DUE_HOURS} hours. Until it is approved the agents ask instead of deciding (governance §1)."))
    fresh = [(key, text) for key, text in lines if key not in alerted]
    sent = []
    for key, text in fresh:
        try:
            _alert([text])
            sent.append(key)
        except Exception as e:
            print(f"[{SERVICE_NAME}] {key} not alerted ({type(e).__name__})")
    # an approval alert is raised again for a NEW wait: forget it once the brief is approved
    settled = {f"approval:{i['client_id']}" for i in briefs if i["approved"] or not i["has_brief"]}
    if sent or lost or settled & set(alerted):
        def change(v):
            marks = v.setdefault("alerted", {})
            for key in sent:
                marks[key] = now.isoformat()
            for key in settled:
                marks.pop(key, None)
            for row in v.get("proposals") or []:
                if row.get("id") in lost and "seconds" not in row:
                    row.update(seconds=None, ok=False, lost=True)
        _update(change)
    return {"alerted": sent, "due": len(lines)}


# ─── The report (reads only) ─────────────────────────────────────────────────

def _spread(values: list) -> dict:
    """{"n", "median", "p90", "max"} of real samples; only "n" when there are none."""
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return {"n": 0}
    pick = lambda q: values[min(len(values) - 1, int(round(q * (len(values) - 1))))]     # noqa: E731
    import statistics
    return {"n": len(values), "median": round(statistics.median(values), 1), "p90": pick(0.9), "max": values[-1]}


def report(days: int = 90, now: datetime = None) -> dict:
    now = now or _now()
    cutoff = now - timedelta(days=days)
    rows = [r for r in (_read_store().get("proposals") or []) if (_when(r.get("started_at")) or now) >= cutoff]
    done = [r for r in rows if r.get("ok") and isinstance(r.get("seconds"), (int, float))]
    briefs = brief_times(now=now)
    return {"days": days, "measured_since": min((r["started_at"] for r in rows), default=None),
            "proposal": {**_spread([r["seconds"] for r in done]), "failed": sum(1 for r in rows if r.get("ok") is False),
                         "running": sum(1 for r in rows if "seconds" not in r),
                         "over_alert_line": sum(1 for r in done if r["seconds"] > PROPOSAL_ALERT_SECONDS),
                         "alert_line_seconds": PROPOSAL_ALERT_SECONDS,
                         "recent": [{"started_at": r["started_at"], "seconds": r.get("seconds"), "ok": r.get("ok")} for r in rows[-10:]]},
            "brief_draft": {**_spread([b["draft_seconds"] for b in briefs]), "due_hours": DRAFT_DUE_HOURS,
                            "overdue": [b["client_id"] for b in briefs if (b["draft_overdue_hours"] or 0) >= DRAFT_DUE_HOURS]},
            "brief_approval": {**_spread([b["approval_hours"] for b in briefs]), "due_hours": APPROVAL_DUE_HOURS,
                               "waiting": [{"client_id": b["client_id"], "hours": b["waiting_hours"]} for b in briefs
                                           if b["waiting_hours"] is not None]}}
