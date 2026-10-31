"""ONE way for an agent to ask for "a presenter video with this audio/script".

    make_video(client_id, script="", voice={"kind": "recording", "file_id": …}
                                         | {"kind": "clone"},
               provider=None, avatar_id="")

THE PRESENTER IS HIGGSFIELD (decided 2026-10-31). HeyGen was retired: its
avatar-creation API is Enterprise-only and everything else about it sent the
client out of the product. Higgsfield takes the client's character image plus
an audio track and returns a lip-synced clip, on the same account the client
already uses for images and videos — no extra vendor signup. The model id is
named in ONE place, core/media_gen_service.PRESENTER_MODEL.

- character  the image the clip starts from (avatar_agent.create_avatar —
             one of the client's own photos, or an AI image media_agent made
             for them that they approved). Several → `avatar_id` picks one.
             A character from their own photos needs the likeness consent.
- voice      recording  a file from the client's "voice" folder
                        (core/voice_recordings), used exactly as recorded — no
                        cloning, so no voice-cloning consent is needed.
             clone      the script read in the client's ElevenLabs clone.
                        Needs ElevenLabs connected AND the voice-cloning
                        consent AND a clone.
- length     a clip is at most MAX_CLIP_SECONDS (the model's ceiling). A
             longer recording or script is REFUSED with a plain line that
             offers to split it into several clips — never cut silently.
             Nothing stitches clips together (docs/CREATIVE-TOOLS.md says
             what that would take).
- privacy    the image and the audio are public only for the vendor call
             (core/public_share: closed and verified right after).
- gate       until the presenter has run live against a real Higgsfield
             account it is open to INTERNAL test clients only
             (billing_exempt). Setting PRESENTER_VIDEO_ALL_CLIENTS=true on
             Cloud Run opens it to everyone (open_to_all) — one switch, read
             here and by the chat actions (core/agent_actions).

The caller (agents/avatar_agent.generate_avatar_video) keeps the business
gates — tier, minutes — and stores the result in Drive videos/avatar/.

RETIRED, UNREACHABLE: HeyGenProvider below and core/heygen_service stay in
the repo only so stored history reads sensibly; no client path reaches them
(tests/test_presenter_higgsfield.py fails if one does).
"""
import os
import re
from datetime import datetime, timezone

from core import drive_storage
from core import media_gen_service as mg

DEFAULT_PROVIDER = "higgsfield"
VOICE_KINDS = ("recording", "clone")
MAX_CLIP_SECONDS = mg.PRESENTER_MAX_SECONDS
# A small grace for a recording measured at 15.3 s — the model takes whole seconds
LENGTH_GRACE_SECONDS = 0.5
# Speech rate used to ESTIMATE a script's length before any audio exists
# (unhurried Hebrew/English speech, ~150 words a minute)
WORDS_PER_SECOND = 2.5
# ElevenLabs' default output (mp3_44100_128): length from the byte count
CLONE_MP3_KBPS = 128
OPEN_TO_ALL_ENV = "PRESENTER_VIDEO_ALL_CLIENTS"
CHARACTER_PROP = "uallak_character"   # own_photo | generated (avatar-source appProperties)


class VideoRefused(Exception):
    """A plain, expected refusal: `block` is what the caller returns."""

    def __init__(self, block: dict):
        super().__init__(block.get("code", "refused"))
        self.block = block


def _refuse(code: str, message_he: str, **extra) -> VideoRefused:
    return VideoRefused({"success": False, "code": code, "message_he": message_he, **extra})


# ─── The gate: internal test clients until the first live run ───────────────

def open_to_all() -> bool:
    return os.environ.get(OPEN_TO_ALL_ENV, "").strip().lower() in ("1", "true", "yes")


def _internal(client_id) -> bool:
    try:
        from agents.client_agent import get_client
        return (get_client(int(client_id)) or {}).get("billing_exempt") is True
    except Exception:
        return False   # unknown = not internal: the gate fails closed


def presenter_allowed(client_id=None) -> bool:
    return open_to_all() or (client_id is not None and _internal(client_id))


# ─── Length: say it before the click, never cut ──────────────────────────────

def estimate_seconds(script: str) -> float:
    words = len((script or "").split())
    return round(words / WORDS_PER_SECOND, 1)


def mp3_seconds(n_bytes: int, kbps: int = CLONE_MP3_KBPS) -> float:
    return round((n_bytes or 0) * 8 / (kbps * 1000), 1)


def fits(seconds) -> bool:
    return seconds is not None and float(seconds) <= MAX_CLIP_SECONDS + LENGTH_GRACE_SECONDS


