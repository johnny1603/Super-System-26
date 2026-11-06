"""Admin side of uallak-hosted landing pages (docs/LANDING_PAGES.md §7-9).

- `overview()`: every client's pages, status, live URL, 30-day numbers, and
  what each page and each client has COST us (ILS) — AI generation from
  client_costs rows labelled action_ref "lp:<page id>", hosting from the
  monthly estimate below. The numbers packages and add-ons get priced from.
- `unpublish()`: instant takedown (the next visit answers 404), audited.
- Hosting cost: no provider bill splits per page, so it is an ESTIMATE —
  last month's served bytes and requests per page × rates the owner sets on
  the admin page (app_settings `landing_hosting_rates`). Recorded once per
  month per page into client_costs (category landing_hosting, meter infra,
  details.estimate = true). Unset rates → nothing is recorded, and the page
  says "not set", never ₪0.
- `backup()` / `migrate()`: the move to versioned pages. Backup first (JSON +
  sha256, every page and its leads count, no secrets); migrate refuses
  without the matching backup hash, then gives every page that has no
  versions a version 1 equal to what is live today, and points
  published_version at it. Idempotent; deletes nothing.

Old tool: InstaWP was never used for landing pages (Step 0 inventory) — there
is nothing to migrate from it and nothing of it is removed here.
"""
import hashlib
import json
from datetime import date, datetime, timezone

from core import landing_pages, landing_tracking

SERVICE_NAME = "landing_admin"
RATES_KEY = "landing_hosting_rates"
HOSTING_DONE_PREFIX = "landing_hosting:"
BACKUP_KEY = "landing_backup:last"


def _db():
    return landing_pages._db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _setting(key: str):
    try:
        rows = _db().table("app_settings").select("value").eq("key", key).limit(1).execute().data or []
        return rows[0].get("value") if rows else None
    except Exception:
        return None


def _put_setting(key: str, value):
    _db().table("app_settings").upsert({"key": key, "value": value, "updated_at": _now()},
                                       on_conflict="key").execute()


def _audit(message: str):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, [message])


# ─── Costs ───────────────────────────────────────────────────────────────────

def costs_by_page() -> dict:
    """{page_id: {"ai_ils": x, "hosting_ils": y}} from client_costs."""
    out = {}
    try:
        rows = (_db().table("client_costs").select("id, amount, category, details")
                .in_("category", ["claude_landing_page", "landing_hosting"]).limit(20000).execute().data or [])
    except Exception:
        rows = []
    # Generation also triggers research calls under the same label
    try:
        rows += (_db().table("client_costs").select("id, amount, category, details")
                 .eq("details->>action", "landing_page_generate").limit(20000).execute().data or [])
    except Exception:
        pass
    seen = set()
    for row in rows:
        details = row.get("details") or {}
        ref = str(details.get("action_ref") or "")
        if not ref.startswith("lp:"):
            continue
        if row.get("id") in seen:
            continue
        seen.add(row.get("id"))
        try:
            page_id = int(ref[3:])
        except ValueError:
            continue
        acc = out.setdefault(page_id, {"ai_ils": 0.0, "hosting_ils": 0.0})
        bucket = "hosting_ils" if row.get("category") == "landing_hosting" else "ai_ils"
        acc[bucket] = round(acc[bucket] + float(row.get("amount") or 0), 4)
    return out


def rates() -> dict:
    value = _setting(RATES_KEY)
    value = value if isinstance(value, dict) else {}
    out = {}
    for key in ("ils_per_gb", "ils_per_10k_requests"):
        try:
            number = float(value.get(key))
            if number >= 0:
                out[key] = number
        except (TypeError, ValueError):
            pass
    return out


def set_rates(ils_per_gb, ils_per_10k_requests) -> dict:
    clean = {}
    for key, raw in (("ils_per_gb", ils_per_gb), ("ils_per_10k_requests", ils_per_10k_requests)):
        number = float(raw)
        if number < 0 or number > 1000:
            raise ValueError(key)
        clean[key] = number
    _put_setting(RATES_KEY, {**clean, "set_at": _now()})
    return clean


