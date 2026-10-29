"""Admin "enter as client" (core/admin_view, docs/ACCESS-MODES.md).

- Tokens: the admin-view cookie works only with the admin session that opened
  it, never as a client session, and a client session never works as it.
- Read-only: the endpoint allowlist names real GET client endpoints, and the
  ones that write on the client's behalf are not in it.
- api_server wiring: admin-only start, audit BEFORE the cookie, no client
  session or credential touched, staff decisions need the explicit mark and
  are audited before they run.
- agent_actions: staff may decide only staff-approvable cards, recorded as
  decided_by 'staff'; client-consent actions stay the client's click.
- The client dashboard: a visible logout button, the admin banner + Exit.

Stdlib only. Run from the repo root:  python tests/test_admin_view.py -v
"""
import ast
import base64
import json
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

os.environ.setdefault("SESSION_SECRET_KEY", "test-secret-for-admin-view")
if "agents.keys_agent" not in sys.modules:
    sys.modules["agents.keys_agent"] = types.SimpleNamespace(get_key=lambda k: os.environ.get(k, ""))

from core import admin_view, session  # noqa: E402
from test_agent_actions import ActionTestCase, aa  # noqa: E402


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


API = _read("core", "api_server.py")
API_TREE = ast.parse(API)


def _function(name: str):
    for node in API_TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not in api_server")


def _source(name: str) -> str:
    return ast.get_source_segment(API, _function(name))


def _methods(node) -> set:
    return {d.func.attr.upper() for d in node.decorator_list
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and d.func.attr in ("get", "post", "put", "patch", "delete")}


def _calls(node) -> list:
    """(line, name) of every call inside a function, in source order."""
    out = []
    body = ast.Module(body=node.body, type_ignores=[])     # not the route decorator
    for n in ast.walk(body):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            out.append((n.lineno, name))
    return sorted(out)


class Tokens(unittest.TestCase):
    def setUp(self):
        self.admin = session.create_admin_session_token()

    def test_view_token_needs_the_admin_session_that_opened_it(self):
        view = session.create_admin_view_token(40, self.admin)
        self.assertEqual(session.verify_admin_view_token(view, self.admin), 40)
        saved = session.ADMIN_SESSION_MAX_AGE_SECONDS
        session.ADMIN_SESSION_MAX_AGE_SECONDS = saved - 60     # another, still valid, admin session
        try:
            other_admin = session.create_admin_session_token()
        finally:
            session.ADMIN_SESSION_MAX_AGE_SECONDS = saved
        self.assertTrue(session.verify_admin_session_token(other_admin))
        self.assertIsNone(session.verify_admin_view_token(view, other_admin))
        self.assertIsNone(session.verify_admin_view_token(view, ""))
        self.assertIsNone(session.verify_admin_view_token(view, None))

    def test_a_client_session_can_never_open_it(self):
        client = session.create_session_token(40)
        # A client cookie is neither an admin session nor an admin-view token
        self.assertFalse(session.verify_admin_session_token(client))
        self.assertIsNone(session.verify_admin_view_token(client, client))
        self.assertIsNone(session.verify_admin_view_token(client, self.admin))

    def test_the_view_token_is_not_a_client_session(self):
        view = session.create_admin_view_token(40, self.admin)
        self.assertIsNone(session.verify_session_token(view))
        self.assertFalse(session.verify_admin_session_token(view))

    def test_tampered_client_id_refused(self):
        view = session.create_admin_view_token(40, self.admin)
        payload_b64, sig = view.split(".", 1)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        payload["client_id"] = 41
        forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode() + "." + sig
        self.assertIsNone(session.verify_admin_view_token(forged, self.admin))

    def test_expired_view_refused(self):
        saved = session.ADMIN_VIEW_MAX_AGE_SECONDS
        session.ADMIN_VIEW_MAX_AGE_SECONDS = -10
        try:
            view = session.create_admin_view_token(40, self.admin)
        finally:
            session.ADMIN_VIEW_MAX_AGE_SECONDS = saved
        self.assertIsNone(session.verify_admin_view_token(view, self.admin))


