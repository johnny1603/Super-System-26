"""uallak's avatar agent — real-person digital twins (HeyGen) and voice
clones (ElevenLabs). A DISTINCT PAID ADD-ON TIER, deliberately separate from
media_agent/Higgsfield (which makes regular social content with INVENTED
imagery): this agent handles a real human's face and voice. Never merge the
two, never reuse the Higgsfield connection, never blur their content into the
same pricing/usage bucket.

Pricing — single source of truth is PRICING["avatar"] in onboarding_agent
(setup 150₪ first / 100₪ additional avatar; monthly tiers billed by MINUTES:
10min/450₪, 20min/800₪, 40min/1550₪, custom above). Video counts are
client-facing ESTIMATES only. The client separately pays HeyGen/ElevenLabs
directly on their own accounts (their card, never ours). Sales/support-chat
integration of this tier is a SEPARATE follow-up handoff — this agent only
enforces and tracks.

CONSENT IS MANDATORY AND RECORDED — not reinterpretable. No avatar creation,
voice cloning, or avatar-video generation runs without an explicit, logged
consent row (avatar_consent_recorded activity: scope, statement version,
timestamp) for the relevant scope ('likeness' / 'voice'). The dashboard card
collects it with an explicit checkbox; the server re-checks on every
creation path. HeyGen additionally requires its own recorded consent-
statement VIDEO for digital twins — our source-kit instructions cover it.

HeyGen API reality (verified 2026-07-19): photo-avatar creation/training is
standard-plan API; VIDEO digital-twin creation via API is Enterprise-only —
on a normal client account the twin is created in HeyGen's web UI (our
source kit walks the client through it) and this agent detects readiness by
scanning their avatar list, then generates videos with it (standard API).

Storage: everything lives under the client's existing media Drive root
(media_agent's folders — reused, not rebuilt): `avatar-source/` for their
uploaded footage/photos/audio (shared to the client as WRITER so they can
upload), finished videos in `videos/avatar/`.
"""
import json
import os
from datetime import datetime, timezone

import httpx
from supabase import create_client as _supabase_client

from core import drive_service as drive
from core import elevenlabs_service as el
from core import heygen_service as hg
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call

AGENT_NAME = "avatar_agent"
HEYGEN_PLATFORM = "heygen"
ELEVENLABS_PLATFORM = "elevenlabs"

CONSENT_SCOPES = ("likeness", "voice")
# Bump when the statement wording changes — every consent row records the
# version it was given under
CONSENT_VERSION = "2026-07-19.v1"
CONSENT_STATEMENTS_HE = {
    "likeness": ("אני מאשר/ת במפורש ל-uallak להשתמש בצילומים ובתמונות שאעלה כדי ליצור "
                 "אווטאר דיגיטלי של דמותי בחשבון ה-HeyGen שלי, ולהפיק באמצעותו סרטונים "
                 "עבור העסק שלי. אוכל לבקש את הפסקת השימוש בכל עת."),
    "voice": ("אני מאשר/ת במפורש ל-uallak להשתמש בהקלטות הקול שאעלה כדי ליצור שיבוט קול "
              "דיגיטלי בחשבון ה-ElevenLabs שלי, ולהשתמש בו בסרטונים עבור העסק שלי. "
              "אוכל לבקש את הפסקת השימוש בכל עת."),
}

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


def _log_activity(client_id: int, action_type: str, details: dict, result: dict = None):
    _db().table("client_activity").insert({
        "client_id": client_id, "agent_name": AGENT_NAME,
        "action_type": action_type, "details": details, "result": result or {},
    }).execute()


def _rows(client_id: int, action_type: str, limit: int = 20) -> list:
    return (_db().table("client_activity").select("*")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", action_type)
            .order("created_at", desc=True).limit(limit).execute().data or [])


def _key(client_id: int, platform: str) -> str:
    rows = (_db().table("client_accounts").select("access_token")
            .eq("client_id", client_id).eq("platform", platform)
            .eq("status", "active").order("id", desc=True).limit(1).execute().data)
    return (rows[0].get("access_token") or "") if rows else ""


