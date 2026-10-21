"""Dead Google Ads grant handling: core/google_ads_health, the readers in
agents/google_ads_agent, the Performance card, client_state, the deletion keys.

Stdlib only. google_ads_agent is imported for real with its third-party and
platform dependencies (supabase, httpx, keys, alerts) replaced by stubs, so
what runs is the actual skip / record / alert logic.

Run from the repo root:  python tests/test_google_ads_health.py -v
"""
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ─── Fake app_settings ───────────────────────────────────────────────────────

class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.key, self.payload = db, table, "select", None, None

    def select(self, *_a):
        return self

    def eq(self, col, val):
        self.key = val
        return self

    def limit(self, _n):
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", row
        return self

    def delete(self):
        self.op = "delete"
        return self

    def execute(self):
        self.db.calls.append(self.op)
        rows = self.db.rows
        if self.op == "upsert":
            rows[self.payload["key"]] = dict(self.payload)
            return types.SimpleNamespace(data=[self.payload])
        if self.op == "delete":
            rows.pop(self.key, None)
            return types.SimpleNamespace(data=[])
        hit = rows.get(self.key)
        return types.SimpleNamespace(data=[hit] if hit else [])


class FakeSettings:
    def __init__(self):
        self.rows, self.calls = {}, []

    def table(self, name):
        assert name == "app_settings", name
        return _Q(self, name)


from core import google_ads_health as gh   # noqa: E402  (stdlib-only module)


class HealthCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeSettings()
        self.alerts = []
        self.saved = (gh._db, gh._alert)
        gh._db = lambda: self.db
        gh._alert = lambda issue: self.alerts.append(issue)
        gh._memory.clear()

    def tearDown(self):
        gh._db, gh._alert = self.saved
        gh._memory.clear()


class Classification(unittest.TestCase):
    def test_invalid_grant_is_dead(self):
        self.assertEqual(gh.dead_reason(400, {"error": "invalid_grant"}), "invalid_grant")

    def test_unauthorized_client_is_dead(self):
        self.assertEqual(gh.dead_reason(400, {"error": "unauthorized_client"}), "unauthorized_client")

    def test_our_own_client_config_is_not_the_clients_grant(self):
        self.assertEqual(gh.dead_reason(400, {"error": "invalid_client"}), "")

    def test_other_statuses_and_bodies_are_not_dead(self):
        self.assertEqual(gh.dead_reason(401, {"error": "invalid_grant"}), "")
        self.assertEqual(gh.dead_reason(500, {"error": "invalid_grant"}), "")
        self.assertEqual(gh.dead_reason(400, None), "")
        self.assertEqual(gh.dead_reason(400, "invalid_grant"), "")


class HealthRecord(HealthCase):
    def test_one_alert_per_dead_grant(self):
        self.assertTrue(gh.record_dead(27, "grantA", "invalid_grant"))
        self.assertFalse(gh.record_dead(27, "grantA", "invalid_grant"))
        gh._memory.clear()   # a restart: the durable record still dedupes
        self.assertFalse(gh.record_dead(27, "grantA", "invalid_grant"))
        self.assertEqual(len(self.alerts), 1)

    def test_alert_is_plain_and_carries_no_token(self):
        gh.record_dead(27, "grantA", "invalid_grant")
        text = self.alerts[0]
        self.assertIn("client 27", text)
        self.assertIn("Google Ads connection needs reconnect", text)
        self.assertNotIn("grantA", text)
        self.assertNotIn("oauth2.googleapis.com", text)
        self.assertNotIn("400 Bad Request", text)

    def test_health_is_bound_to_the_grant(self):
        gh.record_dead(27, "grantA", "invalid_grant")
        self.assertTrue(gh.needs_reconnect(27, "grantA"))
        self.assertFalse(gh.needs_reconnect(27, "grantB"))   # reconnected: new token
        self.assertFalse(gh.needs_reconnect(27, ""))

    def test_a_new_grant_that_dies_alerts_again(self):
        gh.record_dead(27, "grantA", "invalid_grant")
        gh.record_dead(27, "grantB", "invalid_grant")
        self.assertEqual(len(self.alerts), 2)

    def test_skip_until_the_recheck_window_passes(self):
        gh.record_dead(27, "grantA", "invalid_grant")
        self.assertTrue(gh.should_skip(27, "grantA"))
        old = (datetime.now(timezone.utc) - timedelta(seconds=gh.RECHECK_SECONDS + 5)).isoformat()
        self.db.rows["gads_token:27"]["value"]["checked_at"] = old
        gh._memory.clear()
        self.assertFalse(gh.should_skip(27, "grantA"))        # one re-probe allowed
        self.assertTrue(gh.needs_reconnect(27, "grantA"))     # still a fact meanwhile

    def test_success_clears_dead(self):
        gh.record_dead(27, "grantA", "invalid_grant")
        gh.record_ok(27, "grantA")
        self.assertFalse(gh.needs_reconnect(27, "grantA"))
        self.assertTrue(gh.health(27)["ok"])

    def test_ok_is_not_rewritten_on_every_call(self):
        gh.record_ok(27, "grantA")
        writes = self.db.calls.count("upsert")
        for _ in range(5):
            gh.record_ok(27, "grantA")
        self.assertEqual(self.db.calls.count("upsert"), writes)

    def test_clear_after_reconnect(self):
        gh.record_dead(27, "grantA", "invalid_grant")
        gh.clear(27)
        self.assertEqual(gh.health(27), {})
        self.assertNotIn("gads_token:27", self.db.rows)

    def test_reads_are_memory_cached(self):
        gh.health(27)
        gh.health(27)
        self.assertEqual(self.db.calls.count("select"), 1)

    def test_unreadable_store_assumes_nothing(self):
        def broken():
            raise RuntimeError("db down")
        gh._db = broken
        self.assertEqual(gh.health(27), {})
        self.assertFalse(gh.should_skip(27, "grantA"))


