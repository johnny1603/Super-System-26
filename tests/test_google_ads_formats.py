"""ONE table of Google Ads media specs (core/google_ads_formats, 2026-11-28).

- the table itself: slots, campaign types, text limits, banners, video;
- NO Google Ads dimension, ratio, character limit or size cap written in any
  other module of core/ or agents/;
- what a campaign type needs is DERIVED from the table (required_assets);
- a produced file is measured from its bytes (type, pixels, weight);
- a video is a YouTube URL, never a file;
- the table carries its verification date and says when it is stale.

Run from the repo root:  python tests/test_google_ads_formats.py -v
"""
import io
import json
import os
import re
import sys
import tokenize
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import google_ads_formats as gaf          # noqa: E402

KB = 1024


def _image(w, h, kind="PNG", noisy=False):
    from PIL import Image
    img = Image.effect_noise((w, h), 80).convert("RGB") if noisy else Image.new("RGB", (w, h), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, kind, **({"quality": 95} if kind == "JPEG" else {}))
    return buf.getvalue()


class Table(unittest.TestCase):
    def test_the_slots(self):
        want = {"landscape": ("1.91:1", 1200, 628, 600, 314), "square": ("1:1", 1200, 1200, 300, 300),
                "portrait": ("4:5", 960, 1200, 480, 600), "vertical": ("9:16", 1080, 1920, 600, 1067),
                "logo_square": ("1:1", 1200, 1200, 128, 128), "logo_landscape": ("4:1", 1200, 300, 512, 128)}
        self.assertEqual(set(gaf.SLOTS), set(want))
        for name, (aspect, w, h, mw, mh) in want.items():
            s = gaf.SLOTS[name]
            self.assertEqual((s.aspect, s.width, s.height, s.min_width, s.min_height), (aspect, w, h, mw, mh), name)
            a, b = (float(x) for x in aspect.split(":"))
            self.assertAlmostEqual(s.ratio, a / b, delta=0.004, msg=name)          # the name matches the pixels
            self.assertAlmostEqual(mw / mh, a / b, delta=0.004, msg=name)
        self.assertEqual({n for n, s in gaf.SLOTS.items() if s.kind == "logo"}, {"logo_square", "logo_landscape"})

    def test_the_caps(self):
        self.assertEqual(gaf.IMAGE_MAX_BYTES, 5120 * KB)
        self.assertEqual(gaf.SMALL_MAX_BYTES, 150 * KB)
        self.assertEqual(gaf.BANNER_MAX_BYTES, 150 * KB)
        self.assertEqual(gaf.COMPANION_BANNER["max_bytes"], 150 * KB)
        # Demand Gen's logo is the odd one
        self.assertEqual(gaf.CAMPAIGNS["demand_gen"]["logo_max_bytes"], 150 * KB)
        self.assertEqual(gaf.CAMPAIGNS["demand_gen"]["image_max_bytes"], 5120 * KB)
        for name in ("display", "pmax"):
            self.assertEqual(gaf.CAMPAIGNS[name]["logo_max_bytes"], 5120 * KB)
        # GIF is not accepted in a responsive display ad
        for name in ("display", "pmax", "demand_gen"):
            self.assertEqual(gaf.CAMPAIGNS[name]["file_types"], ("jpg", "png"))

    def test_responsive_display(self):
        c = gaf.campaign("display")
        self.assertEqual((c["images"]["landscape"]["min"], c["images"]["landscape"]["max"],
                          c["images"]["landscape"]["recommended"], c["images"]["landscape"]["need"]),
                         (1, 15, 5, "required"))
        self.assertEqual(c["images"]["square"], c["images"]["landscape"])
        self.assertEqual(c["images"]["portrait"]["need"], "optional")
        self.assertEqual((c["logos"]["logo_square"]["need"], c["logos"]["logo_square"]["max"]), ("required", 5))
        self.assertEqual(c["logos"]["logo_landscape"]["need"], "recommended")
        t = c["text"]
        self.assertEqual((t["headline"]["max_chars"], t["headline"]["max"]), (30, 5))
        self.assertEqual((t["long_headline"]["max_chars"], t["long_headline"]["max"]), (90, 1))
        self.assertEqual((t["description"]["max_chars"], t["description"]["max"]), (90, 5))
        self.assertEqual(t["business_name"]["max_chars"], 25)

    def test_performance_max(self):
        c = gaf.campaign("pmax")
        self.assertEqual(set(c["images"]), {"landscape", "square", "portrait"})
        self.assertEqual((c["images_total_max"], c["logos_total_max"]), (20, 5))
        t = c["text"]
        self.assertEqual((t["headline"]["min"], t["headline"]["max"], t["headline"]["max_chars"]), (3, 15, 30))
        self.assertEqual(t["headline"]["short"], {"max_chars": 15, "min": 1})
        self.assertEqual((t["long_headline"]["min"], t["long_headline"]["max"], t["long_headline"]["max_chars"]), (1, 5, 90))
        self.assertEqual((t["description"]["min"], t["description"]["max"], t["description"]["max_chars"]), (2, 5, 90))
        self.assertEqual(c["video"], {"min": 0, "max": 15, "min_seconds": 10})
        self.assertEqual(gaf.minimum_to_serve("pmax"),
                         {"landscape": 1, "square": 1, "logo_square": 1, "headline": 3, "long_headline": 1,
                          "description": 2, "business_name": 1})
        self.assertEqual(c["minimum_source"], "stated")

    def test_demand_gen(self):
        c = gaf.campaign("demand_gen")
        self.assertEqual(set(c["images"]), {"landscape", "square", "portrait", "vertical"})     # vertical: Shorts
        self.assertEqual(c["images_total_max"], 20)
        self.assertEqual(c["text"]["headline"]["max_chars"], 40)
        self.assertEqual(c["text"]["headline"]["short"], {"max_chars": 30, "min": 1})
        self.assertEqual((c["text"]["description"]["max_chars"], c["text"]["business_name"]["max_chars"]), (90, 25))
        self.assertEqual(c["carousel"], {"min_cards": 2, "max_cards": 10, "one_ratio": True,
                                         "card_headline_max_chars": 40, "logo": "logo_square"})

    def test_banners(self):
        self.assertEqual(gaf.BANNERS["mobile"], ((300, 50), (320, 50), (320, 100)))
        self.assertEqual(gaf.BANNERS["square_rect"],
                         ((200, 200), (240, 400), (250, 250), (250, 360), (300, 250), (336, 280), (580, 400)))
        self.assertEqual(gaf.BANNERS["skyscraper"], ((120, 600), (160, 600), (300, 600), (300, 1050)))
        self.assertEqual(gaf.BANNERS["leaderboard"],
                         ((468, 60), (728, 90), (930, 180), (970, 90), (970, 250), (980, 120)))
        self.assertEqual(len(gaf.BANNER_SIZES), 20)
        self.assertEqual(gaf.TOP_BANNERS, ((300, 250), (336, 280), (728, 90), (300, 600), (320, 100), (320, 50)))
        self.assertTrue(set(gaf.TOP_BANNERS) <= set(gaf.BANNER_SIZES))

    def test_video(self):
        v = gaf.VIDEO_FORMATS
        self.assertEqual((v["skippable_in_stream"]["skip_after_seconds"], v["skippable_in_stream"]["aim_seconds"]),
                         (5, (15, 30)))
        self.assertEqual((v["non_skippable_in_stream"]["min_seconds"], v["non_skippable_in_stream"]["max_seconds"],
                          v["non_skippable_in_stream"]["connected_tv_seconds"]), (7, 15, (16, 30)))
        self.assertEqual((v["bumper"]["min_seconds"], v["bumper"]["max_seconds"]), (5, 6))
        self.assertEqual((v["in_feed"]["recommended_seconds"], v["in_feed"]["remarketing_min_seconds"]), (15, 12))
        self.assertEqual((v["shorts"]["max_seconds"], v["shorts"]["aspect"]), (60, "9:16"))
        self.assertEqual(gaf.VIDEO_RATIOS, {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)})
        self.assertEqual((gaf.COMPANION_BANNER["width"], gaf.COMPANION_BANNER["height"],
                          gaf.COMPANION_BANNER["file_types"]), (300, 60, ("jpg", "gif", "png")))
        # a 30-second non-skippable must be horizontal; a bumper is 5-6 s
        self.assertEqual(gaf.video_length_problems("non_skippable_in_stream", 15, "9:16"), [])
        self.assertEqual([p["code"] for p in gaf.video_length_problems("non_skippable_in_stream", 30, "9:16")],
                         ["must_be_horizontal"])
        self.assertEqual(gaf.video_length_problems("non_skippable_in_stream", 30, "16:9"), [])
        self.assertEqual([p["code"] for p in gaf.video_length_problems("bumper", 7)], ["too_long"])
        self.assertEqual([p["code"] for p in gaf.video_length_problems("bumper", 4)], ["too_short"])
        self.assertEqual([p["code"] for p in gaf.video_length_problems("shorts", 61, "9:16")], ["too_long"])

    def test_search(self):
        t = gaf.text_limits("search")
        self.assertEqual((t["headline"]["max_chars"], t["headline"]["max"]), (30, 15))
        self.assertEqual((t["description"]["max_chars"], t["description"]["max"]), (90, 4))
        self.assertEqual((t["path"]["max_chars"], t["path"]["max"]), (15, 2))
        self.assertFalse(gaf.needs_media("search"))
        self.assertEqual(gaf.required_assets("search"), [])

    def test_the_whole_table_is_plain_data(self):
        t = gaf.table()
        json.dumps(t)
        self.assertEqual(set(t["campaigns"]), set(gaf.CAMPAIGN_TYPES))
        self.assertEqual(t["video"]["workflow"], ["upload_to_client_youtube_channel", "reference_by_url"])
        with self.assertRaises(ValueError):
            gaf.campaign("billboard")


