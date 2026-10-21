"""Low-level YouTube Data API v3 plumbing: OAuth2 consent + upload +
engagement reads. Business logic lives in agents/youtube_content_agent.py —
this module only talks HTTP, same split as meta/tiktok/gtm services.

Auth model — its own consent (like GTM, unlike a scope bolted onto Ads):
platform='youtube', account_id=channel id, access_token=refresh token.
Scopes below are Google SENSITIVE scopes (youtube.upload especially) — same
consent-screen + verification-resubmission reality as the GTM scopes (see
gtm_service's docstring); YouTube API services additionally reserve the
right to a compliance audit at scale. Development works now via test users.

COST REALITY (checked 2026-07-23 — the handoff's explicit question):
- The Data API has NO monetary billing at all — quota units, not money.
- Default project quota: 10,000 units/day, and videos.insert dropped from
  1,600 to ~100 units in Dec 2025 (with a 100 uploads/day cap) — so the
  default free quota supports ~100 uploads/day across ALL clients, far
  beyond our volume. API access itself therefore has ZERO real cost
  exposure at current scale; the only real costs are our operational time
  and the existing per-client Claude/media costs already priced elsewhere.
- Uploads are counted through core.api_call_counters ("youtube") anyway —
  same runaway-loop brake idiom as google_ads_service.

VERIFICATION STATUS: written against the Data API v3 reference, never run
with a live grant — same accepted MVP state as every service module before
its first real key.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from agents.keys_agent import get_key
from core.api_call_counters import increment_call_counter
# Deliberate reuse (same as gtm_service): token exchange/refresh are
# scope-agnostic OAuth calls — one implementation, not a second copy. The
# exchange is NOT redirect-agnostic, though: see exchange_code below.
from core.google_ads_service import OAUTH_CONSENT_URL, _access_token  # noqa: F401
from core.google_ads_service import exchange_code as _google_exchange_code

API_BASE = "https://www.googleapis.com/youtube/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3"
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")
REDIRECT_PATH = "/api/oauth/youtube/callback"
# THE YouTube scope list (one constant; docs/GOOGLE-SCOPES.md mirrors it).
# upload: videos.insert (upload_video). readonly: channels.list mine=true,
# playlistItems.list and videos.list on the client's own — often private —
# uploads (get_own_channel, list_recent_uploads, get_video_stats).
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
TIMEOUT = 30
UPLOAD_TIMEOUT = 600  # a long-form video upload is genuinely slow
DAILY_UPLOAD_LIMIT = 90  # default project cap is 100 videos.insert/day - brake below it


def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def exchange_code(code: str) -> dict:
    # Must send THIS consent's callback — Google rejects a mismatch (see
    # google_ads_service.exchange_code)
    return _google_exchange_code(code, redirect_uri())


def build_consent_url(state: str) -> str:
    params = {
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH_CONSENT_URL}?{urlencode(params)}"


def _get(refresh_token: str, path: str, params: dict) -> dict:
    response = httpx.get(f"{API_BASE}/{path.lstrip('/')}",
                         headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                         params=params, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"YouTube GET {path} failed: {response.status_code} {response.text[:300]}")
    return response.json()


def get_own_channel(refresh_token: str) -> dict:
    """The authorized user's own channel (id, title, uploads playlist id) —
    the asset-discovery step of the OAuth callback."""
    data = _get(refresh_token, "channels",
                {"part": "id,snippet,contentDetails", "mine": "true"})
    items = data.get("items", [])
    if not items:
        return {}
    channel = items[0]
    return {
        "channel_id": channel.get("id", ""),
        "title": (channel.get("snippet") or {}).get("title", ""),
        "uploads_playlist_id": ((channel.get("contentDetails") or {})
                                .get("relatedPlaylists") or {}).get("uploads", ""),
    }


MAX_UPLOAD_RETRIES = 5            # consecutive failed attempts before giving up
RETRYABLE_UPLOAD_STATUS = (500, 502, 503, 504)


class UploadFailed(RuntimeError):
    pass


def _received_up_to(response) -> int:
    """The next byte YouTube wants, from a 308's `Range: bytes=0-N` (no header =
    nothing stored yet)."""
    rng = response.headers.get("range") or response.headers.get("Range") or ""
    try:
        return int(rng.split("-")[-1]) + 1 if rng else 0
    except ValueError:
        raise UploadFailed(f"unreadable Range header from YouTube: {rng[:60]}")


def upload_video_stream(refresh_token: str, read_chunk, total_size: int, title: str,
                        description: str = "", mime_type: str = "video/mp4",
                        _sleep=None) -> dict:
    """STREAMED resumable upload (videos.insert, uploadType=resumable):
    initiate with the metadata and the total size, then PUT the bytes in
    UPLOAD_CHUNK_BYTES pieces, each with `Content-Range: bytes a-b/total`.
    `read_chunk(offset, length) -> bytes` supplies them (Drive range reads,
    drive_service.read_range), so at most ONE chunk is in memory, whatever the
    file size.

    Resume: a 308 says how much YouTube stored (its Range header) and the next
    chunk starts there; a 5xx or a dropped connection asks the session where
    it stands (`Content-Range: bytes */total`) and continues from that byte —
    MAX_UPLOAD_RETRIES consecutive failures, with backoff, then UploadFailed.

    Always PRIVATE: there is no privacy parameter — a human makes the video
    public in YouTube Studio (same final-tap principle as PAUSED campaigns,
    WordPress drafts, TikTok's inbox)."""
    import time
    from core.video_limits import UPLOAD_CHUNK_BYTES, fits
    sleep = _sleep or time.sleep
    if not fits(total_size):
        raise UploadFailed(f"video size {total_size} is outside the upload limit")
    count = increment_call_counter("youtube", window_days=1)
    if count > DAILY_UPLOAD_LIMIT:
        raise RuntimeError(f"YouTube daily upload brake reached ({DAILY_UPLOAD_LIMIT}) - refusing upload")

    metadata = {
        "snippet": {"title": title[:100], "description": description[:4900]},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
    }
    initiate = httpx.post(
        f"{UPLOAD_BASE}/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={"Authorization": f"Bearer {_access_token(refresh_token)}",
                 "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": mime_type,
                 "X-Upload-Content-Length": str(total_size)},
        json=metadata, timeout=TIMEOUT)
    if initiate.status_code != 200:
        raise UploadFailed(f"upload initiate failed: {initiate.status_code} {initiate.text[:300]}")
    session_url = initiate.headers.get("location") or initiate.headers.get("Location")
    if not session_url:
        raise UploadFailed("upload initiate returned no session Location header")

    def put(content: bytes, content_range: str):
        try:
            return httpx.put(session_url, content=content, timeout=UPLOAD_TIMEOUT, headers={
                "Authorization": f"Bearer {_access_token(refresh_token)}",
                "Content-Type": mime_type, "Content-Length": str(len(content)),
                "Content-Range": content_range})
        except httpx.TransportError:
            return None   # treated like a 5xx: ask the session where it stands

    offset, failures = 0, 0
    while True:
        if offset < total_size:
            length = min(UPLOAD_CHUNK_BYTES, total_size - offset)
            chunk = read_chunk(offset, length)
            if len(chunk) != length:
                raise UploadFailed(f"source returned {len(chunk)} of {length} bytes at {offset}")
            response = put(chunk, f"bytes {offset}-{offset + length - 1}/{total_size}")
            del chunk
        else:
            response = put(b"", f"bytes */{total_size}")   # everything sent: ask for the result

        if response is not None and response.status_code in (200, 201):
            return response.json()
        if response is not None and response.status_code == 308:
            offset, failures = _received_up_to(response), 0
            continue
        if response is not None and response.status_code not in RETRYABLE_UPLOAD_STATUS:
            raise UploadFailed(f"video upload failed: {response.status_code} {response.text[:300]}")

        failures += 1
        if failures > MAX_UPLOAD_RETRIES:
            raise UploadFailed(f"video upload gave up after {MAX_UPLOAD_RETRIES} retries at byte {offset}")
        sleep(min(2 ** failures, 30))
        status = put(b"", f"bytes */{total_size}")
        if status is not None and status.status_code in (200, 201):
            return status.json()
        if status is not None and status.status_code == 308:
            offset = _received_up_to(status)


