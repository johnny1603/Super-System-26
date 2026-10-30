"""THE storage seam for every client file: WHERE it lives and AS WHOM we
reach it. Round 2, behind DRIVE_FILE_ENABLED (core/round2_flags), OFF by
default.

Two places a client's files can live:

  shared_drive — today: `client-{id} — {name}` under OUR Shared Drive media
                 root (DRIVE_MEDIA_FOLDER_ID), reached as our service
                 account. Billed to uallak.
  client_drive — the "uallak — {business}" folder in the CLIENT's own My
                 Drive, created by core/drive_file_service at connect, reached
                 with the client's drive.file grant. drive.file lets us manage
                 ONLY files this app created; it can never read anything else
                 in their Drive.

`location(client_id)` is the ONE decision: client_drive only when the flag is
on, the client's google_drive_file row is active AND the pointer in their
record says so (core/drive_migration flips it after a verified copy, or
drive_file_service at connect when there was nothing to copy). Everything
else — flag off, not connected, migration not finished, any read failure —
is shared_drive, i.e. exactly today's behaviour.

Every Drive writer and reader for a client runs inside `scope(client_id)` (or
is decorated `@scoped`): media_agent's folders, drive_mirror, drive_download,
chat_uploads, voice_recordings, avatar_agent / avatar_video, content_docs,
exports, YouTube/TikTok source reads, agent_actions' file checks and the
verification demo. Inside it, drive_service._headers() uses the client's grant
when the files are in their Drive, and our service account otherwise. No
caller names either destination itself, and nothing but shared_parent_id()
reads DRIVE_MEDIA_FOLDER_ID (tests/test_drive_storage.py enforces both).

Record: app_settings `drive_storage:<id>` = {"root", "parent", "link",
"folders", "pointer", "connected_at", "migration": {...}} — never a token
(the grant is the encrypted google_drive_file row). Cleared by client_deletion.
"""
import contextlib
import functools
import inspect
import os
import time
from datetime import datetime, timezone

SERVICE_NAME = "drive_storage"
PLATFORM = "google_drive_file"
RECORD_PREFIX = "drive_storage:"
SHARED = "shared_drive"
CLIENT = "client_drive"
LOCATION_TTL_SECONDS = 120

# Admin alert while drive.file is not approved and we keep paying: a client's
# folder in OUR Shared Drive above this many GB (env DRIVE_SHARED_ALERT_GB)
DEFAULT_SHARED_ALERT_GB = 10.0
USAGE_ALERT_DAYS = 7

_location_cache = {}   # client_id -> (at, location)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enabled() -> bool:
    from core.round2_flags import drive_file_enabled
    return drive_file_enabled()


def shared_parent_id() -> str:
    """OUR Shared Drive media root — the ONLY reader of DRIVE_MEDIA_FOLDER_ID."""
    return os.environ.get("DRIVE_MEDIA_FOLDER_ID", "").strip()


def shared_alert_bytes() -> int:
    try:
        gb = float(os.environ.get("DRIVE_SHARED_ALERT_GB", "") or DEFAULT_SHARED_ALERT_GB)
    except ValueError:
        gb = DEFAULT_SHARED_ALERT_GB
    return int(gb * 1024 ** 3)


# ─── The record ──────────────────────────────────────────────────────────────

def _db():
    from agents.client_agent import _db as db
    return db()


def record(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value")
            .eq("key", f"{RECORD_PREFIX}{int(client_id)}").limit(1).execute().data or [])
    value = rows[0].get("value") if rows else {}
    return value if isinstance(value, dict) else {}


def save_record(client_id: int, value: dict):
    _db().table("app_settings").upsert(
        {"key": f"{RECORD_PREFIX}{int(client_id)}", "value": value, "updated_at": _now()},
        on_conflict="key").execute()
    forget(client_id)


def _grant_row(client_id: int) -> dict:
    from agents.client_agent import get_accounts
    return next((a for a in get_accounts(client_id)
                 if a.get("platform") == PLATFORM and a.get("status") == "active"), {})


def forget(client_id: int):
    """Drop every per-process cache that remembers WHERE this client's files
    are (a connect, a pointer flip, a disconnect)."""
    _location_cache.pop(client_id, None)
    try:
        from agents import media_agent
        media_agent._folder_cache.pop(client_id, None)
    except Exception:
        pass
    try:
        from core import drive_mirror
        drive_mirror.forget(client_id)
    except Exception:
        pass


# ─── THE decision ────────────────────────────────────────────────────────────

def location(client_id: int) -> dict:
    """{"kind": "client_drive", "root", "parent", "link"} or
    {"kind": "shared_drive", "parent": <Shared Drive media root>}. Never
    raises: any doubt is today's Shared Drive."""
    shared = {"kind": SHARED, "parent": shared_parent_id()}
    if not enabled() or not client_id:
        return shared
    hit = _location_cache.get(client_id)
    if hit and hit[0] > time.time() - LOCATION_TTL_SECONDS:
        return hit[1]
    try:
        row = _grant_row(client_id)
        rec = record(client_id) if row else {}
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: location read failed ({type(e).__name__}) — Shared Drive")
        return shared
    if row and rec.get("pointer") == CLIENT and rec.get("root") and rec.get("root") == row.get("account_id"):
        result = {"kind": CLIENT, "root": rec["root"], "parent": rec.get("parent") or "",
                  "link": rec.get("link") or ""}
    else:
        result = shared
    _location_cache[client_id] = (time.time(), result)
    return result


