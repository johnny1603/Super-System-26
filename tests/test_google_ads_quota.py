"""The Google Ads daily operations ceiling is seen before it is hit, no
developer token is required, and the admin can ask what a client's connection
really is (core/google_ads_quota, core/google_ads_service, google_ads_agent).

Run: python tests/test_google_ads_quota.py
"""
import glob
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import google_ads_health as gh   # noqa: E402  (stdlib only)
from core import google_ads_quota as gq    # noqa: E402  (stdlib only)


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Counter:
    """A day's tally: what `count` reads and `increment` writes."""

    def __init__(self, used=0):
        self.used = used

    def read(self):
        return self.used

    def add(self):
        self.used += 1
        return self.used


class Quota(unittest.TestCase):
    def test_remaining_comes_from_the_days_tally(self):
        s = gq.status(lambda: 880)
        self.assertEqual((s["used"], s["limit"], s["remaining"], s["alert_at"]), (880, 2880, 2000, 1440))
        self.assertEqual(s["access_level"], "explorer")
        self.assertIn("not a figure from Google", s["source"])
        self.assertEqual(gq.status(lambda: 5000)["remaining"], 0)               # never negative

    def test_an_unreadable_tally_is_unknown_never_a_guessed_zero(self):
        s = gq.status(lambda: None)
        self.assertEqual((s["known"], s["used"], s["remaining"]), (False, None, None))
        self.assertFalse(gq.require(1, lambda: None)["known"])                   # not a refusal

    def test_work_that_does_not_fit_is_refused_by_name(self):
        self.assertEqual(gq.require(1, lambda: 2879)["remaining"], 1)
        with self.assertRaises(gq.QuotaExceeded) as caught:
            gq.require(1, lambda: 2880)
        self.assertIn("2880 of 2880 used, 0 left, 1 needed", str(caught.exception))
        self.assertIn("shared by all clients", str(caught.exception))
        self.assertTrue(issubclass(gq.QuotaExceeded, RuntimeError))             # existing `except` paths still catch it

    def test_a_mutate_that_does_not_fit_is_refused_whole_and_not_counted(self):
        tally = Counter(2870)
        with self.assertRaises(gq.QuotaExceeded) as caught:
            gq.spend(25, tally.read, tally.add, lambda text: None)
        self.assertEqual(tally.used, 2870)                                       # nothing of it was counted
        self.assertEqual(caught.exception.needed, 25)
        self.assertEqual(gq.spend(10, tally.read, tally.add, lambda text: None), 2880)   # exactly fits

    def test_a_single_operation_past_the_limit_is_refused(self):
        tally = Counter(2880)
        with self.assertRaises(gq.QuotaExceeded) as caught:
            gq.spend(1, tally.read, tally.add, lambda text: None)
        self.assertIn("2880 of 2880 used, 0 left", str(caught.exception))
        self.assertEqual(gq.spend(1, lambda: None, lambda: 0, lambda text: None), 0)     # counter down: fails open

    def test_the_owner_is_alerted_once_at_half(self):
        sent, tally = [], Counter(1430)
        gq.spend(25, tally.read, tally.add, sent.append)                         # crosses 1440 inside one mutate
        self.assertEqual(len(sent), 1)
        self.assertIn("1440 of 2880", sent[0])
        self.assertIn("15,000 when Basic access is approved", sent[0])
        for _ in range(30):
            gq.spend(1, tally.read, tally.add, sent.append)
        self.assertEqual(len(sent), 1)                                           # not again the same day

        def broken(text):
            raise RuntimeError("sink down")
        gq.spend(1, lambda: 1439, lambda: 1440, broken)                          # never breaks a call

    def test_the_ceiling_is_one_constant(self):
        saved = gq.OPERATIONS_PER_DAY
        gq.OPERATIONS_PER_DAY = 15000                                            # the day Basic is approved
        try:
            s = gq.status(lambda: 3000)
            self.assertEqual((s["limit"], s["remaining"], s["alert_at"]), (15000, 12000, 7500))
            gq.require(1, lambda: 2880)                                          # no longer a refusal
        finally:
            gq.OPERATIONS_PER_DAY = saved
        hits = []
        for path in glob.glob(os.path.join(ROOT, "core", "*.py")) + glob.glob(os.path.join(ROOT, "agents", "*.py")):
            with open(path, encoding="utf-8") as f:
                if re.search(r"=\s*2_?880\b|=\s*15_?000\b.*[Oo]peration", f.read()):
                    hits.append(os.path.basename(path))
        self.assertEqual(hits, ["google_ads_quota.py"])
        self.assertNotIn("DAILY_OP_LIMIT", _src("core", "google_ads_service.py"))