# ─── Connections (client self-service, dashboard card) ────────────────────────

def connect_accounts(client_id: int, heygen_key: str, elevenlabs_key: str = "") -> dict:
    """Store the client's own HeyGen key (required) and ElevenLabs key
    (optional — voice cloning is its own choice). Same self-service pattern
    as the Higgsfield/WordPress cards; validated by first real use."""
    if not (heygen_key or "").strip():
        return {"success": False, "errors": ["heygen_key is required"]}
    from agents.client_agent import upsert_account
    upsert_account(client_id, HEYGEN_PLATFORM, "heygen", heygen_key.strip(), "active")
    connected = ["heygen"]
    if (elevenlabs_key or "").strip():
        upsert_account(client_id, ELEVENLABS_PLATFORM, "elevenlabs",
                       elevenlabs_key.strip(), "active")
        connected.append("elevenlabs")
    _log_activity(client_id, "avatar_accounts_connected", {"connected": connected})
    log_step(AGENT_NAME, "connect_accounts", f"client {client_id}: {connected}")
    return {"success": True, "connected": connected}


# ─── Consent (mandatory, recorded — the non-negotiable gate) ──────────────────

def record_consent(client_id: int, scope: str) -> dict:
    """One explicit, timestamped consent record per scope. The dashboard's
    checkbox calls this; the statement text + version are stored so it's
    auditable exactly as given."""
    if scope not in CONSENT_SCOPES:
        return {"success": False, "errors": [f"scope must be one of {CONSENT_SCOPES}"]}
    _log_activity(client_id, "avatar_consent_recorded", {
        "scope": scope,
        "version": CONSENT_VERSION,
        "statement_he": CONSENT_STATEMENTS_HE[scope],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    log_step(AGENT_NAME, "record_consent", f"client {client_id}: {scope} ({CONSENT_VERSION})")
    return {"success": True, "scope": scope, "version": CONSENT_VERSION}


def has_consent(client_id: int, scope: str) -> bool:
    return any((r.get("details") or {}).get("scope") == scope
               for r in _rows(client_id, "avatar_consent_recorded", limit=20))


_NO_CONSENT = ("explicit {scope} consent has not been recorded for this client - "
               "the client confirms it in the dashboard's avatar card "
               "(POST /api/avatar/consent); creation is blocked until then")


# ─── Source material (reuses media_agent's filming-kit pattern + Drive) ──────

SOURCE_KIT_SYSTEM = """You are uallak's avatar-production coach, writing precise source-material
instructions for ONE Israeli small-business owner, in the CLIENT'S LANGUAGE (from their business
context; Hebrew default). kind='avatar': instructions for HeyGen digital-twin footage — a single
continuous 2-3 minute video, 720p+ (1080p preferred), good even lighting, plain background,
looking at camera, natural pauses, varied short sentences, hands visible sometimes, NO cuts or
filters; PLUS HeyGen's own required consent-statement video (state name + explicit permission to
create the avatar, one take, ~15 seconds — give the exact sentence to say); PLUS one final step:
invite the uallak team member as a Creator-role collaborator to their HeyGen workspace
(Team plan; workspace settings → members — the exact email arrives in the dashboard chat), so
the one-time technical creation is done FOR them, with no password sharing. kind='voice':
instructions for ElevenLabs voice cloning — 1-3 minutes total of clean solo speech, quiet room,
phone mic is fine, natural tone, no music/background noise.

Warm, confidence-building, numbered steps. HARD LIMITS: max 10 steps, 1-2 sentences each;
checklist max 6 one-line items.

Return JSON only:
{"title": "client-language", "steps": ["..."], "checklist": ["..."],
 "consent_sentence": "the exact spoken consent sentence, client language (avatar kind only, else '')"}"""


def request_source_kit(client_id: int, kind: str = "avatar") -> dict:
    """Shot/recording instructions for what HeyGen/ElevenLabs actually need —
    same delivery pattern as media_agent.create_filming_kit: a doc in Drive
    plus a chat message. Also flips the avatar-source folder to WRITER for
    the client so they can upload into it."""
    if kind not in ("avatar", "voice"):
        return {"success": False, "errors": ["kind must be avatar|voice"]}
    from agents.client_agent import get_client, log_communication
    from agents.media_agent import _subfolder
    from agents.seo_agent import _business_context

    try:
        kit = timed_step(
            AGENT_NAME, "source_kit_llm",
            lambda: safe_claude_json_call(
                SOURCE_KIT_SYSTEM,
                json.dumps({"business": _business_context(client_id), "kind": kind},
                           ensure_ascii=False),
                max_tokens=1200, client_id=client_id, cost_category="claude_avatar"))
        folder = _subfolder(client_id, "avatar-source")
        client = get_client(client_id)
        if client.get("email"):
            drive.share_with_user(folder, client["email"], role="writer")
        doc = "\n\n".join(filter(None, [
            kit.get("title", ""),
            "\n".join(f"{i + 1}. {s}" for i, s in enumerate(kit.get("steps") or [])),
            "✔ " + "\n✔ ".join(kit.get("checklist") or []),
            (f'🎙 משפט ההסכמה לצילום: "{kit["consent_sentence"]}"'
             if kit.get("consent_sentence") else ""),
        ]))
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        uploaded = drive.upload_bytes(folder, f"source-kit-{kind}-{stamp}.txt",
                                      doc.encode("utf-8"), "text/plain")
    except Exception as e:  # includes ClaudeJSONError
        agent_alert(AGENT_NAME, [f"client {client_id}: source kit ({kind}) failed: {e}"])
        return {"success": False, "errors": [str(e)]}

    folder_link = drive.get_link(folder)
    _log_activity(client_id, "avatar_source_requested", {"kind": kind},
                  {"file_id": uploaded.get("id"), "folder_link": folder_link})
    log_communication(client_id, "outbound", "dashboard_chat",
                      f"הכנו לך הוראות מדויקות לחומרי הגלם של האווטאר 🎬 ההנחיות מחכות "
                      f"בתיקיית avatar-source ב-Drive שלך — מעלים את הצילומים לאותה תיקייה "
                      f"בדיוק, ומשם אנחנו ממשיכים: {folder_link}")
    return {"success": True, "kind": kind, "folder_link": folder_link,
            "file_id": uploaded.get("id")}


def _source_files(client_id: int) -> list:
    from agents.media_agent import _subfolder
    return drive.list_files(_subfolder(client_id, "avatar-source"))


# ─── Avatar creation (HeyGen — consent-gated) ─────────────────────────────────

def create_avatar(client_id: int, avatar_name: str = "") -> dict:
    """Create the client's avatar from their uploaded source material, on
    THEIR HeyGen account. Photos-only → photo-avatar path (standard API).
    Video footage → digital-twin path: tries the Enterprise creation API;
    on a permission error returns the web-UI fallback instructions instead
    of failing opaquely. Consent (likeness) is a hard gate."""
    if not has_consent(client_id, "likeness"):
        return {"success": False, "errors": [_NO_CONSENT.format(scope="likeness")]}
    api_key = _key(client_id, HEYGEN_PLATFORM)
    if not api_key:
        return {"success": False, "errors": ["client's HeyGen account is not connected (dashboard avatar card)"]}

    files = _source_files(client_id)
    videos = [f for f in files if (f.get("mimeType") or "").startswith("video/")]
    photos = [f for f in files if (f.get("mimeType") or "").startswith("image/")]
    if not videos and not photos:
        return {"success": False,
                "errors": ["no source material in avatar-source - run request-source first, "
                           "then the client uploads their footage/photos there"]}

    from agents.client_agent import get_client
    name = (avatar_name or f"{get_client(client_id).get('name', '')} avatar").strip()
    known_ids = []
    try:
        known_ids = [a.get("avatar_id") for a in hg.list_avatars(api_key)]
    except Exception as e:
        log_step(AGENT_NAME, "create_avatar", f"client {client_id}: pre-list failed ({e})")

    log_step(AGENT_NAME, "create_avatar",
             f"client {client_id}: {len(videos)} videos, {len(photos)} photos")
    try:
        if videos:
            # Digital twin: training footage + consent video as public URLs.
            # Naming convention from the source kit: the consent clip's
            # filename contains 'consent'; the longest other video is training.
            consent_clip = next((v for v in videos if "consent" in (v.get("name") or "").lower()), None)
            training = max((v for v in videos if v is not consent_clip),
                           key=lambda v: int(v.get("size") or 0), default=None)
            if not consent_clip or not training:
                return {"success": False, "errors": [
                    "digital twin needs BOTH a training video and a consent video whose filename "
                    "contains 'consent' (HeyGen's own requirement - see the source kit)"]}
            result = timed_step(
                AGENT_NAME, "submit_digital_twin",
                lambda: hg.submit_digital_twin(
                    api_key, name,
                    training_footage_url=drive.make_file_public(training["id"]),
                    video_consent_url=drive.make_file_public(consent_clip["id"])))
            method = "digital_twin_api"
        else:
            keys = []
            for photo in photos[:10]:
                asset = hg.upload_asset(api_key, drive.download_file(photo["id"]),
                                        photo.get("mimeType") or "image/jpeg")
                keys.append(asset.get("image_key") or asset.get("id"))
            group = hg.create_photo_avatar_group(api_key, name, keys)
            group_id = group.get("group_id") or group.get("id")
            result = hg.train_photo_avatar_group(api_key, group_id)
            method = "photo_avatar"
    except Exception as e:
        message = str(e)
        if any(word in message.lower() for word in ("permission", "forbidden", "enterprise", "401", "403")):
            # Non-Enterprise API key: twin creation happens in HeyGen's web UI.
            # The decided flow (2026-07-19): the CLIENT invites Johnny's own
            # HeyGen login as a Creator-role collaborator to their workspace
            # (Team plan+; scoped role - no password sharing, no full account
            # access), and JOHNNY performs the one-time creation there. After
            # that, all generation runs on the client's API key as usual.
            _log_activity(client_id, "avatar_creation_submitted",
                          {"method": "workspace_invite", "name": name,
                           "known_avatar_ids": known_ids})
            agent_alert(AGENT_NAME, [
                f"client {client_id}: HeyGen twin-creation API not available on their key "
                f"({message[:150]}) - have the client invite you as a Creator-role "
                f"collaborator to their HeyGen workspace (Team plan), then create avatar "
                f"'{name}' there yourself (their footage is in avatar-source); the daily "
                f"scan will detect it when ready"])
            return {"success": True, "method": "workspace_invite",
                    "note": "creation API unavailable on this HeyGen key - the client invites "
                            "Johnny as a Creator-role workspace collaborator and he performs "
                            "the one-time creation; readiness scan will detect and notify"}
        agent_alert(AGENT_NAME, [f"client {client_id}: avatar creation failed: {e}"])
        return {"success": False, "errors": [message]}

    _log_activity(client_id, "avatar_creation_submitted",
                  {"method": method, "name": name, "known_avatar_ids": known_ids},
                  {"response": str(result)[:500]})
    agent_alert(AGENT_NAME, [f"client {client_id}: avatar '{name}' submitted via {method} - "
                             f"the daily scan notifies when ready (typically days)"])
    return {"success": True, "method": method, "name": name}


def list_ready_avatars(client_id: int) -> list:
    """Every ready avatar on record (multi-avatar support — 'each additional
    avatar: 100₪' in PRICING), newest first, deduped by avatar_id. This is
    the picker's data source; generate_avatar_video takes any of these ids."""
    avatars, seen = [], set()
    for row in _rows(client_id, "avatar_ready", limit=50):
        details = row.get("details") or {}
        avatar_id = details.get("avatar_id")
        if not avatar_id or avatar_id in seen:
            continue
        seen.add(avatar_id)
        avatars.append({"avatar_id": avatar_id,
                        "avatar_name": details.get("avatar_name", ""),
                        "ready_at": row.get("created_at")})
    return avatars


def run_readiness_scan() -> dict:
    """Daily: for every client with avatar submissions, diff their HeyGen
    avatar list against everything already known (ids present at each
    submission + avatars already announced) — so SECOND and later avatars
    are detected too, not just the first. Each new one → notify the client
    (they must never be left wondering; training takes days)."""
    from agents.client_agent import log_communication
    rows = (_db().table("client_activity").select("client_id,details,created_at")
            .eq("agent_name", AGENT_NAME).eq("action_type", "avatar_creation_submitted")
            .order("created_at", desc=True).limit(500).execute().data or [])
    submissions = {}
    for row in rows:
        submissions.setdefault(row["client_id"], []).append(row)

    summary = {"clients_scanned": 0, "ready": 0, "failures": 0}
    for client_id, subs in submissions.items():
        api_key = _key(client_id, HEYGEN_PLATFORM)
        if not api_key:
            continue
        # Stop condition: only scan while an avatar is actually EXPECTED (a
        # submission newer than the latest detected avatar). Without it, a
        # client scanned forever would get a false "ready 🎉" whenever HeyGen
        # adds new STOCK avatars to their library (list_avatars includes them).
        ready = list_ready_avatars(client_id)
        latest_ready_at = ready[0]["ready_at"] if ready else ""
        if not any((s.get("created_at") or "") > latest_ready_at for s in subs):
            continue
        summary["clients_scanned"] += 1
        try:
            known = {i for s in subs
                     for i in ((s.get("details") or {}).get("known_avatar_ids") or [])}
            known |= {a["avatar_id"] for a in ready}
            new = [a for a in hg.list_avatars(api_key) if a.get("avatar_id") not in known]
            # Never announce more avatars than are actually pending — extra
            # unknown ids during the window are likely new HeyGen stock avatars
            pending_count = sum(1 for s in subs if (s.get("created_at") or "") > latest_ready_at)
            for avatar in new[:pending_count]:
                _log_activity(client_id, "avatar_ready",
                              {"avatar_id": avatar.get("avatar_id"),
                               "avatar_name": avatar.get("avatar_name", "")})
                log_communication(client_id, "outbound", "dashboard_chat",
                                  "האווטאר הדיגיטלי שלך מוכן! 🎉 מעכשיו אפשר להפיק איתו סרטונים — "
                                  "נתחיל להכין את הסרטון הראשון ונעדכן אותך כשיחכה לאישור ב-Drive.")
                agent_alert(AGENT_NAME, [f"client {client_id}: avatar {avatar.get('avatar_id')} is READY"])
                summary["ready"] += 1
        except Exception as e:
            summary["failures"] += 1
            log_step(AGENT_NAME, "readiness_scan", f"client {client_id}: {e}")
    log_step(AGENT_NAME, "readiness_scan", f"done — {summary}")
    return summary


# ─── Voice cloning (ElevenLabs — consent-gated) ───────────────────────────────

def create_voice_clone(client_id: int) -> dict:
    """Clone the client's voice from audio in avatar-source, on THEIR
    ElevenLabs account. Consent (voice) is a hard gate — separate from the
    likeness consent, same rigor."""
    if not has_consent(client_id, "voice"):
        return {"success": False, "errors": [_NO_CONSENT.format(scope="voice")]}
    api_key = _key(client_id, ELEVENLABS_PLATFORM)
    if not api_key:
        return {"success": False, "errors": ["client's ElevenLabs account is not connected (dashboard avatar card)"]}
    audio = [f for f in _source_files(client_id)
             if (f.get("mimeType") or "").startswith("audio/")]
    if not audio:
        return {"success": False,
                "errors": ["no audio files in avatar-source - run request-source kind=voice first"]}

    from agents.client_agent import get_client, log_communication
    try:
        samples = [(f["name"], drive.download_file(f["id"]), f["mimeType"])
                   for f in audio[:5]]
        voice_id = timed_step(
            AGENT_NAME, "voice_clone",
            lambda: el.create_voice_clone(
                api_key, f"{get_client(client_id).get('name', '')} voice", samples))
    except Exception as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: voice clone failed: {e}"])
        return {"success": False, "errors": [str(e)]}

    _log_activity(client_id, "avatar_voice_created", {"voice_id": voice_id,
                                                      "samples": len(samples)})
    log_communication(client_id, "outbound", "dashboard_chat",
                      "שיבוט הקול שלך מוכן! 🎙 מעכשיו סרטוני האווטאר יכולים לדבר בקול שלך.")
    return {"success": True, "voice_id": voice_id}


# ─── Tier + minutes tracking (the billed unit) ────────────────────────────────

def _tiers() -> list:
    from agents.onboarding_agent import PRICING
    return PRICING["avatar"]["monthly_tiers"]


def set_tier(client_id: int, tier_id: str) -> dict:
    """Assign the client's avatar tier (admin). 'custom' = above 40 min/month,
    individually quoted. Stored as an activity row, newest wins — the same
    derivation idiom as subscription info."""
    valid = [t["id"] for t in _tiers()] + ["custom"]
    if tier_id not in valid:
        return {"success": False, "errors": [f"tier_id must be one of {valid}"]}
    _log_activity(client_id, "avatar_tier_set", {"tier_id": tier_id})
    return {"success": True, "tier_id": tier_id}


def _current_tier(client_id: int):
    rows = _rows(client_id, "avatar_tier_set", limit=1)
    if not rows:
        return None
    tier_id = (rows[0].get("details") or {}).get("tier_id")
    if tier_id == "custom":
        return {"id": "custom", "minutes_per_month": None}
    return next((t for t in _tiers() if t["id"] == tier_id), None)


def get_monthly_usage(client_id: int) -> dict:
    """Minutes consumed this calendar month vs the tier cap — minutes are the
    billed unit; video counts are for conversation only."""
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    rows = (_db().table("client_activity").select("details,created_at")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", "avatar_video_created")
            .gte("created_at", month_start).limit(500).execute().data or [])
    seconds = sum(float((r.get("details") or {}).get("duration_seconds") or 0) for r in rows)
    tier = _current_tier(client_id)
    cap_minutes = tier.get("minutes_per_month") if tier else None
    return {"tier": tier["id"] if tier else None,
            "videos_this_month": len(rows),
            "minutes_used": round(seconds / 60, 2),
            "minutes_cap": cap_minutes,
            "minutes_remaining": (round(cap_minutes - seconds / 60, 2)
                                  if cap_minutes is not None else None),
            # Setup-fee basis: first avatar 150₪, each additional 100₪ (PRICING)
            "avatars_ready": len(list_ready_avatars(client_id))}


# ─── Avatar video generation (tier-gated, minutes-tracked) ────────────────────

def generate_avatar_video(client_id: int, script_text: str,
                          avatar_id: str = "", heygen_voice_id: str = "") -> dict:
    """One avatar video on the client's HeyGen account: their ready avatar +
    either their ElevenLabs cloned voice (preferred when it exists) or a
    HeyGen stock voice. Gates, in order: consent, tier assigned, minutes
    remaining, accounts connected. Lands in Drive videos/avatar/ for human
    review — never auto-published (house rule)."""
    if not has_consent(client_id, "likeness"):
        return {"success": False, "errors": [_NO_CONSENT.format(scope="likeness")]}
    tier = _current_tier(client_id)
    if not tier:
        return {"success": False, "errors": ["no avatar tier assigned - POST /api/avatar/set-tier "
                                             "(this add-on is never part of standard management)"]}
    usage = get_monthly_usage(client_id)
    if usage["minutes_remaining"] is not None and usage["minutes_remaining"] <= 0:
        agent_alert(AGENT_NAME, [f"client {client_id}: avatar tier '{tier['id']}' minutes exhausted "
                                 f"({usage['minutes_used']}/{usage['minutes_cap']}) - upsell moment"])
        return {"success": False, "errors": [
            f"monthly minutes cap reached ({usage['minutes_used']}/{usage['minutes_cap']} min) - "
            f"generation blocked until next month or a tier upgrade"]}
    api_key = _key(client_id, HEYGEN_PLATFORM)
    if not api_key:
        return {"success": False, "errors": ["client's HeyGen account is not connected"]}
    if not (script_text or "").strip():
        return {"success": False, "errors": ["script_text is required"]}

    if not avatar_id:
        # Multi-avatar picker: pass an explicit avatar_id from
        # list_ready_avatars (GET /api/avatar/list). Default = the newest
        # ready avatar when the client has only one (the common case).
        ready = list_ready_avatars(client_id)
        if len(ready) > 1:
            return {"success": False, "errors": [
                "client has multiple ready avatars - pass avatar_id explicitly "
                f"(choices: {[(a['avatar_id'], a['avatar_name']) for a in ready]})"]}
        avatar_id = ready[0]["avatar_id"] if ready else ""
        if not avatar_id:
            return {"success": False, "errors": ["no ready avatar on record and no avatar_id given"]}

    from agents.media_agent import _subfolder
    try:
        audio_url = ""
        voice_rows = _rows(client_id, "avatar_voice_created", limit=1)
        elevenlabs_key = _key(client_id, ELEVENLABS_PLATFORM)
        if voice_rows and elevenlabs_key:
            # Cloned voice path: 11Labs TTS → Drive → public URL → HeyGen audio
            voice_id = (voice_rows[0].get("details") or {}).get("voice_id")
            audio = timed_step(AGENT_NAME, "tts",
                               lambda: el.text_to_speech(elevenlabs_key, voice_id, script_text))
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
            audio_file = drive.upload_bytes(_subfolder(client_id, "videos", "avatar"),
                                            f"voiceover-{stamp}.mp3", audio, "audio/mpeg")
            audio_url = drive.make_file_public(audio_file["id"])
        elif not heygen_voice_id:
            return {"success": False, "errors": [
                "no cloned voice on record and no heygen_voice_id given - pass a HeyGen stock "
                "voice id or clone the client's voice first"]}

        video_id = timed_step(
            AGENT_NAME, "heygen_generate",
            lambda: hg.generate_avatar_video(api_key, avatar_id, script_text,
                                             voice_id=heygen_voice_id, audio_url=audio_url))
        done = timed_step(AGENT_NAME, "heygen_wait",
                          lambda: hg.wait_for_video(api_key, video_id))
        duration = float(done.get("duration") or 0)
    except Exception as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: avatar video failed: {e}"])
        return {"success": False, "errors": [str(e)]}

    # Store the finished video in Drive (HeyGen's video_url is temporary)
    try:
        content = httpx.get(done["video_url"], follow_redirects=True, timeout=300).content
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        uploaded = drive.upload_bytes(_subfolder(client_id, "videos", "avatar"),
                                      f"avatar-{stamp}.mp4", content, "video/mp4")
    except Exception as e:
        uploaded = {}
        agent_alert(AGENT_NAME, [f"client {client_id}: avatar video rendered but Drive save "
                                 f"failed ({e}) - grab it from HeyGen: {done.get('video_url', '')[:120]}"])

    _log_activity(client_id, "avatar_video_created",
                  {"avatar_id": avatar_id, "duration_seconds": duration,
                   "script_preview": script_text[:150]},
                  {"heygen_video_id": video_id, "file_id": uploaded.get("id"),
                   "link": uploaded.get("webViewLink", "")})
    usage = get_monthly_usage(client_id)
    log_step(AGENT_NAME, "generate_avatar_video",
             f"client {client_id}: {duration}s, {usage['minutes_used']}/{usage['minutes_cap']} min used")
    return {"success": True, "duration_seconds": duration,
            "link": uploaded.get("webViewLink", ""), "usage": usage}