class Dated(unittest.TestCase):
    def test_verified_and_reverify(self):
        self.assertEqual((gaf.VERIFIED, gaf.REVERIFY_BY), ("2026-11-28", "2027-02-28"))
        self.assertFalse(gaf.stale(date(2027, 2, 28)))
        self.assertTrue(gaf.stale(date(2027, 3, 1)))
        self.assertEqual(gaf.stale_line(date(2026, 12, 1)), "")
        self.assertIn("2027-02-28", gaf.stale_line(date(2027, 3, 1)))


class Derived(unittest.TestCase):
    """What a campaign type needs comes from the table — nobody picks a size."""

    def rows(self, *a, **k):
        return {r["slot"]: r for r in gaf.required_assets(*a, **k)}

    def test_every_ratio_of_the_type_is_ordered(self):
        self.assertEqual(set(self.rows("pmax")), {"landscape", "square", "portrait", "logo_square", "logo_landscape"})
        self.assertEqual(set(self.rows("demand_gen")), {"landscape", "square", "portrait", "vertical", "logo_square"})
        self.assertEqual(set(self.rows("display")), {"landscape", "square", "portrait", "logo_square", "logo_landscape"})
        self.assertEqual(set(self.rows("youtube")), {"video"})

    def test_a_row_carries_its_own_numbers(self):
        r = self.rows("demand_gen", concepts=3)
        self.assertEqual((r["vertical"]["width"], r["vertical"]["height"], r["vertical"]["count"]), (1080, 1920, 3))
        self.assertEqual(r["landscape"]["max_bytes"], 5120 * KB)
        self.assertEqual(r["logo_square"]["max_bytes"], 150 * KB)
        self.assertEqual(r["logo_square"]["count"], 1)
        self.assertEqual(r["landscape"]["crop_targets"], ["1:1", "4:1"])
        self.assertEqual(self.rows("pmax")["logo_square"]["max_bytes"], 5120 * KB)

    def test_concepts_stay_inside_the_platform_ceilings(self):
        self.assertEqual(gaf.max_concepts("pmax"), 6)             # 20 images across three ratios
        self.assertEqual(gaf.max_concepts("demand_gen"), 5)       # 20 across four
        self.assertEqual(gaf.max_concepts("display"), 15)
        r = self.rows("pmax", concepts=50)
        self.assertEqual(sum(v["count"] for v in r.values() if v["kind"] == "image"), 18)
        self.assertLessEqual(18, gaf.CAMPAIGNS["pmax"]["images_total_max"])

    def test_banners_only_on_display_and_exact(self):
        r = self.rows("display", banners="top")
        self.assertEqual(len([v for v in r.values() if v["kind"] == "banner"]), 6)
        self.assertEqual((r["banner_300x250"]["max_bytes"], r["banner_300x250"]["exact_size"]), (150 * KB, True))
        self.assertEqual(len([v for v in self.rows("display", banners="all").values() if v["kind"] == "banner"]), 20)
        with self.assertRaises(ValueError):
            gaf.required_assets("pmax", banners="top")
        with self.assertRaises(ValueError):
            gaf.required_assets("display", banners="some")

    def test_a_video_row_states_the_workflow(self):
        v = self.rows("youtube")["video"]
        self.assertEqual((v["need"], v["workflow"]), ("required", ["upload_to_client_youtube_channel", "reference_by_url"]))
        self.assertNotIn("video", self.rows("pmax"))
        self.assertEqual(self.rows("pmax", video=True)["video"]["min_seconds"], 10)


