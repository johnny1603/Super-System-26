"""Admin "delete client" — full removal of one client and everything tied to
their client_id, behind a preview + typed confirmation (owner only).

## What this is NOT

Not the client's own closure/transfer (api_server._offboard_client): that one
keeps a PII-stripped tombstone and a Drive archive. This removes the client
row itself. It is admin-only and refuses, SERVER-SIDE, internal
(billing_exempt) accounts and the owner's own accounts.

## Order (no cross-table transaction exists over the Supabase REST API, so
## every step is idempotent and progress is recorded — re-running a failed
## deletion resumes it)

 1. connections   revoke at the platform where the disconnect code supports it
                  (api_server._disconnect_platform), then delete every
                  client_accounts row — the encrypted secrets go with the rows
 2. costs         client_costs rows (and pre-signup rows of their sales leads)
                  are folded into ANONYMOUS monthly aggregates (month,
                  category, metering bucket, payer, amount — no client id, no
                  lead id, no details), then the originals are deleted, so
                  historical totals don't move
 3. data tables   DATA_TABLES below, deleted by client_id
 4. sales leads   leads rows with this client_id + their lead_messages
 5. settings      app_settings "client_settings:<id>"
 6. client row    clients — LAST, so a failed run can always be resumed
 7. caches        in-memory caches in this process
 8. audit         one alert row: who, when, id, name, counts (no other PII)

External resources (hosted site, domains, Drive folders, ad accounts and
running campaigns, PayPal billing) are NEVER touched — the preview lists
them and the owner handles each one separately.
"""
import os
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "client_deletion"
PROGRESS_PREFIX = "client_deletion:"
BACKUP_VALID_HOURS = 6

# Tables deleted by client_id in step 3. OPTIONAL = created by a migration
# that may not have run; a missing optional table is "skipped", not a failure.
DATA_TABLES = (
    ("client_activity", False),
    ("client_communications", False),
    ("client_suggestions", False),
    ("client_agents", False),
    ("client_leads", True),
    ("client_feedback", True),
    ("client_lead_volume", True),
    ("landing_pages", True),
    ("admin_agent_chat", True),
    ("client_target_keywords", True),
    ("login_codes", False),
)


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Who may never be deleted here ───────────────────────────────────────────

def _owner_emails() -> set:
    """The owner's own addresses: OWNER_EMAILS (comma list) plus the admin/
    sender addresses the app already knows."""
    emails = {e.strip().lower() for e in os.environ.get("OWNER_EMAILS", "").split(",") if e.strip()}
    try:
        from core.email_service import ADMIN_EMAIL, GMAIL_USER
        emails |= {e.strip().lower() for e in (ADMIN_EMAIL, GMAIL_USER) if e}
    except Exception:
        pass
    return emails


def hard_stops(client: dict) -> list:
    """Reasons this client can NOT be deleted from the admin screen at all."""
    stops = []
    if client.get("billing_exempt"):
        stops.append("internal")
    if (client.get("email") or "").strip().lower() in _owner_emails():
        stops.append("owner")
    return stops


# ─── Counts ──────────────────────────────────────────────────────────────────

def _count(table: str, column: str, value, select: str = "id") -> int:
    try:
        res = (_db().table(table).select(select, count="exact").eq(column, value)
               .limit(1).execute())
        return res.count or 0
    except Exception:
        return -1  # table unavailable (migration not run) — shown as "—"


def _lead_ids(client_id: int) -> list:
    try:
        rows = (_db().table("leads").select("lead_id").eq("client_id", client_id)
                .limit(1000).execute().data or [])
        return [r["lead_id"] for r in rows if r.get("lead_id")]
    except Exception:
        return []


def counts(client_id: int) -> dict:
    out = {"client_accounts": _count("client_accounts", "client_id", client_id),
           "client_costs": _count("client_costs", "client_id", client_id)}
    for table, _optional in DATA_TABLES:
        out[table] = _count(table, "client_id", client_id)
    out["leads"] = _count("leads", "client_id", client_id)
    lead_ids = _lead_ids(client_id)
    try:
        out["lead_messages"] = ((_db().table("lead_messages").select("id", count="exact")
                                 .in_("lead_id", lead_ids).limit(1).execute().count or 0)
                                if lead_ids else 0)
    except Exception:
        out["lead_messages"] = -1
    out["app_settings"] = _count("app_settings", "key", f"client_settings:{int(client_id)}", select="key")
    return out


