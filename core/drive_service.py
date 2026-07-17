"""Google Drive plumbing for the offboarded-client archive.

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
    response.raise_for_status()
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
    response.raise_for_status()
    return response.json()["id"]


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
    response.raise_for_status()
    return response.json()
