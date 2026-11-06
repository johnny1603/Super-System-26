"""Store purchase measurement (2026-11-07, conversion type store_sales).

- PurchaseFlow: agents.website_agent.configure_lead_conversion + core.gtm_service
  for real, against the in-memory Tag Manager of test_google_scope_flows. A
  store_sales-only client (Shin Sekai, 27) gets the purchase path and NO lead
  trigger; every object is found by name (a re-run creates nothing); the
  live version is read back to verify; nothing is written when the store's
  order-received page can't be recognised.
- Dedupe: the variables' JavaScript, run in Node when it is installed (skipped
  otherwise): one order fires once per browser, a refresh never re-counts,
  total + currency from the page or the dataLayer.
- SaleDefinition: one rule (processing + completed) everywhere; awaiting
  payment and cancelled / failed / refunded only ever beside it.
- Isolation + the card: each client's own types and own store, and a
  store-only card that never says "leads".

Stdlib only. Run:  python -I -B tests/test_store_purchase.py -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
try:
    import httpx  # noqa: F401
except ImportError:                    # stdlib-only run: nothing here touches the network
    _httpx = types.ModuleType("httpx")
    _httpx.__getattr__ = lambda name: type(name, (Exception,), {})   # any class an import names
    sys.modules["httpx"] = _httpx
if "anthropic" not in sys.modules:
    _anthropic = types.ModuleType("anthropic")
    _anthropic.Anthropic = lambda *a, **k: None
    sys.modules["anthropic"] = _anthropic

import test_google_scope_flows as flows  # noqa: E402  (module import: its tests don't re-run here)
from core import agent_actions as aa  # noqa: E402
from core import conversion_types as ct  # noqa: E402
from core import woocommerce_service as wc  # noqa: E402

CONTAINER = flows.CONTAINER
STORE_ONLY = {"types": ["store_sales"], "confirmed": True, "stored": True}
LEAD_TRIGGER_TYPES = {"formSubmission", "linkClick"}


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class PurchaseFlow(unittest.TestCase):
    """The GtmFlow harness (its stubs and in-memory API, NOT its tests), as a
    store_sales-only client."""
    setUpClass = classmethod(flows.GtmFlow.setUpClass.__func__)
    tearDownClass = classmethod(flows.GtmFlow.tearDownClass.__func__)

    def setUp(self):
        flows.GtmFlow.setUp(self)
        self.saved_get = ct.get
        ct.get = lambda cid: dict(STORE_ONLY)
        from agents import store_agent
        self.store_agent = store_agent
        self.saved_endpoint = store_agent.order_received_endpoint
        self.endpoint = {"slug": "order-received", "source": "store", "problem": ""}
        store_agent.order_received_endpoint = lambda cid: dict(self.endpoint)

    def tearDown(self):
        ct.get = self.saved_get
        self.store_agent.order_received_endpoint = self.saved_endpoint

    def ws(self, kind, path=f"{CONTAINER}/workspaces/9"):
        return self.api.entities[path][kind]

    def own(self):
        return self.website.configure_lead_conversion(27, "G-ABC1234")    # the client's OWN types

    # ── store_sales only: purchases, never leads ─────────────────────────────

    def test_store_only_client_gets_the_purchase_path_and_no_lead_trigger(self):
        result = self.own()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["types"], ["store_sales"])
        self.assertEqual(result["events"], ["purchase"])
        (trigger,) = self.ws("trigger")
        self.assertEqual((trigger["name"], trigger["type"]), ("uallak — purchase (order received)", "domReady"))
        self.assertFalse({t["type"] for t in self.ws("trigger")} & LEAD_TRIGGER_TYPES)
        (tag,) = self.ws("tag")
        params = {p["key"]: p.get("value") for p in tag["parameter"]}
        self.assertEqual((tag["name"], params["eventName"]), ("uallak — GA4 purchase", "purchase"))
        self.assertNotIn(params["eventName"], ("generate_lead", "click_whatsapp", "click_to_call"))
        self.assertEqual(sorted(v["name"] for v in self.ws("variable")),
                         sorted(self.gtm.PURCHASE["vars"].values()))
        self.assertEqual(self.api.builtins, [])                       # no Click URL: no link trigger
        self.assertIs(result["purchase_verified"], True)
        self.assertEqual(self.alerts, [])

    def test_trigger_fires_only_on_a_first_seen_order(self):
        self.assertTrue(self.own()["success"])
        (trigger,) = self.ws("trigger")
        (flt,) = trigger["filter"]
        params = {p["key"]: p["value"] for p in flt["parameter"]}
        self.assertEqual((flt["type"], params["arg0"], params["arg1"]),
                         ("equals", "{{uallak — order first seen}}", "yes"))

    def test_tag_carries_order_id_total_and_currency(self):
        self.assertTrue(self.own()["success"])
        (tag,) = self.ws("tag")
        self.assertEqual(self.gtm._event_settings(tag), {
            "transaction_id": "{{uallak — order id}}", "value": "{{uallak — order total}}",
            "currency": "{{uallak — order currency}}"})
        self.assertEqual(tag["firingTriggerId"], [self.ws("trigger")[0]["triggerId"]])

    def test_rerun_finds_everything_by_name_and_creates_nothing(self):
        self.assertTrue(self.own()["success"])
        self.api.live = {k: [dict(e) for e in self.ws(k)] for k in ("trigger", "tag", "variable")}
        self.api.workspaces = [w for w in self.api.workspaces if not w["path"].endswith("/9")]
        self.api.entities.clear()
        self.api.calls.clear()
        self.assertTrue(self.own()["success"])
        created = [p for m, p in self.api.calls if m == "POST" and p.rsplit("/", 1)[-1] in
                   ("variables", "triggers", "tags")]
        self.assertEqual(created, [])
        self.assertEqual(self.api.puts, [])
        self.assertEqual(len(self.ws("variable")), 4)
        self.assertEqual(len(self.ws("trigger")), 1)

    def test_a_changed_variable_is_updated_in_place_never_duplicated(self):
        name = self.gtm.PURCHASE["vars"]["order_id"]
        self.api.live["variable"] = [{"name": name, "type": "jsm", "variableId": "70",
                                      "path": f"{CONTAINER}/workspaces/9/variables/70", "fingerprint": "f0",
                                      "parameter": [{"type": "template", "key": "javascript",
                                                     "value": "function(){return 1;}"}]}]
        self.assertTrue(self.own()["success"])
        same_name = [v for v in self.ws("variable") if v["name"] == name]
        self.assertEqual(len(same_name), 1)
        self.assertEqual([p for p, _b, _q in self.api.puts], [f"{CONTAINER}/workspaces/9/variables/70"])
        self.assertEqual(self.api.puts[0][2], {"fingerprint": "f0"})

    def test_already_live_purchase_is_skipped_not_doubled(self):
        saved = self.website.get_conversion_tracking_status
        self.website.get_conversion_tracking_status = lambda cid: {"configured_live": False,
                                                                   "live_types": ["store_sales"]}
        try:
            self.assertEqual(self.own()["reason"], "already_live")
        finally:
            self.website.get_conversion_tracking_status = saved
        self.assertEqual(self.api.calls, [])

    def test_the_store_endpoint_slug_is_what_the_page_is_matched_on(self):
        self.endpoint = {"slug": "thank-you", "source": "store", "problem": ""}
        self.assertTrue(self.own()["success"])
        order_id = next(v for v in self.ws("variable") if v["name"] == self.gtm.PURCHASE["vars"]["order_id"])
        self.assertIn("thank-you(?:\\/|=)(\\d+)", order_id["parameter"][0]["value"])

    def test_unrecognisable_order_page_writes_nothing_and_says_the_fix(self):
        for problem in ("endpoint_empty", "endpoint_unusable"):
            with self.subTest(problem=problem):
                self.api.calls.clear()
                self.endpoint = {"slug": "", "source": "store", "problem": problem}
                result = self.own()
                self.assertEqual(result["reason"], "store_order_page_unknown")
                self.assertEqual([c for c in self.api.calls if c[0] != "GET"], [])
        self.assertEqual(aa._GTM_REFUSAL_CODES["store_order_page_unknown"], "store_order_page_unknown")
        line = aa.ACTIONS["configure_conversion"]["refusals"]["store_order_page_unknown"]["he"]
        self.assertIn("לא שינינו כלום", line)
        self.assertIn("order-received", line)
        self.assertNotIn("WooCommerce", line)

    def test_a_failed_publish_check_is_never_called_live(self):
        saved = self.gtm.get_live_version
        self.gtm.get_live_version = lambda rt, cp: {"name": "v", "trigger": [], "tag": [], "variable": []}
        try:
            result = self.own()
        finally:
            self.gtm.get_live_version = saved
        self.assertTrue(result["success"])
        self.assertIs(result["purchase_verified"], False)
        self.assertEqual(len(self.alerts), 1)
        text = aa._conversion_done(result, "he")
        self.assertIn("לא אומר שהיא פעילה", text)
        self.assertNotIn("פעילה ✓", text)

    def test_failure_in_a_shared_workspace_removes_the_variables_too(self):
        self.api.fail["create_workspace"] = (400, "FAILED_PRECONDITION")     # no room: default workspace
        self.api.fail["tag"] = (500, "INTERNAL")
        result = self.own()
        self.assertFalse(result["success"])
        deleted = set(self.api.deleted)
        self.assertTrue(any("/variables/" in p for p in deleted), deleted)
        self.assertTrue(any("/triggers/" in p for p in deleted), deleted)
        self.assertEqual(self.api.entities[f"{CONTAINER}/workspaces/1"]["variable"], [])

    def test_an_unreadable_choice_sets_up_nothing_never_a_lead_default(self):
        ct.get = lambda cid: {"types": ["form_submit"], "confirmed": False, "stored": False}
        self.assertEqual(self.own()["reason"], "no_gtm_types")
        self.assertEqual(self.api.calls, [])

    def test_live_status_reports_store_sales_only_when_the_whole_path_is_live(self):
        self.assertTrue(self.own()["success"])
        real_status = self._saved_fns["get_conversion_tracking_status"]
        full = dict(self.api.published)
        saved = self.gtm.get_live_version
        try:
            self.gtm.get_live_version = lambda rt, cp: full
            status = real_status(27)
            self.assertEqual(status["live_types"], ["store_sales"])
            self.assertFalse(status["configured_live"])                # no lead event, ever
            partial = {**full, "variable": full["variable"][:2]}
            self.gtm.get_live_version = lambda rt, cp: partial
            self.assertEqual(real_status(27)["live_types"], [])
        finally:
            self.gtm.get_live_version = saved


# ─── The once-per-order JavaScript, executed ─────────────────────────────────

NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not installed — the JavaScript is checked statically below")
class Dedupe(unittest.TestCase):
    def run_js(self, pages, endpoint="order-received"):
        """pages: [(pathname, search, total_text, symbol, dataLayer, new_page_load)]"""
        from core import gtm_service as gtm
        bodies = {b["name"]: b["parameter"][0]["value"] for b in gtm.purchase_variable_bodies(endpoint)}
        v = gtm.PURCHASE["vars"]
        names = {v["order_id"]: "orderId", v["total"]: "total", v["currency"]: "currency",
                 v["first_seen"]: "firstSeen"}

        def wire(js):
            for name, fn in names.items():
                js = js.replace("{{" + name + "}}", fn + "()")
            return js
        script = ["var store={};", "var out=[];",
                  "var window={localStorage:{getItem:function(k){return k in store?store[k]:null;},"
                  "setItem:function(k,v){store[k]=String(v);}},dataLayer:[]};",
                  "var document={location:{pathname:'/',search:''},total:'',symbol:'',"
                  "querySelector:function(sel){var t=sel.indexOf('currencySymbol')>-1?document.symbol:document.total;"
                  "return t?{textContent:t}:null;}};"]
        script += [f"var {fn}={wire(bodies[name])};" for name, fn in names.items()]
        for path, search, total, symbol, data_layer, new_load in pages:
            if new_load:
                script.append("delete window.__uallakOrders;")
            script.append(f"document.location={{pathname:{json.dumps(path)},search:{json.dumps(search)}}};"
                          f"document.total={json.dumps(total)};document.symbol={json.dumps(symbol)};"
                          f"window.dataLayer={json.dumps(data_layer)};"
                          "out.push([orderId()||null,firstSeen(),firstSeen(),total()===undefined?null:total(),"
                          "currency()||null]);")
        script.append("console.log(JSON.stringify({out:out,store:store}));")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "purchase.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(script))
            done = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_one_order_counts_once_and_a_refresh_never_again(self):
        thanks = ("/checkout/order-received/1234/", "?key=wc_order_x", "₪1,234.50", "₪", [], True)
        result = self.run_js([thanks, thanks, ("/checkout/order-received/1235/", "", "₪99.00", "₪", [], True)])
        first, refresh, other = result["out"]
        self.assertEqual(first, ["1234", "yes", "yes", 1234.5, "ILS"])      # same page load: still yes
        self.assertEqual(refresh[:3], ["1234", "no", "no"])                 # refresh: never again
        self.assertEqual(other[:2], ["1235", "yes"])
        self.assertEqual(json.loads(result["store"]["uallak_orders_seen"]), ["1234", "1235"])

    def test_other_pages_never_fire(self):
        (row,) = self.run_js([("/shop/", "", "₪5.00", "₪", [], True)])["out"]
        self.assertEqual(row[:2], [None, "no"])

    def test_plain_permalinks_and_a_custom_endpoint(self):
        (plain,) = self.run_js([("/", "?page_id=7&order-received=55&key=x", "", "", [], True)])["out"]
        self.assertEqual(plain[:2], ["55", "yes"])
        (custom,) = self.run_js([("/checkout/thank-you/77/", "", "", "", [], True)], endpoint="thank-you")["out"]
        self.assertEqual(custom[:2], ["77", "yes"])

    def test_total_and_currency_from_the_datalayer_for_this_order_only(self):
        mine = [{"ecommerce": {"transaction_id": "88", "value": "250.00", "currency": "ils"}}]
        (row,) = self.run_js([("/checkout/order-received/88/", "", "", "", mine, True)])["out"]
        self.assertEqual(row[3:], [250.0, "ILS"])
        other = [{"ecommerce": {"transaction_id": "87", "value": "999", "currency": "USD"}}]
        (row,) = self.run_js([("/checkout/order-received/88/", "", "€12,50", "€", other, True)])["out"]
        self.assertEqual(row[3:], [12.5, "EUR"])                               # never another order's total

    def test_missing_data_is_sent_without_never_guessed(self):
        (row,) = self.run_js([("/checkout/order-received/9/", "", "", "", [], True)])["out"]
        self.assertEqual(row, ["9", "yes", "yes", None, None])


class DedupeStatic(unittest.TestCase):
    """What holds even where Node isn't installed (CI)."""

    def setUp(self):
        from core import gtm_service as gtm
        self.gtm = gtm
        self.js = {b["name"]: b["parameter"][0]["value"] for b in gtm.purchase_variable_bodies()}

    def test_first_seen_remembers_orders_in_the_browser_and_caches_per_page(self):
        js = self.js[self.gtm.PURCHASE["vars"]["first_seen"]]
        self.assertIn("localStorage.getItem('uallak_orders_seen')", js)
        self.assertIn("localStorage.setItem('uallak_orders_seen'", js)
        self.assertIn("window.__uallakOrders", js)
        self.assertIn("seen.slice(-50)", js)

    def test_es5_only(self):
        for name, js in self.js.items():
            self.assertNotRegex(js, r"=>|\blet\b|\bconst\b|`", name)     # GTM custom JS variables are ES5

    def test_the_endpoint_never_carries_store_text_into_javascript(self):
        for bad in ("order-received');alert(1)//", "../x", "", "הזמנה", "a" * 70, "x y"):
            with self.assertRaises(ValueError, msg=bad):
                ct.clean_endpoint(bad)
        self.assertEqual(ct.clean_endpoint("/Order-Received/"), "order-received")