# ─── Preview: warnings the owner reads before deciding ───────────────────────

def _activity(client_id: int, limit: int = 500) -> list:
    from agents.client_agent import get_activity
    try:
        return get_activity(client_id, limit=limit)
    except Exception:
        return []


def _unpaid_invoices(activity: list) -> list:
    """invoice_sent rows with no later invoice_paid / invoice_cancelled."""
    settled, unpaid = set(), []
    for e in activity:   # newest first
        if e.get("agent_name") != "paypal_service":
            continue
        number = (e.get("details") or {}).get("invoice_number")
        if e.get("action_type") in ("invoice_paid", "invoice_cancelled"):
            settled.add(number)
        elif e.get("action_type") == "invoice_sent" and number and number not in settled:
            unpaid.append(number)
    return unpaid


def preview(client_id: int, subscription_info) -> dict:
    """Everything the confirmation modal shows. Changes NOTHING.
    subscription_info: api_server._client_subscription_info (passed in to
    avoid importing the app module)."""
    from agents.client_agent import get_accounts, get_client
    client = get_client(client_id)
    if not client:
        return {"found": False}

    accounts = get_accounts(client_id)
    active = [a for a in accounts if a.get("status") == "active"]
    activity = _activity(client_id)
    warnings = []

    # 1. Connections
    if accounts:
        warnings.append({"code": "connections",
                         "items": sorted({a.get("platform") for a in accounts})})

    # 2. Billing
    billing = []
    sub = {}
    try:
        sub = subscription_info(client_id) or {}
    except Exception:
        pass
    if sub.get("subscription_id"):
        status = "UNKNOWN"
        try:
            from core.paypal_service import get_subscription_status
            status = get_subscription_status(sub["subscription_id"]).get("status") or "UNKNOWN"
        except Exception:
            pass
        billing.append({"kind": "subscription", "id": sub["subscription_id"], "status": status,
                        "monthly_fee": sub.get("monthly_fee")})
    for number in _unpaid_invoices(activity):
        billing.append({"kind": "unpaid_invoice", "id": number})
    if billing:
        warnings.append({"code": "billing", "items": billing})

    # 3. External resources deletion does NOT remove
    external = []
    by_platform = {}
    for a in active:
        by_platform.setdefault(a.get("platform"), []).append(a)
    for a in by_platform.get("wordpress", []):
        external.append({"kind": "website", "ref": a.get("account_id")})
    if any(e.get("action_type") == "website_provisioned" for e in activity):
        external.append({"kind": "hosted_site", "ref": "InstaWP"})
    for a in [x for x in accounts if x.get("platform") == "landing_domain"]:
        external.append({"kind": "domain", "ref": a.get("account_id")})
    for e in activity:
        if e.get("action_type") == "media_folder_created":
            external.append({"kind": "drive_folder", "ref": (e.get("details") or {}).get("folder_id")})
    for platform, kind in (("google_ads", "ad_account_google"), ("meta_ads", "ad_account_meta"),
                           ("youtube", "youtube_channel"), ("tiktok", "tiktok_account"),
                           ("merchant_center", "merchant_center"), ("google_tagmanager", "gtm_container")):
        for a in by_platform.get(platform, []):
            external.append({"kind": kind, "ref": a.get("account_id")})
    running = 0
    for platform, module in (("google_ads", "agents.google_ads_agent"), ("meta_ads", "agents.meta_ads_agent")):
        if platform not in by_platform:
            continue
        try:
            import importlib
            perf = importlib.import_module(module).get_campaign_performance(client_id)
            running += sum(1 for c in perf.get("campaigns", [])
                           if str(c.get("status", "")).upper() in ("ENABLED", "ACTIVE"))
        except Exception:
            pass
    if running:
        external.append({"kind": "running_campaigns", "ref": str(running)})
    if external:
        warnings.append({"code": "external", "items": external})

    # 4. Recent activity, the client's customers' data, undelivered work
    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent = sum(1 for e in activity if (e.get("created_at") or "") >= month_ago)
    if recent:
        warnings.append({"code": "recent_activity", "items": [str(recent)]})
    c = counts(client_id)
    if c.get("client_leads", 0) > 0:
        warnings.append({"code": "client_leads", "items": [str(c["client_leads"])]})
    pending = _count_where("client_suggestions", client_id, "status", "pending")
    if pending > 0:
        warnings.append({"code": "undelivered", "items": [str(pending)]})
    if c.get("landing_pages", 0) > 0:
        warnings.append({"code": "landing_pages", "items": [str(c["landing_pages"])]})
    unreviewed = _count_where("client_feedback", client_id, "reviewed", False)
    if unreviewed > 0:
        warnings.append({"code": "feedback", "items": [str(unreviewed)]})

    return {
        "found": True,
        "client": {"id": client["id"], "name": client.get("name") or "",
                   "email": client.get("email") or "", "status": client.get("status") or ""},
        "hard_stops": hard_stops(client),
        "warnings": warnings,
        "counts": c,
        "backup_downloaded_at": _progress(client_id).get("backup_at"),
    }


