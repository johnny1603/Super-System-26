"""uallak's avatar agent — the client's presenter character and voice clone.
A DISTINCT PAID ADD-ON TIER, deliberately separate from media_agent's
regular social content: this agent handles a real human's face and voice.
Never blur the two in pricing or usage tracking.

PRESENTER = HIGGSFIELD (2026-10-31). HeyGen was retired: its avatar-creation
API is Enterprise-only and everything else about it sent the client out of
the product. The presenter now runs on the client's existing Higgsfield
account (the same key media_agent uses — no extra signup): a CHARACTER is one
still image (create_avatar — their own photo, or an AI image media_agent made
for them that they approved) and each video is a short lip-synced clip of
that character speaking an audio track (core/avatar_video, at most
MAX_CLIP_SECONDS). Nothing here imports core/heygen_service any more; the
HeyGen rows of internal test clients stay stored and untouched.

Pricing — single source of truth is PRICING["avatar"] in onboarding_agent
(setup 150₪ first / 100₪ additional avatar; monthly tiers billed by MINUTES:
10min/450₪, 20min/800₪, 40min/1550₪, custom above). Video counts are
client-facing ESTIMATES only. The client separately pays their own creative
tools (Higgsfield; ElevenLabs for a clone) directly (their card, never ours).

CONSENT IS MANDATORY AND RECORDED — not reinterpretable. A character made
from the client's OWN photos, and every video with it, needs the logged
'likeness' consent; cloning their voice needs the 'voice' consent
(avatar_consent_recorded activity: scope, statement version, timestamp). The
tool cards collect them with an explicit checkbox; the server re-checks on
every path. An AI-generated character (no real person) needs no likeness
consent.

Privacy: the vendor takes the image and the audio only as public URLs. Each
share is open only for the call (core/public_share): revoked on success AND
failure, read back to verify, retried once, and an alert if it still stands.

Storage: everything lives under the client's existing media Drive root
(media_agent's folders — reused, not rebuilt): `avatar-source/` holds the
character images (appProperties uallak_character = own_photo | generated),
finished videos land in `videos/avatar/`.
"""
import json
import os
from datetime import datetime, timezone

import httpx
from supabase import create_client as _supabase_client

from core import drive_service as drive
from core import elevenlabs_service as el
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call
from core import drive_storage

AGENT_NAME = "avatar_agent"
HEYGEN_PLATFORM = "heygen"   # RETIRED 2026-10-31 — only to recognise stored rows
PRESENTER_PLATFORM = "higgsfield"
ELEVENLABS_PLATFORM = "elevenlabs"

CONSENT_SCOPES = ("likeness", "voice")
# Bump when the statement wording changes — every consent row records the
# version it was given under
# v2 (2026-10-30): the wording names the capability, not the vendor (the
# vendor is named in the connection help, where the client opens the account)
# v3 (2026-10-31): the likeness statement says the files are shared with the
# tool only while the presenter is being created and are closed right after —
# what core/public_share does. Rows recorded under v1/v2 stay valid.
# v4 (2026-10-31): the presenter is a character made from a photo I choose,
# and the photo + audio are open to the tool only while EACH video is made
# (HeyGen retired — no twin is created once; every clip is a fresh call).
# Rows recorded under v1-v3 stay valid.
CONSENT_VERSION = "2026-10-31.v4"
CONSENT_STATEMENTS_HE = {
    "likeness": ("אני מאשר/ת במפורש ל-uallak להשתמש בתמונות שלי שאבחר כדי ליצור מהן דמות "
                 "דיגיטלית שלי, ולהפיק איתה סרטונים קצרים עבור העסק שלי בחשבון שפתחתי לשם כך. "
                 "התמונה וההקלטה נפתחות לכלי רק בזמן שכל סרטון נוצר, והגישה אליהן נסגרת מיד אחרי. "
                 "אוכל לבקש את הפסקת השימוש בכל עת."),
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
    """The client's OWN ElevenLabs/Higgsfield key, decrypted for this job only —
    "" (abort) when it can't be proven to be this client's alone
    (core/secrets_vault.use_vendor_key)."""
    from core.secrets_vault import use_vendor_key
    return use_vendor_key(client_id, platform, job=f"avatar_agent:{platform}")


# ─── Connections (client self-service, dashboard card) ────────────────────────

def connect_accounts(client_id: int, heygen_key: str = "", elevenlabs_key: str = "") -> dict:
    """LEGACY entry (POST /api/avatar/connect): each key given is connected
    ON ITS OWN through core/vendor_tools. The dashboard uses one card per
    tool (/api/client/tools/*). A HeyGen key is refused — HeyGen is retired
    (2026-10-31) and never stored again."""
    from core import vendor_tools
    connected, errors = [], []
    if (heygen_key or "").strip():
        errors.append("heygen: retired")
    for tool, key in (("elevenlabs", elevenlabs_key),):
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
               "the client confirms it on the tool's own dashboard card (likeness: the presenter "
               "tool's card, voice: the voice card; POST /api/avatar/consent); blocked until then")


