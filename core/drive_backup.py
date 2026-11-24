"""
The client-side backup (2026-11-24, docs/DRIVE-FOLDERS.md §Backup).

The system keeps its OWN folder inside the client's Drive: "uallak", created
on first need inside a folder the client SHARED with us (a connected root we
can add to), with subfolders by type and by month:

    uallak/posts/2026-11/   one text file per approved post (caption, channels, date, files)
    uallak/slides/2026-11/  rendered text slides
    uallak/images/2026-11/  generated images
    uallak/video/2026-11/   generated video

Everything the system PRODUCED and the manager APPROVED is copied there
(files.copy inside Drive, as our service account — no byte through our
server). Exactly once: the ledger `done` plus appProperties
uallak_backup_of=<our file id> on the copy (a lost answer is found again).

Never backed up: a file that is not in OUR Shared Drive (the client's own
connected-folder file, anything elsewhere), a copy the old pull made of a
client file (uallak_pulled_from), a working copy, a referenced real asset.
Backup covers what WE produced, not what they already have. A client whose
files already live in their own Drive (drive_storage client_drive) needs none.

Never anything outside a folder the client shared: the "uallak" folder is
created only inside a connected ROOT whose Drive capabilities allow adding
children. None → status `no_writable_folder`, said plainly in the Media tab
(asking the manager to share one); nothing fails silently, nothing is created.

The "uallak" folder is recorded as an EXCLUDED, `backup` subfolder of that
connection, so the index never counts our own output as client assets.

This replaces the publish-time snapshot into social_output (core/asset_refs,
removed): one mechanism. Moved by the every-minute tick
(/api/tiktok/uploads/run → run_due). State: app_settings `drive_backup:<id>`.
"""

import re
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "drive_backup"
PREFIX = "drive_backup:"
FOLDER_NAME = "uallak"
TYPES = ("posts", "slides", "images", "video")
BACKUP_KEY = "uallak_backup_of"
# appProperties that mark a file in our folder as the CLIENT's (never backed up)
CLIENT_COPY_KEYS = ("uallak_pulled_from", "uallak_working_copy_of", "uallak_snapshot_of")
# Queue sources the system produced (social_queue.add callers); media_library =
# a file picked from the Media tab, which may be the client's own upload
PRODUCED_SOURCES = ("post", "slide", "chat", "plan")
RETRY_NO_FOLDER_MIN = 30
MAX_TRIES = 5
FILE_MARGIN_S = 6
FOLDER_MIME = "application/vnd.google-apps.folder"

TEXT = {
    "ok": "גיבוי: כל מה שאנחנו מייצרים ואתם מאשרים נשמר גם אצלכם, בתיקייה \"uallak\" בתוך \"{folder}\" "
          "(לפי סוג וחודש). עד עכשיו גובו {n} קבצים.",
    "waiting": "גיבוי: בפעם הראשונה שתאשרו משהו שייצרנו, ניצור תיקייה \"uallak\" בתוך תיקייה ששיתפתם איתנו "
               "ונשמור שם עותק — הגיבוי שלכם לעבודה שנעשתה עבורכם.",
    "no_writable_folder": "גיבוי: אין לנו כרגע תיקייה ששיתפתם איתנו עם הרשאת עריכה, ולכן אין איפה לשמור אצלכם "
                          "גיבוי של מה שאנחנו מייצרים. שתפו תיקייה ב-Drive עם הרשאת \"עורך\" וחברו אותה כאן.",
    "storage_quota": "גיבוי: Google לא מאפשר לנו לשמור קבצים בתיקייה רגילה ב-My Drive. כדי לקבל גיבוי, שתפו "
                     "איתנו תיקייה מתוך אחסון משותף (Shared Drive) — הכתובת כחבר עם הרשאת \"מנהל תוכן\" — וחברו אותה כאן.",
    "client_drive": "גיבוי: מה שאנחנו מייצרים כבר נשמר ישירות ב-Drive שלכם, אז אין צורך בעותק נוסף.",
    "error": "גיבוי: השמירה האחרונה לא הצליחה — ננסה שוב אוטומטית בדקות הקרובות.",
    "post_date": "תאריך מתוכנן: ",
    "post_channels": "ערוצים: ",
    "post_files": "קבצים:",
}


def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt=None) -> str:
    return (dt or _now()).isoformat()


def _log(line: str):
    print(f"[{SERVICE_NAME}] {line}")


# ─── State ───────────────────────────────────────────────────────────────────

def _load(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}")
            .limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    value = value if isinstance(value, dict) else {}
    for key, default in (("root", {}), ("folders", {}), ("done", {}), ("pending", []), ("status", "")):
        value.setdefault(key, default)
    return value


