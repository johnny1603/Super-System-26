"""Moving a client's existing files from OUR Shared Drive into the uallak
folder in THEIR own Drive (round 2, DRIVE_FILE_ENABLED). Admin-only, four
explicit steps, never a delete before a verification:

  1. copy   — every file under their Shared Drive folder is copied into the
              same path in their Drive: read as our service account
              (drive_service.read_range, 8 MiB at a time), written with their
              drive.file grant (a resumable session), so memory never holds a
              whole file. Google Docs/Sheets/Slides (no bytes of their own) are
              exported and re-imported as the same native type. Resumable
              across runs: each run works for at most budget_seconds and
              records progress; every copy carries appProperties
              uallak_src_id, and a recorded copy is never made twice.
  2. verify — for EVERY file now in the source tree: the copy exists, is not
              trashed, and matches — size AND md5Checksum for binary files,
              the same native type for Google files. Any source file without
              a verified copy fails the whole verification.
  3. flip   — only after a FRESH verification passes: drive_storage's pointer
              moves to the client's Drive, so every new file goes there.
  4. free   — only on explicit confirmation (the typed phrase) and only after
              ANOTHER fresh verification: our Shared Drive folder is moved to
              the trash (the Shared Drive empties it after 30 days). Never
              before the flip.

State lives in drive_storage's record under "migration" ({"copied": {src:
dst}, "in_progress": {...}, "verified_at", "flipped_at", "freed_at"}). The
in-progress upload session URI is a write capability for ONE file being
created in the client's Drive; it stays server-side in that record and is
never sent to a browser or logged.

VERIFICATION STATUS: never run against a live grant — the md5/size check is
Drive's own metadata, tested here with fakes only.
"""
import time
from datetime import datetime, timezone

from core import drive_storage

SERVICE_NAME = "drive_migration"
CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_BUDGET_SECONDS = 240          # inside Cloud Run's ~300 s request limit
WALK_FIELDS = "nextPageToken,files(id,name,mimeType,size,md5Checksum)"
NATIVE_PREFIX = "application/vnd.google-apps."
# Google-native type → (export as, so it can be re-imported as the same type)
EXPORT_AS = {
    "application/vnd.google-apps.document":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.google-apps.spreadsheet":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.google-apps.presentation":
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.google-apps.drawing": "image/png",
}
FREE_PHRASE = "FREE {id}"


