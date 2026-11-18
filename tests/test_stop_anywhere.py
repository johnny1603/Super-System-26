"""Stop at ANY stage, and jobs that stop themselves (2026-11-19, docs/CHAT-APPROVALS.md §Stop).

- every running card has a Stop button; every slow action has ONE stop mode;
- discard: the run ends at once, and nothing it still produces is shown, posted or proposed;
- finish: an uninterruptible step finishes and the card says so (before and after);
- job: a TikTok upload stops BETWEEN chunks; fully uploaded = it can't be cut;
- a typed stop works at any stage, in the client chat and for loose TikTok jobs;
- a TikTok job with no progress ends as "stuck"; failed steps are capped over the job's life;
- the team (admin) can stop a run from the Control Room; Itzik already stops between steps.
"""
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")

for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}), ("httpx", {}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from test_chat_approvals import YT, Base, Tasks   # noqa: E402
from core import agent_actions as aa             # noqa: E402
from core import chat_approval as ca             # noqa: E402
from core import tiktok_uploads as tu            # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
LANDING = "create_landing_page"
TIKTOK = "send_tiktok_to_inbox"


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _past(seconds=60):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class StopBase(Base):
    def running_row(self, action, **extra):
        row = self.card_row(action=action, status="running", decided_at=_past(aa.STOP_WINDOW_SECONDS + 30),
                            decided_by="client", **extra)
        return row

    def stop_click(self, row, actor="client"):
        return aa.decide(row["id"], actor, 27, "stop")


class EveryActionHasOneMode(unittest.TestCase):
    def test_every_slow_action_is_listed_once(self):
        slow = {name for name, spec in aa.ACTIONS.items() if spec.get("slow")}
        sets = (aa.STOP_DISCARD, aa.STOP_JOB, aa.STOP_FINISH)
        for name in slow:
            self.assertEqual(sum(name in s for s in sets), 1, name)
        for s in sets:
            self.assertLessEqual(s, set(aa.ACTIONS), "a stop set names an action that doesn't exist")
        self.assertEqual(aa.stop_mode("never_heard_of_it"), "finish")       # unknown = never claim a cut

    def test_lines_in_every_language(self):
        for table in (aa.STOPPED_MIDWAY, aa.STOPPED_JOB, aa.FINISHING, aa.FINISHING_JOB, aa.FINISHED_AFTER_STOP,
                      ca.JOBS_STOPPED, ca.STOP_HINT):
            self.assertEqual(set(table), set(LANGS))
            for text in table.values():
                self.assertTrue(text.strip())
        self.assertEqual(set(aa.ACTIONS[TIKTOK]["refusals"]["stuck"]), set(LANGS))


class Discard(StopBase):
    def test_button_stops_midway_and_nothing_more_appears(self):
        row = self.running_row(LANDING, params={"page": "x"})
        seen = {}

        def run(client_id, params, background_tasks, **kw):
            # the client clicks Stop while the draft is being written
            out = aa.stop_action(aa._get(row["id"]), by="client", lang="he")
            seen["result"] = out["result"]
            seen["stopped_in_thread"] = aa._run_stopped()
            seen["proposal"] = aa.propose(27, "client_chat", "website", {"name": "publish_landing_page", "params": {}},
                                          by_code=True)
            return {"page_id": 5}

        saved = aa.ACTIONS[LANDING]
        aa.ACTIONS[LANDING] = {**saved, "run": run, "needs_row": False}
        try:
            out = aa._execute(aa._get(row["id"]), aa.ACTIONS[LANDING], {"page": "x"}, "client", {}, "he", None)
        finally:
            aa.ACTIONS[LANDING] = saved
        self.assertEqual(seen["result"], "stopped")
        self.assertTrue(seen["stopped_in_thread"])
        self.assertEqual(seen["proposal"], {"error": "stopped"})           # no publish card after a stop
        self.assertEqual(self.row(row["id"])["status"], "cancelled")
        self.assertEqual(self.row(row["id"])["outcome"], "stopped_midway")
        self.assertEqual(out["message"], "")                                # the late result is not shown
        self.assertEqual(self.posted, [])

    def test_the_card_line_says_what_did_and_did_not_happen(self):
        row = self.running_row(LANDING, params={"page": "x"})
        out = self.stop_click(row)
        self.assertTrue(out["success"])
        self.assertEqual(out["status"], "cancelled")
        self.assertIn("עצרתי", out["message"])
        self.assertIn("טיוטה", out["message"])                              # what may already exist
        self.assertIn("לא יוצג", out["message"])                            # what will not happen
        self.assertEqual(out["card"]["result"], out["message"])


class Finish(StopBase):
    def test_an_uninterruptible_step_finishes_and_says_so(self):
        row = self.running_row(YT)
        out = self.stop_click(row)
        self.assertEqual(out["stop"], "finishing")
        self.assertEqual(self.row(row["id"])["status"], "running")
        self.assertEqual(self.row(row["id"])["outcome"], "stop_requested")
        self.assertIn("אי אפשר לעצור באמצע", out["message"])
        progress = out["card"]["progress"]
        self.assertFalse(progress["stop"]["can"])                         # no second Stop
        self.assertTrue(progress["stop"]["requested"])
        self.assertIn("אי אפשר לעצור באמצע", progress["text"])
        done = aa._execute(aa._get(row["id"]), aa.ACTIONS[YT], row["params"], "client", {}, "he", None)
        self.assertEqual(self.row(row["id"])["outcome"], "done_after_stop")
        self.assertTrue(done["message"].startswith(aa.FINISHED_AFTER_STOP["he"]))
        self.assertTrue(self.posted[-1][2].startswith(aa.FINISHED_AFTER_STOP["he"]))


class Job(StopBase):
    def setUp(self):
        super().setUp()
        self.saved_tu = tu.stop_for_proposal

    def tearDown(self):
        tu.stop_for_proposal = self.saved_tu
        super().tearDown()

    def test_between_chunks(self):
        row = self.running_row(TIKTOK, params={"drive_file_id": "f"})
        tu.stop_for_proposal = lambda cid, pid, by: {"stopped": True, "pct": 40}
        out = self.stop_click(row)
        self.assertEqual(out["status"], "cancelled")
        self.assertEqual(self.row(row["id"])["outcome"], "stopped_job")
        self.assertIn("שום דבר לא הגיע לטיוטות", out["message"])

    def test_fully_uploaded_cannot_be_cut(self):
        row = self.running_row(TIKTOK, params={"drive_file_id": "f"})
        tu.stop_for_proposal = lambda cid, pid, by: {"processing": True}
        out = self.stop_click(row)
        self.assertEqual(out["stop"], "finishing")
        self.assertIn("למחוק", out["message"])                              # they can delete the draft


class TypedStopAtAnyStage(StopBase):
    def test_after_the_window_a_typed_stop_still_stops(self):
        row = self.running_row(LANDING, persona="website", params={"page": "x"})
        out = ca.fast_turn(27, "website", "עצור", Tasks(), "he")
        self.assertEqual(self.row(row["id"])["status"], "cancelled")
        self.assertIn("עצרתי", out["reply"])

    def test_several_runs_ask_which(self):
        self.running_row(LANDING, persona="website", params={"page": "x"})
        self.running_row(YT, persona="website")
        out = ca.from_result(27, "website", {"card_decision": {"verdict": "stop", "card_id": 0}}, Tasks(), "he")
        self.assertIn("1.", out["reply"])
        self.assertTrue(all(r["status"] == "running" for r in self.db.tables[aa.TABLE]))

    def test_a_named_run_is_stopped(self):
        first = self.running_row(LANDING, persona="website", params={"page": "x"})
        self.running_row(YT, persona="website")
        ca.from_result(27, "website", {"card_decision": {"verdict": "stop", "card_id": first["id"]}}, Tasks(), "he")
        self.assertEqual(self.row(first["id"])["status"], "cancelled")

    def test_loose_tiktok_jobs(self):
        saved = (tu.loose_active, tu.stop_client_jobs)
        tu.loose_active = lambda cid: [{"id": "j1"}]
        tu.stop_client_jobs = lambda cid, by: {"stopped": 2, "processing": 0}
        try:
            out = ca.fast_turn(27, "media", "stop", Tasks(), "he")
        finally:
            tu.loose_active, tu.stop_client_jobs = saved
        self.assertIn("(2)", out["reply"])

    def test_the_team_can_stop_a_clients_run(self):
        row = self.running_row(LANDING, params={"page": "x"})
        out = self.stop_click(row, actor="admin")
        self.assertEqual(out["status"], "cancelled")

    def test_another_client_cannot(self):
        row = self.running_row(LANDING, params={"page": "x"})
        out = aa.decide(row["id"], "client", 28, "stop")
        self.assertFalse(out["success"])
        self.assertEqual(self.row(row["id"])["status"], "running")


class TikTokJobs(unittest.TestCase):
    def setUp(self):
        self.jobs = []
        self.failed = []
        self.saved = {n: getattr(tu, n) for n in ("load", "_save", "_fail", "_log")}
        tu.load = lambda cid: [dict(j) for j in self.jobs]
        tu._save = lambda cid, jobs: self.jobs.__setitem__(slice(None), [dict(j) for j in jobs])
        tu._fail = lambda job, why, code="send_failed": self.failed.append(code)
        tu._log = lambda *a, **k: None

    def tearDown(self):
        for n, fn in self.saved.items():
            setattr(tu, n, fn)

    def job(self, **extra):
        job = {"id": "j1", "client_id": 27, "status": "uploading", "next": 2, "chunks": 5, "proposal_id": None,
               "updated_at": datetime.now(timezone.utc).isoformat(), **extra}
        self.jobs.append(job)
        return job

    def test_stop_between_chunks(self):
        self.job()
        self.assertEqual(tu.stop_job(27, "j1"), {"stopped": True, "pct": 40})
        self.assertEqual(self.jobs[0]["status"], "stopped")
        self.assertIsNone(self.jobs[0]["upload_url"])
        self.assertTrue(tu._halted(27, self.jobs[0]))
        self.assertFalse(tu._due(self.jobs[0]))

    def test_processing_cannot_be_cut(self):
        self.job(status="processing")
        self.assertEqual(tu.stop_job(27, "j1"), {"processing": True})
        self.assertEqual(self.jobs[0]["status"], "processing")

    def test_a_stopped_card_halts_its_job(self):
        self.job(proposal_id=9)
        saved = aa._get
        aa._get = lambda pid: {"id": pid, "status": "cancelled"}
        try:
            self.assertTrue(tu._halted(27, self.jobs[0]))
        finally:
            aa._get = saved
        self.assertEqual(self.jobs[0]["status"], "stopped")

    def test_no_progress_ends_as_stuck(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=tu.NO_PROGRESS_S + 60)).isoformat()
        job = self.job(progress_at=old)
        self.assertTrue(tu._no_progress(job))
        self.assertFalse(tu._no_progress({**job, "progress_at": datetime.now(timezone.utc).isoformat()}))
        self.assertFalse(tu._no_progress({**job, "status": "processing"}))
        stub = types.ModuleType("agents.tiktok_content_agent")
        stub.UploadRefused = type("UploadRefused", (Exception,), {})
        saved = sys.modules.get("agents.tiktok_content_agent")
        sys.modules["agents.tiktok_content_agent"] = stub
        import agents
        saved_attr = getattr(agents, "tiktok_content_agent", None)
        agents.tiktok_content_agent = stub
        try:
            tu._advance(27, job, 0)
        finally:
            if saved is not None:
                sys.modules["agents.tiktok_content_agent"] = saved
            else:
                sys.modules.pop("agents.tiktok_content_agent", None)
            if saved_attr is not None:
                agents.tiktok_content_agent = saved_attr
            else:
                delattr(agents, "tiktok_content_agent")
        self.assertEqual(self.failed, ["stuck"])

    def test_failed_steps_are_capped_over_the_jobs_life(self):
        job = self.job(attempts=1, total_attempts=tu.MAX_TOTAL_ATTEMPTS - 1, restarts=2)
        tu._retry_later(job, RuntimeError("chunk 502"))
        self.assertEqual(len(self.failed), 1)
        self.assertLess(tu.MAX_TOTAL_ATTEMPTS, (tu.MAX_RESTARTS + 1) * tu.MAX_ATTEMPTS)

    def test_loose_jobs_only(self):
        self.job()
        self.job(id="j2", proposal_id=4)
        self.job(id="j3", status="done")
        self.assertEqual([j["id"] for j in tu.loose_active(27)], ["j1"])
        self.assertEqual(tu.stop_client_jobs(27), {"stopped": 1, "processing": 0})


class TheScreens(unittest.TestCase):
    def test_client_card_has_a_stop_button(self):
        html = _src("dashboard", "client", "index.html")
        self.assertIn("stop-run", html)
        self.assertIn("decision: 'stop'", html)
        self.assertIn("action_stuck_badge", html)
        for key in ("action_stop:", "action_stopping:", "action_stuck_badge:"):
            line = next(l for l in html.splitlines() if l.strip().startswith(key))
            for lang in LANGS:
                self.assertIn(f"{lang}:'", line, (key, lang))

    def test_itzik_stops_between_steps(self):
        src = _src("core", "operator_agent.py")
        self.assertIn("BETWEEN tools only", src)
        self.assertIn("def stop_line", src)
        self.assertIn("parts.stop.hidden = !on", _src("dashboard", "assets", "itzik-chat.js"))


if __name__ == "__main__":
    unittest.main()