class Measured(unittest.TestCase):
    def row(self, campaign, slot, **k):
        return next(r for r in gaf.required_assets(campaign, **k) if r["slot"] == slot)

    def test_the_type_comes_from_the_bytes(self):
        self.assertEqual(gaf.file_type(_image(10, 10, "PNG")), "png")
        self.assertEqual(gaf.file_type(_image(10, 10, "JPEG")), "jpg")
        self.assertEqual(gaf.file_type(_image(10, 10, "GIF")), "gif")
        self.assertEqual(gaf.file_type(b"hello"), "")

    def test_a_good_file_passes(self):
        self.assertEqual(gaf.check_file(self.row("pmax", "landscape"), _image(1200, 628, "JPEG")), [])
        self.assertEqual(gaf.check_file(self.row("pmax", "square"), _image(600, 600)), [])

    def test_each_failure_is_named_with_the_real_numbers(self):
        codes = lambda row, content: [p["code"] for p in gaf.check_file(row, content)]   # noqa: E731
        land = self.row("display", "landscape")
        self.assertEqual(codes(land, _image(1200, 628, "GIF")), ["file_type"])           # GIF is not accepted
        self.assertEqual(codes(land, _image(1200, 1200)), ["ratio"])
        self.assertEqual(codes(land, _image(573, 300)), ["too_small"])
        self.assertEqual(codes(land, b"not an image"), ["unreadable"])
        self.assertIn("573x300", gaf.check_file(land, _image(573, 300))[0]["detail"])
        # the Demand Gen logo: fine at 5 MB anywhere else, over the cap here
        heavy = _image(1200, 1200, "PNG", noisy=True)
        self.assertGreater(len(heavy), 150 * KB)
        self.assertEqual(codes(self.row("demand_gen", "logo_square"), heavy), ["file_size"])
        self.assertEqual(codes(self.row("pmax", "logo_square"), heavy), [])
        self.assertIn("150 KB", gaf.check_file(self.row("demand_gen", "logo_square"), heavy)[0]["detail"])
        banner = self.row("display", "banner_970x250", banners="all")
        self.assertEqual(codes(banner, _image(970, 250)), [])
        self.assertEqual(codes(banner, _image(1940, 500)), ["size"])                     # same ratio is not enough