# ─── One sale definition ─────────────────────────────────────────────────────

def _order(oid, status, total, refunds=()):
    return {"id": oid, "number": str(oid), "status": status, "currency": "ILS", "total": f"{total:.2f}",
            "line_items": [{"product_id": 1, "name": "Shoe", "quantity": 1, "total": f"{total:.2f}"}],
            "refunds": [{"id": i, "total": f"-{a:.2f}", "reason": ""} for i, a in enumerate(refunds, 1)]}


class SaleDefinition(unittest.TestCase):
    ORDERS = [_order(1, "completed", 100, refunds=[10]), _order(2, "processing", 50), _order(3, "on-hold", 70),
              _order(4, "pending", 30), _order(5, "cancelled", 40), _order(6, "failed", 20),
              _order(7, "refunded", 60, refunds=[60]), _order(8, "checkout-draft", 5), _order(9, "trash", 5)]

    def test_only_processing_and_completed_are_sales(self):
        s = wc.summarize(self.ORDERS)
        self.assertEqual(wc.SALE_STATUSES, ("processing", "completed"))
        self.assertEqual(wc.PAID_STATUSES, wc.SALE_STATUSES)
        self.assertEqual((s["paid_orders"], s["gross_revenue"], s["refunded"], s["net_revenue"]),
                         (2, 150.0, 10.0, 140.0))
        self.assertEqual(s["awaiting_payment"], {"orders": 2, "total": 100.0})
        self.assertEqual(s["not_sales"], {"cancelled": {"orders": 1, "total": 40.0},
                                          "failed": {"orders": 1, "total": 20.0},
                                          "refunded": {"orders": 1, "total": 60.0}})
        self.assertEqual(s["orders"], 9)                                   # status_counts keep everything

    def test_status_lists_cover_every_status_without_overlap(self):
        groups = (wc.SALE_STATUSES, wc.AWAITING_PAYMENT_STATUSES, wc.NOT_SALE_STATUSES, wc.IGNORED_STATUSES)
        flat = [s for g in groups for s in g]
        self.assertEqual(len(flat), len(set(flat)))
        from agents import store_agent
        self.assertEqual(set(flat), set(store_agent.STATUS_HE))

    def test_no_second_definition_anywhere(self):
        """The status names that decide what is a sale appear only in the one
        definition (and the Hebrew status names) — never a second tuple."""
        allowed = {os.path.join("core", "woocommerce_service.py"), os.path.join("agents", "store_agent.py"),
                   os.path.join("agents", "support_agent.py")}
        for folder in ("core", "agents"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                rel = os.path.join(folder, name)
                if name.endswith(".py") and rel not in allowed:
                    self.assertNotRegex(_src(folder, name), r"[\"']on-hold[\"']|[\"']processing[\"'],\s*[\"']completed",
                                        rel)

    def test_every_reader_uses_the_same_numbers(self):
        from agents import store_agent
        from core import performance
        summary = {"connected": True, "period": "last_30_days", "complete": True, **wc.summarize(self.ORDERS)}
        saved = (store_agent.is_connected, store_agent.sales_summary, store_agent.status)
        store_agent.is_connected = lambda cid: True
        store_agent.sales_summary = lambda cid, period="last_30_days", now=None: summary
        store_agent.status = lambda cid: {"connected": True, "method": "key", "site": "shop.example"}
        try:
            page = performance._store_sales(27)
            section = store_agent.section(27)
        finally:
            store_agent.is_connected, store_agent.sales_summary, store_agent.status = saved
        for window in page["windows"]:
            self.assertEqual((window["revenue"], window["orders"]), (140.0, 2))
            self.assertEqual(window["awaiting_payment"], {"orders": 2, "total": 100.0})
        self.assertEqual(page["definition_he"], wc.SALE_DEFINITION_HE)
        self.assertEqual((section["last_30_days"]["net_revenue"], section["last_30_days"]["paid_orders"]), (140.0, 2))
        self.assertEqual(section["sale_definition_he"], wc.SALE_DEFINITION_HE)

    def test_both_chats_get_the_definition_and_the_live_read(self):
        src = _src("agents", "support_agent.py")
        self.assertIn("ONLY processing/completed", src)              # the website agent's notes
        self.assertIn("count ONLY processing/completed", src)        # the manager's SYSTEM
        self.assertEqual(src.count("_add_store_reads(client_id, message, payload)"), 2)
        self.assertIn("SALES ARE THE HEADLINE NUMBER", src)
        self.assertIn("NOT something we can read from here", src)   # no claimed attribution

    def test_the_hebrew_definition_is_plain_and_vendor_free(self):
        self.assertIn("בטיפול", wc.SALE_DEFINITION_HE)
        self.assertIn("הושלמה", wc.SALE_DEFINITION_HE)
        self.assertNotRegex(wc.SALE_DEFINITION_HE, r"[A-Za-z]")


# ─── Per-client isolation and the card ───────────────────────────────────────

class IsolationAndCard(unittest.TestCase):
    def setUp(self):
        self.saved = ct.get
        choices = {27: STORE_ONLY, 40: {"types": ["form_submit", "phone_click"], "confirmed": True, "stored": True}}
        ct.get = lambda cid: dict(choices.get(int(cid), {"types": ["form_submit"], "confirmed": False,
                                                         "stored": False}))

    def tearDown(self):
        ct.get = self.saved

    def card(self, client_id):
        return aa.card({"id": 1, "action": "configure_conversion", "client_id": client_id, "status": "proposed",
                        "params": {}, "approver": "client"}, "client")

    def test_each_client_sets_up_only_its_own_types(self):
        self.assertEqual(ct.gtm_types_for(27), ["store_sales"])
        self.assertEqual(ct.gtm_types_for(40), ["form_submit", "phone_click"])
        self.assertEqual(ct.gtm_types_for(99), [])                         # unreadable: nothing

    def test_store_only_card_says_purchases_and_never_leads(self):
        card = self.card(27)
        text = card["title"] + " " + card["what"]
        self.assertIn("רכישות", text)
        for lead_word in ("לידים", "טופס", "טפסים", "וואטסאפ", "טלפון", "פניות"):
            self.assertNotIn(lead_word, text)
        self.assertIn("רענון של הדף לא נספר שוב", card["what"])
        self.assertIn("שום דבר לא מותקן באתר עצמו", card["what"])

    def test_a_lead_client_card_is_unchanged_in_substance(self):
        what = self.card(40)["what"]
        self.assertIn("פניות מטפסים, שיחות טלפון", what)
        self.assertNotIn("רכישות", what)

    def test_done_line_follows_what_was_added(self):
        self.assertIn("מדידת הרכישות באתר פעילה", aa._conversion_done({"types": ["store_sales"],
                                                                         "purchase_verified": True}, "he"))
        self.assertNotIn("לידים", aa._conversion_done({"types": ["store_sales"], "purchase_verified": True}, "he"))
        both = aa._conversion_done({"types": ["form_submit", "store_sales"], "purchase_verified": True}, "he")
        self.assertIn("לידים", both)
        self.assertIn("רכישות", both)

    def test_setup_card_is_offered_for_a_pending_purchase_only_when_readable(self):
        base = {"connected": True, "needs_reconnect": False}
        self.assertTrue(aa._conversion_setup_possible({"tracking": {
            **base, "conversion_types": ["store_sales"], "conversion_types_not_live": ["store_sales"]}}, {}))
        self.assertFalse(aa._conversion_setup_possible({"tracking": {
            **base, "conversion_types": ["store_sales"], "conversion_types_not_live": []}}, {}))
        self.assertFalse(aa._conversion_setup_possible({"tracking": {
            **base, "conversion_types": ["store_sales"], "conversion_types_readable": False,
            "lead_event_live": False}}, {}))

    def test_client_state_marks_store_sales_pending_per_client(self):
        from core import client_state
        self.assertEqual(client_state._conversion_choice(27, [])["conversion_types_not_live"], ["store_sales"])
        self.assertEqual(client_state._conversion_choice(27, ["store_sales"])["conversion_types_not_live"], [])
        self.assertEqual(client_state._conversion_choice(40, ["form_submit"])["conversion_types_not_live"],
                         ["phone_click"])
        self.assertIs(client_state._conversion_choice(99, None)["conversion_types_readable"], False)

    def test_the_endpoint_is_read_from_this_clients_store_only(self):
        from agents import store_agent
        reads = []
        saved = (store_agent.credentials, store_agent.wc.get)
        store_agent.credentials = lambda cid: ({"site": "https://shin.example", "auth": ("k", "s"), "method": "key"}
                                               if int(cid) == 27 else {})
        store_agent.wc.get = lambda site, path, auth, params=None: (
            reads.append((site, path)) or ({"id": "x", "value": "order-received"}, {}))
        try:
            self.assertEqual(store_agent.order_received_endpoint(40),
                             {"slug": "order-received", "source": "default", "problem": ""})
            self.assertEqual(reads, [])                                        # no other store is read
            self.assertEqual(store_agent.order_received_endpoint(27)["source"], "store")
            self.assertEqual(reads, [("https://shin.example",
                                      "wc/v3/settings/advanced/woocommerce_checkout_order_received_endpoint")])
            store_agent.wc.get = lambda site, path, auth, params=None: ({"value": ""}, {})
            self.assertEqual(store_agent.order_received_endpoint(27)["problem"], "endpoint_empty")
            store_agent.wc.get = lambda site, path, auth, params=None: ({"value": "x');evil()//"}, {})
            self.assertEqual(store_agent.order_received_endpoint(27)["problem"], "endpoint_unusable")
        finally:
            store_agent.credentials, store_agent.wc.get = saved


class NoLeadPathForStoreOnly(unittest.TestCase):
    def test_the_interview_never_re_proposes_types_once_chosen(self):
        src = _src("agents", "interview_agent.py")
        self.assertIn('conversion_types.get(client_id)["confirmed"]', src)

    def test_prompts_forbid_lead_tracking_for_store_only(self):
        src = _src("agents", "support_agent.py")
        self.assertIn("when store_sales is their only type, NEVER suggest or", src)

    def test_the_website_skill_documents_the_path(self):
        skill = _src(".claude", "skills", "website", "SKILL.md")
        self.assertIn("uallak — purchase (order received)", skill)
        self.assertIn("SALE_STATUSES", _src("docs", "STORE.md"))


if __name__ == "__main__":
    unittest.main()
