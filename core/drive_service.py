"""Google Drive plumbing — the offboarded-client archive AND the per-client
media folders (agents/media_agent.py). Same service account for both; they
differ only in the root folder (DRIVE_ARCHIVE_FOLDER_ID vs
DRIVE_MEDIA_FOLDER_ID) and in which `is_*configured()` gates them.

STORAGE GOTCHA (2026-10-13): a service account owns NO storage, so it cannot
create files inside a folder in someone's personal My Drive — Drive answers
403 "Service Accounts do not have storage quota". Roots must be folders in a
SHARED DRIVE with the service account as Content manager (classify_error
names this case so the alert says exactly that).

Retention model (business decision, 2026-07): when a client closes or
transfers out, their full record is exported to a Google Drive folder WE
control - that file is the long-term (accounting-grade) archive - and the
live rows are then purged from Supabase. Drive, not the database, is where
closed-client history lives.

Auth model: a service account (GOOGLE_SERVICE_ACCOUNT_JSON - the full JSON
key as one env var, raw or base64-encoded; see _service_account_info) uploads into a folder in Johnny's own Google Drive
(DRIVE_ARCHIVE_FOLDER_ID) that was shared with the service account as
Editor. A service account has no UI and its own Drive is invisible - the
shared folder is what makes the archive show up in a human's Drive.

google-auth does the JWT signing (already installed transitively via
google-cloud-pubsub, and pinned explicitly in requirements.txt); the Drive
calls themselves are plain REST via httpx, same style as the other services.
"""
import base64
import json
import os
import secrets
import time

import httpx

from agents.keys_agent import get_key

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
TIMEOUT = 60

_token_cache = {"token": None, "expires_at": 0}


def is_configured() -> bool:
    """Both env vars present. Callers must check this BEFORE offboarding-time
    archiving - when it's False the purge must not run either (records stay
    in the DB until the archive can actually be written)."""
    return bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
                and os.environ.get("DRIVE_ARCHIVE_FOLDER_ID"))


def is_media_configured() -> bool:
    """The client MEDIA folders (media_agent) need the service account and
    DRIVE_MEDIA_FOLDER_ID — and nothing else. They used to be gated on
    is_configured(), which also demands the offboarding ARCHIVE folder, so a
    missing archive var silently disabled every client's media folder."""
    return bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
                and os.environ.get("DRIVE_MEDIA_FOLDER_ID"))


class DriveCredentialError(RuntimeError):
    """GOOGLE_SERVICE_ACCOUNT_JSON can't be turned into a service-account key.
    The message names the variable and what was wrong — NEVER its value."""


class DriveResponseError(RuntimeError):
    """Drive answered 2xx with a body that isn't the JSON we asked for."""


def _service_account_info() -> dict:
    """The service-account key from GOOGLE_SERVICE_ACCOUNT_JSON, accepted as
    raw JSON OR base64-encoded JSON (Cloud Run holds it base64 — a value that
    starts "ewog…", which is base64 for '{\\n').

    Root cause of 2026-10-14's "Expecting value: line 1 column 1 (char 0)":
    this used to be a bare json.loads() of the raw value, which fails on the
    very first character of a base64 string — before any Drive call, so it
    looked like an unexplained Drive error rather than a config one."""
    name = "GOOGLE_SERVICE_ACCOUNT_JSON"
    raw = (get_key(name) or "").strip()
    if not raw:
        raise DriveCredentialError(f"{name} is empty")
    if raw.startswith("{"):
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as e:
            # e's message carries positions only ("line 5 column 3"), no content
            raise DriveCredentialError(f"{name} looks like raw JSON but does not parse ({e.msg} "
                                       f"at line {e.lineno} column {e.colno})") from None
    else:
        compact = "".join(raw.split())
        try:
            decoded = base64.b64decode(compact + "=" * (-len(compact) % 4),
                                       altchars=b"-_" if ("-" in compact or "_" in compact) else None,
                                       validate=False)
            info = json.loads(decoded.decode("utf-8"))
        except Exception as e:
            raise DriveCredentialError(f"{name} is neither raw JSON nor base64-encoded JSON "
                                       f"({type(e).__name__} while decoding it as base64)") from None
    if not isinstance(info, dict):
        raise DriveCredentialError(f"{name} parsed, but is not a JSON object")
    missing = [k for k in ("type", "client_email", "private_key") if not info.get(k)]
    if missing:
        raise DriveCredentialError(f"{name} parsed, but is missing {', '.join(missing)} — "
                                   f"it must be the full service-account key file")
    if info.get("type") != "service_account":
        raise DriveCredentialError(f"{name} is a '{info.get('type')}' key, not a service-account key")
    return info


