"""Does the system know whether anything is being measured? (2026-11-28)

- 1.1 three states read from the account, stored, never collapsed; an
      unreadable or stale read is `unknown`, never a guess;
- 1.2 no optimisation decision runs unless the state is `measuring` — at the
      cycle, at the keyword lever and at the single write choke point, in LIVE mode;
- 1.3 "spent with ZERO conversions" is said only of a measuring account;
- 1.4 the state is on the Google page (5 languages) and on the admin side;
- 1.5 tags on the site with no imported conversion action is named;
- Part 2: the budget an existing client records is what get_forecast reads, and
      set_performance_goal is wired and refuses by name;
- Part 3: three connections with nothing behind them are never offered.
"""
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from core import client_speech, conversion_measurement as cm  # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def action(aid, name="Purchase", kind="WEBPAGE", counted=True):
    return {"conversionAction": {"id": aid, "name": name, "type": kind, "category": "PURCHASE", "status": "ENABLED",
                                 "includeInConversionsMetric": counted}}


def recorded(aid, conversions, everything=None, customer="111"):
    return {"segments": {"conversionAction": f"customers/{customer}/conversionActions/{aid}", "conversionActionName": "x"},
            "metrics": {"conversions": conversions, "allConversions": conversions if everything is None else everything}}


# The real agents with their outside dependencies stubbed (as tests/test_google_ads_quota)
_STUBBED = ("supabase", "httpx", "agents.keys_agent", "core.api_call_counters", "core.lead_tracking", "core.agent_base",
            "core.claude_json", "core.secrets_vault", "core.chat_fallback", "agents.google_ads_agent",
            "core.google_ads_service", "agents.budget_agent")


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def load_agents() -> dict:
    """Install the stubs and import the real agents; returns what to restore."""
    saved = {n: sys.modules.get(n) for n in _STUBBED}
    sys.modules.update({
        "supabase": _module("supabase", create_client=lambda *a: None),
        "httpx": _module("httpx", HTTPStatusError=Exception, post=None, get=None, Response=object, Request=object,
                         Client=object, TimeoutException=TimeoutError, TransportError=OSError),
        "agents.keys_agent": _module("agents.keys_agent", get_key=lambda name: ""),
        "core.api_call_counters": _module("core.api_call_counters", increment_call_counter=lambda *a, **k: 1),
        "core.lead_tracking": _module("core.lead_tracking", client_sales_lead=lambda **k: ({}, "not found")),
        "core.agent_base": _module("core.agent_base", agent_alert=lambda src, issues: None,
                                   log_step=lambda *a, **k: None, timed_step=lambda agent, step, fn: fn()),
        "core.claude_json": _module("core.claude_json", ClaudeJSONError=Exception, safe_claude_json_call=None),
        "core.secrets_vault": _module("core.secrets_vault", hydrate=lambda row: row),
        "core.chat_fallback": _module("core.chat_fallback", alert_once=lambda *a, **k: None),
    })
    for name in ("agents.google_ads_agent", "core.google_ads_service", "agents.budget_agent"):
        sys.modules.pop(name, None)
    return saved