def split_script(script: str, limit: int = MAX_CLIP_SECONDS) -> list:
    """The script as consecutive parts that each fit one clip — whole
    sentences where possible, words only when one sentence is too long. The
    agent offers these as separate videos; nothing is dropped."""
    max_words = max(1, int(limit * WORDS_PER_SECOND))
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", " ".join((script or "").split())) if s]
    parts, current = [], []
    for sentence in sentences:
        words = sentence.split()
        while len(words) > max_words:
            if current:
                parts.append(" ".join(current))
                current = []
            parts.append(" ".join(words[:max_words]))
            words = words[max_words:]
        if len(current) + len(words) > max_words:
            parts.append(" ".join(current))
            current = []
        current += words
    if current:
        parts.append(" ".join(current))
    return parts


def too_long_line(seconds: float, kind: str, parts: int = 0) -> str:
    """The plain Hebrew refusal for a clip over the limit (the chat card has
    its own five-language copy in core/agent_actions)."""
    if kind == "recording":
        return (f"ההקלטה הזו ארוכה כ-{round(seconds)} שניות, וסרטון מגיש הוא קליפ קצר של עד {MAX_CLIP_SECONDS} "
                f"שניות — ולא נחתוך אותה בלי לשאול. אפשר לבחור הקלטה קצרה יותר, או לכתוב את הטקסט "
                f"ולהקריא אותו בקול המשובט בכמה סרטונים קצרים.")
    return (f"הטקסט הזה הוא כ-{round(seconds)} שניות דיבור, וסרטון מגיש הוא קליפ קצר של עד {MAX_CLIP_SECONDS} "
            f"שניות — ולא נקצר אותו בשקט. אפשר לחלק אותו ל-{max(parts, 2)} סרטונים קצרים, כל אחד עד "
            f"{MAX_CLIP_SECONDS} שניות. רוצים שאכין אותם?")


# ─── Providers ───────────────────────────────────────────────────────────────

class HiggsfieldProvider:
    id = "higgsfield"
    tool = "higgsfield"
    label_he = "הדמות שלך"   # client-facing: never the vendor's name
    live_tested = False

    @staticmethod
    def enabled(client_id=None) -> bool:
        return presenter_allowed(client_id)

    @staticmethod
    def render(client_id: int, key: str, audio_url: str, seconds: float, options: dict) -> dict:
        image_url = options["image_url"]
        result = mg.generate_talking_character(key, image_url, audio_url, seconds=seconds)
        return {"video_url": result.get("url", ""), "duration": float(result.get("duration") or seconds or 0),
                "job_id": result.get("job_id", ""), "credits": result.get("credits"),
                "model": result.get("model", "")}


class HeyGenProvider:
    """RETIRED 2026-10-31 — UNREACHABLE. Not in PROVIDERS; kept so history
    rows that name it still read sensibly. Never import core/heygen_service
    from a client path again (tests/test_presenter_higgsfield.py)."""
    id = "heygen"
    tool = "heygen"
    label_he = "דמות מצולמת (הופסק)"
    retired = True

    @staticmethod
    def enabled(client_id=None) -> bool:
        return False

    @staticmethod
    def render(*args, **kwargs):
        raise RuntimeError("HeyGen presenter is retired (2026-10-31) - Higgsfield is the presenter provider")


PROVIDERS = {"higgsfield": HiggsfieldProvider}
RETIRED_PROVIDERS = {"heygen": HeyGenProvider}


def not_ready_line(provider_id: str = DEFAULT_PROVIDER) -> str:
    """The ONE plain line while the presenter is not open to this client yet
    (make_video, the connection checklist, the chat)."""
    return "סרטוני מגיש עם הדמות שלך עוד לא פתוחים אצלנו — הם בבדיקות אחרונות, ונעדכן ברגע שייפתחו."


def runnable_notice(connected_tools, client_id=None) -> dict:
    """{} when the connected presenter tool can really make a video for this
    client (or none is connected — nothing to warn about); otherwise the
    notice the checklist and the agents show instead of a silent "done"."""
    linked = [p for p in PROVIDERS.values() if p.tool in set(connected_tools or ())]
    if not linked or any(p.enabled(client_id) for p in linked):
        return {}
    return {"code": "avatar_video_not_available_yet", "provider": linked[0].id,
            "message_he": not_ready_line(linked[0].id)}


def providers_view(client_id: int) -> list:
    """For client_state and the dashboard: the presenter provider, whether it
    is connected and whether it can run for this client yet."""
    from core import vendor_tools
    return [{"id": p.id, "label_he": p.label_he, "tool": p.tool, "chosen": True,
             "ready": p.enabled(client_id), "connected": vendor_tools.is_connected(client_id, p.tool),
             "max_seconds": MAX_CLIP_SECONDS}
            for p in PROVIDERS.values()]


