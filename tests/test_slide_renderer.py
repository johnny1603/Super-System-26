"""Text slides drawn with real fonts, designed by the agent as a spec
(2026-11-23, core/slide_spec + slide_renderer + slide_templates + slide_design,
docs/SLIDE-RENDERER.md).

What must stay true:
- raqm is checked; without it nothing renders (a clear error, never a guess);
- Hebrew is shaped RTL — the first letter is on the RIGHT — and matches the
  golden images; wrapping is measured with the font and keeps word order;
- a guard failure FAILS the render (overflow, overlap, headline words,
  contrast — including over a photo with a scrim); text is never clipped;
- colours are roles from the brief's palette variant, never hex; a set never
  mixes variants or templates;
- templates are versioned (unchanged save = no version, restore = new version)
  and keep the manager's critiques;
- a text slide never reaches an image model.

Render tests run where Pillow + raqm are installed: prod, CI (both workflows
install the pinned Pillow + libfribidi0), and a dev box that has them. Regenerate the golden images
only after LOOKING at them: SLIDE_GOLDENS_UPDATE=1 python tests/test_slide_renderer.py
"""
import copy
import io
import json
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

from core import post_brief, slide_design, slide_renderer as sr, slide_spec, slide_templates, social_queue as sq  # noqa: E402

RAQM = sr.raqm_status()["available"]
# Skipped only on a dev box without Pillow + raqm; CI (CI=true) installs both
# and always runs these — a missing library there fails loudly.
NEEDS_RAQM = unittest.skipUnless(RAQM or os.environ.get("CI"), "Pillow + raqm not installed here (CI and prod have them)")
GOLDEN_DIR = os.path.join(ROOT, "tests", "golden")
UPDATE = os.environ.get("SLIDE_GOLDENS_UPDATE") == "1"

RED = "black + red"
GOLD = "black + gold"
HEAD = "הסיבה הראשונה: המתנה שנשארת"
BODY = "כל זוג מעוצב פעם אחת, בשביל אדם אחד — מידה 42, 2026."


def roles(label=RED):
    return slide_spec.palette_roles(27, label)


def starter(label=RED, aspect="4:5"):
    return slide_spec.starter("b2c" if label == RED else "b2b", label, aspect)


def one_block(text_role="headline", **over):
    block = {"role": text_role, "anchor": "center", "align": "right", "x_pct": 0, "y_pct": 50, "max_width_pct": 80,
             "font_family": "Rubik", "size": 120, "weight": 700, "color_role": "fg"}
    block.update(over)
    return {"version": 1, "canvas": {"width": 1080, "height": 1080}, "palette_variant": RED, "margins": 64,
            "background": {"type": "solid", "color_role": "bg"}, "text_blocks": [block]}


