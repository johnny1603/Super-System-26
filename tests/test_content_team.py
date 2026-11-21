"""מאיה and ליאור as one team (core/content_team, core/content_team_actions) — 2026-11-15.

What must stay true:
- the week is ONE plan approved as a WHOLE in ONE card; the Saturday run opens
  that card and falls back to per-item rows only when it cannot;
- approval starts production of every item through the existing media_agent
  functions; one failed item never stops the others;
- NOTHING is auto-published: every channel is off, there is no publish call in
  the module, and every item says how it gets published;
- the plan is built on the shared brand kit and media library;
- the agents never send the client to another agent, and inspiration is
  research only; 5 languages on every client-facing text.
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

for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}), ("httpx", {"Response": object, "Client": object, "HTTPError": Exception, "HTTPStatusError": Exception, "TimeoutException": Exception, "RequestError": Exception}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:                 # the container has them; this machine's test python does not
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from agents import media_agent              # noqa: E402
from core import agent_actions as aa        # noqa: E402
from core import content_team as ct         # noqa: E402
from test_landing_own import FakeDB          # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
ITEMS = [
    {"title": "מאחורי הקלעים של הקלייה", "body": "סרטון קצר מהבוקר. נכין ערכת צילום.", "format": "self_filmed",
     "platform": "tiktok", "day": 4, "caption": "ככה מתחיל הבוקר אצלנו #קפה"},
    {"title": "מבצע בוקר", "body": "תמונה של מאפה וקפה. נכין תמונה.", "format": "image", "platform": "instagram",
     "day": 1, "caption": "בוקר טוב מקפה נועה"},
    {"title": "באנר לאתר", "body": "תמונה רחבה לעמוד הבית.", "format": "image", "platform": "website", "day": "x"},
    {"title": "", "body": "no title"},
    {"title": "רביעי", "body": "מעבר למכסה", "format": "video", "platform": "facebook", "day": 2},
]


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Tasks:
    def __init__(self):
        self.added = []

    def add_task(self, fn, *args):
        self.added.append((fn, args))


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.alerts, self.made, self.proposed = [], [], []
        self.connected = {"instagram", "tiktok"}
        self.fail_titles = set()
        self.saved = [(ct, n, getattr(ct, n)) for n in ("_db", "_alert", "_connected_channels", "_produce_item")]
        self.saved += [(aa, n, getattr(aa, n)) for n in ("_db", "_client", "_alert", "propose")]
        ct._db = lambda: self.db
        ct._alert = self.alerts.append
        ct._connected_channels = lambda cid: set(self.connected)
        ct._block_cache.clear()
        aa._db = lambda: self.db
        aa._client = lambda cid: {"id": cid, "language": "he", "billing_exempt": False}
        aa._alert = self.alerts.append

        def fake_produce(client_id, item):
            self.made.append(item["title"])
            if item["title"] in self.fail_titles:
                raise RuntimeError("generation down")
            return {"success": True, "file_id": "f-" + str(len(self.made))}
        ct._produce_item = fake_produce
        self.card = {"id": 77}

        def fake_propose(client_id, origin, persona, raw, **kwargs):
            self.proposed.append((client_id, origin, persona, raw, kwargs))
            return {"card": self.card} if self.card else {"error": "refused"}
        aa.propose = fake_propose

    def tearDown(self):
        for module, name, value in self.saved:
            setattr(module, name, value)

    def spec(self, name):
        return aa.ACTIONS[name]


class Plan(Base):
    def test_items_are_cleaned_capped_and_ordered(self):
        items = ct.clean_items(ITEMS)
        self.assertEqual(len(items), ct.MAX_ITEMS)
        self.assertEqual([i["day"] for i in items], sorted(i["day"] for i in items))
        self.assertTrue(all(0 <= i["day"] <= 6 and i["status"] == "proposed" for i in items))
        self.assertNotIn("רביעי", [i["title"] for i in items])
        self.assertEqual(ct.clean_items("garbage"), [])
        self.assertEqual(ct.clean_items([{"title": "a", "body": "b", "format": "hologram", "platform": "myspace"}])[0]
                         ["format"], "image")

    def test_one_plan_one_card(self):
        out = ct.propose(27, ITEMS, "he", persona="meta")
        self.assertEqual(len(self.proposed), 1)                       # ONE card for the whole week
        client_id, origin, persona, raw, kwargs = self.proposed[0]
        self.assertEqual((persona, raw["name"], kwargs.get("by_code")), ("meta", "approve_content_plan", True))
        self.assertEqual(raw["params"], {"plan": out["plan"]["id"]})
        self.assertEqual(ct.open_plan(27)["card_id"], 77)
        self.assertEqual(ct.propose(27, [], "he")["card"], None)       # nothing to plan = no card

    def test_approval_is_all_or_nothing_and_once(self):
        plan = ct.propose(27, ITEMS, "he", persona="media")["plan"]
        with self.assertRaises(ct.PlanError) as caught:
            ct.approve(27, "other")
        self.assertEqual(caught.exception.code, "plan_missing")
        value = ct.approve(27, plan["id"])
        self.assertTrue(all(i["status"] == "in_production" for i in value["items"]))
        self.assertEqual(ct.open_plan(27), {})
        with self.assertRaises(ct.PlanError) as caught:
            ct.approve(27, plan["id"])
        self.assertEqual(caught.exception.code, "already_decided")

    def test_production_runs_every_item_and_survives_a_failure(self):
        plan = ct.propose(27, ITEMS, "he", persona="media")["plan"]
        ct.produce(27, plan["id"])                                    # not approved yet → nothing is made
        self.assertEqual(self.made, [])
        ct.approve(27, plan["id"])
        self.fail_titles = {"מבצע בוקר"}
        ct.produce(27, plan["id"])
        self.assertEqual(len(self.made), 3)
        statuses = {i["title"]: i["status"] for i in ct.latest(27)["items"]}
        self.assertEqual(statuses["מבצע בוקר"], "needs_attention")
        self.assertEqual(statuses["מאחורי הקלעים של הקלייה"], "kit_sent")
        self.assertEqual(statuses["באנר לאתר"], "ready")
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("client 27", self.alerts[0])
        ct.produce(27, plan["id"])                                    # a second run makes nothing twice
        self.assertEqual(len(self.made), 3)

    def test_production_uses_the_existing_media_functions(self):
        ct._produce_item = self.saved[3][2]
        calls = []
        saved = [(media_agent, n, getattr(media_agent, n)) for n in ("generate_image", "generate_video", "create_filming_kit")]
        try:
            media_agent.generate_image = lambda cid, brief, platform="instagram": calls.append(("image", platform)) or {"success": True}
            media_agent.generate_video = lambda cid, brief, platform="instagram": calls.append(("video", platform)) or {"success": True}
            media_agent.create_filming_kit = lambda cid, topic: calls.append(("kit", topic)) or {"success": True}
            for fmt, platform in (("image", "website"), ("image", "youtube"), ("video", "tiktok"), ("self_filmed", "tiktok")):
                ct._produce_item(27, {"title": "t", "body": "b", "format": fmt, "platform": platform})
        finally:
            for module, name, value in saved:
                setattr(module, name, value)
        self.assertEqual(calls, [("image", "website"), ("image", "instagram"), ("video", "tiktok"), ("kit", "t")])


class Publishing(Base):
    def test_nothing_is_auto_published_today(self):
        self.assertEqual(set(ct.AUTO_PUBLISH.values()), {False})
        modes = ct.channel_modes(27)
        self.assertEqual(modes, {"website": "library", "instagram": "client_publishes", "tiktok": "client_publishes",
                                 "facebook": "not_connected", "youtube": "not_connected"})
        self.assertNotIn("auto", modes.values())

    def test_no_publish_path_in_the_module(self):
        source = _src("core", "content_team.py") + _src("core", "content_team_actions.py")
        for call in ("meta_content_agent", "tiktok_content_agent", "youtube_content_agent", ".publish(",
                     "prepare_for_publishing", "send_to_inbox", "init_direct_post"):
            self.assertNotIn(call, source, call)

    def test_unreadable_connections_claim_nothing(self):
        ct._connected_channels = lambda cid: (_ for _ in ()).throw(RuntimeError("db"))
        self.assertEqual(set(ct.channel_modes(27).values()), {"library", "not_connected"})

    def test_every_item_shows_how_it_gets_published(self):
        plan = ct.propose(27, ITEMS, "he", persona="meta")["plan"]
        for lang in LANGS:
            text = ct.lines(plan, lang, ct.channel_modes(27))
            self.assertEqual(text.count("•"), 3)
            self.assertIn(ct.MODE_TEXT["client_publishes"][lang], text)
            self.assertIn(ct.MODE_TEXT["library"][lang], text)
        view = ct.view(27, "en")
        self.assertTrue(view["has_plan"])
        self.assertEqual({i["mode"] for i in view["items"]}, {"client_publishes", "library"})
        self.assertEqual(view["note"], ct.TEXT["why_manual"]["en"])
        self.assertEqual((view["persona"], view["card_id"]), ("meta", 77))

    def test_prompt_block(self):
        block = ct.prompt_block(27)
        # the Social tab's counts ride along (core/social_queue, tests/test_social_queue.py)
        self.assertEqual(block.pop("social_queue")["waiting"], 0)
        self.assertEqual(block, {"has_plan": False, "auto_publishing_available_anywhere": False,
                                               "publishing_by_channel": {"instagram": "client_publishes",
                                                                         "tiktok": "client_publishes",
                                                                         "facebook": "not_connected",
                                                                         "youtube": "not_connected"}})
        ct.propose(27, ITEMS, "he", persona="meta")
        block = ct.prompt_block(27)                                   # the save dropped the cached block
        self.assertEqual((block["status"], len(block["items"])), ("proposed", 3))

    def test_five_languages(self):
        tables = [ct.CHANNEL_TEXT, ct.FORMAT_TEXT, ct.MODE_TEXT, ct.TEXT, ct.STATUS_TEXT]
        for table in tables:
            for key, texts in table.items():
                self.assertEqual(set(texts), set(LANGS), key)
                self.assertTrue(all(texts.values()), key)
        self.assertEqual(set(ct.DAYS), set(LANGS))
        self.assertTrue(all(len(days) == 7 for days in ct.DAYS.values()))


class Cards(Base):
    def test_registered_in_five_languages(self):
        for name in ("approve_content_plan", "build_content_plan"):
            spec = self.spec(name)
            self.assertEqual((spec["approver"], spec["internal_only"]), ("client", False))
            for key in ("title", "what", "done"):
                self.assertEqual(set(spec[key]), set(LANGS), (name, key))
            for code, texts in spec["refusals"].items():
                self.assertEqual(set(texts), set(LANGS), (name, code))
        approve = self.spec("approve_content_plan")
        self.assertTrue(approve["code_only"])
        self.assertFalse(approve["client_requestable"])
        for lang in LANGS:
            for placeholder in ("{lines}", "{team}", "{why}"):
                self.assertIn(placeholder, approve["what"][lang], lang)

    def test_the_card_shows_the_whole_plan(self):
        plan = ct.propose(27, ITEMS, "he", persona="meta")["plan"]
        shown = self.spec("approve_content_plan")["display"](27, {"plan": plan["id"]})
        self.assertEqual(shown["lines"].count("•"), 3)
        self.assertEqual(shown["why"], ct.TEXT["why_manual"]["he"])
        self.assertEqual(self.spec("approve_content_plan")["display"](27, {"plan": "gone"})["lines"], "")

    def test_only_the_open_plan_can_be_approved(self):
        validate = self.spec("approve_content_plan")["params"]["plan"]["validate"]
        with self.assertRaises(ValueError):
            validate("20260101000000", 27)
        plan = ct.propose(27, ITEMS, "he", persona="meta")["plan"]
        self.assertEqual(validate(plan["id"], 27), plan["id"])

    def test_one_click_approves_all_and_production_is_in_the_background(self):
        plan = ct.propose(27, ITEMS, "he", persona="meta")["plan"]
        tasks = Tasks()
        result = self.spec("approve_content_plan")["run"](27, {"plan": plan["id"]}, tasks)
        self.assertEqual(result, {"count": 3})
        self.assertEqual(self.made, [])                                # nothing produced inside the click
        self.assertEqual(tasks.added, [(ct.produce, (27, plan["id"]))])
        with self.assertRaises(aa.ActionFailed) as caught:
            self.spec("approve_content_plan")["run"](27, {"plan": plan["id"]}, tasks)
        self.assertEqual(caught.exception.code, "already_decided")
        for lang in LANGS:
            self.assertIn("3", self.spec("approve_content_plan")["done_message"](result, lang))

    def test_asking_for_a_plan_gives_the_one_card(self):
        saved = media_agent.plan_week
        asked = []
        try:
            media_agent.plan_week = lambda client, events, request="": asked.append(request) or ITEMS
            import core.israel_calendar as calendar
            original = calendar.upcoming_events
            calendar.upcoming_events = lambda *a, **k: []
            try:
                prepare = self.spec("build_content_plan")["prepare"]
                out = prepare(27, {"request": "יותר וידאו"}, {"language": "he"}, [])
                self.assertEqual(out["switch_to"], "approve_content_plan")
                self.assertEqual(out["switch_params"], {"plan": ct.open_plan(27)["id"]})
                again = prepare(27, {}, {"language": "he"}, [])          # a waiting plan is shown, not rewritten
                self.assertEqual(again["switch_params"], out["switch_params"])
                self.assertEqual(asked, ["יותר וידאו"])
                prepare(27, {"replace": True, "request": "בלי טיקטוק"}, {"language": "he"}, [])
                self.assertEqual(asked, ["יותר וידאו", "בלי טיקטוק"])
                media_agent.plan_week = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model down"))
                failed = prepare(27, {"replace": True}, {"language": "en"}, [])
                self.assertEqual(set(failed), {"refuse"})
                self.assertEqual(len(self.alerts), 1)
            finally:
                calendar.upcoming_events = original
        finally:
            media_agent.plan_week = saved


class SaturdayRun(Base):
    def setUp(self):
        super().setUp()
        self.messages, self.activity, self.examples = [], [], []
        self.saved += [(media_agent, n, getattr(media_agent, n))
                       for n in ("_db", "plan_week", "send_trend_examples", "_recent_media_plan_exists")]
        media_agent._db = lambda: self.db
        media_agent.plan_week = lambda client, events, request="": list(ITEMS[:3])
        media_agent.send_trend_examples = lambda cid: self.examples.append(cid)
        media_agent._recent_media_plan_exists = lambda cid: False
        import agents.client_agent as client_agent
        self.saved += [(client_agent, n, getattr(client_agent, n)) for n in ("log_activity", "log_communication")]
        client_agent.log_activity = lambda *a, **k: self.activity.append(a)
        client_agent.log_communication = lambda cid, direction, channel, text: self.messages.append((channel, text))
        self.saved += [(ct, "plan_persona", ct.plan_persona)]
        ct.plan_persona = lambda cid: "meta"

    def test_one_card_and_no_per_item_rows(self):
        count = media_agent._checkin_for_client({"id": 27, "language": "en"}, [])
        self.assertEqual(count, 3)
        self.assertEqual(len(self.proposed), 1)
        self.assertEqual(self.proposed[0][2], "meta")
        self.assertEqual(self.db.rows("client_suggestions") if hasattr(self.db, "rows") else [], [])
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0][1], ct.TEXT["nudge"]["en"])
        self.assertEqual(self.examples, [27])
        self.assertEqual(media_agent._checkin_for_client({"id": 27, "language": "en"}, []), 0)   # a plan is open
        self.assertEqual(len(self.proposed), 1)

    def test_falls_back_to_per_item_rows_when_the_card_cannot_open(self):
        self.card = None
        inserted = []
        real_db = self.db

        class Table:
            def insert(self, row):
                inserted.append(row)
                return self

            def execute(self):
                return types.SimpleNamespace(data=[])

        class Fallback:
            def table(self, name):
                return Table() if name == "client_suggestions" else real_db.table(name)
        media_agent._db = lambda: Fallback()
        count = media_agent._checkin_for_client({"id": 27, "language": "he"}, [])
        self.assertEqual(count, 3)
        self.assertEqual([r["kind"] for r in inserted], ["media_plan"] * 3)


class Wiring(unittest.TestCase):
    def test_planning_prompt(self):
        prompt = media_agent.WEEKLY_PLAN_SYSTEM
        for needle in ("ONE TEAM, ONE PLAN", "brand_kit", "media_library_recent", "publishing_by_channel",
                       "INSPIRATION IS RESEARCH ONLY", "never copy a competitor", '"day"', '"caption"', '"language"'):
            self.assertIn(needle, prompt, needle)
        self.assertNotIn("Hebrew only", prompt)

    def test_agent_rules(self):
        source = _src("agents", "support_agent.py")
        for needle in ("ONE TEAM", "NEVER tell the client to go talk to another agent", '"build_content_plan"',
                       "INSPIRATION IS RESEARCH ONLY", "NEVER copy a", "publishing_by_channel",
                       'payload["content_plan"]'):
            self.assertIn(needle, source, needle)
        from agents import support_agent
        self.assertNotIn("{", support_agent.STUDIO_RULE)       # appended to a .format()-ed template
        self.assertNotIn("}", support_agent.STUDIO_RULE)

    def test_route_parity_admin_view_and_deletion(self):
        api = _src("core", "api_server.py")
        self.assertIn('"/api/client/content-plan"', api)
        self.assertLess(api.index('"/api/client/content-plan"'), api.rindex("app.mount("))
        from core import admin_view, chat_parity
        self.assertIn("client_content_plan", admin_view.READ_ENDPOINTS)
        self.assertIn("GET /api/client/content-plan", chat_parity.PARITY)
        doc = _src("docs", "CHAT_PARITY.md")
        for name in ("build_content_plan", "approve_content_plan"):
            self.assertIn(f"`{name}`", doc)
        self.assertIn("content_plan:", _src("core", "client_deletion.py"))
        self.assertIn('"content_plan:"', _src("core", "deletion_guard.py"))
        self.assertIn('"content_plan:"', _src("core", "admin_service.py"))

    def test_dashboard_box(self):
        page = _src("dashboard", "client", "index.html")
        for needle in ('id="planBox"', "function loadContentPlan", "function planAsk", "/api/client/content-plan",
                       "loadContentPlan();"):
            self.assertIn(needle, page, needle)
        body = page[page.index("async function loadContentPlan"):page.index("function planAsk")]
        self.assertNotIn("innerHTML", body)                    # server text goes in as text
        for key in re.findall(r"\b(plan_\w+)\s*:\s*\{ he:", page):
            line = page[page.index(key + ": {"):].split("\n")[0]
            for lang in LANGS:
                self.assertRegex(line, rf"\b{lang}\s*:", (key, lang))


if __name__ == "__main__":
    unittest.main()
