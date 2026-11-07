"""The online store, READ-ONLY (2026-11-02, docs/STORE.md).

- Read-only enforced: the WooCommerce module has GET and nothing else, and no
  code anywhere sends POST/PUT/PATCH/DELETE to a /wc/ path.
- Per-client isolation: each client reads with its own stored key; the
  aggregate cache never serves one client's numbers to another.
- Missing connection: the reads say so, the connect card is offered, the
  chat-guided steps use the menu path from WooCommerce's docs.
- Sales summary math (revenue, refunds, AOV, top products, status counts).
- Personal data: never fetched for lists/summaries, never in logs, never in
  the cache, never in a DB write; one order's details only on an explicit ask,
  answered by code with a masked copy for the chat history.
- Relative dates ("yesterday") in Israel time.

Stdlib only. Run:  python -I -B tests/test_store_reads.py -v
"""
import ast
import contextlib
import io
import os
import re
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)        # 12:00 in Israel (winter, +02:00)

PII = ("Dana", "Levi", "Herzl 12", "dana@example.com", "050-1234567", "Tel Aviv", "leave at door", "For Dana")


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _order(oid, status, total, gmt, items, refunds=()):
    return {
        "id": oid, "number": str(oid), "status": status, "currency": "ILS", "total": f"{total:.2f}",
        "date_created": gmt, "date_created_gmt": gmt, "total_tax": "0", "shipping_total": "0",
        "discount_total": "0", "payment_method_title": "Card",
        "shipping_lines": [{"method_title": "Courier"}],
        "line_items": [{"product_id": pid, "name": name, "quantity": q, "total": f"{t:.2f}",
                        "meta_data": [{"key": "engraving", "value": "For Dana"}]} for pid, name, q, t in items],
        "refunds": [{"id": i, "total": f"-{amt:.2f}", "reason": reason} for i, (amt, reason) in enumerate(refunds, 1)],
        "billing": {"first_name": "Dana", "last_name": "Levi", "address_1": "Herzl 12", "city": "Tel Aviv",
                    "postcode": "", "email": "dana@example.com", "phone": "050-1234567"},
        "shipping": {"first_name": "Dana", "last_name": "Levi", "address_1": "Herzl 12", "city": "Tel Aviv",
                     "postcode": "", "phone": "050-1234567"},
        "customer_note": "leave at door",
    }


ORDERS = [
    _order(101, "completed", 200, "2026-11-01T08:00:00", [(1, "Shoe A", 1, 150), (2, "Lace", 2, 50)],
           refunds=[(20, "size")]),
    _order(102, "processing", 100, "2026-11-01T21:30:00", [(1, "Shoe A", 1, 100)]),   # 23:30 IL, yesterday
    _order(103, "cancelled", 80, "2026-11-01T22:30:00", [(3, "Hat", 1, 80)]),         # 00:30 IL, today
    _order(104, "refunded", 60, "2026-10-20T10:00:00", [(3, "Hat", 1, 60)]),
    _order(105, "on-hold", 40, "2026-10-25T10:00:00", [(4, "Cap", 1, 40)]),
]


