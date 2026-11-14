"""Tier 2 of the operator (docs/OPERATOR.md): SAFE actions — executed at
once, no approval step, always reversible — and the operator's audit log.

Every action:
- is one entry in ACTIONS (a new one = one dict + a test);
- validates its params here, never trusting the model or the browser;
- is audited BEFORE it runs (fail closed: no audit row → it doesn't run) and
  again with its result: who, what, when, result — `alerts` rows with source
  AUDIT_SOURCE (protected from bulk delete and the Google-data purge, like
  the other audit sources);
- returns plain data, never a token, key or secret value.

`by_agent`: the operator may run the diagnostic ones itself (health check,
token refresh, cache clear). The ones with an outside effect — rollback,
restore, flag, re-send — are only OFFERED by the agent as a one-tap button;
the owner's tap runs them (still immediately, still Tier 2).
"""
import re
from datetime import datetime, timezone

SERVICE_NAME = "operator_actions"
AUDIT_SOURCE = "operator"


class AuditUnavailable(RuntimeError):
    """The audit entry could not be written — the action must not run."""


class ActionError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Audit ───────────────────────────────────────────────────────────────────

def _clean(value, limit=300) -> str:
    from core.redaction import redact
    return redact(str(value))[:limit]


def audit(event: str, who: str, detail: str = "", status: str = "resolved"):
    line = f"{event} by {who} at {_now()}" + (f" — {_clean(detail, 600)}" if detail else "")
    try:
        _db().table("alerts").insert({"source": AUDIT_SOURCE, "issues": [line], "status": status,
                                      "resolved_at": _now()}).execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] audit write failed: {type(e).__name__}")
        raise AuditUnavailable(event) from e
    print(f"[{SERVICE_NAME}] {line}")


def recent_audit(limit: int = 50) -> list:
    rows = (_db().table("alerts").select("issues, created_at").eq("source", AUDIT_SOURCE)
            .order("created_at", desc=True).limit(int(limit)).execute().data or [])
    # Stored lines keep the internal actor name; the owner reads איציק
    from core.operator_agent import DISPLAY_NAME
    return [{"at": r.get("created_at"),
             "entry": (r.get("issues") or [""])[0].replace("operator agent", DISPLAY_NAME)} for r in rows]


# ─── Params ──────────────────────────────────────────────────────────────────

def _client_id(params: dict) -> int:
    try:
        cid = int(params.get("client_id"))
    except (TypeError, ValueError):
        raise ActionError("ERR_PARAM", "client_id")
    if cid <= 0:
        raise ActionError("ERR_PARAM", "client_id")
    from agents.client_agent import get_client
    if not get_client(cid):
        raise ActionError("ERR_CLIENT_UNKNOWN", str(cid))
    return cid


def _choice(params: dict, name: str, options) -> str:
    value = str(params.get(name) or "")
    if value not in options:
        raise ActionError("ERR_PARAM", name)
    return value


# ─── The actions ─────────────────────────────────────────────────────────────

HEALTH_CHECKS = ("monitor", "google_ads", "php", "client_state")


def _health_check(params: dict) -> dict:
    check = _choice(params, "check", HEALTH_CHECKS)
    if check == "monitor":
        from agents.monitor_agent import run_deep_scan
        return {"check": check, "report": run_deep_scan()}
    if check == "google_ads":
        from agents.google_ads_agent import run_health_scan
        return {"check": check, "report": run_health_scan()}
    if check == "php":
        from core import php_exposure
        cid = _client_id(params) if params.get("client_id") else None
        return {"check": check, "report": php_exposure.run_scan(cid)}
    from core import client_state
    cid = _client_id(params)
    state = client_state.snapshot(cid, refresh=True)
    return {"check": check, "client_id": cid, "failed_sections": client_state.failed_sections(state),
            "connections": client_state.for_model(state).get("connections")}


TOKEN_PLATFORMS = ("google_ads", "meta", "gtm", "tiktok")


def _metrics_outcome(result: dict) -> dict:
    """A metrics read reduced to its health — never the numbers, never a token."""
    if result.get("connected") is False:
        return {"ok": False, "state": "not_connected"}
    if result.get("needs_reconnect"):
        return {"ok": False, "state": "needs_reconnect"}
    if result.get("error"):
        return {"ok": False, "state": "error", "error": _clean(result["error"], 200)}
    return {"ok": True, "state": "ok"}


