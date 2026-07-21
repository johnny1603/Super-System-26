"""uallak's TikTok agent — publishing + connection, mirroring
agents/meta_content_agent.py's structure. TikTok needs its own OAuth/API
entirely separate from Meta's, so this is a standalone agent, but the
DIVISION OF LABOR is identical: media_agent (Higgsfield) generates the
actual content; this agent is purely the pipe from already-generated media
to the client's TikTok account. No content generation and no LLM calls here.

Real platform constraints that shape this file (see .claude/skills/tiktok/
SKILL.md for the full picture) — TikTok's Content Posting API forces every
post from an app that hasn't passed its content-audit to SELF_ONLY (private,
visible only to the account owner) if published via Direct Post. This agent
deliberately uses 'Upload to Inbox' instead: the video lands in the CLIENT's
own TikTok inbox and THEY tap publish inside the app. Two reasons, not one:
- It has no SELF_ONLY/audit restriction at all - a real client's video
  posted this way is genuinely public once they publish it.
- It matches house policy already established everywhere else (PAUSED ad
  campaigns, WordPress drafts, Drive-review-first media): a human always
  makes the final publish tap. Direct Post exists in core/tiktok_service.py
  for a future business decision, but ISN'T used here on purpose.

Engagement is real but narrower than Meta's: TikTok's public API has no
comment-CONTENT reading (that lives behind the separately-gated Research
API, not realistically obtainable for a commercial tool) - only aggregate
counts (likes/comments/shares/views) via video.list. There is also no
comment-inbox / reply_to_comment here, unlike meta_content_agent - that's a
genuine platform gap, not an oversight.
"""
import os
import time
from datetime import datetime, timedelta, timezone

from supabase import create_client as _supabase_client

from core import tiktok_service as tiktok
from core.agent_base import agent_alert, log_step, timed_step

AGENT_NAME = "tiktok_content_agent"
TIKTOK_PLATFORM = "tiktok"  # account_id=open_id, access_token='access::refresh' (see tiktok_service)

# Inbox delivery finishes fast (no moderation wait like Direct Post) but the
# video still has to upload/download server-side first.
STATUS_POLL_SECONDS = 5
STATUS_POLL_MAX_TRIES = 24  # ~2 minutes

ENGAGEMENT_WINDOW_DAYS = 7
RECENT_VIDEOS_LIMIT = 20

# Created lazily — no DB client at import time (api_server imports every agent at startup)
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
        .eq("platform", TIKTOK_PLATFORM)
        .eq("status", "active")
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def is_connected(client_id: int) -> bool:
    conn = _get_connection(client_id)
    return bool(conn.get("access_token") and conn.get("account_id"))


def _live_access_token(client_id: int, conn: dict) -> str:
    """The stored access token always expires in 24h - unlike Meta's Page
    tokens, refreshing on every real use (not just failure recovery) is the
    normal path. Re-stores both rotated tokens (TikTok rotates the refresh
    token too) before returning the fresh access token."""
    from agents.client_agent import upsert_account
    access_token, refresh_token = tiktok.split_tokens(conn["access_token"])
    refreshed = tiktok.refresh_access_token(refresh_token)
    new_access = refreshed.get("access_token", access_token)
    new_refresh = refreshed.get("refresh_token", refresh_token)
    upsert_account(client_id, TIKTOK_PLATFORM, conn["account_id"],
                   tiktok.join_tokens(new_access, new_refresh), "active")
    return new_access


# ─── Publishing ─────────────────────────────────────────────────────────────

def _wait_for_inbox_delivery(access_token: str, publish_id: str) -> dict:
    for _ in range(STATUS_POLL_MAX_TRIES):
        time.sleep(STATUS_POLL_SECONDS)
        status = tiktok.get_post_status(access_token, publish_id)
        state = status.get("status", "")
        if state in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            return status
        if state == "FAILED":
            raise RuntimeError(f"TikTok post {publish_id} failed: {status.get('fail_reason', '')}")
        # PROCESSING_UPLOAD / PROCESSING_DOWNLOAD - keep waiting
    raise RuntimeError(f"TikTok post {publish_id} still processing after "
                       f"{STATUS_POLL_MAX_TRIES * STATUS_POLL_SECONDS}s")


