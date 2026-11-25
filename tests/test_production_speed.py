"""Production speed (2026-11-25) — a four-slide carousel with character
images took about five minutes, and the manager stopped card 69 because of it.

What must stay true:
1. A post's generations are SUBMITTED TOGETHER and waited for as a set
   (GEN_PARALLEL at a time) — four take about as long as one — and the
   post's credit budget is still enforced (what runs reserves its budget).
   The same for the character poses of the design step.
2. A run completes in ONE pass inside a request of its own
   (POST /api/actions/{id}/drive): never paced by the per-minute tick, which
   only picks up a run whose call never arrived.
3. Stop leaves a clean state: the card ends at once (never "running"),
   nothing new is submitted, what was already paid is kept as a draft and
   recorded, no credits are counted for work that wasn't made.

Run from the repo root:  python tests/test_production_speed.py -v
"""
import os
import sys
import threading
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_production_honesty as tph          # noqa: E402  (the pipeline harness)
import test_post_brief as tpb                  # noqa: E402

from core import agent_actions as aa           # noqa: E402
from core import agent_characters as ac        # noqa: E402
from core import media_gen_service as gen      # noqa: E402
from core import post_pipeline as pp           # noqa: E402
from core import social_queue as sq            # noqa: E402

ROOT = os.path.dirname(HERE)
NAP = 0.25                                       # one fake generation


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _images_post(n: int, budget: int = 5):
    roles = ["hook"] + ["point"] * max(0, n - 2) + (["cta"] if n > 1 else [])
    return tpb._post(assets=[
        {"asset_id": f"g{i + 1}", "role": roles[i], "copy_headline": f"כותרת {i + 1}", "copy_body": "",
         "asset_type": "generated_image", "budget_credits": budget,
         "visual_direction": f"a plain black gift box on a studio table, angle {i + 1}"} for i in range(n)])


class Meter:
    """A fake generation that sleeps and counts how many run at once."""

    def __init__(self, nap=NAP, credits=1, stop_after=None):
        self.nap, self.credits, self.stop_after = nap, credits, stop_after
        self.lock = threading.Lock()
        self.now = self.peak = 0
        self.calls = []

    def __call__(self, cid, brief, palette, platform="instagram"):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
            self.calls.append(brief.asset_id)
        time.sleep(self.nap)
        with self.lock:
            self.now -= 1
        return {"success": True, "kind": "image", "file_id": f"FILE_{brief.asset_id}_0123456789",
                "credits": self.credits}


class Base(tph.Harness):
    def produce(self, post, meter, design=None):
        from agents import media_agent
        from core import slide_design
        item = self.queue(post)["queued"]
        self.patch(media_agent, "generate_from_brief", meter)
        self.designed = []
        self.patch(slide_design, "open_for_post", lambda cid, it, slides, lang="he": self.designed.append(slides) or (
            design if design is not None else {"item": {"id": "design0001"}, "problems": [], "character_errors": []}))
        sq.approve(27, [item["id"]])
        started = time.monotonic()
        production = pp.produce(27, item["id"], tell=False)
        return item, production, time.monotonic() - started


# ─── 1. Generations submitted together, budget kept ──────────────────────────

