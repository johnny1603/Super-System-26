"""ONE way for an agent to ask for "an avatar video with this audio/script",
whichever video tool the client chose.

    make_video(client_id, script="", voice={"kind": "recording", "file_id": …}
                                         | {"kind": "clone"} | None,
               provider=None, avatar_id="", heygen_voice_id="")

- provider: per video (`provider=`) > the client's default
  (vendor_tools.default_provider) > DEFAULT_PROVIDER.
- voice — the SAME sources for either provider:
    recording  a file from the client's "voice" folder (core/voice_recordings),
               used as the audio track exactly as recorded. No cloning, so no
               voice-cloning consent is needed.
    clone      the script read in the client's ElevenLabs clone. Needs
               ElevenLabs connected AND the voice-cloning consent AND a clone.
    None       HeyGen only: one of HeyGen's own stock voices (heygen_voice_id).
- A provider (or ElevenLabs) that is not connected → vendor_tools
  .missing_block: plain Hebrew naming exactly which tool, and a button that
  opens only that tool's card.

The caller (agents/avatar_agent.generate_avatar_video) keeps the business
gates — likeness consent, tier, minutes — and stores the result in Drive.

WHAT HAS RUN (2026-10-30): nothing here has been run against a live HeyGen,
Higgsfield or ElevenLabs account. HeyGen's generate/poll calls and
ElevenLabs' clone/TTS calls were written against the vendors' public docs
(core/heygen_service, core/elevenlabs_service). The Higgsfield talking-
character call is PREPARED ONLY: the endpoint and model are env settings
whose values were not verified, so the provider stays switched off
(HIGGSFIELD_AVATAR_VIDEO_ENABLED) and answers "not ready yet" until a live
test on client 27 confirms them — see docs/CREATIVE-TOOLS.md.
"""
import os
from datetime import datetime, timezone

DEFAULT_PROVIDER = "heygen"
VOICE_KINDS = ("recording", "clone")


class VideoRefused(Exception):
    """A plain, expected refusal: `block` is what the caller returns."""

    def __init__(self, block: dict):
        super().__init__(block.get("code", "refused"))
        self.block = block


def _refuse(code: str, message_he: str, **extra) -> VideoRefused:
    return VideoRefused({"success": False, "code": code, "message_he": message_he, **extra})


# ─── Providers ───────────────────────────────────────────────────────────────

class HeyGenProvider:
    id = "heygen"
    tool = "heygen"
    label_he = "HeyGen — אווטאר בדמות שלך"
    live_tested = False

    @staticmethod
    def enabled() -> bool:
        return True

    @staticmethod
    def render(client_id: int, key: str, audio_url: str, script: str, options: dict) -> dict:
        from agents.avatar_agent import list_ready_avatars
        from core import heygen_service as hg
        avatar_id = options.get("avatar_id") or ""
        if not avatar_id:
            ready = list_ready_avatars(client_id)
            if len(ready) > 1:
                raise _refuse("choose_avatar", "יש לך כמה אווטארים מוכנים — צריך לבחור באיזה להשתמש.",
                              choices=[{"avatar_id": a["avatar_id"], "name": a["avatar_name"]} for a in ready])
            if not ready:
                raise _refuse("no_avatar", "עוד אין אווטאר מוכן בחשבון ה-HeyGen שלך. כשהוא יסיים להתאמן נעדכן אותך.")
            avatar_id = ready[0]["avatar_id"]
        if not audio_url and not options.get("heygen_voice_id"):
            raise _refuse("no_voice", "צריך לבחור קול לסרטון: הקלטה שהעלית, הקול המשובט שלך, או קול מוכן של HeyGen.")
        video_id = hg.generate_avatar_video(key, avatar_id, script, voice_id=options.get("heygen_voice_id", ""),
                                            audio_url=audio_url)
        done = hg.wait_for_video(key, video_id)
        return {"video_url": done.get("video_url", ""), "duration": float(done.get("duration") or 0),
                "job_id": video_id, "avatar_id": avatar_id}


class HiggsfieldProvider:
    id = "higgsfield"
    tool = "higgsfield"
    label_he = "Higgsfield — דמות / משפיען AI"
    live_tested = False

    @staticmethod
    def enabled() -> bool:
        # Off until the talking-character endpoint is confirmed on a live key
        return os.environ.get("HIGGSFIELD_AVATAR_VIDEO_ENABLED", "").strip().lower() in ("1", "true", "yes")

    @staticmethod
    def render(client_id: int, key: str, audio_url: str, script: str, options: dict) -> dict:
        from core import media_gen_service as mg
        if not audio_url:
            raise _refuse("no_voice", "סרטון דמות ב-Higgsfield צריך קול: הקלטה שהעלית או הקול המשובט שלך.")
        image_url = options.get("image_url") or _character_image(client_id)
        if not image_url:
            raise _refuse("no_character", "כדי ליצור סרטון דמות צריך תמונה של הדמות. מעלים תמונה לתיקיית avatar-source ב-Drive.")
        result = mg.generate_talking_character(key, image_url, audio_url)
        return {"video_url": result.get("url", ""), "duration": float(result.get("duration") or 0),
                "job_id": result.get("job_id", ""), "credits": result.get("credits")}


