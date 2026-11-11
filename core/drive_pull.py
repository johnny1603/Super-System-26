"""Pull from Drive — ADMIN ONLY (2026-11-11, docs/DRIVE-PULL.md).

The owner shares ONE folder of his own Drive with our service account (the
address is shown on the page, copyable), pastes its link here, picks files
or the whole folder and a client, and the files are COPIED INSIDE DRIVE
(files.copy, supportsAllDrives) into that client's media folder in our
Shared Drive — `videos/`, `images/` or `documents/` by type, or the one he
picks. No byte passes through our server, whatever the size; no new scope
(the service account's own `drive`, core/drive_service.DRIVE_SCOPES), no
Google consent, no Picker (that is step 2, after Google's round-2 approval).

A DURABLE JOB, the core/tiktok_uploads pattern: per client one app_settings
row `drive_pull:<id>` = {"jobs": [...]}, a lease `drive_pull_lock:<id>`
(insert-to-acquire, expiring), progress saved after EVERY file, each file
retried up to RETRIES times on Google's transient answers. Moved forward by
the admin page's poll (POST /api/admin/drive-pull/{id}/step) and by the
existing every-minute scheduler request (/api/tiktok/uploads/run calls
run_due with what is left of its budget) — never a background task, no new
scheduler job, no Cloud Run change.

Idempotent: every copy carries appProperties uallak_pulled_from=<source id>;
a re-run (or a retry after a lost answer) finds it and never copies twice.
A source the service account can't open is answered with ONE calm line
naming the address to share with — never an error. Only for clients whose
files live in our Shared Drive (drive_storage.location == shared_drive).
After a job: the client's Shared Drive usage vs DRIVE_SHARED_ALERT_GB, with
the existing one-alert-per-status-change rule (drive_storage.shared_usage_scan).
client_deletion clears both keys; nothing here holds a token.
"""
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "drive_pull"
PREFIX = "drive_pull:"
LOCK_PREFIX = "drive_pull_lock:"
STEP_BUDGET_S = 25             # one poll of the admin page
FILE_MARGIN_S = 8              # don't start a copy with less than this left
LEASE_S = 300
MAX_FILES = 500                # one job
MAX_SELECTIONS = 200
MAX_DEPTH = 6
RETRIES = 3
RETRY_AFTER_S = (20, 60, 180)
KEEP_FINISHED = 10
PULLED_KEY = "uallak_pulled_from"
TARGETS = ("auto", "videos", "images", "documents")
ACTIVE = ("queued", "running")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
_HOLDER = uuid.uuid4().hex[:10]

# The admin page's words for a file's outcome (never Drive's raw error)
FILE_LABELS = {"pending": "ממתין", "copied": "הועתק ✓", "already": "כבר היה שם ✓",
               "skipped_trashed": "דולג — בפח", "skipped_folder": "דולג — תיקייה",
               "no_access": "אין גישה לקובץ — שתפו אותו עם חשבון השירות",
               "copy_blocked": "הבעלים חסם העתקה של הקובץ הזה",
               "storage_quota": "אין מקום ב-Shared Drive", "failed": "לא הועתק אחרי 3 ניסיונות"}


class PullRefused(ValueError):
    def __init__(self, code: str, text: str):
        super().__init__(code)
        self.code, self.text = code, text


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt=None) -> str:
    return (dt or _now()).isoformat()


def _age(value) -> float:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return (_now() - when).total_seconds()
    except (TypeError, ValueError):
        return 10 ** 9


def _log(message: str):
    from core.agent_base import log_step
    log_step(SERVICE_NAME, "job", message)


# ─── The source: a link or an id the owner pasted ───────────────────────────

_REF_PATTERNS = (re.compile(r"/folders/([A-Za-z0-9_-]{10,})"), re.compile(r"/d/([A-Za-z0-9_-]{10,})"),
                 re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})"))


def parse_ref(text: str) -> str:
    """A Drive folder/file id from a pasted link (…/folders/ID, …/file/d/ID,
    …?id=ID) or a bare id; "" when there is none."""
    text = str(text or "").strip()
    for pattern in _REF_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return text if _ID_RE.match(text) else ""


def share_with() -> str:
    """The address the owner shares his folder with (not a secret)."""
    from core import drive_service
    try:
        return drive_service.service_account_email()
    except Exception:
        return ""


