"""The client's voice recordings — "upload is all we ask".

The dashboard's "הקלטות קול" section sends a file (mp3 / m4a / wav / webm,
or a recording made in the browser) and that is the client's whole job.
uallak's agents decide what to do with it: use it as the audio track of an
avatar video (core/avatar_video, voice kind "recording" — no cloning, so no
cloning consent needed), or, ONLY after the explicit voice-cloning consent,
clone the voice on the client's own ElevenLabs account
(agents/avatar_agent.create_voice_clone).

Storage: the client's own Drive media folder, subfolder `voice`
(media_agent._subfolder — the same root the Media tab shows). Drive is the
ONE record: the duration and upload time are kept ON the file as
appProperties, so a list can never drift from what is really there.

Every file id that arrives from a browser is checked against THIS client's
voice folder listing before anything is done with it (`get`).

Duration: exact from a WAV header; for the other formats the browser
measures it (an <audio> element) and sends it — accepted only within
(0, MAX_SECONDS]; otherwise it is shown as unknown, never guessed.
"""
import struct
from datetime import datetime, timezone
from core import drive_storage

FOLDER = "voice"
MAX_MB = 25
MAX_FILES = 50
MAX_SECONDS = 3600
KIND = "voice_recording"

# The format comes from the file's first bytes (sniff), never its name or the
# browser's claim; Safari's in-browser recording (audio/mp4) is stored as m4a
FORMATS = {
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "wav": "audio/wav",
    "webm": "audio/webm",
}


class VoiceError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


