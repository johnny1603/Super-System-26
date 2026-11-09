"""The store with ZERO client work (2026-11-07, docs/STORE.md).

- Default, no store login: our own purchase records (core/store_purchases)
  from the record tag we publish with the purchase measurement; nothing about
  the buyer is ever read, sent or stored; once per order; capped.
- Optional ONE-click connection: WooCommerce's own /wc-auth/v1/authorize
  (scope=read). The store POSTs the key to our https callback; only a
  read-only key for the store WE pinned is kept; single-use state.
- No client route or screen takes a key; the manual key is admin-only.

Stdlib only. Run:  python -I -B tests/test_store_one_click.py -v
"""
import ast
import base64
import json
import os
import re
import sys
import time
import types
import unittest
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_store_reads as sr  # noqa: E402  (its stubs: FakeStore, client_agent, website_agent)

store_agent, wc, STORE, ACCOUNTS, DB_WRITES = sr.store_agent, sr.wc, sr.STORE, sr.ACCOUNTS, sr.DB_WRITES
KEY1 = sr.KEY1

sys.modules["agents.keys_agent"] = types.ModuleType("agents.keys_agent")
sys.modules["agents.keys_agent"].get_key = lambda name: "test-session-secret"
_httpx = sys.modules["httpx"]
for _name in ("HTTPError", "HTTPStatusError", "RequestError", "TimeoutException", "ConnectError",
              "Response", "Request", "Client", "Timeout"):
    if not hasattr(_httpx, _name):
        setattr(_httpx, _name, type(_name, (Exception,), {}))
for _name in ("post", "put", "delete", "request"):
    if not hasattr(_httpx, _name):
        setattr(_httpx, _name, lambda *a, **k: None)
