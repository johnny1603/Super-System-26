"""Intake times measured and enforced (2026-11-28, core/intake_sla).

- every proposal build is timed; a slow or lost one alerts the owner once;
- a brief draft not created within the hour, and a draft waiting for approval
  more than a day, alert once each — read from the brief's own record;
- the report states how many samples each figure rests on, never a number
  from nothing; a measurement never breaks the sale it measures.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from core import intake_sla as sla  # noqa: E402

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)


def ago(**delta):
    return (NOW - timedelta(**delta)).isoformat()


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = (sla._read_store, sla._write_store, sla._brief_rows, sla._alert, sla.time.monotonic)
        self.store, self.briefs, self.alerts, self.clock = {}, [], [], [0.0]
        sla._read_store = lambda: {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in self.store.items()}
        sla._write_store = lambda value: (self.store.clear(), self.store.update(value))
        sla._brief_rows = lambda: self.briefs
        sla._alert = lambda lines: self.alerts.extend(lines)
        sla.time.monotonic = lambda: self.clock[0]
        sla._last_sweep = 0.0

    def tearDown(self):
        sla._read_store, sla._write_store, sla._brief_rows, sla._alert, sla.time.monotonic = self.saved


class TheProposal(Base):
    def build(self, seconds, fail=False):
        with sla.proposal("lead-1"):
            self.clock[0] += seconds
            if fail:
                raise RuntimeError("model down")

    def test_every_build_is_timed_and_a_fast_one_is_silent(self):
        self.build(95)
        row = self.store["proposals"][0]
        self.assertEqual((row["seconds"], row["ok"], row["lead"]), (95.0, True, "lead-1"))
        self.assertEqual(self.alerts, [])

    def test_past_the_line_the_owner_is_told(self):
        self.build(sla.PROPOSAL_ALERT_SECONDS + 20)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("260 seconds", self.alerts[0])
        self.assertIn("finished", self.alerts[0])

    def test_a_failed_build_is_recorded_and_its_error_is_untouched(self):
        with self.assertRaises(RuntimeError):
            self.build(30, fail=True)
        self.assertEqual((self.store["proposals"][0]["ok"], self.store["proposals"][0]["seconds"]), (False, 30.0))

    def test_a_broken_store_never_breaks_the_sale(self):
        def down():
            raise RuntimeError("database")
        sla._read_store = down
        self.build(10)                                                           # no exception
        self.build(sla.PROPOSAL_ALERT_SECONDS + 1)
        self.assertEqual(len(self.alerts), 1)                                    # the alert does not depend on the store

    def test_a_build_that_never_came_back(self):
        self.store["proposals"] = [{"id": "p1", "started_at": ago(minutes=30), "lead": "L"},
                                   {"id": "p2", "started_at": ago(minutes=1), "lead": "L"}]
        out = sla.sweep(NOW, force=True)
        self.assertEqual(out["alerted"], ["proposal:p1"])
        self.assertIn("never reported back", self.alerts[0])
        self.assertTrue(self.store["proposals"][0]["lost"])
        sla.sweep(NOW, force=True)
        self.assertEqual(len(self.alerts), 1)                                    # once


class TheBrief(Base):
    def test_a_draft_not_created_within_the_hour(self):
        self.briefs = [{"key": "brand_brief:41", "value": {"draft_job": {"state": "queued", "queued_at": ago(minutes=40), "attempts": 1}}},
                       {"key": "brand_brief:42", "value": {"draft_job": {"state": "failed", "queued_at": ago(hours=3), "attempts": 3}}}]
        out = sla.sweep(NOW, force=True)
        self.assertEqual(len(out["alerted"]), 1)
        self.assertIn("client 42", self.alerts[0])
        self.assertIn("does not exist", self.alerts[0])
        sla.sweep(NOW, force=True)
        self.assertEqual(len(self.alerts), 1)

    def test_a_draft_waiting_for_approval_more_than_a_day(self):
        draft = {"v": 1, "kind": "drafted", "status": "draft", "at": ago(hours=30)}
        self.briefs = [{"key": "brand_brief:41", "value": {"draft_job": {"state": "done", "queued_at": ago(hours=30, minutes=3),
                                                                         "finished_at": ago(hours=30)}, "versions": [draft]}},
                       {"key": "brand_brief:43", "value": {"versions": [{"v": 1, "status": "draft", "at": ago(hours=5)}]}}]
        sla.sweep(NOW, force=True)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("client 41", self.alerts[0])
        self.assertIn("waiting for your approval", self.alerts[0])
        sla.sweep(NOW, force=True)
        self.assertEqual(len(self.alerts), 1)
        # approved: measured, and the mark is forgotten so a later wait alerts again
        self.briefs[0]["value"]["versions"].append({"v": 2, "kind": "approved", "status": "approved", "at": ago(hours=2)})
        sla.sweep(NOW, force=True)
        self.assertNotIn("approval:41", self.store["alerted"])
        times = {b["client_id"]: b for b in sla.brief_times(now=NOW)}
        self.assertEqual((times[41]["draft_seconds"], times[41]["approval_hours"], times[41]["waiting_hours"]), (180.0, 28.0, None))
        self.assertEqual(times[43]["waiting_hours"], 5.0)

    def test_the_sweep_is_cheap_on_the_minute_tick(self):
        self.clock[0] = 1000.0
        self.assertNotIn("skipped", sla.sweep(NOW))
        self.clock[0] += 60
        self.assertEqual(sla.sweep(NOW), {"skipped": "recent"})
        self.clock[0] += sla.SWEEP_EVERY_S
        self.assertNotIn("skipped", sla.sweep(NOW))


class TheReport(Base):
    def test_figures_carry_their_sample_count_and_nothing_is_invented(self):
        empty = sla.report(now=NOW)
        self.assertEqual((empty["proposal"]["n"], empty["brief_draft"], empty["measured_since"]),
                         (0, {"n": 0, "due_hours": 1, "overdue": []}, None))
        self.assertNotIn("median", empty["proposal"])
        self.store["proposals"] = [{"id": str(n), "started_at": ago(days=n), "seconds": s, "ok": ok}
                                   for n, (s, ok) in enumerate([(100, True), (140, True), (300, True), (50, False), (120, True)])]
        self.store["proposals"].append({"id": "old", "started_at": ago(days=200), "seconds": 999, "ok": True})
        out = sla.report(now=NOW)["proposal"]
        self.assertEqual((out["n"], out["median"], out["max"], out["failed"], out["over_alert_line"]), (4, 130.0, 300, 1, 1))

    def test_wiring(self):
        server = _read("core", "api_server.py")
        self.assertIn("with intake_sla.proposal(req.lead_id):", server)
        self.assertIn('data["intake_sla"] = intake_sla.sweep()', server)
        route = '@app.get("/api/admin/intake-times")'
        self.assertIn("_require_admin(request)", server[server.index(route):][:500])
        self.assertIn('"intake_sla"', _read("core", "admin_service.py"))
        self.assertIn("intakeTimesOut", _read("dashboard", "admin", "connections", "index.html"))
        src = _read("core", "intake_sla.py")
        self.assertNotIn("brand_brief.", src[src.index("# ─── Doors"):])      # the brief's record is read, never written


if __name__ == "__main__":
    unittest.main()
