"""What a video file IS (container, codecs, size, frame rate, length), read
from its own bytes — and whether TikTok's Upload-to-Inbox can take it.

Why (2026-11-09): a demo video reached the client's TikTok inbox
(SEND_TO_USER_INBOX) but the draft never opened in the app. Nothing on the
path looked at the file: the only check was "Drive says video/*", and the
PUT always said Content-Type video/mp4. TikTok's Content Posting API media
rules (developers.tiktok.com/doc/content-posting-api-media-transfer-guide):
  containers MP4 (recommended), WebM, MOV
  video codecs H.264 (recommended), H.265, VP8, VP9
  23-60 frames per second, 360-4096 px on each side, at most 10 minutes.

Pure stdlib, no ffmpeg/ffprobe (the container has neither, and none is
added): the MP4/MOV box tree is walked for each track's handler, sample
description (codec fourcc), dimensions, timescale/duration and sample
timing; WebM is identified by its EBML DocType and its CodecID strings.
This reads the file's HEADERS — never frames or audio content (the
"watching the video" feature in CLAUDE.md stays unbuilt).

Only DEFINITE problems refuse a send. A field the parser could not read is
None and passes — a check that blocks good videos on a parser gap would be
worse than the TikTok failure it guards against.
"""

ALLOWED_CONTAINERS = ("mp4", "mov", "webm")
ALLOWED_VIDEO_CODECS = ("h264", "h265", "vp8", "vp9")
# What Drive must say before we even download (the card's first check)
ALLOWED_MIME_TYPES = ("video/mp4", "video/quicktime", "video/webm", "video/x-m4v")
MIN_FPS, MAX_FPS = 23.0, 60.0
MIN_SIDE_PX, MAX_SIDE_PX = 360, 4096
MAX_DURATION_S = 600

CONTENT_TYPES = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}

_VIDEO_FOURCC = {b"avc1": "h264", b"avc3": "h264", b"hvc1": "h265", b"hev1": "h265",
                 b"vp08": "vp8", b"vp09": "vp9", b"av01": "av1", b"mp4v": "mpeg4",
                 b"apch": "prores", b"apcn": "prores", b"apcs": "prores", b"apco": "prores",
                 b"ap4h": "prores", b"ap4x": "prores", b"jpeg": "mjpeg", b"mjpa": "mjpeg",
                 b"mjpb": "mjpeg", b"dvh1": "h265", b"dvhe": "h265", b"dva1": "h264",
                 b"dvav": "h264"}
_AUDIO_FOURCC = {b"mp4a": "aac", b"ac-3": "ac3", b"ec-3": "eac3", b"Opus": "opus",
                 b"lpcm": "pcm", b"sowt": "pcm", b"twos": "pcm", b"alac": "alac",
                 b".mp3": "mp3", b"fLaC": "flac"}
_WEBM_VIDEO = {b"V_VP8": "vp8", b"V_VP9": "vp9", b"V_AV1": "av1", b"V_MPEG4/ISO/AVC": "h264",
               b"V_MPEGH/ISO/HEVC": "h265"}
_WEBM_AUDIO = {b"A_OPUS": "opus", b"A_VORBIS": "vorbis", b"A_AAC": "aac"}