def restore_agents(saved: dict):
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class Stored(unittest.TestCase):
    """The store, the connection, Google and the site side replaced."""
    NAMES = ("_read_store", "_write_store", "_connection", "_search", "_site_tracking")

    def setUp(self):
        self.saved = {n: getattr(cm, n) for n in self.NAMES}
        self.store, self.google, self.calls = {}, {"actions": [], "recorded": []}, []
        cm._read_store = lambda cid: self.store.get(cid, {})
        cm._write_store = lambda cid, value: self.store.__setitem__(cid, value)
        cm._connection = lambda cid: {"access_token": "t", "account_id": "111"}

        def search(conn, gaql):
            self.calls.append(gaql)
            return self.google["actions" if "FROM conversion_action" in gaql else "recorded"]
        cm._search = search
        cm._site_tracking = lambda cid: {"gtm_api_connected": False}
        cm._cache.clear()

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(cm, name, fn)
        cm._cache.clear()

    def put(self, state, hours_ago=1):
        self.store[27] = {"state": state, "checked_at": (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()}
        cm._cache.clear()


class ThreeStates(Stored):
    def test_never_collapsed(self):
        self.assertEqual(cm.classify([], [])["state"], "not_measured")
        self.assertEqual(cm.classify([action("1")], [])["state"], "configured_silent")
        self.assertEqual(cm.classify([action("1")], [recorded("1", 3)])["state"], "measuring")
        self.assertEqual(cm.STATES, ("measuring", "configured_silent", "not_measured"))

    def test_recorded_but_not_counted_is_still_silent(self):
        out = cm.classify([action("1", counted=False)], [recorded("1", 0, everything=7)])
        self.assertEqual(out["state"], "configured_silent")
        self.assertTrue(out["recorded_not_counted"])
        self.assertEqual((out["conversions_30d"], out["recorded_30d"]), (0, 7))

    def test_an_action_shared_from_another_account_counts(self):
        out = cm.classify([], [recorded("9", 4, customer="999")])
        self.assertEqual(out["state"], "measuring")
        self.assertFalse(out["actions"][0]["in_account"])

    def test_the_read_is_two_operations_and_is_stored(self):
        self.google = {"actions": [action("1")], "recorded": [recorded("1", 2)]}
        value = cm.refresh(27)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.store[27]["state"], "measuring")
        self.assertEqual(value["conversions_30d"], 2)
        self.assertEqual(cm.state(27), "measuring")

    def test_unreadable_or_stale_is_unknown_never_a_guess(self):
        self.assertEqual(cm.state(27), "unknown")                         # never read
        cm._search = lambda conn, gaql: (_ for _ in ()).throw(RuntimeError("PERMISSION_DENIED"))
        self.assertEqual(cm.refresh(27)["state"], "unknown")
        self.assertIn("PERMISSION_DENIED", self.store[27]["error"])
        self.put("measuring", hours_ago=cm.FRESH_HOURS + 1)
        self.assertEqual(cm.state(27), "unknown")                         # a state we read too long ago
        cm._connection = lambda cid: {}
        self.assertEqual(cm.refresh(27)["state"], "not_connected")

    def test_the_gate_names_the_state(self):
        for state in ("configured_silent", "not_measured", "unknown", "not_connected"):
            self.put(state)
            with self.assertRaises(cm.NotMeasuring) as caught:
                cm.require_measuring(27)
            self.assertEqual(caught.exception.state, state)
            self.assertIn("account_not_measuring", str(caught.exception))
        self.put("measuring")
        cm.require_measuring(27)


class SiteAgainstAccount(Stored):
    def test_tags_present_import_missing(self):
        measure = cm.classify([action("1", kind="WEBPAGE")], [])
        site = {"gtm_api_connected": True, "live_types": ["form_submit", "store_sales"]}
        self.assertEqual(cm.compare(measure, site)["mismatch"], "tags_present_import_missing")
        imported = cm.classify([action("2", kind="GOOGLE_ANALYTICS_4_PURCHASE")], [])
        self.assertEqual(cm.compare(imported, site)["mismatch"], "")
        self.assertEqual(cm.compare(imported, {"gtm_api_connected": True, "live_types": []})["mismatch"], "import_present_tags_missing")
        self.assertEqual(cm.compare(measure, {"gtm_api_connected": False})["mismatch"], "site_side_unknown")

    def test_the_refresh_stores_the_comparison(self):
        cm._site_tracking = lambda cid: {"gtm_api_connected": True, "live_types": ["store_sales"]}
        value = cm.refresh(27)
        self.assertEqual((value["state"], value["site"]["mismatch"]), ("not_measured", "tags_present_import_missing"))