sys.modules.pop("core.session", None)
from core import session  # noqa: E402
from core import gtm_service as gtm  # noqa: E402
from core import store_purchases as sp  # noqa: E402


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class FakeTable:
    """Enough of supabase's query builder for app_settings and the records."""

    def __init__(self, db, name):
        self.db, self.name, self.filters, self.op, self.payload = db, name, [], "select", None
        self.want_count = False

    def select(self, *_a, count=None):
        self.want_count = bool(count)
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r: str(r.get(col)) >= str(val))
        return self

    def lt(self, col, val):
        self.filters.append(lambda r: str(r.get(col)) < str(val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def range(self, *_a):
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", row
        return self

    def delete(self):
        self.op = "delete"
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            if self.name == sp.TABLE and any(r["client_id"] == self.payload["client_id"]
                                             and r["order_ref"] == self.payload["order_ref"] for r in rows):
                raise Exception("duplicate key value violates unique constraint (23505)")
            rows.append(dict(self.payload))
            return types.SimpleNamespace(data=[self.payload], count=None)
        if self.op == "upsert":
            rows[:] = [r for r in rows if r.get("key") != self.payload["key"]] + [dict(self.payload)]
            return types.SimpleNamespace(data=[self.payload], count=None)
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "delete":
            rows[:] = [r for r in rows if r not in hit]
            return types.SimpleNamespace(data=hit, count=None)
        return types.SimpleNamespace(data=[dict(r) for r in hit], count=len(hit) if self.want_count else None)


class FakeDB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return FakeTable(self, name)


class Base(sr.Base):
    def setUp(self):
        super().setUp()
        self.db = FakeDB()
        self.saved_db = (store_agent._db, sp._db)
        store_agent._db = sp._db = lambda: self.db
        sp._cache.clear()
        sp._rate.clear()

    def tearDown(self):
        store_agent._db, sp._db = self.saved_db
        super().tearDown()


# ─── One click: the authorize link ───────────────────────────────────────────

class AuthorizeLink(Base):
    def test_documented_parameters_read_scope_and_https_callback(self):
        url = store_agent.authorize_url("https://shop-one.example/", "STATE",
                                        "https://app.example/api/store/wc-auth/return",
                                        "https://app.example/api/store/wc-auth/callback")
        parsed = urlparse(url)
        # The query form of /wc-auth/v1/authorize: WC_Auth reads wc-auth-version
        # and wc-auth-route from $_GET, so plain permalinks work too
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", "https://shop-one.example/")
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.assertEqual(q, {"wc-auth-version": "1", "wc-auth-route": "authorize",
                             "app_name": "uallak", "scope": "read", "user_id": "STATE",
                             "return_url": "https://app.example/api/store/wc-auth/return",
                             "callback_url": "https://app.example/api/store/wc-auth/callback"})

    def test_callback_must_be_https(self):
        with self.assertRaises(ValueError):
            store_agent.authorize_url("https://shop-one.example", "S", "https://a/r", "http://a/c")

    def test_store_site_from_what_we_already_have(self):
        self.assertEqual(store_agent.store_site(1), "https://shop-one.example")   # the website connection
        self.assertEqual(store_agent.store_site(3), "")                           # nothing known
        self.db.tables["client_accounts"] = [{"client_id": 3, "platform": "woocommerce",
                                              "account_id": "https://old-shop.example"}]
        self.assertEqual(store_agent.store_site(3), "https://old-shop.example")   # an earlier store row


class StoreAddress(Base):
    def test_typed_addresses_are_cleaned_to_https(self):
        for typed, want in (("shop.co.il", "https://shop.co.il"),
                            ("https://www.shop.co.il/", "https://www.shop.co.il"),
                            ("www.shop.co.il/he/", "https://www.shop.co.il/he"),
                            ("https://shop.co.il/wp-admin/admin.php?page=x", "https://shop.co.il"),
                            ("https://shop.co.il/shop/", "https://shop.co.il"),
                            ("SHOP.Example.com", "https://shop.example.com")):
            with self.subTest(typed=typed):
                self.assertEqual(store_agent.clean_address(typed), want)

    def test_not_a_public_address(self):
        for typed in ("", "shop", "localhost", "https://127.0.0.1", "10.0.0.1", "https://[::1]",
                      "https://user:pw@shop.co.il", "https://shop.co.il:8443", "shop .co.il",
                      "metadata.google.internal", "printer.local", "ftp://shop.co.il"):
            with self.subTest(typed=typed):
                self.assertEqual(store_agent.clean_address(typed), "")
        with self.assertRaises(wc.StoreNeedsHttps):
            store_agent.clean_address("http://shop.co.il")

    def test_store_target_codes(self):
        self.assertEqual(store_agent.store_target(1), {"site": "https://shop-one.example"})
        self.assertEqual(store_agent.store_target(3), {"code": "store_no_site"})
        self.assertEqual(store_agent.store_target(3, "my-shop.co.il"), {"site": "https://my-shop.co.il"})
        self.assertEqual(store_agent.store_target(3, "not an address"), {"code": "store_bad_address"})
        self.assertEqual(store_agent.store_target(3, "http://my-shop.co.il"), {"code": "store_needs_https"})
        # A typed address wins over what we know
        self.assertEqual(store_agent.store_target(1, "other.example"), {"site": "https://other.example"})

    def test_an_http_website_says_https_not_connect_the_website(self):
        self.db.tables["client_accounts"] = [{"client_id": 3, "platform": "woocommerce",
                                              "account_id": "http://plain-shop.example"}]
        self.assertEqual(store_agent.store_target(3), {"code": "store_needs_https"})


class ExplicitAsk(Base):
    ASKS = ("אני רוצה לחבר את החנות", "חבר את החנות", "תחבר לי את החנות בבקשה", "אפשר לחבר את החנות?",
            "חברו את החנות שלי", "איך מחברים את החנות", "חיבור החנות", "ולחבר גם את החנות",
            "לחבר את ווקומרס", "connect my store", "Please connect the online store", "link my shop",
            "can you connect woocommerce")
    NOT_ASKS = ("כמה מכרנו אתמול?", "חבר שלי פתח חנות", "האם החנות מחוברת?", "לנתק את החנות",
                "disconnect my store", "מה הסטטוס של הזמנה 101?", "החנות לא עובדת", "")

    def test_explicit_asks_match(self):
        for text in self.ASKS:
            with self.subTest(text=text):
                self.assertTrue(store_agent.wants_to_connect(text))

    def test_other_messages_do_not(self):
        for text in self.NOT_ASKS:
            with self.subTest(text=text):
                self.assertFalse(store_agent.wants_to_connect(text))

    def _with_actions(self, propose):
        fake = types.ModuleType("core.agent_actions")
        fake._lang = lambda cid: "he"
        fake._pick = lambda texts, lang: texts.get(lang) or texts["he"]
        fake.propose = propose
        saved = sys.modules.get("core.agent_actions")
        sys.modules["core.agent_actions"] = fake
        import core
        saved_attr = getattr(core, "agent_actions", None)
        core.agent_actions = fake
        self.addCleanup(lambda: (sys.modules.__setitem__("core.agent_actions", saved) if saved
                                 else sys.modules.pop("core.agent_actions", None),
                                 setattr(core, "agent_actions", saved_attr) if saved_attr
                                 else (hasattr(core, "agent_actions") and delattr(core, "agent_actions"))))

    def test_an_ask_gets_the_one_click_card_by_code(self):
        seen = []

        def propose(cid, origin, persona, raw, **kw):
            seen.append((cid, origin, persona, raw, kw.get("by_code")))
            return {"card": {"id": 5, "action": "connect_store", "button": "חיבור החנות בקליק אחד"}}
        self._with_actions(propose)
        turn = store_agent.connect_turn(3, "אני רוצה לחבר את החנות", "general")
        self.assertEqual(seen, [(3, "client_chat", "general", {"name": "connect_store", "params": {}}, True)])
        self.assertEqual(turn["action"]["action"], "connect_store")
        self.assertIn("בקליק אחד", turn["reply"])
        for word in ("מפתח", "wp-admin", "ck_", "cs_", "REST"):
            self.assertNotIn(word, turn["reply"])

    def test_connected_store_gets_one_line_and_no_card(self):
        self._with_actions(lambda *a, **k: self.fail("no card for a connected store"))
        ACCOUNTS[1] = {"client_id": 1, "platform": "woocommerce", "account_id": "https://shop-one.example",
                       "access_token": f"{KEY1[0]}:{KEY1[1]}", "status": "active"}
        turn = store_agent.connect_turn(1, "חבר את החנות")
        self.assertIsNone(turn["action"])
        self.assertIn("כבר מחוברת", turn["reply"])

    def test_not_an_ask_or_no_card_leaves_it_to_the_model(self):
        self._with_actions(lambda *a, **k: {"error": "storage_unavailable"})
        self.assertEqual(store_agent.connect_turn(3, "כמה מכרנו אתמול?"), {})
        self.assertEqual(store_agent.connect_turn(3, "חבר את החנות"), {})


class StoreFallbacks(Base):
    """The two store setups that would make the key-check fail — and the
    store delete the key it just made."""

    def _plain_permalinks(self):
        real = STORE.get

        def get(url, headers=None, params=None, timeout=None, follow_redirects=None):
            params = dict(params or {})
            if "/wp-json/" in url:
                STORE.calls.append(("GET", url, params, headers))
                return sr._Resp(404, "<html>not found</html>", {"content-type": "text/html"})
            route = params.pop("rest_route", "")
            return real(url.rstrip("/") + "/wp-json" + route, headers, params, timeout, follow_redirects)
        return get

    def test_plain_permalinks_use_rest_route(self):
        saved = sr.STUBS["httpx"].get
        sr.STUBS["httpx"].get = self._plain_permalinks()
        try:
            result = store_agent.connect_authorized(1, "https://shop-one.example", _post())
            self.assertTrue(result["success"], result)
            self.assertTrue(result["verified"])
            self.assertEqual(wc._shape["https://shop-one.example"]["route"], "plain")
        finally:
            sr.STUBS["httpx"].get = saved

    def test_auth_header_dropped_uses_query_auth_once(self):
        real = STORE.get

        def get(url, headers=None, params=None, timeout=None, follow_redirects=None):
            if "Authorization" in (headers or {}):
                STORE.calls.append(("GET", url, params, headers))
                return sr._Resp(401, {"code": "woocommerce_rest_cannot_view"}, {"content-type": "application/json"})
            return real(url, headers, params, timeout, follow_redirects)
        saved = sr.STUBS["httpx"].get
        sr.STUBS["httpx"].get = get
        try:
            result = store_agent.connect_authorized(1, "https://shop-one.example", _post())
            self.assertTrue(result["success"], result)
            self.assertEqual(wc._shape["https://shop-one.example"]["auth"], "query")
            self.assertEqual(STORE.calls[-1][2]["consumer_key"], KEY1[0])
            self.assertNotIn(KEY1[1], self.out.getvalue())
        finally:
            sr.STUBS["httpx"].get = saved

    def test_a_real_refusal_is_still_refused(self):
        STORE.status = 401
        self.assertEqual(store_agent.connect_authorized(1, "https://shop-one.example", _post())["code"],
                         "store_auth")
        self.assertEqual(DB_WRITES, [])

    def test_website_password_never_goes_in_a_url(self):
        STORE.status = 401
        with self.assertRaises(wc.StoreAuthError):
            wc.get("https://shop-one.example", "wc/v3/orders", ("admin", "app pw"))
        self.assertTrue(all("consumer_key" not in (c[2] or {}) for c in STORE.calls))


class AuthState(Base):
    def test_round_trip_pins_client_site_and_nonce(self):
        token = session.create_wc_auth_state(7, "https://shop.example", "n1")
        self.assertEqual(session.verify_wc_auth_state(token), (7, "https://shop.example", "n1"))

    def test_tampered_expired_or_other_purpose_tokens_fail(self):
        token = session.create_wc_auth_state(7, "https://shop.example", "n1")
        body, sig = token.split(".")
        forged = json.loads(base64.urlsafe_b64decode(body))
        forged["site"] = "https://evil.example"
        forged_b64 = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode()
        self.assertIsNone(session.verify_wc_auth_state(f"{forged_b64}.{sig}"))
        saved = time.time
        try:
            time.time = lambda: saved() + session.WC_AUTH_STATE_MAX_AGE_SECONDS + 5
            self.assertIsNone(session.verify_wc_auth_state(token))
        finally:
            time.time = saved
        self.assertIsNone(session.verify_wc_auth_state(session.create_oauth_state_token(7)))
        self.assertIsNone(session.verify_wc_auth_state(session.create_store_purchase_token(7)))
        self.assertIsNone(session.verify_store_purchase_token(token))
        self.assertIsNone(session.verify_store_purchase_token(session.create_lead_capture_token(7)))
        self.assertEqual(session.verify_store_purchase_token(session.create_store_purchase_token(7)), 7)

    def test_pending_nonce_is_single_use_and_latest_only(self):
        store_agent.remember_pending(7, "first")
        store_agent.remember_pending(7, "second")
        self.assertFalse(store_agent.consume_pending(7, "first"))
        self.assertFalse(store_agent.consume_pending(8, "second"))
        self.assertTrue(store_agent.consume_pending(7, "second"))
        self.assertFalse(store_agent.consume_pending(7, "second"))


# ─── One click: what the store POSTs ─────────────────────────────────────────

def _post(key=KEY1, permissions="read"):
    return {"key_id": 1, "user_id": "x", "consumer_key": key[0], "consumer_secret": key[1],
            "key_permissions": permissions}


class Callback(Base):
    def test_read_only_key_is_verified_against_the_pinned_store_and_stored(self):
        result = store_agent.connect_authorized(1, "https://shop-one.example", _post())
        self.assertTrue(result["success"], result)
        self.assertTrue(result["verified"])
        self.assertEqual(DB_WRITES, [("upsert", 1, "woocommerce", "https://shop-one.example")])
        self.assertTrue(STORE.calls[0][1].startswith("https://shop-one.example/wp-json/wc/v3/orders"))
        self.assertEqual(store_agent.status(1)["method"], "key")
        self.assertNotIn(KEY1[1], self.out.getvalue())
        self.assertNotIn(KEY1[0], self.out.getvalue())
        self.assertNotIn(KEY1[1], json.dumps(result))

    def test_write_or_read_write_permissions_are_refused(self):
        for perm in ("write", "read_write", "", None):
            with self.subTest(perm=perm):
                result = store_agent.connect_authorized(1, "https://shop-one.example", _post(permissions=perm))
                self.assertEqual(result["code"], "not_read_only")
        self.assertEqual(DB_WRITES, [])
        self.assertEqual(STORE.calls, [])

    def test_malformed_or_refused_keys_are_not_stored(self):
        self.assertEqual(store_agent.connect_authorized(1, "https://shop-one.example",
                                                        _post(key=("abc", "def")))["code"], "bad_key")
        STORE.status = 401
        self.assertEqual(store_agent.connect_authorized(1, "https://shop-one.example", _post())["code"],
                         "store_auth")
        self.assertEqual(DB_WRITES, [])

    def test_inconclusive_verify_still_stores_the_key_the_store_just_made(self):
        STORE.status = 500
        result = store_agent.connect_authorized(1, "https://shop-one.example", _post())
        self.assertTrue(result["success"])
        self.assertFalse(result["verified"])
        self.assertEqual(len(DB_WRITES), 1)

    def test_the_site_comes_from_our_state_never_the_body(self):
        body = {**_post(), "site_url": "https://evil.example", "store_url": "https://evil.example"}
        store_agent.connect_authorized(1, "https://shop-one.example", body)
        self.assertEqual(DB_WRITES[0][3], "https://shop-one.example")


class Routes(unittest.TestCase):
    def setUp(self):
        self.src = _src("core", "api_server.py")

    def route(self, decorator):
        return self.src.split(decorator, 1)[1].split("\n@app.", 1)[0]

    def test_no_client_route_takes_a_key(self):
        self.assertNotIn('"/api/client/store/connect"', self.src)
        self.assertNotIn('"/api/client/store/connect-site"', self.src)
        self.assertNotIn("class StoreConnectRequest", self.src)

    def test_start_is_session_gated_and_never_as_the_admin_view(self):
        start = self.route('@app.get("/api/oauth/woocommerce/start")')
        self.assertIn("_require_session(request)", start)
        self.assertIn("_admin_view_client(request) is not None", start)
        self.assertIn("store_agent.remember_pending(client_id, nonce)", start)
        self.assertIn("/api/store/wc-auth/callback", start)

    def test_callback_answers_200_only_when_stored_and_never_logs_the_body(self):
        cb = self.route('@app.post("/api/store/wc-auth/callback")')
        self.assertIn("verify_wc_auth_state(", cb)
        self.assertIn("store_agent.consume_pending(client_id, nonce)", cb)
        self.assertIn("deletion_guard.is_deleting(client_id)", cb)
        self.assertIn("len(raw) > 4096", cb)
        for line in cb.splitlines():
            if "print(" in line:
                self.assertNotIn("body", line)
                self.assertNotIn("raw", line)
        returns = re.findall(r"return JSONResponse\(([^\n]+)\)", cb)
        ok = [r for r in returns if "status_code" not in r]
        self.assertEqual(ok, ['{"success": True}'])                    # the ONE 200
        self.assertLess(cb.index("connect_authorized"), cb.index('{"success": True}'))

    def test_explicit_ask_is_answered_by_code_before_any_model(self):
        chat = self.route('@app.post("/api/client-chat")')
        # `message` = the turn's (possibly merged) client text (core/chat_turns)
        self.assertIn("store_agent.connect_turn(client_id, message, persona)", chat)
        self.assertLess(chat.index("connect_turn"), chat.index("answer_support_question(client_id"))
        self.assertLess(chat.index("chat_guard.check"), chat.index("connect_turn"))

    def test_start_takes_an_address_never_a_key(self):
        start = self.route('@app.get("/api/oauth/woocommerce/start")')
        self.assertIn('def store_authorize_start(request: Request, site: str = ""):', start)
        self.assertIn("store_agent.store_target(client_id, site)", start)
        for word in ("consumer_key", "consumer_secret", "key"):
            self.assertNotIn(f"{word}: str", start)

    def test_the_chat_card_button_says_connect_in_one_click(self):
        src = _src("core", "agent_actions.py")
        spec = src.split('"connect_store": {', 1)[1].split('\n    },', 1)[0]
        self.assertIn('"button": _txt("חיבור החנות בקליק אחד", "Connect my store in one click"', spec)
        self.assertIn('"link": "/api/oauth/woocommerce/start"', spec)
        self.assertIn('"button": _pick(spec["button"], lang) if spec.get("button") else ""', src)

    def test_prompts_propose_the_card_on_an_explicit_ask(self):
        src = re.sub(r"\s+", " ", _src("agents", "support_agent.py"))
        self.assertIn("ASKS to connect the store", src)
        self.assertGreaterEqual(src.count("propose connect_store at once"), 2)

    def test_manual_key_is_admin_only(self):
        admin = self.route('@app.post("/api/admin/store/connect")')
        self.assertIn("_require_admin(request)", admin)
        self.assertIn("store_agent.connect(", admin)
        self.assertNotIn("_require_session", admin)

    def test_purchase_record_route_is_always_204(self):
        rec = self.route('@app.post("/api/store/purchase/{token}")')
        self.assertIn("verify_store_purchase_token(token)", rec)
        self.assertIn("deletion_guard.is_deleting(client_id)", rec)
        self.assertIn("rate_ok(", rec)
        self.assertEqual(set(re.findall(r"Response\(status_code=(\d+)\)", rec)), {"204"})


class ClientScreens(unittest.TestCase):
    def test_dashboard_has_no_key_field_and_one_button(self):
        html = _src("dashboard", "client", "index.html")
        for gone in ("storeKey", "storeSecret", "ck_...", "cs_...", "Consumer key", "/api/client/store/connect",
                     "storeForm", "store_fill"):
            self.assertNotIn(gone, html)
        self.assertIn("let url = '/api/oauth/woocommerce/start';", html)
        self.assertIn("window.location.href = url;", html)
        self.assertIn("store_one_click: { he:'חיבור החנות בקליק אחד', en:'Connect my store in one click'", html)
        # No known address → ONE address field (never a key), opened by the return codes
        self.assertIn('id="storeSite"', html)
        self.assertIn("['store_no_site', 'store_bad_address', 'store_needs_https'].includes(err)) askStoreAddress()",
                      html)
        for code in ("store_no_site", "store_bad_address", "store_needs_https"):
            self.assertIn(f"notice_{code}: {{ he:", html)
        # A chat card's button may carry its own words
        self.assertIn("ok.textContent = a.button || uallakI18n.t('action_approve');", html)
        card = html.split('id="storeCard"', 1)[1][:60]
        self.assertIn('style="display:none;"', card)                     # shown only when needed

    def test_admin_page_has_the_fallback(self):
        html = _src("dashboard", "admin", "connections", "index.html")
        self.assertIn("/api/admin/store/connect", html)
        self.assertIn("use_website_connection", html)


# ─── Our own purchase records ────────────────────────────────────────────────

class PurchaseRecords(Base):
    def test_only_order_number_total_and_currency_are_kept(self):
        row = sp.clean({"o": "1234", "v": "199.90", "c": "ils", "email": "dana@example.com",
                        "name": "Dana", "phone": "050-1234567"})
        self.assertEqual(row, {"order_ref": "1234", "value": 199.9, "currency": "ILS"})
        self.assertEqual(sp.clean({"o": "dana@example.com"}), {})
        self.assertEqual(sp.clean({"o": "12", "v": "abc", "c": "shekel"}),
                         {"order_ref": "12", "value": None, "currency": None})
        self.assertEqual(sp.clean("not a dict"), {})

    def test_once_per_order_and_per_client(self):
        self.assertTrue(sp.record(5, {"o": "1", "v": 100, "c": "ILS"}))
        self.assertFalse(sp.record(5, {"o": "1", "v": 100, "c": "ILS"}))       # refresh / replay
        self.assertTrue(sp.record(6, {"o": "1", "v": 50, "c": "ILS"}))         # another client's order 1
        self.assertEqual(len(self.db.tables[sp.TABLE]), 2)
        stored = repr(self.db.tables[sp.TABLE])
        for value in sr.PII:
            self.assertNotIn(value, stored)

    def test_daily_cap(self):
        saved = sp.MAX_PER_DAY
        sp.MAX_PER_DAY = 2
        try:
            self.assertTrue(sp.record(5, {"o": "1"}))
            self.assertTrue(sp.record(5, {"o": "2"}))
            self.assertFalse(sp.record(5, {"o": "3"}))
        finally:
            sp.MAX_PER_DAY = saved

    def test_rate_limit_per_sender(self):
        for _ in range(sp.RATE_PER_MINUTE):
            self.assertTrue(sp.rate_ok("1.2.3.4", now=1000.0))
        self.assertFalse(sp.rate_ok("1.2.3.4", now=1000.0))
        self.assertTrue(sp.rate_ok("5.6.7.8", now=1000.0))
        self.assertTrue(sp.rate_ok("1.2.3.4", now=1061.0))

    def test_summarize(self):
        s = sp.summarize([{"value": 100, "currency": "ILS"}, {"value": 50.5, "currency": "ILS"},
                          {"value": None, "currency": None}, {"value": 10, "currency": "USD"}])
        self.assertEqual((s["orders"], s["orders_with_total"], s["revenue"], s["currency"]), (4, 3, 150.5, "ILS"))
        self.assertEqual(s["other_currencies"], {"USD": 10.0})

    def test_store_reads_use_the_records_first_without_a_store_login(self):
        sp.record(9, {"o": "77", "v": 120, "c": "ILS"})
        reads = store_agent.store_reads(9, "כמה הזמנות היו החודש?")
        self.assertFalse(reads["connected"])
        records = reads["measured"]["purchase_records"]
        self.assertEqual(records["asked_period"]["orders"], 1)
        self.assertEqual(records["asked_period"]["revenue"], 120.0)
        self.assertIn("ממתינות לתשלום", records["meaning_he"])
        self.assertEqual(STORE.calls, [])                                   # no store read at all

    def test_performance_shows_orders_placed_not_sales(self):
        from core import performance
        saved = store_agent.is_connected
        store_agent.is_connected = lambda cid: False
        try:
            sp.record(9, {"o": "77", "v": 120, "c": "ILS"})
            block = performance._store_sales(9)
        finally:
            store_agent.is_connected = saved
        self.assertEqual(block["source"], "purchase_measurement")
        self.assertEqual([w["days"] for w in block["windows"]], [7, 30, 90])
        self.assertEqual(block["windows"][0]["orders"], 1)
        self.assertEqual(block["definition_he"], sp.MEANING_HE)


# ─── The record tag ──────────────────────────────────────────────────────────

URL = "https://app.uallak.com/api/store/purchase/" + session.create_store_purchase_token(27)


class RecordTag(unittest.TestCase):
    def test_tag_sends_order_total_currency_only_to_our_endpoint(self):
        html = gtm.purchase_beacon_html(URL)
        v = gtm.PURCHASE["vars"]
        for var in (v["order_id"], v["total"], v["currency"]):
            self.assertIn("{{" + var + "}}", html)
        self.assertIn("navigator.sendBeacon", html)
        self.assertIn(URL, html)
        for personal in ("billing", "email", "phone", "address", "first_name", "customer", "cookie"):
            self.assertNotIn(personal, html.lower())

    def test_only_our_https_endpoint_is_accepted(self):
        for bad in ("http://app.uallak.com/api/store/purchase/x.y", "https://evil.example/collect",
                    URL + "';alert(1);'", URL.replace("/api/store/purchase/", "/api/leads/capture/")):
            with self.assertRaises(ValueError):
                gtm.purchase_beacon_html(bad)

    def test_tag_body_fires_on_the_purchase_trigger(self):
        body = gtm.purchase_beacon_tag_body(URL, "41")
        self.assertEqual((body["name"], body["type"], body["firingTriggerId"]),
                         ("uallak — purchase record", "html", ["41"]))

    def test_live_check(self):
        trigger = {**gtm.purchase_trigger_body(), "triggerId": "41"}
        tag = {**gtm.purchase_beacon_tag_body(URL, "41")}
        self.assertTrue(gtm.purchase_beacon_live({"trigger": [trigger], "tag": [tag]})["live"])
        self.assertFalse(gtm.purchase_beacon_live({"trigger": [trigger], "tag": [{**tag, "paused": True}]})["live"])
        self.assertEqual(gtm.purchase_beacon_live({"trigger": [], "tag": [tag]})["missing"], ["trigger"])

    def test_website_agent_adds_the_record_tag_to_an_old_live_purchase_path(self):
        src = _src("agents", "website_agent.py")
        self.assertIn("gtm.ensure_purchase_beacon_tag(", src)
        self.assertIn("record_missing", src)
        self.assertIn('"purchase_record": gtm.purchase_beacon_live(live)', src)


class Migration(unittest.TestCase):
    def test_table_is_idempotent_unique_per_order_and_deleted_with_the_client(self):
        sql = _src("migrations", "2026-11-07-store-purchase-events.sql").lower()
        self.assertIn("create table if not exists store_purchase_events", sql)
        self.assertIn("create unique index if not exists", sql)
        self.assertIn("(client_id, order_ref)", sql)
        self.assertIn("on delete cascade", sql)
        for personal in ("email", "phone", "name text", "address"):
            self.assertNotIn(personal, sql)
        self.assertIn('("store_purchase_events", True)', _src("core", "client_deletion.py"))


def tearDownModule():
    sr.tearDownModule()


if __name__ == "__main__":
    unittest.main()