class FakeStore:
    """WordPress + WooCommerce answering GET only, honouring _fields like WP."""

    def __init__(self):
        self.calls = []
        self.status = 200
        self.orders = [dict(o) for o in ORDERS]

    def _shape(self, order, fields):
        if not fields:
            return dict(order)
        return {k: order[k] for k in fields.split(",") if k in order}

    def get(self, url, headers=None, params=None, timeout=None, follow_redirects=None):
        params = dict(params or {})
        self.calls.append(("GET", url, params, headers))
        if self.status != 200:
            return _Resp(self.status, {"code": "x"})
        path = url.split("/wp-json/", 1)[1]
        fields = params.get("_fields", "")
        if path == "wc/v3/orders":
            rows = self.orders
            if params.get("after"):
                rows = [o for o in rows if params["after"] <= o["date_created_gmt"] < params["before"]]
            if params.get("search"):
                rows = [o for o in rows if o["number"] == params["search"]]
            per_page, page = int(params.get("per_page", 10)), int(params.get("page", 1))
            chunk = rows[(page - 1) * per_page: page * per_page]
            pages = max(1, -(-len(rows) // per_page))
            return _Resp(200, [self._shape(o, fields) for o in chunk],
                         {"X-WP-Total": str(len(rows)), "X-WP-TotalPages": str(pages)})
        m = re.fullmatch(r"wc/v3/orders/(\d+)", path)
        if m:
            for o in self.orders:
                if o["id"] == int(m.group(1)):
                    return _Resp(200, self._shape(o, fields))
            return _Resp(404, {"code": "woocommerce_rest_shop_order_invalid_id"})
        return _Resp(404, {"code": "rest_no_route"})


class _Resp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


STORE = FakeStore()
DB_WRITES = []
ACCOUNTS = {}      # client_id -> client_accounts row


def _upsert(client_id, platform, account_id, token, status):
    DB_WRITES.append(("upsert", client_id, platform, account_id))
    ACCOUNTS[client_id] = {"client_id": client_id, "platform": platform, "account_id": account_id,
                           "access_token": token, "status": status}
    return {}


def _log_step(agent, step, message=""):
    print(f"[{agent}] {step} — {message}")


STUBS = {
    "httpx": _module("httpx", get=lambda *a, **k: STORE.get(*a, **k)),
    "supabase": _module("supabase", create_client=lambda *a, **k: None),
    "anthropic": _module("anthropic", Anthropic=lambda *a, **k: None),
    "core.agent_base": _module("core.agent_base", log_step=_log_step, agent_alert=lambda *a, **k: None,
                               timed_step=lambda a, s, fn: fn()),
    "agents.client_agent": _module("agents.client_agent", upsert_account=_upsert, _db=lambda: None,
                                   remove_accounts=lambda cid, p: (ACCOUNTS.pop(cid, None), 1)[1]),
    "core.secrets_vault": _module("core.secrets_vault", hydrate=lambda r: r),
    "agents.website_agent": _module("agents.website_agent",
                                    _get_connection=lambda cid: {"cid": cid},
                                    _creds=lambda conn: ({1: ("https://shop-one.example", "admin", "app pw"),
                                                          2: ("https://shop-two.example", "", "")}
                                                         .get(conn["cid"], ("", "", "")))),
}

_saved = {name: sys.modules.get(name) for name in list(STUBS) + ["core.woocommerce_service", "agents.store_agent"]}
sys.modules.update(STUBS)
sys.modules.pop("core.woocommerce_service", None)
sys.modules.pop("agents.store_agent", None)
from core import woocommerce_service as wc  # noqa: E402
from agents import store_agent  # noqa: E402

store_agent._row = lambda cid: dict(ACCOUNTS.get(cid) or {})
store_agent._forget = lambda cid: [store_agent._summary_cache.pop(k) for k in list(store_agent._summary_cache)
                                   if k[0] == cid]

KEY1 = ("ck_" + "a" * 40, "cs_" + "b" * 40)
KEY2 = ("ck_" + "c" * 40, "cs_" + "d" * 40)


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class Base(unittest.TestCase):
    def setUp(self):
        STORE.calls.clear()
        STORE.status = 200
        DB_WRITES.clear()
        ACCOUNTS.clear()
        store_agent._summary_cache.clear()
        wc._reset_rate()
        self.out = io.StringIO()
        self._redirect = contextlib.redirect_stdout(self.out)
        self._redirect.__enter__()

    def tearDown(self):
        self._redirect.__exit__(None, None, None)

    def connect(self, cid=1, key=KEY1, site="https://shop-one.example"):
        result = store_agent.connect(cid, key[0], key[1], site)
        self.assertTrue(result["success"], result)
        STORE.calls.clear()
        return result


# ─── Read-only, enforced ─────────────────────────────────────────────────────

class ReadOnly(Base):
    def test_service_has_no_write_verb(self):
        tree = ast.parse(_src("core", "woocommerce_service.py"))
        used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name) and n.value.id == "httpx"}
        self.assertEqual(used - {"get"}, set(), "core/woocommerce_service.py may only call httpx.get")
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertFalse(names & {"post", "put", "patch", "delete", "request"}, names)

    def test_no_code_writes_to_a_wc_path(self):
        write = re.compile(r"\b(post|put|patch|delete|request)\s*\([^)]*['\"/]wc/", re.IGNORECASE | re.DOTALL)
        for folder in ("core", "agents"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if name.endswith(".py"):
                    hit = write.search(_src(folder, name))
                    self.assertIsNone(hit, f"{folder}/{name} writes to a WooCommerce path: {hit and hit.group(0)}")

    def test_only_get_reaches_the_network(self):
        self.connect()
        store_agent.sales_summary(1, "last_30_days", now=NOW)
        store_agent.recent_orders(1, "last_7_days", now=NOW)
        store_agent.refunds(1, "last_30_days", now=NOW)
        store_agent.order_detail(1, "101")
        self.assertTrue(STORE.calls)
        self.assertEqual({c[0] for c in STORE.calls}, {"GET"})
        self.assertTrue(all("/wp-json/wc/v3/" in c[1] for c in STORE.calls))

    def test_only_wc_read_paths(self):
        for bad in ("wp/v2/users", "wc/v3/../wp/v2/users", "wc/v3/orders?status=any"):
            with self.assertRaises(ValueError):
                wc._path(bad)

    def test_plain_http_is_refused_before_sending_a_key(self):
        with self.assertRaises(wc.StoreNeedsHttps):
            wc.get("http://shop.example", "wc/v3/orders", KEY1)
        self.assertEqual(STORE.calls, [])

    def test_rate_limit_per_store(self):
        self.connect()
        wc._reset_rate()
        for _ in range(wc.RATE_PER_MINUTE):
            wc.get("https://shop-one.example", "wc/v3/orders", KEY1, {"_fields": "id"})
        with self.assertRaises(wc.StoreRateLimited):
            wc.get("https://shop-one.example", "wc/v3/orders", KEY1, {"_fields": "id"})


# ─── Connection ──────────────────────────────────────────────────────────────

class Connection(Base):
    def test_key_is_verified_before_it_is_stored(self):
        STORE.status = 401
        result = store_agent.connect(1, *KEY1, site_url="https://shop-one.example")
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "store_auth")
        self.assertEqual(DB_WRITES, [])
        self.assertNotIn(KEY1[1], self.out.getvalue())

    def test_malformed_key_never_leaves(self):
        result = store_agent.connect(1, "abc", "def", "https://shop-one.example")
        self.assertEqual(result["code"], "bad_key")
        self.assertEqual(STORE.calls, [])

    def test_good_key_is_stored_encrypted_path_and_basic_auth(self):
        self.connect()
        self.assertEqual(DB_WRITES, [("upsert", 1, "woocommerce", "https://shop-one.example")])
        self.assertEqual(store_agent.status(1), {"connected": True, "method": "key", "site": "shop-one.example",
                                                 "recommend_one_click": False})
        store_agent.recent_orders(1, now=NOW)
        auth = STORE.calls[0][3]["Authorization"]
        self.assertTrue(auth.startswith("Basic "))
        self.assertNotIn(KEY1[1], self.out.getvalue())

    def test_site_password_fallback_stores_no_new_secret(self):
        result = store_agent.connect_with_site(1)
        self.assertTrue(result["success"], result)
        self.assertEqual(ACCOUNTS[1]["access_token"], store_agent.SITE_MARKER)
        self.assertEqual(store_agent.status(1)["method"], "site")
        self.assertTrue(store_agent.status(1)["recommend_one_click"])

    def test_site_fallback_needs_a_connected_site(self):
        self.assertEqual(store_agent.connect_with_site(2)["code"], "no_website")
        self.assertEqual(DB_WRITES, [])

    def test_not_a_woocommerce_site(self):
        STORE.status = 404
        self.assertEqual(store_agent.connect(1, *KEY1, site_url="https://shop-one.example")["code"],
                         "store_not_woocommerce")


