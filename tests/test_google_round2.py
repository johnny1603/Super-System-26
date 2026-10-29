"""Google verification ROUND 2 (docs/GOOGLE-ROUND2.md): Gmail send and
Search Console read, both behind flags that are OFF by default.

- Round1Frozen: round 1's scope lists (sign-in, Ads, YouTube, Tag Manager)
  are exactly what was submitted, and no round-1 consent builder depends on a
  round-2 flag.
- FlagsOff: with the flags unset nothing round-2 can happen — no consent URL,
  no API call, no chat card, no dashboard card, routes 404, no rank source.
- FlagsOn: what each flow does once switched on — consent URL scopes, the
  send-only Gmail module, the outreach rules (address on the business's own
  site, personal draft, opt-out line, suppression first, hard daily cap,
  budget guard, a record of every send), the chat card wiring, and Search
  Console keyword matching into the organic table.

Stdlib only. Run from the repo root:  python tests/test_google_round2.py -v
"""
import ast
import os
import re
import sys
import types
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_agent_actions import ActionTestCase  # noqa: E402
from core import agent_actions as aa  # noqa: E402

FLAGS = ("GMAIL_SEND_ENABLED", "SEARCH_CONSOLE_ENABLED")
_STUBBED = ("httpx", "agents.keys_agent", "core.google_ads_service", "core.secrets_vault",
            "core.gmail_service", "core.search_console_service")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _list_constant(path: str, name: str) -> list:
    for node in ast.parse(_read(*path.split("/"))).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return [ast.literal_eval(e) for e in node.value.elts]
    raise AssertionError(f"{name} not found in {path}")


def _function_source(path: str, name: str) -> str:
    source = _read(*path.split("/"))
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{name} not found in {path}")


class FlagEnv(unittest.TestCase):
    """Saves/clears the two flags around every test; `flags_on()` sets them."""

    def setUp(self):
        self._saved_env = {f: os.environ.pop(f, None) for f in FLAGS}

    def tearDown(self):
        for flag, value in self._saved_env.items():
            os.environ.pop(flag, None)
            if value is not None:
                os.environ[flag] = value

    def flags_on(self):
        for flag in FLAGS:
            os.environ[flag] = "true"


class Services(FlagEnv):
    """The two round-2 service modules, imported for real with httpx and the
    Google token helpers stubbed (every HTTP call is recorded)."""

    @classmethod
    def setUpClass(cls):
        cls._saved_modules = {n: sys.modules.get(n) for n in _STUBBED}
        cls.http = []

        class Resp:
            def __init__(self, status=200, body=None):
                self.status_code, self._body, self.text = status, body or {}, str(body)
                self.headers = {"content-type": "application/json"}

            def json(self):
                return self._body

        def call(method):
            def run(url, **kw):
                cls.http.append((method, url, kw))
                if url.endswith("/userinfo"):
                    return Resp(200, {"email": "owner@biz.co.il", "email_verified": True})
                if url.endswith("/messages/send"):
                    return Resp(200, {"id": "m1", "threadId": "t1"})
                if url.endswith("/sites"):
                    return Resp(200, {"siteEntry": [
                        {"siteUrl": "sc-domain:biz.co.il", "permissionLevel": "siteOwner"},
                        {"siteUrl": "https://other.com/", "permissionLevel": "siteUnverifiedUser"}]})
                if url.endswith("/searchAnalytics/query"):
                    return Resp(200, {"rows": [{"keys": ["Shoes  Tel Aviv"], "clicks": 12, "impressions": 340,
                                                "position": 4.26}]})
                return Resp(404, {})
            return run

        sys.modules.update({
            "httpx": _module("httpx", get=call("GET"), post=call("POST")),
            "agents.keys_agent": _module("agents.keys_agent", get_key=lambda k: "cid-" + k),
            "core.google_ads_service": _module("core.google_ads_service",
                                               OAUTH_CONSENT_URL="https://accounts.google.com/o/oauth2/v2/auth",
                                               _access_token=lambda rt: "at",
                                               exchange_code=lambda code, redirect: {"refresh_token": "rt"}),
            "core.secrets_vault": _module("core.secrets_vault", hydrate=lambda r: r),
        })
        for name in ("core.gmail_service", "core.search_console_service"):
            sys.modules.pop(name, None)
        from core import gmail_service, search_console_service
        cls.gmail, cls.sc = gmail_service, search_console_service

    @classmethod
    def tearDownClass(cls):
        for name, module in cls._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self):
        super().setUp()
        self.http.clear()