class TheOptimisationGate(Stored):
    @classmethod
    def setUpClass(cls):
        cls._modules = load_agents()
        import agents.google_ads_agent as ga
        cls.ga = ga

    @classmethod
    def tearDownClass(cls):
        restore_agents(cls._modules)

    def setUp(self):
        super().setUp()
        ga = self.ga
        self.kept = (ga.OPTIMIZATION_MODE, ga._log_optimization, ga._keyword_performance, ga._monthly_budget_ceiling_ils,
                     ga._raise_issue, ga.log_step)
        ga.OPTIMIZATION_MODE = "live"                    # the worst case: nothing but the gate stands in the way
        self.logged, self.fetched, self.raised = [], [], []
        ga._log_optimization = lambda cid, action, reason, details, applied: self.logged.append((action, reason, applied))
        ga._keyword_performance = lambda conn: self.fetched.append(1) or [
            {"criterion_id": "1", "text": "נעליים", "ad_group_id": "2", "campaign_id": "3", "clicks": 40, "conversions": 0.0, "cost": 90.0}]
        ga._monthly_budget_ceiling_ils = lambda cid: 1500
        ga._raise_issue = lambda cid, key, msg, auto_paused=False: self.raised.append((key, msg)) or True
        ga.log_step = lambda *a, **k: None

    def tearDown(self):
        ga = self.ga
        (ga.OPTIMIZATION_MODE, ga._log_optimization, ga._keyword_performance, ga._monthly_budget_ceiling_ils,
         ga._raise_issue, ga.log_step) = self.kept
        super().tearDown()

    def test_live_mode_cannot_write_in_an_account_that_is_not_measuring(self):
        for state in ("not_measured", "configured_silent", "unknown"):
            self.put(state)
            self.logged.clear()
            ran = []
            self.assertFalse(self.ga._apply(27, "keyword_pause", "30 clicks, zero conversions", {}, lambda: ran.append(1)))
            self.assertEqual(ran, [], state)                             # the mutation never ran
            self.assertIn("REFUSED account_not_measuring", self.logged[0][1])

    def test_the_keyword_lever_refuses_before_it_reads(self):
        self.put("not_measured")
        out = self.ga.optimize_keywords(27, {"access_token": "t", "account_id": "111"})
        self.assertEqual((out["refused"], out["measurement"], out["paused"]), ("account_not_measuring", "not_measured", 0))
        self.assertEqual(self.fetched, [])

    def test_the_cycle_is_a_named_refusal(self):
        self.put("not_measured")
        out = self.ga.run_optimization_cycle(27, {"access_token": "t", "account_id": "111"}, [])
        self.assertEqual(out["refused"], "account_not_measuring")
        self.assertIn("not_measured", out["reason"])

    def test_the_alert_says_what_it_actually_is(self):
        campaign = [{"id": "5", "name": "חיפוש", "status": "ENABLED",
                     "last7": {"impressions": 10, "clicks": 9, "cost": 180.0, "conversions": 0.0},
                     "prev7": {"impressions": 10, "clicks": 9, "cost": 100.0, "conversions": 0.0}}]
        self.ga._check_performance_problems(27, campaign, "not_measured")
        key, msg = self.raised[-1]
        self.assertEqual(key, "unmeasured_spend_5")
        self.assertIn("NOTHING IS BEING MEASURED", msg)
        self.assertNotIn("with ZERO conversions", msg)
        self.ga._check_performance_problems(27, campaign, "configured_silent")
        self.assertEqual(self.raised[-1][0], "silent_measurement_5")
        self.ga._check_performance_problems(27, campaign, "measuring")
        self.assertEqual(self.raised[-1][0], "perf_zeroconv_5")
        self.assertIn("ZERO conversions", self.raised[-1][1])
        self.put("not_measured")                                          # with no state given, the stored one is read
        self.ga._check_performance_problems(27, campaign)
        self.assertEqual(self.raised[-1][0], "unmeasured_spend_5")

    def test_no_goal_for_an_account_that_counts_nothing(self):
        from core import target_inputs
        kept = target_inputs.missing
        try:
            target_inputs.missing = lambda cid: ["offer"]
            self.assertEqual(self.ga.set_performance_goal(27)["error"], "required_inputs_missing")
            target_inputs.missing = lambda cid: []
            self.put("not_measured")
            out = self.ga.set_performance_goal(27)
            self.assertEqual((out["error"], out["measurement"]), ("account_not_measuring", "not_measured"))
        finally:
            target_inputs.missing = kept


class OnThePage(Stored):
    def test_each_state_is_said_plainly_in_five_languages(self):
        for lang in LANGS:
            for state in cm.STATES + ("unknown",):
                shown = cm.view(27, lang, {"state": state, "conversions_30d": 12, "checked_at": "2026-11-28T08:00:00+00:00"})
                self.assertEqual(shown["state"], state)
                self.assertEqual(shown["ok"], state == "measuring")
                self.assertTrue(shown["text"] and shown["title"])
                self.assertEqual(client_speech.problems(shown["text"], lang), [], (lang, state))
            self.assertIn("12", cm.view(27, lang, {"state": "measuring", "conversions_30d": 12})["text"])
        self.assertEqual(cm.view(27, "he", {"state": "not_connected"}), {})

    def test_the_mismatch_is_told_to_the_client(self):
        shown = cm.view(27, "he", {"state": "not_measured", "site": {"mismatch": "tags_present_import_missing"}})
        self.assertIn("עוד לא מחוברת לחשבון הפרסום", shown["notes"][0])
        for key, row in cm.T.items():
            self.assertEqual(set(row), set(LANGS), key)

    def test_the_agents_are_told_a_zero_is_not_a_result(self):
        self.put("not_measured")
        self.assertIn("NOT 'no results'", cm.prompt_block(27)["rule"])
        self.put("not_connected")
        self.assertEqual(cm.prompt_block(27), {})

    def test_wiring(self):
        api = _read("core", "api_server.py")
        for needle in ('"/api/admin/google-ads/measurement"', '"/api/admin/google-ads/goal"'):
            self.assertIn(needle, api)
        self.assertIn('"measurement": _measurement(', _read("core", "google_page.py"))
        self.assertIn("d.measurement", _read("dashboard", "client", "index.html"))
        admin = _read("dashboard", "admin", "connections", "index.html")
        for needle in ("gaMeasureBtn", "gaGoalBtn", "configured_silent", "not_measured", "tags_present_import_missing"):
            self.assertIn(needle, admin)
        self.assertIn('payload["results_measurement"]', _read("agents", "support_agent.py"))
        for name in ("client_deletion.py", "deletion_guard.py", "admin_service.py"):
            self.assertIn("conversion_measurement:", _read("core", name), name)
        scan = _read("agents", "google_ads_agent.py")
        self.assertIn("conversion_measurement.refresh(client_id, conn)", scan)
        self.assertEqual(scan.count("conversion_measurement.require_measuring(client_id)"), 3)   # cycle, keyword lever, _apply


