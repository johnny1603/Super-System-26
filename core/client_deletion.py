"""Admin "delete client" — full removal of one client and everything tied to
their client_id, behind a preview + typed confirmation (owner only).

## What this is NOT

Not the client's own closure/transfer (api_server._offboard_client): that one
keeps a PII-stripped tombstone and a Drive archive. This removes the client
row itself. It is admin-only and refuses, SERVER-SIDE, internal
(billing_exempt) accounts and the owner's own accounts.

## Order

See delete_clients() — the one implementation behind the single delete and
every bulk chunk. In short: lock → mark "deleting" (core/deletion_guard
refuses new rows) → revoke every provider grant (parallel, 5 s + one retry)
→ delete connection rows → costs into ANONYMOUS monthly aggregates (month,
category, metering bucket, payer, amount — no client id, no lead id, no
details) so historical totals don't move → every client_id table found in
the SCHEMA (client_tables) → sales leads + transcripts → settings → verify
every table, delete leftovers once more, verify again → the clients row
LAST → audit. No cross-table transaction exists over the Supabase REST API,
so every step is idempotent and progress is recorded — a re-run resumes.

## Speed (2026-10-19)

The work is the same; the round trips are fewer. Each step is ONE statement
per table for the whole chunk (`client_id in (...)`), independent tables run
TABLE_WORKERS at a time, deletes ask for no rows back, the revokes all run
at once, and the preview snapshot is read while they do. Every step is timed
— one "[client_delete_timing] id=N total=… steps: …" log line per client.

External resources (hosted site, domains, Drive folders, ad accounts and
running campaigns, PayPal billing) are NEVER touched — the preview lists
them and the owner handles each one separately.
"""
import os
import threading
from datetime import datetime, timedelta, timezone

from core import google_data_purge

SERVICE_NAME = "client_deletion"
PROGRESS_PREFIX = "client_deletion:"
BACKUP_VALID_HOURS = 6

# The KNOWN client_id tables (beyond client_accounts / client_costs / leads).
# The deletion clears every client_id table the schema reports (client_tables)
# PLUS these. OPTIONAL = created by a migration that may not have run; a
# missing optional table is "skipped", not a failure — but only when the
# database says it doesn't exist, never because a preview counted 0 rows.
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


# ─── External checks: always bounded ─────────────────────────────────────────
# PayPal (subscription status) and the ad platforms (running campaigns) are
# HTTP calls to other companies. Each runs with its own EXTERNAL_CHECK_TIMEOUT;
# a slow or failing provider becomes "not_checked", never a hung preview. The
# BULK preview skips them entirely (the owner can "בדוק" one client).
EXTERNAL_CHECK_TIMEOUT = 5
_checks_pool = None


def _bounded(fn, timeout: float = EXTERNAL_CHECK_TIMEOUT):
    """(ok, value). The worker thread may finish later; we stop waiting."""
    global _checks_pool
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
    if _checks_pool is None:
        _checks_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="delete-preview")
    future = _checks_pool.submit(fn)
    try:
        return True, future.result(timeout=timeout)
    except FutureTimeout:
        return False, "timeout"
    except Exception:
        return False, "error"


def _subscription_from_activity(activity: list) -> dict:
    """Same rule as api_server._client_subscription_info, from rows already
    read: the newest subscription_created, unless a newer cancel stops it."""
    for e in activity:   # newest first
        if e.get("agent_name") != "paypal_service":
            continue
        if e.get("action_type") == "subscription_cancelled":
            return {}
        if e.get("action_type") == "subscription_created":
            details = e.get("details") or {}
            return {"subscription_id": (e.get("result") or {}).get("subscription_id"),
                    "monthly_fee": details.get("monthly_management_total") or 0}
    return {}


def _build_warnings(accounts: list, activity: list, recent: int, c: dict, pending: int,
                    unreviewed: int, sub: dict, paypal_status: str, running) -> list:
    """The warning list, from data already read. paypal_status / running
    are "not_checked" when the external check was skipped or timed out."""
    active = [a for a in accounts if a.get("status") == "active"]
    warnings = []
    if accounts:
        warnings.append({"code": "connections", "items": sorted({a.get("platform") for a in accounts})})

    billing = []
    if sub.get("subscription_id"):
        billing.append({"kind": "subscription", "id": sub["subscription_id"], "status": paypal_status,
                        "monthly_fee": sub.get("monthly_fee")})
    for number in _unpaid_invoices(activity):
        billing.append({"kind": "unpaid_invoice", "id": number})
    if billing:
        warnings.append({"code": "billing", "items": billing})

    external, by_platform = [], {}
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
    if by_platform.get("google_ads") or by_platform.get("meta_ads"):
        if running == "not_checked":
            external.append({"kind": "running_campaigns", "ref": "not_checked"})
        elif running:
            external.append({"kind": "running_campaigns", "ref": str(running)})
    if external:
        warnings.append({"code": "external", "items": external})

    if recent:
        warnings.append({"code": "recent_activity", "items": [str(recent)]})
    if c.get("client_leads", 0) > 0:
        warnings.append({"code": "client_leads", "items": [str(c["client_leads"])]})
    if pending > 0:
        warnings.append({"code": "undelivered", "items": [str(pending)]})
    if c.get("landing_pages", 0) > 0:
        warnings.append({"code": "landing_pages", "items": [str(c["landing_pages"])]})
    if unreviewed > 0:
        warnings.append({"code": "feedback", "items": [str(unreviewed)]})
    return warnings


