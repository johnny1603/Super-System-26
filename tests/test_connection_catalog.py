"""Connections onboarding through the chat (core/connection_catalog, 2026-11-01).

- three tiers → three different flows; included shows nothing
- links come ONLY from the connection_catalog table
- a missing / disabled link falls back to human support, never a guessed URL
- "done" verifies and confirms, or schedules ONE reminder (never more)
- the dashboard has exactly one "חבר" button per connection, no help buttons
- keys never pass through this flow
"""
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import connection_catalog as cc  # noqa: E402


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


ROWS = [
    {"key": "ahrefs", "tier": "included", "signup_url": "", "pricing_url": "", "recommended_plan_note": "",
     "help_notes": "", "enabled": True},
    {"key": "tiktok", "tier": "free_account", "signup_url": "https://signup.example/tiktok", "pricing_url": "",
     "recommended_plan_note": "", "help_notes": "נרשמים עם מייל.", "enabled": True},
    {"key": "elevenlabs", "tier": "paid_account", "signup_url": "https://signup.example/11",
     "pricing_url": "https://pricing.example/11", "recommended_plan_note": "Starter.", "help_notes": "צעדים.",
     "enabled": True},
    {"key": "higgsfield", "tier": "paid_account", "signup_url": "", "pricing_url": "https://pricing.example/h",
     "recommended_plan_note": "", "help_notes": "", "enabled": False},
    {"key": "youtube", "tier": "free_account", "signup_url": "", "pricing_url": "", "recommended_plan_note": "",
     "help_notes": "", "enabled": True},
    {"key": "website", "tier": "paid_account", "signup_url": "", "pricing_url": "",
     "recommended_plan_note": "האחסון כלול.", "help_notes": "", "enabled": True},
]


class FakeQuery:
    def __init__(self, store, op="select", payload=None):
        self.store, self.op, self.payload, self.filters = store, op, payload, []

    def select(self, *_a, **_k): return self
    def limit(self, *_a): return self
    def order(self, *_a, **_k): return self

    def eq(self, field, value):
        self.filters.append(lambda r: r.get(field) == value)
        return self

    def like(self, field, pattern):
        prefix = pattern.rstrip("%")
        self.filters.append(lambda r: str(r.get(field, "")).startswith(prefix))
        return self

    def execute(self):
        if self.op == "upsert":
            self.store[self.payload["key"]] = dict(self.payload)
            return type("R", (), {"data": [self.payload]})()
        rows = [r for r in self.store.values() if all(f(r) for f in self.filters)]
        return type("R", (), {"data": rows})()


class FakeSettings:
    def __init__(self):
        self.store = {}

    def table(self, name):
        assert name == "app_settings"
        return self

    def select(self, *_a, **_k): return FakeQuery(self.store)
    def upsert(self, payload, on_conflict=None): return FakeQuery(self.store, "upsert", payload)


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = (cc.rows, cc.is_connected, cc._support_link, cc._db)
        cc.rows = lambda fresh=False: [dict(r) for r in ROWS]
        self.connected = set()
        cc.is_connected = lambda client_id, key: (client_id, key) in self.connected
        cc._support_link = lambda: "https://wa.me/972500000000"
        self.settings = FakeSettings()
        cc._db = lambda: self.settings
        from core import chat_fallback
        self.alerts = []
        self.saved_alert = chat_fallback.alert_once
        chat_fallback.alert_once = lambda *a, **k: self.alerts.append(a) or True

    def tearDown(self):
        cc.rows, cc.is_connected, cc._support_link, cc._db = self.saved
        from core import chat_fallback
        chat_fallback.alert_once = self.saved_alert