# ─── The real google_ads_agent, dependencies stubbed ─────────────────────────

class _HTTPStatusError(Exception):
    def __init__(self, message, *, request=None, response=None):
        super().__init__(message)
        self.request, self.response = request, response


_STUBBED = ("supabase", "httpx", "agents.keys_agent", "core.api_call_counters", "core.lead_tracking",
            "core.agent_base", "core.claude_json", "core.secrets_vault", "core.chat_fallback",
            "agents.google_ads_agent", "core.google_ads_service")


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class AgentReaders(HealthCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_modules = {n: sys.modules.get(n) for n in _STUBBED}
        cls.alert_once_calls, cls.agent_alerts = [], []
        sys.modules.update({
            "supabase": _module("supabase", create_client=lambda *a: None),
            "httpx": _module("httpx", HTTPStatusError=_HTTPStatusError, post=None, Response=object,
                             Request=object, Client=object, TimeoutException=TimeoutError,
                             TransportError=OSError),
            "agents.keys_agent": _module("agents.keys_agent", get_key=lambda k: "x"),
            "core.api_call_counters": _module("core.api_call_counters", increment_call_counter=lambda *a, **k: 1),
            "core.lead_tracking": _module("core.lead_tracking"),
            "core.agent_base": _module("core.agent_base",
                                       agent_alert=lambda src, issues: cls.agent_alerts.append(issues),
                                       log_step=lambda *a, **k: None,
                                       timed_step=lambda agent, step, fn: fn()),
            "core.claude_json": _module("core.claude_json", ClaudeJSONError=Exception,
                                        safe_claude_json_call=None),
            "core.secrets_vault": _module("core.secrets_vault", hydrate=lambda row: row),
            "core.chat_fallback": _module("core.chat_fallback",
                                          alert_once=lambda *a, **k: cls.alert_once_calls.append(a)),
        })
        for name in ("agents.google_ads_agent", "core.google_ads_service"):
            sys.modules.pop(name, None)
        import agents.google_ads_agent as agent
        import core.google_ads_service as service
        cls.agent, cls.service = agent, service

    @classmethod
    def tearDownClass(cls):
        for name, module in cls._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self):
        super().setUp()
        self.alert_once_calls.clear()
        self.agent_alerts.clear()
        self.agent._perf_cache.clear()
        self.agent._daily_cache.clear()
        self.token = "refresh-token-ONE"
        self.agent._get_connection = lambda cid: {"client_id": cid, "access_token": self.token,
                                                  "account_id": "1234567890"}
        self.searches = 0

    def revoked(self, *_a, **_k):
        self.searches += 1
        response = types.SimpleNamespace(request=None, status_code=400)
        raise self.service.GrantRevoked("invalid_grant", response)

    def works(self, *_a, **_k):
        self.searches += 1
        return []

    def test_dead_grant_is_a_fact_not_an_error_alert(self):
        self.service.search = self.revoked
        result = self.agent.get_campaign_performance(27)
        self.assertTrue(result["needs_reconnect"])
        self.assertEqual(result["error"], self.agent.RECONNECT_NEEDED)   # no raw httpx text
        self.assertEqual(self.alert_once_calls, [])                       # no per-fetch alert
        self.assertEqual(len(self.alerts), 1)                             # the one plain alert

    def test_no_retry_while_dead(self):
        self.service.search = self.revoked
        self.agent.get_campaign_performance(27)
        self.agent._perf_cache.clear()
        self.agent.get_daily_metrics(27)
        self.agent.get_campaign_performance(27, force_refresh=True)
        self.assertEqual(self.searches, 1)          # only the call that discovered it
        self.assertEqual(len(self.alerts), 1)

    def test_daily_reader_reports_reconnect_too(self):
        self.service.search = self.revoked
        result = self.agent.get_daily_metrics(27)
        self.assertTrue(result["needs_reconnect"])
        self.assertEqual(result["days"], [])

    def test_reconnect_with_a_new_token_recovers_by_itself(self):
        self.service.search = self.revoked
        self.agent.get_campaign_performance(27)
        self.token = "refresh-token-TWO"            # the OAuth callback stored a new grant
        self.service.search = self.works
        self.agent._perf_cache.clear()
        result = self.agent.get_campaign_performance(27)
        self.assertNotIn("needs_reconnect", result)
        self.assertTrue(gh.health(27)["ok"])
        self.assertFalse(self.agent.grant_needs_reconnect(27))

    def test_forget_grant_clears_health_and_caches(self):
        self.service.search = self.revoked
        self.agent.get_campaign_performance(27)
        self.assertTrue(self.agent.grant_needs_reconnect(27))
        self.agent.forget_grant(27)
        self.assertEqual(gh.health(27), {})
        self.assertNotIn(27, self.agent._perf_cache)

    def test_other_errors_keep_the_existing_path(self):
        def boom(*_a, **_k):
            raise RuntimeError("quota")
        self.service.search = boom
        result = self.agent.get_campaign_performance(27)
        self.assertNotIn("needs_reconnect", result)
        self.assertEqual(len(self.alert_once_calls), 1)
        self.assertEqual(self.alerts, [])

    def test_grant_revoked_is_still_an_http_status_error(self):
        self.assertTrue(issubclass(self.service.GrantRevoked, _HTTPStatusError))

    def test_health_scan_skips_a_dead_grant_quietly(self):
        self.agent._all_connections = lambda: [{"client_id": 27, "access_token": self.token,
                                                "account_id": "1234567890"}]
        self.service.search = self.revoked
        first = self.agent.run_health_scan()
        second = self.agent.run_health_scan()
        self.assertIn("client 27: Google Ads needs reconnect", first["issues"])
        self.assertIn("client 27: Google Ads needs reconnect", second["issues"])
        self.assertEqual(self.searches, 1)            # the second scan didn't call Google
        self.assertEqual(self.agent_alerts, [])       # no "health scan failed" alerts
        self.assertEqual(len(self.alerts), 1)


