"""uallak's YouTube agent — publishing + connection, mirroring
agents/tiktok_content_agent.py's structure (own OAuth entirely separate from
Meta/Google Ads/TikTok). Same division of labor as every content-publishing
agent: media_agent/avatar_agent generate the actual video; this agent is
purely the pipe from already-generated media to the client's YouTube channel.
No content generation and no LLM calls here.

PRICING MODEL (business decision, 2026-07-23 handoff, CONFIRMED same day at
150 NIS/month) — DECOUPLED from generation cost, on purpose:
- The YouTube fee (PRICING["platform_management_fees"]["youtube"]) covers
  ONLY ongoing management: connection, uploads, engagement tracking. It is
  NOT a generation-cost bucket the way Higgsfield/avatar minutes are. Fully
  wired into the live sales-chat proposal flow (build_proposal) alongside
  meta/google/tiktok — see the youtube skill for the full pricing writeup.
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
# A client who authorized YouTube but has NO channel yet: the refresh token
# is parked here so run_channel_detection_scan can keep checking channels.list
# until their self-created channel appears — then it's swapped for a real
# 'youtube' row and this row is deleted. Same shape as meta_content_agent's
# meta_pending row for the no-Page flow.
YOUTUBE_PENDING_PLATFORM = "youtube_pending"

CHANNEL_GUIDE_DEDUP_DAYS = 3  # a client retrying "Connect" shouldn't get the guide spammed

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


def _get_pending_connection(client_id: int) -> dict:
    result = (
        _db().table("client_accounts")
        .select("*")
        .eq("client_id", client_id)
        .eq("platform", YOUTUBE_PENDING_PLATFORM)
        .eq("status", "active")
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


# ─── No-channel flow: guided self-creation + auto-detection ─────────────────
# Channel creation via API was deliberately NOT attempted — same reasoning as
# meta_content_agent's no-Page flow: there is no reliable "create a YouTube
# channel on the user's behalf" API for a standard app, while the native flow
# is a genuinely quick few clicks the client does once. Guide + auto-detect
# beats a fragile API path here, same as we can't provision a channel the way
# website_agent.provision_site provisions a new WordPress site.

_CHANNEL_GUIDE = {
    "he": ("כדי לפרסם בשבילך בטיוב, צריך ערוץ YouTube — וזה משהו שרק בעל החשבון יכול "
           "ליצור, לכן זה אצלך. זה לוקח דקה:\n"
           "1. היכנסו ל-youtube.com עם אותו חשבון גוגל שאישרתם איתו הרגע את החיבור\n"
           "2. לחצו על תמונת הפרופיל (למעלה מימין) ← \"צור ערוץ\" (או פתחו את "
           "studio.youtube.com, שמציע את זה אוטומטית אם אין לכם ערוץ)\n"
           "3. תנו לערוץ שם (בדרך כלל שם העסק) ואפשר להוסיף תמונת פרופיל\n"
           "4. זהו! אין צורך לחבר שוב — המערכת שלנו תזהה את הערוץ החדש אוטומטית "
           "ותעדכן אותך כאן 🎉"),
    "en": ("To publish for you on YouTube we need a channel — and only the account "
           "owner can create one, which is why this step is yours. It takes a minute:\n"
           "1. Go to youtube.com signed in with the same Google account you just used "
           "to connect\n"
           "2. Click your profile picture (top right) → \"Create a channel\" (or open "
           "studio.youtube.com, which offers this automatically if you have none)\n"
           "3. Give the channel a name (usually your business name) and optionally add "
           "a profile picture\n"
           "4. That's it! No need to reconnect — our system detects the new channel "
           "automatically and will update you here 🎉"),
    "fr": ("Pour publier pour vous sur YouTube, il faut une chaîne — et seul le "
           "propriétaire du compte peut la créer, c'est pourquoi cette étape vous "
           "revient. Cela prend une minute :\n"
           "1. Allez sur youtube.com connecté avec le même compte Google que vous "
           "venez d'utiliser pour la connexion\n"
           "2. Cliquez sur votre photo de profil (en haut à droite) → « Créer une "
           "chaîne » (ou ouvrez studio.youtube.com, qui le propose automatiquement "
           "si vous n'en avez pas)\n"
           "3. Donnez un nom à la chaîne (généralement le nom de votre entreprise) et "
           "ajoutez éventuellement une photo de profil\n"
           "4. C'est tout ! Pas besoin de reconnecter — notre système détecte la "
           "nouvelle chaîne automatiquement et vous informera ici 🎉"),
    "ar": ("لكي ننشر نيابةً عنك على يوتيوب نحتاج إلى قناة — وصاحب الحساب وحده يستطيع "
           "إنشاءها، ولهذا هذه الخطوة لك. تستغرق دقيقة:\n"
           "1. ادخلوا إلى youtube.com بنفس حساب غوغل الذي استخدمتموه للتو للربط\n"
           "2. اضغطوا على صورة الملف الشخصي (أعلى اليمين) ← \"إنشاء قناة\" (أو افتحوا "
           "studio.youtube.com الذي يقترح ذلك تلقائيًا إن لم يكن لديكم قناة)\n"
           "3. أعطوا القناة اسمًا (عادةً اسم عملكم) ويمكن إضافة صورة ملف شخصي\n"
           "4. هذا كل شيء! لا حاجة لإعادة الربط — نظامنا يكتشف القناة الجديدة تلقائيًا "
           "وسيحدثكم هنا 🎉"),
    "ru": ("Чтобы публиковать за вас на YouTube, нужен канал — создать его может "
           "только владелец аккаунта, поэтому этот шаг за вами. Это займёт минуту:\n"
           "1. Откройте youtube.com под тем же аккаунтом Google, которым вы только "
           "что подключились\n"
           "2. Нажмите на фото профиля (справа вверху) → «Создать канал» (или "
           "откройте studio.youtube.com — он предложит это автоматически, если "
           "канала ещё нет)\n"
           "3. Дайте каналу название (обычно название вашего бизнеса) и при желании "
           "добавьте фото профиля\n"
           "4. Готово! Повторно подключаться не нужно — наша система обнаружит новый "
           "канал автоматически и сообщит вам здесь 🎉"),
}


def send_channel_creation_guide(client_id: int) -> dict:
    """The guided "create your channel yourself" instructions, via dashboard
    chat — sent when the OAuth callback finds no channel. Static steps in
    the client's stored language preference (YouTube's native flow is
    identical for everyone; no LLM call for fixed content) — same pattern as
    meta_content_agent.send_page_creation_guide. Deduped so a client
    retrying "Connect" a few times isn't spammed."""
    from agents.client_agent import get_activity, get_client, log_communication, log_activity

    cutoff = (datetime.now(timezone.utc) - timedelta(days=CHANNEL_GUIDE_DEDUP_DAYS)).isoformat()
    for entry in get_activity(client_id, limit=50):
        if (entry.get("agent_name") == AGENT_NAME
                and entry.get("action_type") == "channel_guide_sent"
                and (entry.get("created_at") or "") >= cutoff):
            return {"success": True, "deduped": True}

    client = get_client(client_id)
    language = (client.get("language") or "he").lower()
    log_communication(client_id, "outbound", "dashboard_chat",
                      _CHANNEL_GUIDE.get(language, _CHANNEL_GUIDE["he"]))
    log_activity(client_id, AGENT_NAME, "channel_guide_sent", {"language": language}, {})
    log_step(AGENT_NAME, "send_channel_creation_guide", f"client {client_id} ({language})")
    return {"success": True, "deduped": False}


