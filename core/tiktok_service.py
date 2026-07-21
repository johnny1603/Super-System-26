"""Low-level TikTok plumbing: OAuth2 (Login Kit) + Content Posting API +
video-stats HTTP. Mirrors core/meta_service.py's shape (agents pass relative
paths, one error class, OAuth helpers) — see .claude/skills/tiktok/SKILL.md
for the access-tier reality this integration lives under.

Auth model: each client authorizes THEIR OWN TikTok account via OAuth
("Connect Now" in the dashboard, same pattern as Meta/Google Ads). ONE
consent covers publishing + basic stats:
  platform='tiktok'  account_id=open_id (TikTok's user id),
                     access_token='{access_token}::{refresh_token}' (see
                     _split_tokens — TikTok has no analog to Meta's
                     Page-token-never-expires shortcut: the access token
                     always expires in 24h, so the refresh token MUST be
                     stored too; client_accounts has no separate column for
                     it, so it rides along composite-encoded in access_token,
                     same accepted pattern as website_agent's
                     'username:app_password').
TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET are ours, one pair for the whole
system (in keys_agent KEYS) — the equivalent of META_APP_ID/SECRET.

Business logic lives in agents/tiktok_content_agent.py — this module only
talks HTTP.

VERIFICATION STATUS: written against TikTok's public developer docs
(developers.tiktok.com), never run with a live key/app — same accepted MVP
state as every other services module in this codebase before its first real
key. The likeliest first-round fixes: exact response field names for
get_user_info (TikTok's docs excerpt didn't confirm GET vs a query-param
shape) and whatever undocumented chunk-size ceiling FILE_UPLOAD enforces
(a single-chunk upload is used here — see upload_video_chunk — since
uallak's short-form clips are always well under any plausible ceiling).
"""
import os
from urllib.parse import urlencode

import httpx

from agents.keys_agent import get_key

API_BASE = "https://open.tiktokapis.com/v2"
OAUTH_AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://uallak.com")
REDIRECT_PATH = "/api/oauth/tiktok/callback"
TIMEOUT = 60  # video chunk uploads can be slow on the client's connection... no,
              # WE do the upload server-side (Drive -> TikTok), but keep a
              # generous timeout since video bytes can be several MB

# video.publish: Content Posting API (both inbox and direct-post init calls).
# video.list: engagement counts (query/list endpoints) - the closest thing to
# "engagement visibility" the public API offers (see the skill for why real
# comment CONTENT reading isn't available, unlike Meta).
# user.info.basic: added by default with Login Kit; needed for get_user_info.
OAUTH_SCOPES = ["user.info.basic", "video.publish", "video.list"]

TOKEN_DELIMITER = "::"  # see the module docstring - composite access_token storage


class TikTokAPIError(RuntimeError):
    """API error with TikTok's error code attached, so callers can tell an
    expired/revoked token from a plain bad request."""

    def __init__(self, message, code=None, log_id=None):
        super().__init__(message)
        self.code = code
        self.log_id = log_id


def split_tokens(stored_access_token: str) -> tuple:
    """'{access}::{refresh}' -> (access, refresh). See the module docstring —
    client_accounts has no dedicated refresh_token column."""
    access, _, refresh = stored_access_token.partition(TOKEN_DELIMITER)
    return access, refresh


def join_tokens(access_token: str, refresh_token: str) -> str:
    return f"{access_token}{TOKEN_DELIMITER}{refresh_token}"


def _headers(access_token: str, json_body: bool = True) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    if json_body:
        headers["Content-Type"] = "application/json; charset=UTF-8"
    return headers


def _raise_api_error(response: httpx.Response):
    try:
        body = response.json()
        err = body.get("error", {})
    except Exception:
        raise TikTokAPIError(f"{response.status_code}: {response.text[:300]}")
    code = err.get("code", "")
    if code and code != "ok":
        raise TikTokAPIError(
            f"{response.status_code} (code {code}): {err.get('message', '')}",
            code=code, log_id=err.get("log_id"))
    if response.status_code != 200:
        raise TikTokAPIError(f"{response.status_code}: {err.get('message', response.text[:300])}",
                             code=code, log_id=err.get("log_id"))


def api_post(path: str, access_token: str, json_body: dict = None, params: dict = None) -> dict:
    # TikTok's convention (video/list, video/query, user/info): which fields
    # come back is a QUERY-STRING param even on POST endpoints - cursor/
    # max_count/filters are the JSON body.
    response = httpx.post(f"{API_BASE}/{path.lstrip('/')}", headers=_headers(access_token),
                          params=params or {}, json=json_body or {}, timeout=TIMEOUT)
    _raise_api_error(response)
    return response.json().get("data", {})


def api_get(path: str, access_token: str, params: dict = None) -> dict:
    response = httpx.get(f"{API_BASE}/{path.lstrip('/')}", headers=_headers(access_token, json_body=False),
                         params=params or {}, timeout=TIMEOUT)
    _raise_api_error(response)
    return response.json().get("data", {})


# 190/401-equivalent: TikTok's expired/invalid token error code. Access
# tokens always expire in 24h regardless of activity - unlike Meta's Page
# tokens, refreshing is the NORMAL path here, not just a failure recovery.
TOKEN_ERROR_CODES = {"access_token_invalid", "access_token_expired"}


def is_token_error(exc) -> bool:
    return isinstance(exc, TikTokAPIError) and exc.code in TOKEN_ERROR_CODES


# ─── OAuth flow ─────────────────────────────────────────────────────────────

