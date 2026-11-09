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
counts (likes/comments/shares/views) via video.list — and even that is
switched off today (tiktok_service.ENGAGEMENT_READS_ENABLED; the scope is
not requested until a Display API review). There is also no
comment-inbox / reply_to_comment here, unlike meta_content_agent - that's a
genuine platform gap, not an oversight.
"""
import os
import time
from datetime import datetime, timedelta, timezone

from supabase import create_client as _supabase_client

from core import tiktok_service as tiktok
from core.agent_base import agent_alert, log_step, timed_step
from core.secrets_vault import hydrate
from core import drive_storage
from core import video_probe

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
    # Decrypted right here, for use (core/secrets_vault.get_client_secret)
    return hydrate(result.data[0]) if result.data else {}


def is_connected(client_id: int) -> bool:
    conn = _get_connection(client_id)
    return bool(conn.get("access_token") and conn.get("account_id"))


def _live_access_token(client_id: int, conn: dict) -> str:
    """The stored access token always expires in 24h - unlike Meta's Page
    tokens, refreshing on every real use (not just failure recovery) is the
    normal path. Re-stores both rotated tokens (TikTok rotates the refresh
    token too) before returning the fresh access token."""
    from agents.client_agent import upsert_account
    from core import tiktok_grant
    access_token, refresh_token = tiktok.split_tokens(conn["access_token"])
    try:
        refreshed = tiktok.refresh_access_token(refresh_token)
    except Exception as e:
        if tiktok.is_dead_grant(e):
            tiktok_grant.record_dead(client_id, e.code)
        raise
    new_access = refreshed.get("access_token", access_token)
    new_refresh = refreshed.get("refresh_token", refresh_token)
    upsert_account(client_id, TIKTOK_PLATFORM, conn["account_id"],
                   tiktok.join_tokens(new_access, new_refresh), "active")
    # The refresh answer says what this grant covers — how an OLD grant
    # (video.publish, no video.upload) is learned without a failed upload
    tiktok_grant.record_scopes(client_id, refreshed.get("scope", ""))
    return new_access


def check_grant(client_id: int) -> str:
    """"ok" | "missing_upload" | "dead" | "not_connected" | "unknown" — for
    the send_tiktok_to_inbox proposal, before a card is shown. A grant with
    no record yet costs ONE token refresh (which also records its scopes);
    a known record costs nothing. "unknown" = the refresh failed for some
    other reason (network, TikTok down): the click will tell."""
    from core import tiktok_grant
    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return "not_connected"
    known = tiktok_grant.state(client_id)
    if known != "unknown":
        return known
    try:
        _live_access_token(client_id, conn)
    except Exception as e:
        log_step(AGENT_NAME, "check_grant", f"client {client_id}: refresh failed ({type(e).__name__})")
    return tiktok_grant.state(client_id)


# ─── The connected account, shown on the dashboard (user.info.basic) ───────

PROFILE_CACHE_SECONDS = 600
_profile_cache = {}   # client_id -> (time, account_id, profile)


def _clean_profile(user: dict) -> dict:
    name = str((user or {}).get("display_name") or "").strip()[:100]
    avatar = str((user or {}).get("avatar_url") or "").strip()
    return {"display_name": name,
            "avatar_url": avatar if avatar.startswith("https://") and len(avatar) <= 2000 else ""}


def get_profile(client_id: int) -> dict:
    """The TikTok card's "connected as": {connected, display_name,
    avatar_url, needs_reconnect, unavailable}. This is what user.info.basic
    is FOR in the product (docs/TIKTOK-REVIEW.md): /v2/user/info/ read live
    with the client's own token — the stored access token first (it lives
    24 h), one refresh only when TikTok says it expired. Display data only:
    nothing is stored; a TikTok avatar URL is signed and short-lived, so it
    is never persisted either. Cached PROFILE_CACHE_SECONDS per account."""
    from core import tiktok_grant
    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"connected": False}
    hit = _profile_cache.get(client_id)
    if hit and hit[1] == conn["account_id"] and hit[0] > time.time() - PROFILE_CACHE_SECONDS:
        return hit[2]
    if tiktok_grant.state(client_id) == "dead":
        return {"connected": True, "needs_reconnect": True, "display_name": "", "avatar_url": ""}
    access_token, _ = tiktok.split_tokens(conn["access_token"])
    try:
        try:
            user = tiktok.get_user_info(access_token)
        except Exception as e:
            if not tiktok.is_token_error(e):
                raise
            user = tiktok.get_user_info(_live_access_token(client_id, conn))
    except Exception as e:
        log_step(AGENT_NAME, "get_profile", f"client {client_id}: user/info failed "
                 f"({type(e).__name__}, code={getattr(e, 'code', None) or '-'}, "
                 f"log_id={getattr(e, 'log_id', None) or '-'})")
        if tiktok.is_dead_grant(e) or tiktok.is_scope_error(e):
            return {"connected": True, "needs_reconnect": True, "display_name": "", "avatar_url": ""}
        return {"connected": True, "unavailable": True, "display_name": "", "avatar_url": ""}
    profile = {"connected": True, **_clean_profile(user),
               "needs_reconnect": tiktok_grant.needs_reconnect(client_id)}
    _profile_cache[client_id] = (time.time(), conn["account_id"], profile)
    return profile


def forget_profile(client_id: int):
    _profile_cache.pop(client_id, None)


# ─── Publishing ─────────────────────────────────────────────────────────────

def poll_tries(size_bytes: int = 0) -> int:
    """~2 minutes, plus 5 s per 8 MB for a big file TikTok must process,
    capped so the whole send stays inside the card's 10-minute deadline."""
    return min(STATUS_POLL_MAX_TRIES + int((size_bytes or 0) // (8 * 1024 * 1024)), 72)


def _wait_for_inbox_delivery(access_token: str, publish_id: str, size_bytes: int = 0) -> dict:
    tries = poll_tries(size_bytes)
    for _ in range(tries):
        time.sleep(STATUS_POLL_SECONDS)
        status = tiktok.get_post_status(access_token, publish_id)
        state = status.get("status", "")
        if state in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            return status
        if state == "FAILED":
            raise RuntimeError(f"TikTok post {publish_id} failed: {status.get('fail_reason', '')}")
        # PROCESSING_UPLOAD / PROCESSING_DOWNLOAD - keep waiting
    raise RuntimeError(f"TikTok post {publish_id} still processing after "
                       f"{tries * STATUS_POLL_SECONDS}s")


@drive_storage.scoped
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
    from core import tiktok_grant
    from core.video_limits import TIKTOK_INBOX_MAX_MB, fits_tiktok, tiktok_chunk_plan, tiktok_chunk_size

    drive_file_id = (spec.get("drive_file_id") or "").strip()
    if not drive_file_id:
        return {"success": False, "errors": ["drive_file_id is required"]}

    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"success": False, "needs_reconnect": True, "errors": ["no connected TikTok account"]}
    if tiktok_grant.needs_reconnect(client_id):
        # Known bad grant: don't call TikTok with it (the record already alerted)
        return {"success": False, "needs_reconnect": True,
                "errors": [f"TikTok connection must be renewed ({tiktok_grant.state(client_id)})"]}

    log_step(AGENT_NAME, "publish", f"client_id={client_id} file={drive_file_id}")
    try:
        access_token = timed_step(AGENT_NAME, "refresh_token",
                                  lambda: _live_access_token(client_id, conn))
        if tiktok_grant.needs_reconnect(client_id):
            # The refresh just learned this grant has no video.upload
            return {"success": False, "needs_reconnect": True,
                    "errors": [f"TikTok connection must be renewed ({tiktok.UPLOAD_SCOPE} not granted)"]}
        # STREAMED (2026-11-09): the size from Drive's metadata, then the file
        # range by range — never whole in memory (Cloud Run has 512 Mi)
        size = int((drive.get_meta(drive_file_id, "id,size") or {}).get("size") or 0)
        if not fits_tiktok(size):
            # The root guard, whatever path called this
            return {"success": False, "too_large": True,
                    "errors": [f"video is {round(size / 1048576, 1)} MB; the TikTok inbox upload "
                               f"takes up to {TIKTOK_INBOX_MAX_MB} MB"]}

        def read(start, length):
            return drive.read_range(drive_file_id, start, length)

        # What the file IS, from its own headers (core/video_probe, range
        # reads): a container or codec TikTok can't show is refused here —
        # before init, so no broken draft lands in the client's inbox — and
        # the PUT names the real container instead of always video/mp4
        facts = video_probe.probe_ranges(read, size)
        log_step(AGENT_NAME, "probe", f"client_id={client_id} file={drive_file_id} "
                 f"{_facts_line(facts)} bytes={size}")
        bad = video_probe.problems(facts)
        if bad:
            return {"success": False, "bad_format": True, "problems": bad, "video": _facts_brief(facts),
                    "errors": [f"TikTok can't take this file ({', '.join(bad)}): {_facts_line(facts)}"]}
        plan = tiktok_chunk_plan(size)
        init = timed_step(
            AGENT_NAME, "init_inbox_upload",
            lambda: tiktok.init_inbox_upload(access_token, video_size=size,
                                             chunk_size=tiktok_chunk_size(size), total_chunk_count=len(plan)))
        publish_id = init["publish_id"]
        timed_step(AGENT_NAME, "upload_video",
                   lambda: tiktok.upload_video_stream(init["upload_url"], read, size,
                                                      mime_type=video_probe.content_type(facts["container"])))
        final = timed_step(AGENT_NAME, "await_delivery",
                           lambda: _wait_for_inbox_delivery(access_token, publish_id, size))
    except Exception as e:
        if tiktok.is_scope_error(e):
            # A grant from before 2026-10-29 (video.publish, no video.upload):
            # a fact with one fix — reconnect, which asks for OAUTH_SCOPES.
            # The record alerts once, on the transition.
            tiktok_grant.record_missing_upload(client_id)
            return {"success": False, "needs_reconnect": True,
                    "errors": [f"TikTok connection must be renewed ({tiktok.UPLOAD_SCOPE} not granted)"]}
        if tiktok.is_dead_grant(e):
            return {"success": False, "needs_reconnect": True,
                    "errors": [f"TikTok connection must be renewed ({e.code})"]}
        if tiktok.is_pending_limit(e):
            # Too many unpublished drafts already waiting in their TikTok inbox
            return {"success": False, "pending_limit": True, "errors": [str(e)]}
        agent_alert(AGENT_NAME, [f"TikTok publish failed for client {client_id} "
                                 f"(file {drive_file_id}): {e}"])
        return {"success": False, "errors": [str(e)]}

    log_activity(client_id, AGENT_NAME, "content_published",
                {"drive_file_id": drive_file_id, "caption": spec.get("caption", ""),
                 "size_bytes": size, "video": _facts_brief(facts)},
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


def _facts_brief(facts: dict) -> dict:
    keys = ("container", "video_codec", "audio_codec", "width", "height", "fps", "duration_s")
    return {k: (facts or {}).get(k) for k in keys}


def _facts_line(facts: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in _facts_brief(facts).items())


# ─── Diagnosis (admin, read-only) ───────────────────────────────────────────

DIAGNOSE_MAX = 5
STATUS_FIELDS = ("status", "fail_reason", "uploaded_bytes", "downloaded_bytes")


def _when(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _status_reader(client_id: int):
    """get_post_status with the client's own grant: the stored access token
    first (it lives 24 h), ONE refresh only if TikTok says it expired —
    exactly get_profile's rule. The token never leaves this function."""
    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return None
    token = {"value": tiktok.split_tokens(conn["access_token"])[0], "refreshed": False}

    def read(publish_id: str) -> dict:
        try:
            try:
                status = tiktok.get_post_status(token["value"], publish_id)
            except Exception as e:
                if token["refreshed"] or not tiktok.is_token_error(e):
                    raise
                token["value"], token["refreshed"] = _live_access_token(client_id, conn), True
                status = tiktok.get_post_status(token["value"], publish_id)
            return {k: status.get(k) for k in STATUS_FIELDS if k in status}
        except Exception as e:
            return {"error": type(e).__name__, "code": getattr(e, "code", None) or "",
                    "log_id": getattr(e, "log_id", None) or ""}
    return read


@drive_storage.scoped
def diagnose(client_id: int, limit: int = 2, probe_files: bool = True) -> dict:
    """The last `limit` send_tiktok_to_inbox attempts, read-only: which Drive
    file each card held (id, name, size, MIME, Drive's video metadata, md5,
    modified time), what the file IS (core/video_probe, from its bytes, when
    probe_files), the publish_id our activity log recorded for it, and
    TikTok's LIVE status for that publish_id (status, fail_reason, bytes).
    No token, no secret and no file content is returned. Nothing is sent,
    changed or stored (a token refresh, when TikTok says the stored token
    expired, re-stores the rotated pair as every TikTok call does)."""
    from core import drive_service as drive
    limit = max(1, min(int(limit or 2), DIAGNOSE_MAX))
    rows = (_db().table("agent_actions")
            .select("id,status,outcome,params,persona,created_at,decided_at,finished_at")
            .eq("client_id", int(client_id)).eq("action", "send_tiktok_to_inbox")
            .order("created_at", desc=True).limit(limit).execute().data or [])
    sent = (_db().table("client_activity").select("created_at,details,result")
            .eq("client_id", int(client_id)).eq("agent_name", AGENT_NAME)
            .eq("action_type", "content_published")
            .order("created_at", desc=True).limit(50).execute().data or [])
    reader = _status_reader(client_id)
    files = {}
    attempts = []
    for row in rows:
        file_id = str((row.get("params") or {}).get("drive_file_id") or "")
        started = _when(row.get("decided_at") or row.get("created_at"))
        # The activity row this run wrote: same file, at/after the click
        match = None
        for act in sorted(sent, key=lambda a: str(a.get("created_at") or "")):
            at = _when(act.get("created_at"))
            if (act.get("details") or {}).get("drive_file_id") == file_id and (not started or not at or at >= started):
                match = act
                break
        publish_id = str(((match or {}).get("result") or {}).get("publish_id") or "")
        if file_id and file_id not in files:
            info = {}
            try:
                meta = drive.get_meta(file_id, "id,name,mimeType,size,md5Checksum,modifiedTime,"
                                               "videoMediaMetadata,trashed")
                info = {k: meta.get(k) for k in ("name", "mimeType", "size", "md5Checksum",
                                                  "modifiedTime", "videoMediaMetadata", "trashed")}
                info["drive_problems"] = video_probe.drive_meta_problems(meta)
                if probe_files and int(meta.get("size") or 0) > 0:
                    # Range reads (headers only), never the whole file
                    facts = video_probe.probe_ranges(
                        lambda start, length, fid=file_id: drive.read_range(fid, start, length),
                        int(meta.get("size") or 0))
                    info["video"] = _facts_brief(facts)
                    info["video_notes"] = facts.get("notes") or []
                    info["tiktok_problems"] = video_probe.problems(facts)
            except Exception as e:
                info["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            files[file_id] = info
        attempts.append({
            "proposal_id": row.get("id"), "status": row.get("status"), "outcome": row.get("outcome"),
            "persona": row.get("persona"), "created_at": row.get("created_at"),
            "decided_at": row.get("decided_at"), "finished_at": row.get("finished_at"),
            "drive_file_id": file_id, "file": files.get(file_id, {}),
            "publish_id": publish_id,
            "recorded_video": ((match or {}).get("details") or {}).get("video"),
            "tiktok_status": (reader(publish_id) if (reader and publish_id) else
                              {"error": "no publish_id recorded for this attempt" if reader
                               else "TikTok not connected"}),
        })
    same_file = len(attempts) >= 2 and attempts[0]["drive_file_id"] == attempts[1]["drive_file_id"]
    return {"client_id": int(client_id), "attempts": attempts,
            "last_two_same_file": same_file}


# ─── Engagement (counts only — see the module docstring for why) ──────────

def get_engagement_summary(client_id: int, window_days: int = ENGAGEMENT_WINDOW_DAYS) -> dict:
    """Aggregate like/comment/share/view counts on the account's public
    videos published in the window. No comment CONTENT, no inbox — TikTok's
    public API doesn't expose either (see the module docstring).

    While tiktok_service.ENGAGEMENT_READS_ENABLED is off (video.list not
    requested), a connected account answers engagement_available=False with
    NO TikTok call at all — not even the token refresh — and no alert: it is
    a known state, not a failure."""
    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"connected": False}
    from core import tiktok_grant
    if tiktok_grant.needs_reconnect(client_id):
        # A recorded fact (no TikTok call): the Performance card says reconnect
        return {"connected": True, "needs_reconnect": True, "grant_state": tiktok_grant.state(client_id)}
    if not tiktok.ENGAGEMENT_READS_ENABLED:
        return {"connected": True, "engagement_available": False}

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