def _channel_guide_sent_client_ids() -> list:
    """Clients who were sent the creation guide — the ONLY population the
    detection scan watches, same deliberate scoping as Meta's equivalent (a
    long-standing client who never asked to connect YouTube never gets a
    channel silently auto-connected)."""
    rows = (_db().table("client_activity").select("client_id")
            .eq("agent_name", AGENT_NAME).eq("action_type", "channel_guide_sent")
            .limit(500).execute().data or [])
    return sorted({r["client_id"] for r in rows})


def run_channel_detection_scan() -> dict:
    """For every guided no-channel client who still has no youtube row: call
    channels.list(mine=true) with their parked refresh token; the moment
    their self-created channel appears, connect it exactly like the OAuth
    callback would have, clean up the parked token, and tell the client.
    No existing recurring YouTube scan to piggyback on (unlike Meta's inbox
    scan) — this needs its own scheduler job, see the youtube skill."""
    from agents.client_agent import log_activity, log_communication, remove_accounts, upsert_account

    summary = {"clients_scanned": 0, "channels_connected": 0, "failures": 0}
    for client_id in _channel_guide_sent_client_ids():
        if is_connected(client_id):
            continue  # already resolved (detected earlier, or a manual reconnect)
        pending = _get_pending_connection(client_id)
        refresh_token = pending.get("access_token", "")
        if not refresh_token:
            continue
        summary["clients_scanned"] += 1
        try:
            channel = yt.get_own_channel(refresh_token)
            if not channel.get("channel_id"):
                continue
            upsert_account(client_id, YOUTUBE_PLATFORM, channel["channel_id"], refresh_token, "active")
            remove_accounts(client_id, [YOUTUBE_PENDING_PLATFORM])
            log_activity(client_id, AGENT_NAME, "account_connected",
                         {"channel_id": channel["channel_id"], "title": channel.get("title", ""),
                          "via": "channel_detection_scan"}, {})
            log_communication(client_id, "outbound", "dashboard_chat",
                              f'זיהינו את ערוץ היוטיוב החדש שלך ("{channel.get("title", "")}") '
                              'וחיברנו אותו אוטומטית — הכל מוכן, אין צורך בשום פעולה נוספת 🎉')
            summary["channels_connected"] += 1
        except Exception as e:
            # Unverified-app OAuth grants (Google's "Testing" publishing status —
            # see the youtube skill's verification-timeline flag) expire refresh
            # tokens after just 7 days, unlike normal ~unlimited-lifetime tokens.
            # A client slower than that to create their channel needs a real
            # reconnect, not an endless silent retry.
            if "invalid_grant" in str(e).lower():
                remove_accounts(client_id, [YOUTUBE_PENDING_PLATFORM])
                agent_alert(AGENT_NAME, [
                    f"client {client_id}: parked YouTube consent token expired before their "
                    f"channel was created (7-day expiry while the app is in Google's "
                    f"unverified/testing mode) - they need to tap Connect again (guide was sent)"])
            else:
                summary["failures"] += 1
                log_step(AGENT_NAME, "channel_detection", f"client {client_id}: {e}")
    log_step(AGENT_NAME, "run_channel_detection_scan", f"done - {summary}")
    return summary


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
