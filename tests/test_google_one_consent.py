"""ONE Google consent with EXACTLY the six Cloud Console scopes (2026-11-10).

- OneList: core/google_scopes.GOOGLE_SCOPES is the Console list, and the only
  place in the code a round-1 scope string is written.
- ConsentUrls: the Google Ads, YouTube and Tag Manager connects each send all
  six on one screen: offline, prompt=consent, no include_granted_scopes, no
  select_account / login_hint (the browser's account chooser is unchanged),
  no round-2 scope.
- Diagnostic: the admin read-only check (stubbed rows + tokeninfo) reports the
  scopes per connection, the missing ones, and never a token.
- Routes / card: the admin endpoints are admin-only, reconnect is internal
  clients only, and the client's card is one calm line with no tool name.

The GTM end-to-end (tag → version → publish, each with its own scope) is in
tests/test_google_scope_flows.py (GtmScopeEnforced), next to its fake API.

Stdlib only. Run from the repo root:  python tests/test_google_one_consent.py -v
"""
import ast
import os
import re
import sys
import types
import unittest
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

CONSOLE_SIX = ["https://www.googleapis.com/auth/adwords",
               "https://www.googleapis.com/auth/youtube.readonly",
               "https://www.googleapis.com/auth/youtube.upload",
               "https://www.googleapis.com/auth/tagmanager.edit.containers",
               "https://www.googleapis.com/auth/tagmanager.edit.containerversions",
               "https://www.googleapis.com/auth/tagmanager.publish"]
ROUND2_WORDS = ("gmail", "webmasters", "analytics", "drive")
_STUBBED = ("httpx", "agents.keys_agent", "core.api_call_counters", "core.google_ads_service",
            "core.youtube_service", "core.gtm_service", "core.conversion_types")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _function_source(path: str, name: str) -> str:
    source = _read(*path.split("/"))
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{name} not found in {path}")


class OneList(unittest.TestCase):
    def test_the_list_is_exactly_the_console_six(self):
        from core import google_scopes
        self.assertEqual(google_scopes.GOOGLE_SCOPES, CONSOLE_SIX)
        self.assertEqual(len(set(google_scopes.GOOGLE_SCOPES)), 6)

    def test_product_lists_are_slices_of_the_one_list(self):
        from core import google_scopes as gs
        for platform, needed in gs.PRODUCT_SCOPES.items():
            self.assertTrue(set(needed) <= set(gs.GOOGLE_SCOPES), platform)
        self.assertEqual(set(gs.ADS_SCOPES) | set(gs.YOUTUBE_SCOPES) | set(gs.GTM_SCOPES), set(CONSOLE_SIX))

    def test_round1_literals_live_only_in_google_scopes(self):
        for folder in ("core", "agents"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if not name.endswith(".py") or name == "google_scopes.py":
                    continue
                source = _read(folder, name)
                for scope in CONSOLE_SIX:
                    self.assertNotIn(f'"{scope}"', source, f"{folder}/{name} writes {scope} again")

    def test_services_take_their_lists_from_google_scopes(self):
        for path, constant in (("core/google_ads_service.py", "ADS_SCOPES = google_scopes.ADS_SCOPES"),
                               ("core/youtube_service.py", "YOUTUBE_SCOPES = google_scopes.YOUTUBE_SCOPES"),
                               ("core/gtm_service.py", "GTM_SCOPES = google_scopes.GTM_SCOPES")):
            self.assertIn(constant, _read(*path.split("/")), path)
            body = _function_source(path, "build_consent_url")
            self.assertIn("google_scopes.consent_url(", body, path)
            self.assertNotIn("include_granted_scopes", body, path)

    def test_missing_helper(self):
        from core import google_scopes as gs
        old = " ".join(CONSOLE_SIX[:3])             # what client 27 has today
        self.assertEqual(gs.missing(old), CONSOLE_SIX[3:])
        self.assertEqual(gs.missing(old, gs.GTM_SCOPES), gs.GTM_SCOPES)
        self.assertEqual(gs.missing(CONSOLE_SIX), [])
        self.assertEqual(gs.missing(old.split(), gs.ADS_SCOPES), [])


class ConsentUrls(unittest.TestCase):
    """The three real build_consent_url functions, with httpx / keys stubbed."""

    @classmethod
    def setUpClass(cls):
        cls._saved = {n: sys.modules.get(n) for n in _STUBBED}
        sys.modules.update({
            "httpx": _module("httpx", Response=object, get=None, post=None, put=None, delete=None,
                             HTTPStatusError=type("HTTPStatusError", (Exception,), {}),
                             HTTPError=type("HTTPError", (Exception,), {}),
                             TimeoutException=type("TimeoutException", (Exception,), {})),
            "agents.keys_agent": _module("agents.keys_agent", get_key=lambda k: "cid-" + k),
            "core.api_call_counters": _module("core.api_call_counters",
                                              increment_call_counter=lambda *a, **k: None),
            "core.conversion_types": _module("core.conversion_types", DEFAULT_ORDER_RECEIVED_ENDPOINT="x",
                                             clean_endpoint=lambda v: v),
        })
        for name in ("core.google_ads_service", "core.youtube_service", "core.gtm_service"):
            sys.modules.pop(name, None)
        import core.google_ads_service as ads
        import core.youtube_service as yt
        import core.gtm_service as gtm
        cls.services = {"ads": ads, "youtube": yt, "gtm": gtm}

    @classmethod
    def tearDownClass(cls):
        for name, module in cls._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_every_connect_asks_all_six_on_one_screen(self):
        redirects = set()
        for product, service in self.services.items():
            with self.subTest(product=product):
                url = service.build_consent_url("st-" + product)
                parsed = urlparse(url)
                self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                                 "https://accounts.google.com/o/oauth2/v2/auth")
                query = parse_qs(parsed.query)
                self.assertEqual(query["scope"][0].split(), CONSOLE_SIX)
                self.assertEqual(query["access_type"], ["offline"])
                self.assertEqual(query["prompt"], ["consent"])
                self.assertEqual(query["response_type"], ["code"])
                self.assertEqual(query["state"], ["st-" + product])
                for absent in ("include_granted_scopes", "login_hint", "hd"):
                    self.assertNotIn(absent, query)
                self.assertNotIn("select_account", url)
                for word in ROUND2_WORDS:
                    self.assertNotIn(word, query["scope"][0])
                redirects.add(query["redirect_uri"][0])
        # each product still lands on its own callback
        self.assertEqual(len(redirects), 3)

    def test_round2_flags_on_do_not_change_round1(self):
        saved = {f: os.environ.get(f) for f in ("GMAIL_SEND_ENABLED", "SEARCH_CONSOLE_ENABLED",
                                                 "ANALYTICS_ENABLED", "DRIVE_FILE_ENABLED")}
        try:
            for flag in saved:
                os.environ[flag] = "true"
            query = parse_qs(urlparse(self.services["gtm"].build_consent_url("s")).query)
            self.assertEqual(query["scope"][0].split(), CONSOLE_SIX)
        finally:
            for flag, value in saved.items():
                os.environ.pop(flag, None)
                if value is not None:
                    os.environ[flag] = value

    def test_each_tag_manager_write_names_its_scope(self):
        gtm = self.services["gtm"]
        base = "accounts/1/containers/2/workspaces/9"
        self.assertEqual(gtm.required_scope(f"{base}/tags"), CONSOLE_SIX[3])
        self.assertEqual(gtm.required_scope(f"{base}/triggers"), CONSOLE_SIX[3])
        self.assertEqual(gtm.required_scope(f"{base}:create_version"), CONSOLE_SIX[4])
        self.assertEqual(gtm.required_scope("accounts/1/containers/2/versions/12:publish"), CONSOLE_SIX[5])

    def test_missing_scopes_still_checks_the_three_gtm_ones(self):
        gtm = self.services["gtm"]
        self.assertEqual(gtm.missing_scopes(" ".join(CONSOLE_SIX[:3])), CONSOLE_SIX[3:])
        self.assertEqual(gtm.missing_scopes(" ".join(CONSOLE_SIX)), [])


