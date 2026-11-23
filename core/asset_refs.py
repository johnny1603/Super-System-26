"""
Real assets are REFERENCED, never copied by default (2026-11-23,
docs/DRIVE-FOLDERS.md §Referenced, not copied).

A real asset the agents choose from the client's connected Drive folders
stays where it is: the Social-queue item carries its own Drive file_id, and
selection, ranking, preview, the approval card, download and the TikTok /
YouTube sends (which stream byte ranges straight from Drive) all work from
that file_id plus Drive's own thumbnail link.

Copies happen in exactly two places:

1. A WORKING COPY, only when an operation genuinely needs one — today that is
   Meta publishing, which needs a PUBLIC link (making the client's own file
   public would change the sharing of their original). The copy goes into
   our media folder (`working/`), is made public there, and is deleted when
   the publish call returns (`release_for_url`). A working copy that was
   never released is deleted by the daily pass after WORKING_COPY_MAX_AGE_H.
   The same `working_copy()` context manager is the one to use for any future
   step that must hold bytes (e.g. compositing a real photo into a slide —
   the slide renderer does not do that today).

2. A PUBLISH SNAPSHOT: when a post is actually published, the assets it used
   are copied into the client's connected social_output folder, so the
   published post survives the client moving or deleting the original.
   Client setting `drive_snapshot_on_publish`, default ON; the manager can
   switch it off in the Control Room. No social_output folder connected → no
   snapshot (recorded on the item as `snapshot.skipped`).

Nothing else is ever copied. Working-copy bookkeeping: app_settings
`working_copies:<id>`.
"""

import contextlib
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "asset_refs"
PREFIX = "working_copies:"
WORKING_FOLDER = "working"
WORKING_COPY_MAX_AGE_H = 24
SNAPSHOT_KEY = "uallak_snapshot_of"
WORKING_KEY = "uallak_working_copy_of"


def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(line: str):
    print(f"[{SERVICE_NAME}] {line}")


# ─── Which file ids are the client's connected assets ───────────────────────

def is_connected(client_id: int, file_id: str) -> bool:
    """True when file_id is in this client's connected-folder index (not
    removed, its folder not excluded). This is what authorises a referenced
    file for preview / download / send, in place of the media-folder walk."""
    if not file_id:
        return False
    try:
        from core import drive_assets, drive_folders
        item = (drive_assets._load(client_id).get("items") or {}).get(str(file_id))
        if not item or item.get("state") in drive_assets.OUT_STATES:
            return False
        conn = next((c for c in drive_folders.load(client_id) if c.get("id") == item.get("connection_id")), None)
        return bool(conn) and not conn.get("excluded_effective")
    except Exception as e:
        _log(f"client {client_id}: connected-asset check failed ({type(e).__name__})")
        return False


# ─── Working copies (only when genuinely required) ──────────────────────────

def _read(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}")
            .limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    value = value if isinstance(value, dict) else {}
    value.setdefault("copies", {})
    return value


def _write(client_id: int, value: dict):
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": value,
                                        "updated_at": _now().isoformat()}, on_conflict="key").execute()


def make_working_copy(client_id: int, file_id: str, purpose: str) -> dict:
    """Copy the client's file into OUR media folder's `working/` subfolder
    (files.copy inside Drive). Recorded so it is always deleted later."""
    from agents import media_agent
    from core import drive_service, drive_storage
    with drive_storage.scope(client_id):
        folder = media_agent._subfolder(client_id, WORKING_FOLDER)
    with drive_storage.as_service_account():
        copy = drive_service.copy_file(file_id, folder, app_properties={WORKING_KEY: file_id})
    state = _read(client_id)
    state["copies"][copy["id"]] = {"source": file_id, "purpose": purpose, "at": _now().isoformat(), "url": ""}
    _write(client_id, state)
    return copy


def note_url(client_id: int, copy_id: str, url: str):
    state = _read(client_id)
    if copy_id in state["copies"]:
        state["copies"][copy_id]["url"] = url
        _write(client_id, state)


