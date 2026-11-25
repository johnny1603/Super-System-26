"""A real progress bar for a post's production (2026-11-25).

The indeterminate stripe said only "working". The bar now reflects the
production's real steps (core/production_progress):
1. the total is computed at the start from the real step list;
2. each step weighs its expected duration — measured, kept per step kind;
3. it only moves forward;
4. the line under it names the step actually running;
5. it is there for every production, in the chat card and the Social tab;
6. a failed step stops it where it is and its reason is shown next to it;
7. elapsed time always; remaining only once measured, and called an estimate.

Run from the repo root:  python tests/test_production_progress.py -v
"""
import os
import sys
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_production_honesty as tph          # noqa: E402  (the pipeline harness)

from core import agent_actions as aa           # noqa: E402
from core import chat_approval as ca           # noqa: E402
from core import production_progress as prog   # noqa: E402
from core import social_queue as sq            # noqa: E402

ROOT = os.path.dirname(HERE)


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def time(self):
        return self.t

    def monotonic(self):
        return self.t


class Store(unittest.TestCase):
    def setUp(self):
        self.settings = {}
        self.clock = Clock()
        saved = (prog._read, prog._write, prog.time)
        prog._read = lambda key: dict(self.settings.get(key) or {})
        prog._write = lambda key, value: self.settings.__setitem__(key, dict(value))
        prog.time = self.clock
        self.addCleanup(lambda: (setattr(prog, "_read", saved[0]), setattr(prog, "_write", saved[1]),
                                 setattr(prog, "time", saved[2])))
        self.addCleanup(prog.stop_tracking)

    def record(self, item_id="p1", cid=27):
        return self.settings[f"{prog.KEY_PREFIX}{cid}"][item_id]


SLIDES = [{"asset_type": "text_slide", "characters": ["maya"], "character_mode": "composite"},
          {"asset_type": "generated_image"},
          {"asset_type": "text_slide", "characters": ["maya"]},
          {"asset_type": "text_slide"},
          {"asset_type": "generated_video"}]


# ─── 1. The total, from the real step list ───────────────────────────────────

class TheSteps(Store):
    def test_every_real_step_is_counted_at_the_start(self):
        ids = [s["id"] for s in prog.plan_for(SLIDES)]
        self.assertEqual(ids, ["generate:2", "generate:5",              # each image / video slide
                               "pose:1", "cutout:1", "pose:3", "cutout:3",  # a pose + its cut-out per character slide
                               "layout",                                # the layout of the text slides
                               "render:1", "render:3", "render:4",      # each text slide rendered
                               "assemble"])                             # the post put together
        self.assertEqual(prog.plan_for([{"asset_type": "text_slide"}])[-1]["id"], "assemble")
        tracker = prog.start(27, "p1", SLIDES)
        self.assertEqual(len(self.record()["steps"]), 11)               # stored from the first second
        self.assertEqual(tracker.snapshot()["fraction"], 0.0)

    def test_a_production_run_tracks_its_steps_end_to_end(self):
        run = tph.InTheChat("test_started_then_finished_in_the_producers_chat")
        run.setUp()
        self.addCleanup(run.doCleanups)
        item = run.queue()["queued"]
        run.approve(item["id"])                                          # the harness's production, inline
        rec = self.record(item["id"])
        self.assertEqual([s["id"] for s in rec["steps"]],
                         ["generate:2", "layout", "render:1", "render:3", "assemble"])
        self.assertEqual(rec["state"], "done")
        steps = {s["id"]: s["status"] for s in rec["steps"]}
        self.assertEqual((steps["generate:2"], steps["assemble"]), ("done", "done"))
        self.assertEqual(prog.read(27, item["id"])["fraction"], 1.0)    # 100% only when it really ended


# ─── 2. Weights from measured durations ──────────────────────────────────────