_CONTAINERS = (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts")


def content_type(container: str) -> str:
    """The PUT's Content-Type — the file's real container, never a guess."""
    return CONTENT_TYPES.get(container or "", "video/mp4")


def _u(buf, at: int, n: int) -> int:
    return int.from_bytes(buf[at:at + n], "big")


def _boxes(buf, start: int, end: int):
    """(type, payload_start, box_end) for each box between start and end.
    Stops at the first malformed or truncated box."""
    i = start
    while i + 8 <= end:
        size, kind, header = _u(buf, i, 4), bytes(buf[i + 4:i + 8]), 8
        if size == 1:
            if i + 16 > end:
                return
            size, header = _u(buf, i + 8, 8), 16
        elif size == 0:
            size = end - i
        if size < header or i + size > end:
            return
        yield kind, i + header, i + size
        i += size


def _child(buf, start: int, end: int, kind: bytes):
    for k, s, e in _boxes(buf, start, end):
        if k == kind:
            return s, e
    return None


def _path(buf, start: int, end: int, *kinds):
    span = (start, end)
    for kind in kinds:
        span = _child(buf, span[0], span[1], kind)
        if not span:
            return None
    return span


def _track(buf, start: int, end: int) -> dict:
    """One trak: {kind, codec, width, height, duration_s, fps}."""
    out = {"kind": "", "codec": None, "width": None, "height": None, "duration_s": None, "fps": None}
    hdlr = _path(buf, start, end, b"mdia", b"hdlr")
    if hdlr:
        out["kind"] = {b"vide": "video", b"soun": "audio"}.get(bytes(buf[hdlr[0] + 8:hdlr[0] + 12]), "other")
    timescale = duration = None
    mdhd = _path(buf, start, end, b"mdia", b"mdhd")
    if mdhd:
        s = mdhd[0]
        if buf[s] == 1:
            timescale, duration = _u(buf, s + 20, 4), _u(buf, s + 24, 8)
        else:
            timescale, duration = _u(buf, s + 12, 4), _u(buf, s + 16, 4)
        if timescale:
            out["duration_s"] = round(duration / timescale, 3)
    stbl = _path(buf, start, end, b"mdia", b"minf", b"stbl")
    if stbl:
        stsd = _child(buf, stbl[0], stbl[1], b"stsd")
        if stsd and _u(buf, stsd[0] + 4, 4) >= 1:
            entry = stsd[0] + 8
            fourcc = bytes(buf[entry + 4:entry + 8])
            if out["kind"] == "video":
                out["codec"] = _VIDEO_FOURCC.get(fourcc, fourcc.decode("latin-1").strip() or None)
                w, h = _u(buf, entry + 8 + 24, 2), _u(buf, entry + 8 + 26, 2)
                if w and h:
                    out["width"], out["height"] = w, h
            elif out["kind"] == "audio":
                out["codec"] = _AUDIO_FOURCC.get(fourcc, fourcc.decode("latin-1").strip() or None)
        stts = _child(buf, stbl[0], stbl[1], b"stts")
        if stts and out["kind"] == "video" and timescale:
            count = _u(buf, stts[0] + 4, 4)
            samples = ticks = 0
            at = stts[0] + 8
            for _ in range(min(count, 100000)):
                if at + 8 > stts[1]:
                    break
                n, delta = _u(buf, at, 4), _u(buf, at + 4, 4)
                samples, ticks, at = samples + n, ticks + n * delta, at + 8
            if samples and ticks:
                out["fps"] = round(samples * timescale / ticks, 3)
    if out["kind"] == "video" and not out["width"]:
        tkhd = _child(buf, start, end, b"tkhd")
        if tkhd:
            at = tkhd[0] + (88 if buf[tkhd[0]] == 1 else 76)
            w, h = _u(buf, at, 4) >> 16, _u(buf, at + 4, 4) >> 16
            if w and h:
                out["width"], out["height"] = w, h
    return out


def _probe_isobmff(buf, container: str) -> dict:
    out = _empty(container)
    moov = _child(buf, 0, len(buf), b"moov")
    if not moov:
        out["notes"].append("no moov box (truncated or unusual file)")
        return out
    for kind, s, e in _boxes(buf, moov[0], moov[1]):
        if kind != b"trak":
            continue
        t = _track(buf, s, e)
        if t["kind"] == "video" and out["video_codec"] is None:
            out.update(video_codec=t["codec"], width=t["width"], height=t["height"],
                       fps=t["fps"], duration_s=t["duration_s"])
            out["has_video"] = True
        elif t["kind"] == "audio" and out["audio_codec"] is None:
            out["audio_codec"] = t["codec"]
            out["has_audio"] = True
            if out["duration_s"] is None:
                out["duration_s"] = t["duration_s"]
    out["parsed"] = True
    return out


def _empty(container: str) -> dict:
    return {"container": container, "video_codec": None, "audio_codec": None, "width": None,
            "height": None, "fps": None, "duration_s": None, "has_video": None, "has_audio": None,
            "parsed": False, "notes": []}


def probe(data: bytes) -> dict:
    """The file's facts. Never raises: what it can't read is None."""
    buf = memoryview(data or b"")
    try:
        head = bytes(buf[:64])
        if len(head) >= 12 and head[4:8] == b"ftyp":
            brand = head[8:12]
            return _probe_isobmff(buf, "mov" if brand == b"qt  " else "mp4")
        if len(head) >= 8 and head[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip"):
            return _probe_isobmff(buf, "mov")
        if head[:4] == b"\x1a\x45\xdf\xa3":
            window = bytes(buf[:4 * 1024 * 1024])
            out = _empty("webm" if b"webm" in window[:200] else "mkv")
            out["video_codec"] = next((v for k, v in _WEBM_VIDEO.items() if k in window), None)
            out["audio_codec"] = next((v for k, v in _WEBM_AUDIO.items() if k in window), None)
            out["has_video"] = out["video_codec"] is not None or None
            out["parsed"] = True
            return out
        if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
            return _empty("avi")
        return _empty("unknown")
    except Exception as e:   # a parser bug must never block or crash a send
        out = _empty("unknown")
        out["notes"].append(f"probe error: {type(e).__name__}")
        return out


HEAD_BYTES = 64 * 1024
WEBM_HEAD_BYTES = 4 * 1024 * 1024
MOOV_MAX_BYTES = 32 * 1024 * 1024        # a moov this big is not a short-form clip
TOP_BOXES_MAX = 64


def probe_ranges(read, size: int) -> dict:
    """probe() for a file that is NOT in memory (the streamed TikTok upload):
    `read(start, length) -> bytes` reads a byte range. MP4/MOV: the top-level
    boxes are walked by their headers (16 bytes each) to find `moov`, which is
    read whole and probed after the `ftyp` — wherever it sits (start or end).
    WebM and anything else: the first bytes. A failed READ raises (that is
    a Drive problem, never reported as a bad file); parsing never raises."""
    size = int(size or 0)
    head = bytes(read(0, min(HEAD_BYTES, size))) if size else b""
    if len(head) < 12 or head[4:8] not in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"):
        if head[:4] == b"\x1a\x45\xdf\xa3" and size > len(head):
            head = bytes(read(0, min(WEBM_HEAD_BYTES, size)))
        return probe(head)
    ftyp, at = b"", 0
    for _ in range(TOP_BOXES_MAX):
        if at + 8 > size:
            break
        header = head[at:at + 16] if at + 16 <= len(head) else bytes(read(at, min(16, size - at)))
        box_size, kind = int.from_bytes(header[0:4], "big"), header[4:8]
        if box_size == 1 and len(header) >= 16:
            box_size = int.from_bytes(header[8:16], "big")
        elif box_size == 0:
            box_size = size - at
        if box_size < 8 or at + box_size > size:
            break
        if kind == b"ftyp" and box_size <= HEAD_BYTES:
            ftyp = head[at:at + box_size] if at + box_size <= len(head) else bytes(read(at, box_size))
        if kind == b"moov":
            if box_size > MOOV_MAX_BYTES:
                break
            moov = head[at:at + box_size] if at + box_size <= len(head) else bytes(read(at, box_size))
            return probe(ftyp + moov if ftyp else moov)
        at += box_size
    out = probe(ftyp or head)
    out["notes"].append("moov not found by range reads")
    return out


def problems(facts: dict) -> list:
    """DEFINITE reasons TikTok can't take the file, as short codes:
    container / codec / no_video / fps / resolution / duration."""
    facts = facts or {}
    out = []
    if facts.get("container") not in ALLOWED_CONTAINERS:
        out.append("container")
    codec = facts.get("video_codec")
    if codec and codec not in ALLOWED_VIDEO_CODECS:
        out.append("codec")
    if facts.get("parsed") and facts.get("container") in ("mp4", "mov") and facts.get("has_video") is not True:
        out.append("no_video")
    fps = facts.get("fps")
    if fps and not (MIN_FPS - 0.05 <= fps <= MAX_FPS + 0.05):
        out.append("fps")
    out += _size_problems(facts.get("width"), facts.get("height"), facts.get("duration_s"))
    return out


def _size_problems(width, height, duration_s) -> list:
    out = []
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        w = h = 0
    if w and h and (min(w, h) < MIN_SIDE_PX or max(w, h) > MAX_SIDE_PX):
        out.append("resolution")
    try:
        if duration_s and float(duration_s) > MAX_DURATION_S:
            out.append("duration")
    except (TypeError, ValueError):
        pass
    return out


def drive_meta_problems(meta: dict) -> list:
    """The same rules from what Drive already knows (mimeType +
    videoMediaMetadata) — before anything is downloaded: the card's check."""
    meta = meta or {}
    out = []
    if str(meta.get("mimeType") or "").lower() not in ALLOWED_MIME_TYPES:
        out.append("container")
    vm = meta.get("videoMediaMetadata") or {}
    duration = None
    try:
        if vm.get("durationMillis"):
            duration = int(vm["durationMillis"]) / 1000
    except (TypeError, ValueError):
        duration = None
    return out + _size_problems(vm.get("width"), vm.get("height"), duration)
