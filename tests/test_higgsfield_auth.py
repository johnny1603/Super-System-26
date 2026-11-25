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
        self.assertEqual(body, {"prompt": "a coffee cup", "aspect_ratio": "4:3", "resolution": "1080p", "batch_size": 1})
        self.assertNotIn("model", body)
        self.assertEqual(http.calls[1][:2], ("GET", "https://api.higgsfield.ai/requests/req-1/status"))
        self.assertEqual(http.calls[1][2]["Authorization"], f"Key {KEY}")
        self.assertEqual(http.calls[2][1], "https://cdn.example/i.jpg")
        self.assertEqual(out["content"], b"PNGDATA")

    def test_a_4_5_post_image_is_4_5_not_square(self):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (768, 1024), (10, 20, 30)).save(buf, "PNG")      # what a 3:4 job returns
        http = self.use(FakeHttp())
        saved = mg._download
        mg._download = lambda url: buf.getvalue()
        try:
            out = mg.generate_image(KEY, "a coffee cup", "4:5")
        finally:
            mg._download = saved
        body = http.calls[0][3]
        self.assertEqual(body["aspect_ratio"], "3:4")                       # the model's nearest, never 1:1
        self.assertEqual((out["aspect_ratio"], out["cropped"]), ("3:4", True))
        self.assertEqual(Image.open(io.BytesIO(out["content"])).size, (768, 960))   # a true 4:5

    def test_fields_come_from_the_models_own_entry(self):
        for path, model in mg.IMAGE_MODELS.items():
            body = mg.image_payload("x", "9:16", path)
            self.assertEqual(set(body), {"prompt", "aspect_ratio", "resolution", model["count_field"]})
            self.assertIn(body["resolution"], model["resolutions"])
        self.assertEqual(mg.image_payload("x", "21:9")["aspect_ratio"], "16:9")
        with self.assertRaises(mg.UnverifiedModelPath):
            mg.image_payload("x", "1:1", "higgsfield-ai/soul/made-up")

    def test_only_the_verified_soul_values_are_ever_sent(self):
        self.assertEqual(list(mg.IMAGE_MODELS), ["higgsfield-ai/soul/standard"])          # v2: unverified
        soul = mg.IMAGE_MODELS["higgsfield-ai/soul/standard"]
        self.assertEqual(soul["aspects"], ("9:16", "16:9", "4:3", "3:4", "1:1"))
        self.assertEqual(soul["resolutions"], ("720p", "1080p"))
        self.assertEqual(soul["count_field"], "batch_size")
        for ar in ("4:5", "2:3", "3:2", "21:9", "1:2", "nonsense"):
            body = mg.image_payload("x", ar)
            self.assertIn(body["aspect_ratio"], soul["aspects"], ar)
            self.assertEqual((body["resolution"], body["batch_size"]), ("1080p", 1))   # a feed image: never 720p
        saved = mg.IMAGE_RESOLUTION
        mg.IMAGE_RESOLUTION = "2K"
        try:
            self.assertEqual(mg.image_payload("x", "1:1")["resolution"], "1080p")       # never out of range
        finally:
            mg.IMAGE_RESOLUTION = saved
        with self.assertRaises(mg.UnverifiedModelPath):
            mg.image_payload("x", "1:1", "higgsfield-ai/soul/v2/standard")

    def test_no_vendor_background_removal_left(self):
        src = open(os.path.join(ROOT, "core", "media_gen_service.py"), encoding="utf-8").read()
        for gone in ("def remove_background", "BG_REMOVE_PATH", "HIGGSFIELD_BG_REMOVE_PATH", '"remove-background"'):
            self.assertNotIn(gone, src)
        from core import background_removal as br
        saved = br.BG_MODEL_PATH
        br.BG_MODEL_PATH = os.path.join(ROOT, "no-such-model.onnx")
        try:
            with self.assertRaises(br.BackgroundModelMissing):
                br.remove_background("unused", "https://cdn.example/x.png", _download=lambda u: b"PNG")
        finally:
            br.BG_MODEL_PATH = saved
        docker = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
        self.assertIn("309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8", docker)
        self.assertIn("ENV BG_MODEL_PATH=/app/models/u2netp.onnx", docker)

    def test_video_flow(self):
        http = self.use(FakeHttp(status={"status": "completed", "video": {"url": "https://cdn.example/v.mp4"}}))
        mg.generate_video(KEY, "waves", "9:16")
        self.assertEqual(http.calls[0][1], "https://api.higgsfield.ai/minimax/hailuo-2.3/standard/text-to-video")
        self.assertEqual(http.calls[0][3], {"prompt": "waves", "duration": 6})
        self.assertEqual(http.calls[-1][1], "https://cdn.example/v.mp4")

    def test_status_url_on_another_host_is_not_followed(self):
        http = self.use(FakeHttp(create=(200, {"request_id": "r9", "status_url": "https://evil.example/r9"})))
        mg.generate_image(KEY, "x", "1:1")
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
        # An older caller's ONE value still works when it holds both parts
        self.use(FakeHttp(poll_status=404, status={}))
        out = vt.connect(27, "higgsfield", "a1b2c3d4-0000-4000-8000-0123456789ab\ns3cr3tS3cr3tS3cr3t\n")
        self.assertEqual(out["status"], "connected")
        self.assertEqual(self.stored[(27, "higgsfield")], KEY)
        with self.assertRaises(vt.ToolError) as e:
            vt.connect(27, "higgsfield", "onlyonepart")
        self.assertEqual(e.exception.code, "missing_key_secret")

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