class Weights(Store):
    def test_measured_medians_replace_the_defaults_once_there_are_enough(self):
        few = prog.expected_seconds({"generate": [10.0, 30.0]})
        self.assertEqual(few["generate"], (prog.DEFAULT_SECONDS["generate"], False))
        enough = prog.expected_seconds({"generate": [10.0, 30.0, 20.0], "render": [0.2, 0.4, 0.3]})
        self.assertEqual(enough["generate"], (20.0, True))
        self.assertEqual(enough["render"], (0.3, True))

    def test_the_weights_decide_how_far_one_step_moves_the_bar(self):
        steps = prog.plan_for([{"asset_type": "generated_image"}, {"asset_type": "text_slide"}])
        steps[0]["status"] = "done"                                     # the generation finished
        heavy = {k: (v, True) for k, v in {**prog.DEFAULT_SECONDS, "generate": 100.0}.items()}
        light = {k: (v, True) for k, v in {**prog.DEFAULT_SECONDS, "generate": 1.0}.items()}
        rec = {"steps": steps, "state": "running", "t0": self.clock.t}
        f_heavy = prog.compute(rec, now=self.clock.t, expected=heavy)["fraction"]
        f_light = prog.compute(rec, now=self.clock.t, expected=light)["fraction"]
        total_heavy = 100.0 / 4 + 4.0 + 0.4 + 2.0                       # generations run 4 at once
        self.assertAlmostEqual(f_heavy, (100.0 / 4) / total_heavy, places=3)
        self.assertLess(f_light, f_heavy)

    def test_every_finished_run_feeds_the_measured_durations(self):
        tracker = prog.start(27, "p1", [{"asset_type": "generated_image"}])
        prog.begin("generate:1")
        self.clock.t += 37.0
        prog.done("generate:1")
        prog.begin("assemble")
        self.clock.t += 1.5
        prog.done("assemble")
        tracker.finish("done")
        times = self.settings[prog.TIMES_KEY]
        self.assertEqual((times["generate"], times["assemble"]), ([37.0], [1.5]))
        # a reused stored pose took no real time: it never drags the weights down
        tracker = prog.start(27, "p2", [{"asset_type": "text_slide", "characters": ["maya"]}])
        prog.done("pose:1", skipped=True)
        tracker.finish("done")
        self.assertNotIn("pose", self.settings[prog.TIMES_KEY])

    def test_a_run_keeps_the_weights_it_started_with(self):
        prog.start(27, "p1", [{"asset_type": "generated_image"}])
        stored = self.record()["expected"]
        self.assertEqual(stored["generate"][0], prog.DEFAULT_SECONDS["generate"])


# ─── 3. Never backwards ──────────────────────────────────────────────────────

class Monotonic(Store):
    def test_the_bar_only_moves_forward_and_never_claims_100_early(self):
        tracker = prog.start(27, "p1", SLIDES)
        seen = [tracker.snapshot()["fraction"]]
        order = [("generate:2", 30), ("generate:5", 90), ("pose:1", 50), ("cutout:1", 2), ("pose:3", 40),
                 ("cutout:3", 2), ("layout", 5), ("render:1", 1), ("render:3", 1), ("render:4", 1),
                 ("assemble", 3)]
        for step, seconds in order:
            prog.begin(step)
            for _ in range(4):
                self.clock.t += seconds / 4
                seen.append(prog.read(27, "p1")["fraction"])
            prog.done(step)
            seen.append(prog.read(27, "p1")["fraction"])
        self.assertEqual(seen, sorted(seen))                            # never backwards
        self.assertLessEqual(max(seen), 0.99)                           # never 100% before it ended
        tracker.finish("done")
        self.assertEqual(prog.read(27, "p1")["fraction"], 1.0)

    def test_a_step_slower_than_expected_never_pulls_it_back(self):
        rec = {"steps": prog.plan_for([{"asset_type": "generated_image"}]), "state": "running",
               "t0": self.clock.t, "shown": 0.6}
        self.assertEqual(prog.compute(rec, now=self.clock.t)["fraction"], 0.6)
        rec["steps"][0].update(status="running", began=self.clock.t)
        late = prog.compute(rec, now=self.clock.t + 10_000)["fraction"]   # far past its expectation
        self.assertGreaterEqual(late, 0.6)
        self.assertLessEqual(late, 0.99)


# ─── 4. The step in words ────────────────────────────────────────────────────

