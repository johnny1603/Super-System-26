"""TikTok demo fixes (2026-11-09):
- core/video_probe: what a video file IS (container, codecs, size, fps,
  length) from its own bytes, and TikTok's media rules (MP4/MOV/WebM,
  H.264/H.265/VP8/VP9, 23-60 fps, 360-4096 px, <= 10 min);
- tiktok_content_agent.publish refuses a file TikTok can't show BEFORE init
  and the PUT names the real container (no more fixed video/mp4);
- the send card refuses from Drive's metadata with a plain line;
- the media list the model picks from is re-read on a video turn, newest
  first with dates, and marks videos already sent to TikTok.
Stdlib unittest, synthetic files — runnable without deps."""
import os
import re
import struct
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import video_probe as vp   # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ─── A tiny MP4/MOV writer (box headers only — no media data needed) ────────

def box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def full(kind: bytes, payload: bytes, version: int = 0) -> bytes:
    return box(kind, bytes([version, 0, 0, 0]) + payload)


def video_trak(fourcc=b"avc1", width=1080, height=1920, fps=30, seconds=15, timescale=15360):
    frames = fps * seconds
    delta = timescale // fps
    mdhd = full(b"mdhd", struct.pack(">IIII", 0, 0, timescale, frames * delta) + b"\0" * 4)
    hdlr = full(b"hdlr", b"\0" * 4 + b"vide" + b"\0" * 12 + b"VideoHandler\0")
    entry = box(fourcc, b"\0" * 6 + struct.pack(">H", 1) + b"\0" * 16
                + struct.pack(">HH", width, height) + b"\0" * 50)
    stsd = full(b"stsd", struct.pack(">I", 1) + entry)
    stts = full(b"stts", struct.pack(">III", 1, frames, delta))
    stbl = box(b"stbl", stsd + stts)
    return box(b"trak", box(b"mdia", mdhd + hdlr + box(b"minf", stbl)))


def audio_trak(fourcc=b"mp4a", seconds=15, rate=44100):
    mdhd = full(b"mdhd", struct.pack(">IIII", 0, 0, rate, rate * seconds) + b"\0" * 4)
    hdlr = full(b"hdlr", b"\0" * 4 + b"soun" + b"\0" * 12 + b"SoundHandler\0")
    stsd = full(b"stsd", struct.pack(">I", 1) + box(fourcc, b"\0" * 28))
    return box(b"trak", box(b"mdia", mdhd + hdlr + box(b"minf", box(b"stbl", stsd))))


def mp4(brand=b"isom", video=None, audio=True, moov_last=True) -> bytes:
    ftyp = box(b"ftyp", brand + b"\0\0\0\0" + brand)
    traks = (video if video is not None else video_trak()) + (audio_trak() if audio else b"")
    moov = box(b"moov", full(b"mvhd", b"\0" * 96) + traks)
    mdat = box(b"mdat", b"\0" * 64)
    return ftyp + (mdat + moov if moov_last else moov + mdat)


GOOD_MP4 = mp4()


