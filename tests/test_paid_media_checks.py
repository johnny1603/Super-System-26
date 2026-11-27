"""The two paid-media checks that run on the PRODUCED file (2026-11-28,
knowledge/paid-media-craft.md §2.1 and §2.3):

- core/crop_survival: a display image is cropped the way Google crops it and
  the subject must still be there — a check, not advice;
- core/ad_compress: a file is compressed to its cap, the damage is measured,
  and one that cannot get there without visible degradation FAILS by name.

Also: the craft file exists, is loaded in full, and names both checks.

Run from the repo root:  python tests/test_paid_media_checks.py -v
"""
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import ad_compress as ac                  # noqa: E402
from core import crop_survival as cs                # noqa: E402
from core import google_ads_formats as gaf          # noqa: E402

LAND = gaf.SLOTS["landscape"].size
REAL = os.path.join(ROOT, "dashboard", "assets", "agents", "yoav-portrait.png")


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _scene(cx, cy, sw, sh, size=LAND, noisy=False):
    """A product-like subject (two solid shapes) on a plain or textured backdrop."""
    from PIL import Image, ImageDraw
    img = Image.effect_noise(size, 40).convert("RGB") if noisy else Image.new("RGB", size, (232, 226, 214))
    d = ImageDraw.Draw(img)
    d.ellipse((cx - sw // 2, cy - sh // 2, cx + sw // 2, cy + sh // 2), fill=(180, 40, 30))
    d.rectangle((cx - sw // 4, cy - sh // 4, cx + sw // 4, cy + sh // 4), fill=(30, 40, 120))
    return _png(img)


def _real_on_landscape(x_centre, height):
    """The repo's real portrait photo, placed on a landscape canvas."""
    from PIL import Image
    with Image.open(REAL) as im:
        face = im.convert("RGB")
    face.thumbnail((height, height))
    canvas = Image.new("RGB", LAND, (240, 238, 232))
    canvas.paste(face, (x_centre - face.size[0] // 2, (LAND[1] - face.size[1]) // 2))
    return _png(canvas)


class CropSurvival(unittest.TestCase):
    def crops(self, result):
        return {c["aspect"]: c for c in result["crops"]}

    def test_the_crops_come_from_the_table(self):
        r = cs.check(_scene(600, 314, 300, 240), "landscape")
        self.assertEqual([c["aspect"] for c in r["crops"]], list(gaf.CROP_TARGETS["landscape"]))
        self.assertEqual([c["aspect"] for c in r["crops"]], ["1:1", "4:1"])
        c = self.crops(r)
        self.assertEqual((c["1:1"]["size"], c["4:1"]["size"]), ([628, 628], [1200, 300]))     # the real pixels kept
        self.assertEqual(c["1:1"]["box"], [286, 0, 914, 628])

    def test_a_centred_subject_survives(self):
        r = cs.check(_scene(600, 314, 300, 240), "landscape")
        self.assertTrue(r["ok"], r["problems"])
        self.assertEqual(r["problems"], [])
        self.assertGreaterEqual(min(c["retained"] for c in r["crops"]), 0.95)
        # found where it was drawn (within the working copy's resolution)
        x0, y0, x1, y1 = r["subject"]["box"]
        self.assertAlmostEqual((x0 + x1) / 2, 600, delta=12)
        self.assertAlmostEqual((y0 + y1) / 2, 314, delta=12)

    def test_an_off_centre_subject_disappears_and_the_check_says_where(self):
        r = cs.check(_scene(260, 314, 300, 240), "landscape")
        self.assertFalse(r["ok"])
        c = self.crops(r)
        self.assertFalse(c["1:1"]["ok"])
        self.assertTrue(c["4:1"]["ok"])                    # still inside the strip — only the square loses it
        self.assertLess(c["1:1"]["retained"], cs.MIN_RETAINED)
        self.assertEqual(len(r["problems"]), 1)
        self.assertIn("1:1 centre crop", r["problems"][0])
        self.assertIn("landscape 1200x628", r["problems"][0])

    def test_a_subject_at_the_bottom_is_lost_in_the_strip(self):
        c = self.crops(cs.check(_scene(600, 520, 300, 200), "landscape"))
        self.assertTrue(c["1:1"]["ok"])
        self.assertFalse(c["4:1"]["ok"])

    def test_a_textured_backdrop_is_not_the_subject(self):
        self.assertTrue(cs.check(_scene(600, 314, 300, 240, noisy=True), "landscape")["ok"])
        self.assertFalse(cs.check(_scene(220, 314, 300, 240, noisy=True), "landscape")["ok"])

    def test_no_subject_fails_closed(self):
        from PIL import Image
        for content in (_png(Image.new("RGB", LAND, (200, 200, 200))), _png(Image.effect_noise(LAND, 40).convert("RGB")),
                        b"not an image"):
            r = cs.check(content, "landscape")
            self.assertFalse(r["ok"])
            self.assertEqual(r["crops"], [])
            self.assertEqual(len(r["problems"]), 1)
        self.assertIn("no subject", cs.check(_png(Image.new("RGB", LAND, (200, 200, 200))), "landscape")["problems"][0])

    def test_a_declared_product_box_must_sit_inside_every_crop(self):
        content = _scene(600, 314, 300, 240)
        inside = cs.check(content, "landscape", subject_box=(0.38, 0.30, 0.62, 0.70))
        self.assertTrue(inside["ok"], inside["problems"])
        self.assertEqual({c["box_inside"] for c in inside["crops"]}, {1.0})
        # the producer says the product ALSO reaches the left edge: the pixels pass, the declaration does not
        wide = cs.check(content, "landscape", subject_box=(0.02, 0.30, 0.62, 0.70))
        self.assertFalse(wide["ok"])
        self.assertIn("declared product area", wide["problems"][0])

    def test_the_other_slots(self):
        sq = gaf.SLOTS["square"].size
        self.assertEqual([c["aspect"] for c in cs.check(_scene(600, 600, 400, 300, sq), "square")["crops"]], ["1.91:1"])
        self.assertTrue(cs.check(_scene(600, 600, 400, 300, sq), "square")["ok"])
        self.assertFalse(cs.check(_scene(600, 1050, 400, 250, sq), "square")["ok"])        # at the bottom of a square
        # a logo is never cropped by this check
        self.assertEqual(cs.check(_scene(600, 600, 400, 300, sq), "logo_square")["crops"], [])

    def test_the_crops_themselves_can_be_looked_at(self):
        out = cs.crops(_scene(600, 314, 300, 240), "landscape")
        self.assertEqual({a: gaf.measure(b) for a, b in out.items()}, {"1:1": (628, 628), "4:1": (1200, 300)})

    def test_on_a_real_photo(self):
        """The repo's real portrait on a landscape canvas: sized for the strip
        and centred it survives; the same picture pushed left does not."""
        good = cs.check(_real_on_landscape(600, 330), "landscape")
        self.assertTrue(good["ok"], good["problems"])
        left = cs.check(_real_on_landscape(130, 330), "landscape")
        self.assertFalse(left["ok"])
        self.assertFalse(self.crops(left)["1:1"]["ok"])
        # full height and centred: fine in the square, cut to pieces by the 4:1 strip
        tall = self.crops(cs.check(_real_on_landscape(600, 600), "landscape"))
        self.assertTrue(tall["1:1"]["ok"])
        self.assertFalse(tall["4:1"]["ok"])

    def test_the_log_line(self):
        line = cs.line(cs.check(_scene(260, 314, 300, 240), "landscape"))
        self.assertTrue(line.startswith("[crop_survival] landscape 1200x628 colour: 1:1 "))
        self.assertIn("FAIL", line)
        self.assertIn("4:1 100% ok", line)


class CompressToCap(unittest.TestCase):
    CAP = gaf.BANNER_MAX_BYTES

    def photo(self, size):
        from PIL import Image
        with Image.open(REAL) as im:
            return im.convert("RGB").resize((max(size), max(size))).crop((0, 0, size[0], size[1]))

    def test_within_the_cap_is_left_alone(self):
        small = _png(self.photo((320, 50)))
        out = ac.to_cap(small, self.CAP, gaf.BANNER_FILE_TYPES, "banner_320x50")
        self.assertEqual((out["changed"], out["content"], out["method"]), (False, small, "as is"))

    def test_a_wide_banner_is_brought_under_150_kb(self):
        raw = _png(self.photo((970, 250)))
        self.assertGreater(len(raw), self.CAP)                       # a normal export is over the cap
        out = ac.to_cap(raw, self.CAP, gaf.BANNER_FILE_TYPES, "banner_970x250")
        self.assertLessEqual(out["bytes"], self.CAP)
        self.assertEqual(len(out["content"]), out["bytes"])
        self.assertEqual(gaf.measure(out["content"]), (970, 250))    # the size is the spec: never resized
        self.assertIsNone(out["resized_from"])
        self.assertGreaterEqual(out["psnr"], ac.MIN_PSNR)
        self.assertTrue(out["changed"])
        row = next(r for r in gaf.required_assets("display", banners="all") if r["slot"] == "banner_970x250")
        self.assertEqual(gaf.check_file(row, out["content"]), [])
        self.assertIn("KB ->", ac.line("banner_970x250", out))

    def test_it_fails_loudly_rather_than_ship_something_soft(self):
        from PIL import Image
        noise = _png(Image.effect_noise((970, 250), 90).convert("RGB"))      # incompressible on purpose
        with self.assertRaises(ac.CannotReachCap) as caught:
            ac.to_cap(noise, self.CAP, gaf.BANNER_FILE_TYPES, "banner_970x250")
        message = str(caught.exception)
        self.assertIn("banner_970x250", message)
        self.assertIn("cannot reach 150 KB without visible degradation", message)
        self.assertIn("dB", message)
        self.assertLess(caught.exception.best["psnr"], ac.MIN_PSNR)
        self.assertNotIn("content", caught.exception.best)

    def test_jpeg_is_never_pushed_under_the_quality_floor(self):
        from PIL import Image
        noise = _png(Image.effect_noise((300, 1050), 120).convert("RGB"))
        try:
            out = ac.to_cap(noise, 40 * gaf.KB, ("jpg",), "tight")
            self.assertGreaterEqual(out["quality"], ac.MIN_QUALITY)
        except ac.CannotReachCap as e:
            self.assertGreaterEqual(e.best["quality"], ac.MIN_QUALITY)

    def test_a_cut_out_logo_keeps_its_transparency(self):
        from PIL import Image, ImageDraw
        logo = Image.new("RGBA", (1200, 1200), (0, 0, 0, 0))
        ImageDraw.Draw(logo).ellipse((200, 200, 1000, 1000), fill=(255, 76, 31, 255))
        out = ac.to_cap(_png(logo), gaf.SMALL_MAX_BYTES, ("jpg", "png"), "logo_square")
        self.assertEqual(out["file_type"], "png")                    # JPEG would have flattened it
        with Image.open(io.BytesIO(out["content"])) as back:
            self.assertEqual(back.convert("RGBA").getpixel((5, 5))[3], 0)

    def test_a_slot_with_a_minimum_may_step_down_and_says_so(self):
        from PIL import Image
        heavy = _png(Image.effect_noise((1200, 1200), 60).convert("RGB"))
        with self.assertRaises(ac.CannotReachCap):
            ac.to_cap(heavy, gaf.SMALL_MAX_BYTES, ("jpg", "png"), "logo_square")
        try:
            out = ac.to_cap(heavy, gaf.SMALL_MAX_BYTES, ("jpg", "png"), "logo_square", floor=(300, 300))
        except ac.CannotReachCap:
            return                                                  # even the floor could not do it: still loud
        self.assertEqual(out["resized_from"], [1200, 1200])
        self.assertGreaterEqual(min(out["size"]), 300)
        self.assertIn("stepped down from 1200x1200", out["method"])
        self.assertLessEqual(out["bytes"], gaf.SMALL_MAX_BYTES)

    def test_an_unreadable_file_is_an_error_not_a_pass(self):
        with self.assertRaises(ValueError):
            ac.to_cap(b"not an image", self.CAP, gaf.BANNER_FILE_TYPES, "x")


class Craft(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "knowledge", "paid-media-craft.md"), encoding="utf-8") as f:
            self.text = f.read()

    def test_the_five_rules_are_there(self):
        for needle in ("Crop survival", "core/crop_survival", "Text belongs in text fields", "The 150 KB problem",
                       "core/ad_compress", "Coverage beats polish", "Brand discipline", "core/google_ads_formats"):
            self.assertIn(needle, self.text)

    def test_it_holds_no_numbers_of_its_own(self):
        """The judgment layer never restates the table: a size written here would be a second source."""
        import re
        self.assertEqual(re.findall(r"\b\d{3,4}\s*[x×]\s*\d{2,4}\b", self.text), [])

    def test_it_is_dated(self):
        self.assertIn(f"Verified: {gaf.VERIFIED}", self.text)
        self.assertIn(f"Re-verify by: {gaf.REVERIFY_BY}", self.text)


if __name__ == "__main__":
    unittest.main()