def preview(client_id: int, subscription_info=None, light: bool = False) -> dict:
    """ONE client, everything the confirmation modal shows. Changes NOTHING.
    light=True skips the external HTTP checks (used by the delete itself);
    otherwise each external check is bounded by EXTERNAL_CHECK_TIMEOUT."""
    from agents.client_agent import get_accounts, get_client
    client = get_client(client_id)
    if not client:
        return {"found": False}
    accounts = get_accounts(client_id)
    activity = _activity(client_id)
    sub = _subscription_from_activity(activity)

    paypal_status, running = "not_checked", "not_checked"
    if not light:
        if sub.get("subscription_id"):
            from core.paypal_service import get_subscription_status
            ok, value = _bounded(lambda: get_subscription_status(sub["subscription_id"]).get("status"))
            paypal_status = (value or "UNKNOWN") if ok else "not_checked"
        active_platforms = {a.get("platform") for a in accounts if a.get("status") == "active"}
        if active_platforms & {"google_ads", "meta_ads"}:
            def count_running():
                import importlib
                total = 0
                for platform, module in (("google_ads", "agents.google_ads_agent"),
                                         ("meta_ads", "agents.meta_ads_agent")):
                    if platform in active_platforms:
                        perf = importlib.import_module(module).get_campaign_performance(client_id)
                        total += sum(1 for c in perf.get("campaigns", [])
                                     if str(c.get("status", "")).upper() in ("ENABLED", "ACTIVE"))
                return total
            ok, value = _bounded(count_running, timeout=EXTERNAL_CHECK_TIMEOUT * 2)
            running = value if ok else "not_checked"

    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent = sum(1 for e in activity if (e.get("created_at") or "") >= month_ago)
    c = counts(client_id)
    warnings = _build_warnings(accounts, activity, recent, c,
                               _count_where("client_suggestions", client_id, "status", "pending"),
                               _count_where("client_feedback", client_id, "reviewed", False),
                               sub, paypal_status, running)
    return {
        "found": True,
        "client": {"id": client["id"], "name": client.get("name") or "",
                   "email": client.get("email") or "", "status": client.get("status") or ""},
        "hard_stops": hard_stops(client),
        "warnings": warnings,
        "counts": c,
        "external_checked": not light,
        "backup_downloaded_at": _progress(client_id).get("backup_at"),
    }


# ─── Bulk preview: batched, no external calls ────────────────────────────────
# The single preview costs ~25 database round trips plus up to 3 external HTTP
# calls per client. Run sequentially for 50 clients that was ~1,250 round
# trips + ~150 provider calls in ONE request — far past Cloud Run's 300 s.
# Here: ONE paged query per table for the whole batch (`client_id in (...)`),
# and no provider calls at all.
PAGE_ROWS = 1000
MAX_PAGES = 50   # per table; past it the count is reported as a lower bound

_EVENT_ACTIONS = ("subscription_created", "subscription_cancelled", "invoice_sent", "invoice_paid",
                  "invoice_cancelled", "website_provisioned", "media_folder_created")


def _paged(make_query) -> tuple:
    """(rows, truncated). make_query() must return a FRESH query builder."""
    rows, start = [], 0
    for _ in range(MAX_PAGES):
        batch = make_query().range(start, start + PAGE_ROWS - 1).execute().data or []
        rows += batch
        if len(batch) < PAGE_ROWS:
            return rows, False
        start += PAGE_ROWS
    return rows, True


def _tally(table: str, ids: list, extra=None) -> tuple:
    """({client_id: n}, truncated) — or (None, False) when the table is unavailable."""
    def q():
        query = _db().table(table).select("client_id").in_("client_id", ids)
        return extra(query) if extra else query
    try:
        rows, truncated = _paged(q)
    except Exception:
        return None, False
    out = {}
    for r in rows:
        out[r["client_id"]] = out.get(r["client_id"], 0) + 1
    return out, truncated


def batch_preview(ids: list) -> list:
    """The bulk modal's per-client preview for up to BULK_DELETE_MAX clients,
    in a bounded number of queries (~20 + paging), whatever the batch size.
    External checks are reported as "not_checked"."""
    ids = [int(i) for i in ids]
    db = _db()
    clients = {c["id"]: c for c in (db.table("clients").select("id,name,email,status,billing_exempt")
                                     .in_("id", ids).execute().data or [])}
    found = [i for i in ids if i in clients]
    if not found:
        return [{"id": i, "found": False} for i in ids]

    accounts = {}
    for a in _paged(lambda: db.table("client_accounts").select("client_id,platform,status,account_id")
                    .in_("client_id", found))[0]:
        accounts.setdefault(a["client_id"], []).append(a)
    events = {}
    for e in _paged(lambda: db.table("client_activity")
                    .select("client_id,agent_name,action_type,details,result,created_at")
                    .in_("client_id", found).in_("action_type", list(_EVENT_ACTIONS))
                    .order("created_at", desc=True))[0]:
        events.setdefault(e["client_id"], []).append(e)
    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent, _ = _tally("client_activity", found, lambda q: q.gte("created_at", month_ago))

    per_table, partial = {}, set()
    for table in ["client_accounts", "client_costs"] + [t for t, _o in DATA_TABLES]:
        tally, truncated = _tally(table, found)
        per_table[table] = tally
        if truncated:
            partial.add(table)
    pending, _ = _tally("client_suggestions", found, lambda q: q.eq("status", "pending"))
    unreviewed, _ = _tally("client_feedback", found, lambda q: q.eq("reviewed", False))

    leads_by_client, lead_owner = {}, {}
    try:
        for r in _paged(lambda: db.table("leads").select("client_id,lead_id").in_("client_id", found))[0]:
            leads_by_client[r["client_id"]] = leads_by_client.get(r["client_id"], 0) + 1
            if r.get("lead_id"):
                lead_owner[r["lead_id"]] = r["client_id"]
    except Exception:
        leads_by_client = None
    messages_by_client = {}
    lead_ids = list(lead_owner)
    try:
        for start in range(0, len(lead_ids), 100):
            chunk = lead_ids[start:start + 100]
            for r in _paged(lambda: db.table("lead_messages").select("lead_id").in_("lead_id", chunk))[0]:
                owner = lead_owner.get(r["lead_id"])
                messages_by_client[owner] = messages_by_client.get(owner, 0) + 1
    except Exception:
        messages_by_client = None
    settings = set()
    backups = {}
    try:
        keys = [f"client_settings:{i}" for i in found] + [f"{PROGRESS_PREFIX}{i}" for i in found]
        for r in db.table("app_settings").select("key,value").in_("key", keys).execute().data or []:
            key = r["key"]
            if key.startswith("client_settings:"):
                settings.add(int(key.split(":", 1)[1]))
            else:
                value = r.get("value") if isinstance(r.get("value"), dict) else {}
                backups[int(key.split(":", 1)[1])] = value.get("backup_at")
    except Exception:
        pass

    def n(tally, cid):
        return -1 if tally is None else tally.get(cid, 0)

    out = []
    for cid in ids:
        client = clients.get(cid)
        if not client:
            out.append({"id": cid, "found": False})
            continue
        c = {table: n(tally, cid) for table, tally in per_table.items()}
        c["leads"] = n(leads_by_client, cid)
        c["lead_messages"] = n(messages_by_client, cid)
        c["app_settings"] = 1 if cid in settings else 0
        client_events = events.get(cid, [])
        warnings = _build_warnings(accounts.get(cid, []), client_events, max(n(recent, cid), 0), c,
                                   max(n(pending, cid), 0), max(n(unreviewed, cid), 0),
                                   _subscription_from_activity(client_events), "not_checked", "not_checked")
        out.append({"id": cid, "found": True, "name": client.get("name") or "",
                    "protected": bool(hard_stops(client)), "hard_stops": hard_stops(client),
                    "warnings": warnings, "counts": c, "partial_counts": sorted(partial),
                    "external_checked": False, "backup_downloaded_at": backups.get(cid)})
    return out


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