def _count_where(table: str, client_id: int, column: str, value) -> int:
    try:
        return (_db().table(table).select("id", count="exact").eq("client_id", client_id)
                .eq(column, value).limit(1).execute().count or 0)
    except Exception:
        return 0


# ─── Backup (everything except secrets) ──────────────────────────────────────

def extra_backup(client_id: int) -> dict:
    """The tables api_server._build_client_export does not already carry.
    Secrets never appear: connections come from get_accounts (credential-
    free) in the main export, and every string here is redacted."""
    from core.redaction import redact_obj
    out = {}
    for table in ("client_suggestions", "client_feedback", "landing_pages", "client_lead_volume",
                  "client_target_keywords", "client_agents"):
        try:
            out[table] = (_db().table(table).select("*").eq("client_id", client_id)
                          .limit(5000).execute().data or [])
        except Exception:
            out[table] = None   # table unavailable
    try:
        out["sales_leads"] = (_db().table("leads").select("lead_id,created_at,status,answers,proposal,"
                                                          "setup_fee,monthly_fee,converted_at")
                              .eq("client_id", client_id).limit(100).execute().data or [])
    except Exception:
        out["sales_leads"] = None
    lead_ids = _lead_ids(client_id)
    try:
        out["sales_chat_messages"] = ((_db().table("lead_messages").select("lead_id,role,content,created_at")
                                       .in_("lead_id", lead_ids).limit(5000).execute().data or [])
                                      if lead_ids else [])
    except Exception:
        out["sales_chat_messages"] = None
    try:
        from core.cost_tracker import client_cost_view
        out["costs_summary"] = client_cost_view(client_id)
    except Exception:
        out["costs_summary"] = None
    return redact_obj(out)


def mark_backup(client_id: int):
    _save_progress(client_id, {**_progress(client_id), "backup_at": _now()})


def _backup_is_fresh(client_id: int) -> bool:
    at = _progress(client_id).get("backup_at")
    if not at:
        return False
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - when < timedelta(hours=BACKUP_VALID_HOURS)


# ─── Progress (resumability) ─────────────────────────────────────────────────

def _progress(client_id: int) -> dict:
    try:
        rows = (_db().table("app_settings").select("value")
                .eq("key", f"{PROGRESS_PREFIX}{int(client_id)}").limit(1).execute().data or [])
        value = rows[0].get("value") if rows else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_progress(client_id: int, value: dict):
    _db().table("app_settings").upsert(
        {"key": f"{PROGRESS_PREFIX}{int(client_id)}", "value": value, "updated_at": _now()},
        on_conflict="key").execute()


# ─── Step 2: costs -> anonymous monthly aggregates ───────────────────────────

