"""The post contract (2026-11-23, core/post_brief + core/post_pipeline).

The failure it fixes: a carousel request went to the image model as one
free-text brief and came back as four random AI sneakers with zero readable
text. What must stay true:
- both knowledge files load in full, or the work fails loudly;
- post_format is explicit with a reason ("a carousel about why X" =
  text_carousel; ambiguous = ONE question);
- the slide copy NEVER reaches an image prompt (assert_copy_free), and Hebrew
  never reaches a model; text_slide is not sent to a model at all;
- objections: two rounds, then the manager — never a third;
- the audience picks ONE palette variant; sourcing priority; lead-time warning;
- the copy waits in the Social tab, is editable, is never publishable, and
  its approval is what starts production.
"""
import contextlib
import json
import os
import sys
import types
import unittest
from datetime import date

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

from core import post_brief as pb       # noqa: E402
from core import post_pipeline as pp    # noqa: E402
from core import social_queue as sq     # noqa: E402

HEAD = "הסיבה הראשונה: המתנה שנשארת"
BODY = "כל זוג נעליים מעוצב פעם אחת בלבד, בשבילו"


def _post(**over):
    post = {
        "post_format": "text_carousel", "format_reason": "the value is an argument: reasons, so text leads",
        "objective": "consideration", "audience": "gift buyers (private customers)", "set_id": "gift-why-1",
        "title": "למה עיצוב אישי", "caption": "כיתוב קצר עם קריאה אחת לפעולה",
        "assets": [
            {"asset_id": "s1", "role": "hook", "copy_headline": HEAD, "copy_body": "",
             "asset_type": "text_slide", "visual_direction": ""},
            {"asset_id": "s2", "role": "point", "copy_headline": "רגע הפתיחה", "copy_body": BODY,
             "asset_type": "generated_image",
             "visual_direction": "hands opening a plain black gift box on a studio table, warm side light",
             "must_avoid": ["text", "logos"], "budget_credits": 5},
            {"asset_id": "s3", "role": "cta", "copy_headline": "מזמינים עד ה-1 בחודש", "copy_body": "",
             "asset_type": "text_slide", "visual_direction": ""},
        ],
    }
    post.update(over)
    return post


class Context(unittest.TestCase):
    def test_both_files_load_in_full_for_client_27(self):
        ctx = pb.load_context(27)
        self.assertIn("Social & Design Craft Library", ctx["craft"])
        self.assertIn("Shin Sekai", ctx["brand"])
        with open(pb.CRAFT_PATH, encoding="utf-8") as f:
            self.assertEqual(ctx["craft"], f.read().strip())           # in full, not a summary

    def test_missing_brand_brief_degrades_and_says_what_it_lacks(self):
        # 2026-11-24 (core/brand_brief): no brief = the agents still work and say what they lack
        from core import brand_brief
        ctx = pb.load_context(999999)
        self.assertEqual((ctx["brand"], ctx["brand_status"]), (brand_brief.MISSING_NOTE, "missing"))
        self.assertEqual(ctx["brand_lacks"], ["brand_brief"])
        self.assertEqual(ctx["brand_facts"], {"lead_time_days": None, "palette_variants": []})

    def test_missing_craft_library_fails_loudly(self):
        saved = pb.CRAFT_PATH
        pb.CRAFT_PATH = os.path.join(ROOT, "knowledge", "nope.md")
        try:
            with self.assertRaises(pb.ContextMissing) as e:
                pb.load_context(27)
            self.assertIn("knowledge/nope.md", e.exception.missing)
        finally:
            pb.CRAFT_PATH = saved

    def _no_craft(self):
        saved = pb.CRAFT_PATH
        pb.CRAFT_PATH = os.path.join(ROOT, "knowledge", "nope.md")
        self.addCleanup(setattr, pb, "CRAFT_PATH", saved)

    def test_media_agent_refuses_to_generate_without_context(self):
        from agents import media_agent
        self._no_craft()
        brief = pb.asset(pb.validate_post(_post(), 999999)["assets"][1])
        out = media_agent.generate_from_brief(999999, brief, {})
        self.assertEqual(out["status"], "context_missing")

    def test_submit_without_context_refuses_and_queues_nothing(self):
        self._no_craft()
        out = pp.submit(999999, _post(), "", "he")
        self.assertEqual(out["code"], "context_missing")
        self.assertIn("knowledge/nope.md", out["refuse"])