class Parallel(Base):
    def test_four_generations_take_about_as_long_as_one(self):
        meter = Meter()
        _item, production, took = self.produce(_images_post(4), meter)
        self.assertEqual(production["state"], "done", production)
        self.assertEqual(production["made"], 4)
        self.assertEqual(meter.peak, 4)                                   # all four at the vendor at once
        self.assertLess(took, NAP * 2.2)                                  # not 4 × NAP
        self.assertEqual(production["credits"], 4)
        self.assertLess(production["timing"]["generate_s"], NAP * 2.2)    # where the time went, on the record
        self.assertIn("total_s", production["timing"])

    def test_concurrency_is_capped(self):
        meter = Meter(nap=0.15)
        _item, production, _took = self.produce(_images_post(6), meter)
        self.assertEqual(production["made"], 6)
        self.assertEqual(meter.peak, pp.GEN_PARALLEL)

    def test_queue_items_keep_the_slide_order(self):
        meter = Meter(nap=0.05)
        item, _production, _took = self.produce(_images_post(4), meter)
        made = [i for i in self.store.items if (i.get("post_ref") or {}).get("post_id") == item["id"]]
        self.assertEqual([i["post_ref"]["n"] for i in made], [1, 2, 3, 4])

    def test_the_budget_counts_what_is_still_running(self):
        # budgets 1 + 1 + 0 + 0 = 2: the first two reserve the whole budget, so the
        # next two are refused BEFORE they reach the vendor — even while nothing is spent yet
        post = _images_post(4)
        for asset, b in zip(post["assets"], (1, 1, 0, 0)):
            asset["budget_credits"] = b
        meter = Meter()
        _item, production, _took = self.produce(post, meter)
        self.assertEqual(sorted(meter.calls), ["g1", "g2"])
        statuses = {a["asset_id"]: a["status"] for a in production["assets"]}
        self.assertEqual(statuses, {"g1": "made", "g2": "made", "g3": "over_budget", "g4": "over_budget"})

    def test_a_slide_waiting_for_a_slot_sees_what_was_spent(self):
        # six slides, budget 1 each (6): the first four each spend 3 → 12 ≥ 6, so 5 and 6 are refused
        meter = Meter(nap=0.1, credits=3)
        _item, production, _took = self.produce(_images_post(6, budget=1), meter)
        self.assertEqual(len(meter.calls), 4)
        self.assertEqual([a["status"] for a in production["assets"]][4:], ["over_budget", "over_budget"])
        self.assertEqual(production["credits"], 12)

    def test_one_missing_account_is_said_once(self):
        def no_account(cid, brief, palette, platform="instagram"):
            return {"success": False, "status": "no_account", "errors": ["x"]}
        _item, production, _took = self.produce(_images_post(4), no_account)
        self.assertEqual(production["failed"], 1)
        self.assertEqual(len(production["problems"]), 1)

    def test_the_choice_of_a_real_file_takes_turns(self):
        from agents import media_agent
        state = {"now": 0, "peak": 0}
        lock = threading.Lock()

        def choose(cid, brief, candidates):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
            return {"file_id": "REAL_1"}
        self.patch(media_agent, "_find_real", lambda brief, on_demand: [{"file_id": "REAL_1"}])
        self.patch(media_agent, "choose_real_asset", choose)
        self.patch(media_agent, "_use_real_asset", lambda cid, brief, chosen, platform: {"success": True,
                                                                                         "file_id": "REAL_1"})
        from core import parallel
        brief = types.SimpleNamespace(asset_type="generated_image", characters=[], character_mode="")
        with media_agent.one_post_choices():
            got = parallel.gather({i: (lambda: media_agent.generate_from_brief(27, brief, {})) for i in range(4)})
        self.assertTrue(all(ok for ok, _ in got.values()))
        self.assertEqual(state["peak"], 1)                                 # one choice at a time inside a post