# ─── Which tables: every table with an integer client_id column ──────────────
# Discovered from the database's own description (PostgREST's OpenAPI document
# at /rest/v1/), not from a list someone has to remember to update. The known
# list below is ALWAYS included on top (a required table is never dropped
# because discovery missed it); when discovery itself fails, the known list is
# used alone and that is reported in the result.

# Tables with their own step instead of the generic delete-by-client_id
SPECIAL_TABLES = ("client_accounts", "client_costs", "leads")
KNOWN_CLIENT_TABLES = SPECIAL_TABLES + tuple(t for t, _o in DATA_TABLES)
_OPTIONAL = {t for t, optional in DATA_TABLES if optional}
SCHEMA_TTL = 600
_schema = {"at": 0.0, "tables": None}


def _discover_tables():
    """Set of table names with an INTEGER client_id column that accept a
    DELETE, or None when the description can't be read. Read with the service
    key, which is never logged."""
    import httpx
    url = os.environ.get("SUPABASE_URL", "").rstrip("/") + "/rest/v1/"
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    try:
        response = httpx.get(url, headers={"apikey": key, "Authorization": f"Bearer {key}",
                                           "Accept": "application/openapi+json"}, timeout=10)
        if response.status_code != 200:
            print(f"[client_deletion] schema discovery: HTTP {response.status_code} — using the known table list")
            return None
        spec = response.json()
    except Exception as e:
        print(f"[client_deletion] schema discovery failed ({type(e).__name__}) — using the known table list")
        return None
    definitions, paths = spec.get("definitions") or {}, spec.get("paths") or {}
    found = set()
    for name, definition in definitions.items():
        column = ((definition or {}).get("properties") or {}).get("client_id") or {}
        if column.get("type") == "integer" and "delete" in (paths.get(f"/{name}") or {}):
            found.add(name)
    found.discard("clients")
    return found


def client_tables() -> tuple:
    """(tables, discovered). `tables` = every client_id table to clear."""
    import time
    if _schema["tables"] is None or time.monotonic() - _schema["at"] > SCHEMA_TTL:
        _schema["tables"], _schema["at"] = _discover_tables(), time.monotonic()
    found = _schema["tables"]
    if found is None:
        return list(KNOWN_CLIENT_TABLES), False
    known = [t for t in KNOWN_CLIENT_TABLES if t not in _OPTIONAL or t in found]
    return known + sorted(found - set(known)), True


def _missing_table(error: Exception) -> bool:
    text = str(error)
    return any(m in text for m in ("PGRST205", "42P01", "Could not find the table", "does not exist"))


def _delete_query(table: str):
    """DELETE that returns no rows back (the default sends every deleted row
    over the wire — the slowest part of clearing a big activity table)."""
    try:
        from postgrest.types import ReturningMethod
        return _db().table(table).delete(returning=ReturningMethod.minimal)
    except Exception:
        return _db().table(table).delete()


# ─── Timing ([client_delete_timing] log lines: ids and durations only) ───────

class _Timer:
    def __init__(self):
        import time
        self._clock = time.monotonic
        self.started = self._clock()
        self.steps = []

    def step(self, name: str):
        timer = self

        class _Step:
            def __enter__(self):
                self.t = timer._clock()

            def __exit__(self, *exc):
                timer.steps.append((name, timer._clock() - self.t))
                return False
        return _Step()

    def total(self) -> float:
        return self._clock() - self.started

    def text(self) -> str:
        return " ".join(f"{name}={seconds:.2f}s" for name, seconds in self.steps)


# ─── Parallel helpers ────────────────────────────────────────────────────────
TABLE_WORKERS = 5      # concurrent table statements within one chunk
REVOKE_TIMEOUT = 5     # seconds per provider revoke attempt (one retry)


def _parallel(fn, items, workers: int = TABLE_WORKERS) -> dict:
    """{item: (ok, value | exception)} — every item runs, none is dropped."""
    from concurrent.futures import ThreadPoolExecutor
    items = list(items)
    if not items:
        return {}
    out = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items))),
                            thread_name_prefix="client-delete") as pool:
        futures = {item: pool.submit(fn, item) for item in items}
        for item, future in futures.items():
            try:
                out[item] = (True, future.result())
            except Exception as e:
                out[item] = (False, e)
    return out


def _paged_rows(table: str, select: str, column: str, values: list, extra=None) -> list:
    rows = []
    for start in range(0, len(values), 100):
        chunk = values[start:start + 100]

        def q(chunk=chunk):
            query = _db().table(table).select(select).in_(column, chunk)
            return extra(query) if extra else query
        rows += _paged(q)[0]
    return rows