def _character_image(client_id: int) -> str:
    """The newest image the client put in avatar-source (their own material)."""
    from agents.avatar_agent import _source_files
    from core import drive_service as drive
    images = [f for f in _source_files(client_id) if (f.get("mimeType") or "").startswith("image/")]
    return drive.make_file_public(images[0]["id"]) if images else ""


PROVIDERS = {"heygen": HeyGenProvider, "higgsfield": HiggsfieldProvider}


def providers_view(client_id: int) -> list:
    """For the dashboard choice and client_state: every provider, whether it
    is connected and whether it can run at all yet."""
    from core import vendor_tools
    chosen = vendor_tools.default_provider(client_id)
    return [{"id": p.id, "label_he": p.label_he, "tool": p.tool, "chosen": p.id == chosen,
             "ready": p.enabled(), "connected": vendor_tools.is_connected(client_id, p.tool)}
            for p in PROVIDERS.values()]


# ─── Voice → audio URL ───────────────────────────────────────────────────────

def _audio_from_recording(client_id: int, file_id: str) -> str:
    from core import drive_service as drive
    from core import voice_recordings
    if not voice_recordings.get(client_id, file_id):
        raise _refuse("recording_not_found", "ההקלטה הזו לא נמצאת בתיקיית ההקלטות שלך. אפשר לבחור הקלטה אחרת מהרשימה.")
    return drive.make_file_public(file_id)


def _audio_from_clone(client_id: int, script: str) -> tuple:
    """(audio_url, characters). ElevenLabs errors → plain Hebrew."""
    from agents.avatar_agent import current_voice_id, has_consent
    from agents.media_agent import _subfolder
    from core import drive_service as drive
    from core import elevenlabs_service as el
    from core import vendor_tools
    from core.secrets_vault import use_vendor_key
    if not has_consent(client_id, "voice"):
        raise _refuse("no_voice_consent", "כדי להשתמש בקול משובט צריך קודם לאשר את הסכמת שיבוט הקול בכרטיס ElevenLabs.",
                      buttons=[{"tool": "elevenlabs", "label": "לכרטיס ElevenLabs",
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
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    uploaded = drive.upload_bytes(_subfolder(client_id, "videos", "avatar"), f"voiceover-{stamp}.mp3",
                                  audio, "audio/mpeg")
    return drive.make_file_public(uploaded["id"]), len(script)


# ─── The one entry point ─────────────────────────────────────────────────────

def make_video(client_id: int, script: str = "", voice: dict = None, provider: str = None,
               avatar_id: str = "", heygen_voice_id: str = "") -> dict:
    """{"success": True, "provider", "video_url", "duration", "job_id",
    "voice_kind", "tts_characters"} or a refusal block (success False, code,
    message_he, optional buttons). Never raises for an expected refusal."""
    from core import vendor_tools
    from core.secrets_vault import use_vendor_key
    provider_id = provider or vendor_tools.default_provider(client_id)
    spec = PROVIDERS.get(provider_id)
    if not spec:
        return {"success": False, "code": "unknown_provider", "message_he": "כלי הווידאו שנבחר לא מוכר."}
    if not spec.enabled():
        return {"success": False, "code": "provider_not_ready",
                "message_he": f"יצירת סרטון דמות ב-{vendor_tools.TOOLS[spec.tool]['label']} עוד לא הופעלה אצלנו — "
                              f"היא תיפתח אחרי בדיקה חיה. בינתיים אפשר להשתמש ב-HeyGen."}
    voice = voice or {}
    kind = voice.get("kind") or ""
    if kind and kind not in VOICE_KINDS:
        return {"success": False, "code": "bad_voice", "message_he": "מקור הקול לא מוכר."}
    needed = [spec.tool] + (["elevenlabs"] if kind == "clone" else [])
    missing = vendor_tools.missing(client_id, needed)
    if missing:
        return vendor_tools.missing_block(missing)
    key = use_vendor_key(client_id, spec.tool, job=f"avatar_video:{spec.id}")
    if not key:
        return vendor_tools.missing_block([spec.tool])
    try:
        characters = 0
        if kind == "recording":
            audio_url = _audio_from_recording(client_id, str(voice.get("file_id") or ""))
        elif kind == "clone":
            audio_url, characters = _audio_from_clone(client_id, script)
        else:
            audio_url = ""
        result = spec.render(client_id, key, audio_url, script,
                             {"avatar_id": avatar_id, "heygen_voice_id": heygen_voice_id})
    except VideoRefused as e:
        return e.block
    return {"success": True, "provider": spec.id, "voice_kind": kind or "stock", "tts_characters": characters,
            **result}
