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

HeyGen API reality (re-verified 2026-07-20): photo-avatar creation/training
is standard API; VIDEO digital-twin creation via API is Enterprise-only, BUT
web-UI twin creation is available on EVERY plan including Free (1 twin
included; more need a paid plan/add-on) — so the CLIENT creates the twin
themselves in HeyGen's web UI following our source kit, and this agent
detects readiness by scanning their avatar list, then generates videos with
it (standard pay-as-you-go API — also plan-independent).

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
from core import drive_storage

AGENT_NAME = "avatar_agent"
HEYGEN_PLATFORM = "heygen"
ELEVENLABS_PLATFORM = "elevenlabs"

CONSENT_SCOPES = ("likeness", "voice")
# Bump when the statement wording changes — every consent row records the
# version it was given under
# v2 (2026-10-30): the wording names the capability, not the vendor (the
# vendor is named in the connection help, where the client opens the account)
CONSENT_VERSION = "2026-10-30.v2"
CONSENT_STATEMENTS_HE = {
    "likeness": ("אני מאשר/ת במפורש ל-uallak להשתמש בצילומים ובתמונות שאעלה כדי ליצור "
                 "דמות דיגיטלית מצולמת שלי בחשבון שפתחתי לשם כך, ולהפיק באמצעותה סרטונים "
                 "עבור העסק שלי. אוכל לבקש את הפסקת השימוש בכל עת."),
    "voice": ("אני מאשר/ת במפורש ל-uallak להשתמש בהקלטות הקול שאעלה כדי ליצור שיבוט קול "
              "דיגיטלי בחשבון שפתחתי לשם כך, ולהשתמש בו בסרטונים עבור העסק שלי. "
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
    """The client's OWN HeyGen/ElevenLabs key, decrypted for this job only —
    "" (abort) when it can't be proven to be this client's alone
    (core/secrets_vault.use_vendor_key)."""
    from core.secrets_vault import use_vendor_key
    return use_vendor_key(client_id, platform, job=f"avatar_agent:{platform}")


# ─── Connections (client self-service, dashboard card) ────────────────────────

def connect_accounts(client_id: int, heygen_key: str = "", elevenlabs_key: str = "") -> dict:
    """LEGACY entry (POST /api/avatar/connect): each key given is connected
    ON ITS OWN through core/vendor_tools — either may be given without the
    other. The dashboard now uses one card per tool (/api/client/tools/*)."""
    from core import vendor_tools
    connected, errors = [], []
    for tool, key in (("heygen", heygen_key), ("elevenlabs", elevenlabs_key)):
        if not (key or "").strip():
            continue
        try:
            vendor_tools.connect(client_id, tool, key)
            connected.append(tool)
        except vendor_tools.ToolError as e:
            errors.append(f"{tool}: {e.code}")
    if not connected and not errors:
        errors.append("no key given")
    log_step(AGENT_NAME, "connect_accounts", f"client {client_id}: connected {connected}, refused {errors}")
    return {"success": bool(connected), "connected": connected, "errors": errors}


def current_voice_id(client_id: int) -> str:
    """The client's ElevenLabs clone, unless it was deleted since."""
    created = _rows(client_id, "avatar_voice_created", limit=1)
    if not created:
        return ""
    deleted = _rows(client_id, "avatar_voice_deleted", limit=1)
    if deleted and (deleted[0].get("created_at") or "") >= (created[0].get("created_at") or ""):
        return ""
    return (created[0].get("details") or {}).get("voice_id") or ""


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
               "the client confirms it on the tool's own dashboard card (likeness: HeyGen, "
               "voice: ElevenLabs; POST /api/avatar/consent); creation is blocked until then")


# ─── Source material (reuses media_agent's filming-kit pattern + Drive) ──────

SOURCE_KIT_SYSTEM = """You are uallak's avatar-production coach, writing precise source-material
instructions for ONE Israeli small-business owner, in the CLIENT'S LANGUAGE (from their business
context; Hebrew default). kind='avatar': instructions for HeyGen digital-twin footage — a single
continuous 2-3 minute video, 720p+ (1080p preferred), good even lighting, plain background,
looking at camera, natural pauses, varied short sentences, hands visible sometimes, NO cuts or
filters; PLUS HeyGen's own required consent-statement video (state name + explicit permission to
create the avatar, one take, ~15 seconds — give the exact sentence to say); PLUS one final step:
create the avatar THEMSELVES in HeyGen's website (a few clicks, works on every plan including
the free one — in HeyGen: Avatars → create digital twin → upload the SAME training footage and
consent video they filmed); once it finishes training, our system detects it automatically and
lets them know. kind='voice':
instructions for ElevenLabs voice cloning — 1-3 minutes total of clean solo speech, quiet room,
phone mic is fine, natural tone, no music/background noise.

Warm, confidence-building, numbered steps. HARD LIMITS: max 10 steps, 1-2 sentences each;
checklist max 6 one-line items.

Return JSON only:
{"title": "client-language", "steps": ["..."], "checklist": ["..."],
 "consent_sentence": "the exact spoken consent sentence, client language (avatar kind only, else '')"}"""


@drive_storage.scoped
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


@drive_storage.scoped
def _source_files(client_id: int) -> list:
    from agents.media_agent import _subfolder
    return drive.list_files(_subfolder(client_id, "avatar-source"))


# ─── Avatar creation (HeyGen — consent-gated) ─────────────────────────────────

@drive_storage.scoped
def create_avatar(client_id: int, avatar_name: str = "", photo_ids: list = None) -> dict:
    """Create the client's avatar from their uploaded source material, on
    THEIR HeyGen account. Photos-only → photo-avatar path (standard API).
    Video footage → digital-twin path: tries the Enterprise creation API;
    on a permission error the CLIENT is pointed to HeyGen's web UI to create
    the twin themselves (available on every plan incl. Free) instead of
    failing opaquely. Consent (likeness) is a hard gate.

    `photo_ids`: images the client uploaded in a chat (the
    create_presenter_from_photos card, which already proved each one is in
    their media folder) — the photo-avatar path from exactly those, instead
    of the avatar-source folder."""
    if not has_consent(client_id, "likeness"):
        return {"success": False, "code": "no_consent", "errors": [_NO_CONSENT.format(scope="likeness")]}
    api_key = _key(client_id, HEYGEN_PLATFORM)
    if not api_key:
        from core import vendor_tools
        return {**vendor_tools.missing_block(["heygen"]),
                "errors": ["client's HeyGen account is not connected (dashboard HeyGen card)"]}

    if photo_ids:
        videos = []
        photos = []
        for file_id in photo_ids[:10]:
            meta = drive.get_meta(file_id, "id,name,mimeType,trashed")
            if not meta.get("trashed") and str(meta.get("mimeType") or "").startswith("image/"):
                photos.append(meta)
        if not photos:
            return {"success": False, "code": "no_photos",
                    "errors": ["none of the chosen photos is still an image in the media folder"]}
    else:
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
            # Non-Enterprise API key (the normal case): twin creation happens
            # in HeyGen's web UI, BY THE CLIENT — decided 2026-07-20 after
            # HeyGen's pricing page confirmed every plan (incl. Free) includes
            # web-UI custom-twin creation; only the creation API is
            # Enterprise-gated. The source kit already walks them through it;
            # the daily scan detects the finished avatar and notifies them.
            from agents.client_agent import log_communication
            _log_activity(client_id, "avatar_creation_submitted",
                          {"method": "web_ui_self_service", "name": name,
                           "known_avatar_ids": known_ids})
            log_communication(client_id, "outbound", "dashboard_chat",
                              "הצעד האחרון ליצירת הדמות המצולמת שלכם הוא אצלכם 🎬 נכנסים לחשבון "
                              "שפתחתם לדמות, בוחרים יצירת דמות דיגיטלית, ומעלים שם את אותם צילומים "
                              "וסרטון ההסכמה שהכנתם לפי הערכה (ההוראות המדויקות מחכות בערכה ב-Drive). "
                              "ברגע שהדמות תהיה מוכנה — נזהה אותה אוטומטית ונעדכן אתכם 🎉 "
                              "נתקעתם? כתבו לי כאן ונעשה את זה יחד.")
            log_step(AGENT_NAME, "create_avatar",
                     f"client {client_id}: twin-creation API unavailable ({message[:120]}) - "
                     f"client directed to self-create '{name}' in HeyGen's web UI")
            return {"success": True, "method": "web_ui_self_service",
                    "note": "creation API unavailable on this HeyGen key (normal - it is "
                            "Enterprise-only) - the client creates the twin themselves in "
                            "HeyGen's web UI per the source kit (any plan, incl. free); "
                            "the readiness scan will detect and notify"}
        agent_alert(AGENT_NAME, [f"client {client_id}: avatar creation failed: {e}"])
        return {"success": False, "code": "provider_error", "errors": [message]}

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

def _voice_samples(client_id: int, file_ids: list = None) -> list:
    """[(name, id, mime)] — the dashboard's voice recordings first (the
    client's upload section, core/voice_recordings), then any audio left in
    avatar-source by the older flow."""
    from core import voice_recordings
    rows = voice_recordings.list_recordings(client_id)
    if file_ids:
        wanted = set(file_ids)
        rows = [r for r in rows if r["id"] in wanted]
    samples = [(r["name"], r["id"], r["mime"] or "audio/mpeg") for r in rows]
    if not samples and not file_ids:
        samples = [(f["name"], f["id"], f["mimeType"]) for f in _source_files(client_id)
                   if (f.get("mimeType") or "").startswith("audio/")]
    return samples[:5]


@drive_storage.scoped
def create_voice_clone(client_id: int, file_ids: list = None, notify: bool = True) -> dict:
    """Clone the client's voice from their recordings, on THEIR ElevenLabs
    account. The voice-cloning consent is a hard gate (separate from the
    likeness consent, same rigor) — without it a recording is only ever an
    audio track. ElevenLabs errors come back as one plain Hebrew sentence."""
    from core import vendor_tools
    if not has_consent(client_id, "voice"):
        return {"success": False, "code": "no_voice_consent", "errors": [_NO_CONSENT.format(scope="voice")],
                "message_he": "שיבוט קול דורש את הסכמת שיבוט הקול בכרטיס \"הקול שלך\"."}
    if vendor_tools.missing(client_id, ["elevenlabs"]):
        return {**vendor_tools.missing_block(["elevenlabs"]), "errors": ["elevenlabs not connected"]}
    api_key = _key(client_id, ELEVENLABS_PLATFORM)
    if not api_key:
        return {**vendor_tools.missing_block(["elevenlabs"]), "errors": ["elevenlabs key unusable"]}
    samples = _voice_samples(client_id, file_ids)
    if not samples:
        return {"success": False, "code": "no_recordings", "errors": ["no voice recordings"],
                "message_he": "עוד אין הקלטות קול. מעלים הקלטה באזור \"הקלטות קול\" בדשבורד."}

    from agents.client_agent import get_client, log_communication
    try:
        files = [(name, drive.download_file(fid), mime) for name, fid, mime in samples]
        voice_id = timed_step(
            AGENT_NAME, "voice_clone",
            lambda: el.create_voice_clone(
                api_key, f"{get_client(client_id).get('name', '')} voice", files))
    except el.ElevenLabsError as e:
        if e.key_rejected:
            vendor_tools.mark_rejected(client_id, "elevenlabs")
        log_step(AGENT_NAME, "voice_clone", f"client {client_id}: refused by ElevenLabs ({e.status} {e.code})")
        return {"success": False, "code": "elevenlabs_error", "errors": [f"{e.status} {e.code}"],
                "message_he": el.plain_he(e)}
    except Exception as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: voice clone failed: {type(e).__name__}"])
        return {"success": False, "code": "error", "errors": [type(e).__name__],
                "message_he": el.plain_he(e)}

    _log_activity(client_id, "avatar_voice_created", {"voice_id": voice_id,
                                                      "samples": len(samples)})
    _record_tool_use(client_id, "elevenlabs", "voice_clone", units=len(samples))
    if notify:
        # notify=False: a chat card (core/agent_actions clone_voice) posts the outcome itself
        log_communication(client_id, "outbound", "dashboard_chat",
                          "שיבוט הקול שלך מוכן! 🎙 מעכשיו הסרטונים יכולים לדבר בקול שלך.")
    return {"success": True, "voice_id": voice_id}


def delete_voice_clone(client_id: int) -> dict:
    """The client asked to delete their voice: the clone goes from THEIR
    ElevenLabs account (and is recorded as deleted, so nothing uses it). The
    recordings themselves are deleted by core/voice_recordings.delete."""
    from core import vendor_tools
    voice_id = current_voice_id(client_id)
    if not voice_id:
        return {"success": True, "deleted": False, "note": "no clone on record"}
    api_key = _key(client_id, ELEVENLABS_PLATFORM)
    if not api_key:
        return {**vendor_tools.missing_block(["elevenlabs"]),
                "message_he": ("כדי למחוק את הקול המשובט מהחשבון שלך צריך שהכרטיס \"הקול שלך\" יהיה מחובר. "
                               "הצוות קיבל התראה ויעזור למחוק אותו.")}
    try:
        el.delete_voice(api_key, voice_id)
    except el.ElevenLabsError as e:
        if e.key_rejected:
            vendor_tools.mark_rejected(client_id, "elevenlabs")
        return {"success": False, "code": "elevenlabs_error", "message_he": el.plain_he(e)}
    except Exception as e:
        return {"success": False, "code": "error", "message_he": el.plain_he(e)}
    _log_activity(client_id, "avatar_voice_deleted", {"voice_id": voice_id})
    log_step(AGENT_NAME, "delete_voice_clone", f"client {client_id}: clone deleted")
    return {"success": True, "deleted": True}


def _record_tool_use(client_id: int, tool: str, kind: str, usd_estimate: float = 0.0, units=None):
    """Cost tracking stays PER TOOL: one client_costs row per job, tagged with
    the tool (cost_tracker.record_client_paid_media — 0 for a paying client,
    whose own vendor account pays; the estimate for an internal account)."""
    try:
        from core.cost_tracker import record_client_paid_media
        record_client_paid_media(client_id, usd_estimate, credits=units, kind=kind, model=tool, tool=tool)
    except Exception as e:
        log_step(AGENT_NAME, "cost", f"client {client_id}: {tool} cost not recorded ({type(e).__name__})")


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

@drive_storage.scoped
def generate_avatar_video(client_id: int, script_text: str = "", avatar_id: str = "",
                          heygen_voice_id: str = "", provider: str = "", voice: dict = None) -> dict:
    """One avatar video on the provider the client chose (HeyGen avatar or a
    Higgsfield character — core/avatar_video), with the same voice sources
    for either: an uploaded recording as the audio track, the ElevenLabs
    clone reading the script, or (HeyGen) a stock voice. Gates here, in
    order: likeness consent, tier assigned, minutes remaining; the provider
    layer then names any missing tool. Lands in Drive videos/avatar/ for
    human review — never auto-published (house rule)."""
    if not has_consent(client_id, "likeness"):
        return {"success": False, "code": "no_likeness_consent", "errors": [_NO_CONSENT.format(scope="likeness")]}
    tier = _current_tier(client_id)
    if not tier:
        return {"success": False, "code": "no_tier",
                "errors": ["no avatar tier assigned - POST /api/avatar/set-tier "
                           "(this add-on is never part of standard management)"]}
    usage = get_monthly_usage(client_id)
    if usage["minutes_remaining"] is not None and usage["minutes_remaining"] <= 0:
        agent_alert(AGENT_NAME, [f"client {client_id}: avatar tier '{tier['id']}' minutes exhausted "
                                 f"({usage['minutes_used']}/{usage['minutes_cap']}) - upsell moment"])
        return {"success": False, "code": "minutes_cap", "errors": [
            f"monthly minutes cap reached ({usage['minutes_used']}/{usage['minutes_cap']} min) - "
            f"generation blocked until next month or a tier upgrade"]}
    from agents.media_agent import _subfolder
    from core import avatar_video, vendor_tools
    if voice is None and not heygen_voice_id:
        # Legacy callers: the clone when one exists, else nothing to say it with
        voice = {"kind": "clone"} if current_voice_id(client_id) else None
    if (voice or {}).get("kind") == "clone" and not (script_text or "").strip():
        return {"success": False, "code": "no_script", "errors": ["script_text is required for the cloned voice"]}
    try:
        done = timed_step(AGENT_NAME, "avatar_video",
                          lambda: avatar_video.make_video(client_id, script_text, voice, provider or None,
                                                          avatar_id, heygen_voice_id))
    except Exception as e:
        message = str(e)
        if any(code in message for code in (" 401 ", " 403 ", "401 ", "403 ")):
            vendor_tools.mark_rejected(client_id, provider or vendor_tools.default_provider(client_id))
        agent_alert(AGENT_NAME, [f"client {client_id}: avatar video failed: {type(e).__name__}: {message[:200]}"])
        return {"success": False, "code": "provider_error", "errors": [message[:300]]}
    if not done.get("success"):
        return {**done, "errors": [done.get("code", "refused")]}
    duration = float(done.get("duration") or 0)
    video_id = done.get("job_id", "")
    avatar_id = done.get("avatar_id", avatar_id)

    # Store the finished video in Drive (HeyGen's video_url is temporary)
    try:
        content = httpx.get(done["video_url"], follow_redirects=True, timeout=300).content
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        uploaded = drive.upload_bytes(_subfolder(client_id, "videos", "avatar"),
                                      f"avatar-{stamp}.mp4", content, "video/mp4")
    except Exception as e:
        uploaded = {}
        agent_alert(AGENT_NAME, [f"client {client_id}: avatar video rendered but Drive save "
                                 f"failed ({type(e).__name__}) - grab it from {done['provider']} "
                                 f"(job {video_id})"])

    _log_activity(client_id, "avatar_video_created",
                  {"avatar_id": avatar_id, "duration_seconds": duration, "provider": done["provider"],
                   "voice_kind": done.get("voice_kind"), "script_preview": (script_text or "")[:150]},
                  {"heygen_video_id" if done["provider"] == "heygen" else "job_id": video_id,
                   "file_id": uploaded.get("id"), "link": uploaded.get("webViewLink", "")})
    # Per-tool cost rows: the video tool, and ElevenLabs when it read the script
    if done["provider"] == "heygen":
        from core.third_party_pricing import THIRD_PARTY_PRICING
        low, high = THIRD_PARTY_PRICING["heygen"]["generation_usd_per_min_range"]
        _record_tool_use(client_id, "heygen", "avatar_video", usd_estimate=duration / 60 * (low + high) / 2,
                         units=round(duration, 1))
    else:
        _record_tool_use(client_id, done["provider"], "avatar_video", units=done.get("credits"))
    if done.get("tts_characters"):
        _record_tool_use(client_id, "elevenlabs", "tts", units=done["tts_characters"])
    usage = get_monthly_usage(client_id)
    log_step(AGENT_NAME, "generate_avatar_video",
             f"client {client_id}: {duration}s, {usage['minutes_used']}/{usage['minutes_cap']} min used")
    return {"success": True, "duration_seconds": duration,
            "link": uploaded.get("webViewLink", ""), "usage": usage}
