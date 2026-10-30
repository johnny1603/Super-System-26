"""Upload from the chat (core/chat_uploads, the resumable Drive helpers in
core/drive_service, the two /api/client/chat-upload routes) and the
resizable chat windows in dashboard/client/index.html.

Stdlib only: httpx is a fake Drive resumable session that checks every byte;
`cryptography` is replaced by a toy authenticated cipher (the test checks OUR
wrapper — binding to the client, expiry, tamper refusal — not AES itself).

Run from tests/:  python test_chat_uploads.py -v
"""
import base64
import contextlib
import hashlib
import hmac
import io
import os
import re
import sys
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import video_limits as vl   # noqa: E402

CHUNK = vl.UPLOAD_CHUNK_BYTES
MiB = 1024 * 1024
SESSION = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=abc"


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


def pattern(offset: int, length: int) -> bytes:
    out, pos = bytearray(), offset
    while pos < offset + length:
        block_end = min(offset + length, (pos // 262144 + 1) * 262144)
        out += bytes([(pos // 262144) % 251]) * (block_end - pos)
        pos = block_end
    return bytes(out)


# ─── Fakes ───────────────────────────────────────────────────────────────────

class _HTTPStatusError(Exception):
    def __init__(self, message, *, request=None, response=None):
        super().__init__(message)
        self.request, self.response = request, response


class _TransportError(Exception):
    pass


class Response:
    def __init__(self, status, headers=None, body=None, text=""):
        self.status_code, self.headers, self._body, self.text = status, headers or {}, body, text
        self.request = types.SimpleNamespace(method="PUT")

    def json(self):
        return self._body or {}


class FakeDriveSession:
    """Drive's resumable protocol: start → Location; PUT Content-Range →
    308 + Range / 200 + file. `faults` maps a PUT index to "503" |
    "transport" | "gone" | ("partial", n)."""

    def __init__(self, total, faults=None, content=None):
        self.total, self.stored, self.faults, self.puts = total, 0, dict(faults or {}), 0
        self.corrupt, self.started = False, []
        self.content = content or pattern

    def post(self, url, headers=None, params=None, content=b"", timeout=None, json=None):
        self.started.append({"url": url, "headers": headers, "params": params, "meta": content})
        return Response(200, {"location": SESSION})

    def _range(self):
        return {"range": f"bytes=0-{self.stored - 1}"} if self.stored else {}

    def put(self, url, headers=None, content=b"", timeout=None):
        assert url == SESSION
        index, self.puts = self.puts, self.puts + 1
        fault = self.faults.get(index)
        if fault == "transport":
            raise _TransportError("reset")
        if fault == "503":
            return Response(503, text="busy")
        if fault == "gone":
            return Response(404, text="no such session")
        content_range = headers["Content-Range"]
        if content_range.startswith("bytes */"):
            return (Response(200, body={"id": "f1", "name": "x", "size": str(self.total)})
                    if self.stored == self.total else Response(308, self._range()))
        span, total = content_range[len("bytes "):].split("/")
        start, end = (int(x) for x in span.split("-"))
        assert int(total) == self.total and end - start + 1 == len(content)
        if start != self.stored:
            return Response(400, text=f"expected {self.stored}, got {start}")
        keep = fault[1] if isinstance(fault, tuple) else len(content)
        if content[:keep] != self.content(start, keep):
            self.corrupt = True
        self.stored += keep
        if self.stored == self.total:
            return Response(201, body={"id": "f1", "name": "x", "size": str(self.total)})
        return Response(308, self._range())


class _ToyAESGCM:
    """Authenticated toy cipher with AESGCM's interface: a tampered blob or a
    different associated data fails, like the real thing."""

    def __init__(self, key):
        self.key = key

    def _stream(self, nonce, n):
        out, i = b"", 0
        while len(out) < n:
            out += hashlib.sha256(self.key + nonce + i.to_bytes(4, "big")).digest()
            i += 1
        return out[:n]

    def encrypt(self, nonce, data, aad):
        body = bytes(a ^ b for a, b in zip(data, self._stream(nonce, len(data))))
        return body + hmac.new(self.key, nonce + body + (aad or b""), hashlib.sha256).digest()[:16]

    def decrypt(self, nonce, sealed, aad):
        body, tag = sealed[:-16], sealed[-16:]
        if not hmac.compare_digest(tag, hmac.new(self.key, nonce + body + (aad or b""), hashlib.sha256).digest()[:16]):
            raise ValueError("InvalidTag")
        return bytes(a ^ b for a, b in zip(body, self._stream(nonce, len(body))))


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


ALERTS, LOGGED, ACTIVITY = [], [], []
STATE = {"value": {}}
FOLDERS_MADE = []

FAKE_HTTPX = _module("httpx", HTTPStatusError=_HTTPStatusError, TransportError=_TransportError,
                     Timeout=lambda **k: None, Response=object, post=None, put=None, get=None, stream=None)
_NAMES = ("httpx", "agents.keys_agent", "cryptography", "cryptography.hazmat",
          "cryptography.hazmat.primitives", "cryptography.hazmat.primitives.ciphers",
          "cryptography.hazmat.primitives.ciphers.aead", "core.chat_fallback", "core.client_state",
          "agents.client_agent", "agents.support_agent", "agents.media_agent", "core.drive_mirror",
          "core.drive_service", "core.chat_uploads")
_saved = {n: sys.modules.get(n) for n in _NAMES}
FAKE_MIRROR = _module("core.drive_mirror", _list_cache={})
sys.modules.update({
    "httpx": FAKE_HTTPX,
    "agents.keys_agent": _module("agents.keys_agent", get_key=lambda k: "session-secret-for-tests"),
    "cryptography": _module("cryptography"),
    "cryptography.hazmat": _module("cryptography.hazmat"),
    "cryptography.hazmat.primitives": _module("cryptography.hazmat.primitives"),
    "cryptography.hazmat.primitives.ciphers": _module("cryptography.hazmat.primitives.ciphers"),
    "cryptography.hazmat.primitives.ciphers.aead": _module("cryptography.hazmat.primitives.ciphers.aead",
                                                           AESGCM=_ToyAESGCM),
    "core.chat_fallback": _module("core.chat_fallback",
                                  alert_once=lambda *a, **k: ALERTS.append(a)),
    "core.client_state": _module("core.client_state", snapshot=lambda cid, refresh=False: STATE["value"],
                                 forget=lambda cid: None),
    "agents.client_agent": _module("agents.client_agent", get_client=lambda cid: {"id": cid},
                                   log_communication=lambda *a: LOGGED.append(a)),
    "agents.support_agent": _module("agents.support_agent", PERSONAS={"google": {}, "meta": {}},
                                    persona_channel=lambda p: "dashboard_chat" if p == "general"
                                    else f"dashboard_chat:{p}"),
    "agents.media_agent": _module("agents.media_agent",
                                  _subfolder=lambda cid, *p: FOLDERS_MADE.append((cid, p)) or f"folder-{'/'.join(p)}",
                                  _log_activity=lambda *a: ACTIVITY.append(a)),
    "core.drive_mirror": FAKE_MIRROR,
})
sys.modules.pop("core.drive_service", None)
sys.modules.pop("core.chat_uploads", None)
from core import drive_service as ds    # noqa: E402
from core import chat_uploads as cu     # noqa: E402
from core import agent_actions as aa    # noqa: E402
from core import voice_recordings as vr  # noqa: E402

ds.is_media_configured = lambda: True
ds._headers = lambda: {"Authorization": "Bearer test"}


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


# ─── Sniffing: the real type comes from the bytes ────────────────────────────

def ftyp(brand: bytes, *compat: bytes) -> bytes:
    body = brand + b"\x00\x00\x00\x00" + b"".join(compat)
    return (8 + len(body)).to_bytes(4, "big") + b"ftyp" + body + b"\x00" * 64


class Sniff(unittest.TestCase):
    def kind(self, head, name="", hint=""):
        return cu.sniff(head, name, hint)

    def test_iphone_heic_is_an_image(self):
        self.assertEqual(self.kind(ftyp(b"heic", b"mif1", b"heic"), "IMG_0001.HEIC", "image/heic"),
                         ("image", "heic", "image/heic"))
        self.assertEqual(self.kind(ftyp(b"mif1", b"heic"), "x", "")[0], "image")
        self.assertEqual(self.kind(ftyp(b"avif", b"mif1"), "x.avif")[1], "avif")

    def test_videos(self):
        self.assertEqual(self.kind(ftyp(b"qt  "), "IMG_0002.MOV", "video/quicktime")[:2], ("video", "mov"))
        self.assertEqual(self.kind(ftyp(b"isom", b"mp41"), "a.mp4", "video/mp4")[:2], ("video", "mp4"))
        self.assertEqual(self.kind(ftyp(b"3gp5"), "a.3gp")[:2], ("video", "3gp"))
        self.assertEqual(self.kind(b"\x1aE\xdf\xa3" + b"\x00" * 20 + b"webm" + b"\x00" * 40, "a.webm")[:2],
                         ("video", "webm"))
        self.assertEqual(self.kind(b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 20)[0], "video")

    def test_audio_including_containers_that_could_be_either(self):
        self.assertEqual(self.kind(ftyp(b"M4A "), "voice.m4a")[:2], ("audio", "m4a"))
        self.assertEqual(self.kind(ftyp(b"isom"), "memo.m4a", "audio/mp4")[0], "audio")
        self.assertEqual(self.kind(b"\x1aE\xdf\xa3" + b"\x00" * 40, "rec.webm", "audio/webm")[0], "audio")
        self.assertEqual(self.kind(b"ID3\x03" + b"\x00" * 20)[:2], ("audio", "mp3"))
        self.assertEqual(self.kind(b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 20)[:2], ("audio", "wav"))
        self.assertEqual(self.kind(b"OggS" + b"\x00" * 30 + b"OpusHead")[:2], ("audio", "opus"))
        self.assertEqual(self.kind(b"fLaC" + b"\x00" * 10)[:2], ("audio", "flac"))
        self.assertEqual(self.kind(b"\xff\xf1\x50\x80" + b"\x00" * 10)[:2], ("audio", "aac"))

    def test_images_and_documents(self):
        self.assertEqual(self.kind(b"\xff\xd8\xff\xe0" + b"\x00" * 10)[:2], ("image", "jpg"))
        self.assertEqual(self.kind(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)[:2], ("image", "png"))
        self.assertEqual(self.kind(b"%PDF-1.7\n")[:2], ("document", "pdf"))
        docx = b"PK\x03\x04" + b"\x00" * 26 + b"[Content_Types].xml" + b"\x00" * 30 + b"word/document.xml"
        self.assertEqual(self.kind(docx, "חוזה.docx")[:2], ("document", "docx"))
        xlsx = b"PK\x03\x04" + b"\x00" * 26 + b"[Content_Types].xml" + b"xl/workbook.xml"
        self.assertEqual(self.kind(xlsx)[1], "xlsx")
        self.assertEqual(self.kind("שם,טלפון\nדנה,050\n".encode(), "leads.csv")[:2], ("document", "csv"))
        self.assertEqual(self.kind(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 50, "old.xls")[1], "xls")

    def test_the_name_never_decides_the_type(self):
        # A JPEG called ".exe" is a JPEG; an executable called ".jpg" is refused
        self.assertEqual(self.kind(b"\xff\xd8\xff\xe0" + b"\x00" * 10, "photo.exe")[0], "image")
        with self.assertRaises(cu.UploadRefused) as e:
            self.kind(b"MZ\x90\x00" + b"\x00" * 50, "photo.jpg", "image/jpeg")
        self.assertEqual(e.exception.code, "unsafe")

    def test_what_we_refuse(self):
        cases = {
            "unsafe": [b"\x7fELF" + b"\x00" * 10, b"#!/bin/sh\nrm -rf /\n",
                       b"PK\x03\x04" + b"\x00" * 26 + b"[Content_Types].xml word/ vbaProject.bin",
                       b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "_VBA_PROJECT".encode("utf-16-le")],
            "archive": [b"PK\x03\x04" + b"\x00" * 60, b"Rar!\x1a\x07\x00", b"7z\xbc\xaf\x27\x1c",
                        b"\x1f\x8b\x08\x00"],
            "unsupported": [bytes(range(1, 200)) * 3],
            "empty": [b""],
        }
        for code, heads in cases.items():
            for head in heads:
                with self.assertRaises(cu.UploadRefused, msg=repr(head[:12])) as e:
                    self.kind(head, "file.bin")
                self.assertEqual(e.exception.code, code, repr(head[:12]))


# ─── Names ───────────────────────────────────────────────────────────────────

class Names(unittest.TestCase):
    def test_a_credential_looking_name_is_never_stored(self):
        for bad in ("sk-proj-abcdefghijklmnopqrstuvwxyz123456.mp4", "AIzaSyA1234567890abcdefghijklmnopqrstu.png",
                    "password=hunter2secret.pdf", "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8.jpg"):
            name = cu.clean_name(bad, "mp4", "video")
            self.assertTrue(name.startswith("video-"), name)
            for part in ("sk-proj", "AIza", "hunter2", "ghp_"):
                self.assertNotIn(part, name)

    def test_hebrew_stays_and_the_extension_is_the_real_type(self):
        self.assertEqual(cu.clean_name("C:\\Users\\dana\\סרטון השקה.MOV", "mov", "video"), "סרטון השקה.mov")
        self.assertEqual(cu.clean_name("invoice.exe", "pdf", "document"), "invoice.pdf")

    def test_bidi_and_control_characters_are_removed(self):
        name = cu.clean_name("photo\u202egpj.exe", "jpg", "image")
        self.assertNotIn("\u202e", name)
        self.assertTrue(name.endswith(".jpg"))
        self.assertNotIn("/", cu.clean_name("../../etc/passwd", "txt", "document"))


# ─── The sealed token ────────────────────────────────────────────────────────

class Token(unittest.TestCase):
    PAYLOAD = {"u": SESSION, "t": 123, "k": "video", "n": "a.mp4", "m": "video/mp4", "f": "folder", "p": "general"}

    def test_round_trip_for_the_same_client_only(self):
        token = cu.seal(self.PAYLOAD, 7)
        self.assertEqual(cu.unseal(token, 7)["u"], SESSION)
        self.assertEqual(cu.unseal(token, 8), {})

    def test_the_session_uri_is_not_readable_in_the_token(self):
        raw = base64.urlsafe_b64decode(cu.seal(self.PAYLOAD, 7).encode())
        self.assertNotIn(b"googleapis", raw)
        self.assertNotIn(b"upload_id", raw)

    def test_tampered_or_expired_tokens_open_nothing(self):
        token = cu.seal(self.PAYLOAD, 7)
        blob = bytearray(base64.urlsafe_b64decode(token.encode()))
        blob[20] ^= 1
        self.assertEqual(cu.unseal(base64.urlsafe_b64encode(bytes(blob)).decode(), 7), {})
        self.assertEqual(cu.unseal("garbage", 7), {})
        self.assertEqual(cu.unseal("", 7), {})
        real_time = cu.time.time
        try:
            cu.time.time = lambda: real_time() + cu.TOKEN_SECONDS + 5
            self.assertEqual(cu.unseal(token, 7), {})
        finally:
            cu.time.time = real_time


# ─── Chunks into Drive ───────────────────────────────────────────────────────

class Chunks(unittest.TestCase):
    def setUp(self):
        ALERTS.clear()
        self.sleeps = []
        self.real_put_chunk = ds.put_chunk
        ds.put_chunk = lambda uri, data, offset, total: self.real_put_chunk(uri, data, offset, total,
                                                                            _sleep=self.sleeps.append)

    def tearDown(self):
        ds.put_chunk = self.real_put_chunk

    def upload(self, total, faults=None, head_name="clip.mp4"):
        """What the browser does: first chunk opens the session, then chunk by
        chunk, following Drive's next_offset; on a failure, ask status."""
        head = ftyp(b"isom", b"mp41")

        def content(off, n):
            # the file: an MP4 head, then the position pattern
            data = bytearray(pattern(off, n))
            if off < len(head):
                piece = head[off:off + n]
                data[:len(piece)] = piece
            return bytes(data)
        drive = FakeDriveSession(total, faults, content=content)
        FAKE_HTTPX.post, FAKE_HTTPX.put = drive.post, drive.put
        sent = []
        payload = cu.begin(5, "general", head_name, total, content(0, min(CHUNK, total)), "video/mp4")
        offset = 0
        for _ in range(200):
            n = min(CHUNK, total - offset)
            data = content(offset, n)
            try:
                answer = cu.send(5, payload, offset, data)
            except cu.UploadRefused as e:
                if e.code != "storage":
                    raise
                answer = cu.status(5, payload)
            sent.append(n)
            if answer["done"]:
                return answer, drive, sent, payload
            offset = answer["next_offset"]
        self.fail("never finished")

    def test_fixed_chunks_and_every_byte_arrives(self):
        total = 3 * CHUNK + 12345
        with quiet():
            answer, drive, sent, payload = self.upload(total)
        self.assertTrue(answer["done"])
        self.assertEqual(answer["file"]["id"], "f1")
        self.assertEqual(drive.stored, total)
        self.assertFalse(drive.corrupt)
        self.assertEqual(sent, [CHUNK, CHUNK, CHUNK, 12345])
        self.assertEqual(CHUNK % (256 * 1024), 0)
        start = drive.started[0]
        self.assertEqual(start["params"]["uploadType"], "resumable")
        self.assertEqual(start["params"]["supportsAllDrives"], "true")
        self.assertEqual(start["headers"]["X-Upload-Content-Length"], str(total))
        self.assertIn(b'"parents": ["folder-videos"]', start["meta"])

    def test_a_5xx_is_retried_after_asking_drive_where_it_stands(self):
        with quiet():
            answer, drive, sent, _ = self.upload(2 * CHUNK + 7, faults={1: "503"})
        self.assertTrue(answer["done"])
        self.assertFalse(drive.corrupt)
        self.assertEqual(drive.stored, 2 * CHUNK + 7)
        self.assertTrue(self.sleeps)

    def test_a_dropped_connection_resumes(self):
        with quiet():
            answer, drive, _, _ = self.upload(2 * CHUNK, faults={0: "transport"})
        self.assertTrue(answer["done"])
        self.assertFalse(drive.corrupt)

    def test_a_partial_store_is_followed_from_drives_offset(self):
        with quiet():
            answer, drive, _, _ = self.upload(2 * CHUNK, faults={0: ("partial", MiB)})
        self.assertTrue(answer["done"])
        self.assertEqual(drive.stored, 2 * CHUNK)

    def test_a_gone_session_is_expired_not_a_raw_error(self):
        drive = FakeDriveSession(CHUNK * 2, faults={0: "gone"})
        FAKE_HTTPX.post, FAKE_HTTPX.put = drive.post, drive.put
        payload = {"u": SESSION, "t": CHUNK * 2, "k": "video", "n": "a.mp4"}
        with self.assertRaises(cu.UploadRefused) as e:
            cu.send(5, payload, 0, pattern(0, CHUNK))
        self.assertEqual(e.exception.code, "expired")

    def test_chunk_rules(self):
        for offset, length, total in ((0, CHUNK + 1, 10 * CHUNK), (0, CHUNK - 1, 10 * CHUNK),
                                      (CHUNK, CHUNK, CHUNK + 5), (-1, 5, 10)):
            with self.assertRaises(cu.UploadRefused):
                cu.check_chunk(offset, length, total)
        cu.check_chunk(CHUNK, 5, CHUNK + 5)         # the last chunk may be short
        cu.check_chunk(MiB, CHUNK, 3 * CHUNK)       # after a partial store, a full chunk from Drive's offset

    def test_put_chunk_only_ever_talks_to_drive(self):
        with self.assertRaises(ValueError):
            ds.put_chunk("https://evil.example/upload", b"x", 0, 1)
        with self.assertRaises(ValueError):
            ds.resumable_status("http://169.254.169.254/", 1)


# ─── begin(): no size limit of ours, routing, refusals ───────────────────────

class Begin(unittest.TestCase):
    def setUp(self):
        ALERTS.clear()
        FOLDERS_MADE.clear()
        self.started = []
        self.real = ds.start_resumable
        ds.start_resumable = lambda folder, name, mime, total, props: self.started.append(
            (folder, name, mime, total, props)) or SESSION

    def tearDown(self):
        ds.start_resumable = self.real

    def test_no_artificial_size_limit(self):
        fifty_gb = 50 * 1024 ** 3
        payload = cu.begin(5, "general", "wedding.mov", fifty_gb, ftyp(b"qt  "), "video/quicktime")
        self.assertEqual(payload["t"], fifty_gb)
        self.assertEqual(self.started[0][3], fifty_gb)
        with quiet(), self.assertRaises(cu.UploadRefused) as e:
            cu.begin(5, "general", "huge.mov", cu.DRIVE_MAX_BYTES + 1, ftyp(b"qt  "))
        self.assertEqual(e.exception.code, "too_big")     # Drive's own ceiling, the only one

    def test_each_type_lands_in_its_first_level_folder(self):
        heads = {"video": ftyp(b"isom"), "image": b"\xff\xd8\xff\xe0" + b"\x00" * 9,
                 "audio": b"ID3\x03" + b"\x00" * 9, "document": b"%PDF-1.4"}
        for kind, head in heads.items():
            payload = cu.begin(5, "google", f"file-{kind}", 1000, head)
            self.assertEqual(payload["k"], kind)
            self.assertEqual(payload["f"], f"folder-{cu.FOLDERS[kind]}")
        self.assertEqual(cu.FOLDERS["audio"], vr.FOLDER)          # the voice-recordings folder itself
        self.assertTrue(all(len(path) == 1 for _, path in FOLDERS_MADE))   # first level: client_state sees them

    def test_audio_is_tagged_as_a_voice_recording(self):
        cu.begin(5, "general", "memo.m4a", 1000, ftyp(b"M4A "), "audio/mp4", duration_hint="42.31")
        props = self.started[-1][4]
        self.assertEqual(props["uallak_kind"], vr.KIND)
        self.assertEqual(props["duration_seconds"], 42.3)
        cu.begin(5, "general", "memo.m4a", 1000, ftyp(b"M4A "), "audio/mp4", duration_hint="-3")
        self.assertNotIn("duration_seconds", self.started[-1][4])

    def test_refusals_are_one_plain_hebrew_sentence_plus_an_alert(self):
        with quiet(), self.assertRaises(cu.UploadRefused) as e:
            cu.begin(5, "general", "setup.exe", 5000, b"MZ\x90\x00" + b"\x00" * 60)
        self.assertEqual(e.exception.code, "unsafe")
        self.assertTrue(ALERTS and ALERTS[-1][2] == "chat_uploads:unsafe")
        for code, line in cu.PLAIN_HE.items():
            self.assertNotIn("\n", line, code)
            self.assertRegex(line, r"[\u0590-\u05FF]", code)
            for raw in ("Traceback", "Error", "HTTP", "Exception", "None"):
                self.assertNotIn(raw, line, code)
        self.assertEqual(self.started, [])                        # nothing was opened in Drive

    def test_a_drive_failure_is_storage_and_alerts(self):
        def boom(*a):
            raise RuntimeError("Drive POST 403: storageQuotaExceeded")
        ds.start_resumable = boom
        with quiet(), self.assertRaises(cu.UploadRefused) as e:
            cu.begin(5, "general", "a.mp4", 1000, ftyp(b"isom"))
        self.assertEqual(e.exception.code, "storage")
        self.assertTrue(any(a[2] == "chat_uploads:storage" for a in ALERTS))

    def test_unknown_personas_fall_back_to_general(self):
        self.assertEqual(cu.valid_persona("google"), "google")
        self.assertEqual(cu.valid_persona("<script>"), "general")
        self.assertEqual(cu.valid_persona(""), "general")


# ─── After the upload: the agent proposes, nothing runs ──────────────────────

def _state(youtube=True, tiktok=True, elevenlabs=False, consent=False):
    by_channel = {}
    platforms = []
    if youtube:
        by_channel["youtube"] = {"status": "active"}
        platforms.append("youtube")
    if tiktok:
        by_channel["tiktok"] = {"status": "active"}
        platforms.append("tiktok")
    return {"channels": {"by_channel": by_channel}, "connections": {"active_platforms": platforms},
            "media_folder": {"videos": []},
            "creative_tools": {"tools": {"elevenlabs": {"status": "connected" if elevenlabs else "not_connected"}}},
            "voice": {"count": 0, "voice_clone_consent": consent, "voice_clone_exists": False}}


class Proposals(unittest.TestCase):
    def setUp(self):
        self.proposed = []
        self.saved = (aa.propose, aa._client, aa._lang)
        aa._lang = lambda cid: "he"
        aa._client = lambda cid: {"id": cid, "billing_exempt": self.internal}
        self.internal = False

        def propose(client_id, origin, persona, raw, state=None, client=None, dry_run=False, context=None):
            # The real availability rule must pass on the state we hand it
            spec = aa.ACTIONS[raw["name"]]
            assert spec["available"](state, client), raw["name"]
            self.proposed.append((origin, persona, raw))
            return {"card": {"id": len(self.proposed), "action": raw["name"]}}
        aa.propose = propose

    def tearDown(self):
        aa.propose, aa._client, aa._lang = self.saved

    def run_for(self, kind, size, state):
        STATE["value"] = state
        return cu.proposals(5, "google", kind, "file123", size)

    def names(self):
        return [raw["name"] for _, _, raw in self.proposed]

    def test_small_video_offers_youtube_and_tiktok_as_cards(self):
        lines, cards = self.run_for("video", 20 * MiB, _state())
        self.assertEqual(self.names(), ["publish_youtube_from_media", "send_tiktok_to_inbox"])
        self.assertEqual(len(cards), 2)
        self.assertTrue(all(o == "client_chat" and p == "google" for o, p, _ in self.proposed))
        self.assertTrue(all(raw["params"] == {"drive_file_id": "file123"} for _, _, raw in self.proposed))

    def test_too_big_for_tiktok_is_said_at_the_point_of_use(self):
        lines, cards = self.run_for("video", 100 * MiB, _state())
        self.assertEqual(self.names(), ["publish_youtube_from_media"])
        self.assertTrue(any(str(vl.TIKTOK_INBOX_MAX_MB) in line and "טיקטוק" in line for line in lines))

    def test_too_big_for_both_still_stored_and_says_which(self):
        lines, cards = self.run_for("video", (vl.VIDEO_UPLOAD_MAX_MB + 1) * MiB, _state())
        self.assertEqual(cards, [])
        text = "\n".join(lines)
        self.assertIn(str(vl.VIDEO_UPLOAD_MAX_MB), text)
        self.assertIn(str(vl.TIKTOK_INBOX_MAX_MB), text)

    def test_nothing_connected_says_so_plainly(self):
        lines, cards = self.run_for("video", 5 * MiB, _state(youtube=False, tiktok=False))
        self.assertEqual(cards, [])
        self.assertTrue(any("עוד לא מחוברים" in line for line in lines))

    def test_tiktok_not_in_plan_is_not_offered(self):
        state = _state(youtube=False, tiktok=False)
        state["channels"]["by_channel"]["tiktok"] = {"status": "not_in_plan"}
        self.run_for("video", 5 * MiB, state)
        self.assertEqual(self.names(), [])

    def test_audio_clone_only_where_really_available(self):
        lines, cards = self.run_for("audio", MiB, _state(elevenlabs=True, consent=True))
        self.assertEqual(cards, [])                       # clone_voice is internal_only: not for a normal client
        self.assertTrue(any("אין לי פעולה נוספת" in line for line in lines))
        self.internal = True
        lines, cards = self.run_for("audio", MiB, _state(elevenlabs=True, consent=True))
        self.assertEqual(self.names(), ["clone_voice"])
        self.proposed.clear()
        self.run_for("audio", MiB, _state(elevenlabs=True, consent=False))   # no consent → no card
        self.assertEqual(self.names(), [])

    def test_audio_too_big_for_voice_tools_is_explained(self):
        self.internal = True
        lines, cards = self.run_for("audio", (vr.MAX_MB + 1) * MiB, _state(elevenlabs=True, consent=True))
        self.assertEqual(cards, [])
        self.assertTrue(any(f"{vr.MAX_MB}MB" in line for line in lines))

    def test_images_and_documents_get_no_fake_card(self):
        for kind in ("image", "document"):
            lines, cards = self.run_for(kind, MiB, _state())
            self.assertEqual(cards, [])
            self.assertTrue(any("עדיין אין לי פעולה אוטומטית" in line for line in lines))
        self.assertEqual(self.proposed, [])

    def test_finish_logs_the_thread_and_clears_only_this_clients_listing(self):
        LOGGED.clear()
        FAKE_MIRROR._list_cache.clear()
        FAKE_MIRROR._list_cache.update({(5, "root", ""): 1, (5, "videos", ""): 1, (6, "root", ""): 1})
        STATE["value"] = _state()
        out = cu.finish(5, {"k": "video", "p": "google", "n": "a.mp4", "t": 10 * MiB},
                        {"id": "f9", "name": "a.mp4", "size": str(10 * MiB)})
        self.assertEqual(list(FAKE_MIRROR._list_cache), [(6, "root", "")])
        self.assertEqual(out["file"], {"id": "f9", "name": "a.mp4", "kind": "video", "size": 10 * MiB,
                                       "folder": "videos"})
        self.assertIn("לשונית המדיה", out["message"])
        self.assertEqual([entry[2] for entry in LOGGED], ["dashboard_chat:google"] * 2)
        self.assertEqual(len(out["cards"]), 2)

    def test_proposing_is_all_it_does(self):
        # No runner, no decide, no publish call anywhere in the module
        src = _src("core", "chat_uploads.py")
        for forbidden in ("decide(", "_run_", "publish(", "upload_video", "make_file_public"):
            self.assertNotIn(forbidden, src)


# ─── Routes, scopes, admin view ──────────────────────────────────────────────

class Routes(unittest.TestCase):
    def setUp(self):
        self.api = _src("core", "api_server.py")
        start = self.api.index("# ─── Upload from the chat (core/chat_uploads)")
        self.route = self.api[start:self.api.index("@app.get(\"/api/avatar/scan\"", start)]

    def test_session_gated_and_memory_bounded_to_one_chunk(self):
        self.assertEqual(self.route.count("_require_session(request)"), 2)
        self.assertIn("request.stream()", self.route)
        self.assertIn("if len(body) > limit", self.route)
        self.assertIn("if declared > limit", self.route)
        self.assertNotIn("await request.body()", self.route)

    def test_staff_viewing_as_the_client_cannot_upload(self):
        from core import admin_view
        for endpoint in ("client_chat_upload", "client_chat_upload_status"):
            self.assertFalse(admin_view.allowed(endpoint, "POST"))

    def test_no_new_google_scope(self):
        self.assertEqual(ds.DRIVE_SCOPES, ["https://www.googleapis.com/auth/drive"])
        for src in (_src("core", "chat_uploads.py"), self.route):
            self.assertNotIn("googleapis.com/auth", src)
            self.assertNotIn("photoslibrary", src)
        # The later verification round is written down where someone would add it
        for src in (_src("core", "chat_uploads.py"), self.route, _src("docs", "CHAT-UPLOADS.md")):
            self.assertRegex(src, r"(?i)google(\s|#)+photos")
            self.assertRegex(src, r"(?i)later")


# ─── The chat windows: resizable, and still every button works ───────────────

def _js_function(html: str, name: str) -> str:
    start = html.index(f"function {name}(")
    depth, i = 0, html.index("{", start)
    while True:
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
        i += 1


DOM_MOVES = re.compile(r"(?<!classList)\.(appendChild|insertBefore|prepend|append|replaceChild|replaceWith|before|after|remove)\(")


class Windows(unittest.TestCase):
    def setUp(self):
        self.html = _src("dashboard", "client", "index.html")

    def test_drag_handlers_never_move_a_node(self):
        for name in ("focusWindow", "wireWindowResize", "applyWindowSize", "wireWindowDrop", "clampWindowSize"):
            body = _js_function(self.html, name)
            self.assertIsNone(DOM_MOVES.search(body), f"{name} adds/moves/removes DOM nodes")

    def test_mousedown_only_raises_the_window(self):
        build = _js_function(self.html, "buildWindow")
        self.assertIn("root.addEventListener('mousedown', () => focusWindow(id));", build)
        self.assertEqual(len(re.findall(r"addEventListener\('(mousedown|pointerdown)'", build)), 1)
        # every node the drag code touches is created HERE, before the handlers are wired
        self.assertLess(build.index("cw-resize"), build.index("wireWindowResize(id, root)"))
        self.assertLess(build.index("cw-drop"), build.index("wireWindowDrop(id, root)"))

    def test_every_window_button_is_still_wired(self):
        build = _js_function(self.html, "buildWindow")
        for wiring in ("[data-act=\"dl\"]').onclick = () => downloadWindowConversation(id)",
                       "[data-act=\"new\"]').onclick = () => newChatInWindow(id)",
                       "[data-act=\"min\"]').onclick = () => minimizeWindow(id)",
                       "[data-act=\"close\"]').onclick = () => closeAgentWindow(id)",
                       "send.onclick = () => sendInWindow(id)",
                       "wireChatInput(input, () => sendInWindow(id))",
                       "attach.onclick = () => file.click()"):
            self.assertIn(wiring, build)

    def test_size_is_per_window_per_client_with_a_minimum(self):
        self.assertIn("uallak_cw_size_${chatData.clientId}_${id}", self.html)
        self.assertIn("return { w: 280, h: 360 }", self.html)
        self.assertIn("width: var(--cw-w, 322px); height: var(--cw-h, 468px)", self.html)

    def test_phone_keeps_the_fixed_sheet(self):
        mobile = self.html[self.html.index("@media (max-width: 640px) {\n  .chat-windows"):]
        mobile = mobile[:mobile.index("}\n}") + 3]
        self.assertIn(".cw-resize { display: none; }", mobile)
        self.assertIn("width: auto; height: 70vh", mobile)   # fixed values, not the variables
        self.assertIn("if (isPhoneChatLayout() || e.button > 0) return;", self.html)

    def test_browser_chunk_matches_the_server(self):
        m = re.search(r"const CHAT_UPLOAD_CHUNK = (\d+) \* (\d+) \* (\d+);", self.html)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)) * int(m.group(2)) * int(m.group(3)), vl.UPLOAD_CHUNK_BYTES)

    def test_phone_plus_opens_the_native_picker(self):
        build = _js_function(self.html, "buildWindow")
        self.assertIn("file.type = 'file'", build)
        accept = re.search(r"file\.accept = '([^']*)'\s*\+\s*'([^']*)'", build)
        self.assertIsNotNone(accept)
        for kind in ("image/*", "video/*", "audio/*", "application/pdf", ".heic"):
            self.assertIn(kind, accept.group(1) + accept.group(2))
        self.assertNotIn("capture", build)    # capture would skip the gallery and force the camera

    def test_upload_shows_a_progress_card_and_then_the_agents_cards(self):
        one = _js_function(self.html, "uploadOneFile")
        self.assertIn("appendUploadCard(w, file)", one)
        self.assertIn("appendActionCard(id, c)", one)
        self.assertIn("chatUploadStatus(token)", one)
        self.assertNotIn("spinner", one)

    def test_i18n_keys_exist_in_five_languages(self):
        for key in ("chat_attach", "chat_drop_here", "upload_title", "upload_progress", "upload_resuming",
                    "upload_done", "upload_failed", "upload_empty"):
            line = re.search(rf"^  {key}: \{{(.*)\}},$", self.html, re.M)
            self.assertIsNotNone(line, key)
            for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
                self.assertIn(lang, line.group(1), key)


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
