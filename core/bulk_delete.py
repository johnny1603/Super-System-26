"""Admin bulk delete — one batch of selected rows, one confirmation, one audit
entry, and a per-item result.

## Speed (2026-10-19)

Previews are batched (one query per table for the whole selection, no
provider calls) and deletes run in small chunks, one request each — see
CHUNK_MAX. No request does work proportional to the whole selection.

## The contract every kind follows

- The server re-validates EVERYTHING: the ids, the cap (BULK_DELETE_MAX,
  default 50), the typed phrase ("מחק N" with the exact count), protection.
- Items are processed ONE AT A TIME through the single-item logic; one
  failure never stops the batch. Each item comes back deleted / failed /
  skipped with a plain reason code.
- Safe to run twice: an item that is already gone is "skipped: not_found".
- ONE audit entry per batch (alerts table): kind, who (admin), when, the ids
  and the counts. Names only for clients (as the single delete does); never
  other personal data.
- External resources are never touched (clients reuse client_deletion's
  "left for you to handle" list).

## Kinds

  clients       core/client_deletion.delete_client_full, per client
  leads         uallak's own sales funnel rows (+ their transcript); a lead
                LINKED to a client is protected — it goes with the client
  client_leads  one client's own leads (their customers), via
                client_leads.delete_lead (scoped to that client)
  alerts        admin alerts; the deletion AUDIT entries themselves are
                protected (deleting the record of a deletion defeats it)
"""
import os
from datetime import datetime, timezone

SERVICE_NAME = "bulk_delete"
BULK_DELETE_MAX = int(os.environ.get("BULK_DELETE_MAX", "50"))
KINDS = ("clients", "leads", "client_leads", "alerts")
AUDIT_SOURCES = ("client_deleted", "bulk_delete")


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def confirm_phrase(n: int) -> str:
    return f"מחק {int(n)}"


def _clean_ids(kind: str, ids) -> list:
    """De-duplicated, typed ids, order kept. leads use the text lead_id."""
    out, seen = [], set()
    for raw in ids or []:
        value = str(raw).strip() if kind == "leads" else raw
        try:
            value = value if kind == "leads" else int(value)
        except (TypeError, ValueError):
            continue
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def validate(kind: str, ids, confirm: str = None, client_id: int = None) -> list:
    """Raises ValueError(code) for a refusal; returns the clean ids."""
    if kind not in KINDS:
        raise ValueError("unknown_kind")
    if kind == "client_leads" and not client_id:
        raise ValueError("client_required")
    clean = _clean_ids(kind, ids)
    if not clean:
        raise ValueError("empty_selection")
    if len(clean) > BULK_DELETE_MAX:
        raise ValueError("too_many")
    if confirm is not None and (confirm or "").strip() != confirm_phrase(len(clean)):
        raise ValueError("confirm_mismatch")
    return clean


# ─── Previews (change nothing) ───────────────────────────────────────────────

def preview(kind: str, ids, subscription_info=None, client_id: int = None) -> dict:
    clean = validate(kind, ids, client_id=client_id)
    items = []
    if kind == "clients":
        # Batched reads for the whole selection, no provider calls (the
        # per-client "בדוק" uses the single preview with bounded checks)
        from core.client_deletion import batch_preview
        items = batch_preview(clean)
    elif kind == "leads":
        rows = (_db().table("leads").select("lead_id,client_id,client_name,client_email,status")
                .in_("lead_id", clean).execute().data or [])
        by_id = {r["lead_id"]: r for r in rows}
        for lid in clean:
            r = by_id.get(lid)
            if not r:
                items.append({"id": lid, "found": False})
                continue
            msgs = _count("lead_messages", "lead_id", lid)
            items.append({"id": lid, "found": True,
                          "name": r.get("client_name") or r.get("client_email") or lid,
                          "protected": bool(r.get("client_id")),
                          "linked_client_id": r.get("client_id"),
                          "counts": {"leads": 1, "lead_messages": msgs}})
    elif kind == "client_leads":
        rows = (_db().table("client_leads").select("id,client_id,name,phone,email")
                .eq("client_id", int(client_id or 0)).in_("id", clean).execute().data or [])
        by_id = {r["id"]: r for r in rows}
        for lid in clean:
            r = by_id.get(lid)
            items.append({"id": lid, "found": bool(r), "protected": False,
                          "name": (r.get("name") or r.get("phone") or r.get("email") or f"#{lid}") if r else "",
                          "counts": {"client_leads": 1} if r else {}})
    elif kind == "alerts":
        rows = (_db().table("alerts").select("id,source,status").in_("id", clean).execute().data or [])
        by_id = {r["id"]: r for r in rows}
        for aid in clean:
            r = by_id.get(aid)
            items.append({"id": aid, "found": bool(r),
                          "protected": bool(r) and r.get("source") in AUDIT_SOURCES,
                          "name": f"#{aid} · {r.get('source') or ''}" if r else "",
                          "counts": {"alerts": 1} if r else {}})

    totals = {}
    for item in items:
        if item.get("found") and not item.get("protected"):
            for table, n in (item.get("counts") or {}).items():
                if isinstance(n, int) and n > 0:
                    totals[table] = totals.get(table, 0) + n
    deletable = [i for i in items if i.get("found") and not i.get("protected")]
    return {"kind": kind, "items": items, "totals": totals,
            "deletable": len(deletable), "confirm_phrase": confirm_phrase(len(clean)),
            "max": BULK_DELETE_MAX}


