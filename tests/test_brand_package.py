"""What a branding package would add, on the client's branding page
(core/brand_package, 2026-11-27): sections that read as parts of the brand,
ONE confirmed sample per section built from the client's own brand, a real
way to ask the owner, and every request logged as a buying signal.

Run: python tests/test_brand_package.py
"""
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
try:
    from PIL import features
    RAQM = features.check("raqm")
except Exception:
    RAQM = False


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(tbp.Base):
    def setUp(self):
        super().setUp()
        self.signals = []
        saved = (pk._alert, pk.render)
        pk._alert = lambda lines: self.signals.extend(lines)
        pk.render = lambda client_id, section, snap: PNG
        pk._PNG.clear()
        self.addCleanup(lambda: [setattr(pk, n, v) for n, v in zip(("_alert", "render"), saved)])
        bp.view(27, "he")                         # the brief of client 27 exists on its row


class TheSections(Base):
    def test_the_four_parts_of_a_brand_each_with_one_line(self):
        package = bp.view(27, "he")["package"]
        self.assertEqual([s["id"] for s in package["sections"]], ["typography", "visual_language", "templates", "brand_book"])
        for s in package["sections"]:
            self.assertTrue(s["title"] and s["line"])
            self.assertEqual(len(s["includes"]), 3)
            self.assertEqual(s["sample"]["state"], "available")
            self.assertIn("קרדיטים", s["sample"]["cost_text"])            # the cost is on the page BEFORE anything is made
        self.assertIn("עדיין אין למותג שלכם", package["sub"])             # read as what the brand lacks, not a blocked feature

    def test_every_text_exists_in_five_languages(self):
        tables = list(pk.TEXT.values()) + list(pk.ERRORS.values())
        for spec in pk.SECTIONS.values():
            tables += [spec["title"], spec["line"]] + spec["includes"]
        for table in tables:
            for lang in ("he", "en", "fr", "ar", "ru"):
                self.assertTrue(table[lang].strip())
        self.assertEqual(set(pk.SECTIONS), set(pk.SECTION_IDS))
        self.assertEqual(set(pk.SAMPLE_CREDITS), set(pk.SECTION_IDS))


class TheOneSample(Base):
    def test_nothing_is_made_without_the_confirmation(self):
        with self.assertRaises(pk.PackageError) as ctx:
            pk.request_sample(27, "templates", False)
        self.assertEqual(ctx.exception.code, "confirm_needed")
        self.assertEqual(pk.state(27), {})
        with self.assertRaises(pk.PackageError):
            pk.sample_png(27, "templates")
        with self.assertRaises(pk.PackageError) as ctx:
            pk.request_sample(27, "a section we do not have", True)
        self.assertEqual(ctx.exception.code, "bad_section")

    def test_one_confirmed_sample_then_the_section_locks_again(self):
        pk.request_sample(27, "templates", True)
        mine = pk.state(27)["templates"]
        self.assertEqual(mine["credits"], 0)
        self.assertEqual(set(mine["snapshot"]), {"variant", "segment", "roles", "fonts", "copy"})   # frozen at the request
        section = next(s for s in bp.view(27, "he")["package"]["sections"] if s["id"] == "templates")
        self.assertEqual(section["sample"]["state"], "made")
        self.assertIn("/api/client/brand-identity/package/templates/sample", section["sample"]["url"])
        self.assertEqual(pk.sample_png(27, "templates"), PNG)
        with self.assertRaises(pk.PackageError) as ctx:
            pk.request_sample(27, "templates", True)
        self.assertEqual(ctx.exception.code, "sample_used")
        self.assertEqual(pk.state(27)["templates"]["sample_at"], mine["sample_at"])               # nothing made again
        other = next(s for s in bp.view(27, "he")["package"]["sections"] if s["id"] == "brand_book")
        self.assertEqual(other["sample"]["state"], "available")                                   # once PER section

    def test_every_request_is_logged_for_the_owner(self):
        pk.request_sample(27, "visual_language", True)
        self.assertEqual(len(self.signals), 1)
        self.assertIn("client 27", self.signals[0])                       # the Control Room matches on it
        self.assertIn("buying signal", self.signals[0])
        self.assertIn("visual language", self.signals[0].lower())
        with self.assertRaises(pk.PackageError):
            pk.request_sample(27, "visual_language", True)
        self.assertEqual(len(self.signals), 2)                            # the refused second ask is a signal too

    def test_a_sample_that_cannot_be_drawn_records_nothing(self):
        def broken(client_id, section, snap):
            raise RuntimeError("renderer down")
        pk.render = broken
        with self.assertRaises(pk.PackageError) as ctx:
            pk.request_sample(27, "brand_book", True)
        self.assertEqual(ctx.exception.code, "sample_unavailable")
        self.assertEqual(pk.state(27), {})                                # not used up, not charged
        self.assertIn("could NOT be drawn", self.signals[0])

    def test_the_sample_carries_the_clients_own_copy(self):
        from core import social_queue
        saved = social_queue._load
        social_queue._load = lambda cid: [{"id": "d1", "kind": "design", "created_at": "2026-11-26T10:00:00",
                                           "design": {"slides": [{"copy_headline": "הכותרת של הלקוח", "copy_body": "הגוף"}]}}]
        self.addCleanup(setattr, social_queue, "_load", saved)
        self.assertEqual(pk._real_copy(27), ("הכותרת של הלקוח", "הגוף"))
        self.assertEqual(pk.snapshot(27, "templates")["copy"], ["הכותרת של הלקוח", "הגוף"])
        src = _src("core", "brand_package.py")
        for word in ("media_gen_service", "anthropic", "claude_json", "generate_image"):
            self.assertNotIn(word, src)                                   # drawn in code: never a model

    @unittest.skipUnless(RAQM, "needs Pillow with raqm (the Hebrew renderer)")
    def test_the_real_samples_are_pictures(self):
        for section in ("templates", "visual_language", "brand_book"):
            png = REAL_RENDER(27, section, pk.snapshot(27, section))
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        try:
            snap = pk.snapshot(27, "typography")                          # needs library families installed
        except pk.PackageError:
            return
        self.assertNotEqual(snap["fonts"]["headline"], snap["fonts"]["body"])
        self.assertEqual(REAL_RENDER(27, "typography", snap)[:8], b"\x89PNG\r\n\x1a\n")