class ThreeTiers(Base):
    def test_included_shows_nothing(self):
        for step in ("start", "has", "none", "done"):
            self.assertEqual(cc.flow(1, "ahrefs", step), {"show": False})

    def test_unknown_key_shows_nothing(self):
        self.assertEqual(cc.flow(1, "nope", "start"), {"show": False})

    def test_start_asks_one_question_with_two_chips(self):
        r = cc.flow(1, "tiktok", "start")
        self.assertTrue(r["show"])
        self.assertEqual(len(r["messages"]), 1)
        self.assertEqual([c["label"] for c in r["chips"]], ["יש לי חשבון", "אין לי עדיין"])
        self.assertEqual([c["step"] for c in r["chips"]], ["has", "none"])

    def test_has_runs_the_existing_connect(self):
        r = cc.flow(1, "tiktok", "has")
        self.assertEqual(r["connect"], "tiktok")
        self.assertEqual(r["chips"], [])

    def test_free_and_paid_differ(self):
        free = "\n".join(cc.flow(1, "tiktok", "none")["messages"])
        paid = "\n".join(cc.flow(1, "elevenlabs", "none")["messages"])
        self.assertIn("https://signup.example/tiktok", free)
        self.assertNotIn("לעולם לא אלינו", free)
        self.assertIn("https://signup.example/11", paid)
        self.assertIn("https://pricing.example/11", paid)
        self.assertIn("Starter.", paid)
        self.assertIn("ישירות מכם אליהם — לעולם לא אלינו", paid)

    def test_three_tiers_three_flows(self):
        flows = {cc.flow(1, "ahrefs", "none").get("show"),
                 "\n".join(cc.flow(1, "tiktok", "none")["messages"]),
                 "\n".join(cc.flow(1, "elevenlabs", "none")["messages"])}
        self.assertEqual(len(flows), 3)

    def test_none_offers_done_chip(self):
        self.assertEqual([c["step"] for c in cc.flow(1, "tiktok", "none")["chips"]], ["done"])

    def test_messages_are_hebrew(self):
        for key in ("tiktok", "elevenlabs"):
            for step in ("start", "has", "none"):
                for line in cc.flow(1, key, step)["messages"]:
                    self.assertTrue(re.search(r"[֐-׿]", line), line)


class LinksFromTableOnly(Base):
    def test_module_holds_no_vendor_url(self):
        source = _src("core", "connection_catalog.py")
        self.assertEqual(re.findall(r"https://[\w.-]+", source), [])

    def test_changing_the_table_changes_the_link(self):
        ROWS_EDIT = [dict(r) for r in ROWS]
        ROWS_EDIT[1]["signup_url"] = "https://edited.example/new"
        cc.rows = lambda fresh=False: ROWS_EDIT
        text = "\n".join(cc.flow(1, "tiktok", "none")["messages"])
        self.assertIn("https://edited.example/new", text)
        self.assertNotIn("https://signup.example/tiktok", text)

    def test_only_https_reaches_a_client(self):
        self.assertEqual(cc._clean({"key": "x", "tier": "free_account", "signup_url": "http://a.example",
                                    "enabled": True})["signup_url"], "")
        self.assertEqual(cc._clean({"key": "x", "tier": "free_account", "signup_url": "javascript:alert(1)",
                                    "enabled": True})["signup_url"], "")

    def test_concierge_links_come_from_the_catalog(self):
        from core import connection_help
        entry = connection_help.get("elevenlabs")
        block = connection_help.for_agent(entry, "he", "050")
        self.assertEqual(sorted(l["url"] for l in block["links"]),
                         ["https://pricing.example/11", "https://signup.example/11"])
        reply = connection_help.strip_foreign_links(
            "go https://elevenlabs.io and https://signup.example/11", entry)
        self.assertNotIn("elevenlabs.io", reply)
        self.assertIn("https://signup.example/11", reply)

    def test_disabled_row_gives_the_concierge_no_links(self):
        from core import connection_help
        self.assertEqual(connection_help.for_agent(connection_help.get("higgsfield"), "he", "050")["links"], [])


class HumanFallback(Base):
    def test_disabled_row_falls_back_to_support(self):
        r = cc.flow(5, "higgsfield", "none")
        self.assertTrue(r["support"])
        text = "\n".join(r["messages"])
        self.assertNotIn("pricing.example", text)
        self.assertIn("https://wa.me/972500000000", text)
        self.assertTrue(self.alerts)

    def test_enabled_without_signup_falls_back(self):
        r = cc.flow(5, "youtube", "none")
        self.assertTrue(r["support"])
        self.assertIn("no_signup_url", self.alerts[-1][2])

    def test_missing_row_falls_back(self):
        r = cc.flow(5, "manychat", "none")   # known connection, no row in this table
        self.assertTrue(r["support"])

    def test_no_whatsapp_still_says_a_person_will_help(self):
        cc._support_link = lambda: ""
        r = cc.flow(5, "youtube", "none")
        self.assertIn("הצוות", r["messages"][0])

    def test_website_offers_our_build_not_links(self):
        r = cc.flow(5, "website", "none")
        self.assertEqual(r["connect"], "website_create")
        self.assertFalse(r["support"])


