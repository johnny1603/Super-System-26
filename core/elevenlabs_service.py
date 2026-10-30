"""Low-level ElevenLabs plumbing — professional VOICE CLONING of a real
person's voice. HTTP only; consent enforcement and business logic live in
agents/avatar_agent.py (a voice is someone's likeness exactly like a face —
the same mandatory-consent rigor applies).

Billing: the CLIENT'S own ElevenLabs account and API key (client_accounts,
platform='elevenlabs') — their plan, their card; we only operate. Instant
Voice Cloning (v1/voices/add) is available on their standard paid plans.

Errors: every failure raises ElevenLabsError(status, code) — `code` is
ElevenLabs' own `detail.status` when it sends one (invalid_api_key,
quota_exceeded, missing_permissions, voice_limit_reached, …). `plain_he()`
turns it into one plain Hebrew sentence for the client. Neither the key nor
the response body is ever put in a message or a log.

VERIFICATION STATUS: written against the public docs (this API has been
stable for years), never run with a live key. The first live test is the
internal client 27 path (docs/CREATIVE-TOOLS.md).
"""
import os

import httpx

API_BASE = os.environ.get("ELEVENLABS_API_BASE", "https://api.elevenlabs.io")
TIMEOUT = 120


class ElevenLabsError(RuntimeError):
    def __init__(self, status: int, code: str = "", what: str = ""):
        super().__init__(f"elevenlabs {what} failed: {status} {code}".strip())
        self.status, self.code, self.what = int(status or 0), code or "", what

    @property
    def key_rejected(self) -> bool:
        return self.status == 401 and self.code in ("", "invalid_api_key", "unauthorized")


def _error(response: httpx.Response, what: str) -> ElevenLabsError:
    code = ""
    try:
        detail = (response.json() or {}).get("detail")
        if isinstance(detail, dict):
            code = str(detail.get("status") or detail.get("code") or "")[:60]
    except Exception:
        pass
    return ElevenLabsError(response.status_code, code, what)


_PLAIN_HE = {
    "invalid_api_key": "ElevenLabs לא מכיר את המפתח שהודבק. צריך ליצור מפתח חדש בחשבון ה-ElevenLabs ולהדביק אותו בכרטיס ElevenLabs.",
    "quota_exceeded": "נגמרה המכסה החודשית בחשבון ה-ElevenLabs שלך. אפשר להגדיל את המסלול אצלם או לחכות לחודש הבא.",
    "missing_permissions": "למפתח ה-ElevenLabs חסרה הרשאה לפעולה הזו. ביצירת המפתח אצלם צריך לאפשר גישה לקולות (Voices) ולהקראה (Text to Speech).",
    "voice_limit_reached": "הגעתם למספר הקולות המרבי בחשבון ה-ElevenLabs. מוחקים שם קול ישן שלא בשימוש, ואז מנסים שוב.",
    "can_not_use_instant_voice_cloning": "שיבוט קול זמין רק במסלול בתשלום של ElevenLabs. משדרגים את המסלול אצלם, ואז מנסים שוב.",
    "too_many_concurrent_requests": "ElevenLabs עמוס כרגע בחשבון שלך. ננסה שוב בעוד כמה דקות.",
    "voice_not_found": "הקול המשובט כבר לא קיים בחשבון ה-ElevenLabs שלך. אפשר לשבט אותו מחדש מההקלטות.",
}


def plain_he(error: Exception) -> str:
    """One plain Hebrew sentence for the client — never a code or a body."""
    if not isinstance(error, ElevenLabsError):
        return "לא הצלחנו להגיע ל-ElevenLabs כרגע. ננסה שוב בקרוב."
    if error.code in _PLAIN_HE:
        return _PLAIN_HE[error.code]
    if error.key_rejected:
        return _PLAIN_HE["invalid_api_key"]
    if error.status == 404:
        return _PLAIN_HE["voice_not_found"]
    if error.status in (400, 422):
        return "ElevenLabs לא הצליח לעבד את ההקלטה. כדאי להעלות הקלטה נקייה של דיבור בלבד, בלי מוזיקה, באורך של דקה לפחות."
    if error.status == 429:
        return _PLAIN_HE["too_many_concurrent_requests"]
    if error.status >= 500:
        return "ל-ElevenLabs יש תקלה זמנית אצלם. ננסה שוב בקרוב."
    return "ElevenLabs סירב לבקשה. אפשר לבדוק את החיבור בכרטיס ElevenLabs."


def _headers(api_key: str) -> dict:
    return {"xi-api-key": api_key}


def list_voices(api_key: str) -> list:
    response = httpx.get(f"{API_BASE}/v1/voices", headers=_headers(api_key), timeout=TIMEOUT)
    if response.status_code != 200:
        raise _error(response, "list_voices")
    return response.json().get("voices") or []


def create_voice_clone(api_key: str, name: str, samples: list, description: str = "") -> str:
    """Instant Voice Clone from audio samples. samples: list of
    (filename, bytes, mime_type) tuples — 1-3 clean minutes of speech total
    is the sweet spot. Returns the new voice_id."""
    files = [("files", (fname, content, mime)) for fname, content, mime in samples]
    response = httpx.post(
        f"{API_BASE}/v1/voices/add",
        headers=_headers(api_key),
        data={"name": name, "description": description or "uallak client voice"},
        files=files,
        timeout=300,  # uploads several audio files
    )
    if response.status_code != 200:
        raise _error(response, "voice_clone")
    voice_id = response.json().get("voice_id")
    if not voice_id:
        raise ElevenLabsError(response.status_code, "no_voice_id", "voice_clone")
    return voice_id


def delete_voice(api_key: str, voice_id: str) -> bool:
    """Deletes the clone from the client's ElevenLabs account. True also when
    it is already gone (404) — the outcome the client asked for."""
    response = httpx.delete(f"{API_BASE}/v1/voices/{voice_id}", headers=_headers(api_key), timeout=TIMEOUT)
    if response.status_code in (200, 204, 404):
        return True
    raise _error(response, "delete_voice")


def text_to_speech(api_key: str, voice_id: str, text: str,
                   model_id: str = "eleven_multilingual_v2") -> bytes:
    """Speech audio (MP3 bytes) in the cloned voice — Hebrew works on the
    multilingual model. Feeds the avatar video providers' audio_url path after
    a Drive upload + make_file_public."""
    response = httpx.post(
        f"{API_BASE}/v1/text-to-speech/{voice_id}",
        headers={**_headers(api_key), "Content-Type": "application/json"},
        json={"text": text, "model_id": model_id},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise _error(response, "tts")
    return response.content
