"""Upload from the chat — the "+" button and drag-and-drop on a chat window.

## The path

The browser cuts the file into fixed chunks of video_limits.UPLOAD_CHUNK_BYTES
(8 MiB) and sends them one request at a time to POST /api/client/chat-upload.
Each request forwards its ONE chunk into a Drive resumable session
(drive_service.start_resumable / put_chunk), so:

- memory never grows with the file (one chunk at a time, like the
  Drive → YouTube streaming path in core/video_limits);
- no request comes near Cloud Run's ~300 s timeout, whatever the file's size;
- an interrupted upload resumes: the browser asks /chat-upload/status, Drive
  answers how many bytes it holds, and the upload continues from there.

There is NO size limit of ours. The only ceiling is Google Drive's own
per-file maximum (DRIVE_MAX_BYTES). Limits that are REAL for a destination —
YouTube (video_limits.VIDEO_UPLOAD_MAX_MB), TikTok drafts
(video_limits.TIKTOK_INBOX_MAX_MB), voice tools (voice_recordings.MAX_MB) —
are explained after the upload, next to the action they block; the file is
stored either way.

## Where the file lands

The client's media folder (media_agent._subfolder — the folder the Media tab
mirrors and that is shared read-only with the client's email; it lives in
uallak's Shared Drive), in a FIRST-LEVEL subfolder by the file's real type
(read from its first bytes, never its name): videos / images / voice (the
folder core/voice_recordings lists) / documents. First level on purpose:
client_state reads first-level subfolders, so a new video is offered to the
agents right away.

## Session token

Drive's session URI is a capability (whoever holds it can write into the
session), so it never reaches the browser in the clear: the browser gets an
AES-GCM SEALED token (key derived from SESSION_SECRET_KEY, associated data =
the client id) that carries the URI, the folder, the size and the type. A
token from another client does not open; nothing is stored server-side, so
any Cloud Run instance can take the next chunk.

## Safety

- The type is sniffed from the bytes; executables, archives, macro-enabled
  Office files and unknown types are refused with ONE plain Hebrew sentence
  (PLAIN_HE) and an admin alert (chat_fallback.alert_once) — never a raw
  error. There is no antivirus engine here: Drive itself scans files when they
  are downloaded; we refuse what can run code.
- A file name that looks like a credential (core/redaction) is never stored:
  the file gets a generic name instead.
- Nothing is published or sent: after the upload the agent PROPOSES
  (core/agent_actions cards) and only the client's click runs anything.

## NOT here, deliberately (a later verification round)

Picking files FROM the client's own Google Drive or Google Photos needs new
Google OAuth scopes (Drive picker / Photos Picker API). No scope is added by
this module or the upload route — that is a separate, later Google
verification round (docs/CHAT-UPLOADS.md). Do not add it by accident here.
"""
import base64
import hashlib
import json
import os
import time
import unicodedata
from datetime import datetime, timezone
from core import drive_storage

SERVICE_NAME = "chat_uploads"
DRIVE_MAX_BYTES = 5 * 1024 ** 4      # Google Drive's own per-file maximum (5 TB) — not ours
TOKEN_SECONDS = 3 * 24 * 3600        # Drive keeps a resumable session about a week
KIND_TAG = "chat_upload"
NAME_MAX = 100
ALERT_DAYS = 1

# Real type → the client's first-level media subfolder
FOLDERS = {"video": "videos", "image": "images", "audio": "voice", "document": "documents"}
FOLDER_HE = {"video": "סרטונים", "image": "תמונות", "audio": "הקלטות קול", "document": "מסמכים"}
GENERIC_NAME = {"video": "video", "image": "image", "audio": "recording", "document": "document"}

PLAIN_HE = {
    "empty": "הקובץ ריק — אין מה להעלות.",
    "unsupported": "את סוג הקובץ הזה אי אפשר להעלות כאן. אפשר להעלות סרטונים, תמונות, הקלטות קול, PDF ומסמכי Office.",
    "archive": "קובצי ZIP / RAR לא נפתחים כאן — מעלים את הקבצים עצמם (סרטון, תמונה או מסמך).",
    "unsafe": "הקובץ נחסם כי הוא יכול להריץ קוד (תוכנה, סקריפט או מסמך עם מאקרו). אפשר לשמור אותו בלי מאקרו ולהעלות שוב.",
    "too_big": "הקובץ גדול יותר ממה ש-Google Drive עצמו מאפשר לקובץ אחד (5TB), ולכן אי אפשר לשמור אותו.",
    "expired": "ההעלאה נקטעה לזמן ארוך מדי — צריך להתחיל אותה מחדש.",
    "bad_chunk": "משהו השתבש באמצע ההעלאה. צריך להתחיל אותה מחדש.",
    "not_configured": "תיקיית המדיה שלך עוד לא מוכנה, ולכן אי אפשר להעלות כרגע. הצוות קיבל התראה.",
    "storage": "לא הצלחנו לשמור את הקובץ כרגע. הצוות קיבל התראה, ואפשר לנסות שוב בעוד רגע.",
}