# ─── Round 1 is frozen ───────────────────────────────────────────────────────

ROUND1 = {
    ("core/client_auth.py", "GOOGLE_LOGIN_SCOPES"): ["openid", "https://www.googleapis.com/auth/userinfo.email"],
    ("core/google_ads_service.py", "ADS_SCOPES"): ["https://www.googleapis.com/auth/adwords"],
    ("core/youtube_service.py", "YOUTUBE_SCOPES"): ["https://www.googleapis.com/auth/youtube.upload",
                                                    "https://www.googleapis.com/auth/youtube.readonly"],
    ("core/gtm_service.py", "GTM_SCOPES"): ["https://www.googleapis.com/auth/tagmanager.edit.containers",
                                            "https://www.googleapis.com/auth/tagmanager.edit.containerversions",
                                            "https://www.googleapis.com/auth/tagmanager.publish"],
}
ROUND1_BUILDERS = {"core/client_auth.py": ("google_login_url", "GOOGLE_LOGIN_SCOPES"),
                   "core/google_ads_service.py": ("build_consent_url", "ADS_SCOPES"),
                   "core/youtube_service.py": ("build_consent_url", "YOUTUBE_SCOPES"),
                   "core/gtm_service.py": ("build_consent_url", "GTM_SCOPES")}


class Round1Frozen(unittest.TestCase):
    def test_round1_scope_lists_are_unchanged(self):
        for (path, name), expected in ROUND1.items():
            self.assertEqual(_list_constant(path, name), expected, f"{path} {name}")

    def test_round1_consent_builders_use_only_their_own_list(self):
        for path, (function, constant) in ROUND1_BUILDERS.items():
            body = _function_source(path, function)
            self.assertIn(constant, body, path)
            for word in ("round2", "GMAIL", "SEARCH_CONSOLE", "gmail", "webmasters", "include_granted_scopes"):
                self.assertNotIn(word, body, f"{path}.{function} mentions {word}")

    def test_no_round1_module_imports_a_round2_one(self):
        for path in ROUND1_BUILDERS:
            source = _read(*path.split("/"))
            for name in ("gmail_service", "search_console_service", "round2_flags"):
                self.assertNotIn(name, source, f"{path} imports {name}")


# ─── Flags off (the default) ─────────────────────────────────────────────────

