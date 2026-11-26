"""The branding package on the client's branding page (core/brand_package,
2026-11-27): three DELIVERABLES — a logo, a branded website, two landing
pages — ONE confirmed mockup per section built from THIS client's material
(their logo file, their palette variants, their product photos, their own
copy), a real way to ask the owner, and every request logged as a buying signal.

Run: python tests/test_brand_package.py
"""
import io
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_brand_page as tbp                    # noqa: E402  (the brief + Drive harness)
import core                                      # noqa: E402
from core import brand_package as pk             # noqa: E402
from core import brand_page as bp                # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
REAL_RENDER = pk.render
PHOTO_COLOURS = [(12, 180, 60), (30, 60, 200), (200, 120, 20)]     # three "product photos", one flat colour each
try:
    from PIL import Image, features
    RAQM = features.check("raqm")
except Exception:
    RAQM = False


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _picture(colour, size=(900, 700), mode="RGB", fmt="PNG") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, colour).save(out, fmt)
    return out.getvalue()


def _section(client_id: int, sid: str, lang: str = "he") -> dict:
    return next(s for s in bp.view(client_id, lang)["package"]["sections"] if s["id"] == sid)


class Base(tbp.Base):
    def setUp(self):
        super().setUp()
        self.signals = []
        self.photos = ["photo-1", "photo-2", "photo-3"]
        names = ("_alert", "render", "_photos", "_photo_bytes", "_business_name")
        saved = [getattr(pk, n) for n in names]
        pk._alert = lambda lines: self.signals.extend(lines)
        pk.render = lambda client_id, section, snap: PNG
        pk._photos = lambda client_id, limit=5: list(self.photos)
        pk._photo_bytes = lambda client_id, file_id: _picture(PHOTO_COLOURS[int(file_id[-1]) - 1])
        pk._business_name = lambda client_id: "Shin Sekai"
        pk._PNG.clear()
        self.addCleanup(lambda: [setattr(pk, n, v) for n, v in zip(names, saved)])
        bp.view(27, "he")                         # the brief of client 27 exists on its row


class TheDeliverables(Base):
    def test_a_logo_a_website_and_two_landing_pages(self):
        package = bp.view(27, "he")["package"]
        self.assertEqual([s["id"] for s in package["sections"]], ["logo", "website", "landing_pages"])
        self.assertEqual([s["title"] for s in package["sections"]], ["לוגו", "אתר ממותג", "שני דפי נחיתה, כל אחד לייעוד אחר"])
        for s in package["sections"]:
            self.assertTrue(s["line"])                                    # one line on what it does for the business
            self.assertEqual(len(s["includes"]), 3)
            self.assertEqual(s["sample"]["state"], "available")
            self.assertIn("קרדיטים", s["sample"]["cost_text"])            # the cost is on the page BEFORE anything is made
        # typography and the visual language are what the work establishes, never products of their own
        titles = " ".join(s["title"] for s in package["sections"])
        for word in ("טיפוגרפיה", "שפה חזותית", "תבניות", "ספר מותג"):
            self.assertNotIn(word, titles)
        said = package["sub"] + " ".join(i for s in package["sections"] for i in s["includes"])
        self.assertIn("הטיפוגרפיה והשפה החזותית", said)
        self.assertIn("בדרך נקבעות", package["sub"])

    def test_every_text_exists_in_five_languages(self):
        tables = list(pk.TEXT.values()) + list(pk.ERRORS.values()) + list(pk.CHROME.values())
        for spec in pk.SECTIONS.values():
            tables += [spec["title"], spec["line"]] + spec["includes"]
        for table in tables:
            for lang in ("he", "en", "fr", "ar", "ru"):
                self.assertTrue(table[lang].strip())
        self.assertEqual(set(pk.SECTIONS), set(pk.SECTION_IDS))
        self.assertEqual(set(pk.SAMPLE_CREDITS), set(pk.SECTION_IDS))
        for sid in pk.SECTION_IDS:
            self.assertIn("cap_" + sid, pk.TEXT)

    def test_an_older_sample_of_a_section_that_no_longer_exists_is_ignored(self):
        from core import brand_brief
        brand_brief.set_page_state(27, package={"templates": {"sample_at": "2026-11-27T10:00:00+00:00", "snapshot": {}}})
        self.assertEqual([s["sample"]["state"] for s in bp.view(27, "he")["package"]["sections"]], ["available"] * 3)
        with self.assertRaises(pk.PackageError) as ctx:
            pk.sample_png(27, "templates")
        self.assertEqual(ctx.exception.code, "bad_section")