# ─── Missing connection ──────────────────────────────────────────────────────

class MissingConnection(Base):
    def test_reads_say_not_connected_and_call_no_store(self):
        reads = store_agent.store_reads(9, "כמה הזמנות היו אתמול?")
        self.assertIs(reads["connected"], False)
        # The no-login sources travel instead (nothing recorded in this stub)
        self.assertIs(reads["measured"]["available"], False)
        self.assertIn("one_click_possible", reads)
        self.assertEqual(store_agent.store_reads(9, "שלום"), {"connected": False})   # not a store question
        self.assertEqual(store_agent.sales_summary(9), {"connected": False})
        self.assertEqual(store_agent.personal_answer(9, "מה הכתובת של הזמנה 101?"), {})
        self.assertEqual(STORE.calls, [])

    def test_connect_card_is_one_click_and_only_when_not_connected(self):
        from core import agent_actions as aa
        spec = aa.ACTIONS["connect_store"]
        self.assertEqual((spec["kind"], spec["link"]), ("link", "/api/oauth/woocommerce/start"))
        self.assertTrue(spec["available"]({"store": {"connected": False}}, {}))
        self.assertFalse(spec["available"]({"store": {"connected": True}}, {}))
        for lang in ("he", "en"):
            text = aa._pick(spec["what"], lang) + aa._pick(spec["title"], lang)
            for word in ("ck_", "cs_", "Consumer", "wp-admin", "REST API", "מפתח", "key"):
                self.assertNotIn(word, text)

    def test_chat_flow_is_one_click_never_wp_admin_steps(self):
        from core import connection_catalog as cc
        self.assertFalse(hasattr(cc, "STORE_STEPS"))
        for step in ("start", "has", "none"):
            turn = cc.flow(5, "store", step)
            self.assertTrue(turn["show"])
            self.assertEqual((turn["persona"], turn["connect"], turn["chips"]), ("website", "store", []))
            text = "\n".join(turn["messages"])
            self.assertIn("בקליק אחד", text)
            for word in ("wp-admin", "WooCommerce", "Settings", "REST API", "Add Key", "ck_", "מפתח"):
                self.assertNotIn(word, text)

    def test_persona_prompts_never_ask_for_keys_or_walk_through_wp_admin(self):
        src = _src("agents", "support_agent.py")
        self.assertNotIn("walk them through creating a read-only key", src)
        self.assertNotIn("recommend switching to a read-only store key", src)
        self.assertIn("NEVER 'go look in your site's admin / WooCommerce'", src)
        self.assertIn("NEVER walk them through", src)
        self.assertIn("NEVER ask for a store key", src)
        self.assertIn("use it FIRST", src)
        self.assertIn("propose configure_conversion", src)