def not_shared_line() -> str:
    email = share_with()
    return (f"אין לי גישה לתיקייה הזו עדיין. ב-Drive: לחיצה ימנית על התיקייה ← שיתוף ← הוסיפו את "
            f"{email or 'כתובת חשבון השירות'} כ\"צופה\" ← שליחה. ואז שוב \"הצג\". "
            f"שתפו רק את התיקייה הזו — לא את כל ה-Drive.")


def _kind(mime: str) -> str:
    from core.drive_mirror import kind_of
    return kind_of(mime)


def _item(raw: dict) -> dict:
    mime = raw.get("mimeType") or ""
    item = {"id": raw.get("id", ""), "name": raw.get("name", ""), "mime_type": mime, "kind": _kind(mime),
            "size": int(raw["size"]) if str(raw.get("size") or "").isdigit() else None,
            "modified_time": raw.get("modifiedTime", "")}
    if item["kind"] == "shortcut":
        details = raw.get("shortcutDetails") or {}
        item["target_id"] = details.get("targetId", "")
        item["target_kind"] = _kind(details.get("targetMimeType", ""))
    return item


def _unreachable(error: Exception) -> bool:
    from core import drive_service
    return drive_service.classify_error(error, target="file") in ("file_not_found", "file_permission",
                                                                  "no_access")


def browse(ref: str, page_token: str = "") -> dict:
    """What a pasted folder (or file) holds, as the service account:
    {"source": {...}, "items": [...], "next_page_token"} — or {"not_shared":
    True, "share_with", "text"} when we can't open it, or {"error", "text"}.
    Metadata only; never raises."""
    from core import drive_service as drive
    source_id = parse_ref(ref)
    if not source_id:
        return {"error": "bad_ref", "text": "זה לא נראה כמו קישור לתיקייה או לקובץ ב-Drive. העתיקו את הקישור משורת הכתובת."}
    try:
        with _service_account():
            meta = drive.get_meta(source_id, "id,name,mimeType,size,modifiedTime,driveId,"
                                             "shortcutDetails(targetId,targetMimeType)")
            source = _item(meta)
            if source["kind"] != "folder":
                return {"source": source, "items": [source], "next_page_token": ""}
            page = drive.list_children(source_id, page_token=page_token, page_size=100)
    except Exception as e:
        if _unreachable(e):
            return {"not_shared": True, "share_with": share_with(), "text": not_shared_line()}
        _log(f"browse {source_id} failed ({drive.classify_error(e)})")
        return {"error": drive.classify_error(e), "text": "Drive לא ענה כרגע. נסו שוב בעוד רגע."}
    return {"source": source, "items": [_item(i) for i in page["files"]],
            "next_page_token": page["next_page_token"]}


def _service_account():
    from core import drive_storage
    return drive_storage.as_service_account()


# ─── Storage (one row per client, like core/tiktok_uploads) ─────────────────

def _key(client_id) -> str:
    return f"{PREFIX}{int(client_id)}"


def load(client_id: int) -> list:
    rows = (_db().table("app_settings").select("value").eq("key", _key(client_id)).limit(1)
            .execute().data) or []
    value = rows[0].get("value") if rows else {}
    jobs = (value or {}).get("jobs") if isinstance(value, dict) else None
    return list(jobs or [])


def _save(client_id: int, jobs: list):
    finished = [j for j in jobs if j.get("status") not in ACTIVE]
    keep = [j for j in jobs if j.get("status") in ACTIVE] + finished[-KEEP_FINISHED:]
    keep.sort(key=lambda j: j.get("created_at") or "")
    _db().table("app_settings").upsert({"key": _key(client_id), "value": {"jobs": keep},
                                        "updated_at": _iso()}, on_conflict="key").execute()


def _put(client_id: int, job: dict):
    """Fresh read → replace THIS job → write."""
    jobs = [j for j in load(client_id) if j.get("id") != job.get("id")]
    job["updated_at"] = _iso()
    jobs.append(job)
    _save(client_id, jobs)


def get(client_id: int, job_id: str) -> dict:
    return next((j for j in load(client_id) if j.get("id") == job_id), {})