def _anonymize_costs(client_id: int, progress: dict) -> dict:
    """Fold this client's cost rows (and the pre-signup rows of their sales
    leads) into rows with NO client id, NO lead id and NO details beyond the
    aggregate itself, then delete the originals. Totals per month/category
    are unchanged. Resumable: the exact row ids are recorded before the
    aggregate insert, so a re-run never aggregates the same rows twice."""
    db = _db()
    ids = progress.get("cost_row_ids")
    if ids is None:
        rows = (db.table("client_costs").select("*").eq("client_id", client_id)
                .limit(50000).execute().data or [])
        lead_ids = _lead_ids(client_id)
        if lead_ids:
            try:
                rows += (db.table("client_costs").select("*").in_("lead_id", lead_ids)
                         .is_("client_id", "null").limit(50000).execute().data or [])
            except Exception:
                pass  # lead_id column not migrated — nothing attributed by lead
        ids = sorted({r["id"] for r in rows})
        aggregates = {}
        for r in rows:
            details = r.get("details") or {}
            key = ((r.get("created_at") or "")[:7], r.get("category") or "other",
                   r.get("cost_category") or details.get("cost_category") or "",
                   details.get("payer") or "")
            agg = aggregates.setdefault(key, {"amount": 0.0, "usd": 0.0, "rows": 0})
            agg["amount"] += float(r.get("amount") or 0)
            agg["usd"] += float(details.get("usd") if details.get("usd") is not None
                                else (r.get("amount") or 0) / 3.40)
            agg["rows"] += 1
        progress = {**progress, "cost_row_ids": ids, "cost_aggregates": [
            {"month": k[0], "category": k[1], "cost_category": k[2], "payer": k[3],
             "amount": round(v["amount"], 4), "usd": round(v["usd"], 6), "rows": v["rows"]}
            for k, v in aggregates.items()]}
        _save_progress(client_id, progress)

    if not progress.get("costs_aggregated"):
        for agg in progress.get("cost_aggregates", []):
            month = agg["month"] or datetime.now(timezone.utc).strftime("%Y-%m")
            base = {"client_id": None, "category": agg["category"], "amount": agg["amount"],
                    "created_at": f"{month}-01T00:00:00+00:00",
                    "details": {"aggregate": True, "deleted_client": True, "rows": agg["rows"],
                                "usd": agg["usd"], "payer": agg["payer"] or None,
                                "cost_category": agg["cost_category"] or None}}
            try:
                db.table("client_costs").insert({**base, "attribution": "deleted_client",
                                                 "cost_category": agg["cost_category"] or None}).execute()
            except Exception:
                db.table("client_costs").insert(base).execute()  # attribution columns not migrated
        progress = {**progress, "costs_aggregated": True}
        _save_progress(client_id, progress)

    for start in range(0, len(ids), 200):
        chunk = ids[start:start + 200]
        if chunk:
            db.table("client_costs").delete().in_("id", chunk).execute()
    return {**progress, "costs_done": True}


# ─── The deletion ────────────────────────────────────────────────────────────

def confirm_matches(client: dict, confirm: str) -> bool:
    typed = (confirm or "").strip()
    return bool(typed) and typed in {(client.get("name") or "").strip(), str(client.get("id"))}


def _drop_caches(client_id: int):
    import importlib
    for module, names in (("agents.google_ads_agent", ("_perf_cache", "_daily_cache", "_accounts_cache")),
                          ("agents.meta_ads_agent", ("_perf_cache", "_daily_cache")),
                          ("agents.website_agent", ("_overview_cache",)),
                          ("agents.seo_agent", ("_audit_cache",)),
                          ("agents.media_agent", ("_folder_cache",))):
        try:
            mod = importlib.import_module(module)
            for name in names:
                getattr(mod, name, {}).pop(client_id, None)
        except Exception:
            pass
    for module, name in (("core.performance", "_local_cache"), ("core.client_knowledge", "_cache")):
        try:
            cache = getattr(importlib.import_module(module), name, {})
            for key in [k for k in list(cache) if isinstance(k, tuple) and client_id in k]:
                cache.pop(key, None)
        except Exception:
            pass