def record_hosting(month: str = None) -> dict:
    """Last month's hosting estimate per page into client_costs, once. Rows
    carry the inputs (views, bytes, rates) so the number can be re-derived."""
    from core.cost_tracker import cost_action, record_cost
    today = date.today()
    if not month:
        first = today.replace(day=1)
        prev = date(first.year - (first.month == 1), (first.month - 2) % 12 + 1, 1)
        month = prev.strftime("%Y-%m")
    current = rates()
    if len(current) < 2:
        return {"recorded": 0, "month": month, "reason": "rates_not_set"}
    marker = f"{HOSTING_DONE_PREFIX}{month}"
    if _setting(marker):
        return {"recorded": 0, "month": month, "reason": "already_recorded"}
    year, mon = (int(x) for x in month.split("-"))
    start = date(year, mon, 1)
    end = date(year + (mon == 12), mon % 12 + 1, 1)
    rows = (_db().table("landing_page_stats").select("page_id, client_id, views, calls, whatsapp, store, bytes")
            .gte("day", start.isoformat()).lt("day", end.isoformat()).limit(50000).execute().data or [])
    per = {}
    for row in rows:
        acc = per.setdefault((row["page_id"], row["client_id"]), {"requests": 0, "bytes": 0})
        acc["requests"] += sum(int(row.get(k) or 0) for k in ("views", "calls", "whatsapp", "store"))
        acc["bytes"] += int(row.get("bytes") or 0)
    recorded = 0
    for (page_id, client_id), acc in per.items():
        ils = round(acc["bytes"] / 1e9 * current["ils_per_gb"]
                    + acc["requests"] / 10_000 * current["ils_per_10k_requests"], 6)
        if ils <= 0:
            continue
        with cost_action("landing_page_hosting", ref=f"lp:{page_id}"):
            record_cost("landing_hosting", ils, int(client_id),
                        {"estimate": True, "month": month, "requests": acc["requests"],
                         "bytes": acc["bytes"], "rates": current}, cost_category="infra")
        recorded += 1
    _put_setting(marker, {"recorded": recorded, "at": _now()})
    return {"recorded": recorded, "month": month}


# ─── Designed-page generations (core/landing_creative) ──────────────────────

GENERATION_FIELDS = ("outcome", "model", "input_tokens", "output_tokens", "cache_read_tokens",
                     "cache_write_tokens", "cost_ils", "duration_s", "retries", "version", "content_hash",
                     "html_bytes", "exemplar", "stop_reason", "error")


def generations_by_page(limit: int = 3000) -> dict:
    """{page_id: {"count", "ok", "failed", "cost_ils", "latest": {...}}} from
    the measurement rows every generation writes (client_activity,
    action_type landing_generation). Measure first, restrict later."""
    try:
        rows = (_db().table("client_activity").select("client_id, details, created_at")
                .eq("action_type", "landing_generation").order("created_at", desc=True)
                .limit(limit).execute().data or [])
    except Exception:
        return {}
    out = {}
    for row in rows:
        details = row.get("details") or {}
        try:
            page_id = int(details.get("page_id"))
        except (TypeError, ValueError):
            continue
        acc = out.setdefault(page_id, {"count": 0, "ok": 0, "failed": 0, "cost_ils": 0.0, "latest": None})
        acc["count"] += 1
        acc["ok" if details.get("outcome") == "ok" else "failed"] += 1
        acc["cost_ils"] = round(acc["cost_ils"] + float(details.get("cost_ils") or 0), 4)
        if acc["latest"] is None:   # rows are newest first
            acc["latest"] = {**{k: details.get(k) for k in GENERATION_FIELDS}, "at": row.get("created_at")}
    return out


# ─── Overview + takedown ─────────────────────────────────────────────────────

def overview() -> dict:
    pages = (_db().table("landing_pages").select("id, client_id, slug, title, status, current_version, "
                                                 "published_version, published_at, updated_at, created_at")
             .order("client_id").limit(5000).execute().data or [])
    names = {}
    try:
        ids = sorted({p["client_id"] for p in pages})
        if ids:
            for row in (_db().table("clients").select("id, business_name").in_("id", ids).execute().data or []):
                names[row["id"]] = row.get("business_name") or ""
    except Exception:
        pass
    stats = landing_tracking.all_stats(days=30)
    costs = costs_by_page()
    generations = generations_by_page()
    from agents.landing_page_agent import page_url
    from core import landing_domains
    states, clients = {}, {}
    out = []
    for page in pages:
        cid = page["client_id"]
        if cid not in states:
            try:
                states[cid] = landing_domains.get_state(cid)
            except Exception:
                states[cid] = {"mode": "shared", "hostname": ""}
        cost = costs.get(page["id"], {"ai_ils": 0.0, "hosting_ils": 0.0})
        numbers = stats.get(page["id"]) or {}
        out.append({**page, "business_name": names.get(cid, ""),
                    "url": page_url(cid, page, names.get(cid, ""), states[cid]),
                    "views_30d": numbers.get("views", 0), "calls_30d": numbers.get("calls", 0),
                    "whatsapp_30d": numbers.get("whatsapp", 0), "bytes_30d": numbers.get("bytes", 0),
                    "cost_ai_ils": cost["ai_ils"], "cost_hosting_ils": cost["hosting_ils"],
                    "generations": generations.get(page["id"])})
        acc = clients.setdefault(cid, {"client_id": cid, "business_name": names.get(cid, ""), "pages": 0,
                                       "live": 0, "cost_ai_ils": 0.0, "cost_hosting_ils": 0.0})
        acc["pages"] += 1
        acc["live"] += page.get("status") == "published"
        acc["cost_ai_ils"] = round(acc["cost_ai_ils"] + cost["ai_ils"], 4)
        acc["cost_hosting_ils"] = round(acc["cost_hosting_ils"] + cost["hosting_ils"], 4)
    for acc in clients.values():
        acc["near_limit"] = acc["live"] >= landing_pages.MAX_ACTIVE_PAGES
    return {"pages": out, "clients": sorted(clients.values(), key=lambda c: c["client_id"]),
            "limit": landing_pages.MAX_ACTIVE_PAGES, "rates": rates(),
            "counters_on": landing_tracking._stats_ok["value"],
            "versions_on": landing_pages._versions_ok["value"],
            "creative_on": _creative_on()}


