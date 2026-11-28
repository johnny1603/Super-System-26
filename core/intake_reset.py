"""Run a client's characterisation again without losing what the account earned (2026-11-28).

Built for client 27: an early test run marked it characterised, and a goals
card approved before the sales metric existed counts enquiries for a business
whose stated result is sales.

`plan(client_id)` READS and lists exactly what a reset would delete and what it
keeps; it changes nothing. `run(client_id, confirm)` does it only with the typed
phrase, after copying every deleted row into `intake_reset:<id>`.

WHAT A RESET DELETES — this list is the whole of it (`DELETES`):
  - the `first_login_interview_completed` rows (what marks "characterised");
  - the `client_facts_captured` rows of the earlier run (they would stand in
    for the answers of the real one);
  - the agreed goals (`quarter_goals:<id>`: targets, month counter, baseline);
  - an OPEN goals card (cancelled, never deleted);
  - the linked sales lead ONLY when asked (`include_lead`) and only when the
    package does not rest on it.

WHAT IT NEVER TOUCHES (`KEEPS`, verified before and after): every connection and
stored key (`client_accounts`), the brand brief and its versions, connected
Drive folders, the media library, approved poses, character references, the
recorded target inputs, the chat history, the 90-day clock stamp. There is no
write to any of them in this file (tests/test_intake_reset.py scans for one).

The earlier interview's chat messages stay; the interview reads only what was
said AFTER the reset (`since`), otherwise an existing client's long chat history
would end the new interview on its first turn.

`audit()` — every client's agreed result target against what that client says a
result is (a sales business with an enquiries target, or the reverse).
"""
from datetime import datetime, timezone

SERVICE_NAME = "intake_reset"
PREFIX = "intake_reset:"
INTERVIEW_AGENT = "interview_agent"
DONE_ACTION = "first_login_interview_completed"
CAPTURE_ACTION = "client_facts_captured"
GOALS_PREFIX = "quarter_goals:"
GOALS_CARD = "agree_quarter_goals"
OPEN_CARD_STATUSES = ("proposed", "awaiting_staff")

DELETES = ("characterised_mark", "captured_facts", "agreed_goals", "open_goals_card", "sales_lead")
# app_settings rows that must be byte-for-byte the same after a reset (key prefix → what it is)
KEEPS = {"brand_brief:": "brand brief (every version)", "brand_kit:": "brand kit", "drive_folders:": "connected Drive folders",
         "drive_assets:": "connected-folder index", "media_library:": "media library", "pose_approvals:": "approved poses",
         "business_avatars:": "the business's own characters", "target_inputs:": "recorded target inputs",
         "conversion_measurement:": "measurement state", "google_page:": "Google page", "social_queue:": "Social queue",
         "content_plan:": "content plan", "media_orders:": "media orders", "client_settings:": "client settings",
         "gtm_token:": "Tag Manager grant health", "gads_token:": "Google Ads grant health", "tiktok_grant:": "TikTok grant",
         "creative_tools:": "creative tools health"}


class ResetError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail or code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db():
    from agents.client_agent import _db as db
    return db()


def phrase(client_id: int) -> str:
    return f"reset intake {int(client_id)}"


# ─── Doors (patched in tests) ────────────────────────────────────────────────

def _activity_rows(client_id: int, action: str) -> list:
    return (_db().table("client_activity").select("id,created_at,details").eq("client_id", int(client_id))
            .eq("agent_name", INTERVIEW_AGENT).eq("action_type", action).order("created_at").limit(200).execute().data or [])


def _setting(key: str):
    rows = _db().table("app_settings").select("value").eq("key", key).limit(1).execute().data or []
    return rows[0].get("value") if rows else None


def _settings_like(prefix: str) -> list:
    return _db().table("app_settings").select("key,value").like("key", f"{prefix}%").limit(2000).execute().data or []


def _open_cards(client_id: int) -> list:
    return (_db().table("agent_actions").select("id,status,created_at").eq("client_id", int(client_id))
            .eq("action", GOALS_CARD).in_("status", list(OPEN_CARD_STATUSES)).limit(50).execute().data or [])