class TheOneMockup(Base):
    def test_nothing_is_made_without_the_confirmation(self):
        with self.assertRaises(pk.PackageError) as ctx:
            pk.request_sample(27, "website", False)
        self.assertEqual(ctx.exception.code, "confirm_needed")
        self.assertEqual(pk.state(27), {})
        with self.assertRaises(pk.PackageError):
            pk.sample_png(27, "website")
        with self.assertRaises(pk.PackageError) as ctx:
            pk.request_sample(27, "a section we do not have", True)
        self.assertEqual(ctx.exception.code, "bad_section")

    def test_one_confirmed_mockup_then_the_section_locks_again(self):
        pk.request_sample(27, "website", True)
        mine = pk.state(27)["website"]
        self.assertEqual(mine["credits"], 0)
        self.assertEqual(set(mine["snapshot"]), {"lang", "name", "variants", "logos", "photos", "copy", "fonts"})   # frozen at the request
        self.assertTrue(mine["file_id"])                                  # the finished picture is kept in the client's folder
        kept = self.drive[mine["file_id"]]
        self.assertEqual(kept["appProperties"]["uallak_kind"], "package_mockup")
        self.assertEqual(bp.files(27), [])                                # and is NOT one of the client's brand files
        section = _section(27, "website")
        self.assertEqual(section["sample"]["state"], "made")
        self.assertIn("/api/client/brand-identity/package/website/sample", section["sample"]["url"])
        self.assertEqual(pk.sample_png(27, "website"), PNG)
        pk._PNG.clear()
        pk.render = lambda *a: self.fail("a kept mockup is never drawn again")
        self.assertEqual(pk.sample_png(27, "website"), PNG)               # served from the kept file after a restart
        with self.assertRaises(pk.PackageError) as ctx:
            pk.request_sample(27, "website", True)
        self.assertEqual(ctx.exception.code, "sample_used")
        self.assertEqual(pk.state(27)["website"]["sample_at"], mine["sample_at"])                  # nothing made again
        self.assertEqual(_section(27, "landing_pages")["sample"]["state"], "available")            # once PER section

    def test_every_request_is_logged_for_the_owner(self):
        pk.request_sample(27, "landing_pages", True)
        self.assertEqual(len(self.signals), 1)
        self.assertIn("client 27", self.signals[0])                       # the Control Room matches on it
        self.assertIn("buying signal", self.signals[0])
        self.assertIn("two landing pages", self.signals[0].lower())
        self.assertIn("3 of their photos", self.signals[0])
        with self.assertRaises(pk.PackageError):
            pk.request_sample(27, "landing_pages", True)
        self.assertEqual(len(self.signals), 2)                            # the refused second ask is a signal too

    def test_a_mockup_that_cannot_be_composed_records_nothing(self):
        def broken(client_id, section, snap):
            raise RuntimeError("renderer down")
        pk.render = broken
        with self.assertRaises(pk.PackageError) as ctx:
            pk.request_sample(27, "logo", True)
        self.assertEqual(ctx.exception.code, "sample_unavailable")
        self.assertEqual(pk.state(27), {})                                # not used up, not charged
        self.assertIn("could NOT be composed", self.signals[0])


