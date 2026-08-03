"""External CRM sync — pushes a client's OWN leads into a CRM they own.

## Opt-in and additive, in that order

A client with no CRM connected must see ZERO change. Every entry point here
returns early when `_connection()` is empty, `core/client_leads.py` is untouched
except for one guarded dispatch call, and no lead is ever held, delayed or
rejected because of anything in this file.

## The invariant that outranks everything else

**Our copy of the lead is the record. The CRM push is a side effect.**
`capture_lead` stores the row and returns BEFORE any CRM call is made (the
capture endpoint dispatches this through FastAPI BackgroundTasks), so a slow,
broken or revoked CRM cannot delay a customer's form submit or lose an enquiry.
Same principle as `client_leads._insert_lead` keeping the lead when attribution
columns are missing: never trade a real enquiry for a secondary concern.

## Credentials: the existing pattern, not a new one

One `client_accounts` row, written through `client_agent.upsert_account` — the
same helper website/tiktok/media already use:

| platform | account_id | access_token |
|---|---|---|
| `crm` | the vendor key (`hubspot` / `pipedrive`) | credential, or `credential::extra` |

The vendor lives in `account_id` (not in the platform string) ON PURPOSE:
`upsert_account` dedupes per platform, so switching from HubSpot to Pipedrive
REPLACES the row and the old credential is gone. Encoding the vendor into the
platform would leave the previous vendor's key sitting in the table forever.
The `::` composite for Pipedrive's company domain is the same idiom
`tiktok_service` uses for its token pair.

Disconnect deletes the row via `client_agent.remove_accounts` — flipping a
status would leave a live credential at rest.

## Retry

A failed push is recorded on the lead row (`crm_sync_status='failed'`) and
retried by `retry_failed_syncs()` on a schedule, up to MAX_SYNC_ATTEMPTS.
Permanent failures (dead credential, rejected field — `CRMError.permanent`) are
marked failed WITHOUT consuming retries against a wall they cannot clear; the
client is told through the connection card instead.

If `migrations/2026-08-03-crm-sync.sql` has not been run, the status columns do
not exist: pushes still happen and still reach the CRM, but nothing is recorded
and nothing can be retried. That degradation is detected and surfaced, never
silent.
"""
import os
from datetime import datetime, timedelta, timezone

from core import crm_service
from core.agent_base import agent_alert, log_step
from core.crm_service import CRMError

AGENT_NAME = "crm_agent"

CRM_PLATFORM = "crm"
COMPOSITE_DELIMITER = "::"

MAX_SYNC_ATTEMPTS = 4
# How far back the retry pass looks. A lead that has been failing for a fortnight
# is not going to start working because we asked a 30th time.
RETRY_WINDOW_DAYS = 14
RETRY_BATCH = 50

_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"],
                                     os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _log_activity(client_id: int, action_type: str, details: dict, result: dict = None):
    try:
        _db().table("client_activity").insert({
            "client_id": client_id, "agent_name": AGENT_NAME,
            "action_type": action_type, "details": details, "result": result or {},
        }).execute()
    except Exception as e:
        print(f"[{AGENT_NAME}] activity log failed for client {client_id}: {e}")


# ─── Connection ───────────────────────────────────────────────────────────────

def _connection(client_id: int) -> dict:
    """The client's CRM row, or {}. Never raises — every caller treats "no CRM"
    and "could not check" the same way: do nothing."""
    try:
        rows = (_db().table("client_accounts").select("*")
                .eq("client_id", client_id).eq("platform", CRM_PLATFORM)
                .eq("status", "active").order("id", desc=True)
                .limit(1).execute().data or [])
    except Exception as e:
        print(f"[{AGENT_NAME}] connection lookup failed for client {client_id}: {e}")
        return {}
    return rows[0] if rows else {}


def _creds(connection: dict) -> tuple:
    """(vendor, credential, extra) from a client_accounts row."""
    vendor = connection.get("account_id") or ""
    stored = connection.get("access_token") or ""
    credential, _, extra = stored.partition(COMPOSITE_DELIMITER)
    return vendor, credential, extra