def publish(client_id: int, spec: dict) -> dict:
    """Sends an already-generated video to the client's TikTok INBOX for them
    to caption and publish themselves — never Direct Post (see the module
    docstring). spec: {drive_file_id: str, caption?: str}.

    Deliberately takes a Drive file id, NOT a public media_url like
    meta_content_agent.publish() — TikTok's PULL_FROM_URL source requires the
    URL's domain to be pre-verified as OURS in the TikTok developer
    dashboard, which a drive.google.com link could never satisfy. Instead
    this downloads the video privately (core.drive_service, our own
    service-account credentials — the file is never made public) and
    uploads the bytes directly to TikTok (FILE_UPLOAD), which needs no
    domain verification at all.

    `caption` is NOT applied to the inbox video (Upload-to-Inbox has no
    caption/title field — the client writes their own when they finish
    publishing in the app); if given, it's sent to the client as a
    ready-to-paste suggestion via the dashboard chat instead, so the
    generated caption isn't just lost."""
    from agents.client_agent import log_activity, get_client, log_communication
    from core import drive_service as drive

    drive_file_id = (spec.get("drive_file_id") or "").strip()
    if not drive_file_id:
        return {"success": False, "errors": ["drive_file_id is required"]}

    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"success": False, "errors": ["no connected TikTok account"]}

    log_step(AGENT_NAME, "publish", f"client_id={client_id} file={drive_file_id}")
    try:
        access_token = timed_step(AGENT_NAME, "refresh_token",
                                  lambda: _live_access_token(client_id, conn))
        video_bytes = timed_step(AGENT_NAME, "download_from_drive",
                                 lambda: drive.download_file(drive_file_id))
        size = len(video_bytes)
        init = timed_step(
            AGENT_NAME, "init_inbox_upload",
            lambda: tiktok.init_inbox_upload(access_token, video_size=size,
                                             chunk_size=size, total_chunk_count=1))
        publish_id = init["publish_id"]
        timed_step(AGENT_NAME, "upload_video",
                  lambda: tiktok.upload_video_chunk(init["upload_url"], video_bytes,
                                                    total_size=size))
        final = timed_step(AGENT_NAME, "await_delivery",
                           lambda: _wait_for_inbox_delivery(access_token, publish_id))
    except Exception as e:
        agent_alert(AGENT_NAME, [f"TikTok publish failed for client {client_id} "
                                 f"(file {drive_file_id}): {e}"])
        return {"success": False, "errors": [str(e)]}

    log_activity(client_id, AGENT_NAME, "content_published",
                {"drive_file_id": drive_file_id, "caption": spec.get("caption", "")},
                {"publish_id": publish_id, "status": final.get("status")})

    if (spec.get("caption") or "").strip():
        client = get_client(client_id)
        if client.get("email"):
            log_communication(client_id, "outbound", "dashboard_chat",
                              'סרטון חדש חיכה לך בתיבת הנכנסים של טיקטוק 🎬 '
                              'פתחו את האפליקציה כדי לסקור ולפרסם, ואפשר להדביק את הכיתוב הזה:\n'
                              f'{spec["caption"].strip()}')

    return {"success": True, "publish_id": publish_id, "status": final.get("status"),
            "note": "sent to the client's TikTok inbox - they review and publish it themselves"}


# ─── Engagement (counts only — see the module docstring for why) ──────────

def get_engagement_summary(client_id: int, window_days: int = ENGAGEMENT_WINDOW_DAYS) -> dict:
    """Aggregate like/comment/share/view counts on the account's public
    videos published in the window. No comment CONTENT, no inbox — TikTok's
    public API doesn't expose either (see the module docstring)."""
    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"connected": False}

    log_step(AGENT_NAME, "get_engagement_summary", f"client_id={client_id}")
    try:
        access_token = _live_access_token(client_id, conn)
        result = tiktok.list_videos(access_token, max_count=RECENT_VIDEOS_LIMIT)
    except Exception as e:
        agent_alert(AGENT_NAME, [f"TikTok engagement fetch failed for client {client_id}: {e}"])
        return {"connected": True, "error": str(e)}

    since = int((datetime.now(timezone.utc) - timedelta(days=window_days)).timestamp())
    videos = [v for v in (result.get("videos") or []) if (v.get("create_time") or 0) >= since]
    return {
        "connected": True,
        "period_days": window_days,
        "videos": len(videos),
        "likes": sum(int(v.get("like_count", 0) or 0) for v in videos),
        "comments": sum(int(v.get("comment_count", 0) or 0) for v in videos),
        "shares": sum(int(v.get("share_count", 0) or 0) for v in videos),
        "views": sum(int(v.get("view_count", 0) or 0) for v in videos),
    }