class ReadOnlyAllowlist(unittest.TestCase):
    def test_reads_allowed_writes_refused(self):
        self.assertTrue(admin_view.allowed("dashboard_data", "GET"))
        self.assertFalse(admin_view.allowed("dashboard_data", "POST"))
        for write in ("client_chat", "client_profile", "client_close_account", "client_change_password",
                      "website_connect", "client_disconnect", "client_lead_set_status"):
            self.assertFalse(admin_view.allowed(write, "POST"), write)
        self.assertTrue(admin_view.allowed("client_decide_action", "POST"))
        self.assertFalse(admin_view.allowed("client_decide_action", "GET"))
        self.assertFalse(admin_view.allowed("", "GET"))

    def test_every_allowlisted_endpoint_is_a_session_guarded_get(self):
        for name in admin_view.READ_ENDPOINTS:
            node = _function(name)
            self.assertEqual(_methods(node), {"GET"}, name)
            self.assertIn("_require_session(", _source(name), name)

    def test_endpoints_that_write_for_the_client_are_not_allowlisted(self):
        for name in ("client_media_folder", "client_data_export", "client_export", "client_export_google_doc",
                     "google_ads_oauth_start", "gtm_oauth_start", "youtube_oauth_start", "meta_oauth_start",
                     "tiktok_oauth_start", "gmail_oauth_start", "search_console_oauth_start"):
            self.assertNotIn(name, admin_view.READ_ENDPOINTS, name)

    def test_the_media_listing_does_not_create_the_folder_in_admin_view(self):
        body = _source("client_media_assets")
        self.assertIn("_admin_view_client(request)", body)
        self.assertIn("else media_folder_status(client_id)", body)
        self.assertIn('"admin" if _admin_view_client(request)', _source("client_media_drive_file"))


class ServerWiring(unittest.TestCase):
    def test_require_session_gates_the_admin_view_first(self):
        body = _source("_require_session")
        self.assertLess(body.index("_admin_view_client(request)"), body.index('request.cookies.get("session")'))
        self.assertIn("admin_view.allowed(endpoint, request.method)", body)
        self.assertIn("ERR_ADMIN_VIEW_READ_ONLY", body)
        self.assertIn("_locked_out(get_client(viewed))", body)

    def test_view_cookie_needs_the_admin_session(self):
        body = _source("_admin_view_client")
        self.assertIn('verify_admin_view_token(token, request.cookies.get("admin_session"))', body)

    def test_start_is_admin_only_audited_first_and_creates_no_client_session(self):
        node = _function("admin_view_as_start")
        calls = _calls(node)
        names = [n for _, n in calls]
        self.assertEqual(names[0], "_require_admin")
        self.assertLess(names.index("_admin_view_audit"), names.index("set_cookie"))
        body = _source("admin_view_as_start")
        for forbidden in ("create_session_token", "_set_session_cookie", "login_code", "password",
                          "session_version", 'key="session"'):
            self.assertNotIn(forbidden, body)
        self.assertIn("key=admin_view.COOKIE", body)
        self.assertIn("httponly=True", body)
        self.assertIn("AuditUnavailable", body)

    def test_admin_view_code_never_touches_client_credentials(self):
        for name in ("_admin_view_client", "_admin_view_audit", "admin_view_as_start", "admin_view_as_exit",
                     "admin_view_as_audit"):
            body = _source(name)
            for forbidden in ("create_session_token", "_set_session_cookie", "set_password",
                              "mark_login_code_used", "session_version"):
                self.assertNotIn(forbidden, body, name)
        module = _read("core", "admin_view.py").split('"""', 2)[2]
        for forbidden in ("password", "login_code", "session_version", "create_session_token"):
            self.assertNotIn(forbidden, module)

    def test_audit_and_list_are_admin_only(self):
        self.assertEqual([n for _, n in _calls(_function("admin_view_as_audit"))][0], "_require_admin")
        self.assertIn('delete_cookie(admin_view.COOKIE', _source("admin_view_as_exit"))
        self.assertIn('delete_cookie(admin_view.COOKIE', _source("admin_logout"))

    def test_staff_decision_needs_the_mark_and_is_audited_before_it_runs(self):
        body = _source("client_decide_action")
        self.assertIn("if not req.staff_action:", body)
        self.assertIn("ERR_STAFF_ACTION_REQUIRED", body)
        self.assertLess(body.index('"staff_action_requested"'), body.index("agent_actions.decide("))
        self.assertIn("staff=staff", body)
        self.assertIn("ERR_AUDIT_UNAVAILABLE", body)