class Diagnostic(unittest.TestCase):
    TOKEN = "1//refresh-SECRET-token-value"

    def setUp(self):
        from core import google_scopes as gs
        self.gs = gs
        self._saved = {n: getattr(gs, n) for n in ("_rows", "token_scopes", "_recorded_gtm_scopes", "_round2")}
        self._saved_vault = sys.modules.get("core.secrets_vault")
        sys.modules["core.secrets_vault"] = _module("core.secrets_vault", hydrate=lambda r: r)
        gs._round2 = lambda: {"GMAIL_SEND_ENABLED": False, "SEARCH_CONSOLE_ENABLED": False,
                              "ANALYTICS_ENABLED": False, "DRIVE_FILE_ENABLED": False}
        gs._recorded_gtm_scopes = lambda cid: None

    def tearDown(self):
        for name, fn in self._saved.items():
            setattr(self.gs, name, fn)
        if self._saved_vault is None:
            sys.modules.pop("core.secrets_vault", None)
        else:
            sys.modules["core.secrets_vault"] = self._saved_vault

    def test_old_three_scope_grant_shows_what_is_missing(self):
        self.gs._rows = lambda cid: [
            {"id": 9, "platform": "google_tagmanager", "status": "active", "account_id": "accounts/1/containers/2",
             "access_token": self.TOKEN},
            {"id": 8, "platform": "youtube", "status": "active", "account_id": "UC1", "access_token": self.TOKEN},
            {"id": 3, "platform": "youtube", "status": "inactive", "account_id": "UC0", "access_token": "old"},
        ]
        self.gs.token_scopes = lambda token: list(CONSOLE_SIX[:3])
        out = self.gs.diagnostic(27)
        self.assertEqual(out["code_scopes"], [s.rsplit("/", 1)[-1] for s in CONSOLE_SIX])
        self.assertEqual([c["platform"] for c in out["connections"]], ["google_tagmanager", "youtube"])
        gtm, yt = out["connections"]
        self.assertEqual(gtm["source"], "google")
        self.assertEqual(gtm["missing_for_feature"], ["tagmanager.edit.containers",
                                                      "tagmanager.edit.containerversions", "tagmanager.publish"])
        self.assertEqual(yt["missing_for_feature"], [])
        self.assertEqual(len(yt["missing_of_six"]), 3)
        self.assertFalse(out["has_all_six"])
        self.assertFalse(any(out["round2_flags"].values()))
        self.assertNotIn(self.TOKEN, repr(out))
        self.assertNotIn("SECRET", repr(out))

    def test_full_grant_has_all_six(self):
        self.gs._rows = lambda cid: [{"id": 9, "platform": "google_tagmanager", "status": "active",
                                      "access_token": self.TOKEN}]
        self.gs.token_scopes = lambda token: list(CONSOLE_SIX)
        out = self.gs.diagnostic(27)
        self.assertTrue(out["has_all_six"])
        self.assertEqual(out["connections"][0]["missing_of_six"], [])

    def test_dead_grant_falls_back_to_recorded_scopes_and_says_so(self):
        self.gs._rows = lambda cid: [{"id": 9, "platform": "google_tagmanager", "status": "active",
                                      "access_token": self.TOKEN}]

        def dead(token):
            raise RuntimeError("400 invalid_grant " + token)
        self.gs.token_scopes = dead
        self.gs._recorded_gtm_scopes = lambda cid: " ".join(CONSOLE_SIX[3:])
        out = self.gs.diagnostic(27)
        item = out["connections"][0]
        self.assertEqual(item["error"], "grant_dead")
        self.assertEqual(item["source"], "recorded_at_consent")
        self.assertNotIn(self.TOKEN, repr(out))

    def test_not_live_never_calls_google(self):
        self.gs._rows = lambda cid: [{"id": 9, "platform": "youtube", "status": "active", "access_token": "t"}]
        self.gs.token_scopes = lambda token: self.fail("Google called with live=False")
        out = self.gs.diagnostic(27, live=False)
        self.assertIsNone(out["connections"][0]["scopes"])

    def test_unreadable_connections_answer_without_raising(self):
        self.gs._rows = lambda cid: (_ for _ in ()).throw(RuntimeError("db down"))
        out = self.gs.diagnostic(27)
        self.assertIn("error", out)
        self.assertEqual(out["connections"], [])