# ─── Sales summary math ──────────────────────────────────────────────────────

class SummaryMath(Base):
    def test_summarize(self):
        s = wc.summarize(ORDERS)
        self.assertEqual(s["orders"], 5)
        # THE sale definition (2026-11-07): processing + completed only;
        # on-hold waits for payment and is shown apart, never in revenue
        self.assertEqual(s["paid_orders"], 2)                 # completed + processing
        self.assertEqual(s["gross_revenue"], 300.0)
        self.assertEqual(s["refunded"], 20.0)
        self.assertEqual(s["net_revenue"], 280.0)
        self.assertEqual(s["average_order_value"], 150.0)
        self.assertEqual(s["status_counts"], {"completed": 1, "processing": 1, "cancelled": 1,
                                              "refunded": 1, "on-hold": 1})
        self.assertEqual(s["awaiting_payment"], {"orders": 1, "total": 40.0})
        self.assertEqual(s["not_sales"]["cancelled"], {"orders": 1, "total": 80.0})
        self.assertEqual(s["not_sales"]["refunded"], {"orders": 1, "total": 60.0})
        self.assertEqual(s["not_sales"]["failed"], {"orders": 0, "total": 0.0})
        self.assertEqual([p["name"] for p in s["top_products"]], ["Shoe A", "Lace"])
        self.assertEqual(s["top_products"][0], {"name": "Shoe A", "quantity": 2, "revenue": 250.0})

    def test_empty_period(self):
        s = wc.summarize([])
        self.assertEqual((s["orders"], s["net_revenue"], s["average_order_value"]), (0, 0.0, 0.0))

    def test_pagination_reads_every_page(self):
        self.connect()
        STORE.orders = [_order(1000 + i, "completed", 10, "2026-11-01T08:00:00", [(1, "X", 1, 10)])
                        for i in range(250)]
        s = store_agent.sales_summary(1, "yesterday", now=NOW)
        self.assertEqual((s["orders"], s["gross_revenue"], s["complete"]), (250, 2500.0, True))
        self.assertEqual([c[2]["page"] for c in STORE.calls], [1, 2, 3])
        self.assertTrue(all(c[2]["per_page"] == 100 for c in STORE.calls))

    def test_aggregate_is_cached(self):
        self.connect()
        store_agent.sales_summary(1, "last_30_days", now=NOW)
        calls = len(STORE.calls)
        store_agent.sales_summary(1, "last_30_days", now=NOW)
        self.assertEqual(len(STORE.calls), calls)