class MigrationRefused(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _drive():
    from core import drive_service
    return drive_service


# ─── The source tree (our Shared Drive, as the service account) ──────────────

def walk(root: str) -> list:
    """Every non-folder file under root, recursively: [{id, name, mimeType,
    size, md5Checksum, path: [folder names from root]}]. Run as the service
    account."""
    drive = _drive()
    out, pending = [], [(root, [])]
    while pending:
        folder, path = pending.pop()
        page_token = ""
        while True:
            page = drive.list_children(folder, page_token=page_token, page_size=200, fields=WALK_FIELDS)
            for item in page["files"]:
                if item.get("mimeType") == drive.FOLDER_MIME:
                    pending.append((item["id"], path + [item.get("name") or "folder"]))
                else:
                    out.append({**item, "path": path})
            page_token = page["next_page_token"]
            if not page_token:
                break
    return out


def _source_root(client_id: int) -> str:
    from core import drive_mirror
    with drive_storage.as_service_account():
        return drive_mirror.shared_client_root(client_id)


def has_files(client_id: int) -> bool:
    """Does our Shared Drive hold any file of this client's? (decides whether
    a new connection needs a migration before the pointer may flip)"""
    root = _source_root(client_id)
    if not root:
        return False
    with drive_storage.as_service_account():
        return bool(walk(root))


def _client_record(client_id: int) -> dict:
    rec = drive_storage.record(client_id)
    if not rec.get("root"):
        raise MigrationRefused("not_connected", "the client's own Drive is not connected")
    rec.setdefault("migration", {})
    rec["migration"].setdefault("copied", {})
    return rec


def plan(client_id: int) -> dict:
    """What a copy would move: file count, bytes, how many already copied."""
    rec = _client_record(client_id)
    root = _source_root(client_id)
    if not root:
        return {"source": "", "files": 0, "bytes": 0, "copied": 0}
    with drive_storage.as_service_account():
        files = walk(root)
    copied = rec["migration"]["copied"]
    return {"source": root, "files": len(files),
            "bytes": sum(int(f["size"]) for f in files if str(f.get("size") or "").isdigit()),
            "copied": sum(1 for f in files if f["id"] in copied),
            "pointer": rec.get("pointer") or drive_storage.SHARED}


# ─── Copy ────────────────────────────────────────────────────────────────────

def _dest_folder(client_id: int, rec: dict, path: list) -> str:
    """The same folder path under the uallak folder in the client's Drive."""
    drive = _drive()
    parent = rec["root"]
    with drive_storage.as_client(client_id):
        for name in path:
            parent = drive.ensure_folder(name, parent)
    return parent


def _copy_binary(client_id: int, rec: dict, src: dict, folder: str, deadline: float) -> str:
    """Stream one file: SA read_range → client resumable put_chunk. Returns
    the new file id, or "" when the time budget ran out (progress kept)."""
    drive = _drive()
    total = int(src.get("size") or 0)
    if total == 0:
        # An empty file: nothing to stream — one small multipart create
        with drive_storage.as_client(client_id):
            return drive.upload_bytes(folder, src["name"], b"", src["mimeType"],
                                      {"uallak_src_id": src["id"]})["id"]
    state = rec["migration"].get("in_progress") or {}
    if state.get("src") != src["id"]:
        with drive_storage.as_client(client_id):
            uri = drive.start_resumable(folder, src["name"], src["mimeType"], total,
                                        {"uallak_src_id": src["id"]})
        state = {"src": src["id"], "uri": uri, "offset": 0}
    offset = state["offset"]
    while offset < total:
        if time.time() > deadline:
            state["offset"] = offset
            rec["migration"]["in_progress"] = state
            return ""
        length = min(CHUNK_BYTES, total - offset)
        with drive_storage.as_service_account():
            data = drive.read_range(src["id"], offset, length)
        try:
            with drive_storage.as_client(client_id):
                answer = drive.put_chunk(state["uri"], data, offset, total)
        except drive.ResumableSessionGone:
            rec["migration"].pop("in_progress", None)
            raise
        if answer.get("done"):
            rec["migration"].pop("in_progress", None)
            return answer["file"]["id"]
        offset = answer["next_offset"]
    raise RuntimeError(f"upload of {src['id']} ended without Drive confirming the file")


def _copy_native(client_id: int, src: dict, folder: str) -> str:
    """Google Docs/Sheets/Slides/Drawings: export (SA) → upload converting
    back to the same native type (client grant). Native files are small."""
    drive = _drive()
    export_mime = EXPORT_AS.get(src["mimeType"])
    if not export_mime:
        raise MigrationRefused("unsupported_type", src["mimeType"])
    with drive_storage.as_service_account():
        http, upstream = drive.open_download_stream(src["id"], export_mime)
        try:
            content = b"".join(upstream.iter_bytes())
        finally:
            upstream.close()
            http.close()
    if not content:
        raise RuntimeError(f"export of {src['id']} returned no content")
    with drive_storage.as_client(client_id):
        # metadata mimeType = the native type: Drive imports the export back into it
        uri = drive.start_resumable(folder, src["name"], src["mimeType"], len(content),
                                    {"uallak_src_id": src["id"]}, upload_mime=export_mime)
        answer = drive.put_chunk(uri, content, 0, len(content))
    if not answer.get("done"):
        raise RuntimeError(f"export upload of {src['id']} not confirmed")
    return answer["file"]["id"]


def copy(client_id: int, budget_seconds: int = DEFAULT_BUDGET_SECONDS) -> dict:
    """Copy what is not copied yet, for at most budget_seconds. Run again
    until "remaining" is 0. Never deletes anything anywhere."""
    rec = _client_record(client_id)
    root = _source_root(client_id)
    if not root:
        return {"copied_now": 0, "remaining": 0, "failed": []}
    with drive_storage.as_service_account():
        files = walk(root)
    deadline = time.time() + max(5, int(budget_seconds))
    copied = rec["migration"]["copied"]
    done_now, failed = 0, []
    try:
        for src in files:
            if src["id"] in copied:
                continue
            if time.time() > deadline:
                break
            try:
                folder = _dest_folder(client_id, rec, src["path"])
                if str(src.get("mimeType") or "").startswith(NATIVE_PREFIX):
                    new_id = _copy_native(client_id, src, folder)
                else:
                    new_id = _copy_binary(client_id, rec, src, folder, deadline)
            except Exception as e:
                failed.append({"id": src["id"], "name": src.get("name", ""), "error": type(e).__name__})
                continue
            if not new_id:
                break          # time budget ran out mid-file; progress is in the record
            copied[src["id"]] = new_id
            done_now += 1
    finally:
        rec["migration"]["last_copy_at"] = _now()
        # Any copy since the last verification makes that verification stale
        rec["migration"].pop("verified_at", None)
        drive_storage.save_record(client_id, rec)
    return {"copied_now": done_now, "remaining": sum(1 for f in files if f["id"] not in copied),
            "failed": failed}


# ─── Verify ──────────────────────────────────────────────────────────────────

def compare_copy(src: dict, dst: dict) -> str:
    """"" when dst is a faithful copy of src, else the reason. Pure."""
    if not dst:
        return "missing"
    if dst.get("trashed"):
        return "trashed"
    if str(src.get("mimeType") or "").startswith(NATIVE_PREFIX):
        return "" if dst.get("mimeType") == src.get("mimeType") else "type_differs"
    if str(dst.get("size")) != str(src.get("size")):
        return "size_differs"
    if not src.get("md5Checksum") or dst.get("md5Checksum") != src.get("md5Checksum"):
        return "checksum_differs"
    return ""


def verify(client_id: int) -> dict:
    """Fresh, complete check of every source file against its copy. Records
    the result; {"ok", "checked", "problems": [{id, name, reason}]}."""
    drive = _drive()
    rec = _client_record(client_id)
    root = _source_root(client_id)
    files = []
    if root:
        with drive_storage.as_service_account():
            files = walk(root)
    copied = rec["migration"]["copied"]
    problems = []
    for src in files:
        dst_id = copied.get(src["id"])
        dst = {}
        if dst_id:
            try:
                with drive_storage.as_client(client_id):
                    dst = drive.get_meta(dst_id, "id,mimeType,size,md5Checksum,trashed,appProperties")
            except Exception as e:
                problems.append({"id": src["id"], "name": src.get("name", ""), "reason": f"unreadable:{type(e).__name__}"})
                continue
            if (dst.get("appProperties") or {}).get("uallak_src_id") not in (None, src["id"]):
                problems.append({"id": src["id"], "name": src.get("name", ""), "reason": "wrong_source"})
                continue
        reason = compare_copy(src, dst)
        if reason:
            problems.append({"id": src["id"], "name": src.get("name", ""), "reason": reason})
    ok = not problems
    rec["migration"]["last_verify"] = {"at": _now(), "ok": ok, "checked": len(files), "problems": len(problems)}
    if ok:
        rec["migration"]["verified_at"] = _now()
    else:
        rec["migration"].pop("verified_at", None)
    drive_storage.save_record(client_id, rec)
    return {"ok": ok, "checked": len(files), "problems": problems[:50]}


# ─── Flip and free ───────────────────────────────────────────────────────────

def flip(client_id: int) -> dict:
    """Point the client's storage at their own Drive — only after a FRESH
    verification passes here and now."""
    result = verify(client_id)
    if not result["ok"]:
        raise MigrationRefused("not_verified", f"{len(result['problems'])} file(s) without a verified copy")
    rec = _client_record(client_id)
    rec["pointer"] = drive_storage.CLIENT
    rec["migration"]["flipped_at"] = _now()
    drive_storage.save_record(client_id, rec)
    return {"ok": True, "checked": result["checked"]}


def free_shared(client_id: int, confirm: str) -> dict:
    """Trash OUR copy — only with the typed phrase, only after the flip, and
    only after yet another fresh verification."""
    if (confirm or "").strip() != FREE_PHRASE.format(id=int(client_id)):
        raise MigrationRefused("confirm_required", FREE_PHRASE.format(id=int(client_id)))
    rec = _client_record(client_id)
    if rec.get("pointer") != drive_storage.CLIENT:
        raise MigrationRefused("not_flipped", "the client's files still go to our Shared Drive")
    result = verify(client_id)
    if not result["ok"]:
        raise MigrationRefused("not_verified", f"{len(result['problems'])} file(s) without a verified copy")
    root = _source_root(client_id)
    if root:
        with drive_storage.as_service_account():
            _drive().trash_file(root)
    rec = _client_record(client_id)
    rec["migration"]["freed_at"] = _now()
    drive_storage.save_record(client_id, rec)
    return {"ok": True, "trashed": root, "checked": result["checked"]}


def status(client_id: int) -> dict:
    """The admin drawer's view of it — no Drive call."""
    rec = drive_storage.record(client_id)
    mig = rec.get("migration") or {}
    return {"connected": bool(rec.get("root")), "pointer": rec.get("pointer") or drive_storage.SHARED,
            "copied": len(mig.get("copied") or {}), "in_progress": bool(mig.get("in_progress")),
            "last_copy_at": mig.get("last_copy_at"), "last_verify": mig.get("last_verify"),
            "verified_at": mig.get("verified_at"), "flipped_at": mig.get("flipped_at"),
            "freed_at": mig.get("freed_at"), "link": rec.get("link", "")}