def _save(client_id: int, state: dict):
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": state,
                                        "updated_at": _iso()}, on_conflict="key").execute()


def _pending_clients() -> list:
    rows = (_db().table("app_settings").select("key,value").like("key", f"{PREFIX}%").limit(5000)
            .execute().data or [])
    out = []
    for row in rows:
        try:
            cid = int(str(row.get("key", ""))[len(PREFIX):])
        except ValueError:
            continue
        if ((row.get("value") or {}).get("pending")):
            out.append(cid)
    return out


# ─── What goes in ────────────────────────────────────────────────────────────

def type_for(item: dict) -> str:
    if item.get("source") == "slide":
        return "slides"
    return "video" if item.get("kind") == "video" else "images"


def candidate(item: dict) -> bool:
    """Cheap first look (the Drive check comes at copy time): an approved file
    the system produced, never a real asset referenced from the client's folder."""
    return bool(item.get("file_id") and item.get("kind") in ("image", "video")
                and item.get("source") in PRODUCED_SOURCES and not item.get("real_asset"))


def _post_key(item: dict) -> str:
    return str((item.get("post_ref") or {}).get("post_id") or item.get("id") or "")


def enqueue(client_id: int, items: list) -> int:
    """Called on approval (social_queue.approve). Returns how many were queued."""
    items = [i for i in items or [] if candidate(i)]
    if not items:
        return 0
    state = _load(client_id)
    queued = {p.get("file_id") for p in state["pending"]}
    added = 0
    for i in items:
        fid = str(i["file_id"])
        if fid in state["done"] or fid in queued:
            continue
        month = str(i.get("approved_at") or _iso())[:7]
        state["pending"].append({"file_id": fid, "type": type_for(i), "month": month, "item_id": i.get("id", ""),
                                 "post_key": _post_key(i), "title": str(i.get("title") or "")[:120],
                                 "caption": str(i.get("caption") or "")[:2200],
                                 "channels": list(i.get("channels") or []),
                                 "planned_date": str(i.get("planned_date") or ""), "tries": 0, "at": _iso()})
        queued.add(fid)
        added += 1
    if added:
        _save(client_id, state)
    return added


# ─── Where: a folder the client shared, that we may add to ──────────────────

def _roots(client_id: int) -> list:
    from core import drive_folders
    roots = [r for r in drive_folders.load(client_id)
             if not r.get("parent_connection_id") and r.get("status") not in ("no_access", "error")]
    # social_output first (it is for our output), then in the order they were connected
    return sorted(roots, key=lambda r: (r.get("role") != "social_output", r.get("added_at") or ""))


def _root_ids(client_id: int) -> list:
    try:
        return sorted(r["id"] for r in _roots(client_id))
    except Exception:
        return []


def _can_add(folder_id: str) -> bool:
    from core import drive_service
    meta = drive_service.get_meta(folder_id, "id,trashed,mimeType,capabilities(canAddChildren)")
    return (not meta.get("trashed") and meta.get("mimeType") == FOLDER_MIME
            and bool((meta.get("capabilities") or {}).get("canAddChildren")))


def _alive(folder_id: str) -> bool:
    from core import drive_service
    try:
        meta = drive_service.get_meta(folder_id, "id,trashed")
    except Exception as e:
        if getattr(getattr(e, "response", None), "status_code", None) in (403, 404):
            return False
        raise
    return not meta.get("trashed")


def _mark_backup_folder(client_id: int, root: dict, folder_id: str):
    """The "uallak" folder becomes an EXCLUDED `backup` subfolder record of its
    connection: the index walk and the change check never read our output as
    client assets."""
    from core import drive_folders
    data = drive_folders._load(client_id)
    conn = next((r for r in data["connections"] if r.get("id") == root["id"]), None)
    if not conn:
        return
    record = drive_folders.upsert_subfolder(data, conn, folder_id, FOLDER_NAME, conn["folder_id"], 1,
                                            f"{conn.get('display_name') or ''}/{FOLDER_NAME}".lstrip("/"))
    if record:
        record.update(excluded=True, backup=True)
        drive_folders._save(client_id, data)


