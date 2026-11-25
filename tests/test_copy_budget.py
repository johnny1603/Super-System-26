"""The copy budget — the renderer's limits known where the copy is written
(2026-11-25, core/copy_budget, docs/SLIDE-RENDERER.md §Copy budget).

What must stay true:
- the budget per field (lines, ~characters, the headline's words) is derived
  from the ACTIVE template's geometry and type scale, measured with the
  bundled font — and it shrinks when the template's type grows;
- the budget reaches the content personas, and copy written within it fits;
- copy that doesn't fit gets ONE shortening round at submit, then reaches
  the approval card flagged per slide BEFORE approval (never a block);
  needs_room lets the agent say the idea needs more room;
- a failed render names the slide, the field and roughly how much, in plain
  Hebrew — on the design card and in the production line.

Run from the repo root:  python tests/test_copy_budget.py -v
"""
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_post_brief as tpb                  # noqa: E402  (stubs + the pipeline harness)
import test_social_queue as tsq                # noqa: E402  (FakeDB = in-memory app_settings)

from core import copy_budget as cb             # noqa: E402
from core import post_pipeline as pp           # noqa: E402
from core import slide_renderer as sr          # noqa: E402
from core import slide_spec                    # noqa: E402
from core import slide_templates               # noqa: E402
from core import social_queue as sq            # noqa: E402

RAQM = sr.raqm_status()["available"]
NEEDS_RAQM = unittest.skipUnless(RAQM or os.environ.get("CI"), "Pillow + raqm not installed here")
WORDS = ["מתנה", "שנשארת", "איתכם", "הרבה", "אחרי", "שהעטיפה", "נפתחה", "וכל", "זוג", "מעוצב", "פעם", "אחת"]


def hebrew(chars: int = None, words: int = None) -> str:
    """Ordinary Hebrew copy of about `chars` characters (or exactly `words` words)."""
    out, i = [], 0
    while True:
        w = WORDS[i % len(WORDS)]
        if words is not None and len(out) >= words:
            break
        if chars is not None and len(" ".join(out + [w])) > chars:
            break
        out.append(w)
        i += 1
    return " ".join(out)


def starter():
    return slide_spec.starter("b2c", "default")


class Templates:
    """slide_templates on an in-memory app_settings, for one test."""

    def __init__(self, test):
        self.db = tsq.FakeDB()
        saved = slide_templates._db
        slide_templates._db = lambda: self.db
        test.addCleanup(setattr, slide_templates, "_db", saved)

    def save(self, size_headline=None, size_body=None, name="carousel default"):
        spec = starter()
        if size_headline:
            spec["text_blocks"][0]["size"] = size_headline
        if size_body:
            spec["text_blocks"][1]["size"] = size_body
        return slide_templates.save(27, name, spec)


# ─── 1. Derived from the template, measured with the real font ───────────────

