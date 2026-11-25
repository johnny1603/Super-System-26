"""Agent-character poses on request, from the chat (2026-11-25).

Asked in the chat for "three poses of Maya for comparison", the media agent
answered that he couldn't (capability_gap alerts 234 and 235), although the
admin's test-poses button does exactly that.

What must stay true:
- the action exists, the media agent proposes it in the chat, and it runs on
  the ask (no approve step);
- it reuses the admin button's run (agent_characters._begin + run_test: the
  same pose prompt, reference set, model and aspect), never a second path;
- the results reach the chat (the card's images + a line, each with its
  credits) and wait as pending candidates; keep / discard is the same
  save_candidate / discard_candidate as the characters page;
- the per-request cap holds; the cost shows before (measured) and after;
- the social agent still cannot generate media;
- the first run that works closes alerts 234 and 235.

Run from the repo root:  python tests/test_character_poses_chat.py -v
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_agent_characters as tac            # noqa: E402  (stubs + the in-memory app_settings)
import test_agent_actions as taa               # noqa: E402  (the in-memory agent_actions table)

from core import agent_actions as aa           # noqa: E402
from core import agent_characters as ac        # noqa: E402
from core import media_gen_service as gen      # noqa: E402

ROOT = os.path.dirname(HERE)
NAME = "generate_character_poses"
POSES = ["standing, waving hello", "sitting at a desk, typing", "pointing up, explaining"]
CLIENTS = {27: {"id": 27, "billing_exempt": True, "language": "he"},
           40: {"id": 40, "billing_exempt": False, "language": "he"}}


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Poses(tac.Store):
    def setUp(self):
        super().setUp()
        self.set_ref("maya")
        self.old_key = os.environ.get(ac.KEY_ENV)
        os.environ[ac.KEY_ENV] = "kid:ksecret"
        self.adb = taa.FakeDB()
        self.posted, self.handed, self.sent, self.action_alerts = [], [], [], []
        self.saved_aa = {n: getattr(aa, n) for n in ("_db", "_client", "_lang", "_post_client_message", "_alert",
                                                     "_log", "run_in_own_request", "_forget_state")}
        aa._db = lambda: self.adb
        aa._client = lambda cid: CLIENTS.get(cid, {})
        aa._lang = lambda cid: "he"
        aa._post_client_message = lambda cid, persona, text: self.posted.append((cid, persona, text))
        aa._alert = lambda issue: self.action_alerts.append(issue)
        aa._log = lambda *a, **k: None
        aa._forget_state = lambda cid: None
        aa.run_in_own_request = lambda row, actor: self.handed.append((row["id"], actor)) or True
        self.saved_gen = gen.generate_with_references
        self.fail_gen = False

        def fake_gen(key, prompt, refs, aspect, **kw):
            if self.fail_gen:
                raise RuntimeError("vendor 500")
            self.sent.append((prompt, refs, aspect))
            return {"url": f"https://cdn.example/pose-{len(self.sent)}.png", "credits": 2.5,
                    "model": "xai/grok-imagine-image-2.0", "aspect_ratio": aspect, "request_id": f"req-{len(self.sent)}"}
        gen.generate_with_references = fake_gen

    def tearDown(self):
        gen.generate_with_references = self.saved_gen
        for n, v in self.saved_aa.items():
            setattr(aa, n, v)
        if self.old_key is None:
            os.environ.pop(ac.KEY_ENV, None)
        else:
            os.environ[ac.KEY_ENV] = self.old_key
        super().tearDown()

    # helpers
    def ask(self, poses=None, persona="media", client_id=27, agent="maya"):
        return aa.propose(client_id, "client_chat", persona,
                          {"name": NAME, "params": {"agent": agent, "poses": poses or POSES}}, state={},
                          client=CLIENTS[client_id], context={"language": "he"})

    def rows(self):
        return self.adb.tables.get(aa.TABLE, [])

    def run_row(self, row=None):
        row = row or self.rows()[-1]
        return aa._execute(row, aa.ACTIONS[NAME], row["params"], "client", CLIENTS[27], "he", None)


# ─── The action exists, runs from the chat, on the ask ───────────────────────

class FromTheChat(Poses):
    def test_the_action_exists_for_the_media_agent(self):
        spec = aa.ACTIONS[NAME]
        self.assertEqual(spec["personas"], ("media",))
        self.assertTrue(spec["runs_on_ask"])
        self.assertTrue(spec["internal_only"])            # uallak's own key + the team's shared library
        self.assertIn(NAME, aa.STOP_DISCARD)
        self.assertIn(NAME, aa.DRIVE_IN_REQUEST)          # one pass in a request of its own
        names = [a["name"] for a in aa.available(27, "client_chat", {}, client=CLIENTS[27], persona="media")]
        self.assertIn(NAME, names)

    def test_it_runs_on_the_ask_no_approve_step(self):
        out = self.ask()
        self.assertEqual(out["status"], "running")
        card = out["card"]
        self.assertFalse(card["can_decide"])                                     # nothing to approve
        self.assertTrue(card["progress"])                                        # a progress card at once
        row = self.rows()[0]
        self.assertEqual((row["status"], row["decided_by"], row["persona"]), ("running", "client", "media"))
        self.assertEqual(self.handed, [(row["id"], "client")])                  # started now, in its own request
        self.assertTrue(ac.REQUEST_RE.match(row["params"]["request"]))
        self.assertEqual(row["params"]["poses"], POSES)
        # the run itself ends the row
        out = self.run_row()
        self.assertEqual(out["status"], "done")
        self.assertEqual(self.rows()[0]["status"], "done")

    def test_a_missing_reference_or_a_run_in_progress_is_said_plainly(self):
        out = self.ask(agent="lior")
        self.assertIn("לא מוגדרת תמונת ייחוס", out["refuse"])
        ac.start_test("maya")                                                    # the button is running
        out = self.ask()
        self.assertIn("כבר נוצרות", out["refuse"])
        self.assertEqual(self.rows(), [])


# ─── The admin button's path, not a second one ───────────────────────────────

class SamePath(Poses):
    def test_the_same_prompt_references_model_and_aspect_as_the_button(self):
        self.ask()
        self.run_row()
        self.assertEqual(len(self.sent), 3)
        for (prompt, refs, aspect), pose in zip(self.sent, POSES):
            self.assertEqual(prompt, ac.pose_prompt("maya", pose, framing="full body"))
            self.assertIn(ac.STYLE_CLAUSE, prompt)
            self.assertEqual(refs, ac.reference_urls(["maya"]))
            self.assertEqual(aspect, ac.POSE_ASPECT)
        # and the button sends exactly the same thing for the same pose
        button = []
        ac.start_test("maya", POSES[0], 1)
        ac.run_test("maya", [POSES[0]], _gen=lambda key, prompt, refs, aspect: button.append((prompt, refs, aspect))
                    or {"url": "https://cdn.example/b.png", "credits": 1})
        self.assertEqual(button[0], self.sent[0])

    def test_one_run_function_in_the_code(self):
        src = _src("core", "agent_characters.py")
        self.assertIn("return _begin(agent, poses, by)", src[src.index("def start_test"):])
        begin = src[src.index("def begin_request"):src.index("def request_view")]
        self.assertIn("_begin(agent, poses,", begin)
        run = _src("core", "agent_actions.py")
        run = run[run.index("def _run_character_poses"):run.index("def _poses_done_message")]
        self.assertIn("ac.run_test(", run)
        self.assertNotIn("generate_with_references", run)                        # never its own generation
        self.assertNotIn("pose_prompt", run)


# ─── Results: in the chat + pending in the library; keep / discard ───────────

class Results(Poses):
    def test_results_reach_the_chat_with_credits_and_wait_as_pending(self):
        self.ask()
        self.run_row()
        row = self.rows()[0]
        # the chat line: each pose with its credits, the total, a link per image
        (_cid, persona, text), = self.posted
        self.assertEqual(persona, "media")
        for pose in POSES:
            self.assertIn(f"{pose} — 2.5 קרדיטים", text)
        self.assertIn("סה״כ 7.5 קרדיטים", text)
        self.assertEqual(len(re.findall(r"https://\S+/api/client/characters/poses/q[a-f0-9]{10}/c[a-f0-9]{10}", text)), 3)
        self.assertNotIn("cdn.example", text)                                    # never the vendor's URL
        # the card: the images by OUR path, each pending, with its credits
        details = aa.card(row, "client", "he")["details"]
        self.assertEqual([p["state"] for p in details["poses"]], ["pending"] * 3)
        self.assertEqual([p["credits"] for p in details["poses"]], [2.5] * 3)
        self.assertTrue(all(re.match(r"^/api/client/characters/poses/q[a-f0-9]{10}/c[a-f0-9]{10}$", p["image_url"])
                            for p in details["poses"]))
        self.assertEqual(details["pose_credits"], 7.5)
        # pending on the characters page, exactly like the button's results
        maya = tac._row(ac.view(), "maya")
        self.assertEqual(len(maya["candidates"]), 3)
        self.assertEqual(maya["pose_counts"]["total"], 0)                        # nothing kept by itself

    def test_keep_and_discard_are_the_characters_page_decisions(self):
        self.ask()
        self.run_row()
        req = self.rows()[0]["params"]["request"]
        first, second, third = [p["cand_id"] for p in ac.request_view(req)["poses"]]
        view = ac.request_decide(27, req, first, "keep", _remove=lambda key, url: {"content": tac._figure()},
                                 _upload=lambda agent, png: {"id": "POSEFILE_0123456789"})
        self.assertEqual(view["poses"][0]["state"], "kept")
        maya = tac._row(ac.view(), "maya")
        self.assertEqual(maya["pose_counts"]["approved"], 1)                     # into the library, approved
        ac.request_decide(27, req, second, "discard")
        ac.discard_candidate("maya", third)                                      # on the characters page
        states = [p["state"] for p in ac.request_view(req)["poses"]]
        self.assertEqual(states, ["kept", "discarded", "discarded"])             # the card follows both screens
        self.assertEqual(tac._row(ac.view(), "maya")["candidates"], [])
        with self.assertRaises(ValueError):
            ac.request_decide(27, req, second, "keep")                          # decided once

    def test_another_account_never_sees_or_decides_them(self):
        self.ask()
        self.run_row()
        req = self.rows()[0]["params"]["request"]
        cand = ac.request_view(req)["poses"][0]["cand_id"]
        self.assertTrue(ac.request_image_url(27, req, cand).startswith("https://cdn.example/"))
        with self.assertRaises(ValueError):
            ac.request_image_url(40, req, cand)
        with self.assertRaises(ValueError):
            ac.request_decide(40, req, cand, "discard")
        api = _src("core", "api_server.py")
        for route in ('@app.get("/api/client/characters/poses/{request_id}/{cand_id}")',
                      '@app.post("/api/client/characters/poses/{request_id}/{cand_id}/{verb}")'):
            block = api[api.index(route):][:600]
            self.assertIn("_require_session(request)", block)
            self.assertIn("_pose_request_internal(client_id)", block)
        self.assertIn('get("billing_exempt") is not True', api)

    def test_the_dashboard_draws_them(self):
        html = _src("dashboard", "client", "index.html")
        self.assertIn("box.appendChild(poseResults(d))", html)
        self.assertIn("refreshActionDetails(el, c.details);", html)
        self.assertIn("/api/client/characters/poses/${requestId}/${candId}/${verb}", html)
        self.assertIn(r"POSE_IMG_RE = /^\/api\/client\/characters\/poses\/q[a-f0-9]{10}\/c[a-f0-9]{10}$/", html)

    def test_a_stop_ends_between_poses_and_keeps_what_was_made(self):
        self.ask()
        req = self.rows()[0]["params"]["request"]
        ac.begin_request("maya", POSES, req, 27)
        calls = iter([False, True, True])
        job = ac.run_test("maya", POSES, request=req, stopped=lambda: next(calls))
        self.assertEqual((job["state"], job["made"]), ("stopped", 1))
        self.assertEqual(len(self.sent), 1)                                      # nothing new after the stop
        self.assertEqual([p["state"] for p in ac.request_view(req)["poses"]], ["pending", "waiting", "waiting"])


# ─── The cap; the cost before and after ──────────────────────────────────────

class CapAndCost(Poses):
    def test_the_per_request_cap_holds(self):
        many = [f"pose number {i}" for i in range(40)]
        out = self.ask(poses=many)
        self.assertIn("עד 4", out["clarify"])
        self.assertIn("40", out["clarify"])
        self.assertEqual(self.rows(), [])                                        # nothing stored, nothing made
        self.assertEqual(self.sent, [])
        with self.assertRaises(ValueError):
            aa._validate_poses_stored(27, {"agent": "maya", "poses": many[:5], "request": "q0123456789"})
        with self.assertRaises(ValueError):
            ac.begin_request("maya", many[:5], "q0123456789", 27)
        self.assertEqual(ac.MAX_REQUEST_POSES, 4)

    def test_the_cost_before_is_measured_and_after_is_real(self):
        out = self.ask()
        self.assertIn("העלות עוד לא נמדדה", out["card"]["what"])               # nothing measured yet: said so
        self.run_row()
        self.assertEqual(ac.estimate_credits()["each"], 2.5)
        out = self.ask(poses=POSES[:2])
        self.assertIn("כ-5 קרדיטים (2.5 לתנוחה", out["card"]["what"])          # before: the measured total
        self.assertEqual(out["card"]["details"]["pose_estimate"], 2.5)
        self.run_row()
        details = aa.card(self.rows()[-1], "client", "he")["details"]
        self.assertEqual(details["pose_credits"], 5.0)                           # after: what it really cost


# ─── The social agent still does not generate media ─────────────────────────

class SocialSplit(Poses):
    def test_the_social_agent_cannot_propose_it(self):
        for persona in ("meta", "general"):
            self.assertEqual(self.ask(persona=persona), {"error": "not_allowed_here"}, persona)
            names = [a["name"] for a in aa.available(27, "client_chat", {}, client=CLIENTS[27], persona=persona)]
            self.assertNotIn(NAME, names, persona)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.sent, [])

    def test_the_rule_says_whose_job_it_is(self):
        from agents import support_agent
        rule = support_agent.CONTENT_CONTEXT_RULE
        self.assertIn("POSES ON REQUEST", rule)
        self.assertIn("The SOCIAL agent (מאיה) never generates", rule)
        self.assertIn("generate_character_poses", rule)

    def test_a_paying_account_does_not_get_it(self):
        names = [a["name"] for a in aa.available(40, "client_chat", {}, client=CLIENTS[40], persona="media")]
        self.assertNotIn(NAME, names)
        self.assertEqual(self.ask(client_id=40), {"error": "internal_only"})


# ─── Alerts 234 and 235 ──────────────────────────────────────────────────────

class GapAlerts(Poses):
    def seed_alerts(self):
        self.adb.tables["alerts"] = [{"id": 234, "source": "capability_gap", "status": "open"},
                                     {"id": 235, "source": "capability_gap", "status": "open"},
                                     {"id": 236, "source": "capability_gap", "status": "open"}]

    def test_the_first_run_that_works_closes_them(self):
        self.seed_alerts()
        self.ask()
        self.run_row()
        status = {a["id"]: a["status"] for a in self.adb.tables["alerts"]}
        self.assertEqual(status, {234: "resolved", 235: "resolved", 236: "open"})

    def test_a_run_that_made_nothing_leaves_them_open(self):
        self.seed_alerts()
        self.fail_gen = True
        self.ask()
        out = self.run_row()
        self.assertEqual((out["status"], out["code"]), ("failed", "not_made"))
        self.assertEqual({a["status"] for a in self.adb.tables["alerts"]}, {"open"})


if __name__ == "__main__":
    unittest.main()