# ─── Lock: one deletion per client at a time ─────────────────────────────────
# The progress row doubles as the lock: a compare-and-set on its updated_at
# (or the unique key, for a first insert), so two requests — on any instance —
# can never both hold it. The lock expires after LOCK_SECONDS (the proxy's
# request limit), so a crashed request never locks a client forever.
LOCK_SECONDS = 290
_inproc = set()
_inproc_lock = threading.Lock()


def _inproc_guard():
    return _inproc_lock


def _acquire(client_id: int, token: str):
    """The client's progress dict with our lock in it, or None when another
    deletion of this client is running."""
    with _inproc_guard():
        if client_id in _inproc:
            return None
        _inproc.add(client_id)
    got = None
    try:
        got = _acquire_row(client_id, token)
    except Exception as e:
        print(f"[client_deletion] lock for client {client_id} not taken ({type(e).__name__})")
    if got is None:
        _release_inproc(client_id)
    return got


def _release_inproc(client_id: int):
    with _inproc_guard():
        _inproc.discard(client_id)


def _acquire_row(client_id: int, token: str):
    db, key = _db(), f"{PROGRESS_PREFIX}{int(client_id)}"
    now = datetime.now(timezone.utc)
    lock = {"token": token, "until": (now + timedelta(seconds=LOCK_SECONDS)).isoformat()}
    rows = db.table("app_settings").select("value,updated_at").eq("key", key).limit(1).execute().data or []
    if not rows:
        # Unique key: of two simultaneous first inserts, exactly one succeeds
        value = {"lock": lock}
        db.table("app_settings").insert({"key": key, "value": value, "updated_at": _now()}).execute()
        return value
    value = rows[0].get("value") if isinstance(rows[0].get("value"), dict) else {}
    held = value.get("lock") or {}
    if held.get("token") and held.get("token") != token and str(held.get("until") or "") > now.isoformat():
        return None
    value = {**value, "lock": lock}
    query = db.table("app_settings").update({"value": value, "updated_at": _now()}).eq("key", key)
    stamp = rows[0].get("updated_at")
    query = query.eq("updated_at", stamp) if stamp else query.is_("updated_at", "null")
    return value if (query.execute().data or []) else None


def _save_many(values: dict):
    """One upsert for several clients' progress rows."""
    if values:
        _db().table("app_settings").upsert(
            [{"key": f"{PROGRESS_PREFIX}{int(cid)}", "value": value, "updated_at": _now()}
             for cid, value in values.items()], on_conflict="key").execute()


# ─── Connections: revoke FIRST, then delete the rows (secrets go with them) ──

def _revoke_once(fn):
    """(ok, value). Runs fn in its own thread and waits REVOKE_TIMEOUT."""
    import threading
    box = {}

    def run():
        try:
            box["value"] = fn()
        except Exception as e:
            box["error"] = type(e).__name__
    worker = threading.Thread(target=run, daemon=True, name="client-delete-revoke")
    worker.start()
    worker.join(REVOKE_TIMEOUT)
    if worker.is_alive():
        return False, "timeout"
    if "error" in box:
        return False, box["error"]
    return True, box.get("value")


def _revoke_group(client_id: int, group: str, rows: list, revoke) -> dict:
    """One provider grant: up to 2 attempts of REVOKE_TIMEOUT each."""
    import time
    started, error = time.monotonic(), "not_confirmed"
    for attempt in (1, 2):
        ok, value = _revoke_once(lambda: revoke(client_id, group, rows))
        if ok and value is not False:
            return {"platform": group, "revoked": value, "attempts": attempt,
                    "seconds": round(time.monotonic() - started, 2)}
        error = value if not ok else "not_confirmed"
    return {"platform": group, "revoked": False, "error": error, "attempts": 2,
            "seconds": round(time.monotonic() - started, 2)}


def _accounts(ids: list) -> list:
    from core.secrets_vault import public_account
    return [public_account(r) for r in
            _paged(lambda: _db().table("client_accounts").select("*").in_("client_id", ids))[0]]


def _revoke_rows(rows: list, revoke, groups: dict, results: dict, seen: set):
    """Revoke every (client, group) that has a row not seen before, all in
    parallel. Appends to results[cid]; adds the row ids to `seen`."""
    group_of = {platform: group for group, platforms in groups.items() for platform in platforms}
    todo = {}
    for row in rows:
        if row.get("id") in seen:
            continue
        group = group_of.get(row.get("platform"))
        if group:
            todo.setdefault((row["client_id"], group), []).append(row)
    done = _parallel(lambda key: _revoke_group(key[0], key[1], todo[key], revoke), todo, workers=16)
    for (cid, group), (ok, value) in done.items():
        results.setdefault(cid, []).append(value if ok else {"platform": group, "revoked": False,
                                                             "error": type(value).__name__})
    seen.update(r.get("id") for r in rows)


def _clear_accounts(ids: list, revoke, groups: dict, results: dict, seen: set, rows: list = None):
    """Revoke, then re-read (a row written in the guard's cross-instance window
    is revoked too), then delete every client_accounts row of these clients."""
    if rows is None:
        rows = _accounts(ids)
    _revoke_rows(rows, revoke, groups, results, seen)
    late = [r for r in _accounts(ids) if r.get("id") not in seen]
    if late:
        _revoke_rows(late, revoke, groups, results, seen)
    _delete_query("client_accounts").in_("client_id", ids).execute()


# ─── Costs -> anonymous monthly aggregates (one pass per chunk) ──────────────

def _aggregate(rows: list) -> list:
    """Per month / category / metering bucket / payer — no client id, no lead
    id, no details beyond the aggregate itself. Same math as before."""
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
    return [{"month": k[0], "category": k[1], "cost_category": k[2], "payer": k[3],
             "amount": round(v["amount"], 4), "usd": round(v["usd"], 6), "rows": v["rows"]}
            for k, v in aggregates.items()]