class Format(unittest.TestCase):
    def test_carousel_about_why_is_text_carousel(self):
        for text in ("a carousel about why custom shoes make the best gift",
                     "תכין קרוסלה על למה עיצוב אישי הוא המתנה הכי טובה",
                     "קרוסלה של 4 סיבות להזמין"):
            self.assertEqual(pb.infer_format(text), ["text_carousel"], text)
            self.assertEqual(pb.resolve_format("", "", text)["post_format"], "text_carousel")

    def test_the_request_wins_over_a_wrong_choice(self):
        out = pb.resolve_format("product_carousel", "show the shoes", "a carousel about why X")
        self.assertEqual(out["post_format"], "text_carousel")
        self.assertIn("product_carousel", out["format_reason"])

    def test_explicit_choice_needs_a_reason(self):
        self.assertEqual(pb.resolve_format("before_after", "transformation is the proof", "")["post_format"],
                         "before_after")
        with self.assertRaises(ValueError):
            pb.resolve_format("before_after", "", "")
        with self.assertRaises(ValueError):
            pb.resolve_format("story", "a reason long enough", "")

    def test_ambiguous_asks_one_question(self):
        with self.assertRaises(pb.FormatQuestion) as e:
            pb.resolve_format("", "", "make me a carousel")
        self.assertEqual(set(e.exception.question), {"he", "en", "fr", "ar", "ru"})
        self.assertEqual(e.exception.question["he"].count("?"), 1)
        with self.assertRaises(pb.FormatQuestion):
            pb.resolve_format("", "", "something nice for instagram")

    def test_other_formats(self):
        self.assertEqual(pb.infer_format("before and after of a pair we redid"), ["before_after"])
        self.assertEqual(pb.infer_format("קרוסלה של המוצרים החדשים"), ["product_carousel"])
        self.assertEqual(pb.infer_format("a reel of the studio"), ["reel"])


class TheContract(unittest.TestCase):
    def test_validate_post_and_fields(self):
        post = pb.validate_post(_post(), 27)
        self.assertEqual(len(post["assets"]), 3)
        self.assertEqual(set(post["assets"][0]), set(pb.AssetBrief.__dataclass_fields__))
        a = post["assets"][1]
        self.assertEqual((a["post_format"], a["objective"], a["client_id"], a["set_id"]),
                         ("text_carousel", "consideration", 27, "gift-why-1"))

    def test_bad_posts(self):
        for over in ({"objective": "viral"}, {"set_id": "!"}, {"audience": ""}, {"caption": ""},
                     {"assets": []}, {"assets": [{"asset_id": "x", "role": "hook", "asset_type": "generated_image",
                                                  "visual_direction": "short"}]}):
            with self.subTest(over=over):
                with self.assertRaises(ValueError):
                    pb.validate_post(_post(**over), 27)

    def test_prompt_material_has_no_copy(self):
        brief = pb.asset(pb.validate_post(_post(), 27)["assets"][1])
        material = json.dumps(brief.prompt_material(), ensure_ascii=False)
        self.assertNotIn(BODY, material)
        self.assertNotIn("רגע הפתיחה", material)
        self.assertIn("gift box", material)


