"""Character poses: in parallel, and the images kept (2026-11-25).

1. A pose run (Lior's chat action and the characters page's test button —
   both agent_characters.run_test) submits its poses together, POSE_PARALLEL
   (4) at a time, and waits for them as a set — the same cap and budget rule
   as a post's production (post_pipeline._generate_all). Three poses take
   about as long as one.
2. Each generated pose is copied to our Drive the moment it is made and is
   served from there: the provider's link lasts about seven days, so an old
   chat message used to lose its image.

Run from the repo root:  python tests/test_pose_parallel_keep.py -v
"""
import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_agent_characters as tac            # noqa: E402  (stubs + the in-memory app_settings)

from core import agent_characters as ac        # noqa: E402
from core import post_pipeline as pp           # noqa: E402

ROOT = os.path.dirname(HERE)
POSES = ["standing, waving hello", "sitting at a desk, typing", "pointing up, explaining"]


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(tac.Store):
    def setUp(self):
        super().setUp()
        self.set_ref("maya")
        self.old_key = os.environ.get(ac.KEY_ENV)
        os.environ[ac.KEY_ENV] = "kid:ksecret"
        self.lock = threading.Lock()
        self.active = self.peak = self.calls = 0

    def tearDown(self):
        if self.old_key is None:
            os.environ.pop(ac.KEY_ENV, None)
        else:
            os.environ[ac.KEY_ENV] = self.old_key
        super().tearDown()

    def gen(self, seconds=0.0, credits=2.5, barrier=None):
        def fake(key, prompt, refs, aspect, **kw):
            with self.lock:
                self.calls += 1
                n = self.calls
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                if barrier is not None:
                    barrier.wait()                  # every pose must be at the vendor AT ONCE
                time.sleep(seconds)
                return {"url": f"https://cdn.example/pose-{n}.png", "credits": credits, "aspect_ratio": aspect,
                        "model": "m", "request_id": f"req-{n}"}
            finally:
                with self.lock:
                    self.active -= 1
        return fake


# ─── 1. In parallel, with production's cap and budget rule ──────────────────

class Parallel(Base):
    def test_three_poses_are_at_the_vendor_together(self):
        barrier = threading.Barrier(3, timeout=5)    # one after another would break it
        job = ac.run_test("maya", POSES, _gen=self.gen(barrier=barrier), budget=0)
        self.assertEqual((job["state"], job["made"], job["errors"]), ("done", 3, []))
        self.assertEqual(self.peak, 3)

    def test_three_poses_take_about_as_long_as_one(self):
        started = time.monotonic()
        ac.run_test("maya", POSES, _gen=self.gen(seconds=0.4), budget=0)
        self.assertLess(time.monotonic() - started, 0.4 * 2)          # sequential would be 1.2 s

    def test_the_same_cap_as_production(self):
        self.assertEqual(ac.POSE_PARALLEL, pp.GEN_PARALLEL)
        job = ac.run_test("maya", POSES * 3, _gen=self.gen(seconds=0.1), budget=0)  # nine poses
        self.assertEqual(job["made"], 9)
        self.assertEqual(self.peak, ac.POSE_PARALLEL)                 # never more than four at once
        self.assertEqual(job["parallel"], ac.POSE_PARALLEL)

    def test_the_budget_rule_of_production(self):
        # spent + what the poses still running reserved must be under the budget
        # BEFORE a pose is submitted; one at a time this is exact
        saved = ac.POSE_PARALLEL
        ac.POSE_PARALLEL = 1
        try:
            job = ac.run_test("maya", POSES * 2, _gen=self.gen(credits=2.5), budget=6)   # 1 reserved each
        finally:
            ac.POSE_PARALLEL = saved
        self.assertEqual(job["made"], 3)                              # 0 → 2.5 → 5.0 → (7.5 ≥ 6) stop
        self.assertEqual(job["credits"], 7.5)
        self.assertEqual(self.calls, 3)                               # the rest were never submitted
        self.assertEqual(sum("credit budget" in e for e in job["errors"]), 3)
        # in parallel the first wave can go over by what it spends beyond its share — like production
        job = ac.run_test("maya", POSES, _gen=self.gen(seconds=0.05, credits=2.5), budget=3)
        self.assertEqual(job["made"], 3)

    def test_no_budget_given_uses_the_measured_average(self):
        ac.run_test("maya", POSES, _gen=self.gen(credits=2.0), budget=0)       # measured: 2.0 a pose
        self.assertEqual(ac._default_budget(3), 6.0)
        job = ac.run_test("maya", POSES, _gen=self.gen(credits=2.0))
        self.assertEqual(job["budget"], 6.0)

    def test_the_chat_action_budgets_the_cards_estimate(self):
        src = _src("core", "agent_actions.py")
        run = src[src.index("def _run_character_poses"):src.index("def _poses_done_message")]
        self.assertIn('budget=float(params.get("estimate") or 0) * len(params["poses"])', run)
        api = _src("core", "api_server.py")
        self.assertIn("background_tasks.add_task(agent_characters.run_test", api)   # the button: same run_test

    def test_a_stop_submits_nothing_more(self):
        job = ac.run_test("maya", POSES, _gen=self.gen(), stopped=lambda: True, budget=0)
        self.assertEqual((job["state"], job["made"], self.calls), ("stopped", 0, 0))

    def test_each_result_lands_in_its_own_slot_of_the_request(self):
        rid = ac.new_request_id()
        ac.begin_request("maya", POSES, rid, 27)
        ac.run_test("maya", POSES, _gen=self.gen(seconds=0.05), request=rid, budget=0)
        view = ac.request_view(rid)
        self.assertEqual([p["pose"] for p in view["poses"]], POSES)
        self.assertEqual([p["state"] for p in view["poses"]], ["pending"] * 3)
        self.assertEqual(len({p["cand_id"] for p in view["poses"]}), 3)
        self.assertEqual((view["state"], view["made"], view["credits"]), ("done", 3, 7.5))