class FlagsOff(Services):
    def test_flags_default_off(self):
        from core import round2_flags
        self.assertFalse(round2_flags.gmail_send_enabled())
        self.assertFalse(round2_flags.search_console_enabled())

    def test_no_consent_url_and_no_api_call(self):
        with self.assertRaises(self.gmail.GmailSendDisabled):
            self.gmail.build_consent_url("state")
        with self.assertRaises(self.gmail.GmailSendDisabled):
            self.gmail.exchange_code("code")
        with self.assertRaises(self.gmail.GmailSendDisabled):
            self.gmail.send_message("rt", self.gmail.build_message("a@b.co", "", "c@d.co", "s", "b"))
        with self.assertRaises(self.sc.SearchConsoleDisabled):
            self.sc.build_consent_url("state")
        with self.assertRaises(self.sc.SearchConsoleDisabled):
            self.sc.list_verified_sites("rt")
        with self.assertRaises(self.sc.SearchConsoleDisabled):
            self.sc.query_by_keyword("rt", "sc-domain:x.com", "2026-10-01", "2026-10-28")
        self.assertEqual(self.http, [])

    def test_send_refused_before_anything_is_read(self):
        from core import gmail_outreach
        saved = gmail_outreach._db
        gmail_outreach._db = lambda: (_ for _ in ()).throw(AssertionError("db read with the flag off"))
        try:
            with self.assertRaises(gmail_outreach.SendFailed) as ctx:
                gmail_outreach.send_approved(27, {})
        finally:
            gmail_outreach._db = saved
        self.assertEqual(ctx.exception.code, "send_disabled")

    def test_outreach_policy_says_sending_is_off(self):
        from core import outreach_policy
        self.assertFalse(outreach_policy.sending_enabled())

    def test_rank_source_not_connected_and_reads_nothing(self):
        from core import rank_tracking
        provider = next(p for p in rank_tracking.PROVIDERS if p.id == "search_console")
        saved = provider._connection
        provider._connection = lambda cid: (_ for _ in ()).throw(AssertionError("read with the flag off"))
        try:
            self.assertFalse(provider.is_connected(27))
        finally:
            provider._connection = saved

    def test_every_round2_route_checks_its_flag_first(self):
        source = _read("core", "api_server.py")
        tree = ast.parse(source)
        routes = {"gmail_oauth_start", "gmail_oauth_callback", "search_console_oauth_start",
                  "search_console_oauth_callback", "client_search_console_sites", "client_search_console_choose"}
        seen = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in routes:
                seen.add(node.name)
                calls = [n for n in node.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
                self.assertTrue(calls, node.name)
                self.assertEqual(getattr(calls[0].value.func, "id", ""), "_round2_or_404", node.name)
                # The flag check comes before the session / state is even read
                first_call_line = calls[0].lineno
                body = ast.get_source_segment(source, node)
                for later in ("_require_session", "verify_oauth_state_token"):
                    for n in ast.walk(node):
                        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == later:
                            self.assertGreater(n.lineno, first_call_line, f"{node.name}: {later} first")
                self.assertIn("_round2_or_404(", body)
        self.assertEqual(seen, routes)

    def test_dashboard_cards_hidden_unless_the_server_says_so(self):
        html = _read("dashboard", "client", "index.html")
        for card in ("gmailCard", "scCard"):
            self.assertRegex(html, rf'id="{card}" style="display:none;"')
        self.assertIn("features.gmail_send ? '' : 'none'", html)
        self.assertIn("features.search_console ? '' : 'none'", html)
        features = _function_source("core/api_server.py", "_round2_features")
        self.assertIn("if gmail_send_enabled():", features)

    def test_no_round2_chat_action_is_offered(self):
        from test_agent_actions import CLIENTS
        state = {"channels": {"by_channel": {"organic_seo": {"status": "connected_no_data"}}},
                 "connections": {"active_platforms": ["gmail_send"]}}
        names = {a["name"] for a in aa.available(27, "client_chat", state, client=CLIENTS[27])}
        self.assertFalse({"send_outreach_email", "connect_gmail_send", "connect_search_console"} & names)


# ─── Flags on ────────────────────────────────────────────────────────────────

class ConsentScopes(Services):
    def test_gmail_consent_asks_send_only(self):
        self.flags_on()
        query = parse_qs(urlparse(self.gmail.build_consent_url("st")).query)
        self.assertEqual(query["scope"][0].split(), ["openid", "https://www.googleapis.com/auth/userinfo.email",
                                                     "https://www.googleapis.com/auth/gmail.send"])
        self.assertEqual(query["redirect_uri"][0], "https://app.uallak.com/api/oauth/gmail/callback")
        self.assertNotIn("include_granted_scopes", query)

    def test_search_console_consent_asks_read_only(self):
        self.flags_on()
        query = parse_qs(urlparse(self.sc.build_consent_url("st")).query)
        self.assertEqual(query["scope"][0].split(), ["https://www.googleapis.com/auth/webmasters.readonly"])
        self.assertNotIn("include_granted_scopes", query)

    def test_gmail_module_can_only_send(self):
        source = _read("core", "gmail_service.py")
        source = source.split('"""', 2)[2]          # the module docstring describes what it does NOT do
        urls = set(re.findall(r'"(https://[^"]+)"', source))
        self.assertEqual({u for u in urls if "gmail.googleapis.com" in u},
                         {"https://gmail.googleapis.com/gmail/v1/users/me/messages/send"})
        for forbidden in ("gmail.readonly", "gmail.modify", "mail.google.com", "/threads", "/labels",
                          "/history", "messages.list", "messages.get", "/drafts"):
            self.assertNotIn(forbidden, source)
        code = "\n".join(l for l in source.splitlines() if not l.strip().startswith("#"))
        self.assertEqual(len(re.findall(r"httpx\.(get|post|put|patch|delete)\(", code)), 2)   # userinfo + send

    def test_no_mailbox_read_anywhere(self):
        for folder in ("core", "agents"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if name.endswith(".py"):
                    source = _read(folder, name)
                    self.assertNotIn("gmail/v1/users/me/messages?", source, name)
                    self.assertNotIn("gmail/v1/users/me/threads", source, name)
                    self.assertNotIn("auth/gmail.readonly", source, name)

    def test_send_builds_one_raw_message(self):
        self.flags_on()
        message = self.gmail.build_message("owner@biz.co.il", "Biz", "shop@other.co.il", "Hi", "Body")
        self.assertEqual(self.gmail.send_message("rt", message), {"id": "m1", "threadId": "t1"})
        (method, url, kw), = self.http
        self.assertEqual((method, url), ("POST", self.gmail.SEND_URL))
        self.assertEqual(set(kw["json"]), {"raw"})

    def test_search_console_lists_only_verified_sites(self):
        self.flags_on()
        self.assertEqual(self.sc.list_verified_sites("rt"),
                         [{"site_url": "sc-domain:biz.co.il", "permission": "siteOwner"}])

    def test_site_matching(self):
        self.assertTrue(self.sc.site_matches_domain("sc-domain:biz.co.il", "www.biz.co.il"))
        self.assertTrue(self.sc.site_matches_domain("sc-domain:biz.co.il", "shop.biz.co.il"))
        self.assertTrue(self.sc.site_matches_domain("https://www.biz.co.il/", "biz.co.il"))
        self.assertFalse(self.sc.site_matches_domain("https://biz.co.il.evil.com/", "biz.co.il"))
        self.assertFalse(self.sc.site_matches_domain("sc-domain:notbiz.co.il", "biz.co.il"))


class SearchConsoleRanks(Services):
    def setUp(self):
        super().setUp()
        self.flags_on()
        from core import rank_tracking
        self.rt = rank_tracking
        self.provider = next(p for p in rank_tracking.PROVIDERS if p.id == "search_console")
        self.provider._cache.clear()
        self.saved = (self.provider._connection, rank_tracking.get_target_keywords)
        self.provider._connection = lambda cid: {"access_token": "rt", "account_id": "sc-domain:biz.co.il"}
        rank_tracking.get_target_keywords = lambda cid: {"available": True, "keywords": [
            {"keyword": "shoes tel aviv", "target_position": 3},
            {"keyword": "bridal shoes", "target_position": 1}]}

    def tearDown(self):
        self.provider._connection, self.rt.get_target_keywords = self.saved
        self.provider._cache.clear()
        super().tearDown()

    def test_target_keywords_matched_with_clicks_and_average_position(self):
        table = self.rt.organic_table(27)
        self.assertTrue(table["tracking_connected"])
        rows = {r["keyword"]: r for r in table["rows"]}
        self.assertEqual(rows["shoes tel aviv"]["current_position"], 4.3)
        self.assertEqual(rows["shoes tel aviv"]["clicks"], 12)
        self.assertEqual(rows["shoes tel aviv"]["impressions"], 340)
        self.assertEqual(rows["shoes tel aviv"]["position_kind"], "average")
        self.assertEqual(rows["shoes tel aviv"]["source"], "source_search_console")
        self.assertEqual(rows["bridal shoes"]["status"], "no_data")

    def test_readings_are_cached(self):
        self.rt.organic_table(27)
        calls = len(self.http)
        self.rt.organic_table(27)
        self.assertEqual(len(self.http), calls)


# ─── Outreach: drafting and sending ──────────────────────────────────────────

class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.filters, self.payload, self.n = db, table, "select", [], None, None

    def select(self, *_a):
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def eq(self, c, v):
        self.filters.append(lambda r: r.get(c) == v)
        return self

    def gte(self, c, v):
        self.filters.append(lambda r: str(r.get(c, "")) >= v)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self.n = n
        return self

    def execute(self):
        if self.table in self.db.broken:
            raise RuntimeError("PGRST205 missing table")
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            rows.append(dict(self.payload))
            return types.SimpleNamespace(data=[self.payload])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        return types.SimpleNamespace(data=hit[:self.n or None])


class _DB:
    def __init__(self):
        self.tables, self.broken = {}, set()

    def table(self, name):
        return _Q(self, name)


SITE_HTML = """<html><body><h1>Lia Bridal Studio</h1>
<p>Handmade lace wedding dresses since 2009, fitted in our Florentin workshop.</p>
<a href="/contact">צור קשר</a></body></html>"""
CONTACT_HTML = "<html><body>Write to us: hello@liabridal.co.il</body></html>"
GOOD_DRAFT = {"to_email": "hello@liabridal.co.il", "business_name": "Lia Bridal Studio",
              "business_url": "https://liabridal.co.il/", "hook": "lace wedding dresses since 2009",
              "subject": "Shoes for your brides",
              "body": "Hello Lia Bridal Studio, I saw your handmade lace wedding dresses since 2009. "
                      "We make custom bridal shoes in Tel Aviv — would you like details about referring brides?"}


class Outreach(Services):
    def setUp(self):
        super().setUp()
        self.flags_on()
        from core import gmail_agent_plan, gmail_outreach, outreach_policy
        self.go, self.policy, self.plan = gmail_outreach, outreach_policy, gmail_agent_plan
        self.db = _DB()
        self.db.tables["client_accounts"] = [{"client_id": 27, "platform": "gmail_send", "status": "active",
                                              "account_id": "owner@shoes.co.il", "access_token": "rt", "id": 1}]
        self.sent_messages = []
        self.saved = {
            (gmail_outreach, "_db"): gmail_outreach._db, (gmail_outreach, "_fetch"): gmail_outreach._fetch,
            (gmail_outreach, "_client"): gmail_outreach._client, (outreach_policy, "_db"): outreach_policy._db,
            (gmail_agent_plan, "entitled"): gmail_agent_plan.entitled,
            (gmail_agent_plan, "require_budget"): gmail_agent_plan.require_budget,
            (gmail_agent_plan, "_count"): gmail_agent_plan._count,
            (self.gmail, "send_message"): self.gmail.send_message,
        }
        pages = {"https://liabridal.co.il/": SITE_HTML, "https://liabridal.co.il/contact": CONTACT_HTML,
                 "https://liabridal.co.il/robots.txt": "User-agent: *\nAllow: /"}
        gmail_outreach._db = outreach_policy._db = lambda: self.db
        gmail_outreach._fetch = lambda url: pages.get(url, "")
        gmail_outreach._client = lambda cid: {"id": cid, "name": "Shoes by Dana"}
        gmail_agent_plan.entitled = lambda cid: True
        gmail_agent_plan.require_budget = lambda cid, est: None
        gmail_agent_plan._count = lambda counter: 1
        self.gmail.send_message = lambda rt, message: self.sent_messages.append(message) or {"id": "gm1"}

    def tearDown(self):
        for (module, name), fn in self.saved.items():
            setattr(module, name, fn)
        super().tearDown()

    def prepared(self, **changes):
        return self.go.prepare_draft(27, {**GOOD_DRAFT, **changes}, "en", [])

    def test_personal_draft_becomes_the_exact_card_with_the_opt_out(self):
        params = self.prepared()
        self.assertEqual(set(params), self.go.STORED_KEYS)
        self.assertTrue(params["body"].endswith(self.policy.opt_out_line("en")))
        self.assertEqual(params["source_url"], "https://liabridal.co.il/")

    def test_address_must_be_published_on_the_business_site(self):
        with self.assertRaises(self.go.DraftRefused) as ctx:
            self.prepared(to_email="owner@liabridal.co.il")
        self.assertEqual(ctx.exception.code, "address_not_on_site")

    def test_generic_draft_refused(self):
        with self.assertRaises(self.go.DraftRefused) as ctx:
            self.prepared(hook="great service", body="Hello Lia Bridal Studio, we offer great service. Interested?")
        self.assertEqual(ctx.exception.code, "not_personal")

    def test_near_copy_of_an_earlier_draft_refused(self):
        # An earlier card (stored params carry the opt-out) to another business, same text
        earlier = [{"params": {"body": self.policy.with_opt_out(
            GOOD_DRAFT["body"].replace("Lia Bridal Studio", "Noa Bridal"), "en")}}]
        with self.assertRaises(self.go.DraftRefused) as ctx:
            self.go.prepare_draft(27, GOOD_DRAFT, "en", earlier)
        self.assertEqual(ctx.exception.code, "not_personal")

    def test_suppressed_address_refused_at_draft_time(self):
        self.db.tables["outreach_suppression"] = [{"key_type": "email", "key_value": "hello@liabridal.co.il"}]
        with self.assertRaises(self.go.DraftRefused) as ctx:
            self.prepared()
        self.assertEqual(ctx.exception.code, "suppressed")

    def test_robots_disallow_means_nothing_is_read(self):
        self.go._fetch = lambda url: ("User-agent: *\nDisallow: /" if url.endswith("robots.txt") else SITE_HTML)
        with self.assertRaises(self.go.DraftRefused) as ctx:
            self.prepared()
        self.assertEqual(ctx.exception.code, "site_unreadable")

    def test_send_records_every_send_and_goes_from_the_clients_gmail(self):
        params = self.prepared()
        result = self.go.send_approved(27, params)
        self.assertEqual(result["message_id"], "gm1")
        (message,) = self.sent_messages
        self.assertEqual(message["To"], "hello@liabridal.co.il")
        self.assertIn("owner@shoes.co.il", message["From"])
        self.assertIn(self.policy.opt_out_line("en"), message.get_content())
        (record,) = self.db.tables["outreach_sends"]
        self.assertEqual((record["client_id"], record["to_email"], record["gmail_message_id"]),
                         (27, "hello@liabridal.co.il", "gm1"))

    def test_one_message_per_business(self):
        params = self.prepared()
        self.go.send_approved(27, params)
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_approved(27, params)
        self.assertEqual(ctx.exception.code, "already_contacted")
        self.assertEqual(len(self.sent_messages), 1)

    def test_suppression_checked_first_at_send_time(self):
        params = self.prepared()
        self.db.tables["outreach_suppression"] = [{"key_type": "domain", "key_value": "liabridal.co.il"}]
        self.plan.entitled = lambda cid: (_ for _ in ()).throw(AssertionError("entitlement before suppression"))
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_approved(27, params)
        self.assertEqual(ctx.exception.code, "suppressed")
        self.assertEqual(self.sent_messages, [])

    def test_hard_daily_cap_from_the_send_record(self):
        params = self.prepared()
        today = datetime.now(timezone.utc).isoformat()
        self.db.tables["outreach_sends"] = [{"client_id": 27, "to_email": f"x{i}@a.co", "sent_at": today}
                                            for i in range(self.plan.MESSAGES_PER_CLIENT_PER_DAY)]
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_approved(27, params)
        self.assertEqual(ctx.exception.code, "daily_cap")
        self.assertEqual(self.plan.MESSAGES_PER_CLIENT_PER_DAY, 10)

    def test_unreadable_record_counts_as_cap_reached(self):
        params = self.prepared()
        self.db.broken.add("outreach_sends")
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_approved(27, params)
        self.assertEqual(ctx.exception.code, "daily_cap")

    def test_budget_guard(self):
        params = self.prepared()

        def exhausted(cid, est):
            raise self.plan.BudgetExhausted(100.0, 100.0)
        self.plan.require_budget = exhausted
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_approved(27, params)
        self.assertEqual(ctx.exception.code, "budget")
        self.assertEqual(self.sent_messages, [])

    def test_not_entitled(self):
        params = self.prepared()
        self.plan.entitled = lambda cid: False
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_approved(27, params)
        self.assertEqual(ctx.exception.code, "not_entitled")

    def test_revoked_grant_means_reconnect(self):
        params = self.prepared()

        def denied(rt, message):
            raise self.gmail.GmailSendFailed(401, "invalid credentials")
        self.gmail.send_message = denied
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_approved(27, params)
        self.assertEqual(ctx.exception.code, "needs_reconnect")

    def test_stored_card_is_rechecked(self):
        params = self.prepared()
        self.assertEqual(self.go.revalidate_stored(params), params)
        for broken in ({**params, "body": "no opt out here, Lia Bridal Studio"}, {**params, "extra": 1},
                       {**params, "to_email": "not-an-address"}):
            with self.assertRaises(ValueError):
                self.go.revalidate_stored(broken)


class OutreachCard(ActionTestCase):
    STATE = {"channels": {"by_channel": {}}, "connections": {"active_platforms": ["gmail_send"]}}

    def setUp(self):
        super().setUp()
        self._saved_env = {f: os.environ.pop(f, None) for f in FLAGS}
        os.environ["GMAIL_SEND_ENABLED"] = "true"
        from core import gmail_outreach
        self.go = gmail_outreach
        self.saved_go = (gmail_outreach.prepare_draft, gmail_outreach.send_approved)
        self.stored = {"to_email": "hello@liabridal.co.il", "business_name": "Lia Bridal Studio",
                       "subject": "Shoes for your brides", "language": "he", "source_url": "https://liabridal.co.il/",
                       "body": "שלום Lia Bridal Studio ...\n\n" + __import__("core.outreach_policy").outreach_policy.opt_out_line("he")}
        gmail_outreach.prepare_draft = lambda cid, params, lang, rows: dict(self.stored)
        self.sends = []
        gmail_outreach.send_approved = lambda cid, params: self.sends.append(params) or {
            "business_name": params["business_name"], "to_email": params["to_email"]}

    def tearDown(self):
        self.go.prepare_draft, self.go.send_approved = self.saved_go
        for flag, value in self._saved_env.items():
            os.environ.pop(flag, None)
            if value is not None:
                os.environ[flag] = value
        super().tearDown()

    def propose(self, client_id=40):
        return aa.propose(client_id, "client_chat", "general",
                          {"name": "send_outreach_email", "params": GOOD_DRAFT}, state=self.STATE)

    def test_card_shows_the_exact_email_and_the_client_approves_it(self):
        result = self.propose()
        card = result["card"]
        self.assertTrue(card["can_decide"])
        self.assertEqual(card["details"]["body"], self.stored["body"])
        self.assertIn("Lia Bridal Studio", card["what"])
        self.assertEqual(self.rows()[0]["params"], self.stored)      # the prepared draft, not the model's
        done = aa.decide(card["id"], "client", 40, "approve")
        self.assertEqual(done["status"], "done")
        self.assertEqual(self.sends, [self.stored])
        self.assertIn("Lia Bridal Studio", self.posted_client[-1][2])

    def test_one_card_one_email(self):
        card = self.propose()["card"]
        aa.decide(card["id"], "client", 40, "approve")
        self.assertTrue(aa.decide(card["id"], "client", 40, "approve")["already"])
        self.assertEqual(len(self.sends), 1)

    def test_refused_draft_is_a_plain_line_not_a_card(self):
        def refuse(cid, params, lang, rows):
            raise self.go.DraftRefused("address_not_on_site")
        self.go.prepare_draft = refuse
        result = self.propose()
        self.assertNotIn("card", result)
        self.assertEqual(result["refuse"], aa._OUTREACH_DRAFT_REFUSALS["address_not_on_site"]["he"])

    def test_send_refusals_are_explained_and_reconnect_brings_the_connect_card(self):
        from core import gmail_agent_plan
        saved = gmail_agent_plan.entitled
        gmail_agent_plan.entitled = lambda cid: True
        try:
            for code in aa._OUTREACH_SEND_REFUSALS:
                with self.subTest(code=code):
                    def fail(cid, params, c=code):
                        raise self.go.SendFailed(c)
                    self.go.send_approved = fail
                    card = self.propose()["card"]
                    result = aa.decide(card["id"], "client", 40, "approve")
                    self.assertEqual(result["message"], aa._OUTREACH_SEND_REFUSALS[code]["he"])
                    if code == "needs_reconnect":
                        self.assertEqual(result["next_card"]["action"], "connect_gmail_send")
                    self.rows().clear()
        finally:
            gmail_agent_plan.entitled = saved
        self.assertEqual(self.alerts, [])

    def test_refusal_texts_in_every_language(self):
        for table in (aa._OUTREACH_DRAFT_REFUSALS, aa._OUTREACH_SEND_REFUSALS):
            for code, texts in table.items():
                self.assertEqual(set(texts), set(aa.LANGS), code)

    def test_connect_cards_only_when_flagged_and_relevant(self):
        from core import gmail_agent_plan
        from test_agent_actions import CLIENTS
        saved = gmail_agent_plan.entitled
        gmail_agent_plan.entitled = lambda cid: cid == 27
        os.environ["SEARCH_CONSOLE_ENABLED"] = "true"
        try:
            state = {"channels": {"by_channel": {"organic_seo": {"status": "connected_no_data"}}},
                     "connections": {"active_platforms": []}}
            names27 = {a["name"] for a in aa.available(27, "client_chat", state, client=CLIENTS[27])}
            names40 = {a["name"] for a in aa.available(40, "client_chat", state, client=CLIENTS[40])}
        finally:
            gmail_agent_plan.entitled = saved
        self.assertIn("connect_gmail_send", names27)
        self.assertNotIn("connect_gmail_send", names40)          # no Gmail agent package
        self.assertIn("connect_search_console", names27)
        self.assertNotIn("send_outreach_email", names27)          # not connected yet


# ─── The docs ────────────────────────────────────────────────────────────────

class Round2Doc(unittest.TestCase):
    def test_doc_covers_every_round2_scope(self):
        doc = _read("docs", "GOOGLE-ROUND2.md")
        matrix = doc.split("## Evidence matrix", 1)[1].split("\n## ", 1)[0]
        for short in ("gmail.send", "webmasters.readonly"):
            self.assertEqual(len([l for l in matrix.splitlines() if l.startswith(f"| `{short}`")]), 1, short)
            self.assertIn(f"https://www.googleapis.com/auth/{short}", doc)
        for heading in ("## Justifications", "## Demo video", "## Switching it on"):
            self.assertIn(heading, doc)


if __name__ == "__main__":
    unittest.main()
