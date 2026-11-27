"""ONE table of social formats (core/social_formats, 2026-11-25).

- the table itself: sizes, ratios, safe zones, carousel rules;
- NO size or ratio literal in any other module of core/ or agents/ (the
  vendors' documented enums in core/media_gen_service are the one exception);
- a post's ratio is derived from its post_format — whatever the agent writes
  in aspect_ratio is not read;
- a story's text slide is laid out inside the safe box, a feed slide uses the
  whole canvas;
- generation asks for 1080p, the result is measured, and anything narrower
  than the publish width alerts.

Run from the repo root:  python tests/test_social_formats.py -v
"""
import io
import json
import os
import sys
import tokenize
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_agent_characters  # noqa: E402,F401  (its stubs for the libraries a dev box lacks)

from core import social_formats as sf          # noqa: E402


def _png(w, h, mode="RGB"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new(mode, (w, h), (10, 20, 30) if mode == "RGB" else (10, 20, 30, 255)).save(buf, "PNG")
    return buf.getvalue()


class Table(unittest.TestCase):
    def test_the_formats(self):
        want = {"feed_portrait": (1080, 1350, "4:5"), "feed_square": (1080, 1080, "1:1"),
                "feed_landscape": (1080, 566, "1.91:1"), "feed_tall": (1080, 1440, "3:4"),
                "vertical": (1080, 1920, "9:16"), "reel_cover": (420, 654, "1:1.55")}
        for name, (w, h, aspect) in want.items():
            f = sf.FORMATS[name]
            self.assertEqual((f.width, f.height, f.aspect), (w, h, aspect), name)
            a, b = (float(x) for x in aspect.split(":"))
            self.assertAlmostEqual(f.ratio, a / b, delta=0.004, msg=name)      # the name matches the pixels
        self.assertEqual(sf.DEFAULT_FEED, "feed_portrait")
        self.assertEqual(sf.PUBLISH_WIDTH, 1080)

    def test_the_tall_feed_is_never_a_publish_format(self):
        self.assertFalse(sf.FORMATS["feed_tall"].publishable)
        self.assertTrue(all(f.publishable for n, f in sf.FORMATS.items() if n not in ("feed_tall", "site_banner")))
        for fmt in ("text_carousel", "product_carousel", "single_image", "reel", "educational"):
            for placement in ("", "feed", "story"):
                self.assertTrue(sf.for_post(fmt, placement).publishable)

    def test_safe_zones(self):
        self.assertEqual(sf.insets("meta_unified"), {"top": 269, "bottom": 672, "left": 65, "right": 65})
        self.assertEqual(sf.insets("tiktok"), {"top": 130, "bottom": 484, "left": 44, "right": 140})
        self.assertEqual(sf.insets("cross_platform"), {"top": 270, "bottom": 672, "left": 65, "right": 140})
        self.assertEqual(sf.safe_box("cross_platform"), {"x": 65, "y": 270, "width": 875, "height": 978})
        self.assertTrue(sf.SAFE_ZONES["tiktok"]["bottom_is_floor"])        # grows with the caption
        # the percentages the platforms publish
        self.assertEqual(round(269 / 1920 * 100), 14)
        self.assertEqual(round(672 / 1920 * 100), 35)
        self.assertEqual(round(65 / 1080 * 100), 6)

    def test_the_zone_follows_the_channels(self):
        self.assertEqual(sf.zone_for(["instagram"]), "meta_unified")
        self.assertEqual(sf.zone_for(["instagram", "facebook"]), "meta_unified")
        self.assertEqual(sf.zone_for(["tiktok"]), "tiktok")
        self.assertEqual(sf.zone_for(["instagram", "tiktok"]), "cross_platform")
        self.assertEqual(sf.zone_for(["youtube"]), "cross_platform")           # no zone of its own: the strictest
        self.assertEqual(sf.zone_for(["instagram", "youtube"]), "cross_platform")

    def test_carousel_rules(self):
        self.assertEqual((sf.CAROUSEL_MAX_SLIDES, sf.IMAGE_MAX_BYTES), (20, 30 * 1024 * 1024))
        self.assertEqual(sf.carousel_problems([(1080, 1350)] * 20), [])
        self.assertTrue(sf.carousel_problems([(1080, 1350)] * 21))
        mixed = sf.carousel_problems([(1080, 1350), (1080, 1080)])
        self.assertIn("slide 2", mixed[0])                                     # the first slide sets the ratio
        self.assertTrue(sf.carousel_problems([(1080, 1350)], [31 * 1024 * 1024]))
        from core import post_brief
        self.assertLessEqual(post_brief.MAX_ASSETS, sf.CAROUSEL_MAX_SLIDES)

    def test_the_page_gets_the_whole_table(self):
        t = sf.table()
        self.assertEqual(t["formats"]["feed_portrait"]["width"], 1080)
        self.assertEqual(t["safe_zones"]["meta_unified"]["box"]["height"], 1920 - 269 - 672)
        json.dumps(t)
        api = open(os.path.join(ROOT, "core", "api_server.py"), encoding="utf-8").read()
        self.assertIn('data["formats"] = social_formats.table()', api)


class OneSource(unittest.TestCase):
    """No size / ratio / resolution literal outside the table."""
    NUMBERS = {"1080", "1350", "1920", "1440"}
    STRINGS = {"4:5", "3:4", "9:16", "1:1", "16:9", "1.91:1", "1:1.55", "720p", "1080p"}
    # The vendors' own documented enums (what a model ACCEPTS — their fact, not our choice)
    VENDOR_FACTS = {os.path.join("core", "media_gen_service.py"):
                    ("VALID_ASPECT_RATIOS", "IMAGE_MODELS", "PRESENTER_RESOLUTIONS", "CHARACTER_MODELS",
                     '"aspects":', '"resolutions":')}

    def test_no_literal_anywhere_else(self):
        found = []
        for folder in ("core", "agents"):
            for name in sorted(os.listdir(os.path.join(ROOT, folder))):
                rel = os.path.join(folder, name)
                # the two tables: this one, and Google Ads' own (tests/test_google_ads_formats.py guards it)
                if not name.endswith(".py") or rel in (os.path.join("core", "social_formats.py"),
                                                       os.path.join("core", "google_ads_formats.py")):
                    continue
                with open(os.path.join(ROOT, rel), "rb") as f:
                    source = f.read()
                lines = source.decode("utf-8").splitlines()
                allowed = self.VENDOR_FACTS.get(rel, ())
                for tok in tokenize.tokenize(io.BytesIO(source).readline):
                    hit = ""
                    if tok.type == tokenize.NUMBER and tok.string in self.NUMBERS:
                        hit = tok.string
                    elif tok.type == tokenize.STRING and tok.string.strip("\"'") in self.STRINGS:
                        hit = tok.string
                    if not hit:
                        continue
                    line = lines[tok.start[0] - 1]
                    # a vendor enum: the statement it belongs to starts at most a few lines up
                    block = "\n".join(lines[max(0, tok.start[0] - 6):tok.start[0]])
                    if allowed and any(marker in block for marker in allowed):
                        continue
                    found.append(f"{rel}:{tok.start[0]}: {hit}  ← {line.strip()[:90]}")
        self.assertEqual(found, [], "a size or ratio decided outside core/social_formats:\n" + "\n".join(found))

    def test_the_renderers_canvases_are_the_tables(self):
        from core import slide_spec
        self.assertIs(slide_spec.CANVASES, sf.CANVASES)
        self.assertEqual(slide_spec.DEFAULT_CANVAS, sf.FORMATS["feed_portrait"].size)
        schema = slide_spec.schema()
        canvas = schema["properties"]["canvas"]["properties"]
        self.assertEqual(canvas["width"]["const"], sf.PUBLISH_WIDTH)
        self.assertEqual(sorted(canvas["height"]["enum"]), sorted(h for _, h in sf.CANVASES.values()))
        self.assertEqual(sorted(schema["properties"]["safe_zone"]["enum"]), sorted(sf.SAFE_ZONES))
        for seg in slide_spec.SEGMENTS:
            with open(os.path.join(slide_spec.STARTERS_DIR, f"starter-{seg}.json"), encoding="utf-8") as f:
                c = json.load(f)["canvas"]
            self.assertEqual((c["width"], c["height"]), sf.DEFAULT_CANVAS)

    def test_the_other_modules_read_it(self):
        from agents import media_agent
        from core import agent_characters, media_gen_service as mg
        self.assertEqual(agent_characters.POSE_ASPECT, sf.POSE_ASPECT)
        self.assertEqual(media_agent.IMAGE_ASPECTS["instagram"], sf.FEED_ASPECT)      # was a square, by habit
        self.assertEqual(media_agent.IMAGE_ASPECTS["tiktok"], sf.VERTICAL_ASPECT)
        self.assertEqual(mg.IMAGE_RESOLUTION, sf.GENERATION_RESOLUTION)
        self.assertEqual(mg.PRESENTER_RESOLUTION, sf.GENERATION_RESOLUTION)


class Derived(unittest.TestCase):
    """aspect_ratio comes from post_format — never the agent's choice."""

    def post(self, **over):
        raw = {"post_format": "text_carousel", "format_reason": "a carousel that explains three reasons",
               "objective": "awareness", "set_id": "why-us", "audience": "private customers",
               "caption": "three reasons", "channels": ["instagram"],
               "assets": [{"asset_id": "s1", "role": "hook", "copy_headline": "שלוש סיבות", "copy_body": "",
                           "asset_type": "text_slide", "aspect_ratio": "1:1"},
                          {"asset_id": "s2", "role": "point", "copy_headline": "", "copy_body": "body",
                           "asset_type": "generated_image", "visual_direction": "a pair of sneakers on a table",
                           "aspect_ratio": "16:9"}]}
        raw.update(over)
        from core import post_brief
        return post_brief.validate_post(raw, 27)

    def test_a_feed_post_is_the_feed_default_whatever_the_agent_wrote(self):
        post = self.post()
        self.assertEqual({a["aspect_ratio"] for a in post["assets"]}, {"4:5"})      # 1:1 and 16:9 were not read
        self.assertEqual((post["format"], post["placement"], post["safe_zone"]), ("feed_portrait", "feed", ""))
        self.assertEqual({a["safe_zone"] for a in post["assets"]}, {""})

    def test_a_reel_and_a_story_are_vertical_with_their_zone(self):
        reel = self.post(post_format="reel", format_reason="a short vertical video of the process",
                         channels=["instagram", "tiktok"])
        self.assertEqual({a["aspect_ratio"] for a in reel["assets"]}, {"9:16"})
        self.assertEqual((reel["format"], reel["safe_zone"]), ("vertical", "cross_platform"))
        story = self.post(placement="story")
        self.assertEqual({a["aspect_ratio"] for a in story["assets"]}, {"9:16"})
        self.assertEqual((story["placement"], story["safe_zone"]), ("story", "meta_unified"))
        self.assertEqual({a["safe_zone"] for a in story["assets"]}, {"meta_unified"})
        with self.assertRaises(ValueError):
            self.post(placement="billboard")

    def test_the_agents_hint_no_longer_offers_a_ratio(self):
        from core import agent_actions
        hint = agent_actions.ACTIONS["produce_post"]["params"]["post"]["hint"]
        self.assertNotIn("aspect_ratio", hint)
        self.assertIn("placement", hint)
        self.assertIn("you never choose a ratio", " ".join(hint.split()))

    def test_a_stored_slide_keeps_its_zone(self):
        src = open(os.path.join(ROOT, "core", "social_queue.py"), encoding="utf-8").read()
        self.assertIn('{"safe_zone": a.get("safe_zone") or ""}', src)
        src = open(os.path.join(ROOT, "core", "post_pipeline.py"), encoding="utf-8").read()
        self.assertIn('safe_zone=slide.get("safe_zone") or ""', src)


class SafeBox(unittest.TestCase):
    """A story's text sits inside the safe box; a feed slide uses the canvas."""

    def test_margins_and_frame(self):
        from core import slide_renderer, slide_spec
        feed = slide_spec.starter("b2c", "default")
        self.assertNotIn("safe_zone", feed)
        self.assertEqual(slide_renderer.layout_frame(feed), {"x": 0, "y": 0, "w": 1080, "h": 1350})
        plain = slide_spec.margins(feed)
        story = slide_spec.starter("b2c", "default", sf.VERTICAL_ASPECT, "cross_platform")
        self.assertEqual(story["safe_zone"], "cross_platform")
        m = slide_spec.margins(story)
        for side, px in sf.insets("cross_platform").items():
            self.assertGreaterEqual(m[side], px)
            self.assertGreaterEqual(m[side], plain[side])
        self.assertEqual(slide_renderer.layout_frame(story), {"x": 65, "y": 270, "w": 875, "h": 978})
        # a vertical canvas never goes without a zone
        self.assertEqual(slide_spec.starter("b2c", "default", sf.VERTICAL_ASPECT)["safe_zone"], "meta_unified")
        # back on a feed canvas the zone is gone
        self.assertNotIn("safe_zone", slide_spec.on_canvas(dict(story), sf.FEED_ASPECT))

    def test_the_text_is_drawn_inside_the_box(self):
        from core import slide_renderer as sr, slide_spec
        try:
            sr.require_raqm()
        except Exception:
            self.skipTest("no raqm here")
        from PIL import Image, ImageChops
        roles = {"bg": "#101010", "fg": "#FFFFFF", "accent": "#FF3344", "muted": "#BBBBBB"}
        for zone in sf.SAFE_ZONES:
            spec = slide_spec.starter("b2c", "default", sf.VERTICAL_ASPECT, zone)
            spec["background"] = {"type": "solid", "color_role": "bg"}
            spec.pop("accent", None)
            spec["counter"] = {"show": False}
            png = sr.render_slide(spec, roles, "שלוש סיבות להזמין", "הזמנה עד סוף החודש", 1, 3, "hook")
            img = Image.open(io.BytesIO(png)).convert("RGB")
            self.assertEqual(img.size, sf.FORMATS["vertical"].size)
            ink = ImageChops.difference(img, Image.new("RGB", img.size, (16, 16, 16))).getbbox()
            box = sf.safe_box(zone)
            self.assertGreaterEqual(ink[0], box["x"], zone)
            self.assertGreaterEqual(ink[1], box["y"], zone)
            self.assertLessEqual(ink[2], box["x"] + box["width"], zone)
            self.assertLessEqual(ink[3], box["y"] + box["height"], zone)


class Generation(unittest.TestCase):
    def test_no_four_by_five_at_the_model_so_three_by_four_then_a_crop(self):
        from core import image_fit, media_gen_service as mg
        body = mg.image_payload("x", sf.FEED_ASPECT)
        self.assertEqual((body["aspect_ratio"], body["resolution"]), (sf.GENERATION_FALLBACK_ASPECT, "1080p"))
        # what 1080p at 3:4 SHOULD return, cropped to the feed default
        fitted = image_fit.fit_aspect(_png(1080, 1440), sf.FEED_ASPECT, body["aspect_ratio"])
        self.assertTrue(fitted["cropped"])
        self.assertEqual(sf.measure(fitted["content"]), sf.FORMATS["feed_portrait"].size)
        self.assertTrue(sf.audit(fitted["content"], sf.FEED_ASPECT)["ok"])

    def test_what_came_back_is_measured_and_a_narrow_image_alerts(self):
        alerts = []
        from core import agent_base
        saved = agent_base.agent_alert
        agent_base.agent_alert = lambda source, lines: alerts.append(lines)
        try:
            ok = sf.report("client 27: set/a1", _png(1080, 1350), sf.FEED_ASPECT)
            self.assertEqual((ok["width"], ok["height"], ok["ok"]), (1080, 1350, True))
            self.assertEqual(alerts, [])
            # a "1080p" that is really 810x1080 → 810x1012 after the crop: a bug, said out loud
            small = sf.report("client 27: set/a2", _png(810, 1012), sf.FEED_ASPECT)
            self.assertFalse(small["ok"])
            self.assertIn("810x1012", alerts[0][0])
            self.assertIn("client 27", alerts[0][0])
            self.assertFalse(sf.audit(b"not an image", sf.FEED_ASPECT)["ok"])
        finally:
            agent_base.agent_alert = saved

    def test_the_generation_result_carries_the_real_size(self):
        src = open(os.path.join(ROOT, "core", "media_gen_service.py"), encoding="utf-8").read()
        gen = src[src.index("def generate_image"):src.index("# Presenter video")]
        self.assertIn("social_formats.measure(content)", gen)
        self.assertIn('"returned_size"', gen)
        agent = open(os.path.join(ROOT, "agents", "media_agent.py"), encoding="utf-8").read()
        self.assertEqual(agent.count("social_formats.report("), 3)          # brief asset, character scene, free brief
        self.assertIn('out["low_resolution"] = sized["reason"]', agent)
        pipe = open(os.path.join(ROOT, "core", "post_pipeline.py"), encoding="utf-8").read()
        self.assertIn('flags.append("low_resolution")', pipe)
        from core import social_queue
        self.assertIn("low_resolution", social_queue.FLAG_TEXT if hasattr(social_queue, "FLAG_TEXT") else
                      open(os.path.join(ROOT, "core", "social_queue.py"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
