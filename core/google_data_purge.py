"""Google user data at the end of a client — what may outlive them, and the
cleanup of the places that hold it OUTSIDE the client_id tables.

## Why

The privacy policy (dashboard/privacy/, section 9) promises that when a client
closes their account or is deleted, no Google-sourced data about them is kept:
not in the database, not in the long-term archive, not in a backup. Both code
paths that end a client come through here:

- api_server._offboard_client (self-service closure / transfer) keeps a
  long-term Drive archive and emails the client their copy:
  `archive_safe_export()` decides what may go in either. It also calls
  `end_of_client()` before the archive is written.
- client_deletion.delete_clients (admin delete) removes every client_id row,
  then `scrub()` + `verify()` here clear the rest; leftovers block the
  clients-row delete exactly like a leftover table row does. Its pre-deletion
  backup goes through `archive_safe_export()` + `backup_safe_extra()`.

## What counts as Google-sourced

Anything read from a Google API for the client (Google Ads, Merchant Center,
YouTube, Tag Manager, sign-in) and anything derived from it: campaign numbers,
Google account/customer/channel/container ids, assistant chat replies (they may
quote campaign numbers — messages carry no label, so EVERY outbound message is
treated as Google-bearing), activity rows (only an explicit allowlist of
billing/lifecycle rows is known to be free of it), suggestions (generated with
campaign totals as input) and lead-volume rows (counted from ad conversions).

## The places with no client_id column

- `weekly_reports`: one row per weekly run, every client's numbers inside
  report.clients[] plus "<client name>: ..." lines in highlights /
  recommendations. The client's entry and lines are removed; the row keeps an
  id-only `removed_client_ids` list.
- `alerts`: matched on the house "client {id}" text convention
  (control_room._client_pattern). Deleted, except audit entries
  (bulk_delete.AUDIT_SOURCES) and alerts written after the run started — those
  are the run's own operational record (a failed revoke, a failed archive).
- this instance's local `data/alert_history.json` (ephemeral, last 500) — best
  effort; other instances' copies vanish on their next restart.

Not reachable from code, and said so in the policy: copies of emails already
sent (conversion alerts in the client's inbox, the team's weekly report emails)
stay in those mailboxes.
"""
import json
from datetime import datetime, timezone

SERVICE_NAME = "google_data_purge"

# client_accounts platforms that hold a Google grant
GOOGLE_PLATFORMS = ("google_ads", "youtube", "youtube_pending", "google_tagmanager",
                    "merchant_center", "merchant_center_pending")

# The ONLY activity rows that may outlive a client in the archive: billing and
# account lifecycle, none of which carries data read from a Google API. An
# allowlist on purpose — a new agent's rows stay out until someone adds them.
ARCHIVE_ACTIVITY_AGENTS = ("paypal_service",)
ARCHIVE_ACTIVITY_ACTIONS = ("account_closed", "account_transferred", "account_disconnected",
                            "account_created_by_admin", "google_ads_staff_access_consent")

# Extra tables in the admin pre-deletion backup that are derived from Google data
BACKUP_GOOGLE_DERIVED = ("client_suggestions", "client_lead_volume")

# Live rows deleted at closure BEFORE the archive is written (whatever happens
# to the archive afterwards, these do not survive the run)
LIVE_GOOGLE_DERIVED_TABLES = ("client_suggestions", "client_lead_volume", "admin_agent_chat")

PAGE_ROWS = 1000
MAX_PAGES = 50


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def now() -> str:
    """A run's start stamp: alerts written from here on are kept."""
    return datetime.now(timezone.utc).isoformat()


# ─── What may outlive the client ─────────────────────────────────────────────

def activity_archivable(row: dict) -> bool:
    return (row.get("agent_name") in ARCHIVE_ACTIVITY_AGENTS
            or row.get("action_type") in ARCHIVE_ACTIVITY_ACTIONS)


def archive_safe_export(export: dict) -> dict:
    """api_server._build_client_export's output without Google-sourced data.
    Used for the Drive archive, the closure email and the admin backup."""
    out = dict(export)
    performance = dict(export.get("campaign_performance_last_30_days") or {})
    performance.pop("google_ads", None)
    out["campaign_performance_last_30_days"] = performance
    # A Google connection keeps only the fact that it existed (our record),
    # never the Google account id it pointed at
    out["connections"] = [{"platform": c.get("platform"), "status": c.get("status")}
                          if c.get("platform") in GOOGLE_PLATFORMS else c
                          for c in export.get("connections") or []]
    out["activity"] = [a for a in export.get("activity") or [] if activity_archivable(a)]
    # Only the client's own words; every reply of ours may quote Google data
    out["communications"] = [m for m in export.get("communications") or []
                             if m.get("direction") == "inbound"]
    out["google_data_excluded"] = True
    return out


def backup_safe_extra(extra: dict) -> dict:
    out = {k: v for k, v in (extra or {}).items() if k not in BACKUP_GOOGLE_DERIVED}
    out["google_derived_tables_excluded"] = list(BACKUP_GOOGLE_DERIVED)
    return out


# ─── Live rows at closure ────────────────────────────────────────────────────