class CopyNeverReachesThePrompt(unittest.TestCase):
    def setUp(self):
        self.brief = pb.AssetBrief(
            asset_id="s2", post_format="text_carousel", objective="consideration", client_id=27, role="point",
            copy_headline="Shoes that tell her story", copy_body="Every pair is drawn once for one person only",
            asset_type="generated_image", visual_direction="hands opening a gift box", set_id="gift-why-1")

    def test_clean_prompt_passes(self):
        pb.assert_copy_free("Close-up of hands opening a plain black gift box, warm studio light", self.brief)

    def test_headline_body_part_or_hebrew_fails(self):
        for prompt in ("A box. Shoes that tell her story.",
                       "studio photo, every pair is drawn once, soft light",
                       "SHOES THAT TELL HER, cinematic",
                       "a box with the words מתנה on it"):
            with self.subTest(prompt=prompt):
                with self.assertRaises(pb.CopyLeak):
                    pb.assert_copy_free(prompt, self.brief)

    def test_generation_path_fails_the_call_before_any_model(self):
        from agents import media_agent
        calls = {"gen": 0, "claude_payload": None}
        saved = (media_agent._craft_brief_prompt, media_agent.gen.generate_image, media_agent._generation_key,
                 media_agent.drive_storage.scope, media_agent.agent_alert)

        def leaky(client_id, brief, palette, context):
            calls["claude_payload"] = brief.prompt_material()
            return {"generation_prompt": f"A studio shot. {brief.copy_headline}"}

        media_agent._craft_brief_prompt = leaky
        media_agent.gen.generate_image = lambda *a, **k: calls.__setitem__("gen", calls["gen"] + 1)
        media_agent._generation_key = lambda cid: "id:secret"
        media_agent.drive_storage.scope = lambda cid: contextlib.nullcontext()
        media_agent.agent_alert = lambda *a, **k: None
        try:
            out = media_agent.generate_from_brief(27, self.brief, {})
        finally:
            (media_agent._craft_brief_prompt, media_agent.gen.generate_image, media_agent._generation_key,
             media_agent.drive_storage.scope, media_agent.agent_alert) = saved
        self.assertEqual(out["status"], "copy_leak")
        self.assertEqual(calls["gen"], 0)                                    # the model was never called
        self.assertNotIn("tell her story", json.dumps(calls["claude_payload"]))

    def test_the_real_prompt_payload_carries_no_copy(self):
        from agents import media_agent
        seen = {}
        saved = media_agent.safe_claude_json_call
        media_agent.safe_claude_json_call = lambda system, user, **k: seen.update(user=user) or {"generation_prompt": "x"}
        try:
            media_agent._craft_brief_prompt(27, self.brief, {"label": "black + red", "colors": ["black", "red"]},
                                            pb.load_context(27))
        finally:
            media_agent.safe_claude_json_call = saved
        payload = json.loads(seen["user"])
        self.assertNotIn("tell her story", seen["user"])
        self.assertNotIn("drawn once for one person", seen["user"])
        self.assertEqual(payload["palette"]["colors"], ["black", "red"])
        self.assertIn("Social & Design Craft Library", payload["craft_library"])

    def test_text_slide_is_never_sent_to_a_model(self):
        from agents import media_agent
        slide = pb.AssetBrief(asset_id="s1", post_format="text_carousel", objective="consideration", client_id=27,
                              role="hook", copy_headline=HEAD, copy_body="", asset_type="text_slide",
                              visual_direction="", set_id="gift-why-1")
        # Since 2026-11-23 a text slide is DRAWN (core/slide_design → slide_renderer)
        from core import slide_design
        saved = (media_agent._craft_brief_prompt, media_agent.gen.generate_image, slide_design.render_asset)
        media_agent._craft_brief_prompt = lambda *a, **k: self.fail("text_slide reached the prompt step")
        media_agent.gen.generate_image = lambda *a, **k: self.fail("text_slide reached an image model")
        drawn = []
        slide_design.render_asset = lambda cid, brief, palette: drawn.append(brief.asset_id) or {
            "success": True, "kind": "image", "file_id": "F" * 12, "credits": 0}
        try:
            out = media_agent.generate_from_brief(27, slide, {})
        finally:
            media_agent._craft_brief_prompt, media_agent.gen.generate_image, slide_design.render_asset = saved
        self.assertEqual((out["success"], out["credits"], drawn), (True, 0, ["s1"]))

    def test_old_path_refuses_hebrew_in_the_prompt(self):
        with self.assertRaises(pb.CopyLeak):
            pb.assert_no_hebrew("a shoe with עיצוב אישי written on it")