def _insert_aggregates(aggregates: list):
    rows = []
    for agg in aggregates:
        month = agg["month"] or datetime.now(timezone.utc).strftime("%Y-%m")
        rows.append({"client_id": None, "category": agg["category"], "amount": agg["amount"],
                     "created_at": f"{month}-01T00:00:00+00:00",
                     "details": {"aggregate": True, "deleted_client": True, "rows": agg["rows"],
                                 "usd": agg["usd"], "payer": agg["payer"] or None,
                                 "cost_category": agg["cost_category"] or None},
                     "_bucket": agg["cost_category"] or None})
    if not rows:
        return
    db = _db()
    try:
        db.table("client_costs").insert([{**{k: v for k, v in r.items() if k != "_bucket"},
                                          "attribution": "deleted_client", "cost_category": r["_bucket"]}
                                         for r in rows]).execute()
    except Exception:
        # attribution columns not migrated (one statement: nothing was inserted)
        db.table("client_costs").insert([{k: v for k, v in r.items() if k != "_bucket"}
                                         for r in rows]).execute()


def _cost_rows(ids: list, lead_owner: dict) -> dict:
    """{client_id: [rows]} — their own rows plus the pre-signup rows of their
    sales leads (lead_id set, client_id empty). Paged: no 1,000-row ceiling."""
    out = {cid: [] for cid in ids}
    for r in _paged(lambda: _db().table("client_costs").select("*").in_("client_id", ids))[0]:
        out.setdefault(r["client_id"], []).append(r)
    if lead_owner:
        try:
            for r in _paged_rows("client_costs", "*", "lead_id", list(lead_owner),
                                 lambda q: q.is_("client_id", "null")):
                out.setdefault(lead_owner.get(r.get("lead_id")), []).append(r)
        except Exception:
            pass  # lead_id column not migrated — nothing attributed by lead
    out.pop(None, None)
    return out


def _legacy_cost_progress(progress: dict) -> dict:
    """A run interrupted under the previous version left cost_row_ids /
    cost_aggregates / costs_aggregated: carried over as the pending set."""
    if "cost_row_ids" in progress and "cost_pending" not in progress and not progress.get("costs_done"):
        progress["cost_pending"] = {"ids": progress.get("cost_row_ids") or [],
                                    "aggregates": progress.get("cost_aggregates") or [],
                                    "inserted": bool(progress.get("costs_aggregated"))}
    for k in ("cost_row_ids", "cost_aggregates", "costs_aggregated"):
        progress.pop(k, None)
    return progress


def _settle_costs(ids: list, progress: dict, lead_owner: dict):
    """Fold every cost row of these clients into anonymous aggregates, then
    delete the originals. Resumable: the exact row ids and their aggregates
    are recorded BEFORE the insert, and the insert is marked done before the
    delete, so a re-run never counts a row twice. Repeats until no row is
    left (a row that lands mid-way is folded in on the next round)."""
    for cid in ids:
        _legacy_cost_progress(progress[cid])
    for _round in range(5):
        pending = {cid: progress[cid]["cost_pending"] for cid in ids if progress[cid].get("cost_pending")}
        to_insert = [cid for cid, p in pending.items() if not p.get("inserted")]
        if to_insert:
            _insert_aggregates([agg for cid in to_insert for agg in pending[cid].get("aggregates") or []])
            for cid in to_insert:
                pending[cid]["inserted"] = True
            _save_many({cid: progress[cid] for cid in to_insert})
        row_ids = sorted({i for p in pending.values() for i in p.get("ids") or []})
        for start in range(0, len(row_ids), 200):
            _delete_query("client_costs").in_("id", row_ids[start:start + 200]).execute()
        if pending:
            for cid in pending:
                progress[cid].pop("cost_pending", None)
            _save_many({cid: progress[cid] for cid in pending})

        remaining = {cid: rows for cid, rows in _cost_rows(ids, lead_owner).items() if rows and cid in progress}
        if not remaining:
            for cid in ids:
                progress[cid]["costs_done"] = True
            return
        for cid, rows in remaining.items():
            progress[cid]["cost_pending"] = {"ids": sorted({r["id"] for r in rows}),
                                             "aggregates": _aggregate(rows), "inserted": False}
        _save_many({cid: progress[cid] for cid in remaining})
    raise RuntimeError("costs_not_settled")


# ─── Generic tables, sales leads, settings ───────────────────────────────────

def _clear_tables(tables: list, ids: list, discovered: bool) -> tuple:
    """Delete every row of these clients in every table: one statement per
    table for the whole chunk, TABLE_WORKERS at a time. A statement that fails
    is retried after the others (a foreign key between two of them resolves
    once the referencing table is empty), then per client, so one client's
    problem is reported against that client only.
    Returns ({cid: [failures]}, [tables that don't exist])."""
    failures, missing = {cid: [] for cid in ids}, []

    def one(table, scope=ids):
        _delete_query(table).in_("client_id", scope).execute()

    errors = {t: v for t, (ok, v) in _parallel(one, tables).items() if not ok}
    for table, error in list(errors.items()):
        if _missing_table(error) and (table in _OPTIONAL or not discovered):
            missing.append(table)
            errors.pop(table)
            continue
        try:
            one(table)
            errors.pop(table)
        except Exception as e:
            errors[table] = e
    for table in errors:
        for cid in ids:
            try:
                one(table, [cid])
            except Exception as e:
                failures[cid].append({"step": table, "error": type(e).__name__})
    return failures, missing


def _lead_owner(ids: list) -> dict:
    """{lead_id: client_id} for the sales-funnel leads linked to these clients."""
    rows = _paged(lambda: _db().table("leads").select("client_id,lead_id").in_("client_id", ids))[0]
    return {r["lead_id"]: r["client_id"] for r in rows if r.get("lead_id")}


def _clear_leads(ids: list, lead_owner: dict):
    """Transcripts first (they hang off lead_id), then the leads rows."""
    lead_ids = list(lead_owner)
    for start in range(0, len(lead_ids), 100):
        _delete_query("lead_messages").in_("lead_id", lead_ids[start:start + 100]).execute()
    _delete_query("leads").in_("client_id", ids).execute()