def list_recent_uploads(refresh_token: str, uploads_playlist_id: str,
                        max_results: int = 20) -> list:
    """Video ids of the channel's newest uploads via the uploads playlist —
    1 quota unit, vs 100 for search.list. Always use this, never search."""
    data = _get(refresh_token, "playlistItems",
                {"part": "contentDetails", "playlistId": uploads_playlist_id,
                 "maxResults": max_results})
    return [((item.get("contentDetails") or {}).get("videoId"))
            for item in data.get("items", []) if item.get("contentDetails")]


def get_video_stats(refresh_token: str, video_ids: list) -> list:
    """Stats + publish dates for up to 50 known video ids (1 unit)."""
    if not video_ids:
        return []
    data = _get(refresh_token, "videos",
                {"part": "snippet,statistics,status", "id": ",".join(video_ids[:50])})
    videos = []
    for item in data.get("items", []):
        stats = item.get("statistics") or {}
        videos.append({
            "video_id": item.get("id"),
            "title": (item.get("snippet") or {}).get("title", ""),
            "published_at": (item.get("snippet") or {}).get("publishedAt", ""),
            "privacy_status": (item.get("status") or {}).get("privacyStatus", ""),
            "views": int(stats.get("viewCount", 0) or 0),
            "likes": int(stats.get("likeCount", 0) or 0),
            "comments": int(stats.get("commentCount", 0) or 0),
        })
    return videos