# ─── Character image ─────────────────────────────────────────────────────────

@drive_storage.scoped
def _character_image(client_id: int, avatar_id: str = "") -> tuple:
    """(Drive file id, kind) of the character this clip starts from — one the
    client made (avatar_agent.create_avatar) and that is still in their
    avatar-source folder. Resolved BEFORE anything is made public."""
    from agents.avatar_agent import _source_files, list_ready_avatars
    ready = list_ready_avatars(client_id)
    if avatar_id:
        chosen = next((a for a in ready if a["avatar_id"] == avatar_id), None)
        if not chosen:
            raise _refuse("no_character", "הדמות שנבחרה לא נמצאת אצלך. אפשר לבחור דמות אחרת מהרשימה.")
    elif len(ready) > 1:
        raise _refuse("choose_avatar", "יש לך כמה דמויות מוכנות — צריך לבחור באיזו להשתמש.",
                      choices=[{"avatar_id": a["avatar_id"], "name": a["avatar_name"]} for a in ready])
    elif not ready:
        raise _refuse("no_character", "עוד אין לך דמות לסרטונים. שולחים לי בצ'אט תמונה ברורה שלך (או תמונה "
                                      "שיצרנו לך ואהבת), ואכין ממנה את הדמות.")
    else:
        chosen = ready[0]
    if chosen["avatar_id"] not in {f.get("id") for f in _source_files(client_id)}:
        raise _refuse("no_character", "התמונה של הדמות כבר לא נמצאת בתיקייה שלך. שולחים לי תמונה חדשה בצ'אט, "
                                      "ואכין ממנה דמות.")
    return chosen["avatar_id"], chosen.get("kind") or "own_photo"


def _likeness_gate(client_id: int, kind: str):
    """A character made from the client's own photos needs the likeness
    consent; an AI-generated character does not."""
    if kind == "generated":
        return
    from agents.avatar_agent import has_consent
    from core import vendor_tools
    if not has_consent(client_id, "likeness"):
        raise _refuse("no_likeness_consent",
                      "לפני שיוצרים סרטון עם דמות מהתמונות שלך צריך את האישור שלך לשימוש בדמות — הוא בכרטיס "
                      f"\"{vendor_tools.client_label('higgsfield')}\".",
                      buttons=[{"tool": "higgsfield", "label": f"לכרטיס \"{vendor_tools.client_label('higgsfield')}\"",
                                "focus": vendor_tools.focus_id("higgsfield")}])


# ─── Voice → audio URL ───────────────────────────────────────────────────────

def recording_seconds(client_id: int, file_id: str):
    """The recording's length (None when it was never measured), or raises
    recording_not_found."""
    from core import voice_recordings
    recording = voice_recordings.get(client_id, file_id)
    if not recording:
        raise _refuse("recording_not_found", "ההקלטה הזו לא נמצאת בתיקיית ההקלטות שלך. אפשר לבחור הקלטה אחרת מהרשימה.")
    return recording.get("duration_seconds")


def _check_recording_length(seconds):
    if seconds is None:
        raise _refuse("length_unknown",
                      f"לא הצלחנו לדעת את אורך ההקלטה הזו, ולכן לא נשלח אותה — סרטון מגיש הוא עד {MAX_CLIP_SECONDS} "
                      "שניות, ואנחנו לא חותכים בשקט. אפשר להעלות אותה שוב בכרטיס \"הקלטות קול\" או לבחור הקלטה אחרת.")
    if not fits(seconds):
        raise _refuse("too_long", too_long_line(seconds, "recording"), seconds=seconds)


@drive_storage.scoped
def _audio_from_recording(client_id: int, file_id: str, share) -> tuple:
    """(audio_url, seconds)."""
    seconds = recording_seconds(client_id, file_id)
    _check_recording_length(seconds)
    return share.url(file_id), float(seconds)


