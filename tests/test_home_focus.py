"""Home invitations + the prerequisites check (core/home_focus) — 2026-11-16.

What must stay true:
- a card is shown only for a service that really exists for THIS client and
  that they have not started; never more than three; a package that cannot be
  read shows none;
- every card text is in 5 languages and names no tool or vendor;
- a click with missing connections does NOT start the job: the agent's calm
  focus line, exactly the missing items (one connect key each), the request
  remembered — and handed back ONCE when they are connected;
- the same check reaches the chat (prompt_block) with the chat's connect cards;
- the page has the cards, the click and the connect buttons.
"""
import os
import re
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

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

from core import home_focus as hf           # noqa: E402
from test_landing_own import FakeDB          # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
VENDORS = re.compile(r"higgsfield|heygen|eleven\s?labs|manychat|instawp|semrush|ahrefs|kling|veo|sora|make\.com",
                     re.IGNORECASE)


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.steps = {"website", "google_ads", "meta", "media", "avatar"}
        self.resolved = True
        self.started = set()
        self.presenter = True
        self.posted = []
        self.covered = True
        self.saved = [(hf, n, getattr(hf, n)) for n in ("_db", "_package", "_started", "_search_console_on",
                                                         "_organic_covered")]
        hf._organic_covered = lambda cid: self.covered
        hf._db = lambda: self.db
        hf._package = lambda cid: {"resolved": self.resolved, "steps": set(self.steps), "services": ["seo"]}
        hf._started = lambda cid, service, cover: service in self.started
        hf._search_console_on = lambda: False
        hf._block_cache.clear()
        # The presenter gate and the thread write, without their modules' reads
        from core import avatar_video
        from agents import client_agent, support_agent
        self.saved += [(avatar_video, "presenter_allowed", avatar_video.presenter_allowed),
                       (client_agent, "log_communication", client_agent.log_communication)]
        avatar_video.presenter_allowed = lambda cid=None: self.presenter
        client_agent.log_communication = lambda cid, d, ch, text, *a, **k: self.posted.append((ch, text))
        self.channel = support_agent.persona_channel

    def tearDown(self):
        for mod, name, value in self.saved:
            setattr(mod, name, value)

    def connect(self, *platforms):
        rows = self.db.tables.setdefault("client_accounts", [])
        for p in platforms:
            rows.append({"client_id": 7, "platform": p, "status": "active"})


class Cards(Base):
    def test_every_text_in_five_languages_and_no_vendor(self):
        tables = [t for v in hf.INVITE_TEXT.values() for t in v.values()]
        tables += list(hf.STEP_LABEL.values()) + list(hf.WHY.values()) + [hf.FOCUS_LINE, hf.FOCUS_AFTER]
        for table in tables:
            self.assertEqual(set(table), set(LANGS))
            for lang in LANGS:
                self.assertTrue(table[lang].strip())
                self.assertIsNone(VENDORS.search(table[lang]), table[lang])

    def test_only_real_services_never_more_than_three(self):
        ids = [c["id"] for c in hf.invites(7, "en")]
        self.assertEqual(ids, ["avatar", "social", "organic"])
        self.assertLessEqual(len(hf.invites(7, "he")), hf.MAX_INVITES)

    def test_started_or_missing_from_the_package_is_not_shown(self):
        self.started = {"social"}
        self.steps = {"website"}
        ids = [c["id"] for c in hf.invites(7, "en")]
        self.assertNotIn("social", ids)       # started
        self.assertNotIn("google_ads", ids)   # not in the package
        self.assertNotIn("avatar", ids)       # not in the package
        self.assertEqual(ids, ["organic", "automation"])

    def test_presenter_gate_hides_the_avatar_card(self):
        self.presenter = False
        self.assertNotIn("avatar", [c["id"] for c in hf.invites(7, "en")])

    def test_unreadable_package_shows_nothing(self):
        self.resolved = False
        self.assertEqual(hf.invites(7, "en"), [])
        hf._package = lambda cid: (_ for _ in ()).throw(RuntimeError("down"))
        self.assertEqual(hf.invites(7, "en"), [])

    def test_card_carries_persona_face_and_text(self):
        card = hf.invites(7, "fr")[1]
        self.assertEqual((card["id"], card["persona"], card["face"]), ("social", "meta", "maya"))
        self.assertEqual(card["title"], hf.INVITE_TEXT["social"]["title"]["fr"])
        self.assertFalse(card["ready"])   # nothing connected

    def test_faces_exist(self):
        for spec in hf.SERVICES.values():
            if spec["face"]:
                self.assertTrue(os.path.exists(os.path.join(ROOT, "dashboard", "assets", "agents",
                                                            f"{spec['face']}-portrait.png")), spec["face"])