# ─── Relative dates ──────────────────────────────────────────────────────────

class RelativeDates(Base):
    def test_words(self):
        self.assertEqual(store_agent.detect_period("כמה הזמנות היו אתמול?"), "yesterday")
        self.assertEqual(store_agent.detect_period("מה המכירות החודש"), "this_month")
        self.assertEqual(store_agent.detect_period("ומה היה בחודש שעבר?"), "last_month")
        self.assertEqual(store_agent.detect_period("בשבוע האחרון"), "last_7_days")
        self.assertEqual(store_agent.detect_period("כמה הזמנות?"), "last_30_days")

    def test_yesterday_is_israel_midnight_to_midnight(self):
        start, end = store_agent.period_bounds("yesterday", NOW)
        self.assertEqual((start.isoformat(), end.isoformat()),
                         ("2026-11-01T00:00:00+02:00", "2026-11-02T00:00:00+02:00"))

    def test_order_lookup_by_yesterday(self):
        self.connect()
        result = store_agent.recent_orders(1, store_agent.detect_period("מה הזמינו אתמול?"), now=NOW)
        self.assertEqual([o["number"] for o in result["orders"]], ["101", "102"])   # 103 is 00:30 today
        params = STORE.calls[0][2]
        self.assertEqual((params["after"], params["before"], params["dates_are_gmt"]),
                         ("2026-10-31T22:00:00", "2026-11-01T22:00:00", "true"))


# ─── Personal data ───────────────────────────────────────────────────────────

