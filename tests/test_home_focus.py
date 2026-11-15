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


class Onboarding(unittest.TestCase):
    def setUp(self):
        from agents import interview_agent
        from core import client_journey, quarter_goals
        self.status = {"resolved": True, "complete": False, "done_count": 1, "total_count": 3, "next_step": "meta"}
        self.interviewed, self.goals = False, {}
        self.saved = [(client_journey, "connection_status", client_journey.connection_status),
                      (interview_agent, "is_completed", interview_agent.is_completed),
                      (quarter_goals, "agreed", quarter_goals.agreed)]
        client_journey.connection_status = lambda cid: dict(self.status)
        interview_agent.is_completed = lambda cid: self.interviewed
        quarter_goals.agreed = lambda cid: self.goals

    def tearDown(self):
        for mod, name, value in self.saved:
            setattr(mod, name, value)

    def test_focus_until_all_three_are_done(self):
        ob = hf.onboarding(7)
        self.assertEqual((ob["known"], ob["done"]), (True, False))
        self.assertEqual(ob["connections"], {"complete": False, "done": 1, "total": 3, "next": "meta"})
        self.status["complete"] = True
        self.interviewed = True
        self.assertFalse(hf.onboarding(7)["done"])          # goals not agreed yet
        self.goals = {"agreed_at": "2026-11-16"}
        self.assertTrue(hf.onboarding(7)["done"])

    def test_unknown_state_never_builds_a_focus_mode(self):
        self.status["resolved"] = False
        self.assertEqual(hf.onboarding(7), {"known": False, "done": True})
        from core import client_journey
        client_journey.connection_status = lambda cid: (_ for _ in ()).throw(RuntimeError("down"))
        self.assertEqual(hf.onboarding(7), {"known": False, "done": True})

    def test_the_goals_card(self):
        from core import agent_actions
        spec = agent_actions.ACTIONS["agree_quarter_goals"]
        self.assertEqual(spec["approver"], "client")
        for key in ("title", "what", "done", "button"):
            self.assertEqual(set(spec[key]), set(LANGS))
            for lang in LANGS:
                self.assertNotRegex(spec[key][lang], r"(?i)guarantee|מובטח|garanti")
        words = spec["params"]["keywords"]["validate"]("עורך דין גירושין, עורך דין גירושין , ליווי משפטי", 7)
        self.assertEqual(words, ["עורך דין גירושין", "ליווי משפטי"])
        with self.assertRaises(ValueError):
            spec["params"]["posts_per_week"]["validate"](9, 7)

    def test_the_page_focus(self):
        page = _src("dashboard", "client", "index.html")
        for needle in ('id="homeFocus"', 'id="pendingHead"', "function applyOnboardingFocus(",
                       "body.onboarding-focus #pendingHead, body.onboarding-focus #pendingApprovalList",
                       "() => startInterview()", "hf_goals_opener", "function paintFocusLock("):
            self.assertIn(needle, page)
        for key in ("hf_title", "hf_lock", "hf_char_title", "hf_goals_title"):
            line = next(l for l in page.splitlines() if l.strip().startswith(key + ":"))
            for lang in LANGS:
                self.assertIn(lang + ":", line)


class GoalTrack(unittest.TestCase):
    def setUp(self):
        from core import content_team, quarter_goals as qg
        self.qg = qg
        self.db = FakeDB()
        self.plan = {}
        self.saved = [(qg, "_db", qg._db), (qg, "_positions", qg._positions),
                      (content_team, "current", content_team.current)]
        qg._db = lambda: self.db
        qg._positions = lambda cid, words: {}
        content_team.current = lambda cid: self.plan
        now = datetime.now(timezone.utc)
        self.recent = (now - timedelta(days=2)).isoformat()
        self.old = (now - timedelta(days=40)).isoformat()

    def tearDown(self):
        for mod, name, value in self.saved:
            setattr(mod, name, value)

    def test_nothing_until_agreed(self):
        self.assertEqual(self.qg.track(7, "en"), {})

    def test_the_path_from_real_rows(self):
        self.qg.save(7, ["divorce lawyer", "family mediation"], 3, "more calls")
        self.db.tables["client_activity"] = [
            {"client_id": 7, "action_type": "media_image_created", "created_at": self.recent},
            {"client_id": 7, "action_type": "media_video_created", "created_at": self.recent},
            {"client_id": 7, "action_type": "media_image_created", "created_at": self.old},       # outside 7 days
            {"client_id": 7, "action_type": "seo_article_generated", "created_at": self.recent}]
        self.db.tables["client_leads"] = [{"client_id": 7, "created_at": self.recent},
                                          {"client_id": 7, "created_at": self.old}]
        self.plan = {"status": "proposed"}
        g = self.qg.track(7, "en")
        self.assertEqual(g["month"], "Month 1 of 3")
        social, organic, improve = g["steps"]
        self.assertIn("3 items a week", social["goal"])
        self.assertEqual(social["now"], "Measured: items prepared in the last 7 days: 2")
        self.assertTrue(social["measured"])
        self.assertEqual(social["next"], self.qg.T["social_next_approve"]["en"])
        self.assertIn("divorce lawyer, family mediation", organic["goal"])
        self.assertFalse(organic["measured"])          # no tracking source: said plainly
        self.assertIn("last 30 days: 1", organic["now"])
        self.assertEqual(organic["next"], self.qg.T["organic_next_site"]["en"])   # no site connected
        self.assertIn("more calls", improve["goal"])
        self.assertEqual(improve["now"], "Measured: enquiries in the last 30 days: 1 (the 30 days before: 1)")

    def test_positions_only_when_measured(self):
        self.qg.save(7, ["a b", "c d"], 2)
        self.qg._positions = lambda cid, words: {"a b": 7.4}
        organic = self.qg.track(7, "he")["steps"][1]
        self.assertTrue(organic["measured"])
        self.assertIn("1", organic["now"])

    def test_words_in_five_languages_and_no_promise(self):
        for table in self.qg.T.values():
            self.assertEqual(set(table), set(LANGS))
            for lang in LANGS:
                self.assertNotRegex(table[lang], r"(?i)guarantee|we promise|מבטיחים|garanti")

    def test_the_page_draws_it(self):
        page = _src("dashboard", "client", "index.html")
        for needle in ('id="goalTrack"', "function renderGoalTrack(", "gt_not_measured", "renderGoalTrack(d ? d.goals"):
            self.assertIn(needle, page)


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