class TheBudgetOfAnExistingClient(unittest.TestCase):
    def test_get_forecast_reads_what_the_client_recorded(self):
        modules = load_agents()
        import agents.budget_agent as ba
        kept = (ba._lead_row, ba._recorded_budget)
        self.addCleanup(restore_agents, modules)
        try:
            ba._lead_row = lambda cid: ({}, "not found (no lead linked to this client_id)")
            ba._recorded_budget = lambda cid: None
            self.assertFalse(ba.get_forecast(27)["available"])
            ba._recorded_budget = lambda cid: 1500.0                      # no sales-chat lead at all: still a budget
            out = ba.get_forecast(27)
            self.assertEqual((out["available"], out["stated_marketing_budget_ils"], out["budget_source"]),
                             (True, 1500.0, "recorded_by_client"))
            ba._lead_row = lambda cid: ({"answers": {"marketing_budget": "3000"}, "proposal": {}}, "client_id (exact)")
            self.assertEqual(ba.get_forecast(27)["stated_marketing_budget_ils"], 1500.0)   # the newer one wins
            ba._recorded_budget = lambda cid: None
            self.assertEqual(ba.get_forecast(27)["stated_marketing_budget_ils"], 3000.0)
        finally:
            ba._lead_row, ba._recorded_budget = kept

    def test_the_budget_click_calls_the_goal_setter(self):
        from core import agent_actions as aa, quarter_goals_actions as qga, target_inputs as ti
        kept = (ti._read, ti._write, ti.missing)
        store, tasks = {}, []

        class Tasks:
            def add_task(self, fn, *args):
                tasks.append((fn, args))
        try:
            ti._read = lambda cid: store.get(cid, {})
            ti._write = lambda cid, value: store.__setitem__(cid, value)
            ti.missing = lambda cid: ["offer"]
            run = aa.ACTIONS["record_target_inputs"]["run"]
            run(27, {"budget_ils": 1500.0}, Tasks())
            self.assertEqual(tasks, [(qga.set_ads_goal, (27,))])
            run(27, {"lead_time": "15-25 business days"}, Tasks())       # no budget in the click: no goal run
            self.assertEqual(len(tasks), 1)
        finally:
            ti._read, ti._write, ti.missing = kept
        self.assertIn("set_performance_goal(client_id)", _read("core", "quarter_goals_actions.py"))


class NothingBehindTheRow(unittest.TestCase):
    def test_the_three_rows_are_never_offered(self):
        from core import connection_catalog as cc
        self.assertEqual(cc.NOT_BUILT, ("merchant_center", "creatomate", "uppbeat"))
        for key in cc.NOT_BUILT:
            row = cc._clean({"key": key, "tier": "paid_account", "signup_url": "https://example.com/signup",
                             "pricing_url": "https://example.com/pricing", "enabled": True})
            self.assertEqual((row["enabled"], row["signup_url"], row["pricing_url"], row["available"]), (False, "", "", False))
            self.assertFalse(cc.can_verify(key))                          # "done" could never have been checked
            for lang in LANGS:
                out = cc.flow(27, key, "start", lang=lang)
                self.assertEqual((out["chips"], out["connect"], out["support"], out["available"]), ([], "", False, False))
                self.assertNotIn("http", out["messages"][0])
            with self.assertRaises(ValueError):
                cc.admin_update(key, {"enabled": True})
        self.assertTrue(cc._clean({"key": "google_ads", "tier": "free_account", "enabled": True})["available"])
        self.assertEqual(set(cc.NOT_BUILT_LINE), set(LANGS))
        sql = _read("migrations", "2026-11-28-connection-catalog-not-built.sql")
        for key in cc.NOT_BUILT:
            self.assertIn(f"'{key}'", sql)
        self.assertIn("r.available === false", _read("dashboard", "admin", "connections", "index.html"))


if __name__ == "__main__":
    unittest.main()