def configured(client_id: int) -> bool:
    """Can this client's files be written at all right now?"""
    if location(client_id)["kind"] == CLIENT:
        return True
    from core import drive_service
    return drive_service.is_media_configured()


# ─── As whom ─────────────────────────────────────────────────────────────────

def _client_token_fn(client_id: int):
    from core.secrets_vault import get_client_secret
    refresh_token = get_client_secret(client_id, PLATFORM)
    if not refresh_token:
        raise RuntimeError(f"client {client_id}: no drive.file grant stored")
    from core.google_ads_service import _access_token
    return lambda: _access_token(refresh_token)


@contextlib.contextmanager
def as_client(client_id: int):
    """Calls inside run with the client's drive.file grant (their Drive),
    whatever the pointer says — drive_file_service at connect and the
    migration's write side use this directly."""
    from core import drive_service
    token = drive_service._auth_override.set({"client_id": client_id, "token": _client_token_fn(client_id)})
    try:
        yield
    finally:
        drive_service._auth_override.reset(token)


@contextlib.contextmanager
def as_service_account():
    """Calls inside run as OUR service account (the Shared Drive) — the
    migration's read side and the usage scan."""
    from core import drive_service
    token = drive_service._auth_override.set(None)
    try:
        yield
    finally:
        drive_service._auth_override.reset(token)


@contextlib.contextmanager
def scope(client_id: int):
    """Run Drive calls for this client wherever their files live. Re-entrant
    for the same client; a nested scope for ANOTHER client switches to that
    client's location and back. Flag off: touches nothing — today's
    behaviour, the service account, exactly."""
    if not enabled():
        yield
        return
    from core import drive_service
    current = drive_service._auth_override.get()
    if current is not None and current.get("client_id") == client_id:
        yield
        return
    if location(client_id)["kind"] == CLIENT:
        with as_client(client_id):
            yield
    else:
        with as_service_account():
            yield


def scoped(fn):
    """Decorator for every function that reads or writes a client's Drive
    files: finds `client_id` among its arguments and runs it in scope()."""
    signature = inspect.signature(fn)
    if "client_id" not in signature.parameters:
        raise TypeError(f"{fn.__name__} has no client_id parameter")

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        client_id = signature.bind_partial(*args, **kwargs).arguments.get("client_id")
        with scope(client_id):
            return fn(*args, **kwargs)
    wrapper.__drive_scoped__ = True
    return wrapper


# ─── Shared Drive usage (item: alert while we still pay for storage) ─────────

def folder_usage(folder_id: str, max_items: int = 20000) -> dict:
    """{"bytes", "files", "complete"} under a folder, recursively, as whoever
    the current scope is. Google-native docs have no size and count 0."""
    from core import drive_service as drive
    total, files, pending, seen = 0, 0, [folder_id], 0
    while pending:
        folder, page_token = pending.pop(), ""
        while True:
            page = drive.list_children(folder, page_token=page_token, page_size=200,
                                       fields="nextPageToken,files(id,mimeType,size)")
            for item in page["files"]:
                seen += 1
                if item.get("mimeType") == drive.FOLDER_MIME:
                    pending.append(item["id"])
                else:
                    files += 1
                    total += int(item["size"]) if str(item.get("size") or "").isdigit() else 0
            page_token = page["next_page_token"]
            if not page_token or seen >= max_items:
                break
        if seen >= max_items:
            return {"bytes": total, "files": files, "complete": False}
    return {"bytes": total, "files": files, "complete": True}


def shared_usage_scan(client_ids: list) -> list:
    """For each client whose files are (still) in OUR Shared Drive: the size
    of their folder, and ONE admin alert per client per USAGE_ALERT_DAYS when
    it passes shared_alert_bytes(). Admin-triggered (and meant for a daily
    scheduler job). Returns one row per client checked."""
    from core import drive_mirror
    from core.chat_fallback import alert_once
    threshold, out = shared_alert_bytes(), []
    with as_service_account():
        for cid in client_ids:
            try:
                root = drive_mirror.shared_client_root(cid)
                if not root:
                    continue
                usage = folder_usage(root)
            except Exception as e:
                out.append({"client_id": cid, "error": type(e).__name__})
                continue
            over = usage["bytes"] > threshold
            out.append({"client_id": cid, "bytes": usage["bytes"], "files": usage["files"],
                        "complete": usage["complete"], "over_threshold": over,
                        "in_client_drive": location(cid)["kind"] == CLIENT})
            if over:
                alert_once(SERVICE_NAME, cid, "shared_drive_over_threshold",
                           f"client {cid}: their folder in OUR Shared Drive holds "
                           f"{usage['bytes'] / 1024 ** 3:.1f} GB (threshold {threshold / 1024 ** 3:.0f} GB, "
                           f"DRIVE_SHARED_ALERT_GB) — billed to uallak until drive.file is approved "
                           f"and their files move to their own Drive", USAGE_ALERT_DAYS)
    return out