def _count(table: str, column: str, value) -> int:
    try:
        return (_db().table(table).select("id", count="exact").eq(column, value)
                .limit(1).execute().count or 0)
    except Exception:
        return 0


# ─── Single-item deletes the batch reuses ────────────────────────────────────

def _delete_sales_lead(lead_id: str) -> dict:
    """One uallak sales-funnel lead + its transcript. A lead linked to a
    client is skipped (it belongs to that client's record). Its pre-signup
    cost rows are DETACHED from the lead (lead_id cleared, kept as anonymous
    cost) so cost totals don't move."""
    rows = (_db().table("leads").select("lead_id,client_id").eq("lead_id", lead_id)
            .limit(1).execute().data or [])
    if not rows:
        return {"status": "skipped", "reason": "not_found"}
    if rows[0].get("client_id"):
        return {"status": "skipped", "reason": "linked_to_client"}
    try:
        _db().table("client_costs").update({"lead_id": None, "attribution": "deleted_lead"}) \
            .eq("lead_id", lead_id).is_("client_id", "null").execute()
    except Exception:
        pass  # lead_id column not migrated: no cost row carries a lead id
    _db().table("lead_messages").delete().eq("lead_id", lead_id).execute()
    _db().table("leads").delete().eq("lead_id", lead_id).execute()
    return {"status": "deleted"}


def _delete_alert(alert_id: int) -> dict:
    rows = (_db().table("alerts").select("id,source").eq("id", alert_id).limit(1).execute().data or [])
    if not rows:
        return {"status": "skipped", "reason": "not_found"}
    if rows[0].get("source") in AUDIT_SOURCES:
        return {"status": "skipped", "reason": "audit_entry"}
    _db().table("alerts").delete().eq("id", alert_id).execute()
    return {"status": "deleted"}


def _delete_client_lead(client_id: int, lead_id: int) -> dict:
    from core import client_leads
    return ({"status": "deleted"} if client_leads.delete_lead(int(client_id), int(lead_id))
            else {"status": "skipped", "reason": "not_found"})


# ─── The batch ───────────────────────────────────────────────────────────────

# ─── Chunked batches (2026-10-19) ────────────────────────────────────────────
# A batch is deleted in CHUNKS, one request each, so no single request can
# approach Cloud Run's 300 s request timeout (a client delete revokes tokens
# at the providers and touches ~20 tables). The FULL batch is sent with every
# chunk and re-validated each time (cap, phrase "מחק N" for the whole batch,
# chunk ⊆ batch); progress lives in app_settings "bulk_delete:<batch_id>", so
# a closed window resumes where it stopped and a repeated chunk is a no-op.
CHUNK_MAX = {"clients": 5, "leads": 25, "client_leads": 25, "alerts": 50}
PROGRESS_PREFIX = "bulk_delete:"


def _batch_key(batch_id: str) -> str:
    import re
    if not re.fullmatch(r"[A-Za-z0-9\-]{8,64}", batch_id or ""):
        raise ValueError("bad_batch_id")
    return f"{PROGRESS_PREFIX}{batch_id}"