def _missing_table(error: Exception) -> bool:
    text = str(error)
    return any(m in text for m in ("PGRST205", "42P01", "Could not find the table", "does not exist"))


def purge_live_rows(client_id: int) -> dict:
    """Closure only: delete the client's Google-bearing live rows now, so they
    never depend on the Drive archive succeeding (the archive purge removes
    the rest later). {table: deleted | "missing" | "error:<type>"}."""
    db, done = _db(), {}
    try:
        rows, _truncated = _paged(lambda: db.table("client_activity").select("id,agent_name,action_type")
                                  .eq("client_id", client_id).order("created_at"))
        doomed = [r["id"] for r in rows if not activity_archivable(r)]
        for start in range(0, len(doomed), 200):
            db.table("client_activity").delete().in_("id", doomed[start:start + 200]).execute()
        done["client_activity"] = len(doomed)
    except Exception as e:
        done["client_activity"] = f"error:{type(e).__name__}"
    try:
        db.table("client_communications").delete().eq("client_id", client_id).eq(
            "direction", "outbound").execute()
        done["client_communications"] = "outbound"
    except Exception as e:
        done["client_communications"] = f"error:{type(e).__name__}"
    for table in LIVE_GOOGLE_DERIVED_TABLES:
        try:
            db.table(table).delete().eq("client_id", client_id).execute()
            done[table] = "deleted"
        except Exception as e:
            done[table] = "missing" if _missing_table(e) else f"error:{type(e).__name__}"
    # The Google sign-in identifier (clients.google_sub) — the closed account
    # is hard-locked anyway, so its credentials go now, not after the archive
    from core import client_auth
    client_auth.wipe_credentials(client_id)
    done["google_sub"] = "wiped"
    return done


def verify_live_rows(client_id: int) -> dict:
    """{table: n} of Google-bearing live rows still present after purge_live_rows."""
    db, left = _db(), {}
    try:
        rows, _t = _paged(lambda: db.table("client_activity").select("agent_name,action_type")
                          .eq("client_id", client_id).order("created_at"))
        n = sum(1 for r in rows if not activity_archivable(r))
        if n:
            left["client_activity"] = n
    except Exception:
        left["client_activity"] = "unverified"
    try:
        n = len(db.table("client_communications").select("client_id").eq("client_id", client_id)
                .eq("direction", "outbound").limit(PAGE_ROWS).execute().data or [])
        if n:
            left["client_communications"] = n
    except Exception:
        left["client_communications"] = "unverified"
    for table in LIVE_GOOGLE_DERIVED_TABLES:
        try:
            n = len(db.table(table).select("client_id").eq("client_id", client_id)
                    .limit(PAGE_ROWS).execute().data or [])
            if n:
                left[table] = n
        except Exception as e:
            if not _missing_table(e):
                left[table] = "unverified"
    return left


# ─── weekly_reports + alerts: the rows with no client_id column ──────────────

def _paged(make_query) -> tuple:
    rows, start = [], 0
    for _ in range(MAX_PAGES):
        batch = make_query().range(start, start + PAGE_ROWS - 1).execute().data or []
        rows += batch
        if len(batch) < PAGE_ROWS:
            return rows, False
        start += PAGE_ROWS
    return rows, True


def _patterns(ids: list) -> dict:
    from core.control_room import _client_pattern
    return {int(cid): _client_pattern(int(cid)) for cid in ids}


def _report_matches(report: dict, ids: set) -> list:
    return [e for e in (report or {}).get("clients") or []
            if str(e.get("client_id")).isdigit() and int(e["client_id"]) in ids]


def _scrubbed_report(report: dict, ids: set) -> dict:
    entries = report.get("clients") or []
    gone = [e for e in entries if e in _report_matches(report, ids)]
    kept = [e for e in entries if e not in gone]
    # Exact lines first ("<name>: <line>" from the entry's own lists); the bare
    # "<name>: " prefix only when no remaining client shares that name
    exact, prefixes = set(), []
    kept_names = {e.get("client_name") for e in kept}
    for e in gone:
        name = e.get("client_name") or ""
        for key in ("highlights", "recommendations"):
            exact |= {f"{name}: {line}" for line in e.get(key) or []}
        if name and name not in kept_names:
            prefixes.append(f"{name}: ")

    def keep(line) -> bool:
        line = str(line)
        return line not in exact and not any(line.startswith(p) for p in prefixes)

    out = dict(report)
    out["clients"] = kept
    out["highlights"] = [h for h in report.get("highlights") or [] if keep(h)]
    out["recommendations"] = [r for r in report.get("recommendations") or [] if keep(r)]
    out["removed_client_ids"] = sorted(set(report.get("removed_client_ids") or [])
                                       | {int(e["client_id"]) for e in gone})
    return out


def _alert_owner(alert: dict, patterns: dict):
    issues = alert.get("issues") or []
    text = issues if isinstance(issues, str) else " ".join(str(i) for i in issues)
    return next((cid for cid, p in patterns.items() if p.search(text)), None)


