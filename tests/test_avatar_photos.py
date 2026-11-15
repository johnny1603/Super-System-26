"""The presenter's photo SET (core/avatar_photos) — 2026-11-16.

What must stay true:
- own photos: 5-6 asked for, with the plain why (front, eye level, soft daylight,
  angles, expressions); fewer good ones = "send N more", nothing is made;
- each photo is checked in code for what code CAN see: image type, size (short
  side), light, contrast, sharpness, duplicates — and the angle/expression
  check is asked of the client, never claimed;
- the clearest, best-lit photo goes first (it is the one the presenter starts from);
- the set survives across uploads and is cleared with the client;
- every client text in 5 languages, no tool or vendor named.
Pillow checks run only where Pillow is installed (prod has it; skipped otherwise).
"""
import io
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}), ("httpx", {}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from test_landing_own import FakeDB        # noqa: E402
from core import avatar_photos as ap       # noqa: E402

VENDOR = re.compile(r"heygen|eleven\s?labs|higgsfield|wan\s?2|soul", re.IGNORECASE)

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def meta(i, w=1600, h=2000, mime="image/jpeg", md5=None):
    return {"id": i, "mimeType": mime, "md5Checksum": md5 or f"m{i}",
            "imageMediaMetadata": {"width": w, "height": h}}


class OnePhoto(unittest.TestCase):
    def test_what_code_can_see(self):
        self.assertEqual(ap.assess(meta("a", mime="application/pdf"))["issues"], ["not_image"])
        self.assertEqual(ap.assess(meta("a", 300, 400))["issues"], ["too_small"])
        self.assertFalse(ap.assess(meta("a", 300, 400))["usable"])
        low = ap.assess(meta("a", 600, 800))
        self.assertEqual((low["issues"], low["usable"]), (["low_res"], True))   # a note, not a refusal
        self.assertEqual(ap.assess(meta("a"))["issues"], [])
        self.assertTrue(ap.assess(meta("a"))["usable"])

    def test_no_size_known_is_not_a_guess(self):
        out = ap.assess({"id": "a", "mimeType": "image/png"})
        self.assertEqual(out["issues"], [])

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed here (prod has it)")
    def test_light_and_sharpness_from_the_thumbnail(self):
        from PIL import Image, ImageDraw

        def png(fill, pattern=False):
            image = Image.new("L", (200, 200), fill)
            if pattern:
                draw = ImageDraw.Draw(image)
                for x in range(0, 200, 10):
                    draw.line([(x, 0), (x, 200)], fill=255 if fill < 128 else 0, width=3)
            out = io.BytesIO()
            image.save(out, "PNG")
            return out.getvalue()
        self.assertIn("dark", ap.assess(meta("a"), png(20))["issues"])
        self.assertIn("bright", ap.assess(meta("a"), png(245))["issues"])
        self.assertIn("blurry", ap.assess(meta("a"), png(130))["issues"])        # flat grey: nothing sharp
        good = ap.assess(meta("a"), png(110, pattern=True))
        self.assertNotIn("blurry", good["issues"])
        self.assertEqual(len(good["fingerprint"]), 64)


class TheSet(unittest.TestCase):
    def test_five_good_is_ready_and_the_best_goes_first(self):
        photos = [ap.assess(meta(f"p{i}", 1000 + 100 * i, 1400)) for i in range(5)]
        out = ap.review_assessed(photos)
        self.assertTrue(out["ready"])
        self.assertEqual(out["best"], "p4")                                      # the sharpest-sized one
        self.assertEqual(out["best_position"], 5)

    def test_too_few_and_duplicates_ask_for_more(self):
        photos = [ap.assess(meta("a", md5="same")), ap.assess(meta("b", md5="same")),
                  ap.assess(meta("c", 300, 300)), ap.assess(meta("d"))]
        out = ap.review_assessed(photos)
        self.assertFalse(out["ready"])
        self.assertEqual(out["good"], 2)
        self.assertEqual(out["needed"], 3)
        self.assertIn("duplicate", photos[1]["issues"])
        text = ap.message(out, "he")
        for needle in ("בגובה העיניים", "באור יום רך", "זוויות", "הבעות", "יש לי 2 מתוך 5",
                       "תמונה 2:", "תמונה 3:", "שלחו עוד 3", "את הזוויות וההבעות אני לא יכול לבדוק לבד"):
            self.assertIn(needle, text, needle)

    def test_the_ready_line(self):
        out = ap.review_assessed([ap.assess(meta(f"p{i}")) for i in range(6)])
        self.assertIn("בחרתי את התמונה הקדמית הכי ברורה", ap.message(out, "he"))

    def test_five_languages_no_tool_names(self):
        for table in list(ap.TEXT.values()) + list(ap.ISSUE_TEXT.values()):
            for lang in ap.LANGS:
                self.assertTrue(table.get(lang), lang)
                self.assertIsNone(VENDOR.search(table[lang]), table[lang])
        empty = ap.review_assessed([])
        for lang in ap.LANGS:
            self.assertIn(str(ap.MIN_PHOTOS), ap.message(empty, lang))


class AcrossUploads(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.saved = ap._db
        ap._db = lambda: self.db

    def tearDown(self):
        ap._db = self.saved

    def test_merge_keeps_earlier_photos_and_clear_empties(self):
        ap.remember(27, ["a", "b"])
        self.assertEqual(ap.merge(27, ["c", "a"]), ["c", "a", "b"])
        ap.remember(27, [f"x{i}" for i in range(20)])
        self.assertEqual(len(ap.stored(27)), ap.MAX_PHOTOS)
        ap.clear(27)
        self.assertEqual(ap.stored(27), [])


class Wiring(unittest.TestCase):
    def test_deleted_with_the_client(self):
        self.assertIn('f"avatar_photos:{int(i)}"', _src("core", "client_deletion.py"))
        for path in (("core", "deletion_guard.py"), ("core", "admin_service.py")):
            for prefix in ('"avatar_photos:"', '"invite_resume:"', '"quarter_goals:"'):
                self.assertIn(prefix, _src(*path), (path, prefix))

    def test_prompts_ask_for_a_set_and_name_no_tool(self):
        support = _src("agents", "support_agent.py")
        self.assertIn("A PHOTO SET, NOT ONE PHOTO", support)
        kit = _src("agents", "avatar_agent.py").split('SOURCE_KIT_SYSTEM = """', 1)[1].split('"""', 1)[0]
        self.assertIn("5-6 photos", kit)
        self.assertIn("soft daylight", kit)
        self.assertIn("never name a vendor or tool", kit)

    def test_the_prepare_hook_checks_own_photos(self):
        body = _src("core", "agent_actions.py").split("def _prepare_presenter_photos(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("avatar_photos.review(", body)
        self.assertIn('return {"clarify": avatar_photos.message(checked, lang)}', body)


if __name__ == "__main__":
    unittest.main()