def _creative_on() -> bool:
    from core import landing_creative
    return landing_creative.enabled()


def unpublish(client_id: int, page_id: int, reason: str = "") -> dict:
    from agents.landing_page_agent import unpublish_page
    result = unpublish_page(client_id, page_id, by="admin", reason=reason or "admin")
    if result.get("success"):
        _audit(f"client {client_id}: landing page {page_id} taken down by admin ({reason[:100] or 'no reason'})")
    return result


# ─── Backup + migration to versions ──────────────────────────────────────────

def backup() -> dict:
    """Every page row + its versions, as JSON, with its sha256. Stored in
    app_settings (the last one) and returned for download."""
    pages = _db().table("landing_pages").select("*").order("id").limit(10000).execute().data or []
    try:
        versions = (_db().table("landing_page_versions").select("*").order("id")
                    .limit(50000).execute().data or [])
    except Exception:
        versions = []
    counts = {}
    for page in pages:
        try:
            counts[page["id"]] = landing_pages.lead_counts_by_page(page["client_id"]).get(page["slug"], 0)
        except Exception:
            counts[page["id"]] = None
    body = json.dumps({"taken_at": _now(), "pages": pages, "versions": versions, "lead_counts": counts},
                      ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    _put_setting(BACKUP_KEY, {"sha256": digest, "pages": len(pages), "taken_at": _now(), "body": body})
    return {"sha256": digest, "pages": len(pages), "body": body}


def migrate(backup_sha: str, client_id: int = None) -> dict:
    """Version 1 for every page without versions (optionally one client).
    Refuses unless `backup_sha` matches the stored backup and that backup
    still verifies."""
    stored = _setting(BACKUP_KEY) or {}
    body = stored.get("body") or ""
    if not backup_sha or backup_sha != stored.get("sha256") \
            or hashlib.sha256(body.encode("utf-8")).hexdigest() != backup_sha:
        return {"success": False, "code": "backup_required"}
    query = _db().table("landing_pages").select("*").order("id").limit(10000)
    if client_id:
        query = query.eq("client_id", int(client_id))
    pages = query.execute().data or []
    migrated, skipped = [], []
    for page in pages:
        if landing_pages.list_versions(page["client_id"], page["id"]):
            skipped.append(page["id"])
            continue
        if not landing_pages._versions_ok["value"]:
            return {"success": False, "code": "migration_sql_not_run"}
        visible = {"title": page.get("title", ""), "content": landing_pages.normalize_content(page.get("content") or {}),
                   "contact": landing_pages.normalize_contact(page.get("contact") or {})}
        number = landing_pages._add_version(page["client_id"], page["id"], visible, "migrated",
                                            landing_pages.page_size({**page, **visible}))
        if not number:
            return {"success": False, "code": "migration_sql_not_run"}
        changes = {"current_version": number}
        if page.get("status") == "published":
            changes.update(published_version=number, published_at=page.get("updated_at") or _now())
        _db().table("landing_pages").update(changes).eq("id", page["id"]).eq("client_id", page["client_id"]).execute()
        migrated.append(page["id"])
    _audit(f"landing pages migrated to versions: {len(migrated)} (skipped {len(skipped)}), backup {backup_sha[:12]}")
    return {"success": True, "migrated": migrated, "skipped": skipped}
