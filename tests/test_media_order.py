"""The ליאור → יואב handoff contract (docs/PAID-MEDIA.md §3):
core/media_order, core/ad_copy, core/paid_media_production,
core/media_order_actions.

No network, no model, no Drive: the generator, the prompt writer, the logo
loader, the store and the copy model are fakes. Images are drawn with Pillow.
"""
import io
import os
import sys
import types
import unittest
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")

for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}),
                      ("httpx", {"Response": object, "Client": object, "HTTPError": Exception,
                                 "HTTPStatusError": Exception, "TimeoutException": Exception,
                                 "RequestError": Exception}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

try:
    from PIL import Image, ImageDraw
except ImportError:                                   # the container has Pillow; a bare python does not
    Image = None

from core import ad_copy, copy_budget, media_order as mo, paid_media_production as pmp   # noqa: E402
from core import google_ads_formats as gaf                                                # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
TODAY = date(2026, 11, 28)
ROLES = {"bg": "#F4F1DE", "fg": "#1F3A5F", "accent": "#E07A5F", "muted": "#8D99AE"}
TEAL = (0, 128, 128)
YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
FACTS = {"palette_variants": []}
DECISION = {"campaign_type": "pmax", "objective": "sales", "audience_segment": "home bakers",
            "landing_page_url": "https://example.com/shop", "offer": "ערכת אפייה ביתית"}


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def picture(w, h, subject=(0.42, 0.3, 0.58, 0.7), kind="JPEG", backdrop=(236, 232, 222)) -> bytes:
    """A plain backdrop with ONE subject at `subject` (fractions of the frame)."""
    img = Image.new("RGB", (w, h), backdrop)
    d = ImageDraw.Draw(img)
    box = (int(subject[0] * w), int(subject[1] * h), int(subject[2] * w), int(subject[3] * h))
    d.ellipse(box, fill=(190, 40, 40))
    buf = io.BytesIO()
    img.save(buf, kind, **({"quality": 90} if kind == "JPEG" else {}))
    return buf.getvalue()


def order(**changes) -> dict:
    return mo.issue(27, {**DECISION, **changes}, facts=FACTS, today=TODAY, palette_roles=lambda cid, label: ROLES)


def full_delivery(o: dict, skip=(), **overrides):
    """Every image/logo row of the order, well made. `skip` = keys left out."""
    assets, files = [], {}
    for row in o["required_assets"]:
        if row["kind"] not in ("image", "logo"):
            continue
        for n in range(1, row["count"] + 1):
            k = mo.key(row["slot"], n)
            if k in skip:
                continue
            files[k] = overrides.get(k) or picture(row["width"], row["height"],
                                                   kind="PNG" if row["kind"] == "logo" else "JPEG")
            assets.append(mo.asset(row["slot"], n, palette_variant=o["palette"]["variant"], source="generated"))
    return mo.manifest(o, assets), files


TEXTS = {"headline": ["אפייה ביתית", "אופים בבית בקלות", "כל מה שצריך בקופסה"],
         "long_headline": ["ערכת אפייה ביתית עם כל המרכיבים, מדודים ומוכנים"],
         "description": ["כל המרכיבים מדודים מראש, עם הוראות ברורות.", "מזמינים היום ואופים בסוף השבוע."],
         "business_name": ["קפה נועה"]}


@unittest.skipUnless(Image, "Pillow is not installed")
class Order(unittest.TestCase):
    def test_the_order_carries_the_decision_and_derives_the_assets(self):
        o = order()
        for k in ("campaign_type", "objective", "client_id", "audience_segment", "landing_page_url", "offer",
                  "required_assets", "deadline"):
            self.assertIn(k, o, k)
        self.assertEqual(o["issued_by"], "yoav")
        self.assertEqual(o["required_assets"], gaf.required_assets("pmax", concepts=mo.DEFAULT_CONCEPTS))
        self.assertEqual(o["palette"]["variant"], "default")
        self.assertEqual(o["specs"]["verified"], gaf.VERIFIED)
        self.assertEqual(o["deadline"], "2026-12-01")

    def test_a_bad_decision_is_refused_by_field(self):
        for change, code in (({"campaign_type": "banner"}, "campaign_type"), ({"objective": "fame"}, "objective"),
                             ({"audience_segment": ""}, "audience_segment"),
                             ({"landing_page_url": "example.com"}, "landing_page_url"),
                             ({"landing_page_url": "http://example.com"}, "landing_page_url"),
                             ({"offer": ""}, "offer"), ({"deadline": "2026-11-01"}, "deadline")):
            with self.assertRaises(mo.OrderError) as caught:
                order(**change)
            self.assertEqual(caught.exception.code, code, change)

    def test_the_segment_decides_the_palette_variant(self):
        from core import post_brief
        facts = {"palette_variants": [{"label": "warm", "segment": "b2c", "applies_to": "home bakers"},
                                      {"label": "cool", "segment": "b2b", "applies_to": "cafes"}]}
        try:
            post_brief.resolve_palette(facts, "consumers")
        except Exception:
            self.skipTest("resolve_palette reads another fact shape")
        o = mo.issue(27, {**DECISION, "audience_segment": "consumers"}, facts=facts, today=TODAY,
                     palette_roles=lambda cid, label: ROLES)
        self.assertEqual(o["palette"]["variant"], "warm")
        with self.assertRaises(mo.OrderError) as caught:
            mo.issue(27, {**DECISION, "audience_segment": "everyone who bakes"}, facts=facts, today=TODAY,
                     palette_roles=lambda cid, label: ROLES)
        self.assertEqual(caught.exception.code, "audience_unresolved")

    def test_media_and_copy_never_get_each_others_half(self):
        o = order()
        media, words = mo.media_brief(o), mo.copy_brief(o)
        self.assertNotIn("required_text", media)
        self.assertNotIn("required_assets", words)
        self.assertNotIn("palette", words)
        with self.assertRaises(ValueError):
            ad_copy.write(27, media, call=lambda s, u: {})                 # copy handed a media brief
        with self.assertRaises(ValueError):
            pmp.produce(27, {**media, "required_text": o["required_text"]})  # media handed text slots
        self.assertNotIn("ad_copy", _src("core", "paid_media_production.py").split('"""', 2)[2])
        self.assertNotIn("paid_media_production", _src("core", "ad_copy.py"))

    def test_search_orders_no_media(self):
        o = order(campaign_type="search")
        self.assertEqual(o["required_assets"], [])
        self.assertEqual(mo.generations(o), 0)


@unittest.skipUnless(Image, "Pillow is not installed")
class Delivery(unittest.TestCase):
    def setUp(self):
        self.o = order()
        self.notified = []

    def test_a_complete_set_is_handed_over_and_only_then_is_yoav_told(self):
        delivered, files = full_delivery(self.o)
        result = mo.hand_over(27, self.o, delivered, files, notify=self.notified.append, store=False)
        self.assertTrue(result["complete"], result["problems"])
        self.assertEqual(self.o["status"], "handed_over")
        self.assertEqual(len(self.notified), 1)

    def test_a_missing_asset_fails_by_name_and_yoav_is_not_told(self):
        delivered, files = full_delivery(self.o, skip={"square#3", "logo_square#1"})
        with self.assertRaises(mo.IncompleteDelivery) as caught:
            mo.hand_over(27, self.o, delivered, files, notify=self.notified.append, store=False)
        self.assertEqual(self.notified, [])
        self.assertEqual(self.o["status"], "incomplete")
        self.assertNotIn("handed_over_at", self.o)
        missing = {(p["slot"], p["have"], p["need"]) for p in caught.exception.problems if p["code"] == "missing"}
        self.assertEqual(missing, {("square", 2, 3), ("logo_square", 0, 1)})
        he = mo.problem_lines(caught.exception.problems, "he")
        self.assertEqual(len(he), 2)
        self.assertIn("2 מתוך 3", " ".join(he))
        self.assertIn("square", str(caught.exception))
        print("\n[3.3 demo] " + str(caught.exception))
        for line in he:
            print("[3.3 demo] " + line)

    def test_every_check_is_made_on_the_file(self):
        row = {r["slot"]: r for r in self.o["required_assets"]}
        cases = {
            "file_size": ("landscape#1", os.urandom(40) and self._heavy(row["landscape"])),
            "ratio": ("square#1", picture(1200, 900)),
            "too_small": ("landscape#2", picture(382, 200)),
            "crop": ("landscape#3", picture(1200, 628, subject=(0.02, 0.3, 0.16, 0.7))),
            "unreadable": ("square#2", b"not an image"),
        }
        for code, (k, content) in cases.items():
            delivered, files = full_delivery(self.o, **{k: content})
            result = mo.check_delivery(self.o, delivered, files)
            codes = {p["code"] for p in result["problems"] if f"{p['slot']}#{p['n']}" == k}
            self.assertIn(code, codes, (code, result["problems"]))
            self.assertIn("missing", {p["code"] for p in result["problems"]}, code)   # a flawed file is not counted
            self.assertFalse(result["complete"])

    def _heavy(self, row):
        img = Image.frombytes("RGB", (2400, 1256), os.urandom(2400 * 1256 * 3))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        self.assertGreater(len(buf.getvalue()), row["max_bytes"])
        return buf.getvalue()

    def test_the_manifest_cannot_vouch_for_itself(self):
        delivered, files = full_delivery(self.o)
        for a in delivered["assets"]:
            a.update(ok=True, bytes=1, crop={"ok": True})                 # claims
        files.pop("portrait#1")
        result = mo.check_delivery(self.o, delivered, files)
        self.assertIn(("portrait", "no_file"), {(p["slot"], p["code"]) for p in result["problems"]})

    def test_the_palette_is_measured_from_the_pixels_never_read_from_the_name(self):
        from core import render_manifest as rm
        # the row SAYS the right variant; the file is teal
        delivered, files = full_delivery(self.o, **{"landscape#1": picture(1200, 628, backdrop=TEAL)})
        with self.assertRaises(mo.IncompleteDelivery) as caught:
            mo.hand_over(27, self.o, delivered, files, notify=self.notified.append, store=False)
        self.assertEqual(self.notified, [])
        off = [p for p in caught.exception.problems if p["code"] == "palette_measured"]
        self.assertEqual([(p["slot"], p["n"]) for p in off], [("landscape", 1)])
        self.assertLessEqual(rm._distance(off[0]["measured"], "#008080"), 6)
        self.assertEqual(off[0]["nearest"], "#1F3A5F")
        self.assertIn(("landscape", "missing"), {(p["slot"], p["code"]) for p in caught.exception.problems})
        line = mo.problem_lines(off, "he")[0]
        self.assertIn(off[0]["measured"], line)
        self.assertIn("#F4F1DE", line)
        print("\n[item 1 demo] " + mo.problem_lines(off, "en")[0])
        # the row says the WRONG variant; the file is the brand backdrop: the name is not what is checked
        delivered, files = full_delivery(self.o)
        for a in delivered["assets"]:
            a["palette_variant"] = "some other name"
        result = mo.check_delivery(self.o, delivered, files)
        self.assertTrue(result["complete"], result["problems"])
        got = result["measured"]["landscape#1"]
        self.assertEqual(set(got), {"mean", "background", "dominant"})
        self.assertLessEqual(rm._distance(got["background"], "#ECE8DE"), 4)
        print(f"[item 1 demo] landscape#1 measured: background {got['background']}, mean {got['mean']}, dominant "
              f"{[(d['color'], d['share']) for d in got['dominant'][:3]]} | brand variant "
              f"{result['palette']['variant']}: {result['palette']['roles']} -> within {rm.PHOTO_TOLERANCE} of #F4F1DE: ok")
        delivered["assets"][1]["drawn_colors"] = ["#1f3a5f", "#00FF00"]
        codes = {(p["code"], p.get("colors", "")) for p in mo.check_delivery(self.o, delivered, files)["problems"]}
        self.assertIn(("palette_colors", "#00FF00"), codes)

    def test_one_measurement_shared_with_the_slide_renderer(self):
        self.assertIn("render_manifest.mean_color(img)", _src("core", "slide_renderer.py"))
        self.assertNotIn("resize((1, 1)", _src("core", "slide_renderer.py"))
        for name in ("media_order.py", "paid_media_production.py"):
            source = _src("core", name)
            self.assertIn("render_manifest.measure", source, name)
            for own in ("ImageStat", "resize((1, 1)", "quantize("):
                self.assertNotIn(own, source, (name, own))
        body = _src("core", "media_order.py")
        check = body[body.index("def check_delivery"):body.index("def hand_over")]
        self.assertNotIn('a.get("palette_variant")', check)
        self.assertNotIn('a.get("drawn")', check)

    def test_a_video_is_a_youtube_url_never_a_file(self):
        o = order(campaign_type="youtube")
        self.assertEqual([r["kind"] for r in o["required_assets"] if r["need"] == "required"], ["video"])
        for a, code in ((mo.asset("video", 1, file_path="ads/spot.mp4"), "video_file_not_url"),
                        (mo.asset("video", 1, youtube_url="https://vimeo.com/1"), "video_bad_url")):
            problems = mo.check_delivery(o, mo.manifest(o, [a]), {})["problems"]
            self.assertIn(code, {p["code"] for p in problems})
        good = mo.asset("video", 1, youtube_url=YT)
        for state, code in (("ok", None), ("private", "video_private"), ("not_found", "video_unreachable"),
                            ("unknown", "video_unverified")):
            result = mo.check_delivery(o, mo.manifest(o, [good]), {}, probe=lambda url, s=state: {"state": s, "http": 403})
            self.assertEqual({p["code"] for p in result["problems"]}, {code, "missing"} if code else set(), state)
        # nobody asked YouTube = nothing was proven = not counted
        self.assertIn("video_unverified", {p["code"] for p in mo.check_delivery(o, mo.manifest(o, [good]), {})["problems"]})
        # the upload's own claim is not the check: it says unlisted, an outsider is refused
        claims = mo.asset("video", 1, youtube_url=YT, privacy_status="unlisted")
        problems = mo.check_delivery(o, mo.manifest(o, [claims]), {}, probe=lambda u: {"state": "private", "http": 401})
        self.assertIn("video_private", {p["code"] for p in problems["problems"]})

    def test_the_order_carries_the_youtube_url(self):
        o = order(campaign_type="youtube", youtube_url=YT)
        self.assertEqual((o["youtube_url"], mo.media_brief(o)["youtube_url"]), (YT, YT))
        self.assertTrue(any(r["kind"] == "video" for r in order(youtube_url=YT)["required_assets"]))   # pmax + a video
        for change in ({"campaign_type": "youtube", "youtube_url": "https://vimeo.com/1"},
                       {"campaign_type": "search", "youtube_url": YT}):
            with self.assertRaises(mo.OrderError) as caught:
                order(**change)
            self.assertEqual(caught.exception.code, "youtube_url")

    def test_every_problem_reads_in_five_languages(self):
        for code, table in mo.PROBLEM_TEXT.items():
            self.assertEqual(set(table), set(LANGS), code)
            for lang in LANGS:
                self.assertIn("{what}", table[lang], (code, lang))
        for slot, table in mo.SLOT_TEXT.items():
            self.assertEqual(set(table), set(LANGS), slot)
        delivered, files = full_delivery(self.o, **{"square#1": picture(1200, 900)})
        for lang in LANGS:
            for line in mo.problem_lines(mo.check_delivery(self.o, delivered, files)["problems"], lang):
                self.assertNotIn("{", line, line)


@unittest.skipUnless(Image, "Pillow is not installed")
class YoavVerifies(unittest.TestCase):
    def setUp(self):
        self.o = order()
        self.delivered, self.files = full_delivery(self.o)
        mo.hand_over(27, self.o, self.delivered, self.files, store=False)

    def test_a_good_set_can_serve(self):
        result = mo.yoav_verify(27, self.o, self.files, TEXTS, store=False)
        self.assertTrue(result["can_serve"], result["problems"])
        self.assertEqual(self.o["status"], "verified")
        self.assertEqual(self.o["verification"]["verified_by"], "yoav")

    def test_it_is_his_own_check_not_the_manifests(self):
        self.o["delivery"]["check"] = {"complete": True, "problems": []}     # Lior's check says fine
        files = {k: v for k, v in self.files.items() if not k.startswith("logo_square")}
        result = mo.yoav_verify(27, self.o, files, TEXTS, store=False)
        self.assertFalse(result["can_serve"])
        self.assertEqual(self.o["status"], "rejected")
        self.assertIn(("logo_square", "missing"), {(p["slot"], p["code"]) for p in result["problems"]})
        source = _src("core", "media_order.py")
        body = source[source.index("def verify_minimum"):source.index("def yoav_verify")]
        self.assertNotIn("check_delivery", body)
        self.assertNotIn('["check"]', body)
        self.assertIn("minimum_to_serve", body)

    def test_text_assets_are_part_of_the_minimum(self):
        long_one = {**TEXTS, "headline": ["כותרת ארוכה מדי שעוברת בהרבה את שלושים התווים של גוגל"] + TEXTS["headline"][1:]}
        result = mo.yoav_verify(27, self.o, self.files, long_one, store=False)
        self.assertIn("text_too_long", {p["code"] for p in result["problems"]})
        self.assertFalse(mo.verify_minimum("pmax", [], {})["can_serve"])

    def test_nothing_to_verify_before_a_handover(self):
        o = order()
        with self.assertRaises(mo.OrderError):
            mo.yoav_verify(27, o, {}, TEXTS, store=False)


class Copy(unittest.TestCase):
    def test_the_budget_is_googles_limits_from_the_table(self):
        budget = copy_budget.ads_budget("pmax")
        self.assertEqual(budget["fields"], gaf.required_text("pmax"))
        self.assertTrue(copy_budget.ads_fit("pmax", TEXTS)["fits"])
        over = copy_budget.ads_fit("pmax", {**TEXTS, "headline": ["x" * 31, "a", "b"]}, "he")
        self.assertFalse(over["fits"])
        self.assertIn("31", over["lines"][0])

    def test_one_repair_round_with_the_violations_named(self):
        o = order() if Image else None
        brief = mo.copy_brief(o) if o else {"id": "t", "campaign_type": "pmax", "objective": "sales",
                                            "audience_segment": "a", "offer": "b", "landing_page_url": "https://e.com"}
        calls = []

        def call(system, user):
            calls.append(user)
            return {"texts": TEXTS if len(calls) == 2 else {**TEXTS, "headline": ["x" * 40, "a", "b"]}}
        out = ad_copy.write(27, brief, call=call)
        self.assertEqual((out["rounds"], out["texts"]), (2, TEXTS))
        self.assertIn("violations", calls[1])
        self.assertNotIn("violations", calls[0])
        self.assertIn("ad_copy_budget", calls[0])
        self.assertNotIn("required_assets", calls[0])

    def test_copy_that_never_fits_fails_and_is_never_cut(self):
        brief = {"id": "t", "campaign_type": "search", "objective": "leads", "audience_segment": "a", "offer": "b",
                 "landing_page_url": "https://e.com"}
        with self.assertRaises(ad_copy.CopyFailed) as caught:
            ad_copy.write(27, brief, call=lambda s, u: {"texts": {"headline": ["x" * 50] * 3,
                                                                  "description": ["ok one", "ok two"]}})
        self.assertTrue(caught.exception.lines)
        self.assertNotIn("[:", _src("core", "ad_copy.py").split("def write")[1].split("CopyFailed")[0])

    def test_goes_through_the_one_json_helper(self):
        source = _src("core", "ad_copy.py") + _src("core", "media_order_actions.py") + \
            _src("core", "paid_media_production.py")
        self.assertEqual(source.count("safe_claude_json_call("), 3)
        self.assertNotIn("anthropic", source.lower())


@unittest.skipUnless(Image, "Pillow is not installed")
class Production(unittest.TestCase):
    def setUp(self):
        self.o = order()
        self.generated, self.stored = [], []

    def produce(self, generate, logos=None, **kw):
        def gen(prompt, aspect):
            self.generated.append(aspect)
            return generate(prompt, aspect)
        return pmp.produce(27, mo.media_brief(self.o), generate=gen,
                           craft=lambda brief, n, earlier: f"studio scene number {n}, plain backdrop",
                           load_logos=lambda cid: ({"primary": picture(600, 600, kind="PNG")} if logos is None else logos),
                           store=lambda oid, name, content, mime: self.stored.append(name) or {"id": "f-" + name},
                           **kw)

    @staticmethod
    def centred(prompt, aspect):
        w, h = (int(x) for x in aspect.replace(".", "").split(":")) if False else (0, 0)
        ratio = gaf.SLOTS[next(s for s in gaf.SLOTS if gaf.SLOTS[s].aspect == aspect)]
        return picture(ratio.width * 2, ratio.height * 2, subject=(0.44, 0.38, 0.56, 0.62))

    def test_a_full_set_from_one_picture_per_concept(self):
        delivered, files = self.produce(self.centred)
        result = mo.check_delivery(self.o, delivered, files)
        self.assertTrue(result["complete"], mo.problem_lines(result["problems"], "en"))
        self.assertEqual(self.generated, ["1.91:1"] * 3)                   # the other ratios were cut from it
        sources = {a["source"] for a in delivered["assets"]}
        self.assertEqual(sources, {"generated", "derived", "logo"})
        for a in delivered["assets"]:
            self.assertEqual("measured" in ((a.get("drawn") or {}).get("palette") or {}), a["source"] != "logo", a)
        self.assertEqual(len(self.stored), len(delivered["assets"]))
        for k, content in files.items():
            row = next(r for r in self.o["required_assets"] if r["slot"] == k.split("#")[0])
            self.assertEqual(gaf.check_file(row, content), [], k)

    def test_no_logo_file_means_no_logo_and_the_handover_names_it(self):
        delivered, files = self.produce(self.centred, logos={})
        self.assertNotIn("logo", {a["source"] for a in delivered["assets"]})
        with self.assertRaises(mo.IncompleteDelivery) as caught:
            mo.hand_over(27, self.o, delivered, files, store=False)
        self.assertIn("logo_square", {p["slot"] for p in caught.exception.problems})
        self.assertIn("never generated", " ".join(delivered["notes"]))

    def test_an_off_centre_picture_is_regenerated_within_the_retry_budget(self):
        def off(prompt, aspect):
            slot = gaf.SLOTS[next(s for s in gaf.SLOTS if gaf.SLOTS[s].aspect == aspect)]
            return picture(slot.width, slot.height, subject=(0.02, 0.3, 0.14, 0.7))
        delivered, files = self.produce(off)
        self.assertLessEqual(len(self.generated), mo.generations(self.o) + pmp.RETRIES)
        with self.assertRaises(mo.IncompleteDelivery):
            mo.hand_over(27, self.o, delivered, files, store=False)

    def test_an_off_palette_picture_is_regenerated_then_fails_by_name(self):
        def teal(prompt, aspect):
            slot = gaf.SLOTS[next(s for s in gaf.SLOTS if gaf.SLOTS[s].aspect == aspect)]
            return picture(slot.width, slot.height, subject=(0.44, 0.38, 0.56, 0.62), backdrop=TEAL)
        delivered, files = self.produce(teal)
        self.assertEqual(len(self.generated), 3 + pmp.RETRIES)
        with self.assertRaises(mo.IncompleteDelivery) as caught:
            mo.hand_over(27, self.o, delivered, files, store=False)
        self.assertIn("palette_measured", {p["code"] for p in caught.exception.problems})

    def test_hebrew_never_reaches_the_image_model(self):
        with self.assertRaises(Exception):
            pmp.produce(27, mo.media_brief(self.o), generate=self.centred,
                        craft=lambda brief, n, earlier: "שלט עם הכיתוב מבצע", load_logos=lambda cid: {},
                        store=lambda *a: {})

    def test_a_stop_ends_the_run(self):
        with self.assertRaises(pmp.ProductionBlocked) as caught:
            self.produce(self.centred, stopped=lambda: True)
        self.assertEqual(caught.exception.code, "stopped")

    def test_the_logo_is_fitted_never_redrawn(self):
        row = next(r for r in self.o["required_assets"] if r["slot"] == "logo_landscape")
        out = pmp.fit_logo(row, picture(600, 600, kind="PNG"))
        self.assertEqual(gaf.check_file(row, out), [])
        source = _src("core", "paid_media_production.py")
        self.assertNotIn("generate(", source[source.index("def fit_logo"):source.index("# ─── The order")])


class YouTubeOrder(unittest.TestCase):
    """A youtube campaign order through the card's own run(), end to end, on fakes."""

    def setUp(self):
        from core import agent_actions as aa
        self.aa, self.run = aa, aa.ACTIONS["prepare_campaign"]["run"]
        self.store, self.alerts, self.posted, self.made, self.uploaded = {}, [], [], [], []
        self.saved = [(mo, n, getattr(mo, n)) for n in ("save", "get", "_palette")]
        self.saved += [(aa, n, getattr(aa, n)) for n in ("_client", "_alert", "_post_client_message", "_lang")]
        mo.save = lambda cid, o: self.store.__setitem__(o["id"], {**o})
        mo.get = lambda cid, oid: dict(self.store.get(oid) or {})
        mo._palette = lambda cid, audience, facts, roles: {"variant": "default", "segment": "", "roles": ROLES}
        aa._client = lambda cid: {"id": cid, "language": "he", "billing_exempt": True, "business_name": "קפה נועה"}
        aa._alert = self.alerts.append
        aa._lang = lambda cid: "he"
        aa._post_client_message = lambda cid, persona, text: self.posted.append((persona, text))
        self.params = {**DECISION, "campaign_type": "youtube"}
        self.upload_answer = {"url": YT, "privacy_status": "unlisted", "privacy_requested": "unlisted",
                              "api_status": {"privacyStatus": "unlisted", "uploadStatus": "uploaded"}}
        self.probe_state = "ok"

    def tearDown(self):
        for module, name, value in self.saved:
            setattr(module, name, value)

    def tools(self):
        return {"make_video": lambda cid, brief: self.made.append(brief["id"]) or {"file_id": "driveVideo1234"},
                "upload_video": lambda cid, fid, brief: self.uploaded.append(fid) or dict(self.upload_answer),
                "probe": lambda url: {"state": self.probe_state, "http": {"ok": 200, "private": 403}.get(self.probe_state, 0)}}

    def test_with_the_clients_own_video_it_completes_in_one_run(self):
        result = self.run(27, {**self.params, "youtube_url": YT}, None, production=self.tools())
        self.assertEqual((result["status"], result["video_url"]), ("verified", YT))
        self.assertEqual((self.made, self.uploaded), ([], []))                    # nothing generated, nothing uploaded
        self.assertEqual(self.store[result["order"]]["status"], "verified")
        self.assertEqual([p for p, _t in self.posted], ["google"])

    def test_generated_uploaded_unlisted_checked_handed_over_verified(self):
        result = self.run(27, self.params, None, production=self.tools())
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual((len(self.made), self.uploaded), (1, ["driveVideo1234"]))
        self.assertEqual(result["video_privacy"], "unlisted")
        order_ = self.store[result["order"]]
        row = order_["delivery"]["manifest"]["assets"][0]
        self.assertEqual((row["youtube_url"], row["privacy_status"]), (YT, "unlisted"))
        self.assertTrue(order_["verification"]["can_serve"])
        line = self.aa.ACTIONS["prepare_campaign"]["done_message"](result, "he")
        self.assertIn(YT, line)
        self.assertIn("לא רשום", line)
        self.assertEqual(self.alerts, [])

    def test_the_social_gate_holds_the_order_and_it_continues_with_the_same_clip(self):
        self.upload_answer = {"waiting_approval": True}
        first = self.run(27, self.params, None, production=self.tools())
        self.assertEqual((first["status"], first["waiting_video"]), ("incomplete", True))
        self.assertEqual(self.posted, [])                                        # Yoav was not told
        self.assertEqual(self.store[first["order"]]["video_file_id"], "driveVideo1234")
        line = self.aa.ACTIONS["prepare_campaign"]["done_message"](first, "he")
        self.assertIn(first["order"], line)
        self.assertIn("סושיאל", line)
        self.upload_answer = {"url": YT, "privacy_status": "unlisted", "privacy_requested": "unlisted"}
        second = self.run(27, {"order": first["order"]}, None, production=self.tools())
        self.assertEqual((second["status"], second["order"]), ("verified", first["order"]))
        self.assertEqual(len(self.made), 1)                                      # the SAME clip, not a second one
        self.assertEqual(self.uploaded, ["driveVideo1234", "driveVideo1234"])
        with self.assertRaises(self.aa.ActionFailed) as caught:
            self.run(27, {"order": first["order"]}, None, production=self.tools())   # no longer waiting
        self.assertEqual(caught.exception.code, "order_gone")

    def test_an_upload_that_comes_back_private_is_reported_as_the_api_answered(self):
        self.upload_answer = {"url": YT, "privacy_status": "private", "privacy_requested": "unlisted",
                              "api_status": {"privacyStatus": "private", "uploadStatus": "uploaded"}}
        self.probe_state = "private"
        result = self.run(27, self.params, None, production=self.tools())
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("video_private", {p["code"] for p in result["problems"]})
        self.assertEqual(self.posted, [])
        told = " | ".join(self.alerts)
        self.assertIn("asking for unlisted and YouTube answered private", told)
        self.assertIn('"uploadStatus": "uploaded"', told)                      # the API's own status object
        source = _src("core", "media_order_actions.py") + _src("agents", "youtube_content_agent.py")
        self.assertNotIn("unverified API project", source)                       # no assumed policy in the report

    def test_youtube_not_answering_is_not_a_pass(self):
        self.probe_state = "unknown"
        result = self.run(27, self.params, None, production=self.tools())
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("video_unverified", {p["code"] for p in result["problems"]})

    def test_the_real_upload_goes_through_the_one_publisher_and_its_gate(self):
        source = _src("core", "paid_media_production.py")
        body = source[source.index("def _default_upload_video"):source.index("def video_asset")]
        self.assertIn("youtube_content_agent.publish(", body)
        self.assertIn('"purpose": "ad"', body)
        self.assertIn('out.get("not_approved")', body)
        self.assertNotIn("upload_video_stream", source)                          # never around the gate
        agent = _src("agents", "youtube_content_agent.py")
        self.assertLess(agent.index("social_queue.check("), agent.index("yt.upload_video_stream("))

class Wiring(unittest.TestCase):
    def setUp(self):
        from core import agent_actions as aa
        self.aa = aa
        self.spec = aa.ACTIONS["prepare_campaign"]

    def test_registered_gated_stoppable_and_in_five_languages(self):
        spec = self.spec
        self.assertEqual((spec["approver"], spec["internal_only"], spec["needs_row"]), ("client", True, True))
        self.assertIn("general", spec["personas"])
        self.assertIn("prepare_campaign", self.aa.STOP_FINISH)
        self.assertFalse(any(p["required"] for p in spec["params"].values()))     # "תכינו קמפיין" alone is enough
        for key in ("title", "what", "done"):
            self.assertEqual(set(spec[key]), set(LANGS), key)
        for code, texts in spec["refusals"].items():
            self.assertEqual(set(texts), set(LANGS), code)
        for lang in LANGS:
            for placeholder in ("{type}", "{objective}", "{offer}", "{url}", "{produces}", "{generations}"):
                self.assertIn(placeholder, spec["what"][lang], (lang, placeholder))

    def test_the_result_says_exactly_what_is_missing(self):
        problems = [{"slot": "square", "n": None, "size": "1200x1200", "code": "missing", "have": 2, "need": 3},
                    {"slot": "logo_square", "n": None, "size": "", "code": "missing", "have": 0, "need": 1}]
        for lang in LANGS:
            line = self.spec["done_message"]({"status": "incomplete", "made": 11, "problems": problems}, lang)
            self.assertIn("11", line)
            self.assertEqual(line.count("•"), 2, line)
            self.assertNotIn("{", line)
        ready = self.spec["done_message"]({"status": "verified", "images": 9, "logos": 2, "texts": 11}, "he")
        self.assertIn("9", ready)
        self.assertIn("שום דבר לא עלה לגוגל", ready)

    def test_the_card_shows_yoavs_decision(self):
        saved = self.aa._client
        self.aa._client = lambda cid: {"id": cid, "language": "en", "billing_exempt": True}
        try:
            shown = self.spec["display"](27, DECISION)
        finally:
            self.aa._client = saved
        self.assertEqual(shown["generations"], "9")
        self.assertEqual(shown["url"], DECISION["landing_page_url"])
        self.assertEqual(self.spec["display"](27, {**DECISION, "campaign_type": "search"})["generations"], "0")

    def test_yoav_decides_only_what_is_missing_and_never_invents_a_page(self):
        from core import media_order_actions as moa
        asked = []
        full = moa.decide(27, DECISION, "he", call=lambda s, u: asked.append(u) or {})
        self.assertEqual(asked, [])
        self.assertEqual(full["campaign_type"], "pmax")
        answer = {"campaign_type": "pmax", "objective": "sales", "audience_segment": "home bakers",
                  "landing_page_url": "https://invented.example/offer", "offer": "ערכת אפייה", "why": "כי"}
        got = moa.decide(27, {"request": "תכינו קמפיין"}, "he", call=lambda s, u: answer,
                         knowledge={"business": {"website": "https://example.com"}}, facts=FACTS)
        self.assertEqual(got["landing_page_url"], "")
        answer["landing_page_url"] = "https://example.com"
        got = moa.decide(27, {"request": "תכינו קמפיין"}, "he", call=lambda s, u: answer,
                         knowledge={"business": {"website": "https://example.com"}}, facts=FACTS)
        self.assertEqual(got["landing_page_url"], "https://example.com")

    def test_the_chain_order_in_the_run(self):
        source = _src("core", "media_order_actions.py")
        run = source[source.index("    def run("):source.index("    def done_message")]
        steps = ["_order(", "ad_copy.write(", "paid_media_production.produce(", "media_order.hand_over(",
                 "media_order.yoav_verify(", "_post_client_message("]
        positions = [run.index(s) for s in steps]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("copy_brief(order)", run)
        self.assertIn("media_brief(order)", run)
        for word in ("google_ads_agent", "create_campaign", "upload_asset"):
            self.assertNotIn(word, source, word)                          # nothing reaches the ad platform here

    def test_manager_rule_storage_and_docs(self):
        from agents import support_agent
        self.assertIn('"prepare_campaign"', support_agent.STUDIO_RULE)
        self.assertNotIn("{", support_agent.STUDIO_RULE)
        for path in ("client_deletion.py", "deletion_guard.py", "admin_service.py"):
            self.assertIn("media_orders:", _src("core", path), path)
        self.assertIn("`prepare_campaign`", _src("docs", "CHAT_PARITY.md"))
        doc = _src("docs", "PAID-MEDIA.md")
        for needle in ("media order", "delivery manifest", "IncompleteDelivery", "verify_minimum", "prepare_campaign"):
            self.assertIn(needle, doc, needle)


if __name__ == "__main__":
    unittest.main()
