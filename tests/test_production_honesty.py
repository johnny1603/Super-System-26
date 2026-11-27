"""Production honesty (2026-11-25) — what client 27's carousel exposed: four
black slides, no colours, no Maya, alerts in the log, and the agent telling
the manager "waiting for your approval, nothing wrong".

What must stay true:
1. ONE character path in the slide flow: agent_characters (reference set →
   pose library), never a presenter / avatar lookup; every character failure
   carries a plain line for the manager.
2. The manager's explicit colours beat the brand palette — carried per
   slide to the renderer, and announced in one line.
3. A failed or partial production is SAID, with what failed and why — never
   the "done" line; and the agents' data carries it with the rule.
4. rerun_post_production re-runs an approved post's production.
5. In the chat: started → the progress card while it runs → how it ended.

Run from the repo root:  python tests/test_production_honesty.py -v
"""
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_post_brief as tpb                  # noqa: E402  (stubs + the pipeline harness)

from core import agent_actions as aa           # noqa: E402
from core import agent_characters as ac        # noqa: E402
from core import post_brief as pb              # noqa: E402
from core import post_pipeline as pp           # noqa: E402
from core import slide_renderer as sr          # noqa: E402
from core import social_queue as sq            # noqa: E402

ROOT = os.path.dirname(HERE)
REAL_TELL = pp._tell
COLOURS = {"requested": "צבעוני, כל שקופית בצבע אחר, לא צבעי המותג",
           "slides": [{"bg": "#FF3B30"}, {"bg": "#FFCC00"}, {"bg": "#34C759"}]}


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class _Patch:
    """setattr for the test's lifetime."""

    def __init__(self, test):
        self.test = test

    def __call__(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.test.addCleanup(setattr, obj, name, old)


class Harness(unittest.TestCase):
    def setUp(self):
        tpb.Pipeline.setUp(self)
        self.patch = _Patch(self)
        pp._tell = REAL_TELL
        self.lines = []
        from agents import client_agent
        self.patch(client_agent, "log_communication", lambda cid, d, channel, text: self.lines.append((channel, text)))
        self.patch(ac, "approval_flags", lambda cid, has, _brief_text=None: ["ai_label"] if has else [])
        self.patch(ac, "on_items_approved", lambda *a, **k: [])
        self.patch(pp, "_lang", lambda cid: "he")
        self.system_runs = []

        def no_card(client_id, action, params, persona="general"):
            self.system_runs.append((action, params, persona))
            return False                                   # storage "down": production runs without a card
        self.patch(aa, "start_system_run", no_card)

    def queue(self, post=None, **kw):
        out = pp.submit(27, post or tpb._post(**kw), "", "he")
        self.assertIn("queued", out, out)
        return out

    def approve(self, item_id, generate=None, design=None):
        from agents import media_agent
        from core import slide_design
        self.patch(media_agent, "generate_from_brief", generate or (
            lambda cid, brief, palette, platform="instagram": {"success": True, "kind": "image",
                                                               "file_id": "FILE_ok_0123456789", "credits": 1}))
        self.designed = []
        def opened(cid, it, slides, lang="he"):
            self.designed.append(slides)
            out = dict(design if design is not None else {"item": {"id": "design0001"}, "problems": [], "character_errors": []})
            if out.get("item") and not out.get("problems"):
                # the real open_for_post returns what each slide's render drew (core/render_manifest)
                out.setdefault("manifests", [{"asset_id": s["asset_id"], "layers": []} for s in slides])
            return out
        self.patch(slide_design, "open_for_post", opened)
        pp.start(27, sq.approve(27, [item_id]))
        return sq.get(27, item_id)["production"]


# ─── 1. One character path ───────────────────────────────────────────────────

class OneCharacterPath(unittest.TestCase):
    def test_the_slide_flow_resolves_characters_only_through_agent_characters(self):
        for parts in (("core", "slide_design.py"), ("core", "slide_renderer.py"), ("core", "post_pipeline.py"),
                      ("core", "agent_characters.py")):
            src = _src(*parts)
            for word in ("ready_avatars", "list_ready_avatars", "avatar_agent", "avatar_video", "presenter",
                         "heygen"):
                self.assertNotIn(word, src.lower(), f"{parts[-1]} mentions {word}")
        design = _src("core", "slide_design.py")
        attach = design[design.index("def attach_characters"):design.index("def character_flags")]
        # one path: the post's poses at once (2026-11-25) — the library first, then make_pose
        self.assertIn("agent_characters.poses_for(", attach)
        self.assertNotIn("pose_for(agent", attach)
        chars = _src("core", "agent_characters.py")
        batch = chars[chars.index("def poses_for"):chars.index("class StopRequested")]
        self.assertIn("find_poses(", batch)
        self.assertIn("make_pose(", batch)
        # the persona is told an agent in a post is never a presenter / avatar
        from agents import support_agent
        self.assertIn("never the\nclient's presenter / avatar", support_agent.CONTENT_CONTEXT_RULE)

    def test_every_character_failure_carries_a_plain_line(self):
        from core import slide_design
        cases = [(ac.CharacterUnavailable("maya", "not_configured"), "חשבון יצירת הדמויות של הצוות לא מחובר"),
                 (ac.MissingReference("maya"), "לא מוגדרת תמונת ייחוס עבור מאיה (maya)"),
                 (ac.BackgroundNotRemoved("no transparent pixels"), "הסרת הרקע מהדמות נכשלה"),
                 (RuntimeError("higgsfield 500"), "ספק היצירה החזיר שגיאה")]
        saved = (ac.make_pose, ac.find_poses)
        ac.find_poses = lambda *a, **k: []                                 # nothing in the library: make one
        try:
            for error, words in cases:
                def boom(agent, pose, *a, _e=error, **k):
                    raise _e
                ac.make_pose = boom
                entries, failures = slide_design.attach_characters(
                    [{"asset_id": "s1", "role": "hook", "characters": ["maya"], "character_mode": "composite"}])
                self.assertNotIn("character", entries[0])                  # drawn without her …
                self.assertIn(words, failures[0]["message"])               # … and SAID why
                if not isinstance(error, ac.MissingReference):
                    self.assertTrue(failures[0]["message"].startswith("הדמות של מאיה לא נוצרה"))
        finally:
            ac.make_pose, ac.find_poses = saved


# ─── 2. The manager's explicit colours win ───────────────────────────────────

class ExplicitColours(Harness):
    def test_clean_colors(self):
        self.assertEqual(pb.clean_colors(None, 3), {})
        one = pb.clean_colors({"requested": "רקע אדום", "slides": [{"bg": "#ff0000"}]}, 3)
        self.assertEqual(one["slides"], [{"bg": "#FF0000"}] * 3)              # one entry = every slide
        self.assertEqual(len(pb.clean_colors(COLOURS, 3)["slides"]), 3)
        free = pb.clean_colors({"slides": [{"bg": "#FF0000"}]}, 3)             # the agent's own choice: no reason asked
        self.assertEqual((free["by"], free["requested"], len(free["slides"])), ("agent", "", 3))
        for bad in ({"requested": "צבעוני", "slides": [{"bg": "red"}]},
                    {"requested": "צבעוני", "slides": [{"bg": "#FF0000"}, {"bg": "#00FF00"}]}):
            with self.assertRaises(ValueError):
                pb.clean_colors(bad, 3)

    def test_carried_to_every_slide_and_announced(self):
        out = self.queue(colors=COLOURS)
        self.assertIn("לבקשתך, הפוסט הזה בצבעים שביקשת", out["line"])
        self.assertIn(COLOURS["requested"], out["line"])
        item = out["queued"]
        self.assertEqual(item["post"]["colors"]["slides"][2], {"bg": "#34C759"})
        self.approve(item["id"])
        s1, s3 = self.designed[0]                                             # the two text slides
        self.assertEqual((s1["n"], s1["colors"]), (1, {"bg": "#FF3B30"}))
        self.assertEqual((s3["n"], s3["colors"]), (3, {"bg": "#34C759"}))

    def test_without_an_instruction_the_brand_palette_stays(self):
        item = self.queue()["queued"]
        self.assertNotIn("colors", item["post"])
        self.approve(item["id"])
        self.assertTrue(all("colors" not in s for s in self.designed[0]))

    def test_the_renderer_takes_each_slides_colours_readably(self):
        roles = {"bg": "#101010", "fg": "#FAFAFA", "accent": "#FF4C1F", "muted": "#888888"}
        self.assertIs(sr.slide_roles(roles, None), roles)
        yellow = sr.slide_roles(roles, {"bg": "#FFCC00"})
        self.assertEqual(yellow["bg"], "#FFCC00")
        self.assertEqual(yellow["fg"], "#111111")                             # dark text on yellow
        self.assertEqual(sr.slide_roles(roles, {"bg": "#0A2A8A"})["fg"], "#FFFFFF")
        self.assertEqual(sr.slide_roles(roles, {"bg": "#FF4C1F"})["accent"], sr.slide_roles(roles, {"bg": "#FF4C1F"})["fg"])
        self.assertEqual(sr.slide_roles(roles, {"bg": "#FFFFFF", "fg": "#222222"})["fg"], "#222222")
        src = _src("core", "slide_renderer.py")
        self.assertIn('slide_roles(roles, s.get("colors"))', src)             # every slide of a set
        self.assertIn('"colors"', _src("core", "slide_design.py"))            # kept on the design's slides


# ─── 3. A failed production is said, never "done" ────────────────────────────

class FailureIsSaid(Harness):
    def test_maya_not_drawn_FAILS_by_name(self):
        # 2026-11-26 (knowledge/client-governance.md §8): declared in the brief and
        # absent from the render = FAILED — never a "partial" that reads like completion
        msg = ac.failure_text("maya", ac.CharacterUnavailable("maya", "not_configured"))
        line = f"שקופית 1: הדמות של מאיה הוגדרה לפוסט ולא צוירה. {msg}"
        item = self.queue()["queued"]
        production = self.approve(item["id"], design={
            "item": {"id": "design0001"}, "problems": [], "character_errors": [msg, msg],
            "missing": [{"type": "character", "id": "maya", "slide": 1, "asset_id": "s1", "reason": "not_configured",
                         "line": line}]})
        self.assertEqual(production["state"], "failed")
        self.assertEqual(production["problems"], [f"• {line}"])
        self.assertEqual([m["id"] for m in production["missing"]], ["maya"])
        self.assertFalse(production["design"]["ok"])
        last = self.lines[-1][1]
        self.assertIn(msg, last)
        self.assertIn("אי אפשר לאשר אותו כמו שהוא", last)
        self.assertNotIn("הסתיימה", last)                                    # never the "produced" line
        self.assertNotIn("לא הצליחה במלואה", last)                           # … nor the "partly worked" one

    def test_everything_failed_says_what_and_why(self):
        item = self.queue()["queued"]
        production = self.approve(
            item["id"], generate=lambda cid, brief, palette, platform="instagram": {
                "success": False, "status": "no_account", "errors": ["no key"]},
            design={"item": {}, "problems": [{"code": "other"}], "error": "boom"})
        self.assertEqual(production["state"], "failed")
        last = self.lines[-1][1]
        self.assertIn("נכשלה", last)
        self.assertIn("שקופית 2: חשבון יצירת התמונות של העסק לא מחובר", last)
        self.assertIn("עיצוב שקופיות הטקסט נכשל", last)
        self.assertNotIn("מחכים לאישור הפרסום", last)

    def test_a_clean_run_still_says_done(self):
        item = self.queue()["queued"]
        production = self.approve(item["id"])
        self.assertEqual((production["state"], production["problems"]), ("done", []))
        self.assertIn("הסתיימה", self.lines[-1][1])

    def test_the_agents_data_carries_it_with_the_rule(self):
        item = self.queue()["queued"]
        self.approve(item["id"], design={"item": {"id": "design0001"}, "problems": [],
                                         "character_errors": ["הדמות של מאיה לא נוצרה: X"]})
        block = sq.prompt_block(27)
        row = block["post_production"][0]
        self.assertEqual((row["item_id"], row["state"]), (item["id"], "partial"))
        self.assertIn("• הדמות של מאיה לא נוצרה: X", row["problems"])
        self.assertIn("never answer", block["post_production_rule"])
        from agents import support_agent
        self.assertIn("PRODUCTION FAILED = SAY SO", support_agent.CONTENT_CONTEXT_RULE)
        self.assertIn("rerun_post_production", support_agent.CONTENT_CONTEXT_RULE)
        # nothing failed → nothing in the block
        self.store.items = [i for i in self.store.items if i["id"] != item["id"]]
        self.assertNotIn("post_production", sq.prompt_block(27))


# ─── 4. The retry action ─────────────────────────────────────────────────────

class Retry(Harness):
    def test_the_action_exists_and_is_offered_for_a_failed_post(self):
        spec = aa.ACTIONS["rerun_post_production"]
        self.assertTrue(spec["client_requestable"])
        self.assertEqual(spec["approver"], "client")
        self.assertTrue(spec["slow"])
        self.assertIn("rerun_post_production", aa.STOP_DISCARD)       # stops between steps (test_production_speed)
        waiting = self.queue()["queued"]
        with self.assertRaises(ValueError):
            aa._v_approved_post(waiting["id"], 27)                         # copy not approved yet
        self.assertFalse(aa._rerun_possible({}, {"id": 27}))
        self.approve(waiting["id"], generate=lambda *a, **k: {"success": False, "status": "failed", "errors": ["x"]})
        self.assertEqual(aa._v_approved_post(waiting["id"], 27), waiting["id"])
        self.assertTrue(aa._rerun_possible({}, {"id": 27}))

    def test_it_runs_the_production_again(self):
        item = self.queue()["queued"]
        self.approve(item["id"], generate=lambda *a, **k: {"success": False, "status": "failed", "errors": ["x"]})
        self.assertEqual(sq.get(27, item["id"])["production"]["state"], "partial")
        calls = []
        from agents import media_agent
        self.patch(media_agent, "generate_from_brief", lambda cid, brief, palette, platform="instagram": calls.append(
            brief.asset_id) or {"success": True, "kind": "image", "file_id": "FILE_ok_0123456789", "credits": 1})
        result = aa.ACTIONS["rerun_post_production"]["run"](27, {"item_id": item["id"]}, None)
        self.assertEqual(calls, ["s2"])
        self.assertEqual(result["state"], "done")
        self.assertEqual(sq.get(27, item["id"])["production"]["state"], "done")
        self.assertIn("הסתיימה", aa.ACTIONS["rerun_post_production"]["done_message"](result, "he"))

    def test_a_running_production_is_not_doubled(self):
        item = self.queue()["queued"]
        sq.approve(27, [item["id"]])
        sq.set_production(27, item["id"], {"state": "running", "started_at": pp._now()})
        with self.assertRaises(ValueError):
            aa._v_approved_post(item["id"], 27)


# ─── 5. Started → progress card → finished, in the chat ──────────────────────

class _Rows:
    def __init__(self):
        self.rows = []

    def table(self, name):
        db = self

        class Q:
            def insert(self, row):
                self.row = {**row, "id": len(db.rows) + 1, "created_at": row.get("decided_at")}
                return self

            def execute(self):
                db.rows.append(self.row)
                return types.SimpleNamespace(data=[dict(self.row)])
        return Q()


class InTheChat(Harness):
    def test_started_then_finished_in_the_producers_chat(self):
        item = self.queue()["queued"]
        self.approve(item["id"])
        texts = [t for _, t in self.lines]
        self.assertIn("התחלתי להפיק את הפוסט", texts[0])
        self.assertIn("(3 חלקים)", texts[0])
        self.assertIn("הסתיימה", texts[-1])
        self.assertEqual({c for c, _ in self.lines}, {f"dashboard_chat:{item.get('producer') or 'media'}"})
        # the run was asked for as the progress card first
        self.assertEqual(self.system_runs, [("rerun_post_production", {"item_id": item["id"]},
                                             item.get("producer") or "media")])

    def test_the_system_run_is_a_running_card_with_progress(self):
        rows, ran = _Rows(), []
        self.patch(aa, "_db", lambda: rows)
        self.patch(aa, "_execute_in_background", lambda row, spec, params, actor, client, lang: self.fail("ran here"))
        self.patch(aa, "run_in_own_request", lambda row, actor: ran.append(row) or True)   # its own request
        self.patch(aa, "_client", lambda cid: {"id": cid})
        self.patch(aa, "_lang", lambda cid: "he")
        self.patch(aa, "_log", lambda *a, **k: None)
        real = type(self).real_start
        self.assertTrue(real(27, "rerun_post_production", {"item_id": "abc123"}, persona="meta"))
        row = rows.rows[0]
        self.assertEqual((row["status"], row["decided_by"], row["origin"], row["persona"]),
                         ("running", "system", "client_chat", "meta"))
        self.assertEqual(ran[0]["id"], row["id"])                           # and it is handed to a request now
        self.patch(sq, "get", lambda cid, iid: {"title": "למה עיצוב אישי"})
        shown = aa.card(row, "client", "he")
        self.assertTrue(shown["progress"] and shown["progress"]["text"])    # the existing progress card
        self.assertIn("למה עיצוב אישי", shown["what"])


InTheChat.real_start = staticmethod(aa.start_system_run)


if __name__ == "__main__":
    unittest.main()
