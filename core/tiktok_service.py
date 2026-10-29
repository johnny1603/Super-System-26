"""Low-level TikTok plumbing: OAuth2 (Login Kit) + Content Posting API +
video-stats HTTP. Mirrors core/meta_service.py's shape (agents pass relative
paths, one error class, OAuth helpers) — see .claude/skills/tiktok/SKILL.md
for the access-tier reality this integration lives under.

Auth model: each client authorizes THEIR OWN TikTok account via OAuth
("Connect Now" in the dashboard, same pattern as Meta/Google Ads). ONE
consent covers identity + publishing (engagement stats are behind
ENGAGEMENT_READS_ENABLED, off):
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
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")
REDIRECT_PATH = "/api/oauth/tiktok/callback"
TIMEOUT = 60  # video chunk uploads can be slow on the client's connection... no,
              # WE do the upload server-side (Drive -> TikTok), but keep a
              # generous timeout since video bytes can be several MB

# user.info.basic: Login Kit; needed for get_user_info (open_id + display name).
# video.upload: Content Posting API, Upload-to-Inbox ONLY
#   (post/publish/inbox/video/init/ + post/publish/status/fetch/) — the video
#   lands in the owner's TikTok inbox as a draft and THEY publish it in-app.
#   This is the only posting scope the app is configured for in the TikTok
#   developer portal (2026-10-29).
# video.publish is deliberately NOT requested: it is Direct Post's scope
#   (init_direct_post), which needs TikTok's content audit first. Requesting
#   it would also put a scope on the consent screen the demo never shows.
#
# ENGAGEMENT_READS_ENABLED is THE switch for video.list (Display API: the
# video/list + video/query engagement counts). OFF since 2026-10-29: TikTok's
# app review approves only scopes the submitted demo actually shows. While
# off, video.list is not in the consent URL and list_videos/query_videos
# refuse before any HTTP call; tiktok_content_agent.get_engagement_summary
# answers "not available yet" without touching TikTok. To turn it back on
# after a Display API review: flip this flag, add video.list in the TikTok
# developer portal, and have each client reconnect (older grants never
# carried it).
#
# Grants made before 2026-10-29 carry user.info.basic + video.publish (+
# video.list), not video.upload: identity and token refresh keep working, but
# an inbox upload is refused by TikTok with a scope error — publish() turns
# that into needs_reconnect (SCOPE_ERROR_CODES), and the reconnect asks for
# exactly OAUTH_SCOPES.
ENGAGEMENT_READS_ENABLED = False
ENGAGEMENT_SCOPE = "video.list"
UPLOAD_SCOPE = "video.upload"
OAUTH_SCOPES = ["user.info.basic", UPLOAD_SCOPE] + (
    [ENGAGEMENT_SCOPE] if ENGAGEMENT_READS_ENABLED else [])


def missing_scopes(granted: str) -> list:
    """OAUTH_SCOPES the token response's comma-separated `scope` did NOT
    grant (TikTok lets the user untick a scope on the consent screen).
    An absent/empty `scope` field is treated as unknown, not as nothing."""
    if not granted:
        return []
    have = {s.strip() for s in str(granted).split(",") if s.strip()}
    return [s for s in OAUTH_SCOPES if s not in have]

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


# The grant doesn't cover the call — e.g. a pre-2026-10-29 grant (video.publish,
# no video.upload) hitting the inbox endpoint. Only a new consent fixes it.
SCOPE_ERROR_CODES = {"scope_not_authorized", "scope_permission_missed"}


def is_scope_error(exc) -> bool:
    return isinstance(exc, TikTokAPIError) and exc.code in SCOPE_ERROR_CODES


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
    """Scope: video.upload. 'Upload to Inbox' (Creator Post): sends the video to the CLIENT's own
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
    """UNREACHABLE TODAY — nothing calls it, and it could not succeed: Direct
    Post needs the video.publish scope, which OAUTH_SCOPES deliberately does
    not request and the TikTok app is not configured for. Using it needs, in
    order: TikTok's content audit (until then every post from an unaudited
    app is forced to privacy_level=SELF_ONLY), video.publish added in the
    developer portal + OAUTH_SCOPES + its own app review, and clients
    reconnecting. Kept only for that future business decision; house policy
    (a human makes the final publish tap) is Upload-to-Inbox — see
    tiktok_content_agent.publish()."""
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


class EngagementReadsDisabled(TikTokAPIError):
    """video.list is not requested (ENGAGEMENT_READS_ENABLED is off), so a
    stats call would only fail at TikTok — refused here, before any HTTP."""

    def __init__(self):
        super().__init__(f"{ENGAGEMENT_SCOPE} is not requested (ENGAGEMENT_READS_ENABLED is off)",
                         code="scope_not_requested")


def _require_engagement_scope():
    if not ENGAGEMENT_READS_ENABLED:
        raise EngagementReadsDisabled()


def list_videos(access_token: str, cursor: int = None, max_count: int = 20) -> dict:
    """The account's own PUBLIC videos, newest first — {videos, cursor,
    has_more}. This is how engagement tracking works despite inbox uploads
    never returning a video_id: list what's public now and sum what's in the
    window, same shape as meta_content_agent.get_engagement_summary.
    Needs video.list — see ENGAGEMENT_READS_ENABLED."""
    _require_engagement_scope()
    body = {"max_count": max_count}
    if cursor:
        body["cursor"] = cursor
    return api_post("video/list/", access_token, json_body=body,
                    params={"fields": VIDEO_STAT_FIELDS})


def query_videos(access_token: str, video_ids: list) -> dict:
    """Stats for up to 20 SPECIFIC known video ids. Needs video.list — see
    ENGAGEMENT_READS_ENABLED."""
    _require_engagement_scope()
    return api_post("video/query/", access_token,
                    json_body={"filters": {"video_ids": video_ids[:20]}},
                    params={"fields": VIDEO_STAT_FIELDS})