class Text(unittest.TestCase):
    def codes(self, campaign, texts):
        return sorted((p["field"], p["code"]) for p in gaf.check_text(campaign, texts))

    def good(self):
        return {"headline": ["עיצוב אישי לנעליים", "משלוח עד הבית", "מתנה שנשארת"], "long_headline": "נעליים בעיצוב אישי, מצוירות ביד — מתנה שלא שוכחים",
                "description": ["כל זוג מצויר ביד, פעם אחת בלבד.", "מזמינים היום ומקבלים עד הבית."], "business_name": "סטודיו לדוגמה"}

    def test_a_complete_set_passes(self):
        self.assertEqual(gaf.check_text("pmax", self.good()), [])

    def test_hebrew_counts_one_a_character_and_full_width_two(self):
        self.assertEqual(gaf.char_count("שלום עולם"), 9)
        self.assertEqual(gaf.char_count("abc 123"), 7)
        self.assertEqual(gaf.char_count("日本"), 4)

    def test_every_limit(self):
        t = self.good()
        t["headline"] = ["כותרת ארוכה מאוד מאוד מאוד שלא נכנסת בשלושים", "משלוח עד הבית", "מתנה שנשארת"]
        got = gaf.check_text("pmax", t)
        self.assertEqual([(p["field"], p["code"], p["index"]) for p in got], [("headline", "too_long", 1)])
        self.assertEqual((got[0]["limit"], got[0]["over"]), (30, got[0]["chars"] - 30))
        t = self.good()
        t["headline"] = t["headline"][:2]
        self.assertEqual(self.codes("pmax", t), [("headline", "too_few")])
        t = self.good()
        t["headline"] = ["כותרת באורך של יותר מחמש עשרה", "עוד כותרת באורך בינוני פלוס", "וכותרת שלישית ארוכה למדי"]
        self.assertEqual(self.codes("pmax", t), [("headline", "needs_short")])            # at least one of 15 or fewer
        t = self.good()
        t["description"] = t["description"] * 3
        self.assertIn(("description", "too_many"), self.codes("pmax", t))
        self.assertIn(("description", "duplicate"), self.codes("pmax", t))
        t = self.good()
        del t["business_name"]
        self.assertEqual(self.codes("pmax", t), [("business_name", "too_few")])
        # a field the type has no slot for is said, not dropped
        self.assertEqual(self.codes("search", {"headline": ["אחת", "שתיים", "שלוש"], "description": ["א", "ב"],
                                               "long_headline": "אין כזה בחיפוש"}), [("long_headline", "not_taken")])

    def test_demand_gen_takes_forty_with_one_of_thirty(self):
        long40 = "א" * 40
        base = {"description": "תיאור", "business_name": "עסק"}
        self.assertEqual(self.codes("demand_gen", {**base, "headline": [long40]}), [("headline", "needs_short")])
        self.assertEqual(self.codes("demand_gen", {**base, "headline": [long40, "קצר"]}), [])
        self.assertEqual(self.codes("demand_gen", {**base, "headline": ["א" * 41, "קצר"]}), [("headline", "too_long")])