def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def build_consent_url(state: str) -> str:
    params = {
        "client_key": get_key("TIKTOK_CLIENT_KEY"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": ",".join(OAUTH_SCOPES),
        "state": state,
    }
    return f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    """Authorization code -> {access_token, refresh_token, open_id, expires_in,
    refresh_expires_in, ...}. Access token lives 24h, refresh token 365 days —
    BOTH must be stored (see join_tokens)."""
    response = httpx.post(
        f"{API_BASE}/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": get_key("TIKTOK_CLIENT_KEY"),
            "client_secret": get_key("TIKTOK_CLIENT_SECRET"),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(),
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        _raise_api_error(response)
    body = response.json()
    if body.get("error") and body["error"] not in ("", "ok"):
        raise TikTokAPIError(f"token exchange failed: {body.get('error_description', body['error'])}")
    return body


def refresh_access_token(refresh_token: str) -> dict:
    """A dead/aging access token -> a fresh one, using the 365-day refresh
    token. Returns a NEW refresh_token too (TikTok rotates it) — callers
    must re-store both, not just the access token."""
    response = httpx.post(
        f"{API_BASE}/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": get_key("TIKTOK_CLIENT_KEY"),
            "client_secret": get_key("TIKTOK_CLIENT_SECRET"),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        _raise_api_error(response)
    return response.json()


def get_user_info(access_token: str) -> dict:
    """{open_id, display_name, avatar_url, ...} — the connected account's
    identity, analogous to Meta's get_pages() asset discovery."""
    return api_get("user/info/", access_token,
                   params={"fields": "open_id,display_name,avatar_url"})


# ─── Content Posting API (publishing) ──────────────────────────────────────

def init_inbox_upload(access_token: str, video_size: int, chunk_size: int,
                      total_chunk_count: int) -> dict:
    """'Upload to Inbox' (Creator Post): sends the video to the CLIENT's own
    TikTok inbox for them to review/caption/publish themselves in-app. No
    SELF_ONLY/audit visibility restriction applies (that restriction is a
    Direct Post concept) — see the skill for why this is the mode
    tiktok_content_agent.publish() actually uses. Returns {publish_id,
    upload_url}."""
    return api_post("post/publish/inbox/video/init/", access_token, json_body={
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunk_count,
        },
    })


def init_direct_post(access_token: str, video_size: int, chunk_size: int,
                     total_chunk_count: int, privacy_level: str = "SELF_ONLY",
                     title: str = "") -> dict:
    """Direct Post: publishes straight to the client's profile — but every
    post from an UNAUDITED app is forced to privacy_level=SELF_ONLY
    (visible only to the account owner) regardless of what's requested here;
    lifting that needs a TikTok content-audit (see the skill). NOT called by
    tiktok_content_agent today (see publish()'s docstring for why inbox is
    the house choice) — kept here for a future business decision once/if
    the audit is pursued and Direct Post's forced-private v1 result is
    judged worth building UI/flow around anyway."""
    return api_post("post/publish/video/init/", access_token, json_body={
        "post_info": {"title": title, "privacy_level": privacy_level},
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunk_count,
        },
    })


def upload_video_chunk(upload_url: str, content: bytes, mime_type: str = "video/mp4",
                       chunk_start: int = 0, total_size: int = None) -> None:
    """PUTs the raw video bytes to the upload_url an init call returned.
    Single-chunk only (chunk_start=0, the whole file as one Content-Range) —
    correct for uallak's short-form clips; true multi-chunk splitting for
    very large files is not implemented (see the module docstring)."""
    total_size = total_size if total_size is not None else len(content)
    chunk_end = chunk_start + len(content) - 1
    response = httpx.put(
        upload_url,
        headers={
            "Content-Type": mime_type,
            "Content-Length": str(len(content)),
            "Content-Range": f"bytes {chunk_start}-{chunk_end}/{total_size}",
        },
        content=content,
        timeout=300,
    )
    if response.status_code not in (200, 201):
        raise TikTokAPIError(f"video chunk upload failed: {response.status_code} {response.text[:300]}")


def get_post_status(access_token: str, publish_id: str) -> dict:
    """Poll target. status: PROCESSING_UPLOAD -> PROCESSING_DOWNLOAD ->
    SEND_TO_USER_INBOX (inbox flow's success state) or PUBLISH_COMPLETE
    (direct-post's) -> FAILED (see fail_reason). publicaly_available_post_id
    [sic - TikTok's own field name] is only ever populated for a post that
    went publicly live - inbox-delivered videos won't have one until/unless
    the client finishes publishing it themselves, which this API has no way
    to observe (see get_engagement_summary in the agent for how engagement
    tracking works around that gap)."""
    return api_post("post/publish/status/fetch/", access_token,
                    json_body={"publish_id": publish_id})


# ─── Video stats (the closest thing to "engagement" the public API offers) ──

VIDEO_STAT_FIELDS = "id,create_time,title,like_count,comment_count,share_count,view_count"


def list_videos(access_token: str, cursor: int = None, max_count: int = 20) -> dict:
    """The account's own PUBLIC videos, newest first — {videos, cursor,
    has_more}. This is how engagement tracking works despite inbox uploads
    never returning a video_id: list what's public now and sum what's in the
    window, same shape as meta_content_agent.get_engagement_summary."""
    body = {"max_count": max_count}
    if cursor:
        body["cursor"] = cursor
    return api_post("video/list/", access_token, json_body=body,
                    params={"fields": VIDEO_STAT_FIELDS})


def query_videos(access_token: str, video_ids: list) -> dict:
    """Stats for up to 20 SPECIFIC known video ids."""
    return api_post("video/query/", access_token,
                    json_body={"filters": {"video_ids": video_ids[:20]}},
                    params={"fields": VIDEO_STAT_FIELDS})