def delete_working_copy(client_id: int, copy_id: str) -> bool:
    from core import drive_service, drive_storage
    try:
        with drive_storage.scope(client_id):
            drive_service.delete_file(copy_id)
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status != 404:
            _log(f"client {client_id}: working copy {copy_id} not deleted ({type(e).__name__})")
            return False
    state = _read(client_id)
    state["copies"].pop(copy_id, None)
    _write(client_id, state)
    return True


@contextlib.contextmanager
def working_copy(client_id: int, file_id: str, purpose: str):
    """A working copy for exactly one operation; deleted when it completes."""
    copy = make_working_copy(client_id, file_id, purpose)
    try:
        yield copy
    finally:
        delete_working_copy(client_id, copy["id"])


def release_for_url(client_id: int, url: str) -> int:
    """After a publish that used a working copy's public link: delete it."""
    if not url:
        return 0
    try:
        state = _read(client_id)
    except Exception:
        return 0
    done = 0
    for copy_id, rec in list(state["copies"].items()):
        if rec.get("url") == url and delete_working_copy(client_id, copy_id):
            done += 1
    return done


def sweep_working_copies() -> int:
    """The daily pass: a working copy never released (its operation died)
    is deleted after WORKING_COPY_MAX_AGE_H."""
    try:
        rows = (_db().table("app_settings").select("key,value").like("key", f"{PREFIX}%")
                .limit(5000).execute().data or [])
    except Exception as e:
        _log(f"sweep: unreadable ({type(e).__name__})")
        return 0
    cutoff, done = _now() - timedelta(hours=WORKING_COPY_MAX_AGE_H), 0
    for row in rows:
        try:
            client_id = int(str(row["key"])[len(PREFIX):])
        except ValueError:
            continue
        for copy_id, rec in list(((row.get("value") or {}).get("copies") or {}).items()):
            try:
                at = datetime.fromisoformat(str(rec.get("at")))
            except ValueError:
                at = cutoff
            if at <= cutoff and delete_working_copy(client_id, copy_id):
                done += 1
    return done


# ─── The publish snapshot (setting, default ON) ─────────────────────────────

def snapshot_enabled(client_id: int) -> bool:
    try:
        from core import client_settings
        value = client_settings.get(client_id).get("drive_snapshot_on_publish")
    except Exception:
        value = None
    return True if value is None else bool(value)


def _social_output_folder(client_id: int) -> str:
    from core import drive_folders
    for c in drive_folders.load(client_id):
        if c.get("role") == "social_output" and not c.get("excluded_effective") and not c.get("parent_connection_id"):
            return c.get("folder_id", "")
    for c in drive_folders.load(client_id):
        if c.get("role") == "social_output" and not c.get("excluded_effective"):
            return c.get("folder_id", "")
    return ""


def snapshot_published(client_id: int, item: dict) -> dict:
    """Copy the connected-folder assets a just-published item used into the
    client's social_output folder. Returns what to store on the item as
    `snapshot`. Never raises."""
    try:
        if not snapshot_enabled(client_id):
            return {"skipped": "setting_off"}
        file_id = item.get("file_id") or ""
        if not file_id or not is_connected(client_id, file_id):
            return {}
        folder = _social_output_folder(client_id)
        if not folder:
            return {"skipped": "no_social_output_folder"}
        from core import drive_service, drive_storage
        with drive_storage.as_service_account():
            earlier = drive_service.find_by_app_property(folder, SNAPSHOT_KEY, file_id)
            copy = earlier[0] if earlier else drive_service.copy_file(
                file_id, folder, app_properties={SNAPSHOT_KEY: file_id, "uallak_queue_item": str(item.get("id", ""))})
        return {"file_id": copy.get("id", ""), "of": file_id, "folder_id": folder, "at": _now().isoformat()}
    except Exception as e:
        _log(f"client {client_id}: snapshot of {item.get('file_id')} failed ({type(e).__name__})")
        return {"failed": type(e).__name__}