def _lead(client_id: int) -> dict:
    from core import lead_tracking
    try:
        rows = (_db().table("leads").select("id,created_at,answers,proposal,status").eq("client_id", int(client_id))
                .order("created_at", desc=True).limit(20).execute().data or [])
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: leads unreadable ({type(e).__name__})")
        return {}
    return next((r for r in rows if not lead_tracking._is_upgrade_row(r)), {})


def _accounts(client_id: int) -> list:
    """Platform + status only — never a secret column."""
    return (_db().table("client_accounts").select("id,platform,status").eq("client_id", int(client_id))
            .order("id").limit(200).execute().data or [])


def _exempt_services(client_id: int) -> list:
    from core.client_auth import exempt_package
    return list((exempt_package(client_id) or {}).get("recommended_services") or [])


def _readiness(client_id: int) -> dict:
    from core import client_journey
    return client_journey.connection_status(client_id)


def _delete_activity(ids: list):
    _db().table("client_activity").delete().in_("id", list(ids)).execute()


def _delete_setting(key: str):
    _db().table("app_settings").delete().eq("key", key).execute()


def _write_setting(key: str, value: dict):
    _db().table("app_settings").upsert({"key": key, "value": value, "updated_at": _now()}, on_conflict="key").execute()


def _cancel_card(card_id: int) -> bool:
    rows = (_db().table("agent_actions").update({"status": "cancelled", "decided_at": _now(), "decided_by": "staff",
                                                 "outcome": "intake_reset"})
            .eq("id", int(card_id)).in_("status", list(OPEN_CARD_STATUSES)).execute().data or [])
    return bool(rows)


def _delete_lead(lead_id) -> None:
    _db().table("leads").delete().eq("id", lead_id).execute()


def _alert(lines: list):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, lines)


# ─── What is kept (a fingerprint compared before and after) ──────────────────

def _fingerprint(value) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]


def kept(client_id: int) -> dict:
    """Everything a reset must leave exactly as it is: {"accounts", "settings":
    {key: fingerprint}, "brief": {versions, v, status}}. No secret is read."""
    settings = {}
    for prefix in KEEPS:
        key = f"{prefix}{int(client_id)}"
        value = _setting(key)
        if value is not None:
            settings[key] = _fingerprint(value)
    brief = _setting(f"brand_brief:{int(client_id)}") or {}
    versions = brief.get("versions") or []
    last = versions[-1] if versions else {}
    return {"accounts": [f"{a.get('platform')}:{a.get('status')}:{a.get('id')}" for a in _accounts(client_id)],
            "settings": settings,
            "brief": {"versions": len(versions), "v": last.get("v"), "status": last.get("status"),
                      "chars": len(last.get("text") or "")}}


# ─── The marker the interview reads ──────────────────────────────────────────

def since(client_id: int) -> str:
    """ISO time of the last reset ("" = never): the interview counts and reads
    only what was said after it. Never raises."""
    try:
        return str((_setting(f"{PREFIX}{int(client_id)}") or {}).get("reset_at") or "")
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: reset marker unreadable ({type(e).__name__})")
        return ""


# ─── The plan (reads only) ───────────────────────────────────────────────────

def _goal_summary(value) -> dict:
    if not isinstance(value, dict) or not value.get("agreed_at"):
        return {}
    return {"agreed_at": value.get("agreed_at"), "baseline_captured_at": (value.get("baseline") or {}).get("captured_at"),
            "targets": [{"metric": t.get("metric"), "target": t.get("target")} for t in value.get("targets") or []],
            "improve": value.get("improve") or "", "learning": bool(value.get("learning"))}


def can_start(client_id: int) -> dict:
    """Whether the deep interview can open after a reset, and what stops it."""
    try:
        status = _readiness(client_id)
    except Exception as e:
        return {"ready": False, "reason": "unreadable", "detail": type(e).__name__}
    if not status.get("resolved"):
        return {"ready": False, "reason": "package_unresolved",
                "detail": "what this client purchased cannot be read: no checkout row and no admin services grant "
                          "(the account_created_by_admin activity row). Grant the services to the account first."}
    if not status.get("steps"):
        return {"ready": False, "reason": "no_required_connections",
                "detail": "the package resolves but lists no service that needs a connection, so the gate can never open."}
    if not status.get("complete"):
        return {"ready": False, "reason": "connections_missing", "missing": status.get("missing") or []}
    return {"ready": True, "connected": status.get("connected") or []}