def _settings_keys(ids: list) -> list:
    """The client's own app_settings rows: per-client limits, the Tag
    Manager / Google Ads token-health records (core/gtm_connection,
    core/google_ads_health), the TikTok grant record (core/tiktok_grant) and
    the PHP2Shell site record (core/php_exposure) and the creative tools'
    key health + video provider choice (core/vendor_tools) and where their
    files live (core/drive_storage)."""
    return [k for i in ids for k in (f"client_settings:{int(i)}", f"gtm_token:{int(i)}",
                                     f"gads_token:{int(i)}", f"tiktok_grant:{int(i)}",
                                     f"php_exposure:{int(i)}", f"creative_tools:{int(i)}",
                                     f"drive_storage:{int(i)}", f"drive_usage:{int(i)}")]


def _clear_settings(ids: list):
    _delete_query("app_settings").in_("key", _settings_keys(ids)).execute()


# ─── Verification: count what is left, per client, per table ─────────────────

def _verify(tables: list, ids: list, lead_owner: dict, missing: list) -> tuple:
    """({cid: {table: n}} for anything still present, [unverifiable tables]).
    One batched query per table for the whole chunk, in parallel."""
    left, unverified = {}, []

    def count(table):
        if table == "lead_messages":
            rows = _paged_rows("lead_messages", "lead_id", "lead_id", list(lead_owner))
            return [lead_owner.get(r.get("lead_id")) for r in rows]
        if table == "client_costs:lead":
            try:
                rows = _paged_rows("client_costs", "lead_id", "lead_id", list(lead_owner),
                                   lambda q: q.is_("client_id", "null"))
            except Exception:
                return []  # lead_id column not migrated
            return [lead_owner.get(r.get("lead_id")) for r in rows]
        if table == "app_settings":
            rows = (_db().table("app_settings").select("key")
                    .in_("key", _settings_keys(ids)).execute().data or [])
            return [int(r["key"].split(":", 1)[1]) for r in rows]
        return [r["client_id"] for r in
                _paged(lambda: _db().table(table).select("client_id").in_("client_id", ids))[0]]

    checks = [t for t in tables if t not in missing] + ["lead_messages", "client_costs:lead", "app_settings"]
    for table, (ok, value) in _parallel(count, checks).items():
        if not ok:
            unverified.append(table)
            continue
        for cid in value:
            if cid is not None:
                name = "client_costs" if table == "client_costs:lead" else table
                left.setdefault(cid, {})[name] = left.get(cid, {}).get(name, 0) + 1
    return left, unverified


def _fix(left: dict, tables: list, discovered: bool, progress: dict, lead_owner: dict,
         revoke, groups: dict, revokes: dict, seen: set) -> dict:
    """One more delete for exactly what verification found. {cid: [failures]}."""
    failures = {}
    by_table = {}
    for cid, found in left.items():
        for table in found:
            by_table.setdefault(table, []).append(cid)

    def note(cids, step, error):
        for cid in cids:
            failures.setdefault(cid, []).append({"step": step, "error": type(error).__name__})

    for table, cids in by_table.items():
        try:
            if table == "client_accounts":
                _clear_accounts(cids, revoke, groups, revokes, seen)
            elif table == "client_costs":
                _settle_costs(cids, progress, {k: v for k, v in lead_owner.items() if v in cids})
            elif table in ("leads", "lead_messages"):
                fresh = _lead_owner(cids)
                lead_owner.update(fresh)
                _clear_leads(cids, {k: v for k, v in lead_owner.items() if v in cids})
            elif table == "app_settings":
                _clear_settings(cids)
            else:
                for cid, fails in _clear_tables([table], cids, discovered)[0].items():
                    failures.setdefault(cid, []).extend(fails)
        except Exception as e:
            note(cids, table, e)
    return failures


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


# Steps reported to the bulk modal while a chunk runs (on_step)
STEPS = ("lock", "mark", "revoke", "accounts", "costs", "tables", "leads", "google_records", "verify",
         "client_row", "audit")


def delete_client_full(client_id: int, confirm: str, skip_backup: bool,
                       revoke, revoke_groups: dict, subscription_info=None) -> dict:
    """THE single deletion. Raises ValueError for a refusal (not_found,
    hard_stop:<reasons>, confirm_mismatch, backup_required, in_progress);
    otherwise returns this client's result (see delete_clients)."""
    from agents.client_agent import get_client
    client = get_client(client_id)
    if not client:
        raise ValueError("not_found")
    stops = hard_stops(client)
    if stops:
        raise ValueError("hard_stop:" + ",".join(stops))
    if not confirm_matches(client, confirm):
        raise ValueError("confirm_mismatch")
    outcome = delete_clients([client_id], skip_backup, revoke, revoke_groups)[client_id]
    if outcome.get("refused"):
        raise ValueError(outcome["refused"])
    return outcome