def png_of(image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class FakeStores:
    """social_queue + slide_templates in memory."""

    def install(self, test):
        self.queue, self.templates = [], {}
        saved = (sq._load, sq._save, slide_templates._load, slide_templates._save)
        sq._load = lambda cid: copy.deepcopy(self.queue)
        sq._save = lambda cid, items: setattr(self, "queue", copy.deepcopy(items))

        def tload(cid):
            value = copy.deepcopy(self.templates)
            value.setdefault("templates", {})
            return value
        slide_templates._load = tload
        slide_templates._save = lambda cid, data: setattr(self, "templates", copy.deepcopy(data))

        def restore():
            sq._load, sq._save, slide_templates._load, slide_templates._save = saved
        test.addCleanup(restore)
        return self


# ─── raqm ────────────────────────────────────────────────────────────────────

class Raqm(unittest.TestCase):
    def test_missing_raqm_refuses_every_render(self):
        saved = sr.features

        class NoRaqm:
            @staticmethod
            def check(name):
                return False

            @staticmethod
            def version(name):
                return None
        sr.features = NoRaqm
        try:
            self.assertFalse(sr.raqm_status()["available"])
            with self.assertRaises(sr.RendererUnavailable) as e:
                sr.render_slide(starter(), roles(), HEAD, BODY)
            self.assertIn("raqm", str(e.exception))
            self.assertFalse(sr.startup_check()["available"])           # loud, never raises
        finally:
            sr.features = saved

    @NEEDS_RAQM
    def test_raqm_reports_fribidi_and_harfbuzz(self):
        status = sr.startup_check()
        self.assertTrue(status["available"])
        self.assertTrue(status["fribidi"] and status["harfbuzz"])
        self.assertEqual(status["missing_fonts"], [])

    def test_fonts_are_bundled_with_their_licences(self):
        for name in ("Rubik[wght].ttf", "Assistant[wght].ttf", "Rubik-OFL.txt", "Assistant-OFL.txt"):
            self.assertTrue(os.path.isfile(os.path.join(sr.FONTS_DIR, name)), name)
        self.assertEqual(set(sr.FONT_FILES), {"Rubik", "Assistant"})
        with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as f:
            self.assertIn("libfribidi0", f.read())


# ─── The schema (the agent's design space) ───────────────────────────────────

class Schema(unittest.TestCase):
    def test_the_schema_file_is_the_contract(self):
        with open(slide_spec.SCHEMA_PATH, encoding="utf-8") as f:
            schema = json.load(f)
        for knob in ("canvas", "palette_variant", "background", "text_blocks", "accent", "logo", "counter", "margins"):
            self.assertIn(knob, schema["properties"], knob)
        block = schema["$defs"]["text_blocks"]["items"]["properties"]
        for knob in ("role", "text", "anchor", "align", "x_pct", "y_pct", "max_width_pct", "font_family", "size",
                     "weight", "line_spacing", "color_role"):
            self.assertIn(knob, block, knob)
        self.assertEqual(schema["$defs"]["color_role"]["enum"], list(slide_spec.COLOR_ROLES))
        self.assertEqual(schema["properties"]["canvas"]["properties"]["height"]["enum"], [1080, 1350, 1920])
        self.assertIn("schema", slide_spec.schema_text())

    def test_starters_are_plain_and_valid(self):
        for label in (RED, GOLD):
            spec = starter(label)
            self.assertEqual(spec["palette_variant"], label)
            self.assertEqual(spec["background"]["type"], "solid")
            self.assertEqual((spec["accent"]["type"], spec["logo"]["show"]), ("none", False))

    def test_bad_specs_are_refused_with_paths(self):
        bad = []
        s = starter(); s["text_blocks"][0]["color_role"] = "#FF0000"; bad.append((s, "color_role"))   # never hex
        s = starter(); s["canvas"]["height"] = 1000; bad.append((s, "canvas.height"))
        s = starter(); s["text_blocks"][0]["font_family"] = "Comic Sans"; bad.append((s, "font_family"))
        s = starter(); s["shadow"] = True; bad.append((s, "unknown field"))
        s = starter(); s["margins"] = {"top": 10}; bad.append((s, "margins"))
        s = starter(); s["role_overrides"] = {"hook": {"text_blocks": []}}; bad.append((s, "role_overrides.hook"))
        for spec, where in bad:
            with self.subTest(where=where):
                with self.assertRaises(slide_spec.SpecInvalid) as e:
                    slide_spec.validate(spec)
                self.assertTrue(any(where in err for err in e.exception.errors), e.exception.errors)

    def test_role_overrides_and_placeholders(self):
        spec = starter()
        spec["role_overrides"] = {"hook": {"background": {"type": "solid", "color_role": "accent"}}}
        slide_spec.validate(spec)
        self.assertEqual(slide_spec.for_slide(spec, "hook")["background"]["color_role"], "accent")
        self.assertEqual(slide_spec.for_slide(spec, "point")["background"]["color_role"], "bg")
        block = {"role": "kicker", "text": "{n} מתוך {total}"}
        self.assertEqual(slide_spec.bind_text(block, "", "", 2, 5), "2 מתוך 5")
        self.assertEqual(slide_spec.bind_text({"role": "headline"}, HEAD, "", 1, 1), HEAD)
        self.assertEqual(slide_spec.bind_text({"role": "cta"}, HEAD, BODY, 1, 1), "")


# ─── Palette variants ────────────────────────────────────────────────────────

class Palette(unittest.TestCase):
    def test_roles_resolve_from_the_brief_variant(self):
        facts = post_brief.load_context(27)["brand_facts"]
        gift = post_brief.resolve_palette(facts, "gift buyers")
        teams = post_brief.resolve_palette(facts, "sports teams")
        self.assertEqual(roles(gift["label"])["accent"], "#E3262F")
        self.assertEqual(roles(teams["label"])["accent"], "#C9A227")
        self.assertEqual(roles(gift["label"])["bg"], roles(teams["label"])["bg"])
        for label in (v["label"] for v in facts["palette_variants"]):
            self.assertEqual(set(roles(label)), set(slide_spec.COLOR_ROLES))   # every brief variant has roles

    def test_never_guessed(self):
        with self.assertRaises(slide_spec.PaletteMissing):
            slide_spec.palette_roles(27, "pink + green")        # a palette exists: a wrong variant is a mismatch
        # no palette at all (2026-11-24, core/brand_brief): neutral colours, never invented ones
        from core import brand_brief
        self.assertEqual(slide_spec.palette_roles(999999, RED), brand_brief.NEUTRAL)

    def test_a_set_never_mixes_variants(self):
        problems = slide_design.check(27, starter(GOLD), {"label": RED, "segment": "b2c"},
                                      [{"copy_headline": HEAD, "role": "point"}])
        self.assertEqual(problems[0]["code"], "palette")


# ─── Templates: save / restore / versioning / critiques ──────────────────────

class Templates(unittest.TestCase):
    def setUp(self):
        self.stores = FakeStores().install(self)

    def test_versions(self):
        a = starter()
        v1 = slide_templates.save(27, "carousel gift", a, note="first")
        self.assertEqual(v1["version"], 1)
        self.assertEqual(slide_templates.save(27, "carousel gift", a)["version"], 1)     # unchanged = no version
        b = copy.deepcopy(a); b["accent"] = {"type": "bar", "position": "top", "size": 16, "color_role": "accent"}
        self.assertEqual(slide_templates.save(27, "carousel gift", b)["version"], 2)
        self.assertEqual(slide_templates.get(27, "carousel gift")["spec"]["accent"]["type"], "bar")
        self.assertEqual(slide_templates.get(27, "carousel gift", 1)["spec"]["accent"]["type"], "none")
        back = slide_templates.restore(27, "carousel gift", 1)
        self.assertEqual((back["version"], back["spec"]), (3, a))                       # restore = a NEW version
        self.assertEqual([h["version"] for h in slide_templates.history(27, "carousel gift")], [1, 2, 3])
        self.assertIn("restored from version 1", slide_templates.history(27, "carousel gift")[-1]["note"])
        with self.assertRaises(slide_templates.TemplateError):
            slide_templates.restore(27, "carousel gift", 9)
        with self.assertRaises(slide_spec.SpecInvalid):
            slide_templates.save(27, "carousel gift", {"version": 1})

    def test_record_shape_and_variant_lookup(self):
        slide_templates.save(27, "gift", starter(RED))
        slide_templates.save(27, "teams", starter(GOLD))
        t = slide_templates.get(27, "teams")
        for key in ("name", "spec", "version", "created_at"):
            self.assertIn(key, t)
        self.assertEqual(slide_templates.for_variant(27, RED)["name"], "gift")
        self.assertEqual(slide_templates.for_variant(27, GOLD)["name"], "teams")
        self.assertIsNone(slide_templates.for_variant(27, "other"))

    def test_critiques_are_kept_and_move_with_the_template(self):
        slide_templates.add_critique(27, "draft look", "הכותרת קטנה מדי", item_id="abc123")
        self.assertEqual(slide_templates.critiques(27)[0]["text"], "הכותרת קטנה מדי")    # before it exists
        slide_templates.save(27, "draft look", starter())
        slide_templates.add_critique(27, "draft look", "פחות אדום", version=1)
        texts = [c["text"] for c in slide_templates.critiques(27, "draft look")]
        self.assertEqual(texts, ["פחות אדום", "הכותרת קטנה מדי"])                       # newest first, none lost
        with self.assertRaises(slide_templates.TemplateError):
            slide_templates.add_critique(27, "draft look", " ")


# ─── Rendering ───────────────────────────────────────────────────────────────

def golden_cases():
    """name → (spec, copy_headline, copy_body, n, total, role)."""
    plain = starter(RED)
    split = starter(GOLD, "1:1")
    split["background"] = {"type": "split", "color_role": "bg", "second_color_role": "fg", "split_ratio": 0.62,
                           "split_direction": "horizontal"}
    split["text_blocks"] = [
        {"role": "headline", "anchor": "center", "align": "center", "x_pct": 0, "y_pct": 31, "max_width_pct": 82,
         "max_height_pct": 40, "font_family": "Rubik", "size": 84, "weight": 800, "line_spacing": 1.1, "color_role": "fg"},
        {"role": "body", "anchor": "center", "align": "right", "x_pct": 0, "y_pct": 81, "max_width_pct": 82,
         "max_height_pct": 28, "font_family": "Assistant", "size": 38, "weight": 600, "line_spacing": 1.3,
         "color_role": "bg"}]
    split["accent"] = {"type": "number_badge", "position": "top_right", "size": 72, "color_role": "accent"}
    split["counter"] = {"show": False}
    story = starter(RED, "9:16")
    story["background"] = {"type": "gradient", "color_role": "bg", "second_color_role": "accent",
                           "gradient_direction": "top_to_bottom"}
    story["text_blocks"][0].update({"y_pct": 30, "anchor": "top", "max_height_pct": 20})
    story["text_blocks"][1].update({"y_pct": 52, "anchor": "top", "max_height_pct": 14})
    story["accent"] = {"type": "underline", "position": "under_headline", "size": 10, "color_role": "accent"}
    story["counter"] = {"show": True, "style": "dots", "position": "top_left", "color_role": "fg"}
    return {"rtl_plain_4x5": (plain, HEAD, BODY, 2, 5, "point"),
            "split_badge_1x1": (split, "למה זו המתנה הכי טובה?", "כי Shin Sekai מציירים אותה רק בשבילו.", 3, 5, "point"),
            "gradient_story_9x16": (story, "3 סיבות להזמין עכשיו", "הזמנה עד 1.12 מגיעה לחנוכה", 1, 4, "hook")}


@NEEDS_RAQM
class Render(unittest.TestCase):
    def _img(self, png):
        from PIL import Image
        return Image.open(io.BytesIO(png)).convert("RGB")

    def test_hebrew_rtl_against_golden_images(self):
        from PIL import ImageChops, ImageStat
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        for name, (spec, head, body, n, total, role) in golden_cases().items():
            with self.subTest(name=name):
                png = sr.render_slide(spec, roles(spec["palette_variant"]), head, body, n, total, role)
                path = os.path.join(GOLDEN_DIR, f"slide_{name}.png")
                if UPDATE:
                    with open(path, "wb") as f:
                        f.write(png)
                    continue
                with open(path, "rb") as f:
                    golden = self._img(f.read())
                got = self._img(png)
                self.assertEqual(got.size, golden.size)
                diff = ImageChops.difference(got, golden)
                self.assertLess(max(ImageStat.Stat(diff).mean), 0.8, f"{name} differs from its golden image")

    def test_the_first_letter_is_on_the_right(self):
        """Logical "אב": א must be drawn at the right edge (RTL), ב left of it."""
        from PIL import ImageChops
        bg = self._img(sr.render_slide(one_block(text="{body}"), roles(), "", "", 1, 1))   # an empty canvas
        ab = self._img(sr.render_slide(one_block(), roles(), "אב"))
        alef = self._img(sr.render_slide(one_block(), roles(), "א"))
        bet = self._img(sr.render_slide(one_block(), roles(), "ב"))
        box = ImageChops.difference(alef, bg).getbbox()
        crop = lambda im: im.crop((box[0], 0, box[2], im.height))
        same = ImageChops.difference(crop(ab), crop(alef)).getbbox()
        other = ImageChops.difference(crop(ab), crop(bet)).getbbox()
        self.assertIsNone(same, "the right-most glyph of אב is not א")
        self.assertIsNotNone(other)
        ab_box = ImageChops.difference(ab, bg).getbbox()
        self.assertLess(ab_box[0], box[0])                       # ב extends to the LEFT of א

    def test_text_is_anchored_to_the_box_right_edge(self):
        from PIL import ImageChops
        bg = self._img(sr.render_slide(one_block(text="{body}"), roles(), "", "", 1, 1))
        for x_pct in (0, 10):
            img = self._img(sr.render_slide(one_block(x_pct=x_pct, size=90, max_width_pct=70), roles(), "שלום"))
            right = 1080 - 64 - 1080 * x_pct / 100
            self.assertAlmostEqual(ImageChops.difference(img, bg).getbbox()[2], right, delta=12)

    def test_wrapping_is_measured_and_keeps_word_order(self):
        font = sr._font("Assistant", 40, 400)
        text = "כל זוג נעליים מעוצב פעם אחת בלבד בשביל אדם אחד עם הסיפור שלו והצבעים שלו"
        lines = sr.wrap(text, font, 400)
        self.assertGreater(len(lines), 2)
        self.assertEqual(" ".join(lines), text)
        for line in lines:
            self.assertLessEqual(font.getlength(line, direction="rtl", language="he"), 400)
        for a, b in zip(lines, lines[1:]):                       # greedy: the next word really didn't fit
            self.assertGreater(font.getlength(a + " " + b.split()[0], direction="rtl", language="he"), 400)
        self.assertEqual(sr.wrap("שורה אחת\nשורה שתיים", font, 900), ["שורה אחת", "שורה שתיים"])
        with self.assertRaises(sr._TooWide):
            sr.wrap("מילהארוכהמאודמאודמאוד", font, 60)

    def test_overflow_fails_never_clips(self):
        cases = {
            "more lines than the box": (one_block(size=60, max_height_pct=8), " ".join(["מילה"] * 40)),
            "a word wider than the box": (one_block(size=150, max_width_pct=20), "בלתי־אפשרי"),
            "box past the left margin": (one_block(x_pct=30, max_width_pct=80), "שלום"),
            "outside the safe area": (one_block(y_pct=2, anchor="top"), "שלום"),
        }
        for why, (spec, text) in cases.items():
            with self.subTest(why=why):
                with self.assertRaises(sr.RenderError) as e:
                    sr.render_slide(spec, roles(), text)
                self.assertIn("overflow", [p["code"] for p in e.exception.problems])

    def test_headline_word_limit(self):
        words = " ".join(["מילה"] * 11)
        with self.assertRaises(sr.RenderError) as e:
            sr.render_slide(one_block(size=40), roles(), words)
        self.assertEqual(e.exception.problems[0]["code"], "headline_words")
        spec = one_block(size=40)
        spec["guards"] = {"headline_max_words": 4}
        with self.assertRaises(sr.RenderError):
            sr.render_slide(spec, roles(), "אחת שתיים שלוש ארבע חמש")
        spec["guards"] = {"headline_max_words": 5}
        sr.render_slide(spec, roles(), "אחת שתיים שלוש ארבע חמש")

    def test_contrast_guard(self):
        with self.assertRaises(sr.RenderError) as e:
            sr.render_slide(one_block(color_role="bg"), roles(), "שלום")          # dark on dark
        self.assertEqual(e.exception.problems[0]["code"], "contrast")
        self.assertLess(sr.contrast((255, 255, 255), (0xA6, 0xA6, 0xA6)), 3)
        self.assertAlmostEqual(sr.contrast((0, 0, 0), (255, 255, 255)), 21.0, places=1)

    def test_contrast_over_a_photo_counts_the_scrim(self):
        from PIL import Image
        white = png_of(Image.new("RGB", (400, 600), (250, 250, 250)))
        spec = one_block()
        spec["background"] = {"type": "image", "image_asset_id": "PHOTO_0123456789", "scrim_opacity": 0}
        with self.assertRaises(sr.RenderError) as e:
            sr.render_slide(spec, roles(), "שלום", image_loader=lambda a: white)
        self.assertEqual(e.exception.problems[0]["code"], "contrast")
        spec["background"]["scrim_opacity"] = 0.75                                    # a dark wash fixes it
        img = self._img(sr.render_slide(spec, roles(), "שלום", image_loader=lambda a: white))
        self.assertLess(img.getpixel((20, 20))[0], 100)
        spec["background"]["image_asset_id"] = "MISSING_0123456789"
        with self.assertRaises(sr.RenderError) as e:
            sr.render_slide(spec, roles(), "שלום", image_loader=lambda a: (_ for _ in ()).throw(PermissionError("no")))
        self.assertEqual(e.exception.problems[0]["code"], "asset")

    def test_overlap_and_hebrew_alignment(self):
        spec = one_block()
        spec["text_blocks"].append(dict(spec["text_blocks"][0], role="body", font_family="Assistant", weight=400, size=60))
        with self.assertRaises(sr.RenderError) as e:
            sr.render_slide(spec, roles(), "כותרת", "גוף")
        self.assertIn("overlap", [p["code"] for p in e.exception.problems])
        with self.assertRaises(sr.RenderError) as e:
            sr.render_slide(one_block(align="left"), roles(), "שלום")
        self.assertEqual(e.exception.problems[0]["code"], "hebrew_align")
        sr.render_slide(one_block(align="left"), roles(), "SHIN SEKAI")             # Latin may be left-aligned

    def test_formats(self):
        for aspect, size in (("4:5", (1080, 1350)), ("1:1", (1080, 1080)), ("9:16", (1080, 1920))):
            img = self._img(sr.render_slide(starter(RED, aspect), roles(), "שלום", "עולם", 1, 3))
            self.assertEqual(img.size, size)
        self.assertEqual(slide_spec.DEFAULT_CANVAS, (1080, 1350))

    def test_a_set_renders_from_one_spec_and_one_palette(self):
        slides = [{"copy_headline": "אחת", "role": "hook", "n": 1, "total": 4},
                  {"copy_headline": "שתיים", "copy_body": "גוף", "role": "point", "n": 3, "total": 4}]
        pngs = sr.render_set(starter(), roles(), slides)
        self.assertEqual(len(pngs), 2)
        bad = slides + [{"copy_headline": " ".join(["מילה"] * 12), "role": "point"}]
        with self.assertRaises(sr.RenderError) as e:
            sr.render_set(starter(), roles(), bad)
        self.assertTrue(all(p["slide"] == 3 for p in e.exception.problems))


# ─── The design loop (Social tab) ────────────────────────────────────────────

class DesignLoop(unittest.TestCase):
    def setUp(self):
        self.stores = FakeStores().install(self)
        self.lines, self.uploads, self.claude = [], [], []
        from agents import media_agent
        self.media_agent = media_agent
        saved = (slide_design._tell, slide_design._lang, slide_design._alert, slide_design.upload_png,
                 media_agent.safe_claude_json_call, media_agent.gen.generate_image, media_agent.gen.generate_video)
        slide_design._tell = lambda cid, item, line: self.lines.append(line)
        slide_design._lang = lambda cid: "he"
        slide_design._alert = lambda lines: None
        slide_design.upload_png = lambda cid, name, png: self.uploads.append(name) or {
            "id": f"UP{len(self.uploads):02d}_0123456789", "webViewLink": ""}
        media_agent.safe_claude_json_call = self._claude
        media_agent.gen.generate_image = lambda *a, **k: self.fail("an image model was called")
        media_agent.gen.generate_video = lambda *a, **k: self.fail("a video model was called")
        self.answers = []

        def restore():
            (slide_design._tell, slide_design._lang, slide_design._alert, slide_design.upload_png,
             media_agent.safe_claude_json_call, media_agent.gen.generate_image, media_agent.gen.generate_video) = saved
        self.addCleanup(restore)

    def _claude(self, system, user, **kw):
        self.claude.append(json.loads(user))
        if not self.answers:
            self.fail("the design agent was called unexpectedly")
        return self.answers.pop(0)

    def _post_item(self):
        return {"id": "post000001", "kind": "post", "title": "למה עיצוב אישי", "channels": ["instagram"],
                "caption": "כיתוב", "planned_date": "", "post": {"set_id": "gift-why-1",
                                                                  "palette": {"label": RED, "segment": "b2c"}}}

    def _text_slides(self):
        return [{"asset_id": "s1", "role": "hook", "copy_headline": HEAD, "copy_body": "", "aspect_ratio": "4:5",
                 "n": 1, "total": 3},
                {"asset_id": "s3", "role": "cta", "copy_headline": "מזמינים עד ה-1 בחודש", "copy_body": BODY,
                 "aspect_ratio": "4:5", "n": 3, "total": 3}]

    @NEEDS_RAQM
    def test_post_design_comes_from_the_starter_without_the_agent(self):
        out = slide_design.open_for_post(27, self._post_item(), self._text_slides())
        item = out["item"]
        self.assertEqual((item["kind"], item["status"], item["file_id"]), ("design", "waiting", ""))
        self.assertEqual(item["design"]["based_on"], {"starter": "b2c"})
        self.assertEqual(item["design"]["palette"]["label"], RED)
        self.assertEqual(self.claude, [])                                            # it rendered: no call
        row = sq.view(27, "he")["items"][0]
        self.assertEqual(len(row["design"]["previews"]), 2)
        self.assertTrue(any("Rubik" in line for line in row["design"]["spec_lines"]))
        png = slide_design.preview_png(27, item["id"], 2)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

    @NEEDS_RAQM
    def test_critique_revises_the_spec_and_is_remembered(self):
        item = slide_design.open_for_post(27, self._post_item(), self._text_slides())["item"]
        better = copy.deepcopy(item["design"]["spec"])
        better["accent"] = {"type": "bar", "position": "right", "size": 18, "color_role": "accent"}
        broken = copy.deepcopy(better)
        broken["text_blocks"][0]["size"] = 200                                       # overflows → re-asked
        self.answers = [{"spec": broken, "rationale": "x"}, {"spec": better, "rationale": "הוספתי פס אדום בצד"}]
        slide_design.critique(27, item["id"], "חסר משהו מהמותג, תוסיפו נגיעה של אדום")
        d = sq.get(27, item["id"])["design"]
        self.assertEqual((d["rev"], d["state"], d["spec"]["accent"]["type"]), (2, "ready", "bar"))
        self.assertEqual(len(self.claude), 2)
        self.assertEqual(self.claude[0]["critique"], "חסר משהו מהמותג, תוסיפו נגיעה של אדום")
        self.assertTrue(self.claude[1]["problems"])                                  # the guard failure fed back
        self.assertIn("schema", self.claude[0])
        self.assertIn("Social & Design Craft Library", self.claude[0]["craft_library"])
        self.assertEqual(slide_templates.critiques(27)[0]["text"], "חסר משהו מהמותג, תוסיפו נגיעה של אדום")
        self.assertIn("(2)", self.lines[-1])
        # The next design hears the client's taste
        self.answers = [{"spec": better, "rationale": "y"}]
        slide_design.critique(27, item["id"], "יותר אוויר")
        self.assertIn("חסר משהו מהמותג, תוסיפו נגיעה של אדום", self.claude[-1]["earlier_critiques"])

    @NEEDS_RAQM
    def test_a_critique_that_never_passes_keeps_the_last_good_design(self):
        item = slide_design.open_for_post(27, self._post_item(), self._text_slides())["item"]
        broken = copy.deepcopy(item["design"]["spec"])
        broken["text_blocks"][0]["color_role"] = "bg"                                # invisible text
        self.answers = [{"spec": broken}] * slide_design.MAX_DESIGN_ATTEMPTS
        slide_design.critique(27, item["id"], "טקסט שחור")
        d = sq.get(27, item["id"])["design"]
        self.assertEqual((d["rev"], d["state"]), (1, "ready"))
        self.assertEqual(d["spec"], item["design"]["spec"])
        self.assertEqual(d["last_failure"][0]["code"], "contrast")
        self.assertEqual(len(self.claude), slide_design.MAX_DESIGN_ATTEMPTS)          # bounded

    @NEEDS_RAQM
    def test_approval_saves_the_template_and_finalizes_one_consistent_set(self):
        item = slide_design.open_for_post(27, self._post_item(), self._text_slides())["item"]
        done = sq.approve(27, [item["id"]])
        from core import post_pipeline
        post_pipeline.start(27, done)                                                # → slide_design.approved
        tpl = slide_templates.get(27, item["design"]["template_name"])
        self.assertEqual((tpl["version"], tpl["palette_variant"]), (1, RED))
        made = [i for i in self.stores.queue if i.get("source") == "slide"]
        self.assertEqual([i["post_ref"]["asset_id"] for i in made], ["s1", "s3"])
        self.assertEqual({(i["post_ref"]["template"], i["post_ref"]["template_version"], i["post_ref"]["palette"])
                          for i in made}, {(tpl["name"], 1, RED)})                    # ONE template, ONE palette
        self.assertEqual({i["status"] for i in made}, {"approved"})                  # these pixels were approved
        self.assertEqual(len(self.uploads), 2)
        self.assertIn("נשמר", self.lines[-1])
        # Publishing goes through the gate as before; the design item itself never can
        sq.check(27, "instagram", item_id=made[0]["id"])
        with self.assertRaises(sq.GateClosed):
            sq.check(27, "instagram", item_id=item["id"])
        # The next post in this palette starts from the approved template
        spec, name, based = slide_design.base_spec(27, {"label": RED, "segment": "b2c"})
        self.assertEqual(based, {"template": tpl["name"], "version": 1})
        # …and a gold post never borrows it
        _, _, gold = slide_design.base_spec(27, {"label": GOLD, "segment": "b2b"})
        self.assertEqual(gold, {"starter": "b2b"})

    def test_a_design_item_is_not_publishable_or_editable(self):
        item = sq.add_design(27, {"template_name": "t1", "post_id": "", "spec": {}, "slides": [], "palette": {}})
        with self.assertRaises(sq.QueueError):
            sq.edit(27, item["id"], caption="x")
        with self.assertRaises(sq.QueueError) as e:
            sq.reject(27, item["id"], "לא טוב")
        self.assertEqual(e.exception.code, "use_critique")
        sq.approve(27, [item["id"]])
        with self.assertRaises(sq.QueueError):
            sq.mark_published(27, item["id"])
        with self.assertRaises(sq.GateClosed):
            sq.check(27, "instagram", item_id=item["id"])

    def test_text_slides_never_reach_an_image_model(self):
        """The whole path: a post's text slides → design → (critique) → final
        PNGs. gen.* fails the test if touched; the renderer is the only drawer."""
        import inspect
        for fn in (slide_design.open_for_post, slide_design.revise, slide_design.approved, slide_design._finalize,
                   slide_design.render_asset, slide_design.preview_png, sr.render_slide, sr._render):
            src = inspect.getsource(fn)
            self.assertNotIn("generate_image", src)
            self.assertNotIn("generate_video", src)
            self.assertNotIn("gen.", src)
        src = inspect.getsource(self.media_agent.generate_from_brief)
        text_branch = src.split('if brief.asset_type == "text_slide":')[1].split('if brief.asset_type == "real_photo":')[0]
        self.assertIn("slide_design.render_asset", text_branch)
        self.assertNotIn("_generate_brief_asset", text_branch)
        # The copy-leak guard on the generated path is intact
        self.assertIn("assert_copy_free", inspect.getsource(self.media_agent._generate_brief_asset))


class Wiring(unittest.TestCase):
    def test_action_routes_parity_dashboard(self):
        from core import agent_actions, chat_parity, admin_view
        self.assertIn("design_slides", agent_actions.ACTIONS)
        self.assertEqual(chat_parity.PARITY["POST /api/client/social/{item_id}/design"][1], "design_slides")
        self.assertIn("GET /api/client/social/{item_id}/slide/{n}", chat_parity.PARITY)
        self.assertIn("client_social_slide_preview", admin_view.READ_ENDPOINTS)
        with open(os.path.join(ROOT, "core", "api_server.py"), encoding="utf-8") as f:
            api = f.read()
        self.assertIn('@app.post("/api/client/social/{item_id}/design")', api)
        self.assertIn("_slide_renderer.startup_check()", api)
        with open(os.path.join(ROOT, "dashboard", "client", "index.html"), encoding="utf-8") as f:
            html = f.read()
        for needle in ("function socialDesignRow", "data-soc-critique-send", "soc_design_approve:", "/design'"):
            self.assertIn(needle, html)
        for module in ("admin_service", "client_deletion", "deletion_guard"):
            with open(os.path.join(ROOT, "core", f"{module}.py"), encoding="utf-8") as f:
                self.assertIn("slide_templates:", f.read(), module)


if __name__ == "__main__":
    unittest.main()