class VideoIsAUrl(unittest.TestCase):
    def test_a_file_is_not_a_video_asset(self):
        got = gaf.video_reference_problems({"file_path": "/tmp/clip.mp4"})
        self.assertEqual([p["code"] for p in got], ["file_not_url"])
        self.assertIn("uploaded to the client's YouTube channel first", got[0]["detail"])
        self.assertEqual([p["code"] for p in gaf.video_reference_problems({})], ["file_not_url"])
        self.assertEqual([p["code"] for p in gaf.video_reference_problems({"youtube_url": "https://vimeo.com/1"})],
                         ["bad_url"])

    def test_the_urls_youtube_gives(self):
        for url in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ",
                    "https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
                    "https://youtu.be/dQw4w9WgXcQ?t=3"):
            self.assertEqual(gaf.youtube_id(url), "dQw4w9WgXcQ", url)
            self.assertEqual(gaf.video_reference_problems({"youtube_url": url}), [])
        for url in ("http://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=short",
                    "https://evil.example/youtube.com/watch?v=dQw4w9WgXcQ", "C:\\videos\\clip.mp4", ""):
            self.assertEqual(gaf.youtube_id(url), "", url)


class OneSource(unittest.TestCase):
    """No Google Ads size, ratio, limit or cap outside the table."""
    # numbers that are a Google Ads spec and nothing else in this codebase (1200 is not one of them: it is
    # also a token budget and a character cap here — the size strings below catch it where it is a size)
    NUMBERS = {"628", "314", "5120", "1067", "1050"}
    STRINGS = {"1.91:1", "4:1"}
    SIZE = re.compile(r"\b(?:1200x628|1200x1200|960x1200|1200x300|600x314|" +
                      "|".join(f"{w}x{h}" for w, h in gaf.BANNER_SIZES) + r"|300x60)\b")
    TABLE = os.path.join("core", "google_ads_formats.py")
    SOCIAL = os.path.join("core", "social_formats.py")       # its own table: 1.91:1 is also a feed ratio there

    def test_no_literal_anywhere_else(self):
        found = []
        for folder in ("core", "agents"):
            for name in sorted(os.listdir(os.path.join(ROOT, folder))):
                rel = os.path.join(folder, name)
                if not name.endswith(".py") or rel in (self.TABLE, self.SOCIAL):
                    continue
                with open(os.path.join(ROOT, rel), "rb") as f:
                    source = f.read()
                lines = source.decode("utf-8").splitlines()
                for tok in tokenize.tokenize(io.BytesIO(source).readline):
                    hit = ""
                    if tok.type == tokenize.NUMBER and tok.string in self.NUMBERS:
                        hit = tok.string
                    elif tok.type == tokenize.STRING and (tok.string.strip("\"'") in self.STRINGS
                                                          or self.SIZE.search(tok.string)):
                        hit = tok.string[:40]
                    if hit:
                        found.append(f"{rel}:{tok.start[0]}: {hit}  ← {lines[tok.start[0] - 1].strip()[:90]}")
        self.assertEqual(found, [], "a Google Ads spec decided outside core/google_ads_formats:\n" + "\n".join(found))

    def test_the_search_agent_reads_its_limits_from_the_table(self):
        with open(os.path.join(ROOT, "agents", "google_ads_agent.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('_RSA = google_ads_formats.text_limits("search")', src)
        self.assertIn('RSA_HEADLINE_MAX_CHARS = _RSA["headline"]["max_chars"]', src)
        self.assertIn('RSA_DESCRIPTION_MAX_CHARS = _RSA["description"]["max_chars"]', src)
        self.assertNotRegex(src, r"RSA_\w+_MAX_CHARS\s*=\s*\d")
        self.assertNotRegex(src, r"<= len\((?:headlines|descriptions)\) <= \d")
        self.assertNotIn("MAX 30 characters", src)
        self.assertNotIn("MAX 90 characters", src)

    def test_the_social_guard_knows_this_table(self):
        with open(os.path.join(HERE, "test_social_formats.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('os.path.join("core", "google_ads_formats.py")', src)


if __name__ == "__main__":
    unittest.main()