def _all_rows() -> list:
    rows = (_db().table("app_settings").select("key,value").like("key", f"{PREFIX}%").execute().data) or []
    return [list((r.get("value") or {}).get("jobs") or []) if isinstance(r.get("value"), dict) else []
            for r in rows]


# ─── Lease: one worker per client ───────────────────────────────────────────

def _lease_take(client_id) -> str:
    key, token = f"{LOCK_PREFIX}{int(client_id)}", f"{_HOLDER}:{uuid.uuid4().hex[:6]}"
    value = {"token": token, "until": _iso(_now() + timedelta(seconds=LEASE_S))}
    for _ in range(2):
        try:
            _db().table("app_settings").insert({"key": key, "value": value, "updated_at": _iso()}).execute()
            return token
        except Exception:
            rows = (_db().table("app_settings").select("value").eq("key", key).limit(1).execute().data) or []
            held = (rows[0].get("value") if rows else {}) or {}
            if rows and _age(held.get("until")) < 0:
                return ""              # someone is working on it
            q = _db().table("app_settings").delete().eq("key", key)
            if held.get("token"):
                q = q.eq("value->>token", held["token"])
            q.execute()
    return ""


def _lease_release(client_id, token):
    try:
        (_db().table("app_settings").delete().eq("key", f"{LOCK_PREFIX}{int(client_id)}")
         .eq("value->>token", token).execute())
    except Exception:
        pass


# ─── Start ──────────────────────────────────────────────────────────────────

def start(client_id: int, selections: list, target: str = "auto", who: str = "admin") -> dict:
    """A new job: `selections` = [{"id", "kind"}] picked on the page (files
    and/or folders — a folder means everything in it, sub-folders included).
    Raises PullRefused(code, text) with a calm line."""
    from agents.client_agent import get_client
    from core import deletion_guard, drive_storage
    client = get_client(int(client_id)) or {}
    if not client:
        raise PullRefused("no_client", "לא מצאתי לקוח עם המספר הזה.")
    if deletion_guard.is_deleting(int(client_id)):
        raise PullRefused("deleting", "הלקוח הזה בתהליך מחיקה.")
    if drive_storage.location(int(client_id)).get("kind") != drive_storage.SHARED:
        raise PullRefused("client_drive", "הקבצים של הלקוח הזה נמצאים ב-Drive שלו עצמו — משיכה עובדת רק לתיקייה "
                                          "שלו ב-Shared Drive שלנו.")
    if target not in TARGETS:
        raise PullRefused("bad_target", "יעד לא מוכר.")
    picked, seen = [], set()
    for sel in (selections or [])[:MAX_SELECTIONS]:
        sid = str((sel or {}).get("id") or "").strip()
        kind = str((sel or {}).get("kind") or "")
        if _ID_RE.match(sid) and sid not in seen:
            seen.add(sid)
            picked.append({"id": sid, "kind": "folder" if kind == "folder" else "file"})
    if not picked:
        raise PullRefused("nothing", "לא נבחר אף קובץ.")
    if any(j.get("status") in ACTIVE for j in load(client_id)):
        raise PullRefused("busy", "יש כבר משיכה פעילה ללקוח הזה. חכו שתסתיים.")
    job = {"id": uuid.uuid4().hex[:12], "client_id": int(client_id), "target": target, "who": str(who)[:80],
           "status": "queued", "created_at": _iso(), "updated_at": _iso(),
           "folders": [{"id": p["id"], "depth": 0} for p in picked if p["kind"] == "folder"],
           "files": [{"id": p["id"], "status": "pending", "attempts": 0} for p in picked if p["kind"] == "file"],
           "truncated": False, "folder_errors": 0, "usage": None}
    jobs = load(client_id)
    jobs.append(job)
    _save(client_id, jobs)
    _log(f"client {client_id}: pull job {job['id']} queued by {who}: {len(job['files'])} files, "
         f"{len(job['folders'])} folders, target {target}")
    return view(job)


# ─── Moving a job forward ───────────────────────────────────────────────────

def _target_folder(mime: str, target: str) -> str:
    if target != "auto":
        return target
    kind = _kind(mime)
    return "videos" if kind == "video" else "images" if kind == "image" else "documents"