class Poses(unittest.TestCase):
    def setUp(self):
        self.saved = (ac.find_poses, ac.make_pose, ac.record_pose_use)
        self.addCleanup(lambda: (setattr(ac, "find_poses", self.saved[0]), setattr(ac, "make_pose", self.saved[1]),
                                 setattr(ac, "record_pose_use", self.saved[2])))
        ac.record_pose_use = lambda pose_id: {}

    def test_the_posts_poses_are_made_together(self):
        meter = {"now": 0, "peak": 0}
        lock = threading.Lock()

        def make(agent, pose, *a, **k):
            with lock:
                meter["now"] += 1
                meter["peak"] = max(meter["peak"], meter["now"])
            time.sleep(NAP)
            with lock:
                meter["now"] -= 1
            return {"pose_id": f"{agent}-{pose}", "credits": 1}
        ac.find_poses = lambda *a, **k: []
        ac.make_pose = make
        started = time.monotonic()
        out = ac.poses_for([("maya", f"pose {i}") for i in range(4)], stopped=lambda: False)
        self.assertLess(time.monotonic() - started, NAP * 2.2)
        self.assertEqual(meter["peak"], 4)
        self.assertEqual([o["pose_id"] for o in out], [f"maya-pose {i}" for i in range(4)])

    def test_library_lookups_stay_in_order(self):
        seen = []
        ac.find_poses = lambda agent, query, **k: seen.append(query) or [{"pose_id": query, "repeat": False}]
        ac.make_pose = lambda *a, **k: self.fail("generated although the library had one")
        out = ac.poses_for([("maya", "a"), ("maya", "b")], stopped=lambda: False)
        self.assertEqual([o["pose_id"] for o in out], seen)
        self.assertTrue(all(o["reused"] for o in out))

    def test_concurrent_library_writes_lose_nothing(self):
        ac.record_pose_use = self.saved[2]
        store = {"poses": [{"pose_id": "p1", "agent": "maya", "uses": {"count": 0}}], "last": {}}
        saved = (ac._read, ac._write)
        self.addCleanup(lambda: (setattr(ac, "_read", saved[0]), setattr(ac, "_write", saved[1])))

        def read(key):
            snapshot = {"poses": [dict(p, uses=dict(p["uses"])) for p in store["poses"]], "last": dict(store["last"])}
            time.sleep(0.02)                                               # widen the read-modify-write window
            return snapshot
        ac._read = read
        ac._write = lambda key, value: store.update(value)
        threads = [threading.Thread(target=ac.record_pose_use, args=("p1",)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(store["poses"][0]["uses"]["count"], 5)

    def test_a_stopped_run_starts_no_pose(self):
        from core import slide_design
        ac.find_poses = lambda *a, **k: []
        ac.make_pose = lambda *a, **k: self.fail("a pose was generated after the stop")
        saved = aa.stop_requested
        aa.stop_requested = lambda proposal_id=None: True
        self.addCleanup(setattr, aa, "stop_requested", saved)
        entries, failures = slide_design.attach_characters(
            [{"asset_id": "s1", "role": "hook", "characters": ["maya"], "character_mode": "composite"}])
        self.assertEqual(failures, [])                                     # a stop is not a failure
        self.assertNotIn("character", entries[0])


class Polling(unittest.TestCase):
    def test_the_first_checks_come_sooner_and_the_total_is_unchanged(self):
        waits = gen.poll_waits()
        self.assertEqual(waits[0], 2)
        self.assertLessEqual(sum(waits), gen.POLL_SECONDS * gen.POLL_MAX_TRIES)
        self.assertGreater(sum(waits), gen.POLL_SECONDS * gen.POLL_MAX_TRIES - gen.POLL_SECONDS)
        self.assertEqual(max(waits), gen.POLL_SECONDS)


# ─── 2. One pass inside a request of its own ─────────────────────────────────

class Settings:
    """app_settings (insert-to-acquire) + the action rows, in memory."""

    def __init__(self, rows=()):
        self.keys = {}
        self.rows = {r["id"]: dict(r) for r in rows}

    def table(self, name):
        db = self

        class Q:
            def __init__(self):
                self.op, self.filters, self.payload = "select", {}, None

            def insert(self, row):
                self.op, self.payload = "insert", row
                return self

            def select(self, *a):
                return self

            def delete(self):
                self.op = "delete"
                return self

            def eq(self, k, v):
                self.filters[k] = v
                return self

            def order(self, *a, **k):
                return self

            def limit(self, n):
                return self

            def execute(self):
                if name == "app_settings":
                    key = self.filters.get("key")
                    if self.op == "insert":
                        if self.payload["key"] in db.keys:
                            raise RuntimeError("duplicate key")
                        db.keys[self.payload["key"]] = self.payload["value"]
                        return types.SimpleNamespace(data=[self.payload])
                    if self.op == "delete":
                        db.keys.pop(key, None)
                        return types.SimpleNamespace(data=[])
                    return types.SimpleNamespace(data=[{"key": key}] if key in db.keys else [])
                rows = [r for r in db.rows.values() if all(r.get(k) == v for k, v in self.filters.items())]
                return types.SimpleNamespace(data=rows)
        return Q()


def _row(pid=69, age_s=120, status="running"):
    stamp = (aa._now() - aa.timedelta(seconds=age_s)).isoformat()
    return {"id": pid, "client_id": 27, "action": "rerun_post_production", "params": {"item_id": "abc123"},
            "origin": "client_chat", "persona": "media", "status": status, "decided_at": stamp, "created_at": stamp}


class OwnRequest(unittest.TestCase):
    def setUp(self):
        self.db = Settings([_row()])
        self.ran = []
        self.saved = {n: getattr(aa, n) for n in ("_db", "_get", "_execute_in_background", "_client", "_lang", "_log")}
        self.addCleanup(lambda: [setattr(aa, n, v) for n, v in self.saved.items()])
        aa._db = lambda: self.db
        aa._get = lambda pid: self.db.rows.get(int(pid))
        aa._client = lambda cid: {"id": cid}
        aa._lang = lambda cid: "he"
        aa._log = lambda *a, **k: None

        def execute(row, spec, params, actor, client, lang):
            time.sleep(0.1)                                          # the whole pass, here
            self.db.rows[row["id"]]["status"] = "done"
            self.ran.append((row["id"], actor))
        aa._execute_in_background = execute

    def test_the_pass_runs_to_its_end_inside_the_call(self):
        out = aa.drive(69, "request")
        self.assertTrue(out["driven"])
        self.assertEqual(self.db.rows[69]["status"], "done")             # ended when the call returned — no tick
        self.assertEqual(self.ran, [(69, "client")])
        self.assertEqual(self.db.keys, {})                                # its markers are gone

    def test_it_is_driven_once(self):
        self.db.keys[f"{aa.DRIVE_PREFIX}69"] = {"by": "request"}
        self.assertEqual(aa.drive(69, "tick")["reason"], "already_driven")
        self.assertEqual(self.ran, [])

    def test_only_a_running_production_row(self):
        self.db.rows[69]["status"] = "cancelled"
        self.assertEqual(aa.drive(69, "request")["reason"], "not_running")
        self.db.rows[70] = {**_row(70), "action": "build_seo_report"}
        self.assertEqual(aa.drive(70, "request")["reason"], "not_driven_in_a_request")
        self.assertEqual(self.ran, [])

    def test_the_service_calls_itself_with_the_admin_key(self):
        import httpx
        sent = []
        saved = (httpx.post, os.environ.get("ADMIN_KEY"))
        self.addCleanup(lambda: (setattr(httpx, "post", saved[0]),
                                 os.environ.pop("ADMIN_KEY", None) if saved[1] is None
                                 else os.environ.__setitem__("ADMIN_KEY", saved[1])))
        os.environ["ADMIN_KEY"] = "k" * 24
        done = threading.Event()

        def post(url, headers=None, json=None, timeout=None):
            sent.append((url, headers, json))
            done.set()
            return types.SimpleNamespace(status_code=200)
        httpx.post = post
        self.assertTrue(aa.run_in_own_request(self.db.rows[69], "client"))
        self.assertTrue(done.wait(2))
        url, headers, body = sent[0]
        self.assertTrue(url.endswith("/api/actions/69/drive"))
        self.assertEqual(headers, {"X-Admin-Key": "k" * 24})
        self.assertEqual(body, {"actor": "client"})
        self.assertIn(f"{aa.DRIVE_WAIT_PREFIX}69", self.db.keys)          # the tick can pick it up if it never arrives
        self.assertEqual(self.ran, [])                                    # it runs THERE, not here

    def test_a_refused_call_runs_the_pass_here(self):
        import httpx
        saved = (httpx.post, os.environ.get("ADMIN_KEY"))
        self.addCleanup(lambda: (setattr(httpx, "post", saved[0]),
                                 os.environ.pop("ADMIN_KEY", None) if saved[1] is None
                                 else os.environ.__setitem__("ADMIN_KEY", saved[1])))
        os.environ["ADMIN_KEY"] = "k" * 24

        def refused(*a, **k):
            raise httpx.ConnectError("refused")
        httpx.post = refused
        aa._drive_call(69, "client", "k" * 24)
        self.assertEqual(self.ran, [(69, "client")])

    def test_the_tick_only_picks_up_a_call_that_never_arrived(self):
        handed = []
        saved = aa.run_in_own_request
        self.addCleanup(setattr, aa, "run_in_own_request", saved)
        aa.run_in_own_request = lambda row, actor: handed.append(row["id"]) or True
        self.assertEqual(aa.drive_due()["picked"], [])                    # no call was ever sent: not ours
        self.db.keys[f"{aa.DRIVE_WAIT_PREFIX}69"] = {"actor": "client"}
        self.db.keys[f"{aa.DRIVE_PREFIX}69"] = {"by": "request"}
        self.assertEqual(aa.drive_due()["picked"], [])                    # running in its request
        del self.db.keys[f"{aa.DRIVE_PREFIX}69"]
        self.assertEqual(aa.drive_due()["picked"], [69])
        self.db.rows[69]["decided_at"] = aa._now().isoformat()            # just sent: give it time
        self.assertEqual(aa.drive_due()["picked"], [])

    def test_the_wiring(self):
        api = _src("core", "api_server.py")
        self.assertIn('@app.post("/api/actions/{proposal_id}/drive", dependencies=_admin_only)', api)
        tick = api[api.index("def tiktok_uploads_run"):api.index("def admin_tiktok_uploads")]
        self.assertIn("agent_actions.drive_due()", tick)
        self.assertNotIn("post_pipeline", tick)                           # the tick never runs a step of production
        start = _src("core", "agent_actions.py")
        system = start[start.index("def start_system_run"):start.index("# Delete from the Social queue")]
        self.assertIn("run_in_own_request(row", system)
        window = start[start.index("def _execute_after_window"):start.index("def stop_run")]
        self.assertIn("run_in_own_request(row, actor)", window)
        pipeline = _src("core", "post_pipeline.py")
        begin = pipeline[pipeline.index("def start("):pipeline.index("def run_in_chat")]
        self.assertNotIn("add_task(run_in_chat", begin)                  # the card is made inside the approval request


# ─── 3. Stop leaves a clean state ────────────────────────────────────────────

class Stop(Base):
    def test_a_stopped_production_submits_nothing_new_and_keeps_what_was_paid(self):
        flag = {"stop": False}
        self.patch(aa, "stop_requested", lambda proposal_id=None: flag["stop"])

        class Stopper(Meter):
            def __call__(inner, cid, brief, palette, platform="instagram"):
                out = Meter.__call__(inner, cid, brief, palette, platform)
                flag["stop"] = True                       # the manager pressed stop while the first wave ran
                return out
        post = _images_post(6)
        post["assets"].append({"asset_id": "t1", "role": "cta", "copy_headline": "סוף", "copy_body": "",
                               "asset_type": "text_slide", "visual_direction": ""})
        meter = Stopper(nap=0.1)
        item, production, _took = self.produce(post, meter)
        self.assertEqual(len(meter.calls), pp.GEN_PARALLEL)               # only what was already at the vendor
        self.assertEqual(production["state"], "stopped")                  # never left "running"
        self.assertEqual(production["made"], pp.GEN_PARALLEL)
        self.assertEqual(production["credits"], pp.GEN_PARALLEL)          # credits only for what was made
        statuses = [a["status"] for a in production["assets"]]
        self.assertEqual(statuses.count("stopped"), 3)                    # g5, g6 and the text slide
        self.assertEqual(self.designed, [])                               # no design, no poses after the stop
        kept = [i for i in self.store.items if (i.get("post_ref") or {}).get("post_id") == item["id"]]
        self.assertEqual(len(kept), pp.GEN_PARALLEL)                      # every paid file referenced: no orphan
        self.assertTrue(all(i["status"] == "waiting" for i in kept))      # drafts, waiting for approval
        self.assertEqual(production["problems"], [])
        self.assertIn(item["id"], [r["item_id"] for r in pp.retryable(27)])   # the agents can offer to run it again
        self.assertEqual(pp.rerun_refusal(27, item["id"]), "")

    def test_the_card_ends_at_once(self):
        row = _row(age_s=60)
        claims = []
        self.patch(aa, "_claim", lambda pid, frm, values: claims.append((pid, frm, values)) or True)
        self.patch(aa, "_log", lambda *a, **k: None)
        self.patch(aa, "close_alerts", lambda pid: [])
        self.patch(aa, "_forget_state", lambda cid: None)
        out = aa.stop_action(row, "client", "he")
        self.assertEqual(out["result"], "stopped")
        self.assertEqual(claims[-1][2]["status"], "cancelled")            # not "running", not "stop_requested"
        self.assertEqual(claims[-1][2]["outcome"], "stopped_midway")
        self.assertIn("טיוטה", out["message"])                           # what was saved stays a draft

    def test_a_late_result_is_never_posted_on_a_stopped_card(self):
        # the run returns after the stop: the card's compare-and-set fails, so no line contradicts the stop
        src = _src("core", "agent_actions.py")
        tail = src[src.index("asked_to_stop = _asked_to_stop(proposal_id)"):]
        self.assertIn('if not _claim(proposal_id, ("running",)', tail[:400])
        self.assertIn("result not posted", tail[:1200])


if __name__ == "__main__":
    unittest.main()
