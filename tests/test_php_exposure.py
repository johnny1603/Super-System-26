"""PHP2Shell exposure tests (core/php_exposure, core/php_advisory, the
rotate_wordpress_password action and website_agent.rotate_stored_password).

Stdlib only — httpx, supabase and the LLM client are replaced by stubs in
sys.modules, the database by an in-memory fake, and every outgoing request is
RECORDED so the tests can prove what the check sends: an ordinary HEAD/GET of
the homepage, nothing else.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import ast
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))   # test_agent_actions' fake DB is reused


# ─── Stubs (installed before importing anything that imports them) ──────────

class FakeResponse:
    def __init__(self, status_code=200, headers=None, history=None, url=""):
        self.status_code, self.headers, self.history, self.url = status_code, headers or {}, history or [], url

    def json(self):
        return {}


class RecordingHttpx(types.ModuleType):
    """Every call any code makes through httpx lands in .calls."""

    def __init__(self):
        super().__init__("httpx")
        self.calls, self.responses = [], {}

    def _record(self, method, url, **kwargs):
        self.calls.append((method.upper(), url, kwargs))
        answer = self.responses.get(method.upper(), FakeResponse())
        if isinstance(answer, Exception):
            raise answer
        return answer

    def request(self, method, url, **kwargs):
        return self._record(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self._record("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._record("POST", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._record("DELETE", url, **kwargs)

    class Response:   # type annotations in wordpress_service
        pass


HTTPX = RecordingHttpx()
sys.modules["httpx"] = HTTPX
supabase_stub = types.ModuleType("supabase")
supabase_stub.create_client = lambda *a, **k: None
sys.modules.setdefault("supabase", supabase_stub)
claude_stub = types.ModuleType("core.claude_json")
claude_stub.safe_claude_json_call = lambda *a, **k: {}
sys.modules["core.claude_json"] = claude_stub
master_stub = types.ModuleType("agents.master_agent")
master_stub.alert = lambda *a, **k: None
sys.modules["agents.master_agent"] = master_stub

from core import php_advisory as adv     # noqa: E402
from core import php_exposure as px      # noqa: E402


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.filters, self.payload = db, table, "select", [], None

    def select(self, *_a):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def limit(self, _n):
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", row
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            rows[:] = [r for r in rows if r.get("key") != self.payload["key"]] + [dict(self.payload)]
            return types.SimpleNamespace(data=[dict(self.payload)])
        return types.SimpleNamespace(data=[dict(r) for r in rows if all(f(r) for f in self.filters)])


class FakeDB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _Query(self, name)


# ─── Version comparison ──────────────────────────────────────────────────────

class VersionComparison(unittest.TestCase):
    def status(self, header):
        return px.assess_headers([header])

    def test_fixed_releases_are_patched(self):
        for version in ("8.5.12", "8.4.27", "8.3.36", "8.2.35", "8.3.40", "8.2.100"):
            self.assertEqual(self.status(f"PHP/{version}")["status"], px.PATCHED, version)

    def test_one_below_fixed_is_vulnerable(self):
        for version in ("8.5.11", "8.4.26", "8.3.35", "8.2.34", "8.2.0"):
            result = self.status(f"PHP/{version}")
            self.assertEqual((result["status"], result["reason"]), (px.VULNERABLE, "below_fixed_release"), version)

    def test_patch_is_compared_as_a_number_not_text(self):
        # "8.3.4" sorts AFTER "8.3.36" as text; it is far older
        self.assertEqual(self.status("PHP/8.3.4")["status"], px.VULNERABLE)
        self.assertEqual(self.status("PHP/8.3.100")["status"], px.PATCHED)

    def test_81_and_older_have_no_official_fix(self):
        for version in ("8.1.31", "8.1.99", "8.0.30", "7.4.33", "5.6.40"):
            result = self.status(f"PHP/{version}")
            self.assertEqual((result["status"], result["reason"]), (px.VULNERABLE, "no_official_fix"), version)

    def test_81_vendor_build_is_unknown_not_safe(self):
        # Ubuntu/Debian ship 8.1 with backports — maybe fixed, never assumed
        result = self.status("PHP/8.1.2-1ubuntu2.20")
        self.assertEqual((result["status"], result["reason"]), (px.UNKNOWN, "vendor_build_unsupported_branch"))

    def test_patched_looking_prereleases_are_not_patched(self):
        for version in ("8.3.36-dev", "8.3.36RC1", "8.2.35rc2", "8.5.12-beta1", "8.4.27alpha"):
            result = self.status(f"PHP/{version}")
            self.assertEqual((result["status"], result["reason"]), (px.UNKNOWN, "prerelease_build"), version)

    def test_hidden_patch_level_is_unknown(self):
        self.assertEqual(self.status("PHP/8.3")["reason"], "patch_level_hidden")

    def test_branch_the_advisory_does_not_list_is_unknown(self):
        for version in ("8.6.0", "9.0.1", "8.10.0"):
            self.assertEqual(self.status(f"PHP/{version}")["status"], px.UNKNOWN, version)

    def test_vendor_build_below_fix_is_unknown_and_at_fix_is_patched(self):
        self.assertEqual(self.status("PHP/8.2.7-1+deb12u3")["reason"], "vendor_build_may_be_backported")
        self.assertEqual(self.status("PHP/8.3.36-1+ubuntu24.04.1+deb.sury.org+1")["status"], px.PATCHED)

    def test_no_header_is_unknown(self):
        self.assertEqual(px.assess_headers(["nginx", "cloudflare"])["reason"], "not_exposed")
        self.assertEqual(px.assess_headers([])["reason"], "not_exposed")

    def test_php_inside_server_header(self):
        self.assertEqual(self.status("Apache/2.4.62 (Debian) PHP/8.2.35 OpenSSL/3.0")["status"], px.PATCHED)

    def test_conflicting_headers_are_unknown(self):
        result = px.assess_headers(["PHP/8.3.36", "Apache PHP/8.1.2"])
        self.assertEqual((result["status"], result["reason"]), (px.UNKNOWN, "conflicting_headers"))

    def test_advisory_constants_are_the_published_ones(self):
        self.assertEqual(adv.FIXED_RELEASES, {(8, 5): 12, (8, 4): 27, (8, 3): 36, (8, 2): 35})
        self.assertEqual(adv.OLDEST_FIXED_BRANCH, (8, 2))
        self.assertEqual(adv.fixed_versions_text(), "8.5.12 / 8.4.27 / 8.3.36 / 8.2.35")
        self.assertTrue(adv.RECORDED_ON)


# ─── The check sends nothing but an ordinary HEAD/GET ────────────────────────

class NoPayloadsSent(unittest.TestCase):
    def setUp(self):
        HTTPX.calls.clear()
        HTTPX.responses.clear()

    def assert_only_ordinary_requests(self, expected_url):
        self.assertTrue(HTTPX.calls)
        for method, url, kwargs in HTTPX.calls:
            self.assertIn(method, ("HEAD", "GET"))
            self.assertEqual(url, expected_url)          # homepage only — no path, no query string
            self.assertFalse({"content", "data", "json", "files", "params", "auth", "cookies"} & set(kwargs))
            self.assertEqual(set(kwargs.get("headers") or {}), {"User-Agent"})

    def test_head_with_a_php_header_is_the_only_request(self):
        HTTPX.responses["HEAD"] = FakeResponse(200, {"X-Powered-By": "PHP/8.1.2"})
        result = px.check_site("Client-Site.co.il/")
        self.assertEqual(result["status"], px.VULNERABLE)
        self.assertEqual([c[0] for c in HTTPX.calls], ["HEAD"])
        self.assert_only_ordinary_requests("https://Client-Site.co.il/")

    def test_falls_back_to_a_plain_get_when_head_is_refused(self):
        HTTPX.responses["HEAD"] = FakeResponse(405, {})
        HTTPX.responses["GET"] = FakeResponse(200, {"x-powered-by": "PHP/8.3.36"})
        self.assertEqual(px.check_site("https://example.org")["status"], px.PATCHED)
        self.assertEqual([c[0] for c in HTTPX.calls], ["HEAD", "GET"])
        self.assert_only_ordinary_requests("https://example.org/")

    def test_redirect_hop_headers_count(self):
        hop = FakeResponse(301, {"X-Powered-By": "PHP/7.4.33"})
        HTTPX.responses["HEAD"] = FakeResponse(200, {"Server": "nginx"}, history=[hop])
        self.assertEqual(px.check_site("https://example.org")["reason"], "no_official_fix")

    def test_unreachable_is_unknown(self):
        HTTPX.responses["HEAD"] = HTTPX.responses["GET"] = ConnectionError("dns")
        result = px.check_site("https://gone.example")
        self.assertEqual((result["status"], result["reason"]), (px.UNKNOWN, "unreachable"))

    def test_other_methods_are_refused_by_the_seam(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"):
            with self.assertRaises(ValueError):
                px._fetch(method, "https://example.org/")
        self.assertEqual(HTTPX.calls, [])

    def test_source_has_one_httpx_call_and_it_sends_no_body(self):
        tree = ast.parse(_src("core", "php_exposure.py"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "httpx"]
        self.assertEqual([c.func.attr for c in calls], ["request"])
        self.assertFalse({k.arg for k in calls[0].keywords} & {"content", "data", "json", "files", "params", "auth"})


# ─── Storage + dedupe ────────────────────────────────────────────────────────

SITE = {"client_id": 12, "client_name": "Pizza", "site_url": "https://pizza.co.il",
        "origin": px.ORIGIN_CLIENT_CONNECTED, "connected": True, "has_app_password": True}


def result(status, reason="x", version=""):
    return {"status": status, "reason": reason, "php_version": version, "header": "", "reachable": True}


class AlertDedupe(unittest.TestCase):
    def setUp(self):
        self.db, self.alerts = FakeDB(), []
        self.saved = (px._db, px._alert)
        px._db = lambda: self.db
        px._alert = self.alerts.append

    def tearDown(self):
        px._db, px._alert = self.saved

    def test_one_alert_per_status_change(self):
        px.update_site(SITE, result(px.VULNERABLE, "no_official_fix", "8.1.2"))
        px.update_site(SITE, result(px.VULNERABLE, "no_official_fix", "8.1.2"))
        px.update_site(SITE, result(px.VULNERABLE, "no_official_fix", "8.1.2"))
        self.assertEqual(len(self.alerts), 1)
        px.update_site(SITE, result(px.PATCHED, "reports_fixed_release", "8.3.36"))
        px.update_site(SITE, result(px.PATCHED, "reports_fixed_release", "8.3.36"))
        self.assertEqual(len(self.alerts), 2)
        px.update_site(SITE, result(px.UNKNOWN, "unreachable"))
        self.assertEqual(len(self.alerts), 3)

    def test_dedupe_survives_a_restart(self):
        px.update_site(SITE, result(px.UNKNOWN, "not_exposed"))
        # a new process: nothing in memory, the stored record is the dedup
        px.update_site(SITE, result(px.UNKNOWN, "unreachable"))
        self.assertEqual(len(self.alerts), 1)

    def test_first_check_patched_is_quiet(self):
        px.update_site(SITE, result(px.PATCHED, "reports_fixed_release", "8.4.27"))
        self.assertEqual(self.alerts, [])

    def test_sites_are_deduped_separately(self):
        other = {**SITE, "site_url": "https://pizza-2.co.il"}
        px.update_site(SITE, result(px.VULNERABLE, version="8.0.1"))
        px.update_site(other, result(px.VULNERABLE, version="8.0.1"))
        self.assertEqual(len(self.alerts), 2)

    def test_alert_wording(self):
        px.update_site(SITE, result(px.VULNERABLE, "no_official_fix", "8.1.2"))
        text = self.alerts[0]
        self.assertTrue(text.startswith("client 12"))           # Control Room matches on this
        self.assertIn("hosting provider", text)
        self.assertIn("rotate", text)
        self.assertIn(adv.fixed_versions_text(), text)

    def test_record_key_is_one_the_deletion_clears(self):
        px.update_site(SITE, result(px.UNKNOWN))
        keys = [r["key"] for r in self.db.tables["app_settings"]]
        self.assertEqual(keys, ["php_exposure:12"])
        self.assertIn('f"php_exposure:{int(i)}"', _src("core", "client_deletion.py"))
        self.assertIn('"php_exposure:"', _src("core", "deletion_guard.py"))


class SiteSecurityFact(unittest.TestCase):
    def setUp(self):
        self.saved = (px.inventory, px.record)

    def tearDown(self):
        px.inventory, px.record = self.saved

    def test_never_verified_safe_and_not_checked_when_no_record(self):
        px.inventory = lambda client_id=None: [dict(SITE)]
        px.record = lambda cid: {}
        fact = px.site_security_fact(12)
        self.assertIs(fact["verified_safe"], False)
        self.assertEqual(fact["sites"][0]["php_status"], px.NOT_CHECKED)

    def test_patched_record_is_still_not_verified_safe(self):
        px.inventory = lambda client_id=None: [dict(SITE)]
        px.record = lambda cid: {"sites": {SITE["site_url"]: {"status": px.PATCHED, "checked_at": "2026-10-24",
                                                              "php_version": "8.3.36"}}}
        fact = px.site_security_fact(12)
        self.assertEqual(fact["sites"][0]["php_status"], px.PATCHED)
        self.assertIs(fact["verified_safe"], False)

    def test_no_site_no_fact(self):
        px.inventory = lambda client_id=None: []
        self.assertEqual(px.site_security_fact(12), {"has_wordpress_site": False})


# ─── The rotation: action layer + website_agent order of operations ─────────

class RotationActionGuards(unittest.TestCase):
    """Uses test_agent_actions' fake DB (same query shapes)."""

    @classmethod
    def setUpClass(cls):
        import importlib
        cls.ta = importlib.import_module("test_agent_actions")

    def setUp(self):
        aa = self.ta.aa
        self.aa, self.db = aa, self.ta.FakeDB()
        self.saved = {n: getattr(aa, n) for n in ("_db", "_client", "_post_admin_message",
                                                  "_post_client_message", "_alert", "_log")}
        aa._db = lambda: self.db
        aa._client = lambda cid: {"id": cid, "billing_exempt": False, "language": "he"}
        self.admin_posts, self.logs = [], []
        aa._post_admin_message = lambda cid, persona, text: self.admin_posts.append(text)
        aa._post_client_message = lambda *a: None
        aa._alert = lambda issue: None
        aa._log = lambda message, **k: self.logs.append(message)
        self.rotations = []
        website = types.ModuleType("agents.website_agent")
        website.rotate_stored_password = lambda cid: self.rotations.append(cid) or {
            "success": True, "site_url": "https://pizza.co.il", "old_revoked": True}
        sys.modules["agents.website_agent"] = website
        self.noted, self.saved_note = [], px.note_rotated
        px.note_rotated = lambda *a: self.noted.append(a)

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(self.aa, name, fn)
        px.note_rotated = self.saved_note

    STATE = {"connections": {"active_platforms": ["wordpress"]}}

    def test_client_chat_cannot_propose_it(self):
        out = self.aa.propose(40, "client_chat", "website", {"name": "rotate_wordpress_password"}, state=self.STATE)
        self.assertEqual(out["error"], "not_allowed_here")

    def test_not_offered_without_a_wordpress_connection(self):
        out = self.aa.propose(40, "admin_chat", "website", {"name": "rotate_wordpress_password"},
                              state={"connections": {"active_platforms": []}})
        self.assertEqual(out["error"], "not_available")

    def test_runs_only_on_the_admin_click_once_and_is_audited(self):
        out = self.aa.propose(40, "admin_chat", "website", {"name": "rotate_wordpress_password"}, state=self.STATE)
        self.assertEqual(out["status"], "proposed")
        self.assertEqual(self.rotations, [])                           # proposing runs nothing
        pid = out["card"]["id"]
        self.assertFalse(self.aa.decide(pid, "client", 40, "approve")["success"])   # client can't
        done = self.aa.decide(pid, "admin", 40, "approve")
        again = self.aa.decide(pid, "admin", 40, "approve")
        self.assertEqual((done["status"], self.rotations), ("done", [40]))
        self.assertTrue(again["already"])
        row = self.db.tables[self.aa.TABLE][0]
        self.assertEqual((row["status"], row["decided_by"], row["outcome"]), ("done", "admin", "done"))
        self.assertEqual(self.noted, [(40, "https://pizza.co.il", True)])
        self.assertIn("site_url: https://pizza.co.il", self.admin_posts[-1])

    def test_failure_is_recorded_and_says_nothing_was_changed(self):
        sys.modules["agents.website_agent"].rotate_stored_password = lambda cid: {
            "success": False, "errors": ["the stored password is unchanged"]}
        out = self.aa.propose(40, "admin_chat", "website", {"name": "rotate_wordpress_password"}, state=self.STATE)
        result = self.aa.decide(out["card"]["id"], "admin", 40, "approve")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.db.tables[self.aa.TABLE][0]["outcome"], "refused_by_action")
        self.assertEqual(self.noted, [])