@drive_storage.scoped
def _audio_from_clone(client_id: int, script: str, share) -> tuple:
    """(audio_url, characters, seconds). ElevenLabs errors → plain Hebrew."""
    from agents.avatar_agent import current_voice_id, has_consent
    from agents.media_agent import _subfolder
    from core import drive_service as drive
    from core import elevenlabs_service as el
    from core import vendor_tools
    from core.secrets_vault import use_vendor_key
    if not has_consent(client_id, "voice"):
        raise _refuse("no_voice_consent", "כדי להשתמש בקול משובט צריך קודם לאשר את הסכמת שיבוט הקול בכרטיס \"הקול שלך\".",
                      buttons=[{"tool": "elevenlabs", "label": "לכרטיס \"הקול שלך\"",
                                "focus": vendor_tools.focus_id("elevenlabs")}])
    voice_id = current_voice_id(client_id)
    if not voice_id:
        raise _refuse("no_clone", "עוד אין שיבוט של הקול שלך. ברגע שתהיה הקלטה מתאימה, הצוות ישבט את הקול.")
    if not (script or "").strip():
        raise _refuse("no_script", "לקול המשובט צריך טקסט להקראה.")
    key = use_vendor_key(client_id, "elevenlabs", job="avatar_video:tts")
    if not key:
        raise VideoRefused(vendor_tools.missing_block(["elevenlabs"]))
    try:
        audio = el.text_to_speech(key, voice_id, script)
    except el.ElevenLabsError as e:
        if e.key_rejected:
            vendor_tools.mark_rejected(client_id, "elevenlabs")
        raise _refuse("elevenlabs_error", el.plain_he(e))
    seconds = mp3_seconds(len(audio))
    if not fits(seconds):
        # The estimate let it through, the real reading did not: still never cut
        raise _refuse("too_long", too_long_line(seconds, "clone", len(split_script(script))), seconds=seconds,
                      parts=split_script(script))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    uploaded = drive.upload_bytes(_subfolder(client_id, "videos", "avatar"), f"voiceover-{stamp}.mp3",
                                  audio, "audio/mpeg")
    return share.url(uploaded["id"]), len(script), seconds


# ─── The one entry point ─────────────────────────────────────────────────────

def make_video(client_id: int, script: str = "", voice: dict = None, provider: str = None,
               avatar_id: str = "") -> dict:
    """{"success": True, "provider", "video_url", "duration", "job_id",
    "voice_kind", "tts_characters", "character_kind"} or a refusal block
    (success False, code, message_he, optional buttons). Never raises for an
    expected refusal."""
    from core import vendor_tools
    from core.secrets_vault import use_vendor_key
    provider_id = provider or DEFAULT_PROVIDER
    if provider_id in RETIRED_PROVIDERS:
        provider_id = DEFAULT_PROVIDER   # an old stored choice: the one presenter there is
    spec = PROVIDERS.get(provider_id)
    if not spec:
        return {"success": False, "code": "unknown_provider", "message_he": "כלי הווידאו שנבחר לא מוכר."}
    if not spec.enabled(client_id):
        return {"success": False, "code": "provider_not_ready", "message_he": not_ready_line(spec.id)}
    voice = voice or {}
    kind = voice.get("kind") or ""
    if kind not in VOICE_KINDS:
        return {"success": False, "code": "no_voice",
                "message_he": "סרטון מגיש צריך קול: הקלטה שהעלית או הקול המשובט שלך."}
    try:
        # Length first, before any vendor is touched or anything is opened
        if kind == "recording":
            _check_recording_length(recording_seconds(client_id, str(voice.get("file_id") or "")))
        else:
            estimate = estimate_seconds(script)
            if not fits(estimate):
                parts = split_script(script)
                raise _refuse("too_long", too_long_line(estimate, "clone", len(parts)), seconds=estimate,
                              parts=parts)
        # Then whose face, and the consent it needs — still nothing public
        character_id, character_kind = _character_image(client_id, avatar_id)
        _likeness_gate(client_id, character_kind)
    except VideoRefused as e:
        return e.block
    needed = [spec.tool] + (["elevenlabs"] if kind == "clone" else [])
    missing = vendor_tools.missing(client_id, needed)
    if missing:
        return vendor_tools.missing_block(missing)
    key = use_vendor_key(client_id, spec.tool, job=f"avatar_video:{spec.id}")
    if not key:
        return vendor_tools.missing_block([spec.tool])
    from core.public_share import briefly_public
    try:
        # The client's face and voice are public only while the render runs
        # (render waits for the finished clip), closed and verified right after
        with briefly_public(client_id, "avatar_video", f"{spec.id} presenter video") as share:
            image_url = share.url(character_id)
            characters = 0
            if kind == "recording":
                audio_url, seconds = _audio_from_recording(client_id, str(voice.get("file_id") or ""), share)
            else:
                audio_url, characters, seconds = _audio_from_clone(client_id, script, share)
            result = spec.render(client_id, key, audio_url, seconds, {"image_url": image_url})
    except VideoRefused as e:
        return e.block
    return {"success": True, "provider": spec.id, "voice_kind": kind, "tts_characters": characters,
            "character_kind": character_kind, "avatar_id": character_id, **result}