def delete_clients(ids: list, skip_backup: bool, revoke, revoke_groups: dict, on_step=None) -> dict:
    """Delete several clients TOGETHER (one bulk chunk, or one client): every
    step runs once for the whole group, not once per client. Returns
    {client_id: result}; a refused client has {"refused": reason} and was not
    touched at all.

    Order (no cross-table transaction exists over the Supabase REST API, so
    every step is idempotent and progress is recorded — a re-run resumes):
      lock        one deletion per client at a time (any instance)
      mark        status 'deleting' + marker: logins, sessions, schedulers,
                  webhooks and every writer refuse new rows (core/deletion_guard)
      revoke      every provider grant, in parallel, 5 s + one retry each —
                  BEFORE any connection row or secret is deleted
      accounts    re-read (late rows revoked too), then delete client_accounts
      costs       anonymous monthly aggregates, then the originals
      tables      every client_id table from the schema, batched + parallel
      leads       sales-funnel transcripts, then the leads rows; settings row
      google_records  the client's entries in weekly_reports and their alerts
                  (no client_id column there — core/google_data_purge)
      verify      count what is left in EVERY table (and weekly_reports /
                  alerts); delete it once more; count again — leftovers are
                  reported, never hidden
      client_row  clients — LAST, only for a client with no failure and
                  nothing left; then one more sweep for anything written
                  meanwhile
      audit       one alert per client: id, counts, revoke failures,
                  leftovers (id-only — no name, no other personal data)
    External resources (site hosting, domains, Drive, ad accounts, PayPal) are
    never touched — they are listed in not_removed."""
    import uuid
    from agents.master_agent import alert
    from core import deletion_guard
    ids = [int(i) for i in dict.fromkeys(ids)]
    timer, results = _Timer(), {}
    step_no = [0]

    def stage(name):
        step_no[0] += 1
        if on_step:
            try:
                on_step(name, step_no[0], len(STEPS))
            except Exception:
                pass
        return timer.step(name)

    # Refusals first — a refused client is never touched
    with stage("lock"):
        clients = {c["id"]: c for c in (_db().table("clients").select("*").in_("id", ids).execute().data or [])}
        token, progress = uuid.uuid4().hex, {}
        candidates = []
        for cid in ids:
            client = clients.get(cid)
            if not client:
                results[cid] = {"refused": "not_found"}
            elif hard_stops(client):
                results[cid] = {"refused": "hard_stop:" + ",".join(hard_stops(client))}
            else:
                candidates.append(cid)
        for cid, (ok, value) in _parallel(lambda cid: _acquire(cid, token), candidates).items():
            if not ok or value is None:
                results[cid] = {"refused": "in_progress"}
            elif not skip_backup and not _fresh(value.get("backup_at")):
                results[cid] = {"refused": "backup_required"}
                _release(cid, value)
            else:
                progress[cid] = value
    work = [cid for cid in ids if cid in progress]
    if not work:
        return results

    try:
        return _delete_locked(work, clients, progress, results, timer, stage,
                              revoke, revoke_groups, alert, deletion_guard)
    except Exception:
        # Unexpected stop: keep what was recorded (incl. "deleting"), drop the
        # lock so a re-run can resume at once instead of after LOCK_SECONDS
        for cid in work:
            _release(cid, progress[cid])
        raise
    finally:
        for cid in work:
            _release_inproc(cid)