def service_account_email() -> str:
    """Which service account Drive calls run as (an address, not a secret) —
    the one that must be Content manager on the Shared Drive."""
    return _service_account_info().get("client_email", "")


def _json(response: httpx.Response, what: str) -> dict:
    """response.json() with the empty/non-JSON case made explicit: status, size
    and the first 200 characters of the body (Drive bodies carry no secrets;
    the request's Authorization header is never included)."""
    try:
        return response.json()
    except ValueError:
        body = (response.text or "").strip()
        raise DriveResponseError(
            f"Drive {what}: HTTP {response.status_code} returned "
            + ("an EMPTY body" if not body else f"a non-JSON body ({len(body)} chars): {body[:200]!r}")
        ) from None


def classify_error(exc: Exception, target: str = "folder") -> str:
    """Name the Drive failure so the alert can say what to FIX.

      storage_quota — "Service Accounts do not have storage quota": the root
                      folder is in a personal My Drive. A service account owns
                      no storage, so it can't create files there; the root must
                      live in a SHARED DRIVE (service account as Content
                      manager). Every call here already sends
                      supportsAllDrives=true, so no code change is needed.
      no_access     — 404/403 on the root: not shared with the service
                      account, or a wrong DRIVE_MEDIA_FOLDER_ID.
      auth          — the service-account key itself is bad/revoked.
      credentials   — GOOGLE_SERVICE_ACCOUNT_JSON can't even be read as a key.
      bad_response  — Drive answered with an empty / non-JSON body.
      file_not_found / file_permission — a 404 / 403 on a FILE (target="file"),
                      e.g. a probe file the folder listing had just shown. The
                      folder is fine; "no_access" (which blames
                      DRIVE_MEDIA_FOLDER_ID) is only for calls ON the folder.
      other         — anything else (network, 5xx)."""
    if isinstance(exc, DriveCredentialError):
        return "credentials"
    if isinstance(exc, DriveResponseError):
        return "bad_response"
    text, status = str(exc), None
    response = getattr(exc, "response", None)
    if response is not None:
        status = response.status_code
        try:
            text = f"{text} {response.text}"
        except Exception:
            pass
    lowered = text.lower()
    if "storagequotaexceeded" in lowered or "do not have storage quota" in lowered:
        return "storage_quota"
    if target == "file" and (status == 404 or "notfound" in lowered):
        return "file_not_found"
    if target == "file" and (status == 403 or "insufficientfilepermissions" in lowered):
        return "file_permission"
    if status in (403, 404) or "notfound" in lowered or "insufficientfilepermissions" in lowered:
        return "no_access"
    if status == 401 or "invalid_grant" in lowered or "unauthorized_client" in lowered:
        return "auth"
    return "other"


def archive_root_folder_id() -> str:
    return get_key("DRIVE_ARCHIVE_FOLDER_ID")


def _access_token() -> str:
    if _token_cache["token"] and _token_cache["expires_at"] > time.time():
        return _token_cache["token"]
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    info = _service_account_info()
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=[DRIVE_SCOPE])
    credentials.refresh(Request())
    # Drive access tokens live ~1h; refresh a few minutes early
    _token_cache["token"] = credentials.token
    _token_cache["expires_at"] = time.time() + 3300
    return credentials.token