class AskTheOwner(Base):
    def test_a_question_opens_a_real_team_request(self):
        calls = []
        fake = types.SimpleNamespace(open_team_request=lambda *a, **k: calls.append((a, k)) or {"status": "awaiting_staff", "already": False})
        with mock.patch.dict(sys.modules, {"core.agent_actions": fake}), mock.patch.object(core, "agent_actions", fake, create=True):
            got = pk.ask(27, "brand_book", "he")
        self.assertIn("ג'וני", got["message"])
        (args, kwargs), = calls
        self.assertEqual((args[0], args[2], kwargs["origin"]), (27, "package_exception", "brand_page"))
        self.assertIn("brand document", args[3].lower())
        self.assertEqual(self.signals, [])                                # the request row carries it
        section = next(s for s in bp.view(27, "he")["package"]["sections"] if s["id"] == "brand_book")
        self.assertTrue(section["asked"])

    def test_the_owner_still_hears_when_no_request_could_be_opened(self):
        fake = types.SimpleNamespace(open_team_request=lambda *a, **k: {"error": "storage_unavailable"})
        with mock.patch.dict(sys.modules, {"core.agent_actions": fake}), mock.patch.object(core, "agent_actions", fake, create=True):
            pk.ask(27, "typography", "he")
        self.assertIn("client 27", self.signals[0])
        self.assertIn("buying signal", self.signals[0])


class ThePage(unittest.TestCase):
    def test_a_sample_is_made_only_by_the_confirm_click(self):
        html = _src("dashboard", "client", "index.html")
        self.assertEqual(html.count("/sample?confirm=1"), 1)              # one place: the confirm button's handler
        make = html[html.index("btn.hasAttribute('data-br-pkg-make')"):]
        self.assertIn("/sample?confirm=1", make[:400])
        first = html[html.index("btn.hasAttribute('data-br-pkg-sample')"):html.index("btn.hasAttribute('data-br-pkg-cancel')")]
        self.assertNotIn("brJson(", first)                                # the first click only shows the cost
        load = html[html.index("async function loadBrandPage()"):html.index("async function brSend(")]
        self.assertNotIn("/package/", load)                               # never on page load
        for use in ("brPackageHtml()", "brHeroHtml()", "data-br-pkg-ask", "smp.cost_text"):
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
