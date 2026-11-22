"""Higgsfield auth + request shape (2026-11-22, client 27's 401 "Invalid credentials").

What must stay true (docs.higgsfield.ai, read 2026-11-22):
- every call sends `Authorization: Key KEY_ID:KEY_SECRET` to https://api.higgsfield.ai,
  never "Bearer", built ONLY from the one value the client pasted in the tile —
  no env/global key of ours, no second field;
- that value is cleaned the same way on save and on read (quotes, whitespace,
  a pasted prefix; id + secret on two lines → "id:secret");
- image = POST {base}/higgsfield-ai/soul/standard, poll the status_url
  (only on our base), result images[0].url; video = video.url;
- 401/403 → CredentialsRejected → the tile shows "key rejected"; a job that
  ran → "connected"; the tile's check spends no credits.
"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")
for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}),
                      ("httpx", {"Response": object, "Client": object, "HTTPError": Exception,
                                 "HTTPStatusError": Exception, "TimeoutException": Exception,
                                 "RequestError": Exception, "get": None, "post": None}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from core import media_gen_service as mg      # noqa: E402
from core import vendor_tools as vt           # noqa: E402

KEY = "a1b2c3d4-0000-4000-8000-0123456789ab:s3cr3tS3cr3tS3cr3t"


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body, self.text, self.content = status, body or {}, "", b"PNGDATA"

    def json(self):
        return self._body


class FakeHttp:
    def __init__(self, create=None, status=None, poll_status=200):
        self.calls = []
        self.create = create if create is not None else (200, {
            "status": "queued", "request_id": "req-1",
            "status_url": "https://api.higgsfield.ai/requests/req-1/status"})
        self.status = status if status is not None else {"status": "completed",
                                                           "images": [{"url": "https://cdn.example/i.jpg"}]}
        self.poll_status = poll_status

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, headers, json))
        return Resp(*self.create)

    def get(self, url, headers=None, timeout=None, follow_redirects=False):
        self.calls.append(("GET", url, headers, None))
        if url.startswith("https://cdn."):
            return Resp(200)
        return Resp(self.poll_status, self.status)


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = (mg.httpx, mg._count, mg.time.sleep, vt.httpx)
        mg._count = lambda *a: None
        mg.time.sleep = lambda s: None

    def tearDown(self):
        mg.httpx, mg._count, mg.time.sleep, vt.httpx = self.saved

    def use(self, http):
        mg.httpx = http
        vt.httpx = http
        return http


class TheOnePastedKey(unittest.TestCase):
    def test_cleaning(self):
        cases = {
            KEY: KEY,
            f"  {KEY}\n": KEY,
            f'"{KEY}"': KEY,
            f"Key {KEY}": KEY,
            f"Bearer {KEY}": KEY,
            f"Authorization: Key {KEY}": KEY,
            "a1b2c3d4-0000-4000-8000-0123456789ab\ns3cr3tS3cr3tS3cr3t": KEY,     # two lines from the console
            "a1b2c3d4-0000-4000-8000-0123456789ab  s3cr3tS3cr3tS3cr3t": KEY,
            "a1b2c3d4-0000-4000-8000-0123456789ab : s3cr3tS3cr3tS3cr3t": KEY,
            "​onlyonepart​": "onlyonepart",                            # kept as pasted
            "": "",
            None: "",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(mg.normalize_key(raw), want)

    def test_header_is_key_scheme_never_bearer(self):
        self.assertEqual(mg._headers(f" Bearer {KEY} ")["Authorization"], f"Key {KEY}")
        src = open(os.path.join(ROOT, "core", "media_gen_service.py"), encoding="utf-8").read()
        self.assertNotIn('f"Bearer', src)
        self.assertEqual(mg.API_BASE, "https://api.higgsfield.ai")

    def test_no_key_of_ours(self):
        src = open(os.path.join(ROOT, "core", "media_gen_service.py"), encoding="utf-8").read()
        for env in ("HIGGSFIELD_API_KEY", "HF_API_KEY", "HF_KEY", "HF_API_SECRET", "MEDIA_API_BASE\""):
            self.assertNotIn(f'environ.get("{env}', src)

    def test_shape_reveals_no_characters(self):
        shape = mg.key_shape(KEY)
        self.assertTrue(shape["has_id_and_secret"])
        self.assertEqual(shape["part_lengths"], [36, 18])
        self.assertNotIn("s3cr", str(shape))
        self.assertFalse(mg.key_shape("onepart")["has_id_and_secret"])


class Requests(Base):
    def test_image_flow(self):
        http = self.use(FakeHttp())
        out = mg.generate_image(f'"{KEY}"', "a coffee cup", "4:3")
        method, url, headers, body = http.calls[0]
        self.assertEqual((method, url), ("POST", "https://api.higgsfield.ai/higgsfield-ai/soul/standard"))
        self.assertEqual(headers["Authorization"], f"Key {KEY}")
        self.assertIn("Idempotency-Key", headers)
        self.assertEqual(body, {"prompt": "a coffee cup", "aspect_ratio": "4:3", "resolution": "720p", "batch_size": 1})
        self.assertNotIn("model", body)
        self.assertEqual(http.calls[1][:2], ("GET", "https://api.higgsfield.ai/requests/req-1/status"))
        self.assertEqual(http.calls[1][2]["Authorization"], f"Key {KEY}")
        self.assertEqual(http.calls[2][1], "https://cdn.example/i.jpg")
        self.assertEqual(out["content"], b"PNGDATA")

    def test_video_flow(self):
        http = self.use(FakeHttp(status={"status": "completed", "video": {"url": "https://cdn.example/v.mp4"}}))
        mg.generate_video(KEY, "waves", "9:16")
        self.assertEqual(http.calls[0][1], "https://api.higgsfield.ai/minimax/hailuo-2.3/standard/text-to-video")
        self.assertEqual(http.calls[0][3], {"prompt": "waves", "duration": 6})
        self.assertEqual(http.calls[-1][1], "https://cdn.example/v.mp4")

    def test_status_url_on_another_host_is_not_followed(self):
        http = self.use(FakeHttp(create=(200, {"request_id": "r9", "status_url": "https://evil.example/r9"})))
        mg.generate_image(KEY, "x")
        self.assertEqual(http.calls[1][1], "https://api.higgsfield.ai/requests/r9/status")

    def test_401_is_credentials_rejected(self):
        self.use(FakeHttp(create=(401, {"detail": "Invalid credentials"})))
        with self.assertRaises(mg.CredentialsRejected):
            mg.generate_image(KEY, "x")

    def test_nsfw_and_failed_end_the_wait(self):
        for status in ("nsfw", "failed", "canceled"):
            self.use(FakeHttp(status={"status": status}))
            with self.assertRaises(RuntimeError):
                mg.generate_image(KEY, "x")


class Tile(Base):
    def setUp(self):
        super().setUp()
        self.saved_vt = (vt._record, vt._save, vt._log)
        self.records = {}
        vt._record = lambda cid: dict(self.records.get(cid, {}))
        vt._save = lambda cid, rec: self.records.__setitem__(cid, rec)
        vt._log = lambda *a, **k: None
        self.stored = {}
        import agents.client_agent as ca
        self.ca = ca
        self.saved_ca = getattr(ca, "upsert_account", None)
        ca.upsert_account = lambda cid, platform, account_id="", secret="", status="active": \
            self.stored.__setitem__((cid, platform), secret)

    def tearDown(self):
        vt._record, vt._save, vt._log = self.saved_vt
        if self.saved_ca is not None:
            self.ca.upsert_account = self.saved_ca
        super().tearDown()

    def test_check_is_free_and_maps_statuses(self):
        for status, want in ((404, "ok"), (422, "ok"), (401, "rejected"), (403, "rejected"), (500, "unknown")):
            http = self.use(FakeHttp(poll_status=status, status={"detail": "x"}))
            self.assertEqual(vt._check_higgsfield(KEY), want, status)
            self.assertEqual([c[0] for c in http.calls], ["GET"])                     # never a generation
            self.assertTrue(http.calls[0][1].startswith("https://api.higgsfield.ai/requests/"))
            self.assertEqual(http.calls[0][2]["Authorization"], f"Key {KEY}")

    def test_connect_stores_the_cleaned_single_value(self):
        self.use(FakeHttp(poll_status=404, status={}))
        out = vt.connect(27, "higgsfield", "a1b2c3d4-0000-4000-8000-0123456789ab\ns3cr3tS3cr3tS3cr3t\n")
        self.assertEqual(out["status"], "connected")
        self.assertEqual(self.stored[(27, "higgsfield")], KEY)

    def test_a_refused_key_is_not_stored(self):
        self.use(FakeHttp(poll_status=401, status={"detail": "Invalid credentials"}))
        with self.assertRaises(vt.ToolError) as e:
            vt.connect(27, "higgsfield", KEY)
        self.assertEqual(e.exception.code, "key_rejected")
        self.assertEqual(self.stored, {})

    def test_job_outcome_reaches_the_tile(self):
        vt.mark_rejected(27, "higgsfield")
        self.assertEqual(self.records[27]["health"]["higgsfield"]["state"], "key_rejected")
        vt.mark_working(27, "higgsfield")
        self.assertEqual(self.records[27]["health"]["higgsfield"]["state"], "ok")


if __name__ == "__main__":
    unittest.main()