def _headers() -> dict:
    return {"Authorization": f"Bearer {_access_token()}"}


def _check(response: httpx.Response):
    """raise_for_status, but WITH Drive's error body in the message — the
    bare version says only "403 Forbidden", which hid the storage-quota cause
    from every existing media/avatar/content-doc failure alert."""
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"Drive {response.request.method} {response.status_code}: {response.text[:400]}",
            request=response.request, response=response)


def ensure_folder(name: str, parent_id: str) -> str:
    """Find-or-create a subfolder by exact name under parent_id, returning its
    id - so a client who somehow offboards twice reuses one folder."""
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    query = (f"name = '{escaped}' and '{parent_id}' in parents "
             "and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    response = httpx.get(
        DRIVE_FILES_URL,
        headers=_headers(),
        params={"q": query, "fields": "files(id)", "pageSize": 1,
                "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"},
        timeout=TIMEOUT,
    )
    _check(response)
    files = _json(response, "folder lookup").get("files", [])
    if files:
        return files[0]["id"]

    response = httpx.post(
        DRIVE_FILES_URL,
        headers={**_headers(), "Content-Type": "application/json"},
        params={"supportsAllDrives": "true", "fields": "id"},
        json={"name": name, "mimeType": "application/vnd.google-apps.folder",
              "parents": [parent_id]},
        timeout=TIMEOUT,
    )
    _check(response)
    created = _json(response, "folder create")
    if not created.get("id"):
        raise DriveResponseError(f"Drive folder create: HTTP {response.status_code} returned no folder id")
    return created["id"]


def upload_bytes(folder_id: str, filename: str, content: bytes, mime_type: str) -> dict:
    """Binary upload (images/videos/docs for the media agent). Same multipart
    scheme as upload_json but with raw bytes as the media part. Returns
    Drive's {id, size, name, webViewLink}."""
    metadata = json.dumps({"name": filename, "parents": [folder_id]}, ensure_ascii=False)
    boundary = f"uallak_{secrets.token_hex(16)}"
    body = (
        (f"--{boundary}\r\n"
         "Content-Type: application/json; charset=UTF-8\r\n\r\n"
         f"{metadata}\r\n"
         f"--{boundary}\r\n"
         f"Content-Type: {mime_type}\r\n"
         "Content-Transfer-Encoding: base64\r\n\r\n").encode("utf-8")
        + base64.b64encode(content)
        + f"\r\n--{boundary}--".encode("utf-8")
    )
    response = httpx.post(
        DRIVE_UPLOAD_URL,
        headers={**_headers(), "Content-Type": f"multipart/related; boundary={boundary}"},
        params={"uploadType": "multipart", "supportsAllDrives": "true",
                "fields": "id,size,name,webViewLink"},
        content=body,
        timeout=TIMEOUT,
    )
    _check(response)
    return _json(response, "upload")


def share_with_user(file_id: str, email: str, role: str = "reader") -> bool:
    """Grant a user access to a file/folder (the client's window into their
    media). Best-effort: a failed share must not fail the upload that
    preceded it — the media exists either way."""
    try:
        response = httpx.post(
            f"{DRIVE_FILES_URL}/{file_id}/permissions",
            headers={**_headers(), "Content-Type": "application/json"},
            params={"supportsAllDrives": "true", "sendNotificationEmail": "false"},
            json={"type": "user", "role": role, "emailAddress": email},
            timeout=TIMEOUT,
        )
        return response.status_code == 200
    except Exception as e:
        print(f"[drive_service] share with {email} failed (non-fatal): {e}")
        return False


def get_link(file_id: str) -> str:
    """The human-browsable webViewLink for a file/folder."""
    response = httpx.get(
        f"{DRIVE_FILES_URL}/{file_id}",
        headers=_headers(),
        params={"fields": "webViewLink", "supportsAllDrives": "true"},
        timeout=TIMEOUT,
    )
    _check(response)
    return _json(response, "get link").get("webViewLink", "")