def batch_status(batch_id: str) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", _batch_key(batch_id))
            .limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _save_batch(batch_id: str, record: dict):
    _db().table("app_settings").upsert(
        {"key": _batch_key(batch_id), "value": record,
         "updated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="key").execute()


def run(kind: str, ids, confirm: str, batch_id: str, batch_ids, skip_backup: bool = False,
        client_id: int = None, disconnect=None, disconnect_groups=None, subscription_info=None) -> dict:
    """Delete ONE chunk of a batch. Returns the chunk's per-item results and
    the batch progress; the batch's single audit entry is written when the
    last item is done."""
    batch = validate(kind, batch_ids, confirm, client_id=client_id)
    chunk = _clean_ids(kind, ids)
    if not chunk:
        raise ValueError("empty_selection")
    if len(chunk) > CHUNK_MAX[kind]:
        raise ValueError("chunk_too_large")
    if any(item not in batch for item in chunk):
        raise ValueError("not_in_batch")

    record = batch_status(batch_id)
    if record and (record.get("kind") != kind or record.get("ids") != batch):
        raise ValueError("batch_mismatch")
    if not record:
        record = {"kind": kind, "ids": batch, "client_id": client_id or None, "results": {},
                  "started_at": datetime.now(timezone.utc).isoformat()}

    results = []
    for item_id in chunk:
        done = record["results"].get(str(item_id))
        if done and done.get("status") in ("deleted", "skipped"):
            results.append({"id": item_id, **done, "repeat": True})   # already handled
            continue
        try:
            if kind == "clients":
                outcome = _run_client(item_id, skip_backup, disconnect, disconnect_groups, subscription_info)
            elif kind == "leads":
                outcome = _delete_sales_lead(item_id)
            elif kind == "client_leads":
                outcome = _delete_client_lead(client_id, item_id)
            else:
                outcome = _delete_alert(item_id)
        except Exception as e:
            outcome = {"status": "failed", "reason": type(e).__name__}
        record["results"][str(item_id)] = {k: v for k, v in outcome.items() if k in ("status", "reason")}
        results.append({"id": item_id, **outcome})

    handled = len(record["results"])
    finished = handled >= len(batch)
    if finished and not record.get("audited"):
        summary = {s: [i for i in batch if record["results"].get(str(i), {}).get("status") == s]
                   for s in ("deleted", "failed", "skipped")}
        _audit(kind, batch, summary, client_id)
        record["audited"] = True
    _save_batch(batch_id, record)
    return {"kind": kind, "results": results, "progress": {"done": handled, "total": len(batch)},
            "finished": finished}


def _run_client(client_id: int, skip_backup: bool, disconnect, groups, subscription_info) -> dict:
    """Reuses delete_client_full unchanged. The batch phrase ("מחק N") was
    already checked, so the per-client confirmation is its own id; every other
    refusal (protected, no fresh backup) is re-checked inside."""
    from core.client_deletion import delete_client_full
    try:
        out = delete_client_full(client_id, str(client_id), skip_backup,
                                 disconnect=disconnect, disconnect_groups=groups,
                                 subscription_info=subscription_info)
    except ValueError as e:
        reason = str(e)
        if reason == "not_found":
            return {"status": "skipped", "reason": "not_found"}
        if reason.startswith("hard_stop:"):
            return {"status": "skipped", "reason": "protected"}
        return {"status": "skipped", "reason": reason}
    if out.get("success"):
        return {"status": "deleted", "removed": out.get("removed"), "not_removed": out.get("not_removed"),
                "revoke_failed": out.get("revoke_failed")}
    return {"status": "failed", "reason": "partial",
            "failed_steps": [f["step"] for f in out.get("failed", [])],
            "not_removed": out.get("not_removed")}


def _audit(kind: str, ids: list, summary: dict, client_id: int = None):
    """One entry per batch: kind, who, when, ids, counts — ids only (each
    deleted client also gets its own client_deleted entry with its name,
    written by delete_client_full). Never other personal data."""
    from agents.master_agent import alert
    scope = f" of client {client_id}" if kind == "client_leads" and client_id else ""
    alert("bulk_delete", [
        f"bulk delete of {kind}{scope} by admin at {datetime.now(timezone.utc).isoformat()}: "
        f"requested {len(ids)} {ids} — deleted {summary['deleted']}, "
        f"failed {summary['failed']}, skipped {summary['skipped']}"])