class DoneAndReminder(Base):
    def test_connected_confirms(self):
        self.connected.add((7, "tiktok"))
        r = cc.flow(7, "tiktok", "done")
        self.assertTrue(r["connected"])
        self.assertIn("מחובר ✓", r["messages"][0])
        self.assertEqual(self.settings.store, {})

    def test_not_connected_schedules_one_reminder(self):
        cc.flow(7, "tiktok", "done")
        cc.flow(7, "tiktok", "done")
        self.assertEqual(list(self.settings.store), ["conn_reminder:7:tiktok"])

    def test_reminder_sent_once_then_never(self):
        cc.flow(7, "tiktok", "done")
        marker = self.settings.store["conn_reminder:7:tiktok"]
        marker["value"]["due"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        posted = []
        from core import agent_actions
        saved = agent_actions._post_client_message
        agent_actions._post_client_message = lambda cid, persona, text: posted.append((cid, persona, text))
        try:
            self.assertEqual(cc.send_due_reminders()["sent"], 1)
            self.assertEqual(cc.send_due_reminders()["sent"], 0)
            cc.flow(7, "tiktok", "done")   # asking again never re-arms it
            self.assertEqual(cc.send_due_reminders()["sent"], 0)
        finally:
            agent_actions._post_client_message = saved
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0][:2], (7, "media"))

    def test_reminder_not_due_yet_waits(self):
        cc.flow(7, "tiktok", "done")
        self.assertEqual(cc.send_due_reminders(), {"sent": 0, "resolved": 0})

    def test_unverifiable_goes_to_the_team(self):
        r = cc.flow(7, "elevenlabs", "done")   # vendor tool: verifiable
        self.assertIn("connected", r)
        r = cc.flow(7, "website", "done")
        self.assertIn("connected", r)
        rows = [dict(x) for x in ROWS] + [{"key": "creatomate", "tier": "paid_account",
                                             "signup_url": "https://s.example", "pricing_url": "",
                                             "recommended_plan_note": "", "help_notes": "", "enabled": True}]
        cc.rows = lambda fresh=False: rows
        r = cc.flow(7, "creatomate", "done")
        self.assertNotIn("connected", r)
        self.assertIn("הצוות", r["messages"][0])

    def test_clients_never_mix(self):
        cc.flow(7, "tiktok", "done")
        cc.flow(8, "tiktok", "done")
        self.assertEqual(sorted(self.settings.store), ["conn_reminder:7:tiktok", "conn_reminder:8:tiktok"])


class AdminEdit(Base):
    def test_rejects_non_https(self):
        with self.assertRaises(ValueError):
            cc.admin_update("tiktok", {"signup_url": "http://x.example"})
        with self.assertRaises(ValueError):
            cc.admin_update("tiktok", {"tier": "gold"})
        with self.assertRaises(ValueError):
            cc.admin_update("nope", {"enabled": True})