KID, SECRET = KEY.split(":")


class TwoBoxes(unittest.TestCase):
    """The card's Key ID + Key Secret boxes (2026-11-22)."""

    def test_separate_fields(self):
        self.assertEqual(mg.split_credential(KID, SECRET), (KID, SECRET))

    def test_combined_paste_is_split(self):
        for kid, sec in ((KEY, ""), ("", KEY), (KEY, KEY), (f"Key {KEY}", ""), (KEY, SECRET)):
            with self.subTest(kid=kid, sec=sec):
                self.assertEqual(mg.split_credential(kid, sec), (KID, SECRET))
        self.assertEqual(mg.split_credential(f"{KID}:", SECRET), (KID, SECRET))

    def test_missing_field_is_named(self):
        for kid, sec, part in ((KID, "", "key_secret"), ("", SECRET, "key_id"), ("  ", " \n", "key_id"),
                               ('""', SECRET, "key_id"), (KID, "Bearer ", "key_secret")):
            with self.subTest(kid=kid, sec=sec):
                with self.assertRaises(mg.MissingPart) as e:
                    mg.split_credential(kid, sec)
                self.assertEqual(e.exception.part, part)

    def test_trimming_each_box(self):
        self.assertEqual(mg.split_credential(f'  "{KID}"\n', f"Key {SECRET} "), (KID, SECRET))
        self.assertEqual(mg.split_credential(f"Bearer {KID}", f"'{SECRET}'"), (KID, SECRET))
        self.assertEqual(mg.split_credential(f"​{KID}", f"{SECRET}﻿"), (KID, SECRET))

    def test_has_both_parts(self):
        self.assertTrue(mg.has_both_parts(KEY))
        for v in ("onlyonepart", f"{KID}:", f":{SECRET}", ""):
            self.assertFalse(mg.has_both_parts(v), v)