def ensure_root(client_id: int, state: dict) -> dict:
    """The "uallak" folder (created on first need) — or {} with state.status set."""
    from core import drive_service, drive_storage
    with drive_storage.as_service_account():
        root = state.get("root") or {}
        shared = {c["id"] for c in _roots(client_id)}
        if root.get("folder_id") and root.get("connection_id") in shared and _alive(root["folder_id"]):
            return root
        if root.get("folder_id"):
            state["folders"] = {}                 # trashed / removed: recreated, with its subfolders
        for conn in _roots(client_id):
            try:
                if not _can_add(conn["folder_id"]):
                    continue
                folder_id = drive_service.ensure_folder(FOLDER_NAME, conn["folder_id"])
            except Exception as e:
                if drive_service.classify_error(e) == "storage_quota":
                    state["status"] = "storage_quota"
                    state["quota_folder"] = conn["folder_id"]
                    continue
                if getattr(getattr(e, "response", None), "status_code", None) in (403, 404):
                    continue
                raise
            state["root"] = {"folder_id": folder_id, "parent_folder_id": conn["folder_id"],
                             "connection_id": conn["id"], "parent_name": conn.get("display_name", ""),
                             "url": f"https://drive.google.com/drive/folders/{folder_id}", "created_at": _iso()}
            _mark_backup_folder(client_id, conn, folder_id)
            _log(f"client {client_id}: backup folder {folder_id} inside shared folder {conn['folder_id']}")
            return state["root"]
    state["root"] = {}
    if state.get("status") != "storage_quota":
        state["status"] = "no_writable_folder"
    return {}


def _folder(state: dict, kind: str, month: str) -> str:
    from core import drive_service
    key = f"{kind}/{month}"
    if state["folders"].get(key):
        return state["folders"][key]
    type_id = state["folders"].get(kind) or drive_service.ensure_folder(kind, state["root"]["folder_id"])
    state["folders"][kind] = type_id
    state["folders"][key] = drive_service.ensure_folder(month, type_id)
    return state["folders"][key]


# ─── The copy ────────────────────────────────────────────────────────────────

def _ours(meta: dict, our_drive: str) -> bool:
    """Produced by us: in OUR Shared Drive and not a copy of a client file."""
    props = meta.get("appProperties") or {}
    if any(props.get(k) for k in CLIENT_COPY_KEYS):
        return False
    return bool(our_drive) and meta.get("driveId") == our_drive and not meta.get("trashed")


def _post_text(entry: dict, files: list) -> bytes:
    lines = [entry.get("title") or "", ""]
    if entry.get("planned_date"):
        lines.append(TEXT["post_date"] + entry["planned_date"])
    if entry.get("channels"):
        lines.append(TEXT["post_channels"] + ", ".join(entry["channels"]))
    lines += ["", entry.get("caption") or "", ""]
    if files:
        lines.append(TEXT["post_files"])
        lines += [f"- {f}" for f in files]
    return "\n".join(lines).strip().encode("utf-8") + b"\n"


def _backup_one(client_id: int, state: dict, entry: dict, our_drive: str) -> str:
    """'done' | 'skipped:<why>'. Raises on Drive trouble (retried)."""
    from core import drive_service
    fid = entry["file_id"]
    if fid in state["done"]:
        return "done"
    try:
        meta = drive_service.get_meta(fid, "id,name,mimeType,trashed,driveId,appProperties")
    except Exception as e:
        if getattr(getattr(e, "response", None), "status_code", None) == 404:
            return "skipped:gone"
        raise
    if not _ours(meta, our_drive):
        return "skipped:not_ours"          # the client's own file, or outside our storage: never backed up
    target = _folder(state, entry["type"], entry["month"])
    earlier = drive_service.find_by_app_property(target, BACKUP_KEY, fid)
    copy = earlier[0] if earlier else drive_service.copy_file(fid, target, meta.get("name") or "",
                                                              {BACKUP_KEY: fid})
    state["done"][fid] = {"backup_id": copy.get("id", ""), "type": entry["type"], "month": entry["month"],
                          "name": meta.get("name", ""), "at": _iso()}
    # the post's text file: one per post, rewritten idempotently by its key
    key = entry.get("post_key") or ""
    if key:
        posts = state.setdefault("posts", {})
        rec = posts.setdefault(key, {"files": [], "backup_id": ""})
        if meta.get("name") and meta["name"] not in rec["files"]:
            rec["files"].append(meta["name"])
        if not rec["backup_id"]:
            folder = _folder(state, "posts", entry["month"])
            name = re.sub(r"[\\/:*?\"<>|]+", " ", f"{entry['month']} {entry.get('title') or key}").strip()[:120] + ".txt"
            found = drive_service.find_by_app_property(folder, BACKUP_KEY, f"post:{key}")
            rec["backup_id"] = (found[0].get("id", "") if found else drive_service.upload_bytes(
                folder, name, _post_text(entry, rec["files"]), "text/plain",
                {BACKUP_KEY: f"post:{key}"}).get("id", ""))
    return "done"