def _scan(ids: list, started_at: str) -> dict:
    """What still holds these clients' data: {"reports": [(row, report)],
    "alerts": [(row, cid)], "truncated": [table]}."""
    from core.bulk_delete import AUDIT_SOURCES
    id_set, patterns, db = {int(i) for i in ids}, _patterns(ids), _db()
    found = {"reports": [], "alerts": [], "truncated": []}

    reports, truncated = _paged(lambda: db.table("weekly_reports").select("*").order("created_at"))
    if truncated:
        found["truncated"].append("weekly_reports")
    for row in reports:
        report = row.get("report") or {}
        if isinstance(report, str):
            try:
                report = json.loads(report)
            except ValueError:
                continue
        if _report_matches(report, id_set):
            found["reports"].append((row, report))

    alerts, truncated = _paged(lambda: db.table("alerts").select("id,source,issues,created_at")
                               .order("created_at"))
    if truncated:
        found["truncated"].append("alerts")
    for row in alerts:
        if row.get("source") in AUDIT_SOURCES or str(row.get("created_at") or "") >= started_at:
            continue
        cid = _alert_owner(row, patterns)
        if cid is not None:
            found["alerts"].append((row, cid))
    return found


def _scrub_history_file(patterns: dict) -> int:
    from agents.master_agent import ALERT_HISTORY_PATH
    try:
        with open(ALERT_HISTORY_PATH, "r", encoding="utf-8") as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return 0
    kept = [h for h in history if _alert_owner(h, patterns) is None]
    if len(kept) != len(history):
        with open(ALERT_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(kept, f, ensure_ascii=False, indent=2)
    return len(history) - len(kept)


def scrub(ids: list, started_at: str) -> dict:
    """Remove these clients from weekly_reports and delete their alerts.
    {cid: {"weekly_reports": n, "alerts": n}}. Raises on a database error —
    the callers record it as a failed step and verify() reports what is left."""
    ids = [int(i) for i in ids]
    id_set, db = set(ids), _db()
    found = _scan(ids, started_at)
    counts = {cid: {"weekly_reports": 0, "alerts": 0} for cid in ids}

    for row, report in found["reports"]:
        for e in _report_matches(report, id_set):
            counts[int(e["client_id"])]["weekly_reports"] += 1
        query = db.table("weekly_reports").update({"report": _scrubbed_report(report, id_set)})
        query = query.eq("id", row["id"]) if row.get("id") is not None else query.eq("created_at", row["created_at"])
        query.execute()

    alert_ids = [row["id"] for row, _cid in found["alerts"]]
    for start in range(0, len(alert_ids), 200):
        db.table("alerts").delete().in_("id", alert_ids[start:start + 200]).execute()
    for _row, cid in found["alerts"]:
        counts[cid]["alerts"] += 1

    try:
        _scrub_history_file(_patterns(ids))
    except Exception as e:
        print(f"[{SERVICE_NAME}] local alert history not scrubbed ({type(e).__name__})")
    return counts


def verify(ids: list, started_at: str) -> tuple:
    """({cid: {"weekly_reports": n, "alerts": n}} still present, [unverifiable tables])."""
    ids = [int(i) for i in ids]
    try:
        found = _scan(ids, started_at)
    except Exception:
        return {}, ["weekly_reports", "alerts"]
    left = {}
    for _row, report in found["reports"]:
        for e in _report_matches(report, set(ids)):
            cid = int(e["client_id"])
            left.setdefault(cid, {})["weekly_reports"] = left.get(cid, {}).get("weekly_reports", 0) + 1
    for _row, cid in found["alerts"]:
        left.setdefault(cid, {})["alerts"] = left.get(cid, {}).get("alerts", 0) + 1
    return left, found["truncated"]


def scrub_and_verify(ids: list, started_at: str) -> tuple:
    """scrub → verify → once more for whatever is left → verify.
    Returns (removed, leftovers, unverified, error_or_None)."""
    removed, error = {}, None
    try:
        removed = scrub(ids, started_at)
    except Exception as e:
        error = e
    left, unverified = verify(ids, started_at)
    if left:
        try:
            scrub(list(left), started_at)
        except Exception as e:
            error = error or e
        left, unverified = verify(ids, started_at)
    if not left and not unverified:
        error = None   # whatever failed, the final check found nothing left
    return removed, left, unverified, error


# ─── Closure: everything in one call ─────────────────────────────────────────

def end_of_client(client_id: int, started_at: str) -> dict:
    """api_server._offboard_client's Google step: live Google-bearing rows,
    weekly reports and alerts, each verified. Never raises; the caller alerts
    on anything left."""
    try:
        live = purge_live_rows(client_id)
    except Exception as e:
        live = {"error": type(e).__name__}
    removed, left, unverified, error = scrub_and_verify([client_id], started_at)
    leftovers = dict(left.get(int(client_id)) or {})
    try:
        leftovers.update(verify_live_rows(client_id))
    except Exception:
        unverified = list(unverified) + ["live_rows"]
    return {"live": live, "removed": removed.get(int(client_id)) or {},
            "leftovers": leftovers, "unverified": list(unverified),
            "error": type(error).__name__ if error else None}
