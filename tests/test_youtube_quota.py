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
    stub.DAILY_SEARCH_LIMIT = 90
    sys.modules["core.youtube_service"] = stub

from core import youtube_quota as yq  # noqa: E402


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def counts(uploads, searches=0):
    return lambda platform: {"youtube": uploads, "youtube_search": searches}[platform]


class Quota(unittest.TestCase):
    def setUp(self):
        self.saved = yq.limit
        yq.limit = lambda bucket=yq.UPLOAD: 90

    def tearDown(self):
        yq.limit = self.saved

    def test_remaining_uploads_come_from_the_days_tally(self):
        s = yq.status(counts(30, 4))
        self.assertEqual((s["used"], s["limit"], s["remaining"], s["alert_at"]), (30, 90, 60, 45))
        self.assertEqual((s["search"]["used"], s["search"]["remaining"], s["search"]["alert_at"]), (4, 86, 45))
        self.assertEqual((s["google_cap"], s["search"]["google_cap"]), (100, 100))
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

    def test_searches_have_their_own_bucket_refusal_and_alert(self):
        self.assertEqual(yq.require(1, counts(90, 89), bucket=yq.SEARCH)["remaining"], 1)   # full uploads do not block
        with self.assertRaises(yq.QuotaExceeded) as caught:
            yq.require(1, counts(0, 90), bucket=yq.SEARCH)
        self.assertEqual(caught.exception.bucket, yq.SEARCH)
        self.assertIn("searches today: 90 of 90 used, 0 left", str(caught.exception))
        self.assertEqual(yq.require(1, counts(0, 90))["remaining"], 90)                     # nor searches uploads
        sent = []
        for count in (44, 45, 46):
            yq.after_call(yq.SEARCH, count, sent.append)
        self.assertEqual(len(sent), 1)
        self.assertIn("searches", sent[0])

    def test_the_day_is_googles_pacific_day_not_utc(self):
        from datetime import datetime, timezone
        at = lambda *a: datetime(*a, tzinfo=timezone.utc)                          # noqa: E731
        # winter (PST, UTC-8): 07:59 UTC is still yesterday in California
        self.assertEqual(yq.pacific_day(at(2026, 11, 28, 7, 59)), "2026-11-27")
        self.assertEqual(yq.pacific_day(at(2026, 11, 28, 8, 0)), "2026-11-28")
        # summer (PDT, UTC-7)
        self.assertEqual(yq.pacific_day(at(2026, 7, 10, 6, 59)), "2026-07-09")
        self.assertEqual(yq.pacific_day(at(2026, 7, 10, 7, 0)), "2026-07-10")
        # the switches: 8 March 2026 02:00 PST, 1 November 2026 02:00 PDT
        self.assertEqual(yq.pacific_day(at(2026, 3, 9, 7, 30)), "2026-03-09")     # PDT already
        self.assertEqual(yq.pacific_day(at(2026, 3, 8, 7, 30)), "2026-03-07")     # still PST
        self.assertEqual(yq.pacific_day(at(2026, 11, 1, 7, 30)), "2026-11-01")    # still PDT
        self.assertEqual(yq.pacific_day(at(2026, 11, 2, 7, 30)), "2026-11-01")    # PST again
        # the count is written and read on that same day
        written = []
        import core.api_call_counters as counters
        saved = counters.increment_call_counter
        counters.increment_call_counter = lambda platform, window_days=1, day=None: written.append((platform, day)) or 7
        try:
            self.assertEqual(yq.count_call(yq.SEARCH), 7)
        finally:
            counters.increment_call_counter = saved
        self.assertEqual(written, [("youtube_search", yq.pacific_day())])
        source = _src("core", "youtube_quota.py")
        self.assertIn('.eq("call_date", pacific_day())', source)
        self.assertNotIn("time.strftime", source)
        self.assertIn("today = day or time.strftime", _src("core", "api_call_counters.py"))

    def test_search_list_has_one_call_site_and_it_is_refused_first(self):
        import glob
        import re
        hits = []
        for path in glob.glob(os.path.join(ROOT, "core", "*.py")) + glob.glob(os.path.join(ROOT, "agents", "*.py")):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if re.search(r'_get_with_key\(\s*"search"|youtube/v3/search', text):
                hits.append(os.path.basename(path))
        self.assertEqual(hits, ["youtube_service.py"])
        service = _src("core", "youtube_service.py")
        self.assertEqual(len(re.findall(r'_get_with_key\(\s*"search"', service)), 1)
        search = service[service.index("def search_videos("):]
        order = [search.index(s) for s in ("youtube_quota.require(1, bucket=youtube_quota.SEARCH)",
                                           "youtube_quota.count_call(youtube_quota.SEARCH)",
                                           "youtube_quota.after_call(youtube_quota.SEARCH, count)",
                                           '_get_with_key("search"')]
        self.assertEqual(order, sorted(order))
        # every caller of search_videos is known; a new one must be added here on purpose
        callers = []
        for path in glob.glob(os.path.join(ROOT, "core", "*.py")) + glob.glob(os.path.join(ROOT, "agents", "*.py")):
            with open(path, encoding="utf-8") as f:
                if "youtube_service.search_videos(" in f.read():
                    callers.append(os.path.basename(path))
        self.assertEqual(sorted(callers), ["competitor_research.py", "youtube_metadata.py"])
        research = _src("core", "competitor_research.py")
        niche = research[research.index("def _youtube_niche_videos("):]
        self.assertLess(niche.index("youtube_quota.require(1, bucket=youtube_quota.SEARCH)"),
                        niche.index("safe_claude_json_call("))                    # no LLM call for a search that cannot run

    def test_wiring(self):
        service = _src("core", "youtube_service.py")
        upload = service[service.index("def upload_video_stream("):]
        self.assertLess(upload.index("youtube_quota.count_call(youtube_quota.UPLOAD)"),
                        upload.index("youtube_quota.after_call(youtube_quota.UPLOAD, count)"))
        self.assertNotIn("increment_call_counter", service)                       # counted only through youtube_quota
        self.assertIn("return int(getattr(youtube_service, BUCKETS[bucket][2]))", _src("core", "youtube_quota.py"))
        self.assertIn("DAILY_SEARCH_LIMIT = 90", service)
        server = _src("core", "api_server.py")
        route = server[server.index('@app.get("/api/admin/youtube/quota")'):][:600]
        self.assertIn("_require_admin(request)", route)
        self.assertIn("youtube_quota.status()", route)
        page = _src("dashboard", "admin", "connections", "index.html")
        self.assertIn('api("/api/admin/youtube/quota")', page)
        self.assertIn('ytBucket("חיפושים", d.search)', page)
        self.assertIn("b.remaining", page)

if __name__ == "__main__":
    unittest.main()