def _refresh_token(params: dict) -> dict:
    cid = _client_id(params)
    platform = _choice(params, "platform", TOKEN_PLATFORMS)
    if platform == "google_ads":
        # Forget the recorded health (incl. a "dead, re-probe in 6h" mark),
        # then one live read with a fresh access token
        from core import google_ads_health
        from agents.google_ads_agent import get_daily_metrics
        google_ads_health.clear(cid)
        outcome = _metrics_outcome(get_daily_metrics(cid, force_refresh=True))
    elif platform == "meta":
        from agents.meta_ads_agent import get_daily_metrics
        outcome = _metrics_outcome(get_daily_metrics(cid, force_refresh=True))
    elif platform == "gtm":
        from core import gtm_connection
        gtm_connection.forget(cid)
        view = gtm_connection.connection_view(cid, refresh=True) or {}
        state = (view.get("token") or {}).get("state", "not_connected") if view.get("connected") \
            else "not_connected"
        outcome = {"ok": state == "ok", "state": state}
    else:
        from agents import tiktok_content_agent as tt
        conn = tt._get_connection(cid)
        if not (conn.get("access_token") and conn.get("account_id")):
            outcome = {"ok": False, "state": "not_connected"}
        else:
            try:
                tt._live_access_token(cid, conn)          # refreshes + re-stores + records scopes
                from core import tiktok_grant
                outcome = {"ok": not tiktok_grant.needs_reconnect(cid), "state": tiktok_grant.state(cid)}
            except Exception as e:
                outcome = {"ok": False, "state": "error", "error": _clean(type(e).__name__, 80)}
    from core import client_state
    client_state.forget(cid)
    return {"client_id": cid, "platform": platform, **outcome}


def _clear_cache(params: dict) -> dict:
    from core import client_state, gtm_connection
    cid = _client_id(params)
    client_state.forget(cid)
    gtm_connection.forget(cid)
    return {"client_id": cid, "cleared": ["client_state", "gtm_connection"],
            "note": "this instance only — other Cloud Run instances expire theirs within minutes (SECTION_TTL)"}


def _flip_flag(params: dict) -> dict:
    from core import operator_flags
    try:
        return operator_flags.set_flag(str(params.get("flag") or ""), str(params.get("state") or ""))
    except operator_flags.FlagError as e:
        raise ActionError(e.code)


def failed_notifications(limit: int = 20) -> list:
    """Urgent WhatsApp notifications that failed and were not re-sent yet."""
    rows = (_db().table("client_activity").select("id, client_id, created_at, details, result")
            .eq("action_type", "urgent_notification").order("id", desc=True).limit(200).execute().data or [])
    resent = {int((r.get("details") or {}).get("of", 0)) for r in
              (_db().table("client_activity").select("details").eq("action_type", "urgent_notification_resent")
               .order("id", desc=True).limit(200).execute().data or [])
              if (r.get("details") or {}).get("sent")}
    out = []
    for r in rows:
        if (r.get("details") or {}).get("sent") is False and r["id"] not in resent:
            result = r.get("result") or {}
            out.append({"id": r["id"], "client_id": r["client_id"], "at": r.get("created_at"),
                        "preview": _clean(result.get("message", ""), 120),
                        "resendable": bool(result.get("full"))})
        if len(out) >= limit:
            break
    return out


def _resend_notification(params: dict) -> dict:
    try:
        activity_id = int(params.get("activity_id"))
    except (TypeError, ValueError):
        raise ActionError("ERR_PARAM", "activity_id")
    rows = (_db().table("client_activity").select("id, client_id, action_type, details, result")
            .eq("id", activity_id).limit(1).execute().data or [])
    row = rows[0] if rows else {}
    if row.get("action_type") != "urgent_notification" or (row.get("details") or {}).get("sent") is not False:
        raise ActionError("ERR_NOT_A_FAILED_NOTIFICATION")
    result = row.get("result") or {}
    if not result.get("full"):
        # Rows from before 2026-11-01 kept only the first 200 characters
        raise ActionError("ERR_MESSAGE_TRUNCATED")
    from agents.client_agent import get_client, log_activity
    from core.whatsapp_service import send_whatsapp
    client = get_client(row["client_id"]) or {}
    sent = send_whatsapp(client.get("phone", ""), result["message"])
    log_activity(row["client_id"], "operator", "urgent_notification_resent",
                 {"of": activity_id, "sent": sent, "channel": "whatsapp"})
    return {"activity_id": activity_id, "client_id": row["client_id"], "sent": sent}


def _rollback(params: dict) -> dict:
    from core import cloud_ops
    target = str(params.get("revision") or "")
    if not target:
        target = (cloud_ops.previous_revision() or {}).get("name", "")
    if not re.fullmatch(r"[a-z0-9-]{1,80}", target):
        raise ActionError("ERR_NO_PREVIOUS_REVISION")
    try:
        return {**cloud_ops.route_to(target), "undo": "restore_latest"}
    except cloud_ops.CloudOpsUnavailable as e:
        raise ActionError("ERR_CLOUD", e.code)