class Audit(unittest.TestCase):
    def setUp(self):
        from test_agent_actions import FakeDB
        self.db = FakeDB()
        self.saved = admin_view._db
        admin_view._db = lambda: self.db

    def tearDown(self):
        admin_view._db = self.saved

    def test_entry_names_who_which_client_when(self):
        admin_view.audit("view_as_start", 40, "abc123", "1.2.3.4")
        (row,) = self.db.tables["alerts"]
        self.assertEqual((row["source"], row["status"]), ("admin_view", "resolved"))
        line = row["issues"][0]
        for part in ("view_as_start", "client 40", "admin session abc123", "1.2.3.4", " at 20"):
            self.assertIn(part, line)
        self.assertEqual(admin_view.recent()[0]["entry"], line)

    def test_unwritable_audit_raises(self):
        self.db.fail = True
        with self.assertRaises(admin_view.AuditUnavailable):
            admin_view.audit("view_as_start", 40, "abc", "ip")

    def test_audit_rows_are_protected_from_bulk_delete(self):
        from core import bulk_delete
        self.assertIn(admin_view.AUDIT_SOURCE, bulk_delete.AUDIT_SOURCES)


class StaffDecisions(ActionTestCase):
    def stored(self, action, client_id=40):
        row = {"id": len(self.rows()) + 1, "client_id": client_id, "action": action, "params": {},
               "origin": "client_chat", "persona": "general", "approver": "client", "status": "proposed",
               "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}
        self.db.tables.setdefault(aa.TABLE, []).append(row)
        return row["id"]

    def test_staff_approvable_card_runs_and_is_recorded_as_staff(self):
        pid = self.stored("configure_conversion")
        self.assertTrue(aa.card(self.rows()[0], "client")["staff_may_decide"])
        result = aa.decide(pid, "client", 40, "approve", staff=True)
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.rows()[0]["decided_by"], "staff")
        self.assertEqual(len(self.website.configure_lead_conversion.calls), 1)

    def test_client_consent_actions_stay_the_clients_click(self):
        for action in ("send_outreach_email", "publish_youtube_from_media", "send_tiktok_to_inbox",
                       "approve_suggestion", "connect_tag_manager"):
            with self.subTest(action=action):
                pid = self.stored(action)
                self.assertFalse(aa.card(self.rows()[-1], "client")["staff_may_decide"])
                result = aa.decide(pid, "client", 40, "approve", staff=True)
                self.assertEqual(result["code"], "client_only")
                self.assertEqual(self.rows()[-1]["status"], "proposed")    # nothing claimed

    def test_staff_cannot_reach_another_clients_card(self):
        pid = self.stored("configure_conversion", client_id=41)
        self.assertEqual(aa.decide(pid, "client", 40, "approve", staff=True)["code"], "not_found")

    def test_staff_cancel_is_recorded_as_staff(self):
        pid = self.stored("configure_conversion")
        aa.decide(pid, "client", 40, "cancel", staff=True)
        self.assertEqual((self.rows()[0]["status"], self.rows()[0]["decided_by"]), ("cancelled", "staff"))

    def test_client_click_unchanged(self):
        pid = self.stored("configure_conversion")
        aa.decide(pid, "client", 40, "approve")
        self.assertEqual(self.rows()[0]["decided_by"], "client")


class Dashboards(unittest.TestCase):
    def test_client_dashboard_has_a_visible_logout(self):
        html = _read("dashboard", "client", "index.html")
        topbar = html.split('<div class="topbar">', 1)[1].split("</div>\n</div>", 1)[0]
        self.assertIn('id="logoutBtn" onclick="logout()"', topbar)
        self.assertIn("async function logout()", html)
        self.assertIn("fetch('/api/logout', { method: 'POST'", html)
        self.assertIn("window.location.href = '/login/'", html)
        self.assertIn("logout_btn: { he:", html)
        # Phones: the top bar wraps instead of pushing the button off-screen
        self.assertRegex(html, r"@media \(max-width: 640px\) \{\s*\.topbar \{[^}]*flex-wrap: wrap")
        self.assertNotRegex(html, r"\.logout-btn \{[^}]*display:\s*none")

    def test_admin_banner_exit_and_staff_buttons(self):
        html = _read("dashboard", "client", "index.html")
        self.assertIn('id="adminViewBanner"', html)
        self.assertIn("if (d.admin_view) enterAdminViewMode(", html)
        self.assertIn("fetch('/api/admin/view-as/exit'", html)
        self.assertIn("staff_action: true", html)
        self.assertIn("a.staff_may_decide", html)

    def test_admin_list_has_enter_as_client(self):
        html = _read("dashboard", "admin", "index.html")
        self.assertIn("enterAsClient(${Number(c.id)})", html)
        self.assertIn("/view-as`, { method: 'POST' }", html)
        self.assertIn("/api/admin/view-as/audit", html)


if __name__ == "__main__":
    unittest.main()