class StepLine(Store):
    def test_the_line_names_the_step_actually_running(self):
        prog.start(27, "p1", SLIDES)
        self.assertEqual(prog.read(27, "p1")["text"], "מתכונן להפקה…")
        prog.begin("generate:2")
        self.assertEqual(prog.read(27, "p1")["text"], "מייצר תמונה 1 מתוך 2")
        self.clock.t += 1
        prog.begin("generate:5")
        self.assertEqual(prog.read(27, "p1")["text"], "מייצר תמונה 1 מתוך 2 (2 במקביל)")
        prog.done("generate:2")
        self.assertEqual(prog.read(27, "p1")["text"], "מייצר תמונה 2 מתוך 2")
        prog.done("generate:5")
        self.clock.t += 1
        prog.begin("pose:1")
        self.assertEqual(prog.read(27, "p1")["text"], "מייצר פוזה 1 מתוך 2")
        prog.done("pose:1")
        self.clock.t += 1
        prog.begin("cutout:1")
        self.assertEqual(prog.read(27, "p1")["text"], "מסיר רקע מפוזה 1 מתוך 2")
        prog.done("cutout:1")
        for step in ("pose:3", "cutout:3", "layout"):
            prog.done(step)
        self.clock.t += 1
        prog.begin("render:3")
        self.assertEqual(prog.read(27, "p1")["text"], "מרנדר שקופית 3")
        self.assertEqual(prog.read(27, "p1", "en")["text"], "Rendering slide 3")

    def test_the_real_steps_report_where_they_run(self):
        chars = _src("core", "agent_characters.py")
        pose = chars[chars.index("def make_pose"):chars.index("def pose_for")]
        self.assertLess(pose.index('progress.begin(f"pose:{_step}")'), pose.index("_gen or gen.generate_with_references"))
        self.assertLess(pose.index('progress.begin(f"cutout:{_step}")'), pose.index("_remove or _local_remove()"))
        design = _src("core", "slide_design.py")
        opened = design[design.index("def open_for_post"):design.index("def request(")]
        self.assertIn('progress.begin("layout")', opened)
        self.assertIn('progress.begin(f"render:{n}")', opened)
        renderer = _src("core", "slide_renderer.py")
        self.assertIn("on_slide(n, None)", renderer)
        pipeline = _src("core", "post_pipeline.py")
        self.assertIn('production_progress.begin(f"generate:{n}")', pipeline)
        self.assertIn('production_progress.begin("assemble")', pipeline)

    def test_the_renderer_hook_reports_each_slide_in_order(self):
        from core import slide_renderer as sr
        calls = []
        saved = sr.render_slide
        sr.render_slide = lambda *a, **k: b""
        try:
            sr.render_set({}, {}, [{"n": 2}, {"n": 4}], on_slide=lambda n, p: calls.append((n, p)))
        finally:
            sr.render_slide = saved
        self.assertEqual(calls, [(2, None), (2, []), (4, None), (4, [])])


# ─── 5. There for every production: the card and the Social tab ─────────────

class AlwaysThere(Store):
    def row(self, **over):
        return {"id": 5, "client_id": 27, "action": "rerun_post_production", "params": {"item_id": "p1"},
                "status": "running", "persona": "media", "approver": "client", "origin": "client_chat",
                "decided_by": "system", "decided_at": datetime.fromtimestamp(self.clock.t, timezone.utc).isoformat(),
                **over}

    def test_the_card_is_determinate_from_its_first_second(self):
        spec = aa.ACTIONS["rerun_post_production"]
        row = self.row()                                                # stored before the run starts
        first = ca.progress_extra(row, spec, "he")
        self.assertEqual((first["determinate"], first["fraction"]), (True, 0.0))
        self.assertIn("מתכונן", first["text"])
        prog.start(27, "p1", [{"asset_type": "generated_image"}])
        prog.begin("generate:1")
        self.clock.t += 20
        now = ca.progress_extra(row, spec, "he")
        self.assertGreater(now["fraction"], 0)
        self.assertEqual((now["text"], now["step"]), ("מייצר תמונה 1 מתוך 1", "מייצר תמונה 1 מתוך 1"))
        self.assertEqual(now["elapsed_s"], 20.0)

    def test_an_earlier_run_of_the_same_post_is_never_shown(self):
        tracker = prog.start(27, "p1", [{"asset_type": "generated_image"}])
        tracker.finish("done")
        self.clock.t += 3600                                            # run again an hour later
        got = ca.progress_extra(self.row(), aa.ACTIONS["rerun_post_production"], "he")
        self.assertEqual(got["fraction"], 0.0)                          # not the old run's 100%

    def test_the_social_tab_shows_the_same_bar(self):
        prog.start(27, "p1", [{"asset_type": "generated_image"}])
        prog.begin("generate:1")
        item = {"id": "p1", "production": {"state": "running",
                                           "started_at": datetime.fromtimestamp(self.clock.t, timezone.utc).isoformat()}}
        view = sq._production_view(item, "he", 27)
        self.assertEqual(view["progress"]["text"], "מייצר תמונה 1 מתוך 1")
        html = _src("dashboard", "client", "index.html")
        self.assertIn("+ socialProdBar(item.id, prod)", html)
        self.assertIn("((i.post || {}).production || {}).state === 'running'", html)   # refreshed while it runs

    def test_the_dashboard_bar_is_determinate_never_backwards_and_says_estimate(self):
        html = _src("dashboard", "client", "index.html")
        state = html[html.index("function actionBarState"):html.index("function paintActionBar")]
        self.assertIn("Math.max(Number(p.maxPct) || 0", state)          # never backwards on a card
        self.assertIn("p.remaining_s == null ? null", state)            # only the server's measured estimate
        self.assertIn("prod_left_estimate:", html)
        self.assertIn("(הערכה)", html)
        self.assertIn("prod_elapsed:", html)