class PersonalData(Base):
    def _no_pii(self, text, where):
        for value in PII:
            self.assertNotIn(value, text, f"personal data '{value}' in {where}")

    def test_lists_and_summaries_never_fetch_personal_fields(self):
        self.connect()
        store_agent.sales_summary(1, "last_30_days", now=NOW)
        store_agent.recent_orders(1, "last_30_days", now=NOW)
        store_agent.refunds(1, "last_30_days", now=NOW)
        store_agent.order_detail(1, "101")
        for call in STORE.calls:
            fields = call[2].get("_fields", "")
            self.assertTrue(fields)
            for personal in ("billing", "shipping", "customer_note", "meta_data"):
                self.assertNotIn(personal, fields.split(","))

    def test_nothing_personal_in_reads_logs_cache_or_db(self):
        self.connect()
        reads = [store_agent.store_reads(1, "כמה הזמנות ומכירות היו החודש? והחזרים?"),
                 store_agent.store_reads(1, "מה הסטטוס של הזמנה 101?"),
                 store_agent.section(1)]
        self._no_pii(repr(reads), "the agent's data")
        self._no_pii(repr(store_agent._summary_cache), "the aggregate cache")
        self._no_pii(self.out.getvalue(), "the logs")
        self.assertEqual([w for w in DB_WRITES if w[0] != "upsert"], [])
        self.assertEqual(len(DB_WRITES), 1)                      # the key itself, at connect

    def test_line_item_meta_is_dropped(self):
        self.assertNotIn("meta_data", repr(wc.clean_order(ORDERS[0])))
        self.assertNotIn("For Dana", repr(wc.clean_order(ORDERS[0])))

    def test_explicit_ask_shows_details_once_and_stores_a_masked_copy(self):
        self.connect()
        answer = store_agent.personal_answer(1, "מה הכתובת למשלוח של הזמנה 101?")
        self.assertIn("Herzl 12", answer["reply"])
        self.assertIn("dana@example.com", answer["reply"])
        self._no_pii(answer["stored_reply"], "the stored chat copy")
        self.assertIn(store_agent.PII_MASK, answer["stored_reply"])
        self._no_pii(self.out.getvalue(), "the logs")
        self._no_pii(repr(store_agent._summary_cache), "the cache")
        # only THIS one order was read with personal fields
        personal_calls = [c for c in STORE.calls if "billing" in c[2].get("_fields", "")]
        self.assertEqual(len(personal_calls), 1)
        self.assertTrue(personal_calls[0][1].endswith("/wc/v3/orders/101"))

    def test_no_explicit_ask_no_personal_read(self):
        self.connect()
        self.assertEqual(store_agent.personal_answer(1, "מה הסטטוס של הזמנה 101?"), {})
        self.assertEqual(store_agent.personal_answer(1, "מה הכתובת של החנות?"), {})
        self.assertEqual(STORE.calls, [])

    def test_chat_route_answers_by_code_and_stores_the_masked_copy(self):
        src = _src("core", "api_server.py")
        route = src.split('@app.post("/api/client-chat")', 1)[1].split("@app.", 1)[0]
        self.assertIn("store_agent.personal_answer(client_id, req.message)", route)
        self.assertIn('log_communication(client_id, "outbound", channel, personal["stored_reply"])', route)
        # ...before any model is called
        self.assertLess(route.index("personal_answer"), route.index("answer_support_question(client_id"))

    def test_mask(self):
        self.assertEqual(store_agent.mask("dana@example.com 050-1234567"), "••• •••")


# ─── Per-client isolation ────────────────────────────────────────────────────

class Isolation(Base):
    def test_each_client_reads_its_own_store_with_its_own_key(self):
        self.connect(1, KEY1, "https://shop-one.example")
        self.connect(3, KEY2, "https://shop-three.example")
        store_agent.recent_orders(1, now=NOW)
        store_agent.recent_orders(3, now=NOW)
        self.assertTrue(STORE.calls[0][1].startswith("https://shop-one.example/"))
        self.assertTrue(STORE.calls[1][1].startswith("https://shop-three.example/"))
        self.assertNotEqual(STORE.calls[0][3]["Authorization"], STORE.calls[1][3]["Authorization"])

    def test_cache_never_crosses_clients(self):
        self.connect(1)
        store_agent.sales_summary(1, "last_30_days", now=NOW)
        self.assertEqual(store_agent.sales_summary(4, "last_30_days", now=NOW), {"connected": False})
        self.assertTrue(all(k[0] == 1 for k in store_agent._summary_cache))

    def test_another_clients_row_is_never_used(self):
        ACCOUNTS[5] = {"client_id": 6, "platform": "woocommerce", "account_id": "https://x.example",
                       "access_token": f"{KEY1[0]}:{KEY1[1]}", "status": "active"}
        self.assertEqual(store_agent.credentials(5), {})


if __name__ == "__main__":
    unittest.main()