class Probe(unittest.TestCase):
    def test_h264_aac_mp4_is_read_and_passes(self):
        facts = vp.probe(GOOD_MP4)
        self.assertEqual((facts["container"], facts["video_codec"], facts["audio_codec"]), ("mp4", "h264", "aac"))
        self.assertEqual((facts["width"], facts["height"]), (1080, 1920))
        self.assertAlmostEqual(facts["fps"], 30, places=2)
        self.assertAlmostEqual(facts["duration_s"], 15, places=2)
        self.assertEqual(vp.problems(facts), [])
        self.assertEqual(vp.content_type(facts["container"]), "video/mp4")

    def test_moov_before_mdat_is_read_too(self):
        self.assertEqual(vp.probe(mp4(moov_last=False))["video_codec"], "h264")

    def test_quicktime_is_mov_and_gets_its_own_content_type(self):
        facts = vp.probe(mp4(brand=b"qt  ", video=video_trak(b"hvc1")))
        self.assertEqual((facts["container"], facts["video_codec"]), ("mov", "h265"))
        self.assertEqual(vp.problems(facts), [])
        self.assertEqual(vp.content_type("mov"), "video/quicktime")
        self.assertEqual(vp.content_type("webm"), "video/webm")

    def test_prores_is_a_codec_problem(self):
        self.assertIn("codec", vp.problems(vp.probe(mp4(brand=b"qt  ", video=video_trak(b"apcn")))))

    def test_frame_rate_resolution_and_length_limits(self):
        self.assertIn("fps", vp.problems(vp.probe(mp4(video=video_trak(fps=15)))))
        self.assertIn("fps", vp.problems(vp.probe(mp4(video=video_trak(fps=120, timescale=12000)))))
        self.assertIn("resolution", vp.problems(vp.probe(mp4(video=video_trak(width=320, height=240)))))
        self.assertIn("resolution", vp.problems(vp.probe(mp4(video=video_trak(width=5000, height=3000)))))
        self.assertIn("duration", vp.problems(vp.probe(mp4(video=video_trak(seconds=700)))))
        # 23.976 and 59.94 are fine
        self.assertEqual(vp.problems(vp.probe(mp4(video=video_trak(fps=24, timescale=24000)))), [])

    def test_an_mp4_with_no_video_track_is_refused(self):
        self.assertIn("no_video", vp.problems(vp.probe(mp4(video=b""))))

    def test_other_containers_are_refused(self):
        mkv = b"\x1a\x45\xdf\xa3" + b"\0" * 8 + b"matroska" + b"\0" * 50 + b"V_MPEG4/ISO/AVC"
        self.assertEqual(vp.probe(mkv)["container"], "mkv")
        self.assertIn("container", vp.problems(vp.probe(mkv)))
        avi = b"RIFF" + b"\0" * 4 + b"AVI " + b"\0" * 40
        self.assertIn("container", vp.problems(vp.probe(avi)))
        self.assertIn("container", vp.problems(vp.probe(b"not a video at all")))
        self.assertIn("container", vp.problems(vp.probe(b"")))

    def test_webm_vp9_passes(self):
        webm = b"\x1a\x45\xdf\xa3" + b"\0" * 8 + b"webm" + b"\0" * 50 + b"V_VP9" + b"\0" * 20 + b"A_OPUS"
        facts = vp.probe(webm)
        self.assertEqual((facts["container"], facts["video_codec"], facts["audio_codec"]), ("webm", "vp9", "opus"))
        self.assertEqual(vp.problems(facts), [])

    def test_a_truncated_file_never_raises_and_unknowns_pass(self):
        facts = vp.probe(GOOD_MP4[:40])
        self.assertEqual(facts["container"], "mp4")
        self.assertEqual(vp.problems(facts), [])     # unreadable fields block nothing

    def test_drive_metadata_check(self):
        ok = {"mimeType": "video/mp4", "videoMediaMetadata": {"width": 1080, "height": 1920, "durationMillis": "15000"}}
        self.assertEqual(vp.drive_meta_problems(ok), [])
        self.assertEqual(vp.drive_meta_problems({"mimeType": "video/quicktime"}), [])
        self.assertIn("container", vp.drive_meta_problems({"mimeType": "video/x-matroska"}))
        self.assertIn("container", vp.drive_meta_problems({"mimeType": "video/avi"}))
        self.assertIn("resolution", vp.drive_meta_problems({**ok, "videoMediaMetadata": {"width": 200, "height": 200}}))
        self.assertIn("duration", vp.drive_meta_problems({**ok, "videoMediaMetadata": {"durationMillis": "700000"}}))


# ─── publish(): refused before init; the PUT carries the real container ─────

