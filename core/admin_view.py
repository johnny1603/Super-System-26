"""Admin "enter as client" — staff open a client's dashboard exactly as the
client sees it (same /api/client/* data, chat history, cards), READ-ONLY.

How it works (core/api_server):
- POST /api/admin/clients/{id}/view-as (admin session only) writes the audit
  entry FIRST (no audit → no view) and sets the `admin_view` cookie: signed
  with its own derived secret, bound to the admin session that opened it
  (session.verify_admin_view_token), 2 hours at most. NO client session is
  created and nothing on the client row (password, login codes,
  session_version) is read for or written by it.
- `_require_session` resolves that cookie before the client `session`
  cookie and lets the request through ONLY when its endpoint is in
  READ_ENDPOINTS (by FastAPI endpoint function name) or is the staff decide
  endpoint. Everything else — chat sends, uploads, connects, OAuth starts,
  exports, settings, closing the account — answers 403
  ERR_ADMIN_VIEW_READ_ONLY. An allowlist, not a blocklist: a new client
  endpoint is blocked in the admin view until someone adds it here.
- Approving or cancelling a card: only with `staff_action: true` in the
  request AND only for actions staff may already approve in the Control Room
  (agent_actions.staff_may_decide). Recorded as decided_by 'staff' and in the
  audit log before it runs.
- POST /api/admin/view-as/exit clears the cookie and audits the stop.

Audit log: the `alerts` table (source "admin_view", status resolved, so it
never shows as an open alert), the same home as the deletion / bulk-delete
audit entries, and protected the same way (bulk_delete.AUDIT_SOURCES). Who =
the admin session's fingerprint + IP — the admin login is one shared
password, so that is the most specific "who" that exists. Ids only, no
client personal data.
"""
from datetime import datetime, timezone

COOKIE = "admin_view"
AUDIT_SOURCE = "admin_view"

# Endpoints (api_server function names) that only READ. Each was checked for
# side effects; see docs/ACCESS-MODES.md "Admin view". Deliberately NOT here:
# client_media_folder (creates + shares the Drive folder on first ask),
# client_data_export / client_export / client_export_google_doc, every
# *_oauth_start, every POST except the decide endpoint below.
READ_ENDPOINTS = frozenset({
    "dashboard_data", "client_account", "client_performance", "client_connection_help",
    "client_tools", "client_voice_list",
    "client_chat_history", "client_chat_unread", "client_upgrade_options", "client_billing",
    "client_interview_state", "client_connection_checklist", "client_journey_view",
    "client_external_costs", "google_ads_accounts", "gtm_connection_status", "gtm_containers",
    "client_search_console_sites", "client_leads_list", "client_lead_capture_setup",
    "client_landing_pages", "client_crm_status", "client_support", "client_media_drive",
    "client_media_drive_thumb", "client_media_drive_file", "client_media_assets",
    "client_suggestions", "client_open_actions", "client_outreach", "client_ownership",
})
STAFF_DECIDE_ENDPOINT = "client_decide_action"
READ_METHODS = ("GET", "HEAD")


def allowed(endpoint_name: str, method: str) -> bool:
    if endpoint_name == STAFF_DECIDE_ENDPOINT:
        return method == "POST"          # the endpoint itself demands staff_action
    return method in READ_METHODS and endpoint_name in READ_ENDPOINTS


class AuditUnavailable(RuntimeError):
    """The audit entry could not be written — the caller must not proceed."""


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def who(admin_fingerprint: str, ip: str) -> str:
    return f"admin session {admin_fingerprint or '?'} from {ip or '?'}"


def audit(event: str, client_id: int, admin_fingerprint: str, ip: str, detail: str = ""):
    """One audit row. Raises AuditUnavailable when it cannot be written."""
    line = (f"{event}: client {int(client_id)} by {who(admin_fingerprint, ip)} at {_now()}"
            + (f" — {detail}" if detail else ""))
    try:
        _db().table("alerts").insert({"source": AUDIT_SOURCE, "issues": [line], "status": "resolved",
                                      "resolved_at": _now()}).execute()
    except Exception as e:
        print(f"[admin_view] audit write failed: {type(e).__name__}")
        raise AuditUnavailable(event) from e
    print(f"[admin_view] {line}")


def recent(limit: int = 100) -> list:
    rows = (_db().table("alerts").select("issues, created_at").eq("source", AUDIT_SOURCE)
            .order("created_at", desc=True).limit(int(limit)).execute().data or [])
    return [{"at": r.get("created_at"), "entry": (r.get("issues") or [""])[0]} for r in rows]