# ─── 2. The images are kept ──────────────────────────────────────────────────

class Kept(Base):
    def run_request(self):
        rid = ac.new_request_id()
        ac.begin_request("maya", POSES[:1], rid, 27)
        ac.run_test("maya", POSES[:1], _gen=self.gen(), request=rid, budget=0)
        return rid, ac.request_view(rid)["poses"][0]["cand_id"]

    def test_each_pose_is_copied_to_our_drive_when_it_is_made(self):
        rid, cand_id = self.run_request()
        cand = ac._candidate("maya", cand_id, ac._candidates())
        self.assertTrue(cand["file_id"].startswith("KEEP_maya_"))
        self.assertIn("candidate-" + cand_id, cand["file_id"])
        self.assertEqual(self.kept[cand["file_id"]], tac._png())
        result = ac._candidates()["requests"][rid]["results"][0]
        self.assertEqual(result["file_id"], cand["file_id"])

    def test_the_image_still_shows_after_the_provider_link_expired(self):
        rid, cand_id = self.run_request()

        def expired(url):
            raise ValueError("the URL answered 403")              # ~7 days later
        ac._fetch = expired
        src = ac.request_image(27, rid, cand_id)                     # the chat message's image
        self.assertNotIn("url", src)
        self.assertEqual(ac.kept_image(src["file_id"]), tac._png())
        src = ac.candidate_image("maya", cand_id)                    # the characters page's image
        self.assertEqual(ac.kept_image(src["file_id"]), tac._png())
        with self.assertRaises(ValueError):
            ac.request_image(40, rid, cand_id)                       # another account: not theirs

    def test_the_routes_serve_our_copy_not_the_providers_link(self):
        api = _src("core", "api_server.py")
        route = api[api.index('@app.get("/api/client/characters/poses/{request_id}/{cand_id}")'):
                    api.index('@app.post("/api/client/characters/poses/{request_id}/{cand_id}/{verb}")')]
        self.assertIn("agent_characters.request_image(client_id, request_id, cand_id)", route)
        self.assertIn("_serve_pose_image(src)", route)
        helper = api[api.index("def _serve_pose_image"):api.index("def admin_characters_candidate_image")]
        self.assertLess(helper.index("kept_image"), helper.index("RedirectResponse"))   # the link only without a copy
        page = _src("dashboard", "admin", "characters", "index.html")
        self.assertIn("/api/admin/characters/candidate/${encodeURIComponent(a.agent)}", page)
        self.assertNotIn('<img src="${esc(c.url)}"', page)

    def test_a_failed_copy_falls_back_to_the_link_and_says_so(self):
        def no_drive(*a, **k):
            raise RuntimeError("Missing key: DRIVE_MEDIA_FOLDER_ID")
        ac._keep = no_drive
        rid, cand_id = self.run_request()
        self.assertEqual(ac.request_image(27, rid, cand_id), {"url": "https://cdn.example/pose-1.png"})
        self.assertTrue(any("no durable copy" in a and "seven days" in a for a in self.alerts), self.alerts)

    def test_keep_and_promote_read_the_kept_copy(self):
        src = _src("core", "agent_characters.py")
        save = src[src.index("def save_candidate"):src.index("def discard_candidate")]
        self.assertIn("_candidate_bytes(cand)", save)
        promote = src[src.index("def promote_candidate"):src.index("def remove_pose")]
        self.assertIn("content=_candidate_bytes(cand)", promote)


if __name__ == "__main__":
    unittest.main()