class VerifyOnSave(Tile):
    def accounts(self, has_secret=True):
        vt_accounts = {"higgsfield": {"has_secret": has_secret}}
        self.saved_acc = vt._accounts
        vt._accounts = lambda cid: vt_accounts
        self.addCleanup(lambda: setattr(vt, "_accounts", self.saved_acc))

    def test_verified_status_and_masked_hint(self):
        http = self.use(FakeHttp(poll_status=404, status={}))
        out = vt.connect(27, "higgsfield", key_id=f" {KID} ", key_secret=f'"{SECRET}"')
        self.assertEqual(out["status"], "connected")
        self.assertEqual(out["id_hint"], KID[:4])
        self.assertEqual(self.stored[(27, "higgsfield")], KEY)
        self.assertEqual(http.calls[0][2]["Authorization"], f"Key {KEY}")     # one cheap authenticated call
        self.assertEqual(len(http.calls), 1)
        self.accounts()
        self.assertEqual(vt.status(27, "higgsfield"), "connected")
        health = self.records[27]["health"]["higgsfield"]
        self.assertEqual(health["id_hint"], KID[:4])
        self.assertNotIn(SECRET, str(self.records))                          # never the secret in health

    def test_rejected_status_nothing_stored(self):
        self.use(FakeHttp(poll_status=401, status={"detail": "Invalid credentials"}))
        with self.assertRaises(vt.ToolError) as e:
            vt.connect(27, "higgsfield", key_id=KID, key_secret=SECRET)
        self.assertEqual(e.exception.code, "key_rejected")
        self.assertEqual(self.stored, {})

    def test_unreachable_check_stores_nothing(self):
        self.use(FakeHttp(poll_status=503, status={}))
        with self.assertRaises(vt.ToolError) as e:
            vt.connect(27, "higgsfield", key_id=KID, key_secret=SECRET)
        self.assertEqual(e.exception.code, "check_unreachable")
        self.assertEqual(self.stored, {})

    def test_missing_field_makes_no_call_and_stores_nothing(self):
        http = self.use(FakeHttp(poll_status=404, status={}))
        for kid, sec, code in ((KID, "", "missing_key_secret"), ("", SECRET, "missing_key_id")):
            with self.assertRaises(vt.ToolError) as e:
                vt.connect(27, "higgsfield", key_id=kid, key_secret=sec)
            self.assertEqual(e.exception.code, code)
        self.assertEqual(http.calls, [])
        self.assertEqual(self.stored, {})

    def test_combined_paste_in_one_box(self):
        self.use(FakeHttp(poll_status=404, status={}))
        vt.connect(27, "higgsfield", key_id=KEY, key_secret="")
        self.assertEqual(self.stored[(27, "higgsfield")], KEY)

    def test_old_single_part_value_needs_reconnect(self):
        # Client 27: a value saved before the two boxes; whatever health it had
        self.accounts()
        for health in ({}, {"state": "unverified"}, {"state": "key_rejected"}, {"state": "ok"}):
            with self.subTest(health=health):
                self.records[27] = {"health": {"higgsfield": health}} if health else {}
                self.assertEqual(vt.status(27, "higgsfield"), "needs_reconnect")
                self.assertFalse(vt.is_connected(27, "higgsfield"))
        # A 401 in real work afterwards does not make it look connected either
        vt.mark_rejected(27, "higgsfield")
        self.assertEqual(vt.status(27, "higgsfield"), "needs_reconnect")

    def test_a_401_after_verified_save_marks_rejected(self):
        self.use(FakeHttp(poll_status=404, status={}))
        vt.connect(27, "higgsfield", key_id=KID, key_secret=SECRET)
        self.accounts()
        vt.mark_rejected(27, "higgsfield")
        self.assertEqual(vt.status(27, "higgsfield"), "key_rejected")
        self.assertEqual(self.records[27]["health"]["higgsfield"]["id_hint"], KID[:4])

    def test_generation_refuses_an_old_single_part_key(self):
        import core.secrets_vault as sv
        saved = sv.use_vendor_key
        sv.use_vendor_key = lambda *a, **k: "onlyonepart"
        try:
            from agents import media_agent
            self.assertEqual(media_agent._generation_key(27), "")
            sv.use_vendor_key = lambda *a, **k: f" {KEY}\n"
            self.assertEqual(media_agent._generation_key(27), KEY)
        finally:
            sv.use_vendor_key = saved


class CardAndScript(unittest.TestCase):
    def setUp(self):
        self.html = open(os.path.join(ROOT, "dashboard", "client", "index.html"), encoding="utf-8").read()

    def test_two_inputs_secret_masked_with_toggle(self):
        self.assertIn('id="toolKeyId-higgsfield"', self.html)
        self.assertRegex(self.html, r'id="toolKeySecret-higgsfield"[^>]*type="password"')
        self.assertIn('onclick="toggleHfSecret()"', self.html)
        self.assertNotIn('id="toolKey-higgsfield"', self.html)
        self.assertIn("מזהה מפתח (Key ID)", self.html)
        self.assertIn("סוד המפתח (Key Secret)", self.html)

    def test_title_and_statuses(self):
        self.assertIn("יצירת חומרי מדיה ו-AI", self.html)
        self.assertNotIn("הדמות שבסרטונים ותמונות AI", self.html)
        for text in ("מחובר ומאומת", "נדחה: בדוק את שני הערכים", "נדרש חיבור מחדש"):
            self.assertIn(text, self.html)
        self.assertEqual(vt.client_label("higgsfield"), "יצירת חומרי מדיה ו-AI")

    def test_api_takes_two_fields(self):
        src = open(os.path.join(ROOT, "core", "api_server.py"), encoding="utf-8").read()
        self.assertIn("key_id=req.key_id, key_secret=req.key_secret", src)
        for code in ("missing_key_id", "missing_key_secret", "check_unreachable"):
            self.assertIn(f'"{code}":', src)
            self.assertIn(f"tool_err_{code}:", self.html)

    def test_check_script_prints_shape_only(self):
        sh = open(os.path.join(ROOT, "scripts", "higgsfield_check.sh"), encoding="utf-8").read()
        self.assertIn("not_sent_single_part", sh)
        self.assertIn("Key Secret present", sh)
        self.assertNotRegex(sh, r"print\([^)]*\braw\b")


if __name__ == "__main__":
    unittest.main()