def list_files(folder_id: str) -> list:
    """Non-folder files directly inside a folder — the avatar agent reads the
    client's uploaded source material this way. Returns
    [{id, name, mimeType, size}]."""
    response = httpx.get(
        DRIVE_FILES_URL,
        headers=_headers(),
        params={"q": f"'{folder_id}' in parents and trashed = false "
                     "and mimeType != 'application/vnd.google-apps.folder'",
                "fields": "files(id,name,mimeType,size)", "pageSize": 100,
                "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"},
        timeout=TIMEOUT,
    )
    _check(response)
    return _json(response, "list files").get("files", [])


# ─── Read-only listing for the Media tab mirror (core/drive_mirror.py) ───────
# Metadata only — these never download a file body.
MIRROR_FILE_FIELDS = "id,name,mimeType,modifiedTime,size,thumbnailLink,webViewLink"
FOLDER_MIME = "application/vnd.google-apps.folder"


def list_children(folder_id: str, page_token: str = "", page_size: int = 40) -> dict:
    """One page of a folder's direct children (files AND subfolders), folders
    first then newest. {"files": [...], "next_page_token": str}."""
    params = {"q": f"'{folder_id}' in parents and trashed = false",
              "fields": f"nextPageToken,files({MIRROR_FILE_FIELDS})",
              "orderBy": "folder,modifiedTime desc", "pageSize": int(page_size),
              "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
    if page_token:
        params["pageToken"] = page_token
    response = httpx.get(DRIVE_FILES_URL, headers=_headers(), params=params, timeout=TIMEOUT)
    _check(response)
    body = _json(response, "list folder")
    return {"files": body.get("files", []), "next_page_token": body.get("nextPageToken", "")}


def get_meta(file_id: str, fields: str = "id,name,mimeType,parents") -> dict:
    """One file's metadata (never its content)."""
    response = httpx.get(f"{DRIVE_FILES_URL}/{file_id}", headers=_headers(),
                         params={"fields": fields, "supportsAllDrives": "true"}, timeout=TIMEOUT)
    _check(response)
    return _json(response, "file metadata")


def fetch_thumbnail(thumbnail_link: str, max_bytes: int) -> tuple:
    """GET a thumbnailLink AS the service account (for when the browser can't
    load it directly). Returns (bytes, content_type). Refuses anything that
    isn't an image or exceeds max_bytes."""
    with httpx.stream("GET", thumbnail_link, headers=_headers(), timeout=20,
                      follow_redirects=True) as response:
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(f"thumbnail GET {response.status_code}",
                                        request=response.request, response=response)
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise DriveResponseError(f"thumbnail is not an image ({content_type[:40]})")
        chunks, total = [], 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise DriveResponseError(f"thumbnail larger than {max_bytes} bytes")
            chunks.append(chunk)
    return b"".join(chunks), content_type


def trash_file(file_id: str) -> None:
    """Move one file we created to the trash (the Drive self-test's probe
    file). Raises on failure.

    Trash, not files.delete (2026-10-14): a PERMANENT delete of a Shared Drive
    item requires the Manager (organizer) role, and our service account is a
    Content manager — which may trash but not delete. Drive answered the
    delete with 404 "File not found" on a file it had just listed (the call
    already sent supportsAllDrives=true, so that wasn't it). Shared Drive
    trash empties itself after 30 days."""
    response = httpx.patch(f"{DRIVE_FILES_URL}/{file_id}",
                           headers={**_headers(), "Content-Type": "application/json"},
                           params={"supportsAllDrives": "true", "fields": "id,trashed"},
                           json={"trashed": True}, timeout=TIMEOUT)
    _check(response)


def find_files_by_prefix(folder_id: str, prefix: str) -> list:
    """Non-trashed files in folder_id whose name starts with prefix (the
    self-test's leftover probes). [{id, name}]"""
    escaped = prefix.replace("\\", "\\\\").replace("'", "\\'")
    response = httpx.get(
        DRIVE_FILES_URL,
        headers=_headers(),
        params={"q": f"'{folder_id}' in parents and trashed = false and name contains '{escaped}'",
                "fields": "files(id,name)", "pageSize": 100,
                "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"},
        timeout=TIMEOUT,
    )
    _check(response)
    return [f for f in _json(response, "probe lookup").get("files", [])
            if (f.get("name") or "").startswith(prefix)]


def download_file(file_id: str) -> bytes:
    """Raw file content (alt=media). Kept simple — source clips are phone
    videos/photos, well within memory."""
    response = httpx.get(
        f"{DRIVE_FILES_URL}/{file_id}",
        headers=_headers(),
        params={"alt": "media", "supportsAllDrives": "true"},
        follow_redirects=True,
        timeout=300,
    )
    _check(response)
    return response.content


def make_file_public(file_id: str) -> str:
    """Flip ONE file to anyone-with-link reader and return its direct-download
    URL — needed when publishing to Meta (their server fetches media from a
    public URL). Deliberately per-file: client folders stay restricted."""
    response = httpx.post(
        f"{DRIVE_FILES_URL}/{file_id}/permissions",
        headers={**_headers(), "Content-Type": "application/json"},
        params={"supportsAllDrives": "true"},
        json={"type": "anyone", "role": "reader"},
        timeout=TIMEOUT,
    )
    _check(response)
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def upload_google_doc(folder_id: str, filename: str, html_content: str) -> dict:
    """Uploads HTML that Drive CONVERTS into a native, editable Google Doc —
    not a downloadable .txt/.docx sitting in the folder. Same multipart
    upload as upload_bytes, but the two Content-Types differ on purpose: the
    metadata part's mimeType is the TARGET format Drive imports INTO
    (application/vnd.google-apps.document), while the content part's
    Content-Type (text/html) is the SOURCE format Drive imports FROM. Basic
    HTML (headings, <b>/<strong>, <ul>/<ol>, <p>) carries over as real Doc
    formatting. Returns Drive's {id, name, webViewLink}."""
    metadata = json.dumps(
        {"name": filename, "parents": [folder_id],
         "mimeType": "application/vnd.google-apps.document"},
        ensure_ascii=False,
    )
    boundary = f"uallak_{secrets.token_hex(16)}"
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        "Content-Type: text/html; charset=UTF-8\r\n\r\n"
        f"{html_content}\r\n"
        f"--{boundary}--"
    ).encode("utf-8")
    response = httpx.post(
        DRIVE_UPLOAD_URL,
        headers={**_headers(), "Content-Type": f"multipart/related; boundary={boundary}"},
        params={"uploadType": "multipart", "supportsAllDrives": "true",
                "fields": "id,name,webViewLink"},
        content=body,
        timeout=TIMEOUT,
    )
    _check(response)
    return _json(response, "Google Doc upload")


def upload_json(folder_id: str, filename: str, content: str) -> dict:
    """Multipart upload of one JSON document. Returns Drive's {id, size, name}.
    Callers treating this as a durable archive MUST verify id exists and
    size > 0 before deleting anything the file is supposed to replace."""
    metadata = json.dumps(
        {"name": filename, "parents": [folder_id], "mimeType": "application/json"},
        ensure_ascii=False,
    )
    # Random boundary so no conceivable export content (client chat text ends
    # up in these files) can collide with it
    boundary = f"uallak_{secrets.token_hex(16)}"
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{content}\r\n"
        f"--{boundary}--"
    ).encode("utf-8")
    response = httpx.post(
        DRIVE_UPLOAD_URL,
        headers={**_headers(), "Content-Type": f"multipart/related; boundary={boundary}"},
        params={"uploadType": "multipart", "supportsAllDrives": "true",
                "fields": "id,size,name"},
        content=body,
        timeout=TIMEOUT,
    )
    _check(response)
    return _json(response, "JSON upload")