PLAIN_HE = {
    "too_big": f"הקובץ גדול מדי — עד {MAX_MB}MB לקובץ. אפשר לקצר את ההקלטה או לשמור אותה כ-mp3.",
    "bad_format": "אפשר להעלות רק קובצי קול: mp3, m4a, wav או webm.",
    "empty": "הקובץ ריק.",
    "too_many": f"יש כבר {MAX_FILES} הקלטות. מוחקים הקלטה ישנה ואז מעלים חדשה.",
    "storage": "לא הצלחנו לשמור את הקובץ כרגע. ננסה שוב בעוד רגע.",
    "not_found": "ההקלטה לא נמצאה ברשימה שלך.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@drive_storage.scoped
def _folder(client_id: int) -> str:
    from agents.media_agent import _subfolder
    return _subfolder(client_id, FOLDER)


# ─── Validation ──────────────────────────────────────────────────────────────

def sniff(content: bytes) -> str:
    """The real format from the first bytes ('' = not a format we take)."""
    head = content[:16]
    if head[:4] == b"RIFF" and content[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if content[4:8] == b"ftyp":
        return "m4a"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    return ""


def wav_seconds(content: bytes):
    """Exact duration of a PCM WAV from its header, or None."""
    try:
        pos = 12
        rate = channels = bits = None
        while pos + 8 <= len(content):
            cid, size = content[pos:pos + 4], struct.unpack("<I", content[pos + 4:pos + 8])[0]
            if cid == b"fmt ":
                channels, rate = struct.unpack("<HI", content[pos + 10:pos + 16])
                bits = struct.unpack("<H", content[pos + 22:pos + 24])[0]
            elif cid == b"data" and rate and channels and bits:
                data_size = min(size, len(content) - pos - 8)
                return round(data_size / (rate * channels * (bits / 8)), 1)
            pos += 8 + size + (size & 1)
    except Exception:
        return None
    return None


def _clean_name(filename: str, ext: str) -> str:
    base = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    base = base.rsplit(".", 1)[0] if "." in base else base
    base = "".join(c for c in base if c.isalnum() or c in " -_()").strip()[:80]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    return f"{base or 'recording'}-{stamp}.{ext}"


def _duration(content: bytes, ext: str, hint):
    if ext == "wav":
        exact = wav_seconds(content)
        if exact:
            return exact
    try:
        value = float(hint)
    except (TypeError, ValueError):
        return None
    return round(value, 1) if 0 < value <= MAX_SECONDS else None


# ─── Upload / list / delete ──────────────────────────────────────────────────

@drive_storage.scoped
def upload(client_id: int, filename: str, content: bytes, duration_hint=None) -> dict:
    """Store one recording. Raises VoiceError(code) — PLAIN_HE[code] is the
    client's sentence."""
    from core import drive_service as drive
    if not content:
        raise VoiceError("empty")
    if len(content) > MAX_MB * 1024 * 1024:
        raise VoiceError("too_big")
    ext = sniff(content)
    if ext not in FORMATS:
        raise VoiceError("bad_format")
    if len(list_recordings(client_id)) >= MAX_FILES:
        raise VoiceError("too_many")
    seconds = _duration(content, ext, duration_hint)
    props = {"uallak_kind": KIND, "uploaded_at": _now()}
    if seconds:
        props["duration_seconds"] = seconds
    try:
        stored = drive.upload_bytes(_folder(client_id), _clean_name(filename, ext), content, FORMATS[ext],
                                    app_properties=props)
    except Exception as e:
        print(f"[voice_recordings] client {client_id}: upload failed: {type(e).__name__}")
        raise VoiceError("storage")
    return {"id": stored.get("id"), "name": stored.get("name"), "size": len(content),
            "duration_seconds": seconds, "uploaded_at": props["uploaded_at"], "format": ext}


def _row(f: dict) -> dict:
    props = f.get("appProperties") or {}
    try:
        seconds = float(props.get("duration_seconds")) if props.get("duration_seconds") else None
    except ValueError:
        seconds = None
    return {"id": f.get("id"), "name": f.get("name", ""), "mime": f.get("mimeType", ""),
            "size": int(f.get("size") or 0), "duration_seconds": seconds,
            "uploaded_at": props.get("uploaded_at") or f.get("createdTime") or ""}


@drive_storage.scoped
def list_recordings(client_id: int) -> list:
    """Newest first. Raises when Drive can't be read (callers decide)."""
    from core import drive_service as drive
    files = drive.list_files(_folder(client_id), fields="id,name,mimeType,size,createdTime,appProperties")
    rows = [_row(f) for f in files if (f.get("mimeType") or "").startswith("audio/")
            or f.get("name", "").rsplit(".", 1)[-1].lower() in FORMATS]
    return sorted(rows, key=lambda r: r["uploaded_at"], reverse=True)


def get(client_id: int, file_id: str) -> dict:
    """The recording if it is in THIS client's voice folder, else {}."""
    if not file_id:
        return {}
    try:
        return next((r for r in list_recordings(client_id) if r["id"] == file_id), {})
    except Exception:
        return {}


@drive_storage.scoped
def delete(client_id: int, file_ids: list) -> dict:
    """Trash the named recordings — only ids found in this client's folder."""
    from core import drive_service as drive
    mine = {r["id"] for r in list_recordings(client_id)}
    done, failed = [], []
    for file_id in dict.fromkeys(str(i) for i in file_ids or []):
        if file_id not in mine:
            failed.append({"id": file_id, "code": "not_found"})
            continue
        try:
            drive.trash_file(file_id)
            done.append(file_id)
        except Exception as e:
            print(f"[voice_recordings] client {client_id}: delete failed: {type(e).__name__}")
            failed.append({"id": file_id, "code": "storage"})
    return {"done": done, "failed": failed}


def for_agent(client_id: int) -> dict:
    """client_state.voice: what the agents can see and use. Raises when the
    folder is unreadable (the section is then a data gap, never a fake 0)."""
    from agents.avatar_agent import current_voice_id, has_consent
    rows = list_recordings(client_id)
    total = sum(r["duration_seconds"] or 0 for r in rows)
    return {"count": len(rows),
            "total_seconds": round(total, 1),
            "recordings": [{"file_id": r["id"], "name": r["name"], "uploaded_at": r["uploaded_at"][:10],
                            "duration_seconds": r["duration_seconds"]} for r in rows[:20]],
            "voice_clone_consent": bool(has_consent(client_id, "voice")),
            "voice_clone_exists": bool(current_voice_id(client_id)),
            "usable_as": (["audio_track", "voice_clone_source"] if has_consent(client_id, "voice")
                          else ["audio_track"])}
