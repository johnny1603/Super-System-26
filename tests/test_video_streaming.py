"""Streamed Drive → YouTube upload: core/youtube_service.upload_video_stream,
core/drive_service.read_range, youtube_content_agent.publish's guards, and the
one size constant (core/video_limits).

Stdlib only: httpx is replaced by a fake YouTube resumable session that
checks every byte it receives, and a fake Drive that serves ranges.

Run from the repo root:  python tests/test_video_streaming.py -v
"""
import contextlib
import inspect
import os
import sys
import tracemalloc
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import video_limits as vl   # noqa: E402  (stdlib only)

CHUNK = vl.UPLOAD_CHUNK_BYTES
MiB = 1024 * 1024


def pattern(offset: int, length: int) -> bytes:
    """Deterministic content: every byte is a function of its position's
    256 KiB block, so a chunk sent at the wrong offset is detected."""
    out, pos = bytearray(), offset
    while pos < offset + length:
        block_end = min(offset + length, (pos // 262144 + 1) * 262144)
        out += bytes([(pos // 262144) % 251]) * (block_end - pos)
        pos = block_end
    return bytes(out)


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


class FakeYouTube:
    """A resumable session: stores bytes only at the expected offset, verifies
    them against pattern(), and answers 308 / 200 like the real protocol.
    `faults` maps a PUT index (0-based, data PUTs and status PUTs alike) to
    "500" | "503" | "transport" | "403" | ("partial", n_bytes)."""

    def __init__(self, total, faults=None):
        self.total, self.stored, self.faults = total, 0, dict(faults or {})
        self.puts, self.metadata, self.chunk_sizes, self.corrupt = 0, None, [], False

    def post(self, url, params=None, headers=None, json=None, timeout=None):
        self.metadata, self.init_headers = json, headers
        return Response(200, {"location": "https://upload.example/session-1"})

    def _range(self):
        return {"range": f"bytes=0-{self.stored - 1}"} if self.stored else {}

    def put(self, url, content=b"", headers=None, timeout=None):
        index, self.puts = self.puts, self.puts + 1
        fault = self.faults.get(index)
        if fault == "transport":
            raise _TransportError("connection reset")
        if fault in ("500", "503", "403"):
            return Response(int(fault), text="fault")
        content_range = headers["Content-Range"]
        if content_range.startswith("bytes */"):
            return Response(200, body={"id": "vid1"}) if self.stored == self.total \
                else Response(308, self._range())
        span, total = content_range[len("bytes "):].split("/")
        start, end = (int(x) for x in span.split("-"))
        assert int(total) == self.total and end - start + 1 == len(content)
        if start != self.stored:
            return Response(400, text=f"expected offset {self.stored}, got {start}")
        self.chunk_sizes.append(len(content))
        keep = fault[1] if isinstance(fault, tuple) else len(content)
        if content[:keep] != pattern(start, keep):
            self.corrupt = True
        self.stored += keep
        if self.stored == self.total:
            return Response(200, body={"id": "vid1"})
        return Response(308, self._range())


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


_NAMES = ("httpx", "agents.keys_agent", "core.api_call_counters", "core.google_ads_service",
          "core.youtube_service", "core.drive_service")
_saved = {n: sys.modules.get(n) for n in _NAMES}
FAKE_HTTPX = _module("httpx", HTTPStatusError=_HTTPStatusError, TransportError=_TransportError,
                     Timeout=lambda **k: None, Response=object, post=None, put=None, get=None, stream=None)
sys.modules.update({
    "httpx": FAKE_HTTPX,
    "agents.keys_agent": _module("agents.keys_agent", get_key=lambda k: "x"),
    "core.api_call_counters": _module("core.api_call_counters", increment_call_counter=lambda *a, **k: 1),
    "core.google_ads_service": _module("core.google_ads_service", OAUTH_CONSENT_URL="https://consent",
                                       _access_token=lambda token: "access", exchange_code=lambda *a: {}),
})
for _n in ("core.youtube_service", "core.drive_service"):
    sys.modules.pop(_n, None)
from core import youtube_service as yt   # noqa: E402
from core import drive_service as ds     # noqa: E402


class Upload(unittest.TestCase):
    def setUp(self):
        self.sleeps, self.reads = [], []

    def run_upload(self, total, faults=None):
        server = FakeYouTube(total, faults)
        FAKE_HTTPX.post, FAKE_HTTPX.put = server.post, server.put

        def read_chunk(offset, length):
            self.reads.append((offset, length))
            return pattern(offset, length)
        try:
            result = yt.upload_video_stream("refresh", read_chunk, total, "Title", _sleep=self.sleeps.append)
        except yt.UploadFailed as e:
            result = e
        return result, server

    def test_chunks_are_8mib_aligned_and_the_bytes_arrive_intact(self):
        total = 3 * CHUNK + 12345
        result, server = self.run_upload(total)
        self.assertEqual(result, {"id": "vid1"})
        self.assertEqual(server.stored, total)
        self.assertFalse(server.corrupt)
        self.assertEqual(server.chunk_sizes, [CHUNK, CHUNK, CHUNK, 12345])
        for size in server.chunk_sizes[:-1]:
            self.assertEqual(size % (256 * 1024), 0)

    def test_always_private_and_no_privacy_parameter_exists(self):
        _, server = self.run_upload(CHUNK)
        self.assertEqual(server.metadata["status"]["privacyStatus"], "private")
        self.assertEqual(server.init_headers["X-Upload-Content-Length"], str(CHUNK))
        params = inspect.signature(yt.upload_video_stream).parameters
        self.assertFalse([p for p in params if "privacy" in p.lower()])
        self.assertFalse(hasattr(yt, "upload_video"))     # the whole-file version is gone

    def test_resumes_after_a_5xx(self):
        # PUT #1 (second chunk) fails; the status query that follows (#2) says where to resume
        result, server = self.run_upload(3 * CHUNK, faults={1: "503"})
        self.assertEqual(result, {"id": "vid1"})
        self.assertFalse(server.corrupt)
        self.assertEqual(self.reads.count((CHUNK, CHUNK)), 2)   # that chunk was re-read, nothing else
        self.assertEqual(len(self.sleeps), 1)

    def test_resumes_after_a_dropped_connection(self):
        result, server = self.run_upload(2 * CHUNK + 5, faults={0: "transport"})
        self.assertEqual(result, {"id": "vid1"})
        self.assertFalse(server.corrupt)

    def test_resumes_from_the_servers_offset_after_a_partial_store(self):
        # YouTube kept only 1 MiB of the first chunk: the next read starts there
        result, server = self.run_upload(2 * CHUNK, faults={0: ("partial", MiB)})
        self.assertEqual(result, {"id": "vid1"})
        self.assertFalse(server.corrupt)
        self.assertEqual(self.reads[1], (MiB, CHUNK))

    def test_gives_up_after_bounded_retries(self):
        faults = {i: "503" for i in range(1, 50)}
        result, server = self.run_upload(2 * CHUNK, faults=faults)
        self.assertIsInstance(result, yt.UploadFailed)
        self.assertEqual(len(self.sleeps), yt.MAX_UPLOAD_RETRIES)
        self.assertTrue(all(s <= 30 for s in self.sleeps))

    def test_a_4xx_is_not_retried(self):
        result, _ = self.run_upload(2 * CHUNK, faults={1: "403"})
        self.assertIsInstance(result, yt.UploadFailed)
        self.assertEqual(self.sleeps, [])

    def test_size_cap_refused_before_anything_is_sent(self):
        for total in (0, vl.VIDEO_UPLOAD_MAX_BYTES + 1):
            server = FakeYouTube(total)
            FAKE_HTTPX.post, FAKE_HTTPX.put = server.post, server.put
            with self.assertRaises(yt.UploadFailed):
                yt.upload_video_stream("refresh", pattern, total, "t", _sleep=self.sleeps.append)
            self.assertIsNone(server.metadata)

    def test_memory_does_not_grow_with_file_size(self):
        peaks = []
        for total in (4 * CHUNK, 16 * CHUNK):      # 32 MiB and 128 MiB
            server = FakeYouTube(total)
            FAKE_HTTPX.post, FAKE_HTTPX.put = server.post, server.put
            tracemalloc.start()
            yt.upload_video_stream("refresh", pattern, total, "t", _sleep=self.sleeps.append)
            peaks.append(tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()
            self.assertEqual(server.stored, total)
        self.assertLess(peaks[1], 4 * CHUNK)                  # far below the 128 MiB file
        self.assertLess(peaks[1], peaks[0] * 1.5 + MiB)      # 4x the file, ~same peak


# ─── Drive range reads ───────────────────────────────────────────────────────

class FakeDriveStream:
    def __init__(self, plan):
        self.plan, self.calls, self.pieces_served = list(plan), [], 0

    @contextlib.contextmanager
    def stream(self, method, url, headers=None, params=None, follow_redirects=None, timeout=None):
        self.calls.append(headers["Range"])
        kind = self.plan.pop(0)
        start, end = (int(x) for x in headers["Range"][len("bytes="):].split("-"))
        fake = self

        class R:
            text, request = "drive error", types.SimpleNamespace(method="GET")

            def read(self):
                return b""

            def iter_bytes(self):
                if kind == "ignore_range":         # 200 with the WHOLE (huge) file
                    for i in range(10_000):
                        fake.pieces_served += 1
                        yield pattern(i * 65536, 65536)
                elif kind == "short":
                    yield pattern(start, (end - start + 1) // 2)
                else:
                    data = pattern(start, end - start + 1)
                    for i in range(0, len(data), 65536):
                        fake.pieces_served += 1
                        yield data[i:i + 65536]
        r = R()
        r.status_code = {"ok": 206, "short": 206, "ignore_range": 200, "500": 500, "404": 404}[kind]
        r.response = r
        yield r


class DriveRange(unittest.TestCase):
    def setUp(self):
        self.saved_headers = ds._headers
        ds._headers = lambda: {}
        self.sleeps = []

    def tearDown(self):
        ds._headers = self.saved_headers

    def read(self, plan, start, length):
        fake = FakeDriveStream(plan)
        FAKE_HTTPX.stream = fake.stream
        return ds.read_range("file1", start, length, _sleep=self.sleeps.append), fake

    def test_reads_exactly_the_range(self):
        data, fake = self.read(["ok"], 3 * MiB, MiB)
        self.assertEqual(data, pattern(3 * MiB, MiB))
        self.assertEqual(fake.calls, [f"bytes={3 * MiB}-{4 * MiB - 1}"])

    def test_a_server_ignoring_range_costs_one_chunk_not_the_file(self):
        data, fake = self.read(["ignore_range"], 0, MiB)
        self.assertEqual(len(data), MiB)
        self.assertLessEqual(fake.pieces_served, MiB // 65536 + 1)   # stopped reading

    def test_5xx_and_short_reads_are_retried(self):
        data, fake = self.read(["500", "short", "ok"], 0, MiB)
        self.assertEqual(data, pattern(0, MiB))
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_4xx_is_raised_at_once(self):
        with self.assertRaises(_HTTPStatusError):
            self.read(["404"], 0, MiB)
        self.assertEqual(self.sleeps, [])


# ─── publish(): the root guard, whatever path called it ─────────────────────

class Publish(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {n: sys.modules.get(n) for n in ("supabase", "core.youtube_service", "core.agent_base",
                                                     "core.secrets_vault", "core.drive_service",
                                                     "agents.client_agent", "agents.youtube_content_agent")}
        cls.uploads, cls.metas = [], {}
        sys.modules.update({
            "supabase": _module("supabase", create_client=lambda *a: None),
            "core.youtube_service": _module("core.youtube_service", upload_video_stream=lambda *a, **k:
                                            cls.uploads.append((a, k)) or {"id": "vid9"}),
            "core.agent_base": _module("core.agent_base", agent_alert=lambda *a: None,
                                       log_step=lambda *a, **k: None, timed_step=lambda a, s, fn: fn()),
            "core.secrets_vault": _module("core.secrets_vault", hydrate=lambda r: r),
            "core.drive_service": _module("core.drive_service", get_meta=lambda fid, fields="": cls.metas[fid],
                                          read_range=lambda fid, off, n: pattern(off, n)),
            "agents.client_agent": _module("agents.client_agent", log_activity=lambda *a, **k: None),
        })
        # `from core import youtube_service` reads the PACKAGE attribute, which
        # the real module (imported above for the Upload tests) already set
        import core
        cls.saved_attrs = {n: getattr(core, n, None) for n in ("youtube_service", "drive_service")}
        core.youtube_service = sys.modules["core.youtube_service"]
        core.drive_service = sys.modules["core.drive_service"]
        sys.modules.pop("agents.youtube_content_agent", None)
        from agents import youtube_content_agent as agent
        agent._get_connection = lambda cid: {"access_token": "refresh", "account_id": "chan"}
        cls.agent = agent

    @classmethod
    def tearDownClass(cls):
        import core
        for name, value in cls.saved_attrs.items():
            setattr(core, name, value)
        for name, module in cls.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self):
        self.uploads.clear()

    def test_streams_with_the_drive_size(self):
        self.metas["v"] = {"mimeType": "video/mp4", "size": str(300 * MiB)}
        result = self.agent.publish(40, {"drive_file_id": "v", "title": "T"})
        self.assertTrue(result["success"])
        (args, kwargs), = self.uploads
        self.assertEqual(args[2], 300 * MiB)                         # total size from Drive
        self.assertEqual(args[1](CHUNK, 10), pattern(CHUNK, 10))     # reader = Drive range reads
        self.assertNotIn("privacy_status", kwargs)

    def test_over_the_limit_or_not_a_video_is_refused_before_uploading(self):
        self.metas.update({"big": {"mimeType": "video/mp4", "size": str(vl.VIDEO_UPLOAD_MAX_BYTES + 1)},
                           "img": {"mimeType": "image/png", "size": "100"},
                           "bin": {"mimeType": "video/mp4", "size": "100", "trashed": True}})
        for fid in ("big", "img", "bin"):
            self.assertFalse(self.agent.publish(40, {"drive_file_id": fid, "title": "T"})["success"], fid)
        self.assertEqual(self.uploads, [])


class OneConstant(unittest.TestCase):
    def test_every_reader_uses_video_limits(self):
        for rel in ("core/verification_demo.py", "core/client_state.py", "core/agent_actions.py",
                    "agents/youtube_content_agent.py"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                src = f.read()
            self.assertIn("video_limits", src, rel)
            self.assertNotIn("DEMO_VIDEO_MAX_MB", src, rel)

    def test_limit_is_documented_and_chunk_aligned(self):
        self.assertEqual(vl.VIDEO_UPLOAD_MAX_MB, 512)
        self.assertEqual(vl.UPLOAD_CHUNK_BYTES % (256 * 1024), 0)
        self.assertTrue(vl.fits(vl.VIDEO_UPLOAD_MAX_BYTES))
        self.assertFalse(vl.fits(vl.VIDEO_UPLOAD_MAX_BYTES + 1))
        self.assertFalse(vl.fits(None))


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