def run(client_id: int, budget_s: float = 30.0) -> dict:
    """Back up what is pending for one client within budget_s."""
    import time
    from core import drive_pull, drive_storage
    started = time.monotonic()
    state = _load(client_id)
    if not state["pending"]:
        return {"done": 0}
    if drive_storage.location(client_id).get("kind") == drive_storage.CLIENT:
        state.update(status="client_drive", pending=[])
        _save(client_id, state)
        return {"done": 0, "status": "client_drive"}
    nxt = state.get("next_try_at") or ""
    if nxt and nxt > _iso() and _root_ids(client_id) == state.get("roots_seen"):
        # nothing to write into last time, and no folder was connected since
        return {"done": 0, "status": state.get("status"), "waiting_until": nxt}
    try:
        root = ensure_root(client_id, state)
    except Exception as e:
        state.update(status="error", last_error=type(e).__name__)
        _save(client_id, state)
        return {"done": 0, "status": "error"}
    if not root:
        state["next_try_at"] = _iso(_now() + timedelta(minutes=RETRY_NO_FOLDER_MIN))
        state["roots_seen"] = _root_ids(client_id)
        _save(client_id, state)
        return {"done": 0, "status": state["status"]}
    state["next_try_at"] = ""
    our_drive = drive_pull._our_drive_id()
    done, skipped, keep = 0, 0, []
    status = "ok"
    with drive_storage.as_service_account():
        for entry in list(state["pending"]):
            if time.monotonic() - started > budget_s - FILE_MARGIN_S:
                keep.append(entry)
                continue
            try:
                result = _backup_one(client_id, state, entry, our_drive)
            except Exception as e:
                from core import drive_service
                if drive_service.classify_error(e) == "storage_quota":
                    status = "storage_quota"
                    keep.append(entry)
                    continue
                entry["tries"] = int(entry.get("tries") or 0) + 1
                status = "error"
                _log(f"client {client_id}: backup of {entry['file_id']} failed ({type(e).__name__}, try {entry['tries']})")
                if entry["tries"] < MAX_TRIES:
                    keep.append(entry)
                continue
            if result == "done":
                done += 1
            else:
                skipped += 1
                state.setdefault("skipped", {})[entry["file_id"]] = result.split(":", 1)[1]
    state["pending"] = keep
    state["status"] = status
    state["last_run_at"] = _iso()
    if status == "storage_quota":
        # a My Drive folder: Google refuses files owned by a service account there
        state["next_try_at"] = _iso(_now() + timedelta(minutes=RETRY_NO_FOLDER_MIN))
        state["roots_seen"] = _root_ids(client_id)
    _save(client_id, state)
    return {"done": done, "skipped": skipped, "left": len(keep), "status": status}


def run_due(budget_s: float = 40.0) -> dict:
    import time
    started, out = time.monotonic(), {}
    for cid in _pending_clients():
        left = budget_s - (time.monotonic() - started)
        if left < FILE_MARGIN_S * 2:
            break
        try:
            out[cid] = run(cid, left)
        except Exception as e:
            out[cid] = {"error": type(e).__name__}
    return out


# ─── What the Media tab says ─────────────────────────────────────────────────

def view(client_id: int, connections: list = None) -> dict:
    """{"status", "text", "url", "count"} for the connect section. Never raises."""
    try:
        from core import drive_storage
        if drive_storage.location(client_id).get("kind") == drive_storage.CLIENT:
            return {"status": "client_drive", "text": TEXT["client_drive"], "url": "", "count": 0}
        state = _load(client_id)
    except Exception as e:
        _log(f"client {client_id}: backup state unreadable ({type(e).__name__})")
        return {"status": "", "text": "", "url": "", "count": 0}
    if connections is None:
        try:
            from core import drive_folders
            connections = drive_folders.load(client_id)
        except Exception:
            connections = []
    roots = [r for r in connections if not r.get("parent_connection_id")
             and r.get("status") not in ("no_access", "error")]
    root = state.get("root") or {}
    count = len(state.get("done") or {})
    status = state.get("status") or ""
    if status in ("no_writable_folder", "storage_quota") and \
            sorted(r.get("id") for r in roots) != (state.get("roots_seen") or []):
        status = ""                       # a folder was connected since: the next run tries it
    if not roots:
        status = "no_writable_folder"
    elif root.get("folder_id") and status not in ("storage_quota", "error"):
        status = "ok"
    elif status not in ("no_writable_folder", "storage_quota", "error"):
        status = "waiting"
    text = TEXT.get(status, "").replace("{folder}", root.get("parent_name") or "").replace("{n}", str(count))
    return {"status": status, "text": text, "url": root.get("url", "") if status == "ok" else "",
            "count": count, "pending": len(state.get("pending") or [])}