class UploadRefused(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _alert(client_id: int, code: str, message: str):
    print(f"[{SERVICE_NAME}] client {client_id}: {message}")
    try:
        from core.chat_fallback import alert_once
        alert_once(SERVICE_NAME, client_id, f"{SERVICE_NAME}:{code}", f"client {client_id}: {message}", ALERT_DAYS)
    except Exception:
        pass


def refuse(client_id: int, code: str, detail: str = "") -> UploadRefused:
    """The refusal the client reads (PLAIN_HE[code]) + the alert the team reads."""
    from core.redaction import redact
    _alert(client_id, code, f"chat upload refused ({code}){': ' + redact(detail)[:300] if detail else ''}")
    return UploadRefused(code, detail)


# ─── The real type, from the first bytes ─────────────────────────────────────

_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1",
                b"avif", b"avis"}
_AUDIO_MP4_BRANDS = {b"M4A ", b"M4B ", b"M4P ", b"F4A "}
_RAW_IMAGE_EXTS = {"dng", "cr2", "nef", "arw", "orf", "rw2", "tif", "tiff"}
_TEXT_EXTS = {"txt": "text/plain", "csv": "text/csv", "tsv": "text/tab-separated-values", "md": "text/markdown",
              "srt": "text/plain", "vtt": "text/vtt"}
_OLE_TYPES = {"doc": "application/msword", "xls": "application/vnd.ms-excel",
              "ppt": "application/vnd.ms-powerpoint", "msg": "application/vnd.ms-outlook"}
_OOXML = {b"word/": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
          b"xl/": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
          b"ppt/": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation")}
_ODF = {b"opendocument.text": ("odt", "application/vnd.oasis.opendocument.text"),
        b"opendocument.spreadsheet": ("ods", "application/vnd.oasis.opendocument.spreadsheet"),
        b"opendocument.presentation": ("odp", "application/vnd.oasis.opendocument.presentation")}
_EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe",
                     b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"#!", b"dex\n")