def delete_client_full(client_id: int, confirm: str, skip_backup: bool,
                       disconnect, disconnect_groups: dict, subscription_info) -> dict:
    """THE deletion. Returns {"success", "removed": {table: n}, "revoke": [...],
    "failed": [...], "not_removed": [...external...]}. Raises ValueError for a
    refusal (unknown client, hard stop, bad confirmation, no backup)."""
    from agents.client_agent import get_client, remove_accounts
    from agents.master_agent import alert

    client = get_client(client_id)
    if not client:
        raise ValueError("not_found")
    stops = hard_stops(client)
    if stops:
        raise ValueError("hard_stop:" + ",".join(stops))
    if not confirm_matches(client, confirm):
        raise ValueError("confirm_mismatch")
    if not skip_backup and not _backup_is_fresh(client_id):
        raise ValueError("backup_required")

    name = client.get("name") or ""
    external = (preview(client_id, subscription_info).get("warnings") or [])
    external = next((w["items"] for w in external if w["code"] == "external"), [])
    before = counts(client_id)
    progress = {**_progress(client_id), "started_at": _progress(client_id).get("started_at") or _now()}
    _save_progress(client_id, progress)
    result = {"success": False, "removed": {}, "revoke": [], "failed": [], "not_removed": external}

    # 1. Connections: revoke where supported, then delete every row (secrets included)
    for platform in disconnect_groups:
        try:
            outcome = disconnect(client_id, platform)
            if outcome.get("removed"):
                result["revoke"].append({"platform": platform,
                                         "revoked": outcome.get("revoked")})
        except Exception as e:
            result["revoke"].append({"platform": platform, "revoked": False,
                                     "error": type(e).__name__})
    try:
        remove_accounts(client_id)   # catch-all: CRM, domains, automation, anything else
        result["removed"]["client_accounts"] = before.get("client_accounts", 0)
    except Exception as e:
        result["failed"].append({"step": "client_accounts", "error": type(e).__name__})

    # 2. Costs -> anonymous aggregates
    if not progress.get("costs_done"):
        try:
            progress = _anonymize_costs(client_id, progress)
            _save_progress(client_id, progress)
            result["removed"]["client_costs"] = before.get("client_costs", 0)
        except Exception as e:
            result["failed"].append({"step": "client_costs", "error": type(e).__name__})

    # 3. Data tables
    for table, optional in DATA_TABLES:
        if before.get(table) == -1 and optional:
            continue   # table not migrated — nothing to delete
        try:
            _db().table(table).delete().eq("client_id", client_id).execute()
            result["removed"][table] = max(before.get(table, 0), 0)
        except Exception as e:
            if not optional:
                result["failed"].append({"step": table, "error": type(e).__name__})

    # 4. Sales leads + their transcripts
    lead_ids = _lead_ids(client_id)
    try:
        if lead_ids:
            _db().table("lead_messages").delete().in_("lead_id", lead_ids).execute()
        result["removed"]["lead_messages"] = max(before.get("lead_messages", 0), 0)
    except Exception as e:
        result["failed"].append({"step": "lead_messages", "error": type(e).__name__})
    try:
        _db().table("leads").delete().eq("client_id", client_id).execute()
        result["removed"]["leads"] = max(before.get("leads", 0), 0)
    except Exception as e:
        result["failed"].append({"step": "leads", "error": type(e).__name__})

    # 5. Per-client settings
    try:
        _db().table("app_settings").delete().eq("key", f"client_settings:{int(client_id)}").execute()
        result["removed"]["app_settings"] = max(before.get("app_settings", 0), 0)
    except Exception as e:
        result["failed"].append({"step": "app_settings", "error": type(e).__name__})

    # 6. The client row — only when everything before it worked, so a failed
    # run stays findable and resumable from the admin list
    if not result["failed"]:
        try:
            _db().table("clients").delete().eq("id", client_id).execute()
            result["removed"]["clients"] = 1
        except Exception as e:
            result["failed"].append({"step": "clients", "error": type(e).__name__})

    # 7. Caches in this process
    _drop_caches(client_id)

    result["success"] = not result["failed"]
    if result["success"]:
        try:
            _db().table("app_settings").delete().eq("key", f"{PROGRESS_PREFIX}{int(client_id)}").execute()
        except Exception:
            pass

    # 8. Audit — id + name + counts only
    # revoked None = nothing to revoke at that platform (our copy deleted is
    # the whole disconnect); only an explicit failure or an error is flagged
    revoke_failed = [r["platform"] for r in result["revoke"]
                     if r.get("revoked") is False or r.get("error")]
    result["revoke_failed"] = revoke_failed
    alert("client_deleted", [
        f"client {client_id} ({name}) {'DELETED' if result['success'] else 'PARTIALLY deleted'} "
        f"by admin at {_now()}: removed {result['removed']}"
        + (f"; revoke not confirmed for {revoke_failed}" if revoke_failed else "")
        + (f"; FAILED steps {[f['step'] for f in result['failed']]} — re-run to resume"
           if result["failed"] else "")
        + (f"; external resources NOT removed: {len(external)}" if external else "")])
    return result