# ─── Source material (reuses media_agent's filming-kit pattern + Drive) ──────

SOURCE_KIT_SYSTEM = """You are uallak's avatar-production coach, writing precise source-material
instructions for ONE Israeli small-business owner, in the CLIENT'S LANGUAGE (from their business
context; Hebrew default). kind='avatar': instructions for the ONE photo their presenter character
is made from — a sharp, well-lit photo of them from the chest up, facing the camera, mouth closed
and relaxed, plain uncluttered background, no sunglasses/hat/filters, vertical (portrait) framing,
nobody else in the frame. They send it in the dashboard chat with the "+" button; our system turns
it into their presenter. Each presenter video is a SHORT clip of up to 15 seconds of speech — say so.
Never mention file names, Drive folders or naming rules, NEVER tell the client to create anything
themselves or to visit any other website, and never name a vendor or tool. kind='voice':
instructions for voice cloning — 1-3 minutes total of clean solo speech, quiet room, phone mic is
fine, natural tone, no music/background noise; and for a plain recording used as-is in a presenter
video: at most 15 seconds.

Warm, confidence-building, numbered steps. HARD LIMITS: max 10 steps, 1-2 sentences each;
checklist max 6 one-line items.

Return JSON only:
{"title": "client-language", "steps": ["..."], "checklist": ["..."]}"""


@drive_storage.scoped
def request_source_kit(client_id: int, kind: str = "avatar") -> dict:
    """Photo/recording instructions for what the presenter and the voice
    clone actually need — same delivery pattern as
    media_agent.create_filming_kit: a doc in Drive plus a chat message. The
    photo is sent in the chat (create_presenter_from_photos card); the
    avatar-source folder is still shared as WRITER so files put there the old
    way keep working."""
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
                      (f"הכנו לך הוראות מדויקות לתמונה של הדמות שלך 📸 ({folder_link}) את התמונה "
                       f"שולחים לי כאן בצ'אט עם כפתור ה-+, ומשם אנחנו מכינים את הדמות. כל סרטון מגיש "
                       f"הוא קליפ קצר, עד 15 שניות.")
                      if kind == "avatar" else
                      f"הכנו לך הוראות מדויקות להקלטת הקול 🎙 ({folder_link}) את ההקלטה מעלים "
                      f"בכרטיס \"הקלטות קול\" בדשבורד, ומשם אנחנו ממשיכים.")
    return {"success": True, "kind": kind, "folder_link": folder_link,
            "file_id": uploaded.get("id")}


@drive_storage.scoped
def _source_files(client_id: int) -> list:
    from agents.media_agent import _subfolder
    return drive.list_files(_subfolder(client_id, "avatar-source"),
                            fields="id,name,mimeType,size,createdTime,appProperties")