class StoredPasswordRotation(unittest.TestCase):
    """website_agent.rotate_stored_password: mint → verify → store → revoke,
    and neither password in any log line or return value."""

    OLD, NEW = "oldp ass word xxxx yyyy", "newp ass word zzzz wwww"

    def setUp(self):
        for name in ("agents.website_agent",):
            sys.modules.pop(name, None)
        import importlib
        self.wa = importlib.import_module("agents.website_agent")
        self.events, self.stored, self.logged = [], [], []
        wa = self.wa
        self.saved = {n: getattr(wa, n) for n in ("_get_connection", "log_step")}
        wa._get_connection = lambda cid: {"account_id": "https://pizza.co.il", "access_token": f"admin:{self.OLD}"}
        wa.log_step = lambda *a: self.logged.append(" ".join(map(str, a)))
        self.saved_wp = {n: getattr(wa.wp, n) for n in ("rest_post", "get_current_user",
                                                        "introspect_application_password",
                                                        "delete_application_password")}
        wa.wp.rest_post = lambda site, path, user, pw, data=None: self.events.append(("mint", pw)) or {
            "password": self.NEW, "uuid": "new-uuid"}
        wa.wp.get_current_user = lambda site, user, pw: self.events.append(("verify", pw)) or {"id": 1}
        wa.wp.introspect_application_password = lambda site, user, pw: self.events.append(("introspect", pw)) or {
            "uuid": "old-uuid"}
        wa.wp.delete_application_password = lambda site, user, pw, uuid: self.events.append(("delete", uuid))
        client_agent = types.ModuleType("agents.client_agent")
        client_agent.upsert_account = lambda cid, platform, url, token, status: (
            self.events.append(("store", token)) or self.stored.append(token))
        self.saved_client_agent = sys.modules.get("agents.client_agent")
        sys.modules["agents.client_agent"] = client_agent

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(self.wa, name, fn)
        for name, fn in self.saved_wp.items():
            setattr(self.wa.wp, name, fn)
        if self.saved_client_agent is not None:
            sys.modules["agents.client_agent"] = self.saved_client_agent
        sys.modules.pop("agents.website_agent", None)

    def test_order_store_before_revoke(self):
        result = self.wa.rotate_stored_password(5)
        self.assertEqual(result, {"success": True, "site_url": "https://pizza.co.il", "old_revoked": True})
        order = [e[0] for e in self.events]
        self.assertEqual(order, ["mint", "verify", "store", "introspect", "delete"])
        self.assertEqual(self.stored, [f"admin:{self.NEW}"])
        self.assertEqual(self.events[-1], ("delete", "old-uuid"))

    def test_new_password_that_fails_verification_is_never_stored(self):
        def reject(site, user, pw):
            raise RuntimeError("401")
        self.wa.wp.get_current_user = reject
        result = self.wa.rotate_stored_password(5)
        self.assertFalse(result["success"])
        self.assertEqual(self.stored, [])
        self.assertEqual(self.events[-1], ("delete", "new-uuid"))      # our unusable one is cleaned up

    def test_no_password_in_logs_or_result(self):
        result = self.wa.rotate_stored_password(5)
        text = " ".join(self.logged) + repr(result)
        self.assertNotIn(self.OLD, text)
        self.assertNotIn(self.NEW, text)


if __name__ == "__main__":
    unittest.main()