# ─── Status layers: Performance card, client_state, agent actions ───────────

class StatusLayers(unittest.TestCase):
    def setUp(self):
        self.saved = sys.modules.get("agents.google_ads_agent")
        sys.modules["agents.google_ads_agent"] = _module(
            "agents.google_ads_agent", PENDING_STATUS="pending_selection",
            get_daily_metrics=lambda cid: {"connected": True, "needs_reconnect": True,
                                           "error": "reconnect_needed", "days": []})

    def tearDown(self):
        if self.saved is None:
            sys.modules.pop("agents.google_ads_agent", None)
        else:
            sys.modules["agents.google_ads_agent"] = self.saved

    def test_performance_card_says_reconnect(self):
        from core import performance
        card = performance._ads_card(27, "google_ads", {"google_ads"},
                                     [{"platform": "google_ads", "status": "active"}])
        self.assertEqual(card["status"], "needs_attention")
        self.assertEqual(card["reason"], "reconnect")

    def test_client_state_keeps_it_as_a_fact(self):
        from core import client_state
        out = client_state._campaigns({"connected": True, "needs_reconnect": True,
                                       "error": "reconnect_needed", "campaigns": []})
        self.assertEqual(out, {"connected": True, "needs_reconnect": True})

    def test_agents_are_offered_the_reconnect_card(self):
        from core import agent_actions
        state = {"channels": {"by_channel": {"google_ads": {"status": "needs_attention",
                                                           "reason": "reconnect"}}}}
        names = {a["name"] for a in agent_actions.available(
            27, "client_chat", state, client={"billing_exempt": False})}
        self.assertIn("connect_google_ads", names)


class DeletionKeys(unittest.TestCase):
    def test_guard_covers_the_health_row(self):
        from core import deletion_guard
        self.assertIn("gads_token:", deletion_guard.CLIENT_SETTING_PREFIXES)

    def test_deletion_clears_the_health_row(self):
        import ast
        with open(os.path.join(ROOT, "core", "client_deletion.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_settings_keys")
        self.assertIn("gads_token:", ast.unparse(fn))


if __name__ == "__main__":
    unittest.main()
