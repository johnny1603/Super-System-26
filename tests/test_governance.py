"""Client governance, wired (2026-11-26, knowledge/client-governance.md).

The doctrine is in every agent's prompt; authority comes from an APPROVED
brief; every work item carries its lane from creation and the lanes are never
blended; an override is logged with what the client read; the weekly sync is
presented on the client's evening, a silent week reaches the owner (who can
approve on the client's behalf) and the training account is exempt; credits
are shown by lane and never gate anything.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import test_social_queue as tsq                # noqa: E402

from core import governance as gov             # noqa: E402
from core import social_queue as sq            # noqa: E402

ROOT = os.path.dirname(HERE)
IL = timezone(timedelta(hours=2))
THU_EVENING = datetime(2026, 11, 26, 17, 0, tzinfo=timezone.utc)      # Thursday 19:00 in Israel
THU_NOON = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
CID = 5


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(tsq.Base):
    def setUp(self):
        super().setUp()
        self.alerts, self.chat, self.emails = [], [], []
        self.brief = {"status": "approved"}
        self.costs = []
        patches = {"_db": lambda: self.db, "_tz": lambda: IL, "_alert": self.alerts.extend,
                   "_post_chat": lambda cid, body: self.chat.append((cid, body)) or True,
                   "_email_owner": lambda subject, body: self.emails.append((subject, body)) or True,
                   "_lang": lambda cid: "he", "_client_name": lambda cid: f"biz {cid}",
                   "_targets": lambda cid: [], "_campaigns": lambda cid: None,
                   "_cost_rows": lambda cid, since: list(self.costs),
                   "_package_credits": lambda cid: None,
                   "exempt_from_escalation": lambda cid: int(cid) in gov.TRAINING_ACCOUNTS,
                   "authority": lambda cid: {"mode": "decide" if self.brief["status"] == "approved" else "ask",
                                             "brief_status": self.brief["status"]}}
        for name, value in patches.items():
            self.saved.append((gov, name, getattr(gov, name)))
            setattr(gov, name, value)


class TheDoctrineReachesEveryAgent(unittest.TestCase):
    def test_the_file_is_loaded_in_full(self):
        text = gov.text()
        self.assertGreater(len(text), 3000)
        prompt = gov.with_rules("SYSTEM")
        self.assertTrue(prompt.startswith("SYSTEM"))
        self.assertIn(text, prompt)
        self.assertIn("override_log", prompt)

    def test_every_chat_agent_and_the_content_path_load_it(self):
        agent = _src("agents", "support_agent.py")
        self.assertIn("cached_system(_with_governance(SYSTEM + manager_line, light))", agent)
        self.assertIn("    system = _with_governance(system, light)\n", agent.replace("\r\n", "\n"))
        self.assertIn('payload["governance"] = governance.prompt_block(client_id)', agent)
        self.assertEqual(_src("agents", "media_agent.py").count('"client_governance": context.get("governance", "")'), 4)
        brief = _src("core", "post_brief.py")
        self.assertIn("raise ContextMissing([os.path.relpath(governance.PATH, ROOT)", brief)     # missing = loud


class Authority(unittest.TestCase):
    def test_no_approved_brief_means_ask(self):
        from core import brand_brief
        real = brand_brief.current
        try:
            for status, mode in (("approved", "decide"), ("draft", "ask"), ("missing", "ask")):
                brand_brief.current = lambda cid, s=status: {"status": s}
                self.assertEqual(gov.authority(27), {"mode": mode, "brief_status": status})

            def broken(cid):
                raise RuntimeError("db")
            brand_brief.current = broken
            self.assertEqual(gov.authority(27)["mode"], "ask")
        finally:
            brand_brief.current = real


class Lanes(Base):
    def test_the_lane_is_recorded_at_creation(self):
        asked = self.add(source="chat")
        planned = self.add(file_id="B" * 28, source="chat", plan_ref={"plan_id": "p1", "index": 0})
        declared = self.add(file_id="C" * 28, source="chat", lane="system")
        self.assertEqual([i["lane"] for i in (asked, planned, declared)], ["client", "system", "system"])
        with gov.lane_scope("system"):
            inherited = self.add(file_id="D" * 28, source="slide")
        self.assertEqual(inherited["lane"], "system")
        self.assertEqual(gov.current_lane(), "")
        self.assertEqual(sq.get(CID, asked["id"])["lane"], "client")              # stored, not derived

    def test_an_older_item_is_unrecorded_never_guessed(self):
        item = self.add(source="chat")
        queue = sq._load(CID)
        queue[0].pop("lane")
        sq._save(CID, queue)
        self.assertEqual(gov.lane_of(sq.get(CID, item["id"])), gov.UNRECORDED)
        report = gov.lanes_report(CID, 7)
        self.assertEqual((report["unrecorded"]["created"], report["client"]["created"]), (1, 0))

    def test_the_report_keeps_the_lanes_apart(self):
        self.add(source="chat")
        self.add(file_id="B" * 28, source="chat")
        self.add(file_id="C" * 28, plan_ref={"plan_id": "p1", "index": 0})
        report = gov.lanes_report(CID, 7)
        self.assertEqual((report["system"]["created"], report["client"]["created"]), (1, 2))
        self.assertFalse({"total", "all", "created"} & set(report))               # no blended number exists
        lines = gov.sync_lines({"lanes": report, "credits": {"readable": True, "system": 3, "client": 1}}, "en")
        self.assertEqual(len([ln for ln in lines if "lane" in ln.lower() and ln.endswith(":")]), 2)

    def test_the_systems_target_counts_the_system_lane_only(self):
        from core import quarter_goals as qg
        self.add(kind="image", channels=("instagram",), source="chat")                      # client lane
        self.add(file_id="B" * 28, kind="image", channels=("instagram",), plan_ref={"plan_id": "p", "index": 0})
        real = qg._count_activity
        qg._count_activity = lambda cid, actions, days: 9
        try:
            self.assertEqual(qg._posts_week(CID), 2.0)                             # (9 - 1 client piece) / 4
        finally:
            qg._count_activity = real

    def test_creators_and_costs_carry_the_lane(self):
        self.assertIn('with governance.lane_scope("system"):', _src("core", "content_team.py"))
        actions = _src("core", "agent_actions.py")
        self.assertIn('with governance.lane_scope("client"):', actions)
        self.assertIn('replaces=piece.get("replaces", ""), lane="client")', actions)
        self.assertIn('with governance.lane_scope(item.get("lane")):', _src("core", "post_pipeline.py"))
        self.assertIn('enriched["lane"] = governance.current_lane()', _src("core", "cost_tracker.py"))


class Overrides(Base):
    RAW = {"topic": "Palette", "recommendation": "להישאר עם הצבעים של התדריך", "decision": "ורוד בכל הפוסטים"}

    def test_logged_with_the_line_the_client_reads(self):
        reply = gov.note_override(CID, self.RAW, "עושים בדרך שלכם.", "media", "he")
        rows = gov.overrides(CID)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["topic"], rows[0]["agent"], rows[0]["at"][:4]), ("palette", "media", str(datetime.now().year)))
        self.assertIn(self.RAW["recommendation"], reply)
        self.assertTrue(reply.startswith("עושים בדרך שלכם."))
        self.assertEqual(gov.prompt_block(CID)["settled"][0]["topic"], "palette")    # never argued twice

    def test_not_stored_means_not_claimed(self):
        self.db.broken = True
        reply = gov.note_override(CID, self.RAW, "עושים בדרך שלכם.", "media", "he")
        self.assertEqual(reply, "עושים בדרך שלכם.")
        self.assertTrue(self.alerts)

    def test_an_incomplete_entry_logs_nothing(self):
        self.assertEqual(gov.note_override(CID, {"topic": "x", "recommendation": "", "decision": "y"}, "r", "media", "he"), "r")
        self.assertEqual(gov.overrides(CID), [])

    def test_the_week_shows_them(self):
        gov.log_override(CID, "one reel a week", "three reels a week", "cadence", "media")
        lines = gov.sync_lines(gov.build_sync(CID, THU_EVENING), "en")
        self.assertTrue(any("one reel a week" in ln and "three reels a week" in ln for ln in lines))

    def test_the_chat_runs_it_before_the_other_guards(self):
        agent = _src("agents", "support_agent.py")
        self.assertIn("    reply = _note_override(client_id, persona_id, reply, result)", agent)


class WeeklySync(Base):
    def test_due_only_on_the_clients_evening_once_a_week(self):
        self.assertFalse(gov.is_due(CID, THU_NOON))
        self.assertTrue(gov.is_due(CID, THU_EVENING))
        gov.set_sync_day(CID, "sat")
        self.assertFalse(gov.is_due(CID, THU_EVENING))
        self.assertTrue(gov.is_due(CID, THU_EVENING + timedelta(days=2)))
        with self.assertRaises(ValueError):
            gov.set_sync_day(CID, "mon")

    def test_presented_once_and_approved_by_the_client(self):
        self.add(source="chat")
        first = gov.present(CID, THU_EVENING)
        again = gov.present(CID, THU_EVENING + timedelta(hours=1))
        self.assertEqual((first["state"], again["week"], len(self.chat)), ("waiting", first["week"], 1))
        self.assertFalse(gov.is_due(CID, THU_EVENING + timedelta(hours=1)))
        view = gov.client_view(CID, "he")
        self.assertEqual(view["sync"]["state"], "waiting")
        self.assertTrue(view["sync"]["lines"])
        self.assertEqual(gov.approve_sync(CID)["approved_by"], "client")
        self.assertEqual(sq.items(CID)[0]["status"], "waiting")                     # approving the week publishes nothing

    def test_a_silent_client_reaches_the_owner_once(self):
        gov.present(CID, THU_EVENING)
        self.assertFalse(gov.escalate_if_silent(CID, THU_EVENING + timedelta(hours=5)))
        self.assertTrue(gov.escalate_if_silent(CID, THU_EVENING + timedelta(hours=25)))
        self.assertFalse(gov.escalate_if_silent(CID, THU_EVENING + timedelta(hours=40)))
        self.assertEqual((len(self.emails), len(self.alerts)), (1, 1))
        self.assertIn(f"client {CID}", self.alerts[0])
        self.assertEqual(gov.state(CID)["sync"]["state"], "waiting")                # silence approves nothing
        sync = gov.approve_sync(CID, "owner:Johnny")
        self.assertTrue(gov.client_view(CID)["sync"]["by_owner"])
        self.assertEqual(sync["approved_by"], "owner:Johnny")

    def test_an_approved_week_is_never_escalated(self):
        gov.present(CID, THU_EVENING)
        gov.approve_sync(CID)
        self.assertFalse(gov.escalate_if_silent(CID, THU_EVENING + timedelta(days=3)))
        self.assertEqual(self.emails, [])

    def test_the_training_account_is_exempt(self):
        gov.present(27, THU_EVENING)
        self.assertFalse(gov.escalate_if_silent(27, THU_EVENING + timedelta(days=3)))
        self.assertEqual((self.emails, self.alerts), ([], []))
        self.assertIn(27, gov.TRAINING_ACCOUNTS)

    def test_the_tick_survives_one_clients_failure(self):
        self.saved.append((gov, "_clients", gov._clients))
        self.saved.append((gov, "present", gov.present))
        gov._clients = lambda: [{"id": 5}, {"id": 6}]
        real = gov.present

        def flaky(cid, now=None):
            if cid == 5:
                raise RuntimeError("boom")
            return real(cid, now)
        gov.present = flaky
        out = gov.run_due(THU_EVENING)
        self.assertEqual((out["failed"], out["presented"]), ([5], [6]))

    def test_the_job_and_the_routes_exist(self):
        self.assertIn('job governance-weekly-sync GET "5 9,18 * * *" "/api/governance/weekly-sync" active',
                      _src("scripts", "schedule_jobs.sh"))
        api = _src("core", "api_server.py")
        self.assertIn('@app.get("/api/governance/weekly-sync", dependencies=_admin_only)', api)
        self.assertIn('@app.post("/api/admin/governance/{client_id}/approve-sync")', api)
        self.assertIn("crGovApprove", _src("dashboard", "admin", "index.html"))


class Credits(Base):
    def rows(self):
        now = datetime.now(timezone.utc).isoformat()
        return [{"details": {"credits": 6, "lane": "system"}, "created_at": now},
                {"details": {"credits": 3, "lane": "client"}, "created_at": now},
                {"details": {"credits": 2}, "created_at": now},
                {"details": {"credits": None, "lane": "client"}, "created_at": now}]

    def test_by_lane_and_no_balance_without_a_package(self):
        self.costs = self.rows()
        c = gov.credits(CID)
        self.assertEqual((c["system"], c["client"], c["unrecorded"]), (6.0, 3.0, 2.0))
        self.assertEqual((c["package"], c["left"], c["days_left"]), (None, None, None))     # "not set", never zero

    def test_what_is_left_and_how_long_it_lasts(self):
        self.costs = self.rows()
        gov._package_credits = lambda cid: 100
        c = gov.credits(CID)
        self.assertEqual((c["package"], c["left"]), (100, 89.0))
        self.assertIsInstance(c["days_left"], int)

    def test_over_the_package_is_shown_not_blocked(self):
        self.costs = self.rows()
        gov._package_credits = lambda cid: 5
        c = gov.credits(CID)
        self.assertEqual((c["left"], c["days_left"]), (-6.0, None))
        self.assertNotIn("raise", _src("core", "governance.py").split("def credits(")[1].split("\ndef ")[0])

    def test_unreadable_is_said_not_zeroed(self):
        def broken(cid, since):
            raise RuntimeError("db")
        gov._cost_rows = broken
        self.assertFalse(gov.credits(CID)["readable"])

    def test_on_the_main_page(self):
        html = _src("dashboard", "client", "index.html")
        for needle in ('id="govCard"', "gov_system_used", "gov_client_used", "gov_left", "gov_lasts", "gov_no_gate",
                       "loadGovernance();"):
            self.assertIn(needle, html)
        self.assertIn('"package_credits"', _src("core", "client_settings.py"))


if __name__ == "__main__":
    unittest.main()
