"""מאיה follows the competitors' public social presence (core/competitor_watch) — 2026-11-16.

What must stay true:
- only methods the platforms allow: real numbers ONLY from public YouTube
  channels (API key, never search.list here), Instagram is "needs permission"
  (no new scope requested), Facebook/TikTok "not available"; no scraping;
- never copy: a long quote is dropped, a link no source returned is removed,
  vendor names are scrubbed;
- the watch list is the client's own competitors (their additions + the
  interview); the read is stored, a fresh one is reused for a week;
- 5 languages, no vendor names in client text; cleared with the client.
"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")

for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}), ("httpx", {}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from core import agent_actions as aa                   # noqa: E402
from core import claude_json, competitor_watch as cw   # noqa: E402
from test_landing_own import FakeDB                    # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


CHANNEL = {"channel_id": "UCabcdefghijklmnopqrstuv", "title": "Rival Cafe", "url": "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv",
           "subscribers": None, "videos": [
               {"video_id": "v1", "url": "https://www.youtube.com/watch?v=v1", "title": "latte art 30s", "views": 5200,
                "likes": 300, "comments": 12, "published_at": "2026-11-01", "duration": "PT30S"},
               {"video_id": "v2", "url": "https://www.youtube.com/watch?v=v2", "title": "menu tour 8min", "views": 140,
                "likes": 3, "comments": 0, "published_at": "2026-10-20", "duration": "PT8M"}]}


class Links(unittest.TestCase):
    def test_classify_what_each_platform_gives(self):
        self.assertEqual(cw.classify("https://www.youtube.com/@rivalcafe")["handle"], "@rivalcafe")
        self.assertEqual(cw.classify("youtube.com/channel/UCabcdefghijklmnopqrstuv")["channel_id"], "UCabcdefghijklmnopqrstuv")
        self.assertEqual(cw.classify("https://instagram.com/rival")["platform"], "instagram")
        self.assertEqual(cw.classify("https://www.tiktok.com/@rival")["platform"], "tiktok")
        self.assertEqual(cw.classify("rival.co.il")["platform"], "website")
        self.assertEqual(cw.PLATFORM_ACCESS, {"youtube": "measured", "instagram": "needs_permission",
                                              "facebook": "not_available", "tiktok": "not_available",
                                              "website": "observed"})


class NeverCopy(unittest.TestCase):
    def test_long_quotes_unsourced_links_and_flags(self):
        allowed = {"https://www.youtube.com/watch?v=v1"}
        raw = {"works": [
            {"pattern": "short vertical clips", "why": "v1 has 37x the views of v2", "competitor": "Rival Cafe",
             "evidence": "https://www.youtube.com/watch?v=v1", "uses_numbers": True},
            {"pattern": "they write \"come taste the best coffee in the whole north of the country today with us\"",
             "why": "x"},
            {"pattern": "menus https://made-up.example/post", "why": "y", "evidence": "https://made-up.example/post"}],
            "better": [{"idea": "a 20-second 'how we roast' clip in your own voice", "how": "film at the counter"}]}
        out = cw.clean_digest(raw, allowed, {"Rival Cafe"})
        self.assertEqual(len(out["works"]), 2)                                 # the copied caption is gone
        self.assertEqual(out["works"][0]["basis"], "measured")
        self.assertEqual(out["works"][1]["evidence"], "")
        self.assertNotIn("made-up.example", out["works"][1]["pattern"])
        self.assertEqual(out["works"][1]["basis"], "observed")
        self.assertTrue(out["better"][0]["idea"])                              # a short quote is fine
        self.assertTrue(cw.copies('"' + " ".join(["word"] * 13) + '"'))
        self.assertFalse(cw.copies('"' + " ".join(["word"] * 5) + '"'))


class Flow(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.reads, self.writes = [], []
        self.saved = [(cw, n, getattr(cw, n)) for n in ("_db", "_interview_competitors", "_youtube", "_web_research")]
        self.saved += [(claude_json, "safe_claude_json_call", claude_json.safe_claude_json_call)]
        cw._db = lambda: self.db
        cw._interview_competitors = lambda cid: [{"name": "Rival Cafe", "url": "https://www.youtube.com/@rivalcafe"}]

        def youtube(entry):
            self.reads.append(entry)
            return CHANNEL
        cw._youtube = youtube
        cw._web_research = lambda cid: ("COMPETITORS: rival does reels", [])

        def write(system, user, **kw):
            self.writes.append(user)
            return {"works": [{"pattern": "30-second clips", "why": "5200 views vs 140", "competitor": "Rival Cafe",
                               "evidence": "https://www.youtube.com/watch?v=v1", "uses_numbers": True}],
                    "doesnt": [{"pattern": "long menu tours", "why": "few views", "competitor": "Rival Cafe"}],
                    "better": [{"idea": "your roaster's morning in 20 seconds", "how": "one take, natural light"}]}
        claude_json.safe_claude_json_call = write

    def tearDown(self):
        for module, name, value in self.saved:
            setattr(module, name, value)

    def test_watch_list_add_and_dedupe(self):
        listed = cw.add(27, "קפה השכן", "instagram.com/hashachen")
        self.assertEqual([c["name"] for c in listed], ["קפה השכן", "Rival Cafe"])
        self.assertEqual(listed[0]["urls"][0]["platform"], "instagram")
        cw.add(27, "קפה השכן", "https://www.youtube.com/@hashachen")
        self.assertEqual(len(cw.watch_list(27)[0]["urls"]), 2)

    def test_the_read_uses_real_numbers_and_is_reused_for_a_week(self):
        digest = cw.build_digest(27, "he")
        self.assertEqual(digest["works"][0]["basis"], "measured")
        self.assertEqual(digest["access"]["youtube"]["access"], "measured")
        self.assertEqual(len(self.reads), 1)
        self.assertIn("5200", self.writes[0])                                 # the real counts reach the writer
        cw.build_digest(27, "he")
        self.assertEqual(len(self.writes), 1)                                  # fresh: no second call
        text = cw.message(digest, "he")
        for needle in ("מה עובד להם", "איך אנחנו עושים את זה טוב יותר", "(נמדד: מספרים ציבוריים מיוטיוב)",
                       "לא מעתיקים", "https://www.youtube.com/watch?v=v1"):
            self.assertIn(needle, text)
        self.assertEqual(cw.prompt_lines(27)["better_ideas"], ["your roaster's morning in 20 seconds"])

    def test_no_competitors_no_call(self):
        cw._interview_competitors = lambda cid: []
        with self.assertRaises(cw.WatchError) as caught:
            cw.build_digest(27)
        self.assertEqual(caught.exception.code, "no_competitors")
        self.assertEqual(self.writes, [])

    def test_cards(self):
        aa_saved = aa._lang
        aa._lang = lambda cid: "he"
        try:
            spec = aa.ACTIONS["competitor_digest"]
            cw._interview_competitors = lambda cid: []
            self.assertIn("clarify", spec["prepare"](27, {}, {"language": "he"}, []))
            track = aa.ACTIONS["track_competitor"]
            self.assertEqual(track["params"]["url"]["validate"]("rival.co.il", 27), "https://rival.co.il")
            out = track["run"](27, {"name": "Rival", "url": "https://instagram.com/rival"}, None)
            self.assertEqual(out["access"], "needs_permission")
            self.assertIn("אינסטגרם", track["done_message"](out, "he"))
        finally:
            aa._lang = aa_saved


class Rules(unittest.TestCase):
    def test_texts_and_no_vendor_names(self):
        import re
        vendor = re.compile(r"higgsfield|heygen|eleven\s?labs|semrush|ahrefs|dataforseo", re.IGNORECASE)
        for table in cw.TEXT.values():
            self.assertEqual(set(table), set(LANGS))
        for name in ("track_competitor", "competitor_digest"):
            for key in ("title", "what"):
                self.assertEqual(set(aa.ACTIONS[name][key]), set(LANGS))
                for lang in LANGS:
                    self.assertIsNone(vendor.search(aa.ACTIONS[name][key][lang]))

    def test_allowed_methods_only(self):
        yt = _src("core", "youtube_service.py")
        body = yt[yt.index("def public_channel_recent("):yt.index("def _int(")]
        self.assertIn("_get_with_key(", body)
        self.assertNotIn('"search"', body)                                     # 3 units, never search.list
        meta = _src("core", "meta_service.py")
        self.assertNotIn('"instagram_manage_insights",', meta.split("OAUTH_SCOPES = [", 1)[1].split("]", 1)[0])
        mod = _src("core", "competitor_watch.py")
        for forbidden in ("httpx", "requests", "selenium", "playwright", "instagram.com/api"):
            self.assertNotIn(forbidden, mod)

    def test_wiring(self):
        from agents import support_agent
        self.assertIn("COMPETITORS ON SOCIAL", support_agent.STUDIO_RULE)
        self.assertNotIn("{", support_agent.STUDIO_RULE)
        self.assertIn("competitor_watch.prompt_lines", _src("agents", "media_agent.py"))
        for path in (("core", "deletion_guard.py"), ("core", "admin_service.py")):
            self.assertIn('"competitor_watch:"', _src(*path))
        self.assertIn('f"competitor_watch:{int(i)}"', _src("core", "client_deletion.py"))


if __name__ == "__main__":
    unittest.main()
