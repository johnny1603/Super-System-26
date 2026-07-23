"""uallak's YouTube agent — publishing + connection, mirroring
agents/tiktok_content_agent.py's structure (own OAuth entirely separate from
Meta/Google Ads/TikTok). Same division of labor as every content-publishing
agent: media_agent/avatar_agent generate the actual video; this agent is
purely the pipe from already-generated media to the client's YouTube channel.
No content generation and no LLM calls here.

PRICING MODEL (business decision, 2026-07-23 handoff) — DECOUPLED from
generation cost, on purpose:
- The YouTube fee (PRICING["youtube"]) covers ONLY ongoing management:
  connection, uploads, engagement tracking. It is NOT a generation-cost
  bucket the way Higgsfield/avatar minutes are.
- Media generation for YouTube content (podcasts, repurposed shorts/reels,
  YouTube-specific videos) stays entirely inside the client's EXISTING
  media/avatar tier system. A client wanting longer/more complex YouTube
  content upgrades their existing video/avatar tier (billed via their own
  Higgsfield/HeyGen subscription) — never a second, YouTube-specific
  generation charge. This agent has no generation path to charge for at all.
- Justification for a small fee despite the Data API itself costing us
  nothing (see core/youtube_service.py's docstring: quota-based, no
  monetary billing, our volume is nowhere near the free daily cap): the fee
  reflects the connection/upload/engagement OPERATIONAL work, same
  principle as every other platform management fee in PRICING.

Publishing philosophy: uploads land PRIVATE by default (privacy_status=
'private') — a human flips it public/unlisted in YouTube Studio. Same
final-tap principle as PAUSED ad campaigns, WordPress drafts, and TikTok's
Upload-to-Inbox — never auto-published anywhere.

Engagement: real view/like/comment counts via the uploads playlist + videos
list (2 quota units total per check, not search.list's 100) — no comment
CONTENT reading in v1 (matches the TikTok agent's own honest limit; adding
it would need the commentThreads endpoint, a bigger scope ask, deferred).
"""
import os
from datetime import datetime, timedelta, timezone

from supabase import create_client as _supabase_client

from core import youtube_service as yt
from core.agent_base import agent_alert, log_step, timed_step

AGENT_NAME = "youtube_content_agent"
YOUTUBE_PLATFORM = "youtube"  # account_id=channel_id, access_token=refresh token

ENGAGEMENT_WINDOW_DAYS = 7
RECENT_UPLOADS_LIMIT = 20

_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        _db_instance = _supabase_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _db_instance


def _get_connection(client_id: int) -> dict:
    result = (
        _db().table("client_accounts")
        .select("*")
        .eq("client_id", client_id)
        .eq("platform", YOUTUBE_PLATFORM)
        .eq("status", "active")
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def is_connected(client_id: int) -> bool:
    conn = _get_connection(client_id)
    return bool(conn.get("access_token") and conn.get("account_id"))


# ─── Publishing ─────────────────────────────────────────────────────────────

def publish(client_id: int, spec: dict) -> dict:
    """Uploads an already-generated video (Drive file_id, same pull-point
    shape as tiktok_content_agent.publish — TikTok's PULL_FROM_URL reasoning
    doesn't apply here: YouTube's resumable upload takes raw bytes directly,
    no domain-verification requirement at all) to the client's channel as
    PRIVATE. spec: {drive_file_id: str, title: str, description?: str}."""
    from agents.client_agent import log_activity
    from core import drive_service as drive

    drive_file_id = (spec.get("drive_file_id") or "").strip()
    title = (spec.get("title") or "").strip()
    if not drive_file_id:
        return {"success": False, "errors": ["drive_file_id is required"]}
    if not title:
        return {"success": False, "errors": ["title is required"]}

    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"success": False, "errors": ["no connected YouTube account"]}

    log_step(AGENT_NAME, "publish", f"client_id={client_id} file={drive_file_id}")
    try:
        video_bytes = timed_step(AGENT_NAME, "download_from_drive",
                                 lambda: drive.download_file(drive_file_id))
        result = timed_step(
            AGENT_NAME, "youtube_upload",
            lambda: yt.upload_video(conn["access_token"], video_bytes, title,
                                    spec.get("description", "")))
    except Exception as e:
        agent_alert(AGENT_NAME, [f"YouTube publish failed for client {client_id} "
                                 f"(file {drive_file_id}): {e}"])
        return {"success": False, "errors": [str(e)]}

    video_id = result.get("id", "")
    log_activity(client_id, AGENT_NAME, "content_published",
                {"drive_file_id": drive_file_id, "title": title},
                {"video_id": video_id, "privacy_status": "private"})
    return {"success": True, "video_id": video_id,
            "note": "uploaded as PRIVATE - review and publish it in YouTube Studio"}


# ─── Engagement ─────────────────────────────────────────────────────────────

def get_engagement_summary(client_id: int, window_days: int = ENGAGEMENT_WINDOW_DAYS) -> dict:
    """Real view/like/comment totals for videos published in the window.
    Comment CONTENT is NOT read (commentThreads is a bigger scope ask than
    youtube.readonly covers cleanly) - aggregate counts only, same honest
    limit as tiktok_content_agent's own engagement summary."""
    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"connected": False}

    log_step(AGENT_NAME, "get_engagement_summary", f"client_id={client_id}")
    try:
        channel = yt.get_own_channel(conn["access_token"])
        uploads_playlist = channel.get("uploads_playlist_id", "")
        if not uploads_playlist:
            return {"connected": True, "error": "could not resolve the channel's uploads playlist"}
        video_ids = yt.list_recent_uploads(conn["access_token"], uploads_playlist,
                                           max_results=RECENT_UPLOADS_LIMIT)
        videos = yt.get_video_stats(conn["access_token"], video_ids)
    except Exception as e:
        agent_alert(AGENT_NAME, [f"YouTube engagement fetch failed for client {client_id}: {e}"])
        return {"connected": True, "error": str(e)}

    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    in_window = [v for v in videos if (v.get("published_at") or "") >= since]
    return {
        "connected": True,
        "period_days": window_days,
        "videos": len(in_window),
        "views": sum(v["views"] for v in in_window),
        "likes": sum(v["likes"] for v in in_window),
        "comments": sum(v["comments"] for v in in_window),
    }