class RoutesAndCard(unittest.TestCase):
    def test_admin_routes_require_the_admin_first(self):
        for name in ("admin_google_scopes", "admin_google_reconnect"):
            body = _function_source("core/api_server.py", name)
            first = body.index("_require_admin(request)")
            for later in ("google_scopes.", "get_client(", "build_consent_url"):
                if later in body:
                    self.assertGreater(body.index(later), first, f"{name}: {later} before the admin check")

    def test_reconnect_is_internal_clients_only_and_never_google_ads(self):
        body = _function_source("core/api_server.py", "admin_google_reconnect")
        self.assertIn('if not client.get("billing_exempt")', body)
        self.assertIn("ERR_INTERNAL_CLIENTS_ONLY", body)
        source = _read("core", "api_server.py")
        products = re.search(r"_RECONNECT_PRODUCTS = (\{[^}]*\})", source).group(1)
        self.assertEqual(ast.literal_eval(products), {"gtm": "core.gtm_service", "youtube": "core.youtube_service"})

    def test_diagnostic_route_is_get_only_and_returns_the_diagnostic(self):
        source = _read("core", "api_server.py")
        self.assertIn('@app.get("/api/admin/google/scopes")', source)
        self.assertNotIn('@app.post("/api/admin/google/scopes")', source)
        self.assertIn("google_scopes.diagnostic(client_id, live=live)",
                      _function_source("core/api_server.py", "admin_google_scopes"))

    def test_admin_page_shows_the_check_and_the_reconnect(self):
        html = _read("dashboard", "admin", "connections", "index.html")
        self.assertIn('id="googleScopes"', html)
        self.assertIn("/api/admin/google/scopes?client_id=", html)
        self.assertIn("/api/admin/google/reconnect/27?product=gtm", html)
        self.assertNotIn("access_token", html)

    def test_client_card_is_one_calm_line_without_tool_names(self):
        from core import agent_actions as aa
        spec = aa.ACTIONS["connect_tag_manager"]
        texts = list(spec["title"].values()) + list(spec["what"].values()) \
            + list(aa.ACTIONS["configure_conversion"]["refusals"]["gtm_reconnect"].values())
        for text in texts:
            for word in ("Tag Manager", "GTM", "tagmanager", "scope", "error", "שגיאה", "תקלה", "token"):
                self.assertNotIn(word, text)
        self.assertIn("עוד הרשאה אחת", spec["what"]["he"])
        self.assertIn("לאשר", spec["what"]["he"])

    def test_old_grants_reach_the_reconnect_card(self):
        from core import agent_actions as aa
        for reason in ("gtm_not_connected", "gtm_reconnect_needed", "gtm_token_dead"):
            self.assertEqual(aa._GTM_REFUSAL_CODES[reason], "gtm_reconnect")


if __name__ == "__main__":
    unittest.main()
