"""
The media library ("מאגר המדיה", docs/MEDIA-LIBRARY.md): every media item
the system knows about for one client, in one list, with full delete.

Sources (all read live, nothing copied):
  - our media folder (drive_mirror.catalog, the whole tree): generated images
    and video (media_agent activity rows name their file ids), rendered text
    slides (social_queue source "slide", or the renderer's "-vN.png" name),
    real assets the agents copied in, and anything else put there (uploaded);
  - the client's connected Drive folders (core/drive_assets index).

Usage comes from the Social queue (posts; a copied real asset also counts for
its source file) and the current content plan (proposals): used / unused /
rejected (every post that used it was rejected).

Two delete modes, both for any item in any folder (no role restriction):
  - "index": the item leaves the index, agents stop using it, the file stays.
    Media-folder items go into the `removed` set that drive_mirror.catalog
    filters out; connected-folder items get state "removed" (the scan keeps
    them out).
  - "file": the file is deleted from Drive (permanent delete, or the trash when
    Drive refuses a permanent delete — a Shared Drive Content manager may only
    trash) and leaves the index.
Deleting an item used by a waiting/approved post is allowed; the preview
lists those posts. Every deletion is logged (who, when, what, count, size,
mode) — app_settings `media_deletions:<id>`, shown in the Control Room.

Retention (per client, OFF by default): when the manager switches it on with
N days, the daily drive-assets-scan run deletes (mode "file") rejected
generated media older than N days. Settings + the removed set:
app_settings `media_library:<id>`.
"""

import re
import threading
from collections import OrderedDict
import time
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "media_inventory"
PREFIX = "media_library:"
LOG_PREFIX = "media_deletions:"
SOURCES = ("generated_image", "generated_video", "slide", "real_copy", "uploaded", "client_asset")
GENERATED = ("generated_image", "generated_video", "slide")
USAGES = ("used", "unused", "rejected")
MODES = ("index", "file")
SORTS = ("newest", "oldest", "largest", "smallest")
PENDING_STATUSES = ("waiting", "approved")
PAGE = 60
REMOVED_CACHE_S = 60
_SLIDE_NAME = re.compile(r"-v\d+\.png$", re.I)
_removed_cache = {}          # client_id -> (at, set of file ids)
_lock = threading.Lock()


def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt=None) -> str:
    return (dt or _now()).isoformat()