def _fresh(backup_at) -> bool:
    if not backup_at:
        return False
    try:
        when = datetime.fromisoformat(str(backup_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - when < timedelta(hours=BACKUP_VALID_HOURS)


def _release(client_id: int, value: dict):
    try:
        _save_many({client_id: {k: v for k, v in value.items() if k != "lock"}})
    except Exception:
        pass  # the lock expires on its own after LOCK_SECONDS
    _release_inproc(client_id)


def _delete_locked(work, clients, progress, results, timer, stage,
                   revoke, revoke_groups, alert, deletion_guard) -> dict:
    import threading
    failures = {cid: [] for cid in work}
    revokes, seen = {}, set()

    def fail(cids, step, error):
        for cid in cids:
            failures[cid].append({"step": step, "error": type(error).__name__})

    def each(cids, fn, step):
        """The batched statement first; if it fails, client by client, so one
        client's problem is reported against that client only."""
        try:
            fn(cids)
        except Exception:
            for cid in cids:
                try:
                    fn([cid])
                except Exception as e:
                    fail([cid], step, e)

    # mark: the marker row other instances read, then this instance's guard
    # (exact, immediate), then the status every login/session/scheduler checks
    with stage("mark"):
        for cid in work:
            progress[cid].update({"deleting": True,
                                  "started_at": progress[cid].get("started_at") or _now()})
            progress[cid].setdefault("status_before", clients[cid].get("status"))
        _save_many(progress)
        deletion_guard.mark(work)
        try:
            _db().table("clients").update({"status": "deleting"}).in_("id", work).execute()
        except Exception as e:
            print(f"[client_deletion] status 'deleting' not set ({type(e).__name__}) — the marker row still guards")
        for cid in work:
            _drop_caches(cid)

    tables, discovered = client_tables()
    # The snapshot ("what will be removed", external resources) is read while
    # the revokes run. It only fills the REPORT: no step is ever skipped
    # because of it — every table is cleared and then verified.
    snapshot = {}

    def take_snapshot():
        try:
            snapshot.update({p["id"]: p for p in batch_preview(work)})
            extra = [t for t in tables if t not in KNOWN_CLIENT_TABLES]
            for table, (ok, value) in _parallel(lambda t: _tally(t, work)[0], extra).items():
                for cid in work:
                    if ok and value is not None and snapshot.get(cid):
                        snapshot[cid].setdefault("counts", {})[table] = value.get(cid, 0)
        except Exception as e:
            print(f"[client_deletion] snapshot failed ({type(e).__name__}) — counts in the report will be partial")
    snap_thread = threading.Thread(target=take_snapshot, daemon=True, name="client-delete-snapshot")
    snap_thread.start()

    with stage("revoke"):
        try:
            rows = _accounts(work)
            _revoke_rows(rows, revoke, revoke_groups, revokes, seen)
        except Exception as e:
            rows = None
            fail(work, "revoke", e)
    snap_thread.join(60)

    with stage("accounts"):
        if rows is not None:
            # rows already revoked are in `seen`; only late rows revoke again
            each(work, lambda c: _clear_accounts(c, revoke, revoke_groups, revokes, seen,
                                                 rows=[r for r in rows if r.get("client_id") in c]),
                 "client_accounts")

    lead_owner = None
    with stage("costs"):
        try:
            lead_owner = _lead_owner(work)
        except Exception as e:
            fail(work, "client_costs", e)   # without the lead list, lead cost rows can't be found
        if lead_owner is not None:
            each(work, lambda c: _settle_costs(c, progress, {k: v for k, v in lead_owner.items() if v in c}),
                 "client_costs")
    lead_owner = lead_owner or {}

    generic = [t for t in tables if t not in SPECIAL_TABLES]
    missing = []
    with stage("tables"):
        per_client, missing = _clear_tables(generic, work, discovered)
        for cid, fails in per_client.items():
            failures[cid].extend(fails)

    with stage("leads"):
        try:
            lead_owner.update(_lead_owner(work))
        except Exception as e:
            fail(work, "leads", e)
        else:
            each(work, lambda c: _clear_leads(c, {k: v for k, v in lead_owner.items() if v in c}), "leads")
        each(work, _clear_settings, "app_settings")

    # Alerts written since the FIRST attempt started are this deletion's own
    # record and stay; everything older that names the client goes
    run_started = min(str(progress[cid].get("started_at") or _now()) for cid in work)
    with stage("google_records"):
        try:
            google_data_purge.scrub(work, run_started)
        except Exception as e:
            # Not a failure by itself: verify below scrubs once more, and
            # whatever is still there then blocks the clients row
            print(f"[client_deletion] google_records scrub failed ({type(e).__name__}) — verify retries")

    leftovers, unverified = {}, []
    with stage("verify"):
        if rows is not None:
            left, unverified = _verify(tables, work, lead_owner, missing)
            if left:
                for cid, fails in _fix(left, tables, discovered, progress, lead_owner,
                                       revoke, revoke_groups, revokes, seen).items():
                    failures[cid].extend(fails)
                leftovers, unverified = _verify(tables, work, lead_owner, missing)
        # weekly_reports + alerts: verified (and re-scrubbed once) the same way
        g_left, g_unverified = google_data_purge.verify(work, run_started)
        if g_left:
            try:
                google_data_purge.scrub(list(g_left), run_started)
            except Exception as e:
                print(f"[client_deletion] google_records re-scrub failed ({type(e).__name__})")
            g_left, g_unverified = google_data_purge.verify(work, run_started)
        for cid, found in g_left.items():
            if cid in work:
                leftovers.setdefault(cid, {}).update(found)
        unverified = list(unverified) + [t for t in g_unverified if t not in unverified]

    with stage("client_row"):
        ready = [cid for cid in work if not failures[cid] and not leftovers.get(cid) and not unverified]
        if ready:
            try:
                _delete_query("clients").in_("id", ready).execute()
            except Exception:
                for cid in list(ready):
                    try:
                        _delete_query("clients").eq("id", cid).execute()
                    except Exception as e:
                        fail([cid], "clients", e)
                        ready.remove(cid)
        late = {}
        if ready:
            # Anything a job in flight wrote between the last check and the
            # clients row going (the tombstone blocks writes from here on)
            mine = {k: v for k, v in lead_owner.items() if v in ready}
            found, _u = _verify(tables, ready, mine, missing)
            found = {cid: left for cid, left in found.items() if cid in ready}
            if found:
                late = found
                _fix(found, tables, discovered, progress, mine, revoke, revoke_groups, revokes, seen)
                again, _u = _verify(tables, ready, mine, missing)
                for cid, left in again.items():
                    if cid in ready:
                        leftovers[cid] = left
        for cid in work:
            _drop_caches(cid)
        final = {}
        for cid in work:
            if cid in ready:
                final[cid] = {"deleted": True, "deleted_at": _now()}   # id-only tombstone
            else:
                final[cid] = {k: v for k, v in progress[cid].items() if k != "lock"}
        try:
            _save_many(final)
        except Exception as e:
            print(f"[client_deletion] final progress save failed ({type(e).__name__})")

    with stage("audit"):
        for cid in work:
            snap = snapshot.get(cid) or {}
            counts_before = snap.get("counts") or {}
            removed = {t: max(n, 0) for t, n in counts_before.items() if isinstance(n, int)}
            success = cid in ready and not leftovers.get(cid)
            if cid in ready:
                removed["clients"] = 1
            external = next((w["items"] for w in snap.get("warnings") or [] if w.get("code") == "external"), [])
            client_revokes = revokes.get(cid, [])
            revoke_failed = [r["platform"] for r in client_revokes if r.get("revoked") is False or r.get("error")]
            fails = failures[cid] + [{"step": f"verify:{t}", "error": "unverified"} for t in unverified]
            results[cid] = {"success": success, "removed": removed,
                            "revoke": [{k: v for k, v in r.items() if k != "seconds"} for r in client_revokes],
                            "revoke_failed": revoke_failed, "failed": fails,
                            "leftovers": leftovers.get(cid) or {}, "late_rows": late.get(cid) or {},
                            "not_removed": external, "tables_from_schema": discovered,
                            "missing_tables": missing}
            details = ", ".join(f"{r['platform']} ({r.get('error') or 'not_confirmed'})"
                                for r in client_revokes if r["platform"] in revoke_failed)
            try:
                # id-only: this audit entry is the one record that outlives the client
                alert("client_deleted", [
                    f"client {cid} "
                    f"{'DELETED' if success else 'PARTIALLY deleted'} by admin at {_now()}: removed {removed}"
                    + (f"; revoke NOT confirmed for client {cid}: {details}" if details else "")
                    + (f"; rows STILL PRESENT after verification: {leftovers[cid]}" if leftovers.get(cid) else "")
                    + (f"; rows written during the deletion, removed: {late[cid]}" if late.get(cid) else "")
                    + (f"; FAILED steps {[f['step'] for f in fails]} — re-run to resume" if fails else "")
                    + ("" if discovered else "; table list NOT read from the schema (known list used)")
                    + (f"; external resources NOT removed: {len(external)}" if external else "")])
            except Exception as e:
                print(f"[client_deletion] audit alert for client {cid} failed ({type(e).__name__})")

    total = timer.total()
    for cid in work:
        own = max((r.get("seconds") or 0 for r in revokes.get(cid, [])), default=0)
        results[cid]["timing"] = {"total": round(total, 2), "steps": {n: round(s, 2) for n, s in timer.steps},
                                  "own_revoke": own, "chunk_size": len(work)}
        print(f"[client_delete_timing] id={cid} total={total:.2f}s steps: {timer.text()} "
              f"own_revoke={own:.2f}s chunk_size={len(work)}")
    return results
