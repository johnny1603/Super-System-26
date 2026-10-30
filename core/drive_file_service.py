"""The client's OWN Google Drive as the home of their files — Google
verification ROUND 2, behind DRIVE_FILE_ENABLED, OFF by default.
docs/GOOGLE-ROUND2.md is the source of truth for the scope, the
justification and the demo. Mirrors core/search_console_service.py.

Scope: ONLY https://www.googleapis.com/auth/drive.file — Google's narrowest
Drive scope. With it the app can create files and manage the files IT
created; it can never list, open or change anything else in the client's
Drive. Full `drive` is deliberately NOT requested.

On connect (api_server's /api/oauth/google-drive/callback) one clearly named
folder, "uallak — {business}", is created in the client's My Drive with
today's subfolder structure (SUBFOLDERS). The grant is stored as
client_accounts platform='google_drive_file', account_id = that folder's id;
where new files go is decided by core/drive_storage.location — the pointer
flips to the client's Drive at once when our Shared Drive holds nothing of
theirs, otherwise after core/drive_migration has copied AND verified.

Honest limit, said in the client text and the docs: drive.file cannot see
files the client drops into the folder by hand — only files uallak itself
created (uploads from the chat and the dashboard ARE created by uallak).

While the flag is off: build_consent_url raises DriveFileDisabled, the routes
answer 404, the card stays hidden, and drive_storage keeps every file in our
Shared Drive exactly as today.

VERIFICATION STATUS: written against the Drive API v3 reference, never run
with a live grant.
"""
import os
from urllib.parse import urlencode

from agents.keys_agent import get_key
from core.google_ads_service import OAUTH_CONSENT_URL
from core.google_ads_service import exchange_code as _google_exchange_code


def enabled() -> bool:
    """DRIVE_FILE_ENABLED, read at call time (core/round2_flags)."""
    from core.round2_flags import drive_file_enabled
    return drive_file_enabled()


PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")
REDIRECT_PATH = "/api/oauth/google-drive/callback"
# THE client Drive scope list (round 2; docs/GOOGLE-ROUND2.md mirrors it).
# Not drive_service.DRIVE_SCOPES — that is OUR service account's own scope,
# never shown to a client.
DRIVE_FILE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
ROOT_PARENT = "root"          # Drive's alias for the user's My Drive
# Today's structure under a client's media folder (media_agent, chat_uploads,
# voice_recordings, avatar_agent, content_docs_agent, exports). Deeper
# folders (images/<platform>, videos/avatar, website-media/<page>) are still
# created on first use, as today.
SUBFOLDERS = ("images", "videos", "voice", "avatar-source", "scripts", "exports",
              "content-docs", "website-media", "documents")


class DriveFileDisabled(RuntimeError):
    """DRIVE_FILE_ENABLED is off: no consent, no client-Drive call."""


def _require_enabled():
    if not enabled():
        raise DriveFileDisabled("DRIVE_FILE_ENABLED is off")


def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def build_consent_url(state: str) -> str:
    _require_enabled()
    params = {
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(DRIVE_FILE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH_CONSENT_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    _require_enabled()
    return _google_exchange_code(code, redirect_uri())


def folder_name(business: str) -> str:
    name = " ".join((business or "").split())[:80] or "העסק שלי"
    return f"uallak — {name}"


def create_folders(business: str) -> dict:
    """Create (or find again, on a reconnect: drive.file sees the folders it
    made) the uallak folder and its subfolders in the CURRENT scope's Drive —
    the caller runs this inside drive_storage.as_client. Returns {"root",
    "parent", "link", "folders": {name: id}}."""
    _require_enabled()
    from core import drive_service as drive
    root = drive.ensure_folder(folder_name(business), ROOT_PARENT)
    folders = {name: drive.ensure_folder(name, root) for name in SUBFOLDERS}
    meta = drive.get_meta(root, "id,parents,webViewLink")
    return {"root": root, "parent": (meta.get("parents") or [""])[0],
            "link": meta.get("webViewLink", ""), "folders": folders}