class Objections(unittest.TestCase):
    def test_two_rounds_then_the_manager_never_a_third(self):
        history = []
        first = pb.record_objection(history, "s2", "media", "use a real photo")
        self.assertEqual((first["round"], first["action"]), (1, "revise"))
        second = pb.record_objection(history, "s2", "media", "still generated", response="it is a scene")
        self.assertEqual((second["round"], second["action"]), (2, "escalate"))
        self.assertEqual(second["positions"], {"media": "still generated", "counterpart": "it is a scene"})
        self.assertTrue(pb.escalated(history, "s2"))
        with self.assertRaises(pb.ObjectionCapReached):
            pb.record_objection(history, "s2", "media", "third time")
        self.assertEqual(len(history), 2)
        self.assertEqual(pb.record_objection(history, "s3", "media", "other")["round"], 1)   # per asset


class Palette(unittest.TestCase):
    def setUp(self):
        self.facts = pb.load_context(27)["brand_facts"]

    def test_shin_sekai_variants_are_read(self):
        labels = {v["segment"]: v["label"] for v in self.facts["palette_variants"]}
        self.assertEqual(labels, {"b2c": "black + red", "b2b": "black + gold"})
        self.assertEqual(self.facts["lead_time_days"], (15, 25))

    def test_audience_picks_one_variant(self):
        for audience, label in (("gift buyers", "black + red"), ("private customers, fans", "black + red"),
                                ("לקוחות פרטיים שקונים מתנה", "black + red"), ("sports teams", "black + gold"),
                                ("businesses and events", "black + gold"), ("עסקים וקבוצות", "black + gold")):
            with self.subTest(audience=audience):
                self.assertEqual(pb.resolve_palette(self.facts, audience)["label"], label)

    def test_unresolved_audience_is_a_question_not_a_mix(self):
        for audience in ("everyone", "gift buyers and businesses"):
            with self.assertRaises(pb.AudienceUnresolved):
                pb.resolve_palette(self.facts, audience)
        self.assertEqual(pb.resolve_palette({"palette_variants": []}, "anyone"), {})


class Sourcing(unittest.TestCase):
    def brief(self, **over):
        base = dict(asset_id="a", post_format="text_carousel", objective="awareness", client_id=27, role="point",
                    copy_headline="", copy_body="", asset_type="generated_image",
                    visual_direction="a studio workbench with brushes", set_id="set-1")
        base.update(over)
        return pb.AssetBrief(**base)

    def test_priority_order(self):
        self.assertEqual(pb.SOURCE_PRIORITY, ("real_asset", "template", "generated"))
        self.assertEqual([pb.sourcing(self.brief(asset_type=t))["priority"]
                          for t in ("real_photo", "text_slide", "generated_image")], [0, 1, 2])

    def test_generated_scene_is_flagged_concept(self):
        out = pb.sourcing(self.brief())
        self.assertEqual((out["objection"], out["flags"]), ("", ["concept"]))

    def test_generated_product_proof_is_objected(self):
        self.assertIn("real", pb.sourcing(self.brief(role="proof"))["objection"])
        self.assertIn("product_carousel", pb.sourcing(self.brief(post_format="product_carousel"))["objection"])
        self.assertEqual(pb.sourcing(self.brief(asset_type="real_photo", role="proof"))["objection"], "")


class LeadTime(unittest.TestCase):
    facts = {"lead_time_days": (15, 25)}

    def test_order_by_and_warning(self):
        order_by = pb.business_days_before(date(2026, 12, 31), 25)
        self.assertEqual(order_by.isoformat(), "2026-11-26")      # Dec 31 is a Thursday; Sun-Thu counted
        late = pb.lead_time_warning(self.facts, "2026-12-31", "2026-11-27")
        self.assertEqual((late["level"], late["order_by"]), ("late", "2026-11-26"))
        tight = pb.lead_time_warning(self.facts, "2026-12-31", "2026-11-22")
        self.assertEqual(tight["level"], "tight")
        self.assertEqual(pb.lead_time_warning(self.facts, "2026-12-31", "2026-11-01"), {})

    def test_not_applicable(self):
        self.assertEqual(pb.lead_time_warning({}, "2026-12-31", "2026-12-30"), {})
        self.assertEqual(pb.lead_time_warning(self.facts, "", "2026-12-30"), {})