@NEEDS_RAQM
class Derived(unittest.TestCase):
    def test_lines_and_characters_come_from_the_geometry_and_the_font(self):
        spec = starter()
        got = cb.spec_budget(spec)
        head, body = got["copy_headline"], got["copy_body"]
        self.assertEqual((head["block"], body["block"]), ("headline", "body"))
        # lines: the block's max_height_pct / line height, measured the renderer's way
        block = spec["text_blocks"][0]
        font = sr._font(block["font_family"], block["size"], block["weight"])
        ascent, descent = font.getmetrics()
        lh = block["size"] * block["line_spacing"]
        max_h = spec["canvas"]["height"] * block["max_height_pct"] / 100
        self.assertEqual(head["lines"], int((max_h - ascent - descent) // lh) + 1)
        # characters per line: the box width / the font's measured advance on real Hebrew
        per_char = sr._measure(font, cb.SAMPLE_HE, "rtl", "he") / len(cb.SAMPLE_HE)
        box_w = spec["canvas"]["width"] * block["max_width_pct"] / 100
        self.assertEqual(head["chars_per_line"], int(box_w // per_char))
        self.assertEqual(head["max_words"], sr.HEADLINE_MAX_WORDS)
        self.assertEqual(head["font"], "Rubik 76px/700")
        # the body's smaller type holds more per line
        self.assertGreater(body["chars_per_line"], head["chars_per_line"])

    def test_a_heavier_weight_measures_wider(self):
        spec = starter()
        light = copy.deepcopy(spec)
        light["text_blocks"][0]["weight"] = 300
        self.assertGreater(cb.spec_budget(light)["copy_headline"]["chars_per_line"],
                           cb.spec_budget(spec)["copy_headline"]["chars_per_line"])

    def test_a_role_drawn_differently_gets_its_own_budget(self):
        spec = starter()
        hook_blocks = copy.deepcopy(spec["text_blocks"])
        hook_blocks[0]["size"] = 120
        spec["role_overrides"] = {"hook": {"text_blocks": hook_blocks}}
        t = Templates(self)
        slide_templates.save(27, "carousel default", spec)
        b = cb.budget(27, {})
        self.assertIn("hook", b["by_role"])
        self.assertLess(b["by_role"]["hook"]["copy_headline"]["max_chars"], b["all"]["copy_headline"]["max_chars"])
        self.assertTrue(t)


# ─── 5. The budget follows the template ──────────────────────────────────────

@NEEDS_RAQM
class FollowsTheTemplate(unittest.TestCase):
    def test_bigger_type_shrinks_the_budget(self):
        Templates(self)
        before = cb.budget(27, {})
        self.assertEqual(before["template"], "carousel default")                 # the starter, no template yet
        Templates(self).save(size_headline=110, size_body=56)                     # the manager's design: bigger type
        after = cb.budget(27, {})
        self.assertEqual(after["version"], 1)
        for field in ("copy_headline", "copy_body"):
            self.assertLess(after["all"][field]["max_chars"], before["all"][field]["max_chars"], field)
            self.assertLessEqual(after["all"][field]["lines"], before["all"][field]["lines"], field)

    def test_the_fit_check_follows_it_too(self):
        t = Templates(self)
        slides = [{"asset_id": "s1", "role": "point", "asset_type": "text_slide",
                   "copy_headline": hebrew(words=8), "copy_body": ""}]
        self.assertEqual(cb.fit(27, {}, slides)["over"], [])
        t.save(size_headline=150)
        got = cb.fit(27, {}, slides)
        self.assertEqual(got["over"], ["s1"])
        self.assertIn("שקופית 1, הכותרת", got["slides"]["s1"]["lines"][0])


# ─── 2. The copy stage writes within it ──────────────────────────────────────

@NEEDS_RAQM
class WithinTheBudget(unittest.TestCase):
    def setUp(self):
        Templates(self)

    def test_copy_written_to_the_budget_fits_and_past_it_does_not(self):
        b = cb.budget(27, {})["all"]
        head = hebrew(chars=b["copy_headline"]["max_chars"], words=b["copy_headline"]["max_words"])
        body = hebrew(chars=b["copy_body"]["max_chars"])
        slides = [{"asset_id": "s1", "role": "point", "asset_type": "text_slide", "copy_headline": head,
                   "copy_body": body}]
        self.assertEqual(cb.fit(27, {}, slides)["over"], [], (len(head), len(body)))
        slides[0]["copy_body"] = hebrew(chars=int(b["copy_body"]["max_chars"] * 1.7))
        self.assertEqual(cb.fit(27, {}, slides)["over"], ["s1"])

    def test_the_personas_get_the_budget_and_the_rule(self):
        from agents import support_agent
        saved = cb.for_prompt
        cb.for_prompt = lambda cid: {"rule": cb.PROMPT_RULE, "variants": {"default": {"template": "x"}}}
        self.addCleanup(setattr, cb, "for_prompt", saved)
        payload = {}
        support_agent._with_content_context(27, "SYSTEM", payload)
        self.assertEqual(payload["slide_copy_budget"]["variants"]["default"]["template"], "x")
        self.assertIn("COPY THAT FITS", support_agent.CONTENT_CONTEXT_RULE)
        self.assertIn("needs_room", support_agent.CONTENT_CONTEXT_RULE)
        from core import agent_actions
        self.assertIn("needs_room", json.dumps(agent_actions.ACTIONS["produce_post"]["params"]["post"]["hint"]))

    def test_for_prompt_measures_every_palette_variant(self):
        got = cb.for_prompt(27)
        self.assertIn("rule", got)
        for label, b in got["variants"].items():
            self.assertIn("copy_headline", b["all"])
            self.assertGreater(b["all"]["copy_headline"]["max_chars"], 0, label)


# ─── 3. Checked at submit and on the approval card ───────────────────────────

@NEEDS_RAQM
class AtApproval(unittest.TestCase):
    def setUp(self):
        tpb.Pipeline.setUp(self)
        Templates(self)
        from core import agent_characters as ac
        saved = (ac.approval_flags, ac.on_items_approved)
        ac.approval_flags = lambda cid, has, _brief_text=None: []
        ac.on_items_approved = lambda *a, **k: []
        self.addCleanup(lambda: (setattr(ac, "approval_flags", saved[0]), setattr(ac, "on_items_approved", saved[1])))

    def long_post(self, **asset_extra):
        post = tpb._post()
        post["assets"][2]["copy_body"] = hebrew(chars=900)            # slide 3: far too long for the body box
        post["assets"][2].update(asset_extra)
        return post

    def test_one_round_to_shorten_then_flagged_on_the_card_before_approval(self):
        first = pp.submit(27, self.long_post(), "", "he")
        self.assertEqual(first.get("code"), "copy_too_long")
        self.assertIn("ארוך מדי לתבנית העיצוב הנוכחית", first["refuse"])
        self.assertIn("שקופית 3, הטקסט: ארוך בערך ב-", first["refuse"])
        self.assertEqual(self.store.items, [])                          # nothing queued yet
        second = pp.submit(27, self.long_post(), "", "he")             # the agent didn't shorten it
        self.assertIn("queued", second)
        self.assertIn("שקופיות ארוכות מדי לתבנית הנוכחית", second["line"])
        row = next(r for r in sq.view(27, "he")["items"] if r["kind"] == "post")
        s1, s2, s3 = row["post"]["slides"]
        self.assertTrue(s1["fit"]["fits"])
        self.assertEqual(s2["fit"], {})                                  # a generated image: no copy on it
        self.assertFalse(s3["fit"]["fits"])
        self.assertTrue(any("שקופית 3, הטקסט" in l for l in s3["fit"]["lines"]))
        self.assertIn("1 מתוך 2 שקופיות הטקסט ארוכות מדי", row["post"]["fit_summary"])
        self.assertIn("אפשר לאשר כך", row["post"]["fit_summary"])
        # the manager may still approve — it's never a block
        sq.approve(27, [row["id"]])
        after = next(r for r in sq.view(27, "he")["items"] if r["kind"] == "post")
        self.assertEqual(after["status"], "approved")
        self.assertEqual(after["post"]["fit_summary"], "")

    def test_an_edit_that_shortens_clears_the_flag(self):
        pp.submit(27, self.long_post(), "", "he")
        item = pp.submit(27, self.long_post(), "", "he")["queued"]
        sq.edit_copy(27, item["id"], [{"asset_id": "s3", "copy_body": "קצר וברור"}])
        row = next(r for r in sq.view(27, "he")["items"] if r["kind"] == "post")
        self.assertTrue(row["post"]["slides"][2]["fit"]["fits"])
        self.assertEqual(row["post"]["fit_summary"], "")

    def test_needs_room_goes_straight_to_the_card_with_the_reason(self):
        out = pp.submit(27, self.long_post(needs_room="שלושה שלבים שצריך לראות יחד"), "", "he")
        self.assertIn("queued", out)
        row = next(r for r in sq.view(27, "he")["items"] if r["kind"] == "post")
        lines = row["post"]["slides"][2]["fit"]["lines"]
        self.assertTrue(any("שלושה שלבים שצריך לראות יחד" in l for l in lines))

    def test_copy_that_fits_is_queued_at_once(self):
        out = pp.submit(27, tpb._post(), "", "he")
        self.assertIn("queued", out)
        self.assertNotIn("ארוכות מדי", out["line"])

    def test_the_card_draws_it(self):
        with open(os.path.join(os.path.dirname(HERE), "dashboard", "client", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn('data-soc-fit="over"', html)
        self.assertIn('data-soc-fit="summary"', html)
        self.assertIn("p.fit_summary", html)


# ─── 4. A failed render names the slide, the field and how much ──────────────

@NEEDS_RAQM
class NamedFailure(unittest.TestCase):
    ROLES = {"bg": "#101010", "fg": "#FAFAFA", "accent": "#FF4C1F", "muted": "#888888"}

    def failing_set(self):
        slides = [{"copy_headline": "כותרת קצרה", "copy_body": "", "role": "hook"},
                  {"copy_headline": "שנייה", "copy_body": hebrew(chars=900), "role": "point"}]
        with self.assertRaises(sr.RenderError) as e:
            sr.render_set(starter(), self.ROLES, slides, encode=False)
        return e.exception.problems

    def test_the_problem_carries_slide_field_and_amount(self):
        p = next(p for p in self.failing_set() if p.get("kind") == "lines")
        self.assertEqual((p["slide"], p["role"]), (2, "body"))
        self.assertGreater(p["lines"], p["fit_lines"])
        self.assertGreater(p["need_px"], p["have_px"])
        self.assertGreater(p["text_chars"], 800)

    def test_said_in_plain_hebrew(self):
        lines = cb.problem_lines(self.failing_set())
        self.assertEqual(len(lines), 1)                                   # one line per slide and field
        self.assertRegex(lines[0], r"^שקופית 2, הטקסט: ארוך בערך ב-\d+% ממה שנכנס \(\d+ שורות, יש מקום ל-\d+\)\. "
                                   r"צריך לקצר בערך \d+ תווים\.$")
        words = cb.problem_line({"code": "headline_words", "slide": 4, "role": "headline", "words": 13, "limit": 10})
        self.assertEqual(words, "שקופית 4, הכותרת: 13 מילים, המקסימום 10. צריך להוריד 3 מילים.")
        wide = cb.problem_line({"code": "overflow", "kind": "word", "slide": 1, "role": "headline", "word": "אאאאא"})
        self.assertIn("המילה \"אאאאא\" רחבה מדי", wide)
        self.assertIn("Slide 2, the body text: about", cb.problem_lines(self.failing_set(), "en")[0])

    def test_the_production_line_names_it_not_design_failed(self):
        tpb.Pipeline.setUp(self)
        from agents import media_agent
        from core import agent_characters as ac
        from core import slide_design
        problems = [{"code": "overflow", "kind": "lines", "slide": 3, "role": "body", "lines": 9, "fit_lines": 6,
                     "need_px": 480, "have_px": 320, "text_chars": 300, "detail": "slide 3: …"}]
        saved = (media_agent.generate_from_brief, slide_design.open_for_post, ac.approval_flags, ac.on_items_approved)
        media_agent.generate_from_brief = lambda *a, **k: {"success": True, "kind": "image",
                                                           "file_id": "FILE_ok_0123456789", "credits": 1}
        slide_design.open_for_post = lambda cid, it, slides, lang="he": {"item": {"id": "d1"}, "problems": problems,
                                                                         "character_errors": []}
        ac.approval_flags = lambda cid, has, _brief_text=None: []
        ac.on_items_approved = lambda *a, **k: []
        try:
            saved_fit = cb.fit
            cb.fit = lambda *a, **k: {"checked": False, "over": [], "slides": {}}
            item = pp.submit(27, tpb._post(), "", "he")["queued"]
            saved_lang = pp._lang
            pp._lang = lambda cid: "he"
            pp.produce(27, sq.approve(27, [item["id"]])[0]["id"], tell=False)
        finally:
            (media_agent.generate_from_brief, slide_design.open_for_post, ac.approval_flags,
             ac.on_items_approved) = saved
            cb.fit = saved_fit
            pp._lang = saved_lang
        got = sq.get(27, item["id"])["production"]
        self.assertEqual(got["state"], "partial")
        self.assertEqual(got["problems"],
                         ["• שקופית 3, הטקסט: ארוך בערך ב-50% ממה שנכנס (9 שורות, יש מקום ל-6). צריך לקצר בערך 100 תווים."])
        self.assertNotIn("עיצוב שקופיות הטקסט נכשל", " ".join(got["problems"]))

    def test_the_design_card_lists_them(self):
        from core import slide_design
        item = {"id": "d1", "design": {"slides": [{}, {}], "problems": [
            {"code": "overflow", "kind": "lines", "slide": 2, "role": "body", "lines": 8, "fit_lines": 5,
             "need_px": 500, "have_px": 300, "text_chars": 240, "detail": "x"}]}}
        view = slide_design.view(item, "he")
        self.assertEqual(len(view["problem_lines"]), 1)
        self.assertTrue(view["problem_lines"][0].startswith("שקופית 2, הטקסט:"))


class NoRendererHere(unittest.TestCase):
    def test_fit_never_raises(self):
        Templates(self)
        saved = cb._check_slide
        cb._check_slide = lambda *a: (_ for _ in ()).throw(RuntimeError("no raqm"))
        try:
            got = cb.fit(27, {}, [{"asset_id": "s1", "asset_type": "text_slide", "copy_headline": "x"}])
        finally:
            cb._check_slide = saved
        self.assertEqual(got["checked"], False)
        self.assertEqual(cb.card(got, []), {})


if __name__ == "__main__":
    unittest.main()