class Prerequisites(Base):
    def test_each_agent_names_only_what_its_job_needs(self):
        self.assertEqual([i["connect"] for i in hf.prerequisites(7, "media")["items"]], ["higgsfield"])
        self.assertEqual([i["connect"] for i in hf.prerequisites(7, "organic")["items"]], ["website"])
        self.assertEqual([i["connect"] for i in hf.prerequisites(7, "google_ads")["items"]], ["google_ads"])
        self.assertEqual([i["connect"] for i in hf.prerequisites(7, "social")["items"]], ["meta"])
        self.assertTrue(hf.prerequisites(7, "automation")["ready"])
        self.assertTrue(hf.prerequisites(7, "branding")["ready"])
        hf._search_console_on = lambda: True
        self.assertEqual([i["connect"] for i in hf.prerequisites(7, "organic")["items"]],
                         ["website", "search_console"])

    def test_the_item_label_is_a_plain_description(self):
        item = hf.prerequisites(7, "media", "en")["items"][0]
        self.assertEqual(item["label"], "The AI image and video tool")
        self.assertIsNone(VENDORS.search(hf.prerequisites(7, "media", "he")["message"]))

    def test_a_click_with_gaps_focuses_first_and_remembers(self):
        out = hf.start(7, "social", "en")
        self.assertFalse(out["ready"])
        self.assertTrue(out["message"].startswith(hf.FOCUS_LINE["en"]))
        self.assertIn("Facebook and Instagram", out["message"])
        self.assertEqual(self.posted, [(self.channel("meta"), out["message"])])   # in Maya's own thread
        row = self.db.tables["app_settings"][0]
        self.assertEqual((row["key"], row["value"]["service"]), ("invite_resume:7", "social"))

    def test_a_ready_click_only_returns_the_opener(self):
        self.connect("meta_page")
        out = hf.start(7, "social", "ru")
        self.assertTrue(out["ready"])
        self.assertEqual(out["opener"], hf.INVITE_TEXT["social"]["opener"]["ru"])
        self.assertEqual(self.posted, [])
        self.assertFalse(self.db.tables.get("app_settings"))

    def test_resume_comes_back_once_when_connected(self):
        hf.start(7, "organic", "he")
        self.assertEqual(hf.resume(7), {})                  # still missing: nothing yet, row kept
        self.assertTrue(self.db.tables["app_settings"])
        self.connect("wordpress")
        back = hf.resume(7)
        self.assertEqual((back["service"], back["persona"]), ("organic", "website"))
        self.assertEqual(back["opener"], hf.INVITE_TEXT["organic"]["opener"]["he"])
        self.assertEqual(hf.resume(7), {})                  # once

    def test_an_old_request_is_forgotten(self):
        old = (datetime.now(timezone.utc) - timedelta(days=hf.RESUME_DAYS + 1)).isoformat()
        self.db.tables["app_settings"] = [{"key": "invite_resume:7", "value": {"service": "social", "at": old}}]
        self.connect("meta_page")
        self.assertEqual(hf.resume(7), {})
        self.assertFalse(self.db.tables["app_settings"])

    def test_organic_without_the_service_asks_for_nothing_first(self):
        self.covered = False
        out = hf.start(7, "organic", "en")
        self.assertTrue(out["ready"])           # the taste and the upgrade card come first
        self.assertNotIn("organic", hf.prompt_block(7, "en"))

    def test_unknown_service_refused(self):
        with self.assertRaises(ValueError):
            hf.start(7, "rockets", "en")

    def test_the_chat_gets_the_same_check(self):
        self.connect("wordpress")
        block = hf.prompt_block(7, "en")
        self.assertNotIn("organic", block)
        self.assertEqual(block["media"]["connect_cards"], ["connect_creative_tool"])
        self.assertEqual(block["google_ads"]["connect_cards"], ["connect_google_ads"])
        from core import agent_actions
        for entry in block.values():
            for name in entry["connect_cards"]:
                self.assertIn(name, agent_actions.ACTIONS)


