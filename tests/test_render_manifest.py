"""The render manifest (2026-11-26, knowledge/client-governance.md §8).

Live on client 27: a card said "כולל דמות שנוצרה ב-AI" over slides that showed
no character. The cause: the notice was decided from the BRIEF (a pose was
selected for the slide) while the preview the client sees never passed that
pose to the renderer. The rule now, whatever the cause: every statement
about a post is built from what the renderer DREW — and a brief that
declared something the render lacks FAILS the card, by name.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import test_slide_renderer as tsr              # noqa: E402  (the design-loop harness)
import test_agent_characters as tac            # noqa: E402  (a transparent figure, the pose id)
import test_production_honesty as tph          # noqa: E402  (the production harness)

from core import agent_characters as ac        # noqa: E402
from core import post_pipeline as pp           # noqa: E402
from core import render_manifest as rm         # noqa: E402
from core import slide_design                  # noqa: E402
from core import slide_renderer as sr          # noqa: E402
from core import social_queue as sq            # noqa: E402

ROOT = os.path.dirname(HERE)
AI_WORDS = ("AI", "בינה")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Design(tsr.DesignLoop):
    """A post of client 27 whose first slide declares Maya (composite)."""

    def setUp(self):
        super().setUp()
        self.alerts = []
        slide_design._alert = lambda lines: self.alerts.extend(lines)
        saved = (ac.poses_for, ac.pose_bytes, ac.approval_flags)
        ac.poses_for = lambda requests, **k: [{"pose_id": tac.POSE} for _ in requests]
        ac.pose_bytes = lambda pose_id: tac._figure()
        ac.approval_flags = lambda cid, has, _brief_text=None: ["ai_label"] if has else []
        self.addCleanup(lambda: [setattr(ac, n, v) for n, v in zip(("poses_for", "pose_bytes", "approval_flags"), saved)])

    def slides(self):
        slides = self._text_slides()
        slides[0].update(characters=["maya"], character_mode="composite")
        return slides

    def open(self):
        return slide_design.open_for_post(27, self._post_item(), self.slides())

    def card(self, item_id):
        return next(r for r in sq.view(27, "he")["items"] if r["id"] == item_id)


@tac.NEEDS_RAQM
class TheManifest(Design):
    def test_it_lists_every_layer_that_was_drawn(self):
        out = self.open()
        first, last = out["manifests"]
        self.assertEqual([l["type"] for l in first["layers"]], ["background", "character", "text", "counter"])
        ch = rm.layers(first, "character")[0]
        self.assertEqual((ch["id"], ch["pose_id"], ch["generated"]), ("maya", tac.POSE, True))
        self.assertEqual(len(ch["box"]), 4)                                    # where it was placed
        # each text field with its FINAL string
        self.assertEqual(" ".join(rm.layers(first, "text")[0]["text"].split()), tsr.HEAD)
        self.assertEqual({l["role"] for l in rm.layers(last, "text")}, {"headline", "body"})
        self.assertEqual(rm.characters(last), [])                               # slide 3 has no character
        self.assertEqual(out["item"]["design"]["manifests"], out["manifests"])   # kept on the card

    def test_the_stage_log_names_every_stage(self):
        self.open()
        rows = [r for r in sq.get(27, self.card_id())["design"]["trace"] if r["character"] == "maya"]
        self.assertEqual([r["stage"] for r in rows][:5],
                         ["brief_declared", "pose_selected", "pose_fetched", "background_removed", "composited"])
        self.assertTrue(all(r["outcome"].startswith("ok") for r in rows))
        self.assertEqual(rm.lost_at(rows), {})
        self.assertEqual(rm.STAGES[-1], "file_written")

    def card_id(self):
        return sq.view(27, "he")["items"][0]["id"]

    def test_the_preview_draws_the_character_the_card_talks_about(self):
        # THE cause on client 27: preview_png never passed the slide's pose to the renderer
        item = self.open()["item"]
        drawn = {}
        slide_design.preview_png(27, item["id"], 1, manifest=drawn)
        self.assertEqual(rm.characters(drawn), ["maya"])
        nobody = {}
        slide_design.preview_png(27, item["id"], 2, manifest=nobody)
        self.assertEqual(rm.characters(nobody), [])
        self.assertIn('loader_for(client_id), character=s.get("character"), manifest=drawn,', _src("core", "slide_design.py"))

    def test_the_disclosure_fires_if_and_only_if_something_generated_was_drawn(self):
        item = self.open()["item"]
        self.assertIn("ai_label", [f["code"] for f in self.card(item["id"])["flags"]])
        self.assertEqual(rm.disclosure(item["design"]["manifests"][0]), ["ai_label"])
        self.assertEqual(rm.disclosure(item["design"]["manifests"][1]), [])
        self.assertEqual(rm.disclosure({"layers": [{"type": "generated_image", "generated": True}]}), ["ai_content"])
        self.assertEqual(rm.disclosure({"layers": [{"type": "real_asset"}]}), [])
        self.assertEqual(rm.disclosure(None), [])

    def test_each_finished_file_carries_its_own_notice(self):
        item = self.open()["item"]
        sq.approve(27, [item["id"]])
        slide_design.approved(27, item["id"])
        files = {i["post_ref"]["asset_id"]: i for i in sq.items(27) if i.get("post_ref")}
        self.assertEqual(rm.characters(files["s1"]["manifest"]), ["maya"])
        self.assertEqual(files["s1"]["flags"], ["ai_label"])
        self.assertEqual(rm.characters(files["s3"]["manifest"]), [])
        self.assertFalse(files["s3"].get("flags"))                              # no character in THIS file: no notice
        self.assertNotIn("characters", files["s3"])


class Plain(tsr.DesignLoop):
    """A post of client 27 with text slides only (palette "black + red")."""

    def setUp(self):
        super().setUp()
        self.alerts = []
        slide_design._alert = lambda lines: self.alerts.extend(lines)

    def open(self, slides=None, post=None):
        return slide_design.open_for_post(27, post or self._post_item(), slides or self._text_slides())

    def card(self, item_id):
        return next(r for r in sq.view(27, "he")["items"] if r["id"] == item_id)


@tac.NEEDS_RAQM
class ThePaletteThatWasDrawn(Plain):
    """Client 27, 2026-11-26: a card said "צבעים: black + red" over a dark teal
    slide. The label came from the brief; nothing compared it with the file."""

    def test_the_manifest_records_the_colours_the_slide_was_drawn_with(self):
        out = self.open()
        brand = tsr.roles(tsr.RED)
        for m in out["manifests"]:
            self.assertEqual(m["palette"]["roles"], {r: brand[r].upper() for r in ("bg", "fg", "accent", "muted")})
            self.assertEqual(m["palette"]["variant"], tsr.RED)
            self.assertRegex(m["palette"]["background"]["mean"], r"^#[0-9A-F]{6}$")   # measured from the pixels
        d = self.card(out["item"]["id"])["design"]
        self.assertEqual((d["palette"], d["palette_drawn"], d["palette_custom"]), (tsr.RED, True, []))
        self.assertEqual(d["palette_swatches"][0]["accent"], brand["accent"].upper())

    def test_a_palette_that_did_not_reach_the_render_fails_the_card(self):
        real = sr.render_set
        sr.render_set = lambda spec, roles, *a, **k: real(spec, {**roles, "bg": "#0E3B43"}, *a, **k)
        self.addCleanup(setattr, sr, "render_set", real)
        out = self.open()
        design = out["item"]["design"]
        self.assertEqual(design["state"], "failed")
        self.assertEqual({(m["type"], m["id"]) for m in design["missing"]}, {("palette", tsr.RED)})
        card = self.card(out["item"]["id"])["design"]
        self.assertTrue(card["blocked"])
        self.assertIn(tsr.RED, card["missing_lines"][0])
        self.assertIn("צוירה בצבעים אחרים", card["missing_lines"][0])
        self.assertNotEqual(card["palette"], tsr.RED)                   # the label is the render's, not the brief's
        self.assertIn("#0E3B43", card["palette"])
        with self.assertRaises(sq.QueueError):
            sq.approve(27, [out["item"]["id"]])
        self.assertEqual(self.uploads, [])

    def test_the_final_files_are_refused_too(self):
        item = self.open()["item"]
        real = sr.render_set
        sr.render_set = lambda spec, roles, *a, **k: real(spec, {**roles, "bg": "#0E3B43"}, *a, **k)
        self.addCleanup(setattr, sr, "render_set", real)
        d = sq.get(27, item["id"])["design"]
        with self.assertRaises(RuntimeError):
            slide_design._finalize(27, {**item, "design": d}, {"spec": d["spec"], "name": "t", "version": 1})
        self.assertEqual(self.uploads, [])

    def teal(self, reason="", by="client"):
        slides = self._text_slides()
        slides[0]["colors"] = {"bg": "#0E3B43"}
        post = self._post_item()
        if reason:
            slides[0]["colors_reason"] = reason
            post["post"]["colors"] = {"requested": reason, "reason": reason, "by": by}
        return self.open(slides, post)

    def test_a_colour_outside_the_palette_with_a_reason_is_a_named_deviation_the_client_decides(self):
        out = self.teal("a sea theme for the summer launch", by="agent")
        design, item_id = out["item"]["design"], out["item"]["id"]
        self.assertEqual((design["state"], design.get("missing") or []), ("ready", []))
        self.assertEqual(out["manifests"][0]["palette"]["variant"], "")
        self.assertEqual(out["manifests"][0]["palette"]["outside"], {"bg": "#0E3B43"})
        self.assertEqual(out["manifests"][1]["palette"]["outside"], {})
        d = self.card(item_id)["design"]
        self.assertEqual((d["palette_custom"], d["deviation"]["state"]), ([1], "waiting"))
        self.assertNotEqual(d["palette"], tsr.RED)
        self.assertIn("#0E3B43", d["deviation"]["lines"][0])
        self.assertIn("a sea theme for the summer launch", d["deviation"]["reason"])
        self.assertIn("הסיבה שנכתבה בבריף", d["deviation"]["reason"])           # the agent's proposal, said as one
        self.assertTrue(d["blocked"])                                           # never approved silently
        with self.assertRaises(sq.QueueError) as ctx:
            sq.approve(27, [item_id])
        self.assertEqual(ctx.exception.code, "deviation_undecided")
        slide_design.decide_deviation(27, item_id, "accept")
        after = self.card(item_id)["design"]
        self.assertEqual((after["deviation"]["state"], after["blocked"]), ("accepted", False))
        self.assertEqual(sq.get(27, item_id)["design"]["deviation_accepted"]["by"], "client")
        self.assertEqual(len(sq.approve(27, [item_id])), 1)

    def test_the_client_can_turn_the_deviation_down_and_get_the_brand_colours(self):
        out = self.teal("the client asked for teal")
        item_id = out["item"]["id"]
        self.assertIn("לבקשתכם", self.card(item_id)["design"]["deviation"]["reason"])
        slide_design.decide_deviation(27, item_id, "brand")
        d = sq.get(27, item_id)["design"]
        self.assertTrue(all(m["palette"]["variant"] == tsr.RED and not m["palette"]["outside"] for m in d["manifests"]))
        self.assertFalse(any(s.get("colors") for s in d["slides"]))
        self.assertEqual(d["dropped"][-1]["type"], "palette")                   # the decision is recorded
        card = self.card(item_id)["design"]
        self.assertEqual((card["palette"], card["deviation"], card["blocked"]), (tsr.RED, {}, False))
        with self.assertRaises(sq.QueueError):
            slide_design.decide_deviation(27, item_id, "accept")                # nothing left to decide

    def test_a_deviation_with_no_reason_fails_the_card_like_a_missing_character(self):
        out = self.teal()
        design, item_id = out["item"]["design"], out["item"]["id"]
        self.assertEqual(design["state"], "failed")
        self.assertEqual([(m["type"], m["reason"], m["slide"]) for m in design["missing"]],
                         [("palette", "deviation_unexplained", 1)])
        card = self.card(item_id)["design"]
        self.assertIn("לא נכתבה סיבה", card["missing_lines"][0])
        self.assertIn("#0E3B43", card["missing_lines"][0])
        self.assertEqual(card["deviation"]["state"], "unexplained")
        with self.assertRaises(sq.QueueError):
            slide_design.decide_deviation(27, item_id, "accept")                # no reason = nothing to approve
        with self.assertRaises(sq.QueueError):
            sq.approve(27, [item_id])
        self.assertTrue(any("design FAILED, declared and not drawn" in a for a in self.alerts))
        slide_design.decide_deviation(27, item_id, "brand")                     # the one way out: the brand colours
        self.assertEqual(sq.get(27, item_id)["design"]["state"], "ready")
        self.assertEqual(len(sq.approve(27, [item_id])), 1)

    def test_another_variants_colour_is_inside_the_palette(self):
        gold = tsr.roles("black + gold")["accent"]
        slides = self._text_slides()
        slides[0]["colors"] = {"bg": tsr.roles(tsr.RED)["bg"], "accent": gold}
        out = self.open(slides)
        self.assertEqual(out["item"]["design"]["state"], "ready")
        d = self.card(out["item"]["id"])["design"]
        self.assertEqual((d["deviation"], d["blocked"]), ({}, False))           # the brand's own colour: no deviation
        self.assertEqual(d["palette_custom"], [1])                              # …but not called "black + red" either


class ThePaletteRules(unittest.TestCase):
    ROLES = {"bg": "#0B0B0B", "fg": "#FFFFFF", "accent": "#E3262F", "muted": "#A6A6A6"}

    def drawn(self, roles=None, mean="#0B0B0B", kind="solid"):
        m = rm.new(1080, 1350)
        rm.palette(m, roles or self.ROLES, {"type": kind, "color_role": "bg"}, mean)
        return m

    def want(self):
        return [{"type": "palette", "id": "black + red", "roles": dict(self.ROLES)}]

    def test_the_same_colours_are_not_missing(self):
        self.assertEqual(rm.missing(self.want(), self.drawn()), [])
        self.assertEqual(rm.missing(self.want(), self.drawn(mean="#0C0D0B")), [])        # rounding is not a mismatch

    def test_other_roles_or_other_pixels_are_missing(self):
        teal = rm.missing(self.want(), self.drawn({**self.ROLES, "bg": "#0E3B43"}, "#0E3B43"))
        self.assertEqual((teal[0]["type"], teal[0]["id"], teal[0]["roles"]), ("palette", "black + red", ["bg"]))
        pixels = rm.missing(self.want(), self.drawn(mean="#0E3B43"))                     # right roles, wrong file
        self.assertEqual(pixels[0]["mean"], "#0E3B43")
        self.assertEqual(rm.missing(self.want(), self.drawn(mean="#55504A", kind="image")), [])   # a photo is not one colour
        self.assertEqual(rm.missing(self.want(), None)[0]["type"], "palette")            # nothing known to be drawn

    def test_a_colour_the_brand_does_not_define_needs_a_reason(self):
        teal = {**self.ROLES, "bg": "#0E3B43"}
        want = [{"type": "palette", "id": "black + red", "roles": teal, "allowed": sorted(self.ROLES.values()), "reason": ""}]
        silent = rm.missing(want, self.drawn(teal, "#0E3B43"))
        self.assertEqual((silent[0]["why"], silent[0]["outside"]), ("no_reason", {"bg": "#0E3B43"}))
        want[0]["reason"] = "the client asked for teal"
        self.assertEqual(rm.missing(want, self.drawn(teal, "#0E3B43")), [])      # stated = a deviation, not a failure
        m = self.drawn(teal, "#0E3B43")
        self.assertEqual(rm.mark_outside(m, self.ROLES.values()), {"bg": "#0E3B43"})
        self.assertEqual(rm.mark_outside(self.drawn(), self.ROLES.values()), {})
        from core import post_brief
        with self.assertRaises(ValueError):
            post_brief.clean_colors({"slides": [{"bg": "#0E3B43"}]}, 1)          # the brief refuses colours with no reason
        own = post_brief.clean_colors({"reason": "a sea theme for the launch", "slides": [{"bg": "#0E3B43"}]}, 2)
        self.assertEqual((own["by"], own["reason"], len(own["slides"])), ("agent", "a sea theme for the launch", 2))
        asked = post_brief.clean_colors({"requested": "make it teal", "slides": [{"bg": "#0E3B43"}]}, 1)
        self.assertEqual((asked["by"], asked["reason"]), ("client", "make it teal"))
        self.assertIn("PALETTE DISCIPLINE", _src("agents", "support_agent.py"))

    def test_the_label_is_the_variant_only_when_its_colours_were_drawn(self):
        same = self.drawn()
        self.assertEqual(rm.name_palette(same, "black + red", self.ROLES), "black + red")
        other = self.drawn({**self.ROLES, "bg": "#0E3B43"})
        self.assertEqual(rm.name_palette(other, "black + red", self.ROLES), "")
        self.assertEqual(rm.palette_view([same], "he")["label"], "black + red")
        view = rm.palette_view([other, same], "he")
        self.assertEqual((view["custom"], view["variant"]), ([1], ""))
        self.assertIn("#0E3B43", view["label"])
        alone = rm.palette_view([other], "he")["label"]
        self.assertIn("לא פלטת המותג", alone)
        self.assertNotIn("black + red", alone)
        for lang in ("he", "en", "fr", "ar", "ru"):
            for key, table in rm.PALETTE_TEXT.items():
                self.assertTrue(table[lang], key)

    def test_before_a_render_the_label_says_it_is_the_brief(self):
        got = rm.palette_view([], "he", planned="black + red")
        self.assertFalse(got["drawn"])
        self.assertIn("black + red", got["label"])
        self.assertNotEqual(got["label"], "black + red")
        self.assertEqual(rm.palette_view([{"layers": []}], "he", planned="")["label"], "")   # an old file: nothing claimed
        asked = rm.palette_view([], "he", planned="black + red", requested="teal")["label"]
        self.assertNotIn("black + red", asked)

    def test_the_card_reads_the_manifest_and_not_the_brief(self):
        src = _src("core", "slide_design.py")
        view = src[src.index("def view("):]
        self.assertIn("render_manifest.palette_view(", view)
        self.assertNotIn('"palette": (d.get("palette") or {}).get("label", "")', src)
        self.assertNotIn('"palette": (post.get("palette") or {}).get("label", "")', _src("core", "social_queue.py"))
        self.assertIn("drawn.palette", rm.FACTS_RULE)
        named = self.drawn()
        rm.name_palette(named, "black + red", self.ROLES)
        self.assertEqual(rm.facts(named)["palette"]["brand_variant"], "black + red")


@tac.NEEDS_RAQM
class DeclaredAndNotDrawn(Design):
    """The required test: a brief declaring a character, compositing stubbed out."""

    def setUp(self):
        super().setUp()
        saved = sr._character
        sr._character = lambda *a, **k: {}                                      # the compositing step draws nothing
        self.addCleanup(setattr, sr, "_character", saved)

    def test_the_card_fails_and_no_disclosure_is_produced(self):
        out = self.open()
        design = out["item"]["design"]
        self.assertEqual(design["state"], "failed")
        self.assertEqual([(m["type"], m["id"], m["slide"]) for m in design["missing"]], [("character", "maya", 1)])
        self.assertEqual(rm.characters(design["manifests"][0]), [])
        card = self.card(out["item"]["id"])
        said = " ".join(f["text"] for f in card.get("flags") or [])
        self.assertNotIn("ai_label", [f["code"] for f in card.get("flags") or []])
        for word in AI_WORDS:
            self.assertNotIn(word, said)                                         # no disclosure text at all
        self.assertTrue(card["design"]["blocked"])
        self.assertIn("הדמות של מאיה הוגדרה לפוסט ולא צוירה", card["design"]["missing_lines"][0])
        self.assertTrue(any("design FAILED, declared and not drawn" in a for a in self.alerts))

    def test_it_cannot_be_approved_and_nothing_is_written(self):
        item = self.open()["item"]
        with self.assertRaises(sq.QueueError) as ctx:
            sq.approve(27, [item["id"]])
        self.assertEqual(ctx.exception.code, "element_missing")
        self.assertEqual(sq.get(27, item["id"])["status"], "waiting")
        self.assertEqual(self.uploads, [])
        d = sq.get(27, item["id"])["design"]
        with self.assertRaises(RuntimeError):                                   # even called directly, no file is written
            slide_design._finalize(27, {**item, "design": d}, {"spec": d["spec"], "name": "t", "version": 1})
        self.assertEqual(self.uploads, [])

    def test_the_agents_are_told_it_is_not_drawn(self):
        self.open()
        block = sq.prompt_block(27)
        row = block["waiting_items"][0]
        self.assertEqual(row["drawn"]["characters"], [])
        self.assertTrue(row["not_drawn"][0].startswith("slide 1: character maya"))
        self.assertIn("Never say a character appears", block["facts_rule"])
        self.assertIn("never from the brief".lower(), _src("core", "render_manifest.py").lower())

    def test_the_client_can_go_on_without_the_character(self):
        item = self.open()["item"]
        after = slide_design.without_character(27, item["id"])
        self.assertEqual((after["design"]["state"], after["design"]["missing"]), ("ready", []))
        self.assertEqual(after["design"]["dropped"][0]["id"], "maya")           # the decision is recorded
        self.assertEqual(len(sq.approve(27, [item["id"]])), 1)
        with self.assertRaises(sq.QueueError):
            slide_design.without_character(27, item["id"])                      # only a failed, waiting design


class Statements(unittest.TestCase):
    def test_declared_and_missing(self):
        slide = {"copy_headline": "שלום", "copy_body": "עולם יפה", "declared": {"characters": ["maya"]}}
        wanted = rm.declared(slide)
        self.assertEqual([(w["type"], w["id"]) for w in wanted], [("character", "maya"), ("text", "headline"), ("text", "body")])
        self.assertEqual(len(rm.missing(wanted, None)), 3)                       # no manifest: nothing is known drawn
        drawn = {"layers": [{"type": "text", "role": "headline", "text": "שלום"},
                            {"type": "text", "role": "body", "text": "עולם\nיפה"}]}
        self.assertEqual(rm.missing(wanted, drawn), [{"type": "character", "id": "maya"}])
        drawn["layers"].append({"type": "character", "id": "maya"})
        self.assertEqual(rm.missing(wanted, drawn), [])
        self.assertEqual(rm.declared({"wants_character": {"agent": "lior"}}), [{"type": "character", "id": "lior"}])

    def test_the_post_card_plans_then_reports_what_was_drawn(self):
        slide = {"asset_id": "s1", "flags": ["ai_label", "text_slide_pending"], "characters": ["maya"]}
        self.assertEqual(sq._slide_flags(slide, None), ["text_slide_pending", "character_planned"])     # a plan, not a claim
        empty = {"layers": [{"type": "background"}, {"type": "text", "text": "x"}]}
        self.assertEqual(sq._slide_flags(slide, empty), ["text_slide_pending", "character_not_drawn"])
        with_her = {"layers": [{"type": "character", "id": "maya", "generated": True}]}
        self.assertEqual(sq._slide_flags(slide, with_her), ["text_slide_pending", "ai_label"])
        self.assertEqual(sq._slide_flags({"asset_id": "s2", "flags": []}, empty), [])

    def test_stored_items_never_claim_from_the_brief(self):
        # a slide file stored before manifests: the notice follows the pose its own render carried
        self.assertEqual(sq.true_flags({"kind": "image", "flags": ["ai_label", "ai_person_forbidden"]}), [])
        self.assertEqual(sq.true_flags({"kind": "image", "flags": ["ai_label"], "characters": [{"agent": "maya"}]}), ["ai_label"])
        self.assertEqual(sq.true_flags({"kind": "image", "flags": ["ai_label"], "manifest": {"layers": []}}), [])
        design = {"kind": "design", "flags": ["ai_label"], "design": {"slides": [{"copy_headline": "x"}]}}
        self.assertEqual(sq.true_flags(design), [])
        design["design"]["slides"][0]["character"] = {"agent": "maya", "pose_id": "p1"}
        self.assertEqual(sq.true_flags(design), ["ai_label"])
        for code in ("ai_content", "character_planned", "character_not_drawn"):
            for lang in ("he", "en", "fr", "ar", "ru"):
                self.assertTrue(sq.FLAG_TEXT[code][lang])

    def test_the_brief_never_stores_the_ai_notice(self):
        src = _src("core", "post_pipeline.py")
        self.assertIn('if f != "ai_label"]', src)
        self.assertIn("flags += render_manifest.disclosure(drawn)", src)
        self.assertNotIn('f in ("ai_person_forbidden", "ai_label")', src + _src("core", "slide_design.py"))


class Production(tph.Harness):
    def test_a_generated_asset_lacking_its_character_fails_and_is_not_queued(self):
        post = tph.tpb._post()
        post["assets"][1]["characters"] = ["lior"]
        item = self.queue(post)["queued"]
        production = self.approve(item["id"])                                    # the generator returns no character
        self.assertEqual(production["state"], "failed")
        self.assertEqual([(m["id"], m["asset_id"]) for m in production["missing"]], [("lior", "s2")])
        self.assertFalse([i for i in sq.items(27) if (i.get("post_ref") or {}).get("asset_id") == "s2"])
        last = self.lines[-1][1]
        self.assertIn("הדמות של ליאור הוגדרה לתמונה ולא נמצאת בה", last)
        self.assertNotIn("הסתיימה", last)

    def test_no_line_describes_a_post_without_its_manifest(self):
        item = self.queue()["queued"]
        production = self.approve(item["id"], design={"item": {"id": "design0001"}, "problems": [], "manifests": []})
        self.assertEqual(pp.unbacked(production), ["s1", "s3"])
        last = self.lines[-1][1]
        self.assertIn("לא הצלחתי לאמת מה בדיוק צויר", last)
        self.assertNotIn("מחכים לאישור הפרסום", last)
        self.assertNotIn("מחכות לכם כעיצוב", last)

    def test_an_asset_that_never_reached_the_queue_is_not_made(self):
        item = self.queue()["queued"]
        self.patch(sq, "add_safely", lambda *a, **k: {})                        # the queue write failed (and alerted)
        production = self.approve(item["id"])
        self.assertEqual(production["made"], 0)
        self.assertIn("not_queued", [a["status"] for a in production["assets"]])
        self.assertNotEqual(production["state"], "done")
        self.assertIn("לא נכנס לתור האישור", self.lines[-1][1])

    def test_a_clean_run_is_backed_by_manifests(self):
        item = self.queue()["queued"]
        production = self.approve(item["id"])
        self.assertEqual((production["state"], pp.unbacked(production), production["missing"]), ("done", [], []))
        self.assertTrue(all("drawn" in a for a in production["assets"]))
        made = next(i for i in sq.items(27) if i.get("post_ref"))
        self.assertEqual(rm.disclosure(made["manifest"]), ["ai_content"])        # the picture itself is generated


class Page(unittest.TestCase):
    html = _src("dashboard", "client", "index.html")

    def test_a_failed_design_has_no_approve_button_and_a_way_out(self):
        self.assertIn("if (waiting && !ADMIN_VIEW && !revising && !d.blocked) {", self.html)
        self.assertIn('data-soc-nochar="${esc(item.id)}"', self.html)
        self.assertIn("(d.missing_lines || []).map(", self.html)
        self.assertIn("/design/without-character", self.html)
        self.assertIn('@app.post("/api/client/social/{item_id}/design/without-character")', _src("core", "api_server.py"))


if __name__ == "__main__":
    unittest.main()
