"""Running a client's characterisation again (2026-11-28, core/intake_reset).

- the plan reads and changes nothing; the reset needs the typed phrase;
- every removed row is copied first, and no copy = no delete;
- connections, keys and the brand brief are never written (source scan + a
  before/after comparison);
- the interview after a reset reads only what was said since, and never
  writes over a settled brand brief;
- a goals card never reports a sales business in enquiries, and the audit
  names every agreed target that contradicts the client's own result.
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from core import intake_reset as ir, quarter_goals as qg  # noqa: E402

DOORS = ("_activity_rows", "_setting", "_settings_like", "_open_cards", "_lead", "_accounts", "_exempt_services", "_readiness",
         "_delete_activity", "_delete_setting", "_write_setting", "_cancel_card", "_delete_lead", "_alert", "_result_kind")
GOALS = {"agreed_at": "2026-11-20T10:00:00+00:00", "improve": "יותר מכירות בחנות",
         "baseline": {"captured_at": "2026-11-28T09:00:00+00:00", "leads_30d": 0},
         "targets": [{"metric": "posts_per_week", "target": 3}, {"metric": "leads_30d", "target": 5}]}
BRIEF = {"versions": [{"v": n, "status": "approved" if n == 5 else "draft", "text": "x" * (100 + n)} for n in range(1, 6)]}


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = {n: getattr(ir, n) for n in DOORS}
        self.activity = {ir.DONE_ACTION: [{"id": 1, "created_at": "2026-10-01T08:00:00+00:00", "details": {}}],
                         ir.CAPTURE_ACTION: [{"id": 2, "created_at": "2026-10-01T08:01:00+00:00",
                                              "details": {"competitors": [{"name": "a"}], "notes": {"audience": "x"}}}]}
        self.settings = {"quarter_goals:27": dict(GOALS), "brand_brief:27": BRIEF, "pose_approvals:27": {"approved": ["p1"]},
                         "drive_folders:27": {"folders": ["f"]}, "target_inputs:27": {"inputs": {"budget_ils": 3000}}}
        self.accounts = [{"id": 10, "platform": "google_ads", "status": "active"}, {"id": 11, "platform": "youtube", "status": "active"},
                         {"id": 12, "platform": "higgsfield", "status": "active"}]
        self.cards = [{"id": 70, "status": "proposed", "created_at": "2026-11-28T09:00:00+00:00"}]
        self.lead, self.grant, self.kind = {}, ["google ads", "social"], "sales"
        self.status = {"resolved": True, "complete": True, "steps": [{"key": "google_ads"}], "connected": ["google_ads"], "missing": []}
        self.alerts, self.cancelled, self.deleted_leads, self.writes = [], [], [], []
        ir._activity_rows = lambda cid, action: list(self.activity.get(action, []))
        ir._setting = lambda key: self.settings.get(key)
        ir._settings_like = lambda prefix: [{"key": k, "value": v} for k, v in self.settings.items() if k.startswith(prefix)]
        ir._open_cards = lambda cid: [c for c in self.cards if c["id"] not in self.cancelled]
        ir._lead = lambda cid: self.lead
        ir._accounts = lambda cid: list(self.accounts)
        ir._exempt_services = lambda cid: self.grant
        ir._readiness = lambda cid: self.status
        ir._delete_activity = self._delete_activity
        ir._delete_setting = lambda key: self.settings.pop(key, None)
        ir._write_setting = self._write
        ir._cancel_card = lambda cid: self.cancelled.append(cid) or True
        ir._delete_lead = lambda lid: self.deleted_leads.append(lid)
        ir._alert = lambda lines: self.alerts.extend(lines)
        ir._result_kind = lambda cid: self.kind

    def _delete_activity(self, ids):
        for action in self.activity:
            self.activity[action] = [r for r in self.activity[action] if r["id"] not in ids]

    def _write(self, key, value):
        self.writes.append(key)
        self.settings[key] = value

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(ir, name, fn)


class ThePlan(Base):
    def test_it_lists_and_changes_nothing(self):
        before = repr((self.activity, self.settings, self.accounts))
        out = ir.plan(27)
        self.assertEqual(repr((self.activity, self.settings, self.accounts)), before)
        self.assertEqual((self.writes, self.cancelled, self.alerts), ([], [], []))
        d = out["delete"]
        self.assertEqual([r["id"] for r in d["characterised_mark"]], [1])
        self.assertEqual(d["captured_facts"][0]["notes"], ["audience"])
        self.assertEqual(d["agreed_goals"]["baseline_captured_at"], "2026-11-28T09:00:00+00:00")
        self.assertEqual([c["id"] for c in d["open_goals_card"]], [70])
        self.assertEqual(out["keep"]["brand_brief"], {"versions": 5, "v": 5, "status": "approved", "chars": 105})
        self.assertEqual(len(out["keep"]["connections"]), 3)
        self.assertIn("pose_approvals:27", out["keep"]["stored"])
        self.assertIn("target_inputs:27", out["keep"]["stored"])                 # record_target_inputs' answers stay
        self.assertNotIn("quarter_goals:27", out["keep"]["stored"])
        self.assertEqual(out["confirm_phrase"], "reset intake 27")
        self.assertFalse(out["nothing_to_reset"])
        self.assertEqual(out["contradiction"]["counted"], ["leads_30d"])         # a sales business with an enquiries target

    def test_whether_the_interview_can_open_and_what_stops_it(self):
        self.assertEqual(ir.plan(27)["intake_can_start"], {"ready": True, "connected": ["google_ads"]})
        self.status = {"resolved": False, "complete": False, "steps": []}
        self.assertEqual(ir.can_start(27)["reason"], "package_unresolved")
        self.status = {"resolved": True, "complete": False, "steps": []}
        self.assertEqual(ir.can_start(27)["reason"], "no_required_connections")
        self.status = {"resolved": True, "complete": False, "steps": [{"key": "website"}], "missing": ["website"]}
        self.assertEqual(ir.can_start(27), {"ready": False, "reason": "connections_missing", "missing": ["website"]})


class TheReset(Base):
    def test_nothing_without_the_typed_phrase(self):
        for wrong in ("", "yes", "reset intake 28", "RESET INTAKE 27"):
            with self.assertRaises(ir.ResetError) as caught:
                ir.run(27, wrong)
            self.assertEqual(caught.exception.code, "confirm")
        self.assertIn("quarter_goals:27", self.settings)
        self.assertEqual(len(self.activity[ir.DONE_ACTION]), 1)

    def test_it_removes_exactly_the_listed_things_and_keeps_a_copy(self):
        kept = ir.kept(27)
        out = ir.run(27, "reset intake 27")
        self.assertEqual(out["did"], {"characterised_mark": 1, "captured_facts": 1, "agreed_goals": True, "open_goals_card": 1,
                                      "sales_lead": False})
        self.assertEqual((self.activity[ir.DONE_ACTION], self.activity[ir.CAPTURE_ACTION]), ([], []))
        self.assertNotIn("quarter_goals:27", self.settings)
        self.assertEqual(self.cancelled, [70])                                   # cancelled, never deleted
        backup = self.settings["intake_reset:27"]["backup"]
        self.assertEqual(backup["agreed_goals"]["targets"][1], {"metric": "leads_30d", "target": 5})
        self.assertEqual(backup["characterised_mark"][0]["id"], 1)
        self.assertEqual(self.writes, ["intake_reset:27"])                       # the ONLY row written
        self.assertEqual(ir.kept(27), kept)                                      # connections, brief, poses, folders: identical
        self.assertTrue(out["kept_unchanged"])
        self.assertFalse(out["still_marked"])
        self.assertEqual(out["brand_brief"]["v"], 5)
        self.assertEqual(ir.since(27), out["reset_at"])
        self.assertEqual(len(self.alerts), 1)

    def test_no_copy_no_delete(self):
        def refuse(key, value):
            raise RuntimeError("down")
        ir._write_setting = refuse
        with self.assertRaises(ir.ResetError) as caught:
            ir.run(27, "reset intake 27")
        self.assertEqual(caught.exception.code, "backup_failed")
        self.assertIn("quarter_goals:27", self.settings)
        self.assertEqual(len(self.activity[ir.DONE_ACTION]), 1)
        self.assertEqual(self.cancelled, [])

    def test_the_lead_only_when_asked_and_only_when_the_package_does_not_rest_on_it(self):
        self.lead = {"id": "L1", "created_at": "2026-09-30T10:00:00+00:00", "answers": {"q": "a"}, "proposal": {"packages": []}}
        self.assertEqual(ir.plan(27)["delete"]["sales_lead"]["deleted_by_default"], False)
        ir.run(27, "reset intake 27")
        self.assertEqual(self.deleted_leads, [])
        self.grant = []                                                          # the package is read from the lead
        with self.assertRaises(ir.ResetError) as caught:
            ir.run(27, "reset intake 27", include_lead=True)
        self.assertEqual(caught.exception.code, "lead_carries_package")
        self.grant = ["social"]
        self.assertTrue(ir.run(27, "reset intake 27", include_lead=True)["did"]["sales_lead"])
        self.assertEqual(self.deleted_leads, ["L1"])
        self.assertEqual(self.settings["intake_reset:27"]["backup"]["sales_lead"]["id"], "L1")
        self.assertEqual(len(self.settings["intake_reset:27"]["earlier"]), 1)    # an earlier reset's copy is not lost

    def test_this_file_cannot_write_what_must_be_kept(self):
        src = _read("core", "intake_reset.py")
        code = src[src.index("# ─── Doors"):]
        writes = re.findall(r'table\("([a-z_]+)"\)\.(delete|update|upsert|insert)', code)
        self.assertEqual(sorted(set(writes)), [("agent_actions", "update"), ("app_settings", "delete"), ("app_settings", "upsert"),
                                               ("client_activity", "delete"), ("leads", "delete")])
        for forbidden in ("brand_brief.", "save_edit", "_disconnect_platform", "upsert_account", "add_account", "drive_service",
                          "secrets_vault", "get_client_secret", "pose_approvals.", "agent_characters"):
            self.assertNotIn(forbidden, code, forbidden)
        self.assertIn('select("id,platform,status")', code)                      # connections are read without a secret column
        # the two app_settings writes are the goals row (delete) and the reset's own record
        self.assertEqual(code.count("_delete_setting("), 2)
        self.assertIn('_delete_setting(f"{GOALS_PREFIX}{client_id}")', code)


class TheAudit(Base):
    def test_a_target_counted_against_the_stated_result(self):
        self.settings["quarter_goals:31"] = {**GOALS, "targets": [{"metric": "sales_30d", "target": 9}]}
        self.settings["quarter_goals:40"] = {"keywords": ["x"]}                  # never agreed: not a target
        out = ir.audit()
        self.assertEqual((out["checked"], out["clients"], out["with_result_target"]), (2, [27, 31], 2))
        self.assertEqual([(c["client_id"], c["stated_result"], c["counted"]) for c in out["contradictions"]], [(27, "sales", ["leads_30d"])])
        self.kind = "leads"
        self.assertEqual([c["client_id"] for c in ir.audit()["contradictions"]], [31])

        def unreadable(cid):
            raise RuntimeError("down")
        ir._result_kind = unreadable
        out = ir.audit()
        self.assertEqual((out["contradictions"], len(out["unknown"])), ([], 2))  # unknown is said, never guessed


class TheGoalsCard(unittest.TestCase):
    def setUp(self):
        self.saved = (qg._counts_sales, qg._count_leads, qg.current_value)
        qg._count_leads = lambda cid, a, b: 0

    def tearDown(self):
        qg._counts_sales, qg._count_leads, qg.current_value = self.saved

    def test_a_sales_business_is_never_reported_in_enquiries(self):
        value = {"improve": "יותר מכירות בחנות", "baseline": {"captured_at": "2026-11-28", "leads_30d": 0, "sales_30d": {"paid_orders": 4}}}
        qg._counts_sales = lambda cid: True
        qg.current_value = lambda cid, v, metric: 7 if metric == "sales_30d" else None
        step = qg._improve(27, value, "he")
        self.assertEqual(step["now"], "נמדד: 7 מכירות ב-30 הימים האחרונים")
        self.assertNotIn("פניות", step["now"] + step["next"])
        self.assertNotIn("פניות", qg._baseline_line(value, "he", 27))
        qg.current_value = lambda cid, v, metric: None                           # the store is unreadable: not a zero
        self.assertEqual(qg._improve(27, value, "he")["now"], qg.pick(qg.T["not_measured"], "he"))
        qg._counts_sales = lambda cid: None                                      # the definition itself is unreadable
        self.assertFalse(qg._improve(27, value, "he")["measured"])
        qg._counts_sales = lambda cid: False
        self.assertIn("פניות", qg._improve(27, value, "he")["now"])
        for key in ("improve_now_sales", "improve_next_sales"):
            self.assertEqual(set(qg.T[key]), {"he", "en", "fr", "ar", "ru"})


class TheInterview(unittest.TestCase):
    """The interview module needs the model client; its two rules are checked
    on stand-ins for it and on the source."""
    def setUp(self):
        self.added = []
        for name in ("anthropic", "supabase"):
            if name not in sys.modules:
                sys.modules[name] = types.ModuleType(name)
                self.added.append(name)
        sys.modules["anthropic"].Anthropic = getattr(sys.modules["anthropic"], "Anthropic", object)
        sys.modules["supabase"].create_client = getattr(sys.modules["supabase"], "create_client", lambda *a, **k: None)
        from agents import interview_agent
        self.ia = interview_agent
        self.saved = (interview_agent._reset_since, interview_agent._brief_is_settled, interview_agent.agent_alert,
                      interview_agent._log_activity)

    def tearDown(self):
        self.ia._reset_since, self.ia._brief_is_settled, self.ia.agent_alert, self.ia._log_activity = self.saved
        for name in self.added:
            sys.modules.pop(name, None)

    def test_only_what_was_said_since_the_reset_counts(self):
        from agents import client_agent
        rows = [{"direction": "inbound", "content": f"m{n}", "created_at": f"2026-11-{n:02d}T10:00:00+00:00"} for n in range(30, 0, -1)]
        kept = client_agent.get_communications
        client_agent.get_communications = lambda cid, limit=40, channel=None: rows
        try:
            self.ia._reset_since = lambda cid: ""
            self.assertEqual(len(self.ia._transcript(27)), self.ia.MAX_TURNS * 2)     # an old client: the limit is already full
            self.ia._reset_since = lambda cid: "2026-11-29T00:00:00+00:00"
            self.assertEqual([t["text"] for t in self.ia._transcript(27)], ["m29", "m30"])
        finally:
            client_agent.get_communications = kept

    def test_a_settled_brief_is_proposed_to_never_written(self):
        alerts, logged = [], []
        self.ia.agent_alert = lambda agent, lines: alerts.extend(lines)
        self.ia._log_activity = lambda *a, **k: logged.append(a)
        self.ia._brief_is_settled = lambda cid: True
        self.assertEqual(self.ia._store_never_say(27, {"never_say": "לא להגיד זול"}), 0)
        self.assertEqual(logged, [])
        self.assertIn("NOT written", alerts[0])
        self.assertIn("לא להגיד זול", alerts[0])

        def unreadable(cid):
            raise RuntimeError("down")
        self.ia._brief_is_settled = unreadable                                   # unknown = do not write
        self.assertEqual(self.ia._store_never_say(27, {"never_say": "לא להגיד זול"}), 0)
        src = _read("agents", "interview_agent.py")
        self.assertIn('.get("status") == "approved"', src)
        # the intake's automatic draft never replaces an existing brief either
        brief = _read("core", "brand_brief.py")
        self.assertIn('if value is None or value.get("versions") or _legacy(client_id):', brief)
        self.assertIn('return {"ok": False, "error": "exists"}', brief)

    def test_wiring(self):
        server = _read("core", "api_server.py")
        for route in ('@app.get("/api/admin/intake-reset/{client_id}")', '@app.post("/api/admin/intake-reset/{client_id}")',
                      '@app.get("/api/admin/targets/audit")'):
            self.assertIn("_require_admin(request)", server[server.index(route):][:700], route)
        for name in ("client_deletion.py", "deletion_guard.py", "admin_service.py"):
            self.assertIn("intake_reset:", _read("core", name), name)
        page = _read("dashboard", "admin", "connections", "index.html")
        for needle in ("irPlanBtn", "irRunBtn", "irAuditBtn", "confirm_phrase"):
            self.assertIn(needle, page, needle)


if __name__ == "__main__":
    unittest.main()
