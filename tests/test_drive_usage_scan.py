"""The Shared-Drive storage scan is schedulable and alerts on status change.

- Two doors, one scan: POST /api/admin/drive/usage-scan (admin cookie, the
  dashboard button) and GET /api/drive/usage-scan (X-Admin-Key, the daily
  Cloud Scheduler job — a cookie-guarded route would answer the job 401).
- DRIVE_SHARED_ALERT_GB alerts ONCE per client per status change (crossing
  above, and back under), not on every daily run.
- The stored status (`drive_usage:<id>`) is cleared on client deletion and
  covered by the deletion guard.
- The scheduler script reads the admin key from Cloud Run and never prints it.

Stdlib only. Run from the repo root:  python -I -B tests/test_drive_usage_scan.py -v
"""
import contextlib
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core                                  # noqa: E402
from core import drive_storage as ds         # noqa: E402

GB = 1024 ** 3


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8", newline="") as f:
        return f.read()


class StatusChangeAlerts(unittest.TestCase):
    def setUp(self):
        self.sizes, self.stored, self.alerts = {}, {}, []
        self.complete = True
        mirror = types.ModuleType("core.drive_mirror")
        mirror.shared_client_root = lambda cid: f"root{cid}"
        self.saved_modules = (sys.modules.get("core.drive_mirror"), getattr(core, "drive_mirror", None))
        sys.modules["core.drive_mirror"], core.drive_mirror = mirror, mirror
        self.saved = {n: getattr(ds, n) for n in ("folder_usage", "location", "as_service_account",
                                                   "_usage_status", "_save_usage_status", "_usage_alert")}
        ds.folder_usage = lambda root: {"bytes": self.sizes[root], "files": 3, "complete": self.complete}
        ds.location = lambda cid: {"kind": ds.SHARED}
        ds.as_service_account = contextlib.nullcontext
        ds._usage_status = lambda cid: (self.stored.get(cid) or {}).get("status", "")
        ds._save_usage_status = lambda cid, status, size: self.stored.__setitem__(cid, {"status": status,
                                                                                        "bytes": size})
        ds._usage_alert = self.alerts.append
        os.environ["DRIVE_SHARED_ALERT_GB"] = "10"

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(ds, name, fn)
        module, attr = self.saved_modules
        if module is None:
            sys.modules.pop("core.drive_mirror", None)
        else:
            sys.modules["core.drive_mirror"] = module
        if attr is None:
            delattr(core, "drive_mirror")
        else:
            core.drive_mirror = attr
        os.environ.pop("DRIVE_SHARED_ALERT_GB", None)

    def scan(self, gb):
        self.sizes["root7"] = int(gb * GB)
        return ds.shared_usage_scan([7])[0]

    def test_daily_runs_over_the_threshold_alert_once(self):
        self.assertTrue(self.scan(12)["alerted"])
        self.assertFalse(self.scan(12.5)["alerted"])
        self.assertFalse(self.scan(13)["alerted"])
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("client 7", self.alerts[0])          # the Control Room matching convention
        self.assertIn("DRIVE_SHARED_ALERT_GB", self.alerts[0])
        self.assertEqual(self.stored[7]["status"], "over")

    def test_back_under_alerts_once_resolved_then_a_new_crossing_alerts_again(self):
        self.scan(12)
        self.assertTrue(self.scan(4)["alerted"])
        self.assertIn("resolved", self.alerts[-1])
        self.assertFalse(self.scan(3)["alerted"])
        self.assertTrue(self.scan(11)["alerted"])
        self.assertEqual(len(self.alerts), 3)

    def test_first_ever_status_under_is_silent(self):
        self.assertFalse(self.scan(1)["alerted"])
        self.assertEqual(self.alerts, [])
        self.assertEqual(self.stored[7]["status"], "under")

    def test_an_incomplete_walk_below_the_threshold_proves_nothing(self):
        self.scan(12)
        self.complete = False
        row = self.scan(2)                        # walk cut short: maybe more files
        self.assertFalse(row["alerted"])
        self.assertEqual(self.stored[7]["status"], "over")
        self.assertEqual(len(self.alerts), 1)

    def test_an_incomplete_walk_already_over_still_counts(self):
        self.complete = False
        self.assertTrue(self.scan(15)["alerted"])

    def test_unreadable_status_never_breaks_the_scan(self):
        ds._usage_status = lambda cid: (_ for _ in ()).throw(RuntimeError("db down"))
        row = self.scan(12)
        self.assertFalse(row["alerted"])
        self.assertEqual(row["status_error"], "RuntimeError")


class Routes(unittest.TestCase):
    def setUp(self):
        self.src = _src("core", "api_server.py")

    def block(self, decorator):
        return self.src.split(decorator, 1)[1].split("\n@app.", 1)[0]

    def test_cron_twin_uses_the_admin_key(self):
        self.assertIn('@app.get("/api/drive/usage-scan", dependencies=_admin_only)', self.src)
        cron = self.block('@app.get("/api/drive/usage-scan", dependencies=_admin_only)')
        self.assertIn("_drive_usage_scan()", cron)
        self.assertNotIn("_require_admin(request)", cron)

    def test_dashboard_button_keeps_the_cookie(self):
        button = self.block('@app.post("/api/admin/drive/usage-scan")')
        self.assertIn("_require_admin(request)", button)
        self.assertIn("_drive_usage_scan()", button)

    def test_same_pattern_as_the_other_scheduled_scans(self):
        for route in ('@app.get("/api/monitor/scan", dependencies=_admin_only)',
                      '@app.get("/api/leads/volume-scan", dependencies=_admin_only)'):
            self.assertIn(route, self.src)


class StoredStatusLifecycle(unittest.TestCase):
    def test_cleared_on_deletion_and_guarded(self):
        self.assertEqual(ds.USAGE_PREFIX, "drive_usage:")
        self.assertIn('f"drive_usage:{int(i)}"', _src("core", "client_deletion.py"))
        self.assertIn('"drive_usage:"', _src("core", "deletion_guard.py"))
        self.assertIn('"drive_usage:"', _src("core", "admin_service.py"))


class SchedulerScript(unittest.TestCase):
    def setUp(self):
        self.script = _src("scripts", "schedule_drive_usage_scan.sh")

    def test_daily_in_jerusalem_with_the_admin_key_header(self):
        self.assertIn('SCHEDULE="30 3 * * *"', self.script)
        self.assertIn('TZ_NAME="Asia/Jerusalem"', self.script)
        self.assertIn('URL="https://app.uallak.com/api/drive/usage-scan"', self.script)
        self.assertIn("--http-method=GET", self.script)
        self.assertIn('--update-headers="X-Admin-Key=$AK"', self.script)

    def test_key_read_from_cloud_run_and_never_printed(self):
        self.assertIn("gcloud run services describe", self.script)
        self.assertIn('e.get("name") == "ADMIN_KEY"', self.script)
        self.assertIn("unset AK", self.script)
        for line in self.script.splitlines():
            if line.strip().startswith(("echo", "printf")):
                self.assertNotIn("$AK", line, line)

    def test_create_or_update_so_it_can_be_rerun(self):
        self.assertIn('ACTION="update"', self.script)
        self.assertIn('ACTION="create"', self.script)

    def test_lf_line_endings(self):
        self.assertNotIn("\r\n", self.script)


if __name__ == "__main__":
    unittest.main()