class Publish(unittest.TestCase):
    NAMES = ("httpx", "supabase", "agents.keys_agent", "core.agent_base", "core.secrets_vault",
             "core.drive_storage", "core.tiktok_grant", "core.drive_service", "agents.client_agent",
             "core.tiktok_service", "agents.tiktok_content_agent")

    def setUp(self):
        self.saved = {n: sys.modules.get(n) for n in self.NAMES}
        self.calls = []
        self.file = GOOD_MP4
        mod = lambda name, **kw: types.SimpleNamespace(__name__=name, **kw)   # noqa: E731
        sys.modules["supabase"] = mod("supabase", create_client=lambda *a, **k: None)
        sys.modules["core.agent_base"] = mod("core.agent_base", agent_alert=lambda *a, **k: None,
                                             log_step=lambda *a, **k: None, timed_step=lambda a, s, fn: fn())
        sys.modules["core.secrets_vault"] = mod("core.secrets_vault", hydrate=lambda r: r)
        sys.modules["core.drive_storage"] = mod("core.drive_storage", scoped=lambda f: f)
        sys.modules["core.tiktok_grant"] = mod("core.tiktok_grant", needs_reconnect=lambda cid: False,
                                               state=lambda cid: "ok", record_scopes=lambda *a: None,
                                               record_dead=lambda *a: None, record_missing_upload=lambda *a: None)
        sys.modules["core.drive_service"] = mod(
            "core.drive_service", get_meta=lambda fid, fields="": {"id": fid, "size": str(len(self.file))},
            read_range=lambda fid, start, length: self.file[start:start + length])
        sys.modules["agents.client_agent"] = mod("agents.client_agent", log_activity=lambda *a, **k: self.calls.append(("activity", a)),
                                                 get_client=lambda cid: {}, log_communication=lambda *a, **k: None,
                                                 upsert_account=lambda *a, **k: None)
        sys.modules["core.tiktok_service"] = mod(
            "core.tiktok_service", UPLOAD_SCOPE="video.upload",
            split_tokens=lambda s: ("acc", "ref"), join_tokens=lambda a, r: f"{a}::{r}",
            refresh_access_token=lambda r: {"access_token": "acc", "refresh_token": "ref", "scope": "video.upload"},
            is_dead_grant=lambda e: False, is_scope_error=lambda e: False, is_pending_limit=lambda e: False,
            is_token_error=lambda e: False,
            init_inbox_upload=lambda *a, **k: self.calls.append(("init", k)) or {"publish_id": "p1", "upload_url": "u"},
            upload_video_stream=lambda url, read, size, **k: self.calls.append(("put", k)) or 201,
            get_post_status=lambda t, p: {"status": "SEND_TO_USER_INBOX"})
        sys.modules.pop("agents.tiktok_content_agent", None)
        # import_module, not "from agents import ...": the package attribute
        # would hand back the previous test's module and its stubs
        import importlib
        agent = importlib.import_module("agents.tiktok_content_agent")
        agent._get_connection = lambda cid: {"access_token": "acc::ref", "account_id": "open"}
        agent.STATUS_POLL_SECONDS = 0
        self.agent = agent

    def tearDown(self):
        for name, module in self.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_a_good_mp4_is_sent_as_video_mp4(self):
        result = self.agent.publish(27, {"drive_file_id": "f1"})
        self.assertTrue(result["success"], result)
        put = [c for c in self.calls if c[0] == "put"][0][1]
        self.assertEqual(put["mime_type"], "video/mp4")
        init = [c for c in self.calls if c[0] == "init"][0][1]
        self.assertEqual((init["chunk_size"], init["total_chunk_count"]), (len(GOOD_MP4), 1))
        recorded = [c for c in self.calls if c[0] == "activity"][0][1][3]
        self.assertEqual(recorded["video"]["video_codec"], "h264")   # what was sent is on record

    def test_a_mov_is_sent_as_quicktime(self):
        self.file = mp4(brand=b"qt  ")
        self.assertTrue(self.agent.publish(27, {"drive_file_id": "f1"})["success"])
        self.assertEqual([c for c in self.calls if c[0] == "put"][0][1]["mime_type"], "video/quicktime")

    def test_a_file_tiktok_cant_show_is_refused_before_init(self):
        self.file = mp4(brand=b"qt  ", video=video_trak(b"apcn"))
        result = self.agent.publish(27, {"drive_file_id": "f1"})
        self.assertFalse(result["success"])
        self.assertTrue(result["bad_format"])
        self.assertIn("codec", result["problems"])
        self.assertFalse([c for c in self.calls if c[0] in ("init", "put")])   # nothing reached TikTok


# ─── Chunked streaming upload (2026-11-09) ──────────────────────────────────

MB = 1024 * 1024