def _expand(job: dict, deadline: float):
    """Folders → their files (sub-folders queued, depth ≤ MAX_DEPTH), bounded
    by MAX_FILES. Shortcuts become their target file."""
    from core import drive_service as drive
    known = {f["id"] for f in job["files"]}
    while job["folders"] and time.monotonic() < deadline - FILE_MARGIN_S:
        folder = job["folders"].pop(0)
        page_token = ""
        try:
            while True:
                page = drive.list_children(folder["id"], page_token=page_token, page_size=200)
                for raw in page["files"]:
                    item = _item(raw)
                    if item["kind"] == "folder":
                        if folder["depth"] + 1 <= MAX_DEPTH:
                            job["folders"].append({"id": item["id"], "depth": folder["depth"] + 1})
                        continue
                    fid = item.get("target_id") if item["kind"] == "shortcut" else item["id"]
                    if not fid or fid in known:
                        continue
                    if len(job["files"]) >= MAX_FILES:
                        job["truncated"] = True
                        break
                    known.add(fid)
                    job["files"].append({"id": fid, "status": "pending", "attempts": 0,
                                         "name": item["name"], "size": item["size"]})
                page_token = page["next_page_token"]
                if not page_token or job["truncated"]:
                    break
        except Exception as e:
            job["folder_errors"] = int(job.get("folder_errors") or 0) + 1
            _log(f"client {job['client_id']}: pull {job['id']} folder {folder['id']} unreadable "
                 f"({drive.classify_error(e)})")
        if job["truncated"]:
            job["folders"] = []


def _copy_one(job: dict, entry: dict):
    """Copy ONE file; the entry's status says what happened."""
    from agents import media_agent
    from core import drive_service as drive
    client_id = int(job["client_id"])
    try:
        with _service_account():
            meta = drive.get_meta(entry["id"], "id,name,mimeType,size,trashed,capabilities(canCopy),"
                                               "shortcutDetails(targetId,targetMimeType)")
        if meta.get("mimeType") == drive.SHORTCUT_MIME:
            target = (meta.get("shortcutDetails") or {}).get("targetId")
            if target:
                entry["id"] = target
                with _service_account():
                    meta = drive.get_meta(target, "id,name,mimeType,size,trashed,capabilities(canCopy)")
        entry.update(name=meta.get("name") or entry.get("name") or "",
                     size=int(meta["size"]) if str(meta.get("size") or "").isdigit() else entry.get("size"))
        if meta.get("trashed"):
            entry["status"] = "skipped_trashed"
            return
        if meta.get("mimeType") == drive.FOLDER_MIME:
            entry["status"] = "skipped_folder"
            return
        if (meta.get("capabilities") or {}).get("canCopy") is False:
            entry["status"] = "copy_blocked"
            return
        folder = _target_folder(meta.get("mimeType") or "", job["target"])
        parent = media_agent._subfolder(client_id, folder)
        with _service_account():
            existing = drive.find_by_app_property(parent, PULLED_KEY, entry["id"])
            if existing:
                entry.update(status="already", copy_id=existing[0].get("id"), folder=folder)
                return
            copied = drive.copy_file(entry["id"], parent, meta.get("name") or "", {PULLED_KEY: entry["id"]})
        entry.update(status="copied", copy_id=copied.get("id"), folder=folder)
    except Exception as e:
        reason = drive.classify_error(e, target="file")
        if reason in ("file_not_found", "file_permission"):
            entry["status"] = "no_access"
            return
        if reason == "storage_quota":
            entry["status"] = "storage_quota"
            return
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        if entry["attempts"] >= RETRIES:
            entry["status"] = "failed"
            _log(f"client {client_id}: pull {job['id']} file {entry['id']} failed {entry['attempts']}x ({reason})")
        else:
            entry["retry_at"] = _iso(_now() + timedelta(seconds=RETRY_AFTER_S[entry["attempts"] - 1]))


def _due_file(entry: dict) -> bool:
    return entry.get("status") == "pending" and (not entry.get("retry_at") or _age(entry["retry_at"]) >= 0)


