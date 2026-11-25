"""Tests never write under data/ (2026-11-26, core/local_data).

data/alert_history.json was committed holding nothing but test output
(68 lines written in three minutes on one machine: "client 7 … bad_file",
"client 27 … No module named 'supabase'"), and later runs added "client
999999" and "knowledge/nope.md". It read like real history. A test process
now gets a temp folder; the runner fails if data/ changed; and an item that
does not reach the approval queue is never counted as produced.
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import local_data                    # noqa: E402

REAL = os.path.join(ROOT, "data")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _inside(path: str, folder: str) -> bool:
    return os.path.abspath(path).lower().startswith(os.path.abspath(folder).lower() + os.sep)


class NeverUnderData(unittest.TestCase):
    def test_a_test_process_gets_a_temp_folder(self):
        self.assertTrue(local_data.under_test())
        self.assertFalse(_inside(local_data.path("alert_history.json"), REAL))
        self.assertEqual(local_data.data_dir(), local_data.data_dir())            # one folder per process

    def test_an_alert_raised_by_a_test_stays_out_of_the_real_history(self):
        from agents import master_agent
        self.assertFalse(_inside(master_agent.ALERT_HISTORY_PATH, REAL))
        real = os.path.join(REAL, "alert_history.json")
        before = os.stat(real).st_mtime_ns if os.path.exists(real) else None
        saved = master_agent._persist_alert
        master_agent._persist_alert = lambda label, issues: None                   # no database here
        try:
            master_agent.alert("test_local_data", ["client 999999: a test alert"])
        finally:
            master_agent._persist_alert = saved
        with open(master_agent.ALERT_HISTORY_PATH, encoding="utf-8") as f:
            self.assertEqual(json.load(f)[-1]["label"], "test_local_data")
        self.assertEqual(os.stat(real).st_mtime_ns if os.path.exists(real) else None, before)

    def test_every_local_file_goes_through_the_one_resolver(self):
        for folder in ("agents", "core"):
            for name in sorted(os.listdir(os.path.join(ROOT, folder))):
                if not name.endswith(".py") or name == "local_data.py":
                    continue
                src = _src(folder, name)
                for bad in ('BASE_DIR, "data"', "BASE_DIR, 'data'", '"data/', "'/app/data"):
                    self.assertNotIn(bad, src, f"{folder}/{name} builds a path under data/ itself — use core/local_data")
        for name in ("master_agent.py", "monitor_agent.py", "architect_agent.py"):
            self.assertIn("local_data.", _src("agents", name))

    def test_production_resolves_to_the_real_folder_and_an_operator_can_move_it(self):
        self.assertEqual(local_data.REAL_DIR, REAL)
        os.environ[local_data.ENV] = os.path.join(local_data.data_dir(), "elsewhere")
        try:
            self.assertTrue(local_data.data_dir().endswith("elsewhere"))
        finally:
            del os.environ[local_data.ENV]

    def test_the_runner_fails_when_data_changed_and_git_does_not_track_it(self):
        runner = _src("scripts", "run_tests.py")
        self.assertIn("the suite wrote under data/", runner)
        self.assertIn("before = _data_state()", runner)
        self.assertIn("data/", _src(".gitignore").splitlines())
        self.assertIn("data/", _src(".dockerignore").splitlines())               # never part of the image either


class Supabase(unittest.TestCase):
    """"No module named 'supabase'" was a test machine, not Cloud Run."""

    def test_it_is_pinned_and_installed_into_the_image(self):
        self.assertRegex(_src("requirements.txt"), r"(?m)^supabase==\d")
        docker = _src("Dockerfile")
        self.assertIn("COPY requirements.txt .", docker)
        self.assertIn("pip install --no-cache-dir -r requirements.txt", docker)

    def test_the_server_cannot_start_without_it(self):
        # imported at the top of the module uvicorn loads: a missing package stops
        # the revision at startup — it can never run and "silently" skip the queue
        api = _src("core", "api_server.py")
        self.assertRegex(api, r"(?m)^from supabase import create_client")
        self.assertIn('CMD ["uvicorn", "core.api_server:app"', _src("Dockerfile"))

    def test_an_item_that_did_not_reach_the_queue_is_never_made(self):
        src = _src("core", "post_pipeline.py")
        self.assertIn('if not added.get("id"):', src)
        self.assertIn('results.append({"asset_id": brief.asset_id, "status": "not_queued"})', src)


if __name__ == "__main__":
    unittest.main()