class Dashboard(unittest.TestCase):
    html = _src("dashboard", "client", "index.html")
    grid = html.split('id="connGrid"', 1)[1].split('<div class="section-head">', 1)[0]

    def test_no_help_buttons_on_cards(self):
        self.assertNotIn('class="conn-help-btn" data-help-id', self.html)

    def test_old_extra_buttons_are_gone(self):
        for gone in ('id="siteExistingBtn"', 'id="siteCreateBtn"', "chatAboutAutomation('manychat')\"",
                     "openToolGuide('higgsfield')\"", "openToolGuide('elevenlabs')\""):
            self.assertNotIn(gone, self.grid)

    def test_one_connect_button_per_connection(self):
        keys = re.findall(r"onclick=\"connectViaChat\('([a-z_]+)'\)\" data-i18n=\"conn_connect\">חבר</button>", self.grid)
        self.assertEqual(sorted(keys), sorted(["google_ads", "meta", "tiktok", "youtube", "gmail", "search_console",
                                               "analytics", "google_drive", "tag_manager", "website", "higgsfield",
                                               "elevenlabs", "manychat"]))
        self.assertEqual(len(keys), len(set(keys)))

    def test_cards_start_no_connect_flow_directly(self):
        for direct in ('onclick="connectGoogleAds()"', 'onclick="connectMeta()"', 'onclick="connectYoutube()"',
                       'onclick="connectGtm()"', 'onclick="connectTiktokExplained()"'):
            self.assertNotIn(direct, self.grid)

    def test_every_chat_action_exists(self):
        block = self.html.split("const CONNECT_ACTIONS = {", 1)[1].split("};", 1)[0]
        for key in ("google_ads", "meta", "tiktok", "youtube", "gmail", "search_console", "analytics",
                    "google_drive", "tag_manager", "website", "website_create", "higgsfield", "elevenlabs", "manychat"):
            self.assertIn(key + ":", block)

    def test_keys_are_pasted_on_the_card_not_in_chat(self):
        fn = self.html.split("async function connectViaChat", 1)[1].split("\n}\n", 1)[0]
        self.assertNotIn("api_key", fn)
        self.assertIn("JSON.stringify({ key, step })", fn)


class Api(unittest.TestCase):
    src = _src("core", "api_server.py")

    def test_endpoints(self):
        for route in ('@app.post("/api/client/connect-chat")', '@app.get("/api/connections/reminders", dependencies=_admin_only)',
                      '@app.get("/api/admin/connection-catalog")', '@app.post("/api/admin/connection-catalog/{key}")'):
            self.assertIn(route, self.src)

    def test_request_carries_no_secret(self):
        model = self.src.split("class ConnectChatRequest(BaseModel):", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("api_key", model)
        self.assertNotIn("token", model)

    def test_staff_view_is_read_only(self):
        fn = self.src.split("def client_connect_chat", 1)[1].split("\n@app.", 1)[0]
        self.assertIn("_admin_view_client(request) is not None", fn)

    def test_routes_before_root_mount(self):
        self.assertLess(self.src.index('"/api/client/connect-chat"'), self.src.index('app.mount("/"'))


class Migration(unittest.TestCase):
    sql = _src("migrations", "2026-11-01-connection-catalog.sql")

    def _rows(self):
        body = self.sql.split("values", 1)[1].split("on conflict", 1)[0]
        return re.findall(r"\('([a-z_]+)',\s*'(\w+)',\s*'([^']*)',\s*'([^']*)',\s*'[^']*',\s*'[^']*',\s*(true|false)\)",
                          body, re.S)

    def test_idempotent(self):
        self.assertIn("create table if not exists connection_catalog", self.sql)
        self.assertIn("on conflict (key) do nothing", self.sql)

    def test_seed_tiers(self):
        tiers = {k: t for k, t, *_ in self._rows()}
        for k in ("ahrefs", "ai_engine", "google_places", "outreach_sending", "landing_pages"):
            self.assertEqual(tiers[k], "included")
        for k in ("google_ads", "youtube", "tag_manager", "google_drive", "gmail", "search_console", "analytics",
                  "merchant_center", "tiktok"):
            self.assertEqual(tiers[k], "free_account")
        for k in ("manychat", "higgsfield", "elevenlabs", "creatomate", "uppbeat", "website"):
            self.assertEqual(tiers[k], "paid_account")
        self.assertNotIn("heygen", tiers)
        self.assertNotIn("make", tiers)

    def test_unverified_links_are_empty_and_disabled(self):
        for key, tier, signup, pricing, enabled in self._rows():
            if tier == "included" or key == "website":
                continue
            if not signup:
                self.assertEqual(enabled, "false", key)
            for url in (signup, pricing):
                self.assertTrue(url == "" or url.startswith("https://"), url)

    def test_txt_copy_is_gitignored(self):
        self.assertIn("catalog-for-supabase.txt", _src(".gitignore"))


if __name__ == "__main__":
    unittest.main()
