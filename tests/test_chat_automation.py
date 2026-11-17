"""Chat automation is a sales talk that ends in ONE "schedule a call" card (2026-11-17).

What must stay true (core/chat_automation):
- no tool or vendor name in any string of this flow, in any language: the pitch,
  the card, the manager's rule, the dashboard card, the connect flow;
- never "do you already have an account", never a sign-up link or a chip, never a
  connect step: every step of the old connect flow is the pitch + the card;
- the pitch is transparent: our platform is the bridge, a team member builds it
  with the client, a one-off setup fee + an ongoing software subscription, the
  price only after the call. No number;
- the ONLY next step is `book_automation_call`; approving it opens ONE tracked
  team request (a card topic a model's handoff can't use); a second ask shows
  the same card;
- the dashboard card says "tell me more" (5 languages), never "connect";
- the WhatsApp Business card stays "coming soon".
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")

for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}), ("httpx", {}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from core import agent_actions as aa                     # noqa: E402
from core import chat_automation as ca                   # noqa: E402
from core import connection_catalog as cc                # noqa: E402
from core import vendor_names as vn                      # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
VENDOR = re.compile(r"many\s?chat|make\.com|zapier|chatfuel|manychat\.com", re.IGNORECASE)
ACCOUNT_QUESTION = re.compile(r"יש לכם כבר חשבון|already have an account|חשבון שם|sign\s?up|הרשמה", re.IGNORECASE)


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class NoVendorName(unittest.TestCase):
    def test_every_table_in_every_language(self):
        for key, table in ca.TEXT.items():
            self.assertEqual(set(table), set(LANGS), key)
            for lang, text in table.items():
                self.assertIsNone(VENDOR.search(text), f"{key}/{lang}")
                self.assertEqual(vn.find(text), [], f"{key}/{lang}")
                self.assertIsNone(ACCOUNT_QUESTION.search(text), f"{key}/{lang}")

    def test_the_managers_rule_never_learns_the_name(self):
        self.assertIsNone(VENDOR.search(ca.SALES_RULE))
        self.assertNotIn("{", ca.SALES_RULE)
        from agents import support_agent
        self.assertIs(support_agent._AUTOMATION_RULE, ca.SALES_RULE)
        self.assertNotIn("automation_service", _src("agents", "support_agent.py"))

    def test_dashboard_card_and_upgrade_panel(self):
        html = _src("dashboard", "client", "index.html")
        card = html[html.index('data-i18n="manychat_card"') - 600:html.index('id="automationBtn"') + 200]
        self.assertIsNone(VENDOR.search(re.sub(r'data-i18n="manychat_\w+"', "", card)))
        for line in html.splitlines():
            if re.match(r"^\s*(manychat_\w+|automation_more)\s*:\s*\{", line):
                self.assertIsNone(VENDOR.search(line.split(":", 1)[1]), line[:60])
        self.assertNotIn("AUTOMATION_NAMES", html)
        from agents import onboarding_agent
        entry = next(a for a in onboarding_agent.get_upgrade_addons() if a["id"] == "automation")
        self.assertIsNone(re.search(r"₪\s?\d|\d+\s?₪", entry["description"]))      # no automatic price


class NeverAConnectFlow(unittest.TestCase):
    def test_every_step_is_the_pitch_and_the_card(self):
        for step in ("start", "has", "none", "done"):
            for lang in LANGS:
                r = cc.flow(5, "manychat", step, lang=lang)
                self.assertEqual(r["card_action"], "book_automation_call")
                self.assertEqual((r["chips"], r["connect"], r["support"]), ([], "", False))
                text = "\n".join(r["messages"])
                self.assertIsNone(VENDOR.search(text))
                self.assertIsNone(ACCOUNT_QUESTION.search(text))
                self.assertNotIn("http", text)

    def test_the_pitch_is_transparent_and_has_no_number(self):
        he = ca.pitch("he")
        for needle in ("בכל שעה", "מתחרה", "מקצועי", "רציני", "תקציב", "לא רלוונטיות", "הגשר",
                       "איש צוות שלנו", "דמי הקמה חד-פעמיים", "מנוי שוטף", "רק אחרי שיחת אפיון"):
            self.assertIn(needle, he)
        en = ca.pitch("en")
        for needle in ("instantly", "competitor", "qualifies", "bridge", "one-off setup fee", "subscription",
                       "only after"):
            self.assertIn(needle, en)
        for lang in LANGS:
            self.assertIsNone(re.search(r"\d", ca.pitch(lang)), lang)

    def test_api_proposes_the_card_by_code(self):
        fn = _src("core", "api_server.py").split("def client_connect_chat", 1)[1].split("\n@app.", 1)[0]
        self.assertIn('if result.get("card_action"):', fn)
        self.assertIn("by_code=True", fn)
        html = _src("dashboard", "client", "index.html")
        self.assertIn("if (data.card) appendActionCard(data.persona, data.card);", html)
        self.assertIn("manychat: () => openInvite('automation'),", html)


class TheCard(unittest.TestCase):
    def setUp(self):
        self.opened = []
        self.saved = aa.open_team_request

        def opened(client_id, persona, topic, request, origin="client_chat"):
            self.opened.append((client_id, topic, request))
            return {"card": {"id": 9}, "status": "awaiting_staff", "already": len(self.opened) > 1}
        aa.open_team_request = opened

    def tearDown(self):
        aa.open_team_request = self.saved

    def test_spec(self):
        spec = aa.ACTIONS["book_automation_call"]
        self.assertEqual((spec["approver"], spec["kind"], spec["internal_only"]), ("client", "execute", False))
        self.assertTrue(spec["client_requestable"])
        for key in ("title", "what", "button", "done"):
            self.assertEqual(set(spec[key]), set(LANGS), key)
        self.assertEqual(spec["title"]["he"], "לקבוע שיחה עם נציג")
        for lang in LANGS:
            self.assertIsNone(re.search(r"\d", spec["what"][lang]), lang)   # never a price
        # no other automation card exists: no connect, no credentials
        self.assertEqual([n for n in aa.ACTIONS if "automation" in n], ["book_automation_call"])

    def test_approval_opens_one_tracked_request(self):
        run = aa.ACTIONS["book_automation_call"]["run"]
        out = run(27, {"about": "רוב הפניות בוואטסאפ בערב"}, None)
        self.assertEqual(self.opened[0][:2], (27, "automation_call"))
        self.assertIn("רוב הפניות בוואטסאפ בערב", self.opened[0][2])
        self.assertEqual(aa.ACTIONS["book_automation_call"]["done_message"](out, "he"), ca.DONE["he"])
        again = run(27, {}, None)
        self.assertEqual(aa.ACTIONS["book_automation_call"]["done_message"](again, "en"), ca.ALREADY["en"])

    def test_a_storage_failure_is_a_calm_line(self):
        aa.open_team_request = lambda *a, **k: {"error": "storage_unavailable"}
        with self.assertRaises(aa.ActionFailed):
            aa.ACTIONS["book_automation_call"]["run"](27, {}, None)
        self.assertEqual(aa.ACTIONS["book_automation_call"]["refusals"]["error"], ca.FAILED)

    def test_one_open_card_never_two(self):
        prepare = aa.ACTIONS["book_automation_call"]["prepare"]
        self.assertEqual(prepare(27, {"about": ""}, {}, [{"id": 4}]), {"existing": {"id": 4}})
        self.assertEqual(prepare(27, {"about": "x"}, {}, []), {"params": {"about": "x"}})

    def test_the_topic_is_for_the_card_only(self):
        self.assertEqual(aa.team_topic("automation_call"), "")           # a model handoff can't open it
        self.assertEqual(aa._v_team_topic("automation_call", 27), "automation_call")
        self.assertIn("automation_call", aa._TEAM_TOPIC_LABEL)
        self.assertEqual(aa.TEAM_REQUEST_TOPICS, ("legal", "tax", "contract", "billing", "package_exception"))


class Unchanged(unittest.TestCase):
    def test_whatsapp_business_stays_coming_soon(self):
        html = _src("dashboard", "client", "index.html")
        card = html[html.index('data-i18n="whatsapp_card"'):]
        card = card[:card.index("</button>")]
        self.assertIn('data-i18n="coming_soon_status"', card)
        self.assertIn('class="conn-btn locked-btn" disabled data-i18n="coming_soon"', card)

    def test_vendor_stays_admin_side(self):
        from core import automation_service
        self.assertEqual(automation_service.VENDORS["manychat"]["name"], "ManyChat")
        self.assertIsNone(VENDOR.search(automation_service.POLICY_NOTE))


if __name__ == "__main__":
    unittest.main()