# ─── 6. A failed step stops the bar and says why ─────────────────────────────

class Failure(Store):
    def test_a_failed_step_shows_its_reason_and_the_bar_freezes(self):
        tracker = prog.start(27, "p1", [{"asset_type": "generated_image"}, {"asset_type": "generated_image"}])
        prog.begin("generate:1")
        self.clock.t += 30
        prog.done("generate:1")
        prog.begin("generate:2")
        self.clock.t += 5
        prog.done("generate:2", False, "חשבון יצירת התמונות של העסק לא מחובר")
        running = prog.read(27, "p1")
        self.assertEqual(running["problems"], ["חשבון יצירת התמונות של העסק לא מחובר"])
        tracker.finish("failed")
        at_end = prog.read(27, "p1")
        self.clock.t += 600
        later = prog.read(27, "p1")
        self.assertEqual(later["fraction"], at_end["fraction"])         # frozen where it stopped
        self.assertLess(later["fraction"], 1.0)
        self.assertEqual(later["elapsed_s"], at_end["elapsed_s"])       # the clock stopped too
        self.assertIsNone(later["remaining_s"])
        self.assertEqual(later["state"], "failed")

    def test_the_ended_card_keeps_the_frozen_bar_with_the_reason(self):
        tracker = prog.start(27, "p1", [{"asset_type": "generated_image"}])
        prog.done("generate:1", False, "ספק היצירה החזיר שגיאה")
        tracker.finish("failed")
        row = {"id": 5, "client_id": 27, "action": "rerun_post_production", "params": {"item_id": "p1"},
               "status": "done", "persona": "media", "approver": "client", "origin": "client_chat",
               "decided_by": "system", "decided_at": datetime.fromtimestamp(self.clock.t, timezone.utc).isoformat(),
               "result": {"text": "x"}}
        saved = sq.get
        sq.get = lambda cid, iid: {"title": "פוסט"}
        try:
            card = aa.card(row, "client", "he")
        finally:
            sq.get = saved
        self.assertEqual(card["progress_final"]["state"], "failed")
        self.assertEqual(card["progress_final"]["problems"], ["ספק היצירה החזיר שגיאה"])
        html = _src("dashboard", "client", "index.html")
        finish = html[html.index("function finishActionCard"):html.index("function celebrateTask")]
        self.assertIn("paintFrozenBar(el, c.progress_final)", finish)
        self.assertIn("appendBarProblems(box, final.problems)", html)

    def test_a_failed_generation_in_a_real_run_lands_on_the_bar(self):
        run = tph.InTheChat("test_started_then_finished_in_the_producers_chat")
        run.setUp()
        self.addCleanup(run.doCleanups)
        item = run.queue()["queued"]
        run.approve(item["id"], generate=lambda cid, brief, palette, platform="instagram":
                    {"success": False, "status": "no_account"})
        got = prog.read(27, item["id"])
        self.assertIn("חשבון יצירת התמונות של העסק לא מחובר", got["problems"])
        self.assertLess(got["fraction"], 1.0)


# ─── Wiring ──────────────────────────────────────────────────────────────────

class Wiring(unittest.TestCase):
    def test_the_progress_rows_go_with_the_client(self):
        from core import deletion_guard
        self.assertIn("production_progress:", deletion_guard.CLIENT_SETTING_PREFIXES)
        self.assertIn('f"production_progress:{int(i)}"', _src("core", "client_deletion.py"))
        self.assertIn('"production_progress:"', _src("core", "admin_service.py"))

    def test_every_step_line_in_five_languages(self):
        for key, table in prog.STEP_TEXT.items():
            self.assertEqual(set(table), {"he", "en", "fr", "ar", "ru"}, key)


if __name__ == "__main__":
    unittest.main()