class ChunkPlan(unittest.TestCase):
    def test_small_file_is_one_whole_chunk(self):
        from core import video_limits as vl
        self.assertEqual(vl.tiktok_chunk_plan(3 * MB), [(0, 3 * MB)])
        self.assertEqual(vl.tiktok_chunk_size(3 * MB), 3 * MB)
        self.assertEqual(vl.tiktok_chunk_plan(15 * MB), [(0, 15 * MB)])
        self.assertEqual(vl.tiktok_chunk_plan(0), [])

    def test_large_file_follows_tiktoks_rules(self):
        from core import video_limits as vl
        size = int(412.8 * MB)
        plan = vl.tiktok_chunk_plan(size)
        self.assertEqual(len(plan), size // vl.TIKTOK_CHUNK_BYTES)          # floor(size / chunk_size)
        self.assertEqual(sum(n for _, n in plan), size)                    # every byte, once
        self.assertEqual([s for s, _ in plan], [i * vl.TIKTOK_CHUNK_BYTES for i in range(len(plan))])
        for _, n in plan[:-1]:
            self.assertEqual(n, vl.TIKTOK_CHUNK_BYTES)
        self.assertTrue(vl.TIKTOK_CHUNK_BYTES <= plan[-1][1] < 2 * vl.TIKTOK_CHUNK_BYTES)
        self.assertLessEqual(plan[-1][1], 128 * MB)
        self.assertTrue(5 * MB <= vl.TIKTOK_CHUNK_BYTES <= 64 * MB)
        # no cap of ours: even very large files stay within TikTok's chunk rules
        for gb in (1.5, 4, 20, 60):
            size = int(gb * 1024 * MB)
            chunk = vl.tiktok_chunk_size(size)
            plan = vl.tiktok_chunk_plan(size, chunk)
            self.assertTrue(5 * MB <= chunk <= 64 * MB, gb)
            self.assertTrue(1 <= len(plan) <= vl.TIKTOK_MAX_CHUNKS, gb)
            self.assertEqual(len(plan), size // chunk, gb)
            self.assertEqual(sum(n for _, n in plan), size, gb)
            self.assertLessEqual(plan[-1][1], 128 * MB, gb)

    def test_small_file_is_one_whole_chunk(self):
        from core import video_limits as vl
        size = 3 * MB
        self.assertEqual(vl.tiktok_chunk_size(size), size)
        self.assertEqual(vl.tiktok_chunk_plan(size), [(0, size)])

    def test_exact_multiple_has_no_short_tail(self):
        from core import video_limits as vl
        plan = vl.tiktok_chunk_plan(3 * vl.TIKTOK_CHUNK_BYTES)
        self.assertEqual([n for _, n in plan], [vl.TIKTOK_CHUNK_BYTES] * 3)


class StreamUpload(unittest.TestCase):
    def setUp(self):
        self.saved = sys.modules.get("httpx")
        self.puts, self.answers = [], []

        class TransportError(Exception):
            pass

        def put(url, headers=None, content=None, timeout=None):
            self.puts.append(dict(headers))
            answer = self.answers.pop(0) if self.answers else 201
            if isinstance(answer, Exception):
                raise answer
            return types.SimpleNamespace(status_code=answer, text="")

        self.TransportError = TransportError
        sys.modules["httpx"] = types.SimpleNamespace(__name__="httpx", put=put, TransportError=TransportError,
                                                     Response=object)
        sys.modules.pop("core.tiktok_service", None)
        import importlib
        self.svc = importlib.import_module("core.tiktok_service")

    def tearDown(self):
        sys.modules.pop("core.tiktok_service", None)
        if self.saved is None:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = self.saved

    def _file(self, size):
        return lambda start, length: b"\0" * length

    def test_chunks_are_sequential_with_content_range_206_then_201(self):
        size = 40 * MB
        self.answers = [206, 201]
        sent = self.svc.upload_video_stream("u", self._file(size), size, _sleep=lambda s: None)
        self.assertEqual(sent, 2)
        self.assertEqual(self.puts[0]["Content-Range"], f"bytes 0-{16 * MB - 1}/{size}")
        self.assertEqual(self.puts[1]["Content-Range"], f"bytes {16 * MB}-{size - 1}/{size}")
        self.assertEqual(self.puts[1]["Content-Length"], str(24 * MB))

    def test_a_5xx_or_dropped_connection_is_retried(self):
        size = 4 * MB
        self.answers = [500, self.TransportError("reset"), 201]
        slept = []
        self.assertEqual(self.svc.upload_video_stream("u", self._file(size), size, _sleep=slept.append), 1)
        self.assertEqual(len(self.puts), 3)
        self.assertEqual(len(slept), 2)

    def test_a_4xx_is_raised_at_once_and_retries_are_bounded(self):
        self.answers = [400]
        with self.assertRaises(self.svc.TikTokAPIError):
            self.svc.upload_video_stream("u", self._file(MB), MB, _sleep=lambda s: None)
        self.assertEqual(len(self.puts), 1)
        self.puts.clear()
        self.answers = [503] * 10
        with self.assertRaises(self.svc.TikTokAPIError):
            self.svc.upload_video_stream("u", self._file(MB), MB, _sleep=lambda s: None)
        self.assertEqual(len(self.puts), self.svc.CHUNK_RETRIES + 1)

    def test_a_short_read_is_an_error_not_a_bad_upload(self):
        with self.assertRaises(self.svc.TikTokAPIError):
            self.svc.upload_video_stream("u", lambda s, n: b"\0" * (n - 1), MB, _sleep=lambda s: None)
        self.assertEqual(self.puts, [])


class RangeProbe(unittest.TestCase):
    def _reader(self, data, log):
        def read(start, length):
            log.append(length)
            return data[start:start + length]
        return read

    def test_moov_at_the_end_is_found_by_ranges(self):
        data = box(b"ftyp", b"isom\0\0\0\0isom") + box(b"mdat", b"\0" * 200000) \
            + box(b"moov", full(b"mvhd", b"\0" * 96) + video_trak() + audio_trak())
        log = []
        facts = vp.probe_ranges(self._reader(data, log), len(data))
        self.assertEqual((facts["container"], facts["video_codec"]), ("mp4", "h264"))
        self.assertLess(sum(log), len(data))     # never the whole file

    def test_a_read_failure_propagates(self):
        def read(start, length):
            raise OSError("drive down")
        with self.assertRaises(OSError):
            vp.probe_ranges(read, 1000)


# ─── The card: refusal line + bad_format refusal; the list the model sees ───

class CardAndList(unittest.TestCase):
    def test_card_refuses_from_drive_metadata_and_click_has_its_own_line(self):
        src = _read("core", "agent_actions.py")
        prepare = src[src.index("def _prepare_tiktok"):src.index("def _validate_tiktok_stored")]
        self.assertIn("video_probe.drive_meta_problems", prepare)
        self.assertLess(prepare.index("drive_meta_problems"), prepare.index("import check_grant"))
        # only the container blocks the card; resolution / length never do (owner's policy)
        self.assertIn('if p == "container"', prepare)
        # the click's own bad_format comes back from the job (tiktok_uploads → finish_deferred)
        self.assertIn('"bad_format"', _read("agents", "tiktok_content_agent.py"))
        self.assertIn("finish_deferred", _read("core", "tiktok_uploads.py"))
        from core import agent_actions as aa
        self.assertIn("bad_format", aa.ACTIONS["send_tiktok_to_inbox"]["refusals"])
        for text in list(aa.ACTIONS["send_tiktok_to_inbox"]["refusals"]["bad_format"].values()) \
                + list(aa._TIKTOK_BAD_FORMAT.values()):
            self.assertNotRegex(text, r"(?i)h\.?264|codec|ffmpeg|drive")
        self.assertIn("videoMediaMetadata", src[src.index("def _media_file"):src.index("def _v_media_video")])

    def test_media_turn_detects_video_messages(self):
        from core import client_state as cs
        for message in ("תשלח סרטון אחר לטיקטוק", "send the new video to TikTok", "שלח את הקליפ",
                        "envoyer la vidéo", "أرسل الفيديو", "отправь видео"):
            self.assertTrue(cs.media_turn(message), message)
        for message in ("כמה לידים היו אתמול?", "how is my campaign doing", ""):
            self.assertFalse(cs.media_turn(message), message)

    def test_a_video_turn_reads_the_media_folder_past_every_cache(self):
        from core import client_state as cs
        seen = []
        saved = (cs._media_folder, dict(cs._cache))
        try:
            cs._media_folder = lambda cid, fresh=False: seen.append(fresh) or {"exists": False}
            cs._cache[(5, "media_folder")] = (10 ** 12, {"exists": True, "videos": ["stale"]})
            snap = cs.snapshot(5)
            self.assertEqual(snap["media_folder"]["videos"], ["stale"])   # a normal turn: cache
            snap = cs.snapshot(5, fresh_media=True)
            self.assertEqual(snap["media_folder"], {"exists": False})     # a video turn: Drive, now
            self.assertEqual(seen[-1], True)
        finally:
            cs._media_folder = saved[0]
            cs._cache.clear()
            cs._cache.update(saved[1])

    def test_videos_carry_date_and_sent_mark(self):
        src = _read("core", "client_state.py")
        section = src[src.index("def _media_folder"):src.index("def _package")]
        self.assertIn('"modified"', section)
        self.assertIn('"sent_to_tiktok_on"', section)
        self.assertIn("tiktok_sent_files", section)
        self.assertIn("refresh=fresh", section)

    def test_sent_files_come_from_done_rows_newest_first(self):
        from core import agent_actions as aa

        class Q:
            def __init__(self, rows): self.rows, self.filters = rows, {}
            def select(self, *a): return self
            def eq(self, k, v): self.filters[k] = v; return self
            def order(self, *a, **k): return self
            def limit(self, n): return self
            def execute(self):
                rows = [r for r in self.rows if all(r.get(k) == v for k, v in self.filters.items())]
                return types.SimpleNamespace(data=sorted(rows, key=lambda r: r["created_at"], reverse=True))

        rows = [{"client_id": 27, "action": "send_tiktok_to_inbox", "status": "done", "params": {"drive_file_id": "A"},
                 "created_at": "2026-11-08T10:00:00", "finished_at": "2026-11-08T10:01:00"},
                {"client_id": 27, "action": "send_tiktok_to_inbox", "status": "done", "params": {"drive_file_id": "A"},
                 "created_at": "2026-11-09T09:00:00", "finished_at": "2026-11-09T09:01:00"},
                {"client_id": 27, "action": "send_tiktok_to_inbox", "status": "failed", "params": {"drive_file_id": "B"},
                 "created_at": "2026-11-09T10:00:00", "finished_at": None}]
        saved = aa._db
        aa._db = lambda: types.SimpleNamespace(table=lambda name: Q(rows))
        try:
            self.assertEqual(aa.tiktok_sent_files(27), {"A": "2026-11-09"})   # B failed: not sent
        finally:
            aa._db = saved

    def test_prompt_says_different_means_not_sent_before(self):
        src = _read("agents", "support_agent.py")
        self.assertIn("sent_to_tiktok_on", src)
        self.assertRegex(" ".join(src.split()), r"never the one sent before")
        # the snapshot (fresh media on a video message) is read in ONE place both chats use
        self.assertEqual(len(re.findall(r"_account_context\(client_id, client, part, message\)", src)), 1)
        self.assertEqual(src.count("ctx = _load_context(client_id, client, message, "), 2)


# ─── The diagnose route: admin-only, read-only ──────────────────────────────

class Diagnose(unittest.TestCase):
    def test_route_is_admin_only_and_read_only(self):
        api = _read("core", "api_server.py")
        self.assertIn('@app.get("/api/admin/tiktok/diagnose", dependencies=_admin_only)', api)
        agent = _read("agents", "tiktok_content_agent.py")
        body = agent[agent.index("def diagnose"):agent.index("# ─── Engagement")]
        for write in (".insert(", ".update(", ".upsert(", ".delete(", "init_inbox_upload", "upload_video_chunk",
                      "upload_video_stream"):
            self.assertNotIn(write, body)
        self.assertIn("get_post_status", agent[agent.index("def _status_reader"):agent.index("def diagnose")])
        self.assertNotIn("access_token", body.replace("_status_reader", ""))   # no token in the answer

    def test_script_never_prints_the_key(self):
        script = _read("scripts", "tiktok_diagnose.sh")
        self.assertIn("/api/admin/tiktok/diagnose", script)
        self.assertIn("unset AK", script)
        self.assertNotRegex(script, r"echo[^\n]*\$AK")


if __name__ == "__main__":
    unittest.main()