def footage_view(client_id: int) -> dict:
    """The dashboard presenter card's two slots (core/avatar_footage)."""
    from core import avatar_footage
    from core.agent_actions import _lang
    view = avatar_footage.view(_source_files(client_id), _lang(client_id))
    # Already sent to creation since the newest of the two videos landed —
    # the card says "being created" instead of offering the button again
    newest = max((view[s]["uploaded_at"] for s in avatar_footage.SLOTS if view[s]), default="")
    submitted = _rows(client_id, "avatar_creation_submitted", limit=1)
    view["submitted"] = bool(view["ready"] and submitted
                             and (submitted[0].get("created_at") or "") >= newest)
    return view


# ─── The presenter character (Higgsfield's start image — consent-gated) ──────

CHARACTER_PROP = "uallak_character"   # = core/avatar_video.CHARACTER_PROP
_IMAGE_EXT = {"image/png": ".png", "image/webp": ".webp", "image/jpeg": ".jpg"}


def generated_image_ids(client_id: int) -> set:
    """Drive ids of the images media_agent generated for this client (its
    media_image_created rows) — an AI character, no real person's likeness."""
    try:
        rows = (_db().table("client_activity").select("result")
                .eq("client_id", client_id).eq("agent_name", "media_agent")
                .eq("action_type", "media_image_created")
                .order("created_at", desc=True).limit(500).execute().data or [])
    except Exception as e:
        log_step(AGENT_NAME, "generated_image_ids", f"client {client_id}: unreadable ({type(e).__name__})")
        return set()
    return {(r.get("result") or {}).get("file_id") for r in rows} - {None, ""}


@drive_storage.scoped
def create_avatar(client_id: int, avatar_name: str = "", photo_ids: list = None) -> dict:
    """Make the client's presenter CHARACTER: one still image every presenter
    clip starts from (core/avatar_video). No vendor call and no waiting — the
    chosen image is copied into avatar-source, marked (appProperties
    uallak_character = own_photo | generated) and recorded `avatar_ready`.

    `photo_ids`: images the client chose in a chat (the
    create_presenter_from_photos card already proved each is in their media
    folder); the first one is used. Without them: the newest image put in
    avatar-source the old way.

    An image media_agent generated for them (generated_image_ids) is an AI
    character and needs no likeness consent; anything else is their own
    photo, and the likeness consent is a hard gate."""
    if photo_ids:
        candidates = []
        for file_id in photo_ids[:10]:
            meta = drive.get_meta(file_id, "id,name,mimeType,trashed")
            if not meta.get("trashed") and str(meta.get("mimeType") or "").startswith("image/"):
                candidates.append(meta)
    else:
        candidates = [f for f in _source_files(client_id)
                      if str(f.get("mimeType") or "").startswith("image/")
                      and not (f.get("appProperties") or {}).get(CHARACTER_PROP)]
    if not candidates:
        return {"success": False, "code": "no_photos",
                "errors": ["no usable image - the client sends a photo in the chat"]}
    photo = candidates[0]
    kind = "generated" if photo["id"] in generated_image_ids(client_id) else "own_photo"
    if kind == "own_photo" and not has_consent(client_id, "likeness"):
        return {"success": False, "code": "no_consent", "errors": [_NO_CONSENT.format(scope="likeness")]}

    from agents.client_agent import get_client
    from agents.media_agent import _subfolder
    name = (avatar_name or f"{get_client(client_id).get('name', '')} presenter").strip()
    mime = photo.get("mimeType") or "image/jpeg"
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        uploaded = drive.upload_bytes(_subfolder(client_id, "avatar-source"),
                                      f"presenter-{stamp}{_IMAGE_EXT.get(mime, '.jpg')}",
                                      drive.download_file(photo["id"]), mime,
                                      app_properties={CHARACTER_PROP: kind})
    except Exception as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: presenter character not saved: {type(e).__name__}: {e}"])
        return {"success": False, "code": "provider_error", "errors": [str(e)[:300]]}
    _log_activity(client_id, "avatar_ready",
                  {"avatar_id": uploaded.get("id"), "avatar_name": name, "kind": kind,
                   "provider": PRESENTER_PLATFORM, "source_file_id": photo["id"]})
    log_step(AGENT_NAME, "create_avatar", f"client {client_id}: character ready ({kind})")
    return {"success": True, "method": "character_image", "name": name, "kind": kind,
            "avatar_id": uploaded.get("id")}