def plan(client_id: int) -> dict:
    """What a reset would delete and what it keeps — reads only."""
    client_id = int(client_id)
    done, facts = _activity_rows(client_id, DONE_ACTION), _activity_rows(client_id, CAPTURE_ACTION)
    goals = _setting(f"{GOALS_PREFIX}{client_id}")
    cards, lead = _open_cards(client_id), _lead(client_id)
    try:
        grant = _exempt_services(client_id)
    except Exception:
        grant = []
    lead_view = {}
    if lead:
        answers = lead.get("answers") if isinstance(lead.get("answers"), dict) else {}
        lead_view = {"id": lead.get("id"), "created_at": lead.get("created_at"), "answers": len(answers),
                     "has_proposal": bool(lead.get("proposal")),
                     # deletable only when what the client purchased does not rest on this row
                     "deletable": bool(grant),
                     "deleted_by_default": False}
    delete = {
        "characterised_mark": [{"id": r["id"], "created_at": r.get("created_at")} for r in done],
        "captured_facts": [{"id": r["id"], "created_at": r.get("created_at"),
                            "competitors": len((r.get("details") or {}).get("competitors") or []),
                            "notes": sorted(((r.get("details") or {}).get("notes") or {}).keys())} for r in facts],
        "agreed_goals": _goal_summary(goals),
        "open_goals_card": [{"id": c["id"], "status": c.get("status")} for c in cards],
        "sales_lead": lead_view,
    }
    keep = kept(client_id)
    return {"client_id": client_id, "delete": delete,
            "keep": {"connections": keep["accounts"], "brand_brief": keep["brief"],
                     "stored": {key: KEEPS[key[:key.index(":") + 1]] for key in keep["settings"]},
                     "also": ["chat history (the new interview reads only what is said after the reset)",
                              "the 90-day clock stamp", "decided cards (history)", "files in Drive", "character references"]},
            "nothing_to_reset": not (done or facts or delete["agreed_goals"] or cards),
            "intake_can_start": can_start(client_id), "confirm_phrase": phrase(client_id),
            "contradiction": contradiction(client_id, goals)}


# ─── The reset ───────────────────────────────────────────────────────────────