def _finish(job: dict):
    from core import drive_mirror, drive_storage
    failed = [f for f in job["files"] if f["status"] in ("failed", "no_access", "copy_blocked", "storage_quota")]
    job["status"] = "done_with_errors" if (failed or job.get("folder_errors")) else "done"
    job["finished_at"] = _iso()
    drive_mirror.forget(int(job["client_id"]))
    try:
        from core import client_state
        client_state.forget(int(job["client_id"]))
    except Exception:
        pass
    try:
        rows = drive_storage.shared_usage_scan([int(job["client_id"])])
        row = rows[0] if rows else {}
        job["usage"] = {"bytes": row.get("bytes"), "complete": row.get("complete"),
                        "over": row.get("over_threshold"), "alert_bytes": drive_storage.shared_alert_bytes()}
    except Exception as e:
        job["usage"] = {"error": type(e).__name__}
    copied = sum(1 for f in job["files"] if f["status"] == "copied")
    _log(f"client {job['client_id']}: pull job {job['id']} {job['status']} — {copied} copied, {len(failed)} not")


def step_client(client_id: int, deadline: float) -> int:
    """Moves this client's active job forward until `deadline`. Returns how
    many files were handled. Never two workers on one client (lease)."""
    token = _lease_take(client_id)
    if not token:
        return 0
    handled = 0
    try:
        job = next((j for j in load(client_id) if j.get("status") in ACTIVE), None)
        if not job:
            return 0
        job["status"] = "running"
        with _service_account():
            _expand(job, deadline)
        _put(client_id, job)
        for entry in job["files"]:
            if time.monotonic() > deadline - FILE_MARGIN_S:
                break
            if not _due_file(entry):
                continue
            _copy_one(job, entry)
            handled += 1
            _put(client_id, job)          # progress saved after every file
        if not job["folders"] and not any(f["status"] == "pending" for f in job["files"]):
            _finish(job)
            _put(client_id, job)
        return handled
    finally:
        _lease_release(client_id, token)


def step(client_id: int, budget_s: float = STEP_BUDGET_S) -> dict:
    """The admin page's poll: a short step, then the job as it stands."""
    try:
        step_client(int(client_id), time.monotonic() + budget_s)
    except Exception as e:
        _log(f"client {client_id}: pull step failed ({type(e).__name__}: {str(e)[:200]})")
    jobs = load(int(client_id))
    job = next((j for j in reversed(jobs) if j.get("status") in ACTIVE), None) or (jobs[-1] if jobs else {})
    return view(job) if job else {}


def run_due(budget_s: float) -> dict:
    """The scheduler's minute (piggybacks on /api/tiktok/uploads/run): every
    client with an active pull, inside what is left of that request."""
    deadline = time.monotonic() + max(0.0, budget_s)
    seen = handled = 0
    for jobs in _all_rows():
        active = [j for j in jobs if j.get("status") in ACTIVE]
        if not active:
            continue
        seen += 1
        if time.monotonic() > deadline - FILE_MARGIN_S:
            break
        try:
            handled += step_client(active[0]["client_id"], deadline)
        except Exception as e:
            _log(f"client {active[0].get('client_id')}: pull tick failed ({type(e).__name__})")
    return {"clients_with_pulls": seen, "files": handled}


# ─── What the page shows ────────────────────────────────────────────────────

def view(job: dict) -> dict:
    if not job:
        return {}
    files = job.get("files") or []
    counts = {}
    for f in files:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    usage = job.get("usage") or None
    if usage and usage.get("bytes") is not None:
        usage = {**usage, "gb": round(usage["bytes"] / 1024 ** 3, 2),
                 "alert_gb": round((usage.get("alert_bytes") or 0) / 1024 ** 3, 1)}
    return {"id": job.get("id"), "client_id": job.get("client_id"), "status": job.get("status"),
            "target": job.get("target"), "created_at": job.get("created_at"),
            "finished_at": job.get("finished_at", ""), "folders_left": len(job.get("folders") or []),
            "truncated": bool(job.get("truncated")), "folder_errors": int(job.get("folder_errors") or 0),
            "counts": counts, "total": len(files),
            "files": [{"name": f.get("name") or f["id"], "status": f["status"],
                       "label": FILE_LABELS.get(f["status"], f["status"]), "folder": f.get("folder", ""),
                       "size": f.get("size"), "attempts": f.get("attempts", 0)} for f in files],
            "usage": usage}


def latest(client_id: int) -> dict:
    jobs = load(int(client_id))
    return view(jobs[-1]) if jobs else {}