def list_ready_avatars(client_id: int) -> list:
    """Every presenter character on record (multi-character support — 'each
    additional avatar: 100₪' in PRICING), newest first, deduped. This is the
    picker's data source; avatar_video.make_video takes any of these ids.
    Rows from the retired HeyGen path (no provider field) are left stored
    but are not characters any more."""
    avatars, seen = [], set()
    for row in _rows(client_id, "avatar_ready", limit=50):
        details = row.get("details") or {}
        avatar_id = details.get("avatar_id")
        if not avatar_id or avatar_id in seen or details.get("provider") != PRESENTER_PLATFORM:
            continue
        seen.add(avatar_id)
        avatars.append({"avatar_id": avatar_id,
                        "avatar_name": details.get("avatar_name", ""),
                        "kind": details.get("kind") or "own_photo",
                        "ready_at": row.get("created_at")})
    return avatars


def run_readiness_scan() -> dict:
    """RETIRED 2026-10-31 with HeyGen: a character is ready the moment it is
    made, so there is nothing to wait for. Kept as a no-op so the existing
    scheduler job (GET /api/avatar/scan) answers instead of failing — it can
    be deleted from Cloud Scheduler."""
    log_step(AGENT_NAME, "readiness_scan", "retired - characters are ready at creation")
    return {"clients_scanned": 0, "ready": 0, "failures": 0, "retired": True}


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
                          provider: str = "", voice: dict = None) -> dict:
    """One short presenter clip (core/avatar_video — Higgsfield, at most
    MAX_CLIP_SECONDS): the client's character speaking an uploaded recording
    or the ElevenLabs clone reading the script. Gates here: tier assigned,
    minutes remaining; avatar_video then checks the gate, the length, the
    character and its likeness consent, and names any missing tool. Lands in
    Drive videos/avatar/ for human review — never auto-published."""
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
    if voice is None:
        # Legacy callers: the clone when one exists, else nothing to say it with
        voice = {"kind": "clone"} if current_voice_id(client_id) else None
    if (voice or {}).get("kind") == "clone" and not (script_text or "").strip():
        return {"success": False, "code": "no_script", "errors": ["script_text is required for the cloned voice"]}
    try:
        done = timed_step(AGENT_NAME, "avatar_video",
                          lambda: avatar_video.make_video(client_id, script_text, voice, provider or None,
                                                          avatar_id))
    except Exception as e:
        message = str(e)
        if any(code in message for code in (" 401 ", " 403 ", "401 ", "403 ")):
            vendor_tools.mark_rejected(client_id, PRESENTER_PLATFORM)
        agent_alert(AGENT_NAME, [f"client {client_id}: avatar video failed: {type(e).__name__}: {message[:200]}"])
        return {"success": False, "code": "provider_error", "errors": [message[:300]]}
    if not done.get("success"):
        return {**done, "errors": [done.get("code", "refused")]}
    duration = float(done.get("duration") or 0)
    video_id = done.get("job_id", "")
    avatar_id = done.get("avatar_id", avatar_id)

    # Store the finished video in Drive (the vendor's result URL is temporary)
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
                   "voice_kind": done.get("voice_kind"), "character_kind": done.get("character_kind"),
                   "model": done.get("model"), "script_preview": (script_text or "")[:150]},
                  {"job_id": video_id, "file_id": uploaded.get("id"), "link": uploaded.get("webViewLink", "")})
    # Per-tool cost rows: the video tool, and ElevenLabs when it read the script
    _record_tool_use(client_id, done["provider"], "avatar_video", units=done.get("credits"))
    if done.get("tts_characters"):
        _record_tool_use(client_id, "elevenlabs", "tts", units=done["tts_characters"])
    usage = get_monthly_usage(client_id)
    log_step(AGENT_NAME, "generate_avatar_video",
             f"client {client_id}: {duration}s, {usage['minutes_used']}/{usage['minutes_cap']} min used")
    return {"success": True, "duration_seconds": duration,
            "link": uploaded.get("webViewLink", ""), "usage": usage}