def _parse(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _log(line: str):
    print(f"[{SERVICE_NAME}] {line}")


# ─── Storage ─────────────────────────────────────────────────────────────────

def _read(key: str) -> dict:
    rows = _db().table("app_settings").select("value").eq("key", key).limit(1).execute().data or []
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _write(key: str, value: dict):
    _db().table("app_settings").upsert({"key": key, "value": value, "updated_at": _iso()},
                                       on_conflict="key").execute()


def _state(client_id: int) -> dict:
    value = _read(f"{PREFIX}{int(client_id)}")
    value.setdefault("removed", {})
    value.setdefault("retention_enabled", False)
    value.setdefault("retention_days", None)
    value.setdefault("unused_days", None)
    return value


def settings(client_id: int) -> dict:
    s = _state(client_id)
    return {"retention_enabled": bool(s.get("retention_enabled")), "retention_days": s.get("retention_days"),
            "unused_days": s.get("unused_days")}


def _days(value):
    if value in (None, ""):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError("days_not_a_number")
    if n < 0:
        raise ValueError("days_not_a_number")
    return n


def save_settings(client_id: int, retention_enabled=None, retention_days="keep", unused_days="keep") -> dict:
    """The manager's settings. Retention can be on only with a day count."""
    s = _state(client_id)
    if retention_days != "keep":
        s["retention_days"] = _days(retention_days)
    if unused_days != "keep":
        s["unused_days"] = _days(unused_days)
    if retention_enabled is not None:
        s["retention_enabled"] = bool(retention_enabled)
    if s["retention_enabled"] and s.get("retention_days") is None:
        raise ValueError("retention_days_required")
    _write(f"{PREFIX}{int(client_id)}", s)
    return settings(client_id)


def removed_ids(client_id: int) -> set:
    """Media-folder file ids removed from the index (drive_mirror.catalog
    leaves them out). Empty when unreadable — never blocks a listing."""
    hit = _removed_cache.get(int(client_id))
    if hit and hit[0] > time.time() - REMOVED_CACHE_S:
        return hit[1]
    try:
        ids = set(_state(client_id).get("removed") or {})
    except Exception as e:
        _log(f"client {client_id}: removed set unreadable ({type(e).__name__})")
        return set()
    _removed_cache[int(client_id)] = (time.time(), ids)
    return ids


# ─── The inventory ───────────────────────────────────────────────────────────

def _generated_ids(client_id: int) -> dict:
    """file id -> source, from media_agent's own activity rows."""
    out = {}
    try:
        rows = (_db().table("client_activity").select("action_type,details,result").eq("client_id", client_id)
                .in_("action_type", ["media_image_created", "media_video_created",
                                     "media_image_real_asset", "media_video_real_asset"])
                .limit(5000).execute().data or [])
    except Exception as e:
        _log(f"client {client_id}: activity unreadable ({type(e).__name__})")
        return out
    for r in rows:
        fid = (r.get("result") or {}).get("file_id") or (r.get("details") or {}).get("file_id")
        if not fid:
            continue
        kind = r.get("action_type", "")
        out[fid] = ("real_copy" if kind.endswith("_real_asset")
                    else "generated_video" if "video" in kind else "generated_image")
    return out


def _usage_map(client_id: int) -> dict:
    """file id -> [{"kind": "post"|"proposal", "id", "title", "status", "planned_date"}]."""
    refs = {}
    try:
        from core import social_queue
        for q in social_queue.items(client_id):
            ref = {"kind": "post", "id": q.get("id", ""), "title": q.get("title", ""),
                   "status": q.get("status", ""), "planned_date": q.get("planned_date", ""),
                   "source": q.get("source", "")}
            for fid in {q.get("file_id"), (q.get("real_asset") or {}).get("source_file_id")}:
                if fid:
                    refs.setdefault(fid, []).append(ref)
    except Exception as e:
        _log(f"client {client_id}: social queue unreadable ({type(e).__name__})")
    try:
        from core import content_team
        plan = content_team.latest(client_id) or {}
        for i, item in enumerate(plan.get("items") or []):
            if item.get("file_id"):
                refs.setdefault(item["file_id"], []).append(
                    {"kind": "proposal", "id": f"{plan.get('id', '')}:{i}", "title": item.get("title", ""),
                     "status": item.get("status", ""), "planned_date": ""})
    except Exception as e:
        _log(f"client {client_id}: content plan unreadable ({type(e).__name__})")
    return refs


def _usage(refs: list) -> str:
    if not refs:
        return "unused"
    posts = [r for r in refs if r["kind"] == "post"]
    if posts and all(r["status"] == "rejected" for r in posts):
        return "rejected"
    return "used"


def _fmt(name: str, mime: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    return ext[:8] or (mime.split("/")[-1][:12] if mime else "")


def inventory(client_id: int, refresh: bool = False) -> list:
    """Every media item the system knows about for the client, newest first."""
    from core import drive_mirror, drive_assets, drive_folders
    usage = _usage_map(client_id)
    generated = _generated_ids(client_id)
    slides = {fid for fid, refs in usage.items() if any(r.get("source") == "slide" for r in refs)}
    out = []
    try:
        cat = drive_mirror.catalog(client_id, refresh=refresh)
    except Exception as e:
        _log(f"client {client_id}: media folder unreadable ({type(e).__name__})")
        cat = {"items": []}
    for f in cat.get("items") or []:
        if f.get("kind") in ("folder", "shortcut"):
            continue
        fid = f.get("id", "")
        source = ("slide" if fid in slides or (f.get("kind") == "image" and _SLIDE_NAME.search(f.get("name", "")))
                  else generated.get(fid) or "uploaded")
        path = f.get("folder", "")
        keys = ["media:"]
        parts = [p for p in path.split("/") if p]
        keys += ["media:" + "/".join(parts[:n]) for n in range(1, len(parts) + 1)]
        out.append({"id": fid, "name": f.get("name", ""), "origin": "media", "source": source,
                    "kind": f.get("kind", ""), "mime": f.get("mime_type", ""),
                    "format": _fmt(f.get("name", ""), f.get("mime_type", "")),
                    "folder": path, "folder_key": "media:" + path, "folder_keys": keys,
                    "created": f.get("created_time", ""), "size": int(f.get("size") or 0),
                    "description": "", "tags": [], "web_view_link": f.get("web_view_link", "")})
    try:
        state = drive_assets._load(client_id)
        conns = {c["id"]: c for c in drive_folders.load(client_id)}
    except Exception as e:
        _log(f"client {client_id}: connected folders unreadable ({type(e).__name__})")
        state, conns = {"items": {}}, {}
    for item in (state.get("items") or {}).values():
        if item.get("state") in drive_assets.OUT_STATES:     # removed, or gone when an agent went to use it
            continue
        conn = conns.get(item.get("connection_id")) or {}
        root = conns.get(item.get("root_id")) or {}
        path = item.get("path") or conn.get("display_name", "")
        out.append({"id": item.get("file_id", ""), "name": item.get("name", ""), "origin": "connected",
                    "source": "client_asset", "kind": item.get("media_type", ""), "mime": item.get("mime", ""),
                    "format": item.get("format") or _fmt(item.get("name", ""), item.get("mime", "")),
                    "folder": path, "folder_key": "conn:" + str(item.get("connection_id", "")),
                    "folder_keys": ["conn:" + str(item.get("connection_id", "")), "conn:" + str(item.get("root_id", ""))],
                    "root_folder": root.get("display_name", ""), "connection_id": item.get("connection_id", ""),
                    "created": item.get("created") or item.get("modified", ""), "size": int(item.get("size") or 0),
                    "description": item.get("description", ""), "tags": list(item.get("tags") or [])})
    for row in out:
        refs = usage.get(row["id"], [])
        row["usage"] = _usage(refs)
        row["used_in"] = [{k: r[k] for k in ("kind", "id", "title", "status", "planned_date")} for r in refs]
    out.sort(key=lambda r: r["created"] or "", reverse=True)
    return out


def folders(items: list) -> list:
    """The folder filter's choices: every folder key with its count and size."""
    agg = {}
    for i in items:
        for key in set(i["folder_keys"]):
            rec = agg.setdefault(key, {"key": key, "count": 0, "size": 0,
                                       "origin": "media" if key.startswith("media:") else "connected",
                                       "label": key[6:] if key.startswith("media:") else ""})
            rec["count"] += 1
            rec["size"] += i["size"]
            if key == i["folder_key"] and not rec["label"]:
                rec["label"] = i["folder"]
            if key.startswith("conn:") and key != i["folder_key"] and not rec["label"]:
                rec["label"] = i.get("root_folder", "")
    return sorted(agg.values(), key=lambda r: (r["origin"], r["label"]))


def summary(items: list) -> dict:
    by = {s: {"count": 0, "size": 0} for s in SOURCES}
    for i in items:
        by[i["source"]]["count"] += 1
        by[i["source"]]["size"] += i["size"]
    return {"count": len(items), "size": sum(i["size"] for i in items), "by_source": by}


# ─── Filters ─────────────────────────────────────────────────────────────────

def _num(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def clean_filter(raw: dict) -> dict:
    raw = raw or {}
    f = {"source": [s for s in _list(raw.get("source")) if s in SOURCES],
         "folder": str(raw.get("folder") or "")[:600],
         "usage": [u for u in _list(raw.get("usage")) if u in USAGES],
         "date_from": str(raw.get("date_from") or "")[:10], "date_to": str(raw.get("date_to") or "")[:10],
         "size_min_mb": _num(raw.get("size_min_mb")), "size_max_mb": _num(raw.get("size_max_mb")),
         "older_than_days": _num(raw.get("older_than_days")),
         "q": str(raw.get("q") or "").strip()[:200]}
    return f


def _list(value) -> list:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [v for v in str(value or "").split(",") if v]


def matches(item: dict, f: dict, now: datetime = None) -> bool:
    if f["source"] and item["source"] not in f["source"]:
        return False
    if f["folder"] and f["folder"] not in item["folder_keys"]:
        return False
    if f["usage"] and item["usage"] not in f["usage"]:
        return False
    day = (item["created"] or "")[:10]
    if f["date_from"] and (not day or day < f["date_from"]):
        return False
    if f["date_to"] and (not day or day > f["date_to"]):
        return False
    mb = item["size"] / (1024 * 1024)
    if f["size_min_mb"] is not None and mb < f["size_min_mb"]:
        return False
    if f["size_max_mb"] is not None and mb > f["size_max_mb"]:
        return False
    if f["older_than_days"] is not None:
        created = _parse(item["created"])
        if not created or created > (now or _now()) - timedelta(days=f["older_than_days"]):
            return False
    if f["q"]:
        hay = " ".join([item["name"], item["description"], " ".join(item["tags"])]).lower()
        if not all(w in hay for w in f["q"].lower().split()):
            return False
    return True


def _sorted(items: list, sort: str) -> list:
    if sort == "oldest":
        return sorted(items, key=lambda r: r["created"] or "")
    if sort == "largest":
        return sorted(items, key=lambda r: r["size"], reverse=True)
    if sort == "smallest":
        return sorted(items, key=lambda r: r["size"])
    return sorted(items, key=lambda r: r["created"] or "", reverse=True)


def view(client_id: int, raw_filter: dict = None, sort: str = "newest", offset: int = 0, limit: int = PAGE,
         refresh: bool = False) -> dict:
    items = inventory(client_id, refresh=refresh)
    f = clean_filter(raw_filter)
    hit = _sorted([i for i in items if matches(i, f)], sort if sort in SORTS else "newest")
    offset, limit = max(0, int(offset or 0)), max(1, int(limit or PAGE))
    return {"summary": summary(items), "folders": folders(items), "settings": settings(client_id),
            "matching": {"count": len(hit), "size": sum(i["size"] for i in hit)},
            "items": [_public(i) for i in hit[offset:offset + limit]], "offset": offset,
            "has_more": offset + limit < len(hit), "filter": f}


def _public(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "folder_keys"}


# ─── Preview + delete ────────────────────────────────────────────────────────

def select(client_id: int, ids: list = None, raw_filter: dict = None, items: list = None) -> tuple:
    """(chosen items, ids not found). Explicit ids win; else every item the
    filter matches (the screen's "select all matching")."""
    items = items if items is not None else inventory(client_id, refresh=True)
    if ids:
        wanted = [str(i) for i in ids]
        by_id = {i["id"]: i for i in items}
        return [by_id[i] for i in dict.fromkeys(wanted) if i in by_id], [i for i in wanted if i not in by_id]
    if raw_filter is None:
        return [], []
    f = clean_filter(raw_filter)
    return [i for i in items if matches(i, f)], []


def describe(chosen: list) -> dict:
    """What a deletion will touch: count, size, folders, affected posts."""
    by_folder = {}
    for i in chosen:
        label = i["folder"]
        rec = by_folder.setdefault((i["origin"], label), {"origin": i["origin"], "label": label, "count": 0, "size": 0})
        rec["count"] += 1
        rec["size"] += i["size"]
    affected = {}
    for i in chosen:
        for r in i["used_in"]:
            if r["kind"] == "post" and r["status"] in PENDING_STATUSES:
                affected.setdefault(r["id"], {**r, "items": []})["items"].append(i["name"])
    return {"count": len(chosen), "size": sum(i["size"] for i in chosen),
            "folders": sorted(by_folder.values(), key=lambda r: -r["count"]),
            "affected_posts": list(affected.values())}


def preview(client_id: int, ids: list = None, raw_filter: dict = None, mode: str = "index") -> dict:
    chosen, missing = select(client_id, ids, raw_filter)
    return {**describe(chosen), "mode": mode if mode in MODES else "index", "missing": len(missing)}


def _delete_drive_file(client_id: int, item: dict) -> str:
    from core import drive_service, drive_storage
    if item["origin"] == "connected":
        with drive_storage.as_service_account():
            return drive_service.delete_file(item["id"])
    with drive_storage.scope(client_id):
        return drive_service.delete_file(item["id"])


# Why a file delete failed — a CODE; the screen shows it in plain words
# (LIB_T.reason_* in the client dashboard). The technical detail goes to the
# log and the deletion log entry only, never to the screen.
REASONS = ("owner_only", "no_permission", "not_found", "drive_unavailable", "unknown")


def _status(e: Exception):
    return getattr(getattr(e, "response", None), "status_code", None)


def _failure_reason(client_id: int, item: dict, e: Exception) -> str:
    status = _status(e)
    if status == 404:
        return "not_found"
    if status in (401, 403):
        # In a regular (non-shared) Drive only the OWNER may delete or trash a
        # file, whatever the sharing — say that when it is the case
        from core import drive_service, drive_storage
        try:
            ctx = drive_storage.as_service_account() if item.get("origin") == "connected" \
                else drive_storage.scope(client_id)
            with ctx:
                meta = drive_service.get_meta(item["id"], "id,ownedByMe,driveId")
            if not meta.get("driveId") and not meta.get("ownedByMe"):
                return "owner_only"
        except Exception:
            pass
        return "no_permission"
    if status in (429, 500, 502, 503, 504) or (status is None and type(e).__name__ in (
            "TimeoutException", "ConnectTimeout", "ReadTimeout", "ConnectError", "RequestError",
            "RemoteProtocolError", "NetworkError", "PoolTimeout")):
        return "drive_unavailable"
    return "unknown"


def delete(client_id: int, ids: list = None, raw_filter: dict = None, mode: str = "index", by: str = "client",
           items: list = None) -> dict:
    """Removes the chosen items (mode "index") or deletes their files too
    (mode "file"). Per-item outcome; one log entry for the whole action."""
    if mode not in MODES:
        raise ValueError("bad_mode")
    chosen, missing = select(client_id, ids, raw_filter, items=items)
    plan = describe(chosen)
    done, failed = [], []
    from core import drive_assets
    for item in chosen:
        how = "index"
        if mode == "file":
            try:
                how = _delete_drive_file(client_id, item)
            except Exception as e:
                reason = _failure_reason(client_id, item, e)
                detail = f"{type(e).__name__}: {str(e)[:300]}"
                failed.append({**item, "how": "not_deleted", "reason": reason, "detail": detail})
                _log(f"client {client_id}: delete {item['id']} failed ({reason}; {detail})")
                continue
        done.append({**item, "how": how})
    with _lock:
        if done or failed:
            s = _state(client_id)
            changed_state = False
            assets = None
            # A file whose delete FAILED still leaves the library (the agents
            # stop using it) — only the file itself stays in Drive
            for item in done + failed:
                if item["origin"] == "media":
                    if mode == "index" or item.get("how") == "not_deleted":
                        s["removed"][item["id"]] = {"at": _iso(), "by": by}
                        changed_state = True
                    else:
                        s["removed"].pop(item["id"], None)
                else:
                    assets = assets if assets is not None else drive_assets._load(client_id)
                    rec = assets["items"].get(item["id"])
                    if rec is None:
                        continue
                    if mode == "index" or item.get("how") == "not_deleted":
                        rec.update(state="removed", removed_at=_iso(), removed_by=by)
                    else:
                        assets["items"].pop(item["id"], None)
            if changed_state or mode == "file":
                _write(f"{PREFIX}{int(client_id)}", s)
            if assets is not None:
                drive_assets._save(client_id, assets)
            _removed_cache.pop(int(client_id), None)
        entry = {"at": _iso(), "by": by, "mode": mode, "count": len(done),
                 "size": sum(i["size"] for i in done), "failed": len(failed),
                 "folders": [{"label": f["label"], "origin": f["origin"]} for f in describe(done)["folders"]],
                 "affected_posts": [{"id": p["id"], "title": p["title"], "status": p["status"]}
                                    for p in describe(done)["affected_posts"]],
                 "items": [{"id": i["id"], "name": i["name"], "size": i["size"], "source": i["source"],
                            "folder": i["folder"], "how": i["how"]} for i in done],
                 # technical detail lives HERE (and in the server log), never on the screen
                 "failures": [{"id": i["id"], "name": i["name"], "folder": i["folder"], "reason": i["reason"],
                               "detail": i["detail"], "removed_from_library": True} for i in failed]}
        if done or failed:
            log = _read(f"{LOG_PREFIX}{int(client_id)}")
            log.setdefault("entries", []).append(entry)
            _write(f"{LOG_PREFIX}{int(client_id)}", log)
    _forget_caches(client_id)
    return {"done": len(done), "size": entry["size"],
            # file NOT deleted, item removed from the library; reason = a REASONS code
            "failed": [{"id": i["id"], "name": i["name"], "reason": i["reason"], "file_deleted": False,
                        "removed_from_library": True} for i in failed],
            "trashed": sum(1 for i in done if i["how"] == "trashed"), "missing": len(missing),
            "affected_posts": plan["affected_posts"], "mode": mode}


def _forget_caches(client_id: int):
    for name in ("drive_mirror", "media_library"):
        try:
            module = __import__(f"core.{name}", fromlist=["forget"])
            module.forget(client_id)
        except Exception:
            pass


def deletion_log(client_id: int) -> list:
    return list(reversed(_read(f"{LOG_PREFIX}{int(client_id)}").get("entries") or []))


# ─── Retention (OFF unless the manager switches it on) ───────────────────────

def retention_filter(days) -> dict:
    return {"source": list(GENERATED), "usage": ["rejected"], "older_than_days": days}


def run_retention_for(client_id: int) -> dict:
    s = settings(client_id)
    if not s["retention_enabled"] or s["retention_days"] is None:
        return {"client_id": client_id, "ran": False}
    out = delete(client_id, raw_filter=retention_filter(s["retention_days"]), mode="file", by="retention")
    return {"client_id": client_id, "ran": True, "deleted": out["done"], "failed": len(out["failed"])}


def run_retention() -> list:
    """The daily pass: only clients whose manager switched retention on."""
    try:
        rows = (_db().table("app_settings").select("key,value").like("key", f"{PREFIX}%")
                .limit(5000).execute().data or [])
    except Exception as e:
        _log(f"retention: settings unreadable ({type(e).__name__})")
        return []
    results = []
    for row in rows:
        value = row.get("value") or {}
        if not value.get("retention_enabled") or value.get("retention_days") is None:
            continue
        try:
            client_id = int(str(row["key"])[len(PREFIX):])
            results.append(run_retention_for(client_id))
        except Exception as e:
            _log(f"retention: {row.get('key')} failed ({type(e).__name__})")
    return results


# ─── Thumbnails ──────────────────────────────────────────────────────────────

# Drive's thumbnailLink is short-lived and, for a file that isn't public, only
# answers a CREDENTIALED request — the manager's browser can't load it. The
# library's <img> points at OUR endpoint, which fetches the thumbnail here as
# the service account. Cached so each thumbnail is fetched from Drive once (per
# instance, per content version); the browser caches it too. No file is made public.
THUMB_CACHE_ENTRIES = 600
THUMB_CACHE_S = 24 * 3600
_thumbs = OrderedDict()          # (client_id, file_id, version) -> (at, bytes, content_type)
_thumbs_lock = threading.Lock()


def thumbnail(client_id: int, file_id: str) -> tuple:
    """(bytes, content_type): media-folder files through drive_mirror (parents
    walk, its own cache); connected-folder files only when they are in this
    client's index — fetched server-side with the service account, cached."""
    from core import drive_assets, drive_mirror, drive_service, drive_storage
    state = drive_assets._load(client_id)
    item = (state.get("items") or {}).get(file_id)
    if not item:
        return drive_mirror.thumbnail(client_id, file_id)
    key = (int(client_id), str(file_id), str(item.get("md5") or item.get("modified") or ""))
    with _thumbs_lock:
        hit = _thumbs.get(key)
        if hit and time.time() - hit[0] < THUMB_CACHE_S:
            _thumbs.move_to_end(key)
            return hit[1], hit[2]
    link = drive_assets._fresh_thumb_link(item)
    if not link:
        raise FileNotFoundError("no thumbnail")
    if "=s" in link.rsplit("/", 1)[-1]:
        link = link.rsplit("=s", 1)[0] + "=s320"
    with drive_storage.as_service_account():
        content, content_type = drive_service.fetch_thumbnail(link, drive_mirror.THUMB_MAX_BYTES)
    with _thumbs_lock:
        _thumbs[key] = (time.time(), content, content_type)
        while len(_thumbs) > THUMB_CACHE_ENTRIES:
            _thumbs.popitem(last=False)
    return content, content_type
