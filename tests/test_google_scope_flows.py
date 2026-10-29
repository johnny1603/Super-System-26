"""Google verification round 1, v3: every sensitive scope has a client-chat
flow (agent proposes -> client approves -> system executes), and the Tag
Manager write path is safe to run against a real container.

- GtmFlow: core.gtm_service + agents.website_agent.configure_lead_conversion,
  imported for real, against an in-memory Tag Manager API (the only seam is
  gtm_service._get/_post/_delete). Own workspace, compiler errors, cleanup
  after a failure, and the reason code every failure maps to.
- LeadTrackingCard / TestCampaignCard: the agent_actions side — the client's
  own click, and the plain-language refusal (+ reconnect card) per reason.
- ScopeAudit: the scope strings in code are exactly the documented list, and
  docs/GOOGLE-SCOPES.md has an evidence row for each.

Stdlib only. Run from the repo root:  python tests/test_google_scope_flows.py -v
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_agent_actions import CLIENTS, STATE_READY, ActionTestCase  # noqa: E402
from core import agent_actions as aa  # noqa: E402

_STUBBED = ("httpx", "supabase", "agents.keys_agent", "core.google_ads_service", "core.wordpress_service",
            "core.agent_base", "core.claude_json", "core.secrets_vault", "core.gtm_connection",
            "core.gtm_service", "agents.website_agent")

CONTAINER = "accounts/1/containers/2"


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class FakeGtm:
    """The Tag Manager API calls configure_lead_conversion makes, in memory.
    `fail` maps a call key ("create_workspace", "trigger", "tag", "version",
    "publish") to (http_status, google_reason) or "compiler"."""

    def __init__(self, gtm):
        self.gtm, self.calls, self.deleted = gtm, [], []
        self.fail, self.default_has_changes = {}, False

    def _maybe_fail(self, key, method, path):
        failure = self.fail.get(key)
        if failure is None or failure == "compiler":
            return failure
        status, reason = failure
        body = {"error": {"code": status, "status": reason, "details": [{"reason": reason}]}}
        raise self.gtm.GtmApiError(method, path, _Resp(status, body))

    def get(self, refresh_token, path, params=None):
        self.calls.append(("GET", path))
        if path == f"{CONTAINER}/workspaces":
            return {"workspace": [{"path": f"{CONTAINER}/workspaces/1"}]}
        if path.endswith("/status"):
            return {"workspaceChange": [{"tag": {}}]} if self.default_has_changes else {}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, refresh_token, path, json_body=None, params=None):
        self.calls.append(("POST", path))
        if path == f"{CONTAINER}/workspaces":
            self._maybe_fail("create_workspace", "POST", path)
            return {"path": f"{CONTAINER}/workspaces/9", "name": json_body["name"]}
        if path.endswith("/triggers"):
            self._maybe_fail("trigger", "POST", path)
            return {"triggerId": "5", "path": f"{path}/5", "name": json_body["name"]}
        if path.endswith("/tags"):
            self._maybe_fail("tag", "POST", path)
            assert json_body["firingTriggerId"] == ["5"]
            return {"tagId": "6", "path": f"{path}/6", "name": json_body["name"]}
        if path.endswith(":create_version"):
            if self._maybe_fail("version", "POST", path) == "compiler":
                return {"compilerError": True}
            return {"containerVersion": {"path": f"{CONTAINER}/versions/12"}}
        if path.endswith(":publish"):
            if self._maybe_fail("publish", "POST", path) == "compiler":
                return {"compilerError": True}
            return {"containerVersion": {"name": "uallak conversion tracking", "containerVersionId": "12"}}
        raise AssertionError(f"unexpected POST {path}")

    def delete(self, refresh_token, path):
        self.calls.append(("DELETE", path))
        self.deleted.append(path)


class GtmFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_modules = {n: sys.modules.get(n) for n in _STUBBED}
        cls.alerts = []
        sys.modules.update({
            "httpx": _module("httpx", Response=_Resp),
            "supabase": _module("supabase", create_client=lambda *a: None),
            "agents.keys_agent": _module("agents.keys_agent", get_key=lambda k: "key-" + k),
            "core.google_ads_service": _module("core.google_ads_service", OAUTH_CONSENT_URL="https://x",
                                               _access_token=lambda rt: "at", exchange_code=lambda c, r: {}),
            "core.wordpress_service": _module("core.wordpress_service", DEFAULT_SEO_PLUGIN_SLUG="x",
                                              ACCESSIBILITY_PLUGIN_SLUGS=()),
            "core.agent_base": _module("core.agent_base",
                                       agent_alert=lambda src, issues: cls.alerts.append(issues),
                                       log_step=lambda *a, **k: None,
                                       timed_step=lambda agent, step, fn: fn()),
            "core.claude_json": _module("core.claude_json", safe_claude_json_call=None),
            "core.secrets_vault": _module("core.secrets_vault", hydrate=lambda r: r,
                                          get_client_secret=lambda *a: ""),
            "core.gtm_connection": _module("core.gtm_connection", publish_block_reason=lambda cid: None),
        })
        for name in ("core.gtm_service", "agents.website_agent"):
            sys.modules.pop(name, None)
        import core.gtm_service as gtm
        import agents.website_agent as website
        cls.gtm, cls.website = gtm, website
        cls._saved_fns = {n: getattr(website, n) for n in
                          ("_gtm_connection", "gtm_missing_scopes", "get_conversion_tracking_status",
                           "_log_activity")}
        website._gtm_connection = lambda cid: {"access_token": "rt", "account_id": CONTAINER}
        website.gtm_missing_scopes = lambda cid: []
        website.get_conversion_tracking_status = lambda cid: {"gtm_api_connected": True,
                                                              "configured_live": False}
        website._log_activity = lambda *a, **k: None

    @classmethod
    def tearDownClass(cls):
        for name, fn in cls._saved_fns.items():
            setattr(cls.website, name, fn)
        for name, module in cls._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self):
        self.alerts.clear()
        self.api = FakeGtm(self.gtm)
        self.gtm._get, self.gtm._post, self.gtm._delete = self.api.get, self.api.post, self.api.delete

    def run_flow(self):
        return self.website.configure_lead_conversion(27, "G-ABC1234")

    def test_success_runs_in_its_own_workspace_and_publishes(self):
        result = self.run_flow()
        self.assertTrue(result["success"], result)
        posts = [p for m, p in self.api.calls if m == "POST"]
        self.assertEqual(posts, [f"{CONTAINER}/workspaces", f"{CONTAINER}/workspaces/9/triggers",
                                 f"{CONTAINER}/workspaces/9/tags", f"{CONTAINER}/workspaces/9:create_version",
                                 f"{CONTAINER}/versions/12:publish"])
        self.assertEqual(self.api.deleted, [])
        self.assertEqual(result["live_version_id"], "12")
        self.assertEqual(result["tag_name"], "uallak — GA4 generate_lead")
        self.assertEqual(self.alerts, [])

    def test_failed_tag_deletes_our_workspace_and_alerts(self):
        self.api.fail["tag"] = (500, "INTERNAL")
        result = self.run_flow()
        self.assertEqual(result["reason"], "gtm_error")
        self.assertEqual(self.api.deleted, [f"{CONTAINER}/workspaces/9"])
        self.assertEqual(len(self.alerts), 1)
        self.assertFalse(any(p.endswith(":publish") for _m, p in self.api.calls))

    def test_no_publish_permission_is_explained_not_alerted(self):
        self.api.fail["publish"] = (403, "PERMISSION_DENIED")
        result = self.run_flow()
        self.assertEqual(result["reason"], "gtm_no_permission")
        self.assertEqual(self.alerts, [])

    def test_workspace_create_403_is_not_retried_in_the_default_workspace(self):
        self.api.fail["create_workspace"] = (403, "PERMISSION_DENIED")
        result = self.run_flow()
        self.assertEqual(result["reason"], "gtm_no_permission")
        self.assertFalse(any(p.endswith("/triggers") for _m, p in self.api.calls))

    def test_expired_grant_means_reconnect(self):
        self.api.fail["create_workspace"] = (401, "UNAUTHENTICATED")
        self.assertEqual(self.run_flow()["reason"], "gtm_reconnect_needed")

    def test_scope_missing_means_reconnect(self):
        self.api.fail["trigger"] = (403, "ACCESS_TOKEN_SCOPE_INSUFFICIENT")
        self.assertEqual(self.run_flow()["reason"], "gtm_reconnect_needed")

    def test_api_disabled_is_ours_and_alerted(self):
        self.api.fail["trigger"] = (403, "SERVICE_DISABLED")
        result = self.run_flow()
        self.assertEqual(result["reason"], "gtm_api_disabled")
        self.assertEqual(len(self.alerts), 1)

    def test_compiler_error_publishes_nothing(self):
        self.api.fail["version"] = "compiler"
        result = self.run_flow()
        self.assertEqual(result["reason"], "gtm_compiler_error")
        self.assertFalse(any(p.endswith(":publish") for _m, p in self.api.calls))

    def test_workspace_limit_with_clean_default_uses_it(self):
        self.api.fail["create_workspace"] = (400, "FAILED_PRECONDITION")
        result = self.run_flow()
        self.assertTrue(result["success"], result)
        self.assertIn(("POST", f"{CONTAINER}/workspaces/1/triggers"), self.api.calls)

    def test_workspace_limit_with_someone_elses_drafts_touches_nothing(self):
        self.api.fail["create_workspace"] = (400, "FAILED_PRECONDITION")
        self.api.default_has_changes = True
        result = self.run_flow()
        self.assertEqual(result["reason"], "gtm_workspace_busy")
        self.assertFalse(any(p.endswith("/triggers") for _m, p in self.api.calls))
        self.assertEqual(self.alerts, [])

    def test_failure_in_shared_workspace_removes_only_what_we_created(self):
        self.api.fail["create_workspace"] = (400, "FAILED_PRECONDITION")
        self.api.fail["tag"] = (500, "INTERNAL")
        self.run_flow()
        self.assertEqual(self.api.deleted, [f"{CONTAINER}/workspaces/1/triggers/5"])

    def test_refusals_before_any_write_carry_a_reason(self):
        saved = self.website.get_conversion_tracking_status
        self.website.get_conversion_tracking_status = lambda cid: {
            "configured_live": True, "lead_event_tags": [{"tag_name": "x"}]}
        try:
            self.assertEqual(self.run_flow()["reason"], "already_live")
        finally:
            self.website.get_conversion_tracking_status = saved
        self.assertEqual(self.api.calls, [])
        saved = self.website._get_connection
        self.website._get_connection = lambda cid: {}          # no site to read a G- id from
        try:
            self.assertEqual(self.website.configure_lead_conversion(27, "")["reason"], "no_ga4_id")
        finally:
            self.website._get_connection = saved
        self.assertEqual(self.api.calls, [])


# ─── The chat cards ──────────────────────────────────────────────────────────

class LeadTrackingCard(ActionTestCase):
    def stored(self, client_id=27):
        result = self.propose(client_id, "client_chat", "configure_conversion")
        return result["card"]["id"]

    def failing(self, reason):
        def run(cid, ga4=""):
            return {"success": False, "reason": reason, "errors": [f"detail for {reason}"]}
        self.website.configure_lead_conversion = run

    def test_client_approves_and_it_runs_once(self):
        pid = self.stored()
        result = aa.decide(pid, "client", 27, "approve")
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(self.website.configure_lead_conversion.calls), 1)
        self.assertIn("מדידת הלידים באתר פעילה", self.posted_client[-1][2])

    def test_each_explained_reason_gets_its_own_hebrew_line_and_no_alert(self):
        for reason, code in aa._GTM_REFUSAL_CODES.items():
            with self.subTest(reason=reason):
                self.failing(reason)
                self.alerts.clear()
                pid = self.stored()
                result = aa.decide(pid, "client", 27, "approve")
                self.assertEqual(result["code"], code)
                self.assertEqual(result["message"], aa.ACTIONS["configure_conversion"]["refusals"][code]["he"])
                self.assertEqual(self.alerts, [])
                self.rows().clear()

    def test_reconnect_reason_brings_the_reconnect_card(self):
        self.failing("gtm_token_dead")
        result = aa.decide(self.stored(), "client", 27, "approve")
        self.assertEqual(result["next_card"]["action"], "connect_tag_manager")

    def test_our_own_failure_is_alerted_with_the_generic_line(self):
        self.failing("gtm_api_disabled")
        result = aa.decide(self.stored(), "client", 27, "approve")
        self.assertEqual(result["message"], aa._FAILED["he"])
        self.assertEqual(len(self.alerts), 1)

    def test_refusal_texts_exist_in_every_language(self):
        for code, texts in aa.ACTIONS["configure_conversion"]["refusals"].items():
            self.assertEqual(set(texts), set(aa.LANGS), code)
        self.assertEqual(set(aa._GTM_REFUSAL_CODES.values()),
                         set(aa.ACTIONS["configure_conversion"]["refusals"]))


class TestCampaignCard(ActionTestCase):
    def test_internal_client_approves_a_paused_campaign_in_its_own_chat(self):
        result = self.propose(27, "client_chat", "google_ads_test_campaign")
        self.assertTrue(result["card"]["can_decide"])
        self.assertIn("PAUSED", result["card"]["what"])
        done = aa.decide(result["card"]["id"], "client", 27, "approve")
        self.assertEqual(done["status"], "done")
        self.assertEqual(self.demo_calls, [("campaign", 27)])
        self.assertIn("Status: PAUSED", self.posted_client[-1][2])

    def test_paying_client_never_gets_it(self):
        self.assertEqual(self.propose(40, "client_chat", "google_ads_test_campaign")["error"], "internal_only")

    def test_not_offered_without_google_ads(self):
        state = {**STATE_READY, "connections": {"active_platforms": []}}
        self.assertEqual(self.propose(27, "client_chat", "google_ads_test_campaign", state=state)["error"],
                         "not_available")

    def test_the_connect_card_keeps_the_staff_consent(self):
        spec = aa.ACTIONS["connect_google_ads"]
        self.assertEqual(spec["kind"], "focus")
        self.assertNotIn("link", spec)


# ─── Scope audit ─────────────────────────────────────────────────────────────

FINAL_USER_SCOPES = {
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/adwords",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/tagmanager.edit.containers",
    "https://www.googleapis.com/auth/tagmanager.edit.containerversions",
    "https://www.googleapis.com/auth/tagmanager.publish",
}
SERVICE_ACCOUNT_ONLY = {"https://www.googleapis.com/auth/drive"}
# Google verification ROUND 2 (docs/GOOGLE-ROUND2.md): behind flags, off by
# default, and each may appear ONLY in its own service module
ROUND2_SCOPES = {"https://www.googleapis.com/auth/gmail.send": {"core/gmail_service.py"},
                 "https://www.googleapis.com/auth/webmasters.readonly": {"core/search_console_service.py"}}


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class ScopeAudit(unittest.TestCase):
    def code_scopes(self):
        found = {}
        for folder in ("core", "agents"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if name.endswith(".py"):
                    for scope in re.findall(r'"(https://www\.googleapis\.com/auth/[a-z.]+)"', _read(folder, name)):
                        found.setdefault(scope, set()).add(f"{folder}/{name}")
        return found

    def test_code_requests_exactly_the_final_list(self):
        found = set(self.code_scopes()) - SERVICE_ACCOUNT_ONLY - set(ROUND2_SCOPES)
        self.assertEqual(found, FINAL_USER_SCOPES - {"openid"})
        self.assertIn('"openid"', _read("core", "client_auth.py"))

    def test_round2_scopes_live_only_in_their_own_modules(self):
        found = self.code_scopes()
        for scope, allowed in ROUND2_SCOPES.items():
            self.assertEqual(found.get(scope, set()), allowed, scope)

    def test_full_youtube_and_content_scopes_are_gone(self):
        for scope in ("https://www.googleapis.com/auth/youtube", "https://www.googleapis.com/auth/content"):
            self.assertNotIn(scope, self.code_scopes())

    def test_every_scope_has_an_evidence_row(self):
        doc = _read("docs", "GOOGLE-SCOPES.md")
        matrix = doc.split("## Evidence matrix", 1)[1].split("\n## ", 1)[0]
        for scope in FINAL_USER_SCOPES:
            short = scope.rsplit("/", 1)[-1]
            rows = [line for line in matrix.splitlines() if line.startswith(f"| `{short}`")]
            self.assertEqual(len(rows), 1, short)
            self.assertEqual(rows[0].count("|"), 6, short)      # 5 filled columns

    def test_every_tagmanager_method_in_the_matrix_exists_in_gtm_service(self):
        source = _read("core", "gtm_service.py")
        for path_part in ("/workspaces", "/status", "/triggers", "/tags", ":create_version", ":publish",
                          "versions:live"):
            self.assertIn(path_part, source)


if __name__ == "__main__":
    unittest.main()
