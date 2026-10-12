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
key as one env var) uploads into a folder in Johnny's own Google Drive
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


def classify_error(exc: Exception) -> str:
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
      other         — anything else (network, 5xx)."""
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
    info = json.loads(get_key("GOOGLE_SERVICE_ACCOUNT_JSON"))
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
    files = response.json().get("files", [])
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
    return response.json()["id"]


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
    return response.json()


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
    return response.json().get("webViewLink", "")


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
    return response.json().get("files", [])


def delete_file(file_id: str) -> None:
    """Permanently delete one file we created (the Drive self-test's probe
    file). Raises on failure."""
    response = httpx.delete(f"{DRIVE_FILES_URL}/{file_id}", headers=_headers(),
                            params={"supportsAllDrives": "true"}, timeout=TIMEOUT)
    _check(response)


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
    return response.json()


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
    return response.json()
