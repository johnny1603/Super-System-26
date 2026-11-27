"""The YouTube upload ceiling is seen before it is hit (core/youtube_quota).

Run: python tests/test_youtube_quota.py
"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if "core.youtube_service" not in sys.modules:           # the real one imports httpx + the keys agent
    stub = types.ModuleType("core.youtube_service")
    stub.DAILY_UPLOAD_LIMIT = 90
    sys.modules["core.youtube_service"] = stub

from core import youtube_quota as yq  # noqa: E402


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def counts(uploads, searches=0):
    return lambda platform: {yq.PLATFORM: uploads, yq.SEARCH_PLATFORM: searches}[platform]


class Quota(unittest.TestCase):
    def setUp(self):
        self.saved = yq.limit
        yq.limit = lambda: 90

    def tearDown(self):
        yq.limit = self.saved

    def test_remaining_uploads_come_from_the_days_tally(self):
        s = yq.status(counts(30, 4))
        self.assertEqual((s["used"], s["limit"], s["remaining"], s["alert_at"]), (30, 90, 60, 45))
        self.assertEqual((s["searches"], s["search_units"]), (4, 400))
        self.assertTrue(s["known"])
        self.assertIn("not a figure from Google", s["source"])
        self.assertEqual(yq.status(counts(200))["remaining"], 0)               # never negative

    def test_an_unreadable_tally_is_unknown_never_a_guessed_zero(self):
        s = yq.status(lambda platform: None)
        self.assertEqual((s["known"], s["used"], s["remaining"]), (False, None, None))
        self.assertFalse(yq.require(1, lambda platform: None)["known"])          # not a refusal: the upload brake stands

    def test_an_order_that_does_not_fit_fails_by_name(self):
        self.assertEqual(yq.require(1, counts(89))["remaining"], 1)
        with self.assertRaises(yq.QuotaExceeded) as caught:
            yq.require(1, counts(90))
        self.assertIn("90 of 90 used, 0 left, 1 needed", str(caught.exception))
        with self.assertRaises(yq.QuotaExceeded):
            yq.require(3, counts(88))

    def test_the_owner_is_alerted_once_at_half(self):
        sent = []
        for count in (1, 44):
            yq.after_upload(count, sent.append)
        self.assertEqual(sent, [])
        yq.after_upload(45, sent.append)
        self.assertEqual(len(sent), 1)
        self.assertIn("45 of 90", sent[0])
        self.assertIn("compliance audit", sent[0])
        for count in (46, 60, 90):
            yq.after_upload(count, sent.append)                                   # not again the same day
        self.assertEqual(len(sent), 1)

        def broken(text):
            raise RuntimeError("sink down")
        yq.after_upload(45, broken)                                               # never breaks an upload

    def test_wiring(self):
        service = _src("core", "youtube_service.py")
        upload = service[service.index("def upload_video_stream("):]
        self.assertLess(upload.index('increment_call_counter("youtube"'), upload.index("youtube_quota.after_upload(count)"))
        self.assertIn("return int(youtube_service.DAILY_UPLOAD_LIMIT)", _src("core", "youtube_quota.py"))   # one limit
        server = _src("core", "api_server.py")
        route = server[server.index('@app.get("/api/admin/youtube/quota")'):][:500]
        self.assertIn("_require_admin(request)", route)
        self.assertIn("youtube_quota.status()", route)
        page = _src("dashboard", "admin", "connections", "index.html")
        self.assertIn('api("/api/admin/youtube/quota")', page)
        self.assertIn("d.remaining", page)


if __name__ == "__main__":
    unittest.main()
