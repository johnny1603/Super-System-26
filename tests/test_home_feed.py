"""Home as a process (2026-11-16): the onboarding focus shows ONLY the three
tasks, ideas open a few at a time, and the admin can view a client as a
brand-new one.

What must stay true:
- while onboarding is unfinished the page hides the ideas, promotions,
  invitations, branding banner, goal path, stats and journey (CSS on
  body.onboarding-focus); the weekly ideas are stored but not announced;
- the idea feed shows at most 3, the nearest dated event first, then tweaks,
  plans, ideas, homework; one calm line says how many wait (5 languages);
- "view as a brand-new client" is the read-only admin view with mode
  new_client: nothing connected, no characterisation, no goals, no ideas, no
  cards, an empty chat, the real package's steps. Nothing is written
  (no note_connections_complete), and the mode lives only in the signed admin
  view cookie.
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")
os.environ.setdefault("ADMIN_PASSWORD", "test-only")

for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}), ("httpx", {}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from core import home_feed, new_client_view     # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Feed(unittest.TestCase):
    EVENTS = {"hanukkah": {"slug": "hanukkah", "kind": "holiday", "days_until": 12},
              "black_friday": {"slug": "black_friday", "kind": "shopping", "days_until": 30},
              "yom_hazikaron": {"slug": "yom_hazikaron", "kind": "sensitive", "days_until": 40}}

    def item(self, i, kind, slug="", at="2026-11-10"):
        return {"id": i, "kind": kind, "context": {"event_slug": slug}, "created_at": at}

    def test_order_by_importance_and_timing(self):
        items = [self.item(1, "content_idea", at="2026-11-15"), self.item(2, "promotion", "black_friday"),
                 self.item(3, "homework"), self.item(4, "promotion", "hanukkah"),
                 self.item(5, "campaign_tweak"), self.item(6, "promotion", "yom_hazikaron")]
        self.assertEqual([s["id"] for s in home_feed.order(items, self.EVENTS)], [6, 4, 2, 5, 1, 3])

    def test_three_at_a_time(self):
        items = [self.item(i, "content_idea", at=f"2026-11-0{i}") for i in range(1, 6)]
        out = home_feed.release(items, {})
        self.assertEqual(([s["id"] for s in out["active"]], out["waiting"]), ([5, 4, 3], 2))
        self.assertEqual(home_feed.release([], {}), {"active": [], "waiting": 0})

    def test_the_route_and_the_page(self):
        api = _src("core", "api_server.py")
        self.assertIn('released = home_feed.release(pending)', api)
        self.assertIn('"waiting": released["waiting"]', api)
        page = _src("dashboard", "client", "index.html")
        self.assertIn("renderSuggestions(body.data || [], Number(body.waiting) || 0)", page)
        line = next(l for l in page.splitlines() if l.strip().startswith("feed_pace:"))
        for lang in LANGS:
            self.assertRegex(line, rf"\b{lang}:\"[^\"]*\{{n\}}")


class OnlyTheThreeTasks(unittest.TestCase):
    def test_everything_else_waits(self):
        page = _src("dashboard", "client", "index.html")
        rule = page[page.index("body.onboarding-focus #pendingHead"):]
        rule = rule[:rule.index("}")]
        for el in ("#pendingApprovalList", "#brandHero", "#agentInvites", "#goalTrack", "#statGridMain",
                   "#timelineCard", "#journeyPanel", "#feedPace"):
            self.assertIn(f"body.onboarding-focus {el}", rule, el)

    def test_weekly_ideas_not_announced_during_onboarding(self):
        src = _src("agents", "engagement_agent.py")
        fn = src[src.index("def _generate_for_client"):]
        self.assertLess(fn.index("home_focus.onboarding(client_id)"), fn.index("log_communication(client_id"))


class NewClientView(unittest.TestCase):
    REAL = {"resolved": True, "complete": True, "done_count": 2, "total_count": 2, "next_step": None,
            "steps": [{"key": "meta", "platforms": ["meta_ads"], "done": True, "ready": True},
                      {"key": "website", "platforms": ["wordpress"], "done": True, "ready": False}],
            "connected": ["meta", "website"], "missing": [], "not_ready": ["website"], "notices": [{"step": "website"}]}

    def test_day_one_state(self):
        st = new_client_view.status(self.REAL)
        self.assertEqual((st["complete"], st["done_count"], st["next_step"], st["missing"], st["notices"]),
                         (False, 0, "meta", ["meta", "website"], []))
        self.assertTrue(all(not s["done"] for s in st["steps"]))
        self.assertTrue(self.REAL["steps"][0]["done"])                     # the real answer is untouched
        ob = new_client_view.onboarding(self.REAL)
        self.assertEqual((ob["known"], ob["done"], ob["characterised"], ob["goals_agreed"]), (True, False, False, False))
        self.assertEqual(ob["connections"]["total"], 2)
        self.assertEqual(new_client_view.onboarding({"resolved": False}), {"known": False, "done": True})
        dash = new_client_view.dashboard({"connections": [{"platform": "meta_ads"}], "activity": [1]})
        self.assertEqual((dash["connections"], dash["activity"], dash["tour_completed"]), ([], [], False))
        j = new_client_view.journey({"milestones": [{"key": "journey_signup", "done": True, "at": "x"},
                                                    {"key": "journey_website", "done": True, "at": "y"}]})
        self.assertEqual([m["done"] for m in j["milestones"]], [True, False])

    def test_mode_lives_in_the_signed_cookie(self):
        from core import session
        admin = session.create_admin_session_token() if hasattr(session, "create_admin_session_token") else None
        if not admin:
            self.skipTest("no admin token helper")
        token = session.create_admin_view_token(27, admin, "new_client")
        self.assertEqual(session.verify_admin_view_token(token, admin), 27)
        self.assertEqual(session.admin_view_mode(token, admin), "new_client")
        self.assertEqual(session.admin_view_mode(session.create_admin_view_token(27, admin), admin), "")
        self.assertEqual(session.admin_view_mode(token + "x", admin), "")

    def test_wired_read_only(self):
        api = _src("core", "api_server.py")
        start = api[api.index("def admin_view_as_start"):api.index("def admin_view_as_exit")]
        self.assertIn("mode = new_client_view.MODE if mode == new_client_view.MODE else \"\"", start)
        self.assertIn("create_admin_view_token(client_id, request.cookies.get(\"admin_session\"), mode)", start)
        for endpoint in ("def dashboard_data", "def client_chat_history", "def client_interview_state",
                         "def client_connection_checklist", "def client_journey_view", "def client_home_focus",
                         "async def client_suggestions", "def client_open_actions"):
            body = api[api.index(endpoint):]
            body = body[:body.index("\n@app.")]
            self.assertIn("_simulating_new(request)", body, endpoint)
        checklist = api[api.index("def client_connection_checklist"):]
        self.assertLess(checklist.index("_simulating_new(request)"), checklist.index("note_connections_complete"))
        admin = _src("dashboard", "admin", "index.html")
        self.assertIn("enterAsClient(${Number(c.id)}, 'new_client')", admin)
        self.assertIn("?mode=new_client", admin)


if __name__ == "__main__":
    unittest.main()