def _restore_latest(params: dict) -> dict:
    from core import cloud_ops
    try:
        return {**cloud_ops.route_to_latest(), "undo": "rollback"}
    except cloud_ops.CloudOpsUnavailable as e:
        raise ActionError("ERR_CLOUD", e.code)


def _int_param(params: dict, name: str) -> int:
    try:
        value = int(params.get(name))
    except (TypeError, ValueError):
        raise ActionError("ERR_PARAM", name)
    if value <= 0:
        raise ActionError("ERR_PARAM", name)
    return value


def _refusal(e: ValueError):
    return ActionError("ERR_REFUSED", str(e)[:80])


def _fail_action(params: dict) -> dict:
    """End a STUCK card (status running) as failed, with a reason — the
    reaper's own finalize: alert closed, the client told the next step."""
    from core import agent_actions
    reason = _clean(params.get("reason") or "", 200).strip()
    if len(reason) < 3:
        raise ActionError("ERR_PARAM", "reason")
    try:
        return agent_actions.operator_fail(_int_param(params, "proposal_id"), reason, "operator")
    except ValueError as e:
        raise _refusal(e)


def _retry_action(params: dict) -> dict:
    """Re-offer a failed / expired / cancelled card to the client as a NEW
    card. Nothing runs: the client approves it with their own click."""
    from core import agent_actions
    try:
        return agent_actions.operator_retry(_int_param(params, "proposal_id"), "operator")
    except ValueError as e:
        raise _refusal(e)


def _resolve_alert(params: dict) -> dict:
    alert_id = _int_param(params, "alert_id")
    rows = (_db().table("alerts").select("id,source,status").eq("id", alert_id).limit(1).execute().data or [])
    if not rows:
        raise ActionError("ERR_REFUSED", "not_found")
    if rows[0].get("source") == AUDIT_SOURCE:
        raise ActionError("ERR_REFUSED", "audit_rows_are_history")
    if rows[0].get("status") != "open":
        return {"alert_id": alert_id, "resolved": False, "note": "already resolved"}
    from core import admin_service
    admin_service.resolve_alerts([alert_id])
    return {"alert_id": alert_id, "resolved": True}


def _resolve_alert_group(params: dict) -> dict:
    """Every OPEN alert of one triage group (core/operator_insight.alert_triage
    `key`), in one update. Re-read now: only alerts that are still open and
    still carry that key. Audit rows are never in a group."""
    from core import admin_service, operator_insight
    key = str(params.get("key") or "")
    if not re.fullmatch(r"[0-9a-f]{24}", key):
        raise ActionError("ERR_PARAM", "key")
    ids = operator_insight.group_alert_ids(key)
    if not ids:
        return {"key": key, "resolved": 0, "note": "nothing open in this group any more"}
    done = admin_service.resolve_alerts(ids)
    return {"key": key, "resolved": int((done or {}).get("resolved") or 0), "alert_ids": ids[:50]}


# The connections refresh_token can ask about, by the platform row that proves
# the client has one
_CHECKABLE = {"google_ads": "google_ads", "meta_ads": "meta", "google_tagmanager": "gtm", "tiktok": "tiktok"}


def _check_client_connections(params: dict) -> dict:
    """One live read per connected platform of ONE client (the same reads as
    refresh_token, each recording its real health). Reads only."""
    cid = _client_id(params)
    from agents.client_agent import get_accounts
    platforms = {a.get("platform") for a in get_accounts(cid) if a.get("status") == "active"}
    results = {}
    for row_platform, name in _CHECKABLE.items():
        if row_platform not in platforms:
            continue
        try:
            out = _refresh_token({"client_id": cid, "platform": name})
            results[name] = {k: out.get(k) for k in ("ok", "state", "error") if out.get(k) is not None}
        except Exception as e:
            results[name] = {"ok": False, "state": "error", "error": _clean(type(e).__name__, 80)}
    return {"client_id": cid, "checked": results,
            "not_connected": sorted(n for p, n in _CHECKABLE.items() if p not in platforms)}


def _mark_request_handled(params: dict) -> dict:
    from core import agent_actions
    cid = _client_id(params)
    try:
        return agent_actions.operator_mark_request_handled(cid, _int_param(params, "request_id"),
                                                           _clean(params.get("note") or "", 300), "operator")
    except ValueError as e:
        raise _refusal(e)


def _run_reaper(params: dict) -> dict:
    """What the 5-minute scheduler job does, now."""
    from core import agent_actions
    return agent_actions.reap_stuck(by="operator")