def connect(client_id: int, vendor: str, credential: str, extra: str = "") -> dict:
    """Validate against the live CRM, then store. A bad key is user input, not
    an incident — it returns an error dict and raises no alert."""
    vendor = (vendor or "").strip().lower()
    log_step(AGENT_NAME, "connect", f"client {client_id}: {vendor}")
    try:
        verified = crm_service.verify_credentials(vendor, credential, extra)
    except CRMError as e:
        # The raw vendor body goes to the LOG, never to the client: it is
        # unlocalised, often enormous, and leaks the vendor's internals into a
        # Hebrew dashboard. The friendly message already names the two things a
        # client can actually fix — a truncated paste, or a token without scope.
        log_step(AGENT_NAME, "connect", f"client {client_id}: {vendor} rejected — {e}")
        return {"success": False,
                "error": "החיבור נכשל — בדקו שהמפתח הועתק במלואו ושיש לו הרשאת כתיבה לאנשי קשר"}
    except Exception as e:
        log_step(AGENT_NAME, "connect", f"client {client_id}: {vendor} unreachable — {e}")
        return {"success": False, "error": "לא הצלחנו להגיע ל-CRM כרגע — נסו שוב בעוד רגע"}

    stored = credential.strip()
    if extra and extra.strip():
        stored = f"{stored}{COMPOSITE_DELIMITER}{extra.strip()}"

    from agents.client_agent import upsert_account
    upsert_account(client_id, CRM_PLATFORM, vendor, stored, "active")
    _log_activity(client_id, "crm_connected", {"vendor": vendor},
                  {"account_label": verified.get("account_label", "")})
    log_step(AGENT_NAME, "connect", f"client {client_id}: {vendor} connected")
    return {"success": True, "vendor": vendor,
            "account_label": verified.get("account_label", "")}


def disconnect(client_id: int) -> dict:
    """Delete the row — credentials included. Never a status flip."""
    from agents.client_agent import remove_accounts
    removed = remove_accounts(client_id, [CRM_PLATFORM])
    if removed:
        _log_activity(client_id, "crm_disconnected", {}, {"rows_removed": removed})
    log_step(AGENT_NAME, "disconnect", f"client {client_id}: removed {removed} row(s)")
    return {"success": True, "removed": bool(removed)}


def get_status(client_id: int) -> dict:
    """What the dashboard card and the website persona both read. Always
    answers — an unconnected client gets the vendor list and nothing else."""
    connection = _connection(client_id)
    status = {
        "connected": bool(connection),
        "vendors": crm_service.supported_vendors(),
        "vendor": "", "vendor_label": "", "extra": "",
    }
    if connection:
        vendor, _, extra = _creds(connection)
        entry = crm_service.VENDORS.get(vendor) or {}
        status.update({"vendor": vendor,
                       "vendor_label": entry.get("label", vendor),
                       "extra": extra})
        status["recent"] = _recent_sync_summary(client_id)
    return status


