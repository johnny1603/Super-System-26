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
# scope-agnostic OAuth calls — one implementation, not a second copy.
from core.google_ads_service import OAUTH_CONSENT_URL, _access_token, exchange_code  # noqa: F401

API_BASE = "https://www.googleapis.com/youtube/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3"
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")
REDIRECT_PATH = "/api/oauth/youtube/callback"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
TIMEOUT = 30
UPLOAD_TIMEOUT = 600  # a long-form video upload is genuinely slow
DAILY_UPLOAD_LIMIT = 90  # default project cap is 100 videos.insert/day - brake below it


def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


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


def upload_video(refresh_token: str, content: bytes, title: str,
                 description: str = "", privacy_status: str = "private",
                 mime_type: str = "video/mp4") -> dict:
    """Resumable upload in two steps (initiate with JSON metadata → PUT the
    bytes to the returned session URL). privacy_status='private' is the
    house default — a human flips it public in YouTube Studio (same
    final-tap principle as PAUSED campaigns / WP drafts / TikTok inbox)."""
    count = increment_call_counter("youtube", window_days=1)
    if count > DAILY_UPLOAD_LIMIT:
        raise RuntimeError(f"YouTube daily upload brake reached ({DAILY_UPLOAD_LIMIT}) - refusing upload")

    metadata = {
        "snippet": {"title": title[:100], "description": description[:4900]},
        "status": {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": False},
    }
    initiate = httpx.post(
        f"{UPLOAD_BASE}/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={"Authorization": f"Bearer {_access_token(refresh_token)}",
                 "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": mime_type,
                 "X-Upload-Content-Length": str(len(content))},
        json=metadata, timeout=TIMEOUT)
    if initiate.status_code != 200:
        raise RuntimeError(f"upload initiate failed: {initiate.status_code} {initiate.text[:300]}")
    session_url = initiate.headers.get("location") or initiate.headers.get("Location")
    if not session_url:
        raise RuntimeError("upload initiate returned no session Location header")

    upload = httpx.put(session_url, content=content,
                       headers={"Content-Type": mime_type,
                                "Content-Length": str(len(content))},
                       timeout=UPLOAD_TIMEOUT)
    if upload.status_code not in (200, 201):
        raise RuntimeError(f"video upload failed: {upload.status_code} {upload.text[:300]}")
    return upload.json()


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