# ─── The real service and agent, dependencies stubbed (as tests/test_google_ads_health) ───

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


def _missing_key(name):
    raise ValueError(f"Missing key: {name}")


class Real(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_modules = {n: sys.modules.get(n) for n in _STUBBED}
        cls.posts = []

        def post(url, headers=None, json=None, timeout=None):
            cls.posts.append((url, headers, json))
            return types.SimpleNamespace(status_code=200, json=lambda: {"results": []}, text="")
        sys.modules.update({
            "supabase": _module("supabase", create_client=lambda *a: None),
            "httpx": _module("httpx", HTTPStatusError=_HTTPStatusError, post=post, get=post, Response=object,
                             Request=object, Client=object, TimeoutException=TimeoutError, TransportError=OSError),
            # every key is MISSING: a call that still needs one fails loudly
            "agents.keys_agent": _module("agents.keys_agent", get_key=_missing_key),
            "core.api_call_counters": _module("core.api_call_counters", increment_call_counter=lambda *a, **k: 1),
            "core.lead_tracking": _module("core.lead_tracking"),
            "core.agent_base": _module("core.agent_base", agent_alert=lambda src, issues: None,
                                       log_step=lambda *a, **k: None, timed_step=lambda agent, step, fn: fn()),
            "core.claude_json": _module("core.claude_json", ClaudeJSONError=Exception, safe_claude_json_call=None),
            "core.secrets_vault": _module("core.secrets_vault", hydrate=lambda row: row),
            "core.chat_fallback": _module("core.chat_fallback", alert_once=lambda *a, **k: None),
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
        self.posts.clear()
        self.saved = (self.service._access_token, self.service._count_operation, self.service.search,
                      self.agent._stored_row, gh._db, gh._alert, os.environ.pop("GOOGLE_ADS_DEVELOPER_TOKEN", None))
        self.service._access_token = lambda refresh_token: "access"
        self.counted = []
        self.service._count_operation = lambda operations=1: self.counted.append(operations)
        self.dead = []
        gh._db = lambda: (_ for _ in ()).throw(RuntimeError("no database in tests"))
        gh._alert = self.dead.append
        gh._memory.clear()

    def tearDown(self):
        (self.service._access_token, self.service._count_operation, self.service.search,
         self.agent._stored_row, gh._db, gh._alert, token) = self.saved
        os.environ.pop("GOOGLE_ADS_DEVELOPER_TOKEN", None)
        if token is not None:
            os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"] = token
        gh._memory.clear()

    def test_a_missing_developer_token_stops_nothing(self):
        self.assertEqual(self.service.search("refresh", "1234567890", "SELECT customer.id FROM customer"), [])
        headers = self.posts[0][1]
        self.assertNotIn("developer-token", headers)
        self.assertEqual(headers["Authorization"], "Bearer access")
        os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"] = "configured"
        self.assertEqual(self.service._headers("refresh")["developer-token"], "configured")   # sent when set, never required
        source = _src("core", "google_ads_service.py")
        self.assertNotIn('get_key("GOOGLE_ADS_DEVELOPER_TOKEN")', source)
        self.assertNotIn('"GOOGLE_ADS_DEVELOPER_TOKEN":', _src("agents", "keys_agent.py"))   # no "Missing keys" warning

    def test_a_mutate_counts_one_per_operation_and_a_read_counts_one(self):
        self.service.search("refresh", "1234567890", "SELECT customer.id FROM customer")
        self.service.atomic_mutate("refresh", "1234567890", [{"a": 1}] * 25)
        self.service.mutate_ad_group_criteria("refresh", "1234567890", [{"a": 1}] * 3)
        self.service.mutate_ad_group_ads("refresh", "1234567890", [{"a": 1}] * 2)
        self.assertEqual(self.counted, [1, 25, 3, 2])
        self.assertIn("google_ads_quota.spend(operations)", _src("core", "google_ads_service.py"))

    def test_a_refused_call_never_reaches_google(self):
        def refuse(operations=1):
            raise gq.QuotaExceeded({"used": 2880, "limit": 2880, "remaining": 0}, operations)
        self.service._count_operation = refuse
        with self.assertRaises(gq.QuotaExceeded):
            self.service.search("refresh", "1234567890", "SELECT customer.id FROM customer")
        self.assertEqual(self.posts, [])

    # ── what a client's connection really is ──

    def probe(self, row, search=None):
        self.agent._stored_row = lambda client_id: row
        self.calls = 0

        def counted(*a, **k):
            self.calls += 1
            return search(*a, **k)
        self.service.search = counted if search else (lambda *a, **k: self.fail("Google was called"))
        return self.agent.connection_probe(27)

    def test_never_authorised_says_what_the_client_does(self):
        out = self.probe({})
        self.assertEqual(out["state"], "never_authorised")
        self.assertIn("Connect on the Google Ads card", out["next_step"])
        self.assertIn("OWNS", out["next_step"])

    def test_a_consent_with_no_account_is_its_own_state(self):
        out = self.probe({"access_token": "refresh", "account_id": "", "status": self.agent.PENDING_STATUS})
        self.assertEqual(out["state"], "authorised_no_account_chosen")

    def test_authorised_and_readable_is_a_live_answer(self):
        row = {"access_token": "refresh", "account_id": "1234567890", "status": "active"}
        out = self.probe(row, lambda *a: [{"customer": {"descriptiveName": "Shop", "status": "ENABLED"}}])
        self.assertEqual((out["state"], out["account_name"], out["account_status"], out["customer_id"]),
                         ("authorised_readable", "Shop", "ENABLED", "123-456-7890"))
        self.assertEqual(self.calls, 1)                                          # one operation, no more

    def test_authorised_but_erroring_carries_googles_message_and_no_token(self):
        row = {"access_token": "refresh-SECRET", "account_id": "1234567890", "status": "active"}

        def boom(*a):
            raise RuntimeError("GAQL search failed - 403 PERMISSION_DENIED: The caller does not have permission")
        out = self.probe(row, boom)
        self.assertEqual(out["state"], "authorised_errors")
        self.assertIn("PERMISSION_DENIED", out["error"])
        self.assertNotIn("refresh-SECRET", str(out))

    def test_a_dead_grant_is_named_and_recorded(self):
        row = {"access_token": "refresh", "account_id": "1234567890", "status": "active"}

        def revoked(*a):
            raise self.service.GrantRevoked("invalid_grant", types.SimpleNamespace(request=None, status_code=400))
        out = self.probe(row, revoked)
        self.assertEqual((out["state"], out["error"]), ("authorised_grant_dead", "invalid_grant"))
        self.assertEqual(len(self.dead), 1)

    def test_an_unreadable_store_is_unknown_not_never_authorised(self):
        def broken(client_id):
            raise RuntimeError("db down")
        self.agent._stored_row = broken
        self.assertEqual(self.agent.connection_probe(27)["state"], "unknown")


class Wiring(unittest.TestCase):
    def test_admin_routes_and_the_card(self):
        server = _src("core", "api_server.py")
        for path, call in (("/api/admin/google-ads/quota", "google_ads_quota.status()"),
                           ("/api/admin/google-ads/connection", "connection_probe(client_id)")):
            route = server[server.index(f'@app.get("{path}")'):][:700]
            self.assertIn("_require_admin(request)", route)
            self.assertIn(call, route)
        page = _src("dashboard", "admin", "connections", "index.html")
        self.assertIn('api("/api/admin/google-ads/quota")', page)
        self.assertIn('"/api/admin/google-ads/connection?client_id="', page)
        self.assertLess(page.index('id="ytQuota"'), page.index('id="gaQuota"'))   # beside the YouTube counters
        self.assertIn("d.remaining", page)

    def test_ad_copy_asks_before_the_model_is_paid(self):
        agent = _src("agents", "google_ads_agent.py")
        body = agent[agent.index("def optimize_ad_copy("):agent.index("def _final_url_for(")]
        self.assertLess(body.index("google_ads_quota.require(1)"), body.index("safe_claude_json_call("))


if __name__ == "__main__":
    unittest.main()