def _recent_sync_summary(client_id: int) -> dict:
    """Last 30 days of push outcomes, so the card can show "12 נשלחו, 1 נכשל"
    rather than claiming everything is fine. `available: False` means the
    migration has not been run — reported, never guessed around."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    try:
        rows = (_db().table("client_leads").select("crm_sync_status")
                .eq("client_id", client_id).gte("created_at", since)
                .limit(1000).execute().data or [])
    except Exception:
        return {"available": False, "synced": 0, "failed": 0, "pending": 0}
    counts = {"synced": 0, "failed": 0, "pending": 0}
    for row in rows:
        state = row.get("crm_sync_status")
        if state in counts:
            counts[state] += 1
        elif state is None:
            counts["pending"] += 1
    return {"available": True, **counts}


# ─── Sync ─────────────────────────────────────────────────────────────────────

def _mark(lead_id: int, client_id: int, changes: dict) -> bool:
    """Record the outcome on the lead row. False when the columns do not exist
    (migration pending) — the CALLER decides whether that is worth alerting
    about, so a background push does not alert once per lead."""
    try:
        (_db().table("client_leads").update(changes)
         .eq("id", lead_id).eq("client_id", client_id).execute())
        return True
    except Exception as e:
        print(f"[{AGENT_NAME}] could not record sync status for lead {lead_id}: {e}")
        return False


def sync_lead(client_id: int, lead_id: int, lead: dict = None, attempt: int = 0) -> dict:
    """Push ONE lead to this client's CRM. THE entry point for both the
    capture-time background task and the retry pass.

    NEVER raises. Returns {"pushed": bool, "reason": str}. `reason="no_crm"` is
    the overwhelmingly common case and is not a problem: most clients have no
    CRM connected, which is exactly the opt-in design.
    """
    connection = _connection(client_id)
    if not connection:
        return {"pushed": False, "reason": "no_crm"}

    vendor, credential, extra = _creds(connection)
    if not vendor or not credential:
        return {"pushed": False, "reason": "no_crm"}

    if lead is None:
        lead = _load_lead(client_id, lead_id)
        if not lead:
            return {"pushed": False, "reason": "lead_not_found"}

    try:
        result = crm_service.push_lead(vendor, credential, extra, {
            "name": lead.get("name"), "email": lead.get("email"),
            "phone": lead.get("phone"), "message": lead.get("message"),
        })
    except CRMError as e:
        return _record_failure(client_id, lead_id, vendor, e, attempt)
    except Exception as e:
        return _record_failure(client_id, lead_id, vendor,
                               CRMError(str(e), vendor=vendor), attempt)

    recorded = _mark(lead_id, client_id, {
        "crm_sync_status": "synced",
        "crm_synced_at": datetime.now(timezone.utc).isoformat(),
        "crm_external_id": result.get("external_id", ""),
        "crm_error": "",
        "crm_attempts": attempt + 1,
    })
    log_step(AGENT_NAME, "sync_lead",
             f"client {client_id}: lead {lead_id} -> {vendor}"
             f"{'' if recorded else ' (status NOT recorded — migration pending)'}")
    return {"pushed": True, "reason": "", "vendor": vendor,
            "external_id": result.get("external_id", ""), "recorded": recorded}


def _record_failure(client_id: int, lead_id: int, vendor: str,
                    error: CRMError, attempt: int) -> dict:
    """A failed push is a note on the lead, not a lost lead. Alerts only when a
    human can actually do something: a dead credential (permanent) or a lead
    that has exhausted its retries."""
    attempts = attempt + 1
    recorded = _mark(lead_id, client_id, {
        "crm_sync_status": "failed",
        "crm_error": str(error)[:500],
        "crm_attempts": attempts,
    })
    exhausted = attempts >= MAX_SYNC_ATTEMPTS
    if error.permanent or exhausted:
        agent_alert(AGENT_NAME, [
            f"client {client_id}: lead {lead_id} could not be sent to {vendor} "
            f"({'credential/field rejected' if error.permanent else f'{attempts} attempts'}): "
            f"{str(error)[:200]}. The lead IS saved in uallak — only the CRM copy is missing."
            + ("" if recorded else
               " NOTE: crm_sync_status could not be written — run "
               "migrations/2026-08-03-crm-sync.sql, until then nothing is retried.")])
    return {"pushed": False, "reason": "error", "error": str(error)[:200],
            "permanent": error.permanent, "attempts": attempts, "recorded": recorded}


def _load_lead(client_id: int, lead_id: int) -> dict:
    try:
        rows = (_db().table("client_leads").select("*")
                .eq("id", lead_id).eq("client_id", client_id)
                .limit(1).execute().data or [])
    except Exception as e:
        print(f"[{AGENT_NAME}] lead {lead_id} lookup failed: {e}")
        return {}
    return rows[0] if rows else {}


def sync_lead_safe(client_id: int, lead_id: int) -> None:
    """The background-task entry point. Swallows EVERYTHING: this runs after the
    customer's form has already been answered, so there is no caller left to
    return an error to, and an exception here would only surface as noise in the
    Cloud Run log."""
    try:
        sync_lead(client_id, lead_id)
    except Exception as e:  # defence in depth; sync_lead already never raises
        print(f"[{AGENT_NAME}] background sync failed for lead {lead_id}: {e}")


# ─── Retry pass (scheduled) ───────────────────────────────────────────────────

def retry_failed_syncs() -> dict:
    """Re-push leads whose CRM sync failed. Cron: GET /api/crm/retry-syncs.

    Only touches rows already marked 'failed' — a lead that never had a status
    written (migration pending, or captured before this feature) is deliberately
    left alone rather than mass-pushed into a client's CRM by surprise."""
    since = (datetime.now(timezone.utc) - timedelta(days=RETRY_WINDOW_DAYS)).isoformat()
    try:
        rows = (_db().table("client_leads").select("*")
                .eq("crm_sync_status", "failed")
                .lt("crm_attempts", MAX_SYNC_ATTEMPTS)
                .gte("created_at", since)
                .order("created_at", desc=True)
                .limit(RETRY_BATCH).execute().data or [])
    except Exception as e:
        # Migration pending is the likely cause and is not an incident
        print(f"[{AGENT_NAME}] retry scan unavailable: {e}")
        return {"scanned": 0, "pushed": 0, "still_failing": 0, "available": False}

    summary = {"scanned": len(rows), "pushed": 0, "still_failing": 0, "available": True}
    log_step(AGENT_NAME, "retry_failed_syncs", f"{len(rows)} lead(s) to retry")
    for row in rows:
        result = sync_lead(row["client_id"], row["id"], lead=row,
                           attempt=int(row.get("crm_attempts") or 0))
        if result.get("pushed"):
            summary["pushed"] += 1
        else:
            summary["still_failing"] += 1
    log_step(AGENT_NAME, "retry_failed_syncs", f"done — {summary}")
    return summary