_ARCHIVE_MAGIC = (b"Rar!", b"7z\xbc\xaf", b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00", b"MSCF", b"xar!")


def _ext(name: str) -> str:
    base = (name or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def _is_text(head: bytes) -> bool:
    sample = head[:4096]
    if not sample or b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as e:
        return e.start >= len(sample) - 3   # only a character cut at the sample's end
    return True


def sniff(head: bytes, name: str = "", hint_mime: str = "") -> tuple:
    """(kind, ext, mime) of a file from its first bytes (the first chunk).
    kind ∈ FOLDERS. The name and the browser's type are HINTS, used only to
    tell apart containers that hold either sound or picture (MP4 / WebM /
    Ogg). Raises UploadRefused("unsafe" | "archive" | "unsupported" | "empty")."""
    if not head:
        raise UploadRefused("empty")
    ext = _ext(name)
    hint = (hint_mime or "").lower()
    wants_audio = hint.startswith("audio/") or ext in ("m4a", "aac", "weba", "opus", "oga", "ogg")

    if any(head.startswith(m) for m in _EXECUTABLE_MAGIC):
        raise UploadRefused("unsafe", "executable or script")
    if any(head.startswith(m) for m in _ARCHIVE_MAGIC):
        raise UploadRefused("archive", "archive")

    # ISO base media (MP4 / MOV / 3GP / M4A / HEIC / AVIF)
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        brands = {head[i:i + 4] for i in range(16, min(len(head), 64), 4)} | {brand}
        if brands & _HEIF_BRANDS and not brands & {b"isom", b"mp41", b"mp42", b"qt  "}:
            if brands & {b"avif", b"avis"}:
                return "image", "avif", "image/avif"
            return ("image", "heic", "image/heic") if brand[:3] in (b"hei", b"hev") else ("image", "heif", "image/heif")
        if brand in _AUDIO_MP4_BRANDS:
            return "audio", "m4a", "audio/mp4"
        if brand == b"qt  ":
            return "video", "mov", "video/quicktime"
        if wants_audio:
            return "audio", "m4a", "audio/mp4"
        if brand.startswith(b"3g"):
            return "video", "3gp", "video/3gpp"
        return "video", "mp4", "video/mp4"
    # Matroska / WebM
    if head.startswith(b"\x1aE\xdf\xa3"):
        if wants_audio:
            return "audio", "webm", "audio/webm"
        if b"webm" in head[:64]:
            return "video", "webm", "video/webm"
        return "video", "mkv", "video/x-matroska"
    if head.startswith(b"RIFF"):
        form = head[8:12]
        if form == b"WAVE":
            return "audio", "wav", "audio/wav"
        if form == b"AVI ":
            return "video", "avi", "video/x-msvideo"
        if form == b"WEBP":
            return "image", "webp", "image/webp"
        raise UploadRefused("unsupported", f"RIFF {form!r}")
    if head.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"):
        return "video", "wmv", "video/x-ms-wmv"
    if head.startswith(b"FLV"):
        return "video", "flv", "video/x-flv"
    if head.startswith(b"\x00\x00\x01\xba") or head.startswith(b"\x00\x00\x01\xb3"):
        return "video", "mpg", "video/mpeg"
    if head[:1] == b"\x47" and len(head) > 376 and head[188:189] == b"\x47" and head[376:377] == b"\x47":
        return "video", "ts", "video/mp2t"
    if head.startswith(b"OggS"):
        if hint.startswith("video/") or ext == "ogv":
            return "video", "ogv", "video/ogg"
        return "audio", ("opus" if b"OpusHead" in head[:128] else "ogg"), "audio/ogg"

    # Images
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "jpg", "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "png", "image/png"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image", "gif", "image/gif"
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return "image", (ext if ext in _RAW_IMAGE_EXTS else "tif"), "image/tiff"
    if head.startswith(b"BM") and (ext == "bmp" or hint == "image/bmp"):
        return "image", "bmp", "image/bmp"

    # Audio
    if head.startswith(b"ID3"):
        return "audio", "mp3", "audio/mpeg"
    if len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        if (head[1] & 0xF6) == 0xF0:
            return "audio", "aac", "audio/aac"
        return "audio", "mp3", "audio/mpeg"
    if head.startswith(b"fLaC"):
        return "audio", "flac", "audio/flac"
    if head.startswith(b"#!AMR"):
        return "audio", "amr", "audio/amr"
    if head.startswith(b"caff"):
        return "audio", "caf", "audio/x-caf"
    if head.startswith(b"FORM") and head[8:12] in (b"AIFF", b"AIFC"):
        return "audio", "aiff", "audio/aiff"

    # Documents
    if head.startswith(b"%PDF"):
        return "document", "pdf", "application/pdf"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        if "_VBA_PROJECT".encode("utf-16-le") in head or "Macros".encode("utf-16-le") in head:
            raise UploadRefused("unsafe", "legacy Office file with macros")
        kind_ext = ext if ext in _OLE_TYPES else "doc"
        return "document", kind_ext, _OLE_TYPES[kind_ext]
    if head.startswith(b"PK\x03\x04"):
        if b"[Content_Types].xml" in head:
            if b"vbaProject.bin" in head:
                raise UploadRefused("unsafe", "Office file with macros")
            for marker, (doc_ext, mime) in _OOXML.items():
                if marker in head:
                    return "document", doc_ext, mime
        if head[30:38] == b"mimetype":
            for marker, (doc_ext, mime) in _ODF.items():
                if marker in head[:200]:
                    return "document", doc_ext, mime
        raise UploadRefused("archive", "zip that is not an Office document")
    if head.startswith(b"{\\rtf"):
        return "document", "rtf", "application/rtf"
    if _is_text(head):
        stripped = head[:512].lstrip().lower()
        if stripped.startswith(b"<svg") or (stripped.startswith(b"<?xml") and b"<svg" in head[:2048]):
            return "image", "svg", "image/svg+xml"
        if ext in _TEXT_EXTS:
            return "document", ext, _TEXT_EXTS[ext]
        if hint.startswith("text/"):
            return "document", "txt", "text/plain"
    raise UploadRefused("unsupported", f"unknown type (name ends .{ext or '-'}, browser said {hint[:40] or '-'})")


# ─── The stored name ─────────────────────────────────────────────────────────

def clean_name(filename: str, ext: str, kind: str) -> str:
    """The client's own name, readable (Hebrew stays Hebrew), minus path,
    control and bidi-format characters (U+202E can make 'gpj.exe' read as
    'exe.jpg'); the extension is the REAL type's. A name that looks like a
    credential (core/redaction) is replaced by a generic one — it is never
    stored, not even in Drive."""
    from core.redaction import looks_like_secret
    base = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    base = unicodedata.normalize("NFC", base.rsplit(".", 1)[0] if "." in base else base)
    base = "".join(c for c in base if unicodedata.category(c)[0] not in ("C",)
                   and (c.isalnum() or c in " -_().,'&+#"))
    base = " ".join(base.split())[:NAME_MAX].strip(" .")
    if not base or looks_like_secret(base) or looks_like_secret(filename or ""):
        base = f"{GENERIC_NAME.get(kind, 'file')}-{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M%S')}"
    return f"{base}.{ext}"


# ─── Sealed session token ────────────────────────────────────────────────────

def _token_key() -> bytes:
    from agents.keys_agent import get_key
    secret = get_key("SESSION_SECRET_KEY") or ""
    if not secret:
        raise RuntimeError("SESSION_SECRET_KEY is not set")
    return hashlib.sha256(secret.encode() + b":chat-upload").digest()


def _aad(client_id: int) -> bytes:
    return f"chat-upload:{int(client_id)}".encode()


def seal(payload: dict, client_id: int) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    body = json.dumps({**payload, "exp": int(time.time()) + TOKEN_SECONDS}).encode()
    sealed = AESGCM(_token_key()).encrypt(nonce, body, _aad(client_id))
    return base64.urlsafe_b64encode(nonce + sealed).decode()


def unseal(token: str, client_id: int) -> dict:
    """The payload, or {} — for a token that is tampered with, expired, or
    sealed for another client. Never raises."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        blob = base64.urlsafe_b64decode((token or "").encode())
        payload = json.loads(AESGCM(_token_key()).decrypt(blob[:12], blob[12:], _aad(client_id)))
    except Exception:
        return {}
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return {}
    return payload


# ─── Chunks ──────────────────────────────────────────────────────────────────

def chunk_bytes() -> int:
    from core.video_limits import UPLOAD_CHUNK_BYTES
    return UPLOAD_CHUNK_BYTES


def check_chunk(offset: int, length: int, total: int):
    """Every chunk but the last is exactly UPLOAD_CHUNK_BYTES (a multiple of
    256 KiB, as Drive requires); none may pass the declared total."""
    size = chunk_bytes()
    if offset < 0 or length <= 0 or length > size or offset + length > total:
        raise UploadRefused("bad_chunk", f"offset {offset} + {length} of {total}")
    if offset + length < total and length != size:
        raise UploadRefused("bad_chunk", f"short middle chunk ({length} bytes)")


@drive_storage.scoped
def _folder_id(client_id: int, kind: str) -> str:
    from agents.media_agent import _subfolder
    return _subfolder(client_id, FOLDERS[kind])


@drive_storage.scoped
def begin(client_id: int, persona: str, filename: str, total: int, head: bytes,
          hint_mime: str = "", duration_hint=None) -> dict:
    """The first chunk: decide the real type, the folder and the name, and
    open the Drive session. Returns the token payload (not yet sealed)."""
    from core import drive_service as drive
    if total <= 0 or not head:
        raise refuse(client_id, "empty")
    if total > DRIVE_MAX_BYTES:
        raise refuse(client_id, "too_big", f"{total} bytes")
    try:
        kind, ext, mime = sniff(head, filename, hint_mime)
    except UploadRefused as e:
        raise refuse(client_id, e.code, e.detail)
    name = clean_name(filename, ext, kind)
    if not drive_storage.configured(client_id):
        raise refuse(client_id, "not_configured", "DRIVE_MEDIA_FOLDER_ID / service account not set")
    props = {"uallak_kind": KIND_TAG, "uploaded_at": _now(), "source": "chat"}
    if kind == "audio":
        from core import voice_recordings as vr
        props["uallak_kind"] = vr.KIND   # the voice-recordings list treats it as one of theirs
        try:
            seconds = float(duration_hint)
            if 0 < seconds <= vr.MAX_SECONDS:
                props["duration_seconds"] = round(seconds, 1)
        except (TypeError, ValueError):
            pass
    try:
        folder = _folder_id(client_id, kind)
        uri = drive.start_resumable(folder, name, mime, total, props)
    except Exception as e:
        raise refuse(client_id, "storage", f"starting the Drive session failed: "
                                           f"{drive.classify_error(e)} {type(e).__name__}")
    return {"u": uri, "t": int(total), "k": kind, "n": name, "m": mime, "f": folder,
            "p": persona or "general"}


@drive_storage.scoped
def send(client_id: int, payload: dict, offset: int, data: bytes) -> dict:
    """Forward ONE chunk. {"done": False, "next_offset"} or {"done": True, "file"}."""
    from core import drive_service as drive
    check_chunk(offset, len(data), payload["t"])
    try:
        return drive.put_chunk(payload["u"], data, offset, payload["t"])
    except drive.ResumableSessionGone:
        raise UploadRefused("expired")
    except Exception as e:
        raise refuse(client_id, "storage", f"chunk at {offset} of {payload['t']} failed: {type(e).__name__}")


@drive_storage.scoped
def status(client_id: int, payload: dict) -> dict:
    from core import drive_service as drive
    try:
        return drive.resumable_status(payload["u"], payload["t"])
    except drive.ResumableSessionGone:
        raise UploadRefused("expired")
    except Exception as e:
        raise refuse(client_id, "storage", f"status check failed: {type(e).__name__}")


# ─── After the upload: the file is in the Media tab, the agent proposes ──────

def _media_changed(client_id: int):
    """The Media tab and the agents' snapshot must show the new file now,
    not after their caches expire."""
    try:
        from core import drive_mirror
        for key in [k for k in drive_mirror._list_cache if k[0] == client_id]:
            drive_mirror._list_cache.pop(key, None)
    except Exception:
        pass
    try:
        from core import client_state
        client_state.forget(client_id)
    except Exception:
        pass


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):,.0f}MB" if n >= 1024 * 1024 else f"{max(n, 1) / 1024:,.0f}KB"


def _offer(client_id: int, persona: str, name: str, params: dict, state: dict, client: dict,
           lang: str, cards: list, lines: list):
    from core import agent_actions
    result = agent_actions.propose(client_id, "client_chat", persona, {"name": name, "params": params},
                                   state=state, client=client, context={"language": lang, "message": ""})
    if result.get("card"):
        cards.append(result["card"])
        if result.get("notice"):
            lines.append(result["notice"])
    elif result.get("clarify") or result.get("refuse"):
        lines.append(result.get("clarify") or result.get("refuse"))
    else:
        print(f"[{SERVICE_NAME}] client {client_id}: {name} not offered after upload ({result.get('error')})")


def proposals(client_id: int, persona: str, kind: str, file_id: str, size: int) -> tuple:
    """(lines, cards): what can be done with this file, as approval cards —
    only what is really available to THIS client (core/agent_actions' own
    availability rules, run on the snapshot plus this file's facts), a plain
    line for each destination the file is too large for, and a plain line
    when nothing is available yet. Nothing runs without the client's click."""
    from core import agent_actions, client_state
    from core.video_limits import (TIKTOK_INBOX_MAX_MB, VIDEO_UPLOAD_MAX_MB, fits, fits_tiktok)
    client = agent_actions._client(client_id)
    lang = agent_actions._lang(client_id)
    try:
        state = client_state.snapshot(client_id) or {}
    except Exception:
        state = {}
    lines, cards = [], []

    if kind == "video":
        media = state.get("media_folder") or {}

        def with_video(uploadable: bool, tiktok_uploadable: bool) -> dict:
            facts = {"drive_file_id": file_id, "uploadable": uploadable, "tiktok_uploadable": tiktok_uploadable}
            return {**state, "media_folder": {**media, "videos": [facts]}}

        # "Would it be offered if the file fitted?" tells "too big" apart from "not connected"
        youtube_ok = agent_actions._youtube_upload_possible(with_video(True, True), client)
        tiktok_ok = agent_actions._tiktok_send_possible(with_video(True, True), client)
        here = with_video(fits(size), fits_tiktok(size))
        if youtube_ok:
            if fits(size):
                _offer(client_id, persona, "publish_youtube_from_media", {"drive_file_id": file_id},
                       here, client, lang, cards, lines)
            else:
                lines.append(f"ליוטיוב הוא גדול מדי ({_mb(size)}; אנחנו מעלים ליוטיוב סרטונים עד "
                             f"{VIDEO_UPLOAD_MAX_MB}MB). הוא שמור, ואפשר להעלות גרסה דחוסה יותר.")
        if tiktok_ok:
            if fits_tiktok(size):
                _offer(client_id, persona, "send_tiktok_to_inbox", {"drive_file_id": file_id},
                       here, client, lang, cards, lines)
            else:
                lines.append(f"לטיוטות בטיקטוק הוא גדול מדי (טיקטוק מקבלת מאיתנו עד {TIKTOK_INBOX_MAX_MB}MB). "
                             f"גרסה קצרה או דחוסה יותר תעבור.")
        if not youtube_ok and not tiktok_ok:
            lines.append("כרגע אין לי לאן לשלוח אותו: יוטיוב וטיקטוק עוד לא מחוברים אצלך. "
                         "ברגע שיתחברו, אציע להעלות אותו משם.")
    elif kind == "audio":
        from core import voice_recordings as vr
        lines.append("ההקלטה מופיעה גם ברשימת הקלטות הקול שלך.")
        voice = state.get("voice") or {}
        here = {**state, "voice": {**voice, "count": max(int(voice.get("count") or 0), 1)}}
        clone_ok = (agent_actions._is_internal(client)
                    and agent_actions._voice_clone_possible(here, client))
        if size > vr.MAX_MB * 1024 * 1024:
            lines.append(f"היא גדולה מ-{vr.MAX_MB}MB, ולכן כלי הקול לא יכולים להשתמש בה (שיבוט קול או "
                         f"פסקול לסרטון). הקלטה קצרה יותר, או שמירה כ-mp3, תעבור.")
        elif clone_ok:
            _offer(client_id, persona, "clone_voice", {}, here, client, lang, cards, lines)
        if not cards and size <= vr.MAX_MB * 1024 * 1024:
            lines.append("כרגע אין לי פעולה נוספת זמינה עבורה.")
    elif kind == "image":
        lines.append("עדיין אין לי פעולה אוטומטית לתמונה שעולה מהצ'אט — היא שמורה, "
                     "ואפשר לכתוב לי מה תרצו לעשות איתה.")
    else:
        lines.append("המסמך שמור. עדיין אין לי פעולה אוטומטית למסמכים שעולים מהצ'אט — "
                     "אפשר לכתוב לי מה תרצו לעשות איתו.")
    return lines, cards


def finish(client_id: int, payload: dict, drive_file: dict) -> dict:
    """The last chunk landed: refresh the caches, log the exchange in the
    window's own thread, and return the agent's line + the cards."""
    kind, persona = payload["k"], payload.get("p") or "general"
    file_id = drive_file.get("id") or ""
    try:
        size = int(drive_file.get("size") or payload["t"])
    except (TypeError, ValueError):
        size = int(payload["t"])
    name = drive_file.get("name") or payload["n"]
    _media_changed(client_id)
    try:
        lines, cards = proposals(client_id, persona, kind, file_id, size)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: proposals failed: {type(e).__name__}")
        lines, cards = [], []
    head = f"\"{name}\" נשמר בתיקיית המדיה שלך, בתיקייה {FOLDER_HE[kind]} ✓ הוא מופיע גם בלשונית המדיה."
    message = "\n".join([head] + lines)
    try:
        from agents.client_agent import log_communication
        from agents.support_agent import persona_channel
        log_communication(client_id, "inbound", persona_channel(persona), f"📎 {name}")
        log_communication(client_id, "outbound", persona_channel(persona), message)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: chat log failed: {type(e).__name__}")
    try:
        from agents import media_agent
        media_agent._log_activity(client_id, "chat_upload", {"file_id": file_id, "kind": kind, "size": size,
                                                             "folder": FOLDERS[kind], "persona": persona})
    except Exception:
        pass
    return {"file": {"id": file_id, "name": name, "kind": kind, "size": size, "folder": FOLDERS[kind]},
            "message": message, "cards": cards}


def valid_persona(persona: str) -> str:
    pid = (persona or "general").strip()
    if pid == "general":
        return pid
    try:
        from agents.support_agent import PERSONAS
        return pid if pid in PERSONAS else "general"
    except Exception:
        return "general"