def run(client_id: int, confirm: str, include_lead: bool = False, by: str = "admin") -> dict:
    """Delete exactly what `plan` listed, after a copy of each row is stored.
    Refuses without the typed phrase; ABORTS before deleting anything when the
    copy cannot be stored; verifies afterwards that nothing kept has moved."""
    client_id = int(client_id)
    if str(confirm or "").strip() != phrase(client_id):
        raise ResetError("confirm", f"type exactly: {phrase(client_id)}")
    before = kept(client_id)
    done, facts = _activity_rows(client_id, DONE_ACTION), _activity_rows(client_id, CAPTURE_ACTION)
    goals = _setting(f"{GOALS_PREFIX}{client_id}")
    cards = _open_cards(client_id)
    lead = _lead(client_id) if include_lead else {}
    if lead and not _exempt_services(client_id):
        raise ResetError("lead_carries_package", "the purchased package is read from this lead; it is not deleted")

    stamp = _now()
    previous = _setting(f"{PREFIX}{client_id}") or {}
    record = {"reset_at": stamp, "by": str(by)[:40],
              "backup": {"characterised_mark": done, "captured_facts": facts, "agreed_goals": goals,
                         "open_goals_card": cards, "sales_lead": lead or None},
              "earlier": ([{k: previous.get(k) for k in ("reset_at", "by", "backup")}] + list(previous.get("earlier") or []))[:5]
              if previous.get("reset_at") else list(previous.get("earlier") or [])}
    try:
        _write_setting(f"{PREFIX}{client_id}", record)           # the copy FIRST: no copy, no delete
    except Exception as e:
        raise ResetError("backup_failed", f"nothing was deleted ({type(e).__name__})")

    did = {"characterised_mark": 0, "captured_facts": 0, "agreed_goals": False, "open_goals_card": 0, "sales_lead": False}
    if done:
        _delete_activity([r["id"] for r in done])
        did["characterised_mark"] = len(done)
    if facts:
        _delete_activity([r["id"] for r in facts])
        did["captured_facts"] = len(facts)
    if goals is not None:
        _delete_setting(f"{GOALS_PREFIX}{client_id}")
        did["agreed_goals"] = True
    did["open_goals_card"] = sum(1 for c in cards if _cancel_card(c["id"]))
    if lead:
        _delete_lead(lead["id"])
        did["sales_lead"] = True

    after = kept(client_id)
    moved = ([f"connections changed: {before['accounts']} -> {after['accounts']}"] if before["accounts"] != after["accounts"] else [])
    moved += [f"{key} changed" for key in sorted(set(before["settings"]) | set(after["settings"]))
              if before["settings"].get(key) != after["settings"].get(key)]
    left = bool(_activity_rows(client_id, DONE_ACTION)) or _setting(f"{GOALS_PREFIX}{client_id}") is not None
    line = (f"client {client_id}: intake reset by {by} — characterised mark {did['characterised_mark']}, captured facts "
            f"{did['captured_facts']}, agreed goals {'removed' if did['agreed_goals'] else 'none'}, open goals cards cancelled "
            f"{did['open_goals_card']}, sales lead {'removed' if did['sales_lead'] else 'kept'}. A copy of every removed row is "
            f"in app_settings {PREFIX}{client_id}.")
    if moved:
        line += " SOMETHING THAT MUST NOT MOVE DID (a concurrent write, not this reset — it writes to none of them): " + "; ".join(moved)
    try:
        _alert([line])
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: reset not alerted ({type(e).__name__})")
    return {"client_id": client_id, "reset_at": stamp, "did": did, "kept_unchanged": not moved, "moved": moved,
            "still_marked": left, "brand_brief": after["brief"], "intake_can_start": can_start(client_id)}


# ─── Targets that contradict the client's own result definition ──────────────

def _result_kind(client_id: int) -> str:
    from core import target_inputs
    return target_inputs.result_kind(target_inputs._conversion(client_id))


def contradiction(client_id: int, value=None) -> dict:
    """{} when the agreed result target counts what the client says a result
    is; else which metric is counted against which stated result."""
    from core import quarter_goals, target_inputs
    value = _setting(f"{GOALS_PREFIX}{int(client_id)}") if value is None else value
    if not isinstance(value, dict) or not value.get("agreed_at"):
        return {}
    counted = [t.get("metric") for t in value.get("targets") or [] if t.get("metric") in quarter_goals.COUNTED]
    try:
        kind = _result_kind(client_id)
    except Exception as e:
        return {"client_id": int(client_id), "unknown": True, "why": f"result definition unreadable ({type(e).__name__})",
                "counted": counted} if counted else {}
    wrong = [m for m in counted if m != target_inputs.metric_of(kind)]
    if not wrong:
        return {}
    return {"client_id": int(client_id), "stated_result": kind, "counted": wrong, "agreed_at": value.get("agreed_at"),
            "targets": [{"metric": t.get("metric"), "target": t.get("target")} for t in value.get("targets") or []
                        if t.get("metric") in wrong]}


def audit() -> dict:
    """Every client with agreed goals: {"checked", "with_result_target",
    "contradictions": [...], "unknown": [...]}. Reads only."""
    out = {"checked": 0, "with_result_target": 0, "contradictions": [], "unknown": [], "clients": []}
    from core import quarter_goals
    for row in _settings_like(GOALS_PREFIX):
        value = row.get("value")
        try:
            client_id = int(str(row.get("key"))[len(GOALS_PREFIX):])
        except ValueError:
            continue
        if not isinstance(value, dict) or not value.get("agreed_at"):
            continue
        out["checked"] += 1
        out["clients"].append(client_id)
        if any(t.get("metric") in quarter_goals.COUNTED for t in value.get("targets") or []):
            out["with_result_target"] += 1
        found = contradiction(client_id, value)
        if found.get("unknown"):
            out["unknown"].append(found)
        elif found:
            out["contradictions"].append(found)
    out["clients"].sort()
    return out