class BuiltFromThisClientsMaterial(Base):
    def test_the_snapshot_is_the_clients_logo_palette_photos_and_copy(self):
        from core import social_queue
        saved = social_queue._load
        social_queue._load = lambda cid: [
            {"id": "d2", "created_at": "2026-11-26T10:00:00", "design": {"slides": [{"copy_headline": "הכותרת של הלקוח", "copy_body": "הגוף"}]}},
            {"id": "d1", "created_at": "2026-11-25T10:00:00", "design": {"slides": [{"copy_headline": "כותרת קודמת", "copy_body": ""}]}}]
        self.addCleanup(setattr, social_queue, "_load", saved)
        logo = bp.upload(27, "full", "logo.png", _picture((255, 255, 255, 255), (800, 300), "RGBA"))
        bp.upload(27, bp.ASSET, "deck.pdf", b"%PDF-1.4 a brand deck")
        snap = pk.snapshot(27, "landing_pages", "he")
        self.assertEqual(snap["copy"], [["הכותרת של הלקוח", "הגוף"], ["כותרת קודמת", ""]])       # their words, newest first
        self.assertEqual(snap["photos"], self.photos)
        self.assertEqual(snap["logos"], {"full": logo["id"]})                                     # their logo FILE (a PDF is not a logo)
        self.assertEqual(snap["name"], "Shin Sekai")
        self.assertEqual([v["label"] for v in snap["variants"]], ["black + red", "black + gold"])  # each variant, never flattened
        self.assertEqual([v["segment"] for v in snap["variants"]], ["b2c", "b2b"])
        for v in snap["variants"]:
            self.assertEqual(set(v["roles"]), {"bg", "fg", "accent", "muted"})
        self.assertNotEqual(snap["variants"][0]["roles"]["accent"], snap["variants"][1]["roles"]["accent"])

    def test_no_product_photo_means_no_website_or_landing_mockup(self):
        self.photos = []
        for section in ("website", "landing_pages"):
            with self.assertRaises(pk.PackageError) as ctx:
                pk.request_sample(27, section, True)
            self.assertEqual(ctx.exception.code, "needs_photos")          # never a generic template with their colours
        self.assertEqual(pk.state(27), {})                                # nothing recorded: they can ask again
        self.assertEqual(len(self.signals), 2)                            # and the owner still hears they asked
        self.assertIn("NOT made (needs_photos)", self.signals[0])
        self.assertIn("תמונה אחת של מוצר", pk.error_text("needs_photos", "he"))
        pk.request_sample(27, "logo", True)                               # a logo mockup needs their logo or name, not photos
        self.assertEqual(pk.state(27)["logo"]["snapshot"]["photos"], [])

    def test_without_a_logo_file_the_logo_mockup_says_it_is_a_sketch(self):
        pk.request_sample(27, "logo", True)
        self.assertEqual(_section(27, "logo")["sample"]["caption"], pk.TEXT["cap_logo_sketch"]["he"])
        self.assertIn("built from their business name (no logo file)", self.signals[0])
        pk._business_name = lambda client_id: ""
        from core import brand_brief
        brand_brief.set_page_state(27, package={})
        with self.assertRaises(pk.PackageError) as ctx:
            pk.request_sample(27, "logo", True)
        self.assertEqual(ctx.exception.code, "needs_logo")

    def test_only_the_clients_own_pictures_are_used(self):
        self.assertEqual(pk.PHOTO_ROLES[0], "product_catalog")
        for never in ("social_output", "archive", "brand_assets"):        # not our posts, not logo files
            self.assertNotIn(never, pk.PHOTO_ROLES)
        src = _src("core", "brand_package.py")
        self.assertIn("drive_assets.find_assets(client_id, roles=list(PHOTO_ROLES), media_type=\"image\"", src)
        for word in ("media_gen_service", "anthropic", "claude_json", "generate_image", "stock"):
            self.assertNotIn(word, src)                                   # composed in code: never a model, never stock

    @unittest.skipUnless(RAQM, "needs Pillow with raqm (the Hebrew renderer)")
    def test_the_real_mockups_carry_the_clients_photo_logo_and_both_palettes(self):
        logo_colour = (250, 10, 240)
        bp.upload(27, "full", "logo.png", _picture(logo_colour + (255,), (800, 300), "RGBA"))

        def colours(png, box=None):
            image = Image.open(io.BytesIO(png)).convert("RGB")
            image = image.crop(box) if box else image
            return image, {c for _n, c in image.getcolors(maxcolors=1 << 24)}

        def rgb(hex_colour):
            return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))

        snap = pk.snapshot(27, "website", "he")
        site, seen = colours(REAL_RENDER(27, "website", snap))
        self.assertEqual(site.width, pk.PAGE_W + 96)
        for own in (PHOTO_COLOURS[0], PHOTO_COLOURS[1], PHOTO_COLOURS[2], logo_colour, rgb(snap["variants"][0]["roles"]["accent"])):
            self.assertIn(own, seen)                                      # their hero photo, their other products, their logo, their colour

        snap = pk.snapshot(27, "landing_pages", "he")
        png = REAL_RENDER(27, "landing_pages", snap)
        both, _ = colours(png)
        _top, first = colours(png, (0, 0, both.width, both.height // 2))
        _bottom, second = colours(png, (0, both.height // 2, both.width, both.height))
        red, gold = (rgb(v["roles"]["accent"]) for v in snap["variants"])
        self.assertIn(red, first)                                         # the private-customer page on its palette
        self.assertIn(gold, second)                                       # the business page on its own
        self.assertNotIn(gold, first)
        self.assertNotIn(red, second)
        self.assertIn(PHOTO_COLOURS[0], first)                            # each page has its own product photo
        self.assertIn(PHOTO_COLOURS[1], second)
        self.assertIn(logo_colour, first)
        self.assertIn(logo_colour, second)

        _sheet, seen = colours(REAL_RENDER(27, "logo", pk.snapshot(27, "logo", "he")))
        self.assertIn(logo_colour, seen)
        self.assertIn(PHOTO_COLOURS[0], seen)                             # the mark on a product photo of theirs

        self.photos = ["photo-1"]                                         # one photo: a page, without the products row
        one = pk.snapshot(27, "website", "he")
        small, _ = colours(REAL_RENDER(27, "website", one))
        self.assertLess(small.height, site.height)
        from core import brand_brief
        bp.delete(27, bp.files(27)[0]["id"])                              # no logo file: their name stands for it
        nameless, seen = colours(REAL_RENDER(27, "logo", pk.snapshot(27, "logo", "en")))
        self.assertNotIn(logo_colour, seen)
        self.assertTrue(brand_brief.current(27)["text"])


class AskTheOwner(Base):
    def test_a_question_opens_a_real_team_request(self):
        calls = []
        fake = types.SimpleNamespace(open_team_request=lambda *a, **k: calls.append((a, k)) or {"status": "awaiting_staff", "already": False})
        with mock.patch.dict(sys.modules, {"core.agent_actions": fake}), mock.patch.object(core, "agent_actions", fake, create=True):
            got = pk.ask(27, "website", "he")
        self.assertIn("ג'וני", got["message"])
        (args, kwargs), = calls
        self.assertEqual((args[0], args[2], kwargs["origin"]), (27, "package_exception", "brand_page"))
        self.assertIn("branded website", args[3].lower())
        self.assertEqual(self.signals, [])                                # the request row carries it
        self.assertTrue(_section(27, "website")["asked"])

    def test_the_owner_still_hears_when_no_request_could_be_opened(self):
        fake = types.SimpleNamespace(open_team_request=lambda *a, **k: {"error": "storage_unavailable"})
        with mock.patch.dict(sys.modules, {"core.agent_actions": fake}), mock.patch.object(core, "agent_actions", fake, create=True):
            pk.ask(27, "logo", "he")
        self.assertIn("client 27", self.signals[0])
        self.assertIn("buying signal", self.signals[0])


class ThePage(unittest.TestCase):
    def test_a_mockup_is_made_only_by_the_confirm_click(self):
        html = _src("dashboard", "client", "index.html")
        self.assertEqual(html.count("/sample?confirm=1"), 1)              # one place: the confirm button's handler
        make = html[html.index("btn.hasAttribute('data-br-pkg-make')"):]
        self.assertIn("/sample?confirm=1", make[:400])
        first = html[html.index("btn.hasAttribute('data-br-pkg-sample')"):html.index("btn.hasAttribute('data-br-pkg-cancel')")]
        self.assertNotIn("brJson(", first)                                # the first click only shows the cost
        load = html[html.index("async function loadBrandPage()"):html.index("async function brSend(")]
        self.assertNotIn("/package/", load)                               # never on page load
        for use in ("brPackageHtml()", "brHeroHtml()", "data-br-pkg-ask", "smp.cost_text", "לבקש הדמיה מהחומרים שלי"):
            self.assertIn(use, html)

    def test_the_routes_exist_and_the_server_requires_the_confirmation(self):
        api = _src("core", "api_server.py")
        for route in ('@app.post("/api/client/brand-identity/package/{section}/sample")',
                      '@app.get("/api/client/brand-identity/package/{section}/sample")',
                      '@app.post("/api/client/brand-identity/package/{section}/ask")'):
            self.assertIn(route, api)
        self.assertIn("brand_package.request_sample(client_id, section[:40], bool(confirm), lang)", api)
        self.assertIn("confirm: int = 0", api)


if __name__ == "__main__":
    unittest.main()