class Chat(unittest.TestCase):
    def test_the_rule_and_the_payload(self):
        from agents import support_agent
        rule = support_agent.STUDIO_RULE
        self.assertIn("PREREQUISITES", rule)
        self.assertIn("connect_cards", rule)
        self.assertNotIn("{", rule)              # it goes through a .format() template
        self.assertIn("home_focus.prompt_block(client_id", _src("agents", "support_agent.py"))


class OrganicTaste(unittest.TestCase):
    def test_the_taste_is_small_plain_and_names_no_site(self):
        from core import seo_report
        for lang in LANGS:
            text = seo_report.taste(lang)
            self.assertEqual(text.count("\u2022"), len(seo_report.TASTE_SHOWN))   # three kinds, not a list
            self.assertIsNone(re.search(r"https?://|www\.|\.co\.il|\.com\b", text))
            self.assertIsNone(VENDORS.search(text))
        for table in list(seo_report.TASTE_KIND.values()) + [seo_report.TASTE_INTRO, seo_report.TASTE_OUTRO]:
            self.assertEqual(set(table), set(LANGS))

    def test_without_the_service_the_agents_get_the_kinds(self):
        from core import seo_report
        saved = seo_report.status
        try:
            seo_report._block_cache.clear()
            seo_report.status = lambda cid: {"available": True, "package_readable": True, "covered_by_package": False,
                                             "has_report": False, "built_at": ""}
            block = seo_report.prompt_block(8)
            self.assertEqual(len(block["taste_kinds"]), len(seo_report.TYPES))
            seo_report._block_cache.clear()
            seo_report.status = lambda cid: {"available": True, "package_readable": True, "covered_by_package": True,
                                             "has_report": False, "built_at": ""}
            self.assertNotIn("taste_kinds", seo_report.prompt_block(8))
        finally:
            seo_report.status = saved
            seo_report._block_cache.clear()

    def test_the_card_path_and_the_rule(self):
        self.assertIn("seo_report.taste(lang)", _src("core", "seo_report_actions.py"))
        from agents import support_agent
        self.assertIn("SMALL TASTE", support_agent.STUDIO_RULE)
        self.assertIn("NEVER a real site's name", support_agent.STUDIO_RULE)


class Wiring(unittest.TestCase):
    def test_routes_parity_and_staff_view(self):
        api = _src("core", "api_server.py")
        self.assertIn('@app.get("/api/client/home-focus")', api)
        self.assertIn('@app.post("/api/client/invite")', api)
        from core import admin_view, chat_parity
        self.assertIn("GET /api/client/home-focus", chat_parity.PARITY)
        self.assertIn("POST /api/client/invite", chat_parity.PARITY)
        self.assertIn("client_home_focus", admin_view.READ_ENDPOINTS)

    def test_the_page(self):
        page = _src("dashboard", "client", "index.html")
        for needle in ('id="agentInvites"', "function openInvite(", "function appendPrereqButtons(",
                       "connectViaChat(it.connect)", "loadHomeFocus();", "'/api/client/invite'",
                       "function resumeInvite("):
            self.assertIn(needle, page)
        self.assertIn("invite_failed:", page)
        self.assertIn("prereq_connect:", page)


if __name__ == "__main__":
    unittest.main()