ACTIONS = {
    "fail_action": {"run": _fail_action, "by_agent": False, "label_he": "סגירת פעולה תקועה ככישלון",
                    "undo_he": "\"הצעה מחדש ללקוח\" מכינה לו כרטיס חדש לאישור"},
    "retry_action": {"run": _retry_action, "by_agent": False, "label_he": "הצעה מחדש ללקוח",
                     "undo_he": "הכרטיס החדש רק מחכה לאישור הלקוח — אם לא יאשר, הוא פג מעצמו"},
    "resolve_alert": {"run": _resolve_alert, "by_agent": False, "label_he": "סימון התראה כטופלה",
                      "undo_he": "אם הבעיה חוזרת נפתחת התראה חדשה; ההיסטוריה נשמרת"},
    "resolve_alert_group": {"run": _resolve_alert_group, "by_agent": False,
                            "label_he": "סימון קבוצת התראות זהות כטופלה",
                            "undo_he": "אם הבעיה חוזרת נפתחת התראה חדשה; ההיסטוריה נשמרת"},
    "check_client_connections": {"run": _check_client_connections, "by_agent": True,
                                 "label_he": "בדיקה חיה של כל החיבורים של לקוח",
                                 "undo_he": "קריאה חיה אחת לכל חיבור — אין מה לבטל"},
    "mark_request_handled": {"run": _mark_request_handled, "by_agent": False,
                             "label_he": "סימון בקשת צוות כ\"טופל\"",
                             "undo_he": "הבקשה נשמרת; אם משהו עוד פתוח, הלקוח כותב ונפתחת בקשה חדשה"},
    "run_reaper": {"run": _run_reaper, "by_agent": True, "label_he": "סגירת פעולות תקועות עכשיו",
                   "undo_he": "\"הצעה מחדש ללקוח\" לכל פעולה שנסגרה"},
    "health_check": {"run": _health_check, "by_agent": True, "label_he": "הרצת בדיקת תקינות",
                     "undo_he": "קריאה בלבד — אין מה לבטל"},
    "refresh_token": {"run": _refresh_token, "by_agent": True, "label_he": "רענון חיבור",
                      "undo_he": "קריאה חיה אחת עם טוקן טרי — אין מה לבטל"},
    "clear_cache": {"run": _clear_cache, "by_agent": True, "label_he": "ניקוי מטמון",
                    "undo_he": "המטמון מתמלא מחדש בקריאה הבאה"},
    "flip_flag": {"run": _flip_flag, "by_agent": False, "label_he": "שינוי מתג",
                  "undo_he": "\"חזרה לברירת מחדל\" מוחקת את העקיפה"},
    "resend_notification": {"run": _resend_notification, "by_agent": False,
                            "label_he": "שליחה חוזרת של התראת WhatsApp שנכשלה",
                            "undo_he": "הודעה שנשלחה אי אפשר להחזיר — לכן רק בלחיצה שלך"},
    "rollback": {"run": _rollback, "by_agent": False, "label_he": "חזרה לגרסה הקודמת",
                 "undo_he": "\"חזרה לגרסה האחרונה\""},
    "restore_latest": {"run": _restore_latest, "by_agent": False, "label_he": "חזרה לגרסה האחרונה",
                       "undo_he": "\"חזרה לגרסה הקודמת\""},
}


def _params_line(params: dict) -> str:
    return ", ".join(f"{k}={_clean(v, 60)}" for k, v in sorted((params or {}).items()))


def run(name: str, params: dict, who: str, by_agent: bool = False) -> dict:
    """Run ONE Tier 2 action now. Raises ActionError for a refusal and
    AuditUnavailable when the audit can't be written (then nothing ran)."""
    spec = ACTIONS.get(name)
    if not spec:
        raise ActionError("ERR_ACTION_UNKNOWN")
    if by_agent and not spec["by_agent"]:
        raise ActionError("ERR_OWNER_TAP_ONLY")
    params = params if isinstance(params, dict) else {}
    audit(f"tier2 {name} START ({_params_line(params)})", who)
    try:
        result = spec["run"](params)
    except ActionError as e:
        _audit_after(f"tier2 {name} REFUSED {e.code} {e.detail}", who)
        raise
    except Exception as e:
        _audit_after(f"tier2 {name} FAILED {type(e).__name__}", who)
        raise ActionError("ERR_ACTION_FAILED", type(e).__name__)
    _audit_after(f"tier2 {name} OK", who, detail=str(result))
    return {"action": name, "result": result, "undo_he": spec["undo_he"]}


def _audit_after(event: str, who: str, detail: str = ""):
    try:
        audit(event, who, detail)
    except AuditUnavailable:
        pass   # the START row exists; the outcome is still in the response and the server log