class FakeQueueStore:
    def __init__(self):
        self.items = []

    def install(self, test):
        saved = (sq._load, sq._save)
        sq._load = lambda cid: [dict(i) for i in self.items]
        sq._save = lambda cid, items: setattr(self, "items", [dict(i) for i in items])
        test.addCleanup(lambda: (setattr(sq, "_load", saved[0]), setattr(sq, "_save", saved[1])))


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.store = FakeQueueStore()
        self.store.install(self)
        self.drafts = {}
        saved = (pp._load_drafts, pp._save_drafts, pp._alert, pp._tell)
        pp._load_drafts = lambda cid: json.loads(json.dumps(self.drafts))
        pp._save_drafts = lambda cid, d: setattr(self, "drafts", json.loads(json.dumps(d)))
        pp._alert = lambda lines: None
        pp._tell = lambda *a, **k: None
        self.addCleanup(lambda: (setattr(pp, "_load_drafts", saved[0]), setattr(pp, "_save_drafts", saved[1]),
                                 setattr(pp, "_alert", saved[2]), setattr(pp, "_tell", saved[3])))

    def test_copy_waits_in_social_nothing_generated(self):
        from agents import media_agent
        saved = media_agent.generate_from_brief
        media_agent.generate_from_brief = lambda *a, **k: self.fail("generated before the copy was approved")
        try:
            out = pp.submit(27, _post(), "a carousel about why custom shoes", "he")
        finally:
            media_agent.generate_from_brief = saved
        item = out["queued"]
        self.assertEqual((item["kind"], item["status"], item["file_id"]), ("post", "waiting", ""))
        self.assertEqual(item["post"]["palette"]["label"], "black + red")
        self.assertEqual([s["copy_headline"] for s in item["post"]["slides"]][0], HEAD)
        self.assertIn("text_slide_pending", item["post"]["slides"][0]["flags"])
        self.assertIn("concept", item["post"]["slides"][1]["flags"])
        view = sq.view(27, "he")
        row = view["items"][0]
        self.assertEqual([s["copy_headline"] for s in row["post"]["slides"]][0], HEAD)   # the copy as text
        # Resubmitting the same set = the SAME card, revised
        again = pp.submit(27, _post(caption="כיתוב אחר"), "", "he")["queued"]
        self.assertEqual(again["id"], item["id"])
        self.assertEqual(len(self.store.items), 1)

    def test_copy_is_editable_then_locked(self):
        item = pp.submit(27, _post(), "", "he")["queued"]
        sq.edit_copy(27, item["id"], slides=[{"asset_id": "s1", "copy_headline": "כותרת חדשה"}], caption="חדש")
        stored = self.store.items[0]
        self.assertEqual(stored["post"]["slides"][0]["copy_headline"], "כותרת חדשה")
        self.assertEqual(stored["caption"], "חדש")
        with self.assertRaises(sq.QueueError):
            sq.edit_copy(27, item["id"], slides=[{"asset_id": "nope", "copy_headline": "x"}])
        sq.approve(27, [item["id"]])
        with self.assertRaises(sq.QueueError):
            sq.edit_copy(27, item["id"], caption="after approval")

    def test_a_post_item_is_never_publishable(self):
        item = pp.submit(27, _post(), "", "he")["queued"]
        sq.approve(27, [item["id"]])
        with self.assertRaises(sq.GateClosed):
            sq.check(27, "instagram", item_id=item["id"])
        with self.assertRaises(sq.QueueError):
            sq.mark_published(27, item["id"])

    def test_objection_round_one_revises_round_two_escalates(self):
        proof = _post(assets=[{"asset_id": "p1", "role": "proof", "copy_headline": "1000 זוגות",
                               "asset_type": "generated_image",
                               "visual_direction": "a finished custom sneaker on a pedestal"}])
        first = pp.submit(27, proof, "", "he")
        self.assertEqual(first["code"], "objection")
        self.assertEqual(self.store.items, [])                            # round 1: nothing queued
        proof["assets"][0]["response"] = "a hero shot of the style, not a specific order"
        second = pp.submit(27, proof, "", "he")
        slide = second["queued"]["post"]["slides"][0]
        self.assertIn("escalated", slide["flags"])
        self.assertEqual(slide["escalation"]["counterpart"], proof["assets"][0]["response"])
        third = pp.submit(27, proof, "", "he")                            # no third round
        self.assertIn("queued", third)
        self.assertEqual(len(self.drafts["gift-why-1"]["objections"]), 2)

    def test_audience_question_when_unresolved(self):
        out = pp.submit(27, _post(audience="everyone"), "", "he")
        self.assertEqual(out["code"], "audience_question")
        self.assertEqual(self.store.items, [])

    def test_lead_time_warning_on_the_card(self):
        post = _post(occasion="חנוכה", occasion_date="2026-12-31", publish_date="2026-11-27")
        out = pp.submit(27, post, "", "he")
        self.assertEqual(out["queued"]["post"]["warnings"][0]["level"], "late")
        self.assertIn("2026-11-26", out["line"])
        self.assertIn("2026-11-26", sq.view(27, "he")["items"][0]["post"]["warnings"][0])

    def test_approval_starts_production_per_sourcing(self):
        from agents import media_agent
        item = pp.submit(27, _post(), "", "he")["queued"]
        made = []

        def fake(client_id, brief, palette, platform="instagram"):
            made.append((brief.asset_id, palette.get("label")))
            self.assertNotEqual(brief.asset_type, "text_slide")       # text slides go to the design, as a set
            return {"success": True, "kind": "image", "file_id": "FILE_" + brief.asset_id + "_0123456789",
                    "link": "", "credits": 2}

        from core import slide_design
        designed = []
        saved = (media_agent.generate_from_brief, slide_design.open_for_post)
        media_agent.generate_from_brief = fake
        slide_design.open_for_post = lambda cid, post_item, slides, lang="he": designed.append(
            [(s["asset_id"], s["n"], s["total"]) for s in slides]) or {"item": {"id": "design0001"}, "problems": []}
        try:
            done = sq.approve(27, [item["id"]])
            self.assertEqual(pp.start(27, done), 1)
        finally:
            media_agent.generate_from_brief, slide_design.open_for_post = saved
        self.assertEqual(made, [("s2", "black + red")])
        self.assertEqual(designed, [[("s1", 1, 3), ("s3", 3, 3)]])               # ONE design for the set
        post_item = next(i for i in self.store.items if i["id"] == item["id"])
        self.assertEqual((post_item["production"]["made"], post_item["production"]["not_made"]), (1, 0))
        self.assertEqual(post_item["production"]["design"]["item_id"], "design0001")
        asset_item = next(i for i in self.store.items if i.get("post_ref"))
        # + the AI notice, from the asset's manifest: the picture itself is generated (2026-11-26)
        self.assertEqual((asset_item["status"], asset_item["flags"]), ("waiting", ["concept", "ai_content"]))
        self.assertEqual(asset_item["post_ref"]["asset_id"], "s2")


class Wiring(unittest.TestCase):
    def test_action_and_route(self):
        from core import agent_actions, chat_parity
        self.assertIn("produce_post", agent_actions.ACTIONS)
        self.assertEqual(chat_parity.PARITY["POST /api/client/social/{item_id}/copy"][1], "produce_post")
        src = open(os.path.join(ROOT, "core", "api_server.py"), encoding="utf-8").read()
        self.assertIn("post_pipeline.start(client_id, done, background_tasks, report=productions)", src)

    def test_dashboard_shows_copy_as_text(self):
        html = open(os.path.join(ROOT, "dashboard", "client", "index.html"), encoding="utf-8").read()
        self.assertIn("function socialPostBody", html)
        self.assertIn("/copy'", html)
        self.assertIn("soc_post_approve:", html)


if __name__ == "__main__":
    unittest.main()