# ─── Public niche discovery (API KEY auth, not OAuth) ─────────────────────────
#
# Everything above this line acts as ONE CLIENT on their own channel (OAuth
# refresh token). This section is the opposite: an unauthenticated-user view of
# PUBLIC YouTube, used by core/competitor_research to find real videos in a
# client's niche. It therefore needs no client connection, no consent screen and
# no Google verification — just the project API key — which is exactly why it is
# the only outward-looking social source we can actually run today.
#
# QUOTA — the real constraint, read before adding a caller:
#   * search.list costs **100 units**; the default project quota is
#     **10,000 units/day**, so ~100 searches/day.
#   * That pool is SHARED with uploads (videos.insert, also ~100 units), which
#     already carry DAILY_UPLOAD_LIMIT = 90. 90 uploads + 90 searches would be
#     18,000 units — double the quota. Hence a deliberately smaller brake here:
#     DAILY_SEARCH_LIMIT = 40 (4,000 units) leaves ~6,000 for uploads.
#   * Uploads are a paid client deliverable; niche research is an enrichment
#     that degrades silently. If the two ever contend, research must lose.
#   * A quota increase is FREE but needs a Google audit form, not a billing
#     change. See .claude/skills/youtube/SKILL.md before assuming headroom.

SEARCH_UNITS = 100          # documented quota cost of one search.list call
DAILY_SEARCH_LIMIT = 40     # our brake: 4,000 units/day, under the shared 10,000
SEARCH_WINDOW_DAYS = 90     # "what works NOW", not an all-time megahit
SEARCH_MAX_RESULTS = 5
WATCH_URL = "https://www.youtube.com/watch?v={}"
CHANNEL_URL = "https://www.youtube.com/channel/{}"


def data_api_key() -> str:
    """Empty string when the key isn't set. Deliberately does NOT propagate
    `get_key`'s ValueError: this whole source is optional enrichment, so an unset
    key must degrade to "no YouTube results", never break a research call."""
    try:
        return get_key("YOUTUBE_DATA_API_KEY")
    except ValueError:
        return ""


def search_available() -> bool:
    """False when the key isn't configured — callers skip silently rather than
    erroring, so a missing key degrades this to the web-search-only behaviour
    that existed before."""
    return bool(data_api_key())


def _get_with_key(path: str, params: dict) -> dict:
    """Public read authenticated by API KEY, never a user token. A separate
    helper from `_get` on purpose: mixing the two auth modes in one function is
    how a client's OAuth token ends up on an unrelated public read."""
    params = dict(params, key=data_api_key())
    response = httpx.get(f"{API_BASE}/{path.lstrip('/')}", params=params, timeout=TIMEOUT)
    if response.status_code == 403:
        # quotaExceeded / dailyLimitExceeded read the same to us: stop, and say
        # which it was so the log points at the quota form rather than the code
        raise RuntimeError(f"YouTube API key read refused (quota or key restriction): "
                           f"{response.text[:300]}")
    if response.status_code != 200:
        raise RuntimeError(f"YouTube GET {path} failed: {response.status_code} {response.text[:300]}")
    return response.json()


def search_videos(query: str, max_results: int = SEARCH_MAX_RESULTS,
                  published_within_days: int = SEARCH_WINDOW_DAYS,
                  region_code: str = "IL", relevance_language: str = "he") -> list:
    """Public videos matching a niche query — REAL titles, channels, publish
    dates and ids, ordered by view count within the recent window.

    WHAT THIS DOES NOT RETURN, and must never be presented as: **view counts,
    subscriber counts, or a trending rank.** `search.list` exposes none of them.
    `order=viewCount` only ranks the result set; it does not reveal any number,
    and the ranking is not a "trend" measurement. Anything numeric said about
    these videos would be invented.

    (`list_recent_uploads` above is still the right call for a channel's OWN
    videos at 1 unit — this is 100, justified only because there is no cheaper
    way to discover content on channels we don't own.)

    Returns [] for an empty query or a missing key. Raises on quota/HTTP errors
    so the caller can log the reason and carry on unenriched.
    """
    query = (query or "").strip()
    if not query or not search_available():
        return []

    count = increment_call_counter("youtube_search", window_days=1)
    if count > DAILY_SEARCH_LIMIT:
        raise RuntimeError(
            f"YouTube daily search brake reached ({DAILY_SEARCH_LIMIT} searches = "
            f"{DAILY_SEARCH_LIMIT * SEARCH_UNITS} units) - refusing search to protect "
            f"the upload quota sharing the same 10,000/day pool")

    published_after = (datetime.now(timezone.utc)
                       - timedelta(days=published_within_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _get_with_key("search", {
        "part": "snippet", "type": "video", "q": query[:200],
        "maxResults": min(max_results, 25), "order": "viewCount",
        "publishedAfter": published_after,
        "regionCode": region_code, "relevanceLanguage": relevance_language,
    })

    videos = []
    for item in data.get("items", []):
        video_id = ((item.get("id") or {}).get("videoId") or "").strip()
        snippet = item.get("snippet") or {}
        if not video_id:
            continue
        # The canonical watch URL is BUILT from an id the API returned — that is
        # not a guessed link (contrast a model writing a plausible-looking id).
        # These URLs are handed to competitor_research as verified sources.
        videos.append({
            "video_id": video_id,
            "url": WATCH_URL.format(video_id),
            "title": snippet.get("title", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "channel_url": (CHANNEL_URL.format(snippet["channelId"])
                            if snippet.get("channelId") else ""),
            "published_at": snippet.get("publishedAt", ""),
        })
    return videos
