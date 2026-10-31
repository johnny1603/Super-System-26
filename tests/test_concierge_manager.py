"""The concierge behaves like a manager, not a switchboard (2026-10-31).

- A genuine handoff is REAL: agent_actions.open_team_request stores a tracked
  request (awaiting staff) that is an item in the Control Room, a card the
  client sees with its status, one fixed sentence on what happens and when,
  and a posted line when staff mark it handled / close it. Opened by code,
  never proposed by a model; one open request per client + topic.
- "No card could be shown" is a tracked request too, never a bare promise.
- Photos → presenter: create_presenter_from_photos (avatar_agent's photo path).
  In the package → the card; tool missing → the ONE card that fixes it, named;
  likeness consent missing → that card again (it carries the consent), never
  bypassed; not in the package → the upgrade line. Several uploads → one card.
- Capability visibility: what the package doesn't include is listed
  (client_state.package.not_in_package, connection_status.not_in_plan) and the
  dashboard cards say so with a way to ask, instead of silence.
- The prompts: no "open another chat", handoff only for licensed-professional
  or commercial decisions, the stale "TikTok coming soon" line is gone.

Stdlib only. Run from the repo root:  python -I -B tests/test_concierge_manager.py -v
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

VENDOR = re.compile(r"heygen|eleven\s?labs|higgsfield", re.IGNORECASE)


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


HANDOFF_ALERTS = []
AVATAR = {"consent": True, "calls": [], "generated": set(),
          "result": {"success": True, "method": "character_image", "name": "x"}}


def _create_avatar(client_id, avatar_name="", photo_ids=None):
    AVATAR["calls"].append((client_id, list(photo_ids or [])))
    return AVATAR["result"]


STUBS = {
    "httpx": _module("httpx"),
    "anthropic": _module("anthropic", Anthropic=lambda *a, **k: None),
    "supabase": _module("supabase", create_client=lambda *a, **k: None),
    "agents.client_agent": _module(
        "agents.client_agent",
        get_client=lambda cid: {"id": cid, "language": "he", "billing_exempt": False},
        get_activity=lambda *a, **k: [], get_communications=lambda *a, **k: [],
        log_activity=lambda *a, **k: None, _db=lambda: None, get_accounts=lambda cid: [],
        log_communication=lambda *a, **k: None),
    "agents.onboarding_agent": _module("agents.onboarding_agent", LANGUAGE_RULE="\n\nLANGUAGE.", PRICING={}),
    "core.automation_service": _module("core.automation_service", VENDORS={},
                                       POLICY_NOTE="Automation {is} quoted by hand.", SIGNUP_RULE_HE=""),
    "core.agent_base": _module("core.agent_base", agent_alert=lambda *a, **k: None,
                               log_step=lambda *a, **k: None, timed_step=lambda a, s, fn: fn()),
    "agents.avatar_agent": _module("agents.avatar_agent", has_consent=lambda cid, scope: AVATAR["consent"],
                                   create_avatar=_create_avatar, list_ready_avatars=lambda cid: [],
                                   generated_image_ids=lambda cid: set(AVATAR["generated"]),
                                   current_voice_id=lambda cid: ""),
}
_SAVED = {name: sys.modules.get(name) for name in list(STUBS) + ["agents.support_agent"]}
sys.modules.update(STUBS)
sys.modules.pop("agents.support_agent", None)

from agents import support_agent as sa        # noqa: E402
from core import agent_actions as aa          # noqa: E402
from core import chat_fallback                # noqa: E402
from core import client_journey as cj         # noqa: E402
from core import client_state as cs           # noqa: E402
from test_agent_actions import FakeDB          # noqa: E402

CLIENT = {"id": 40, "billing_exempt": False, "language": "he"}


def tearDownModule():
    for name, module in _SAVED.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.posted, self.alerts = [], []
        self.saved = {n: getattr(aa, n) for n in ("_db", "_client", "_post_client_message", "_alert", "_log",
                                                  "_media_file", "_presenter_tool_status")}
        self.saved_cs = (cs.cached_package, cs._cache.copy())
        aa._db = lambda: self.db
        aa._client = lambda cid: dict(CLIENT, id=cid)
        aa._post_client_message = lambda cid, persona, text: self.posted.append((cid, persona, text))
        aa._alert = self.alerts.append
        aa._log = lambda *a, **k: None
        aa._media_file = lambda cid, value: (str(value), {"id": str(value), "mimeType": "image/jpeg",
                                                         "name": f"{value}.jpg"})
        aa._presenter_tool_status = lambda cid: "connected"
        cs.cached_package = lambda cid: {"readable": True, "services": ["avatar"], "not_in_package": []}
        AVATAR.update(consent=True, calls=[], generated=set(),
                      result={"success": True, "method": "character_image", "name": "x"})

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(aa, name, fn)
        cs.cached_package = self.saved_cs[0]
        cs._cache.clear()
        cs._cache.update(self.saved_cs[1])

    def rows(self, action=None):
        return [r for r in self.db.tables.get(aa.TABLE, []) if not action or r.get("action") == action]


# ─── A real handoff: tracked, visible, closed by staff ───────────────────────

class TeamRequest(Base):
    def test_opens_a_tracked_item_the_team_sees(self):
        out = aa.open_team_request(40, "general", "refund", "אני רוצה החזר על החודש")
        row = self.rows("team_request")[0]
        self.assertEqual((row["status"], row["approver"], row["origin"]), ("awaiting_staff", "admin", "client_chat"))
        self.assertEqual(row["params"], {"topic": "refund", "request": "אני רוצה החזר על החודש"})
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("client 40", self.alerts[0])           # the Control Room matching convention
        card = out["card"]
        self.assertFalse(card["can_decide"])                 # nothing for the client to click
        self.assertIn("החזר", card["what"])
        self.assertIn("יום עסקים", card["what"])              # roughly when
        self.assertFalse(out["already"])

    def test_one_open_request_per_topic(self):
        aa.open_team_request(40, "general", "refund", "החזר בבקשה")
        again = aa.open_team_request(40, "general", "refund", "שוב: החזר")
        self.assertTrue(again["already"])
        self.assertEqual(len(self.rows("team_request")), 1)
        aa.open_team_request(40, "general", "legal", "שאלה משפטית")
        self.assertEqual(len(self.rows("team_request")), 2)

    def test_unknown_topic_is_other_and_secrets_are_redacted(self):
        aa.open_team_request(40, "general", "pizza", "my key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123")
        params = self.rows("team_request")[0]["params"]
        self.assertEqual(params["topic"], "other")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123", params["request"])

    def test_storage_failure_is_reported_not_faked(self):
        self.db.fail = True
        self.assertEqual(aa.open_team_request(40, "general", "refund", "החזר"), {"error": "storage_unavailable"})

    def test_never_proposed_by_a_model_and_never_listed(self):
        for origin in ("client_chat", "admin_chat"):
            self.assertEqual(aa.propose(40, origin, "general", {"name": "team_request",
                                                                "params": {"topic": "refund", "request": "x y"}},
                                        state={})["error"], "not_allowed_here")
            self.assertNotIn("team_request", [a["name"] for a in aa.available(40, origin, {}, client=CLIENT)])

    def test_the_client_sees_it_until_handled_and_it_outlives_a_normal_card(self):
        aa.open_team_request(40, "media", "manual_task", "תעלו את הסרטון לאינסטגרם")
        row = self.rows("team_request")[0]
        row["created_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.assertEqual([c["action"] for c in aa.open_cards(40, "client", persona="media")], ["team_request"])
        self.assertEqual(aa.open_cards(40, "client", persona="google"), [])     # in the chat it came from
        row["created_at"] = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        self.assertEqual(aa.open_cards(40, "client", persona="media"), [])

    def test_staff_mark_it_handled_and_the_client_is_told(self):
        rid = aa.open_team_request(40, "general", "billing", "חויבתי פעמיים")["card"]["id"]
        self.assertEqual(aa.decide(rid, "client", 40, "approve")["code"], "not_your_decision")
        out = aa.decide(rid, "admin", 40, "approve")
        self.assertEqual(out["status"], "done")
        self.assertIn("טיפל", self.posted[-1][2])
        self.assertEqual(aa.team_requests_view(40)[0]["status"], "handled")

    def test_staff_close_it_with_its_own_line(self):
        rid = aa.open_team_request(40, "general", "billing", "חויבתי פעמיים")["card"]["id"]
        aa.decide(rid, "admin", 40, "cancel")
        self.assertIn("סגר את הבקשה", self.posted[-1][2])
        self.assertNotIn("לא בוצע שום שינוי", self.posted[-1][2])
        self.assertEqual(aa.team_requests_view(40)[0]["status"], "closed")

    def test_open_requests_are_listed_for_the_agents(self):
        aa.open_team_request(40, "general", "legal", "שאלה על חוזה")
        view = aa.team_requests_view(40)
        self.assertEqual((view[0]["topic"], view[0]["status"]), ("legal", "open"))

    def test_control_room_shows_it_as_its_own_item(self):
        src = _src("core", "control_room.py")
        self.assertIn('"kind": "team_request"', src)
        admin = _src("dashboard", "admin", "index.html")
        self.assertIn("team_request: 'בקשה לצוות'", admin)
        self.assertIn("טופל — לעדכן את הלקוח", admin)


class ChatHandoffs(Base):
    def setUp(self):
        super().setUp()
        self.saved_alert = chat_fallback.alert_handoff
        chat_fallback.alert_handoff = lambda *a, **k: HANDOFF_ALERTS.append((a, k))
        HANDOFF_ALERTS.clear()

    def tearDown(self):
        chat_fallback.alert_handoff = self.saved_alert
        super().tearDown()

    def test_a_handoff_is_a_card_and_one_sentence(self):
        handoff = sa._handoff_alert(40, "general", "צריך עורך דין לחוזה", out_of_scope=True, topic="legal")
        out = sa._with_handoff("זה צריך מישהו מהצוות.", handoff)
        self.assertEqual(out["action"]["action"], "team_request")
        self.assertIn(aa._pick(aa.TEAM_REQUEST_OPENED, "he"), out["reply"])
        self.assertTrue(out["reply"].startswith("זה צריך מישהו מהצוות."))
        self.assertEqual(HANDOFF_ALERTS, [])                 # the request IS the alert now

    def test_no_storage_falls_back_to_the_alert_and_promises_no_card(self):
        self.db.fail = True
        handoff = sa._handoff_alert(40, "general", "צריך עורך דין", out_of_scope=True, topic="legal")
        self.assertEqual(handoff, {"line": "", "card": None})
        self.assertEqual(len(HANDOFF_ALERTS), 1)
        self.assertNotIn("action", sa._with_handoff("טקסט", handoff))

    def test_topic_defaults(self):
        self.assertEqual(sa._handoff_topic({}, True), "other")
        self.assertEqual(sa._handoff_topic({}, False), "manual_task")
        self.assertEqual(sa._handoff_topic({"handoff_topic": "Refund"}, True), "refund")

    def test_no_card_is_a_tracked_request_not_a_bare_promise(self):
        out = sa._route_proposal(40, "general", "תעשה לי משהו", {"proposed_action": {"name": "made_up"}},
                                 "מכין כרטיס", state={}, client=CLIENT)
        self.assertEqual(out["action"]["action"], "team_request")
        self.assertEqual(self.rows("team_request")[0]["params"]["topic"], "action_not_offered")
        self.assertNotIn(aa._pick(aa.NO_CARD, "he"), out["reply"])


# ─── Photos → presenter ──────────────────────────────────────────────────────

def _state(services=("avatar",), readable=True, images=1):
    return {"package": {"readable": readable, "services": list(services)},
            "media_folder": {"counts": {"image": images},
                             "images": [{"drive_file_id": f"img{i}"} for i in range(images)]}}


INTERNAL = dict(CLIENT, billing_exempt=True)


class PresenterFromPhotos(Base):
    """Internal test clients only until the presenter ran live
    (core/avatar_video.open_to_all) — so these run as an internal client."""

    def setUp(self):
        super().setUp()
        aa._client = lambda cid: dict(INTERNAL, id=cid)

    def propose(self, photos, state=None, client=None):
        return aa.propose(40, "client_chat", "media", {"name": "create_presenter_from_photos",
                                                       "params": {"photo_ids": photos}},
                          state=state or _state(), client=client or INTERNAL, context={"language": "he"})

    def test_a_paying_client_is_not_offered_it_until_it_ran_live(self):
        self.assertEqual(self.propose(["imgA"], client=CLIENT), {"error": "internal_only"})
        names = [a["name"] for a in aa.available(40, "client_chat", _state(), client=CLIENT)]
        self.assertNotIn("create_presenter_from_photos", names)

    def test_offered_only_when_the_package_has_it_and_there_are_photos(self):
        possible = aa.ACTIONS["create_presenter_from_photos"]["available"]
        self.assertTrue(possible(_state(), CLIENT))
        self.assertFalse(possible(_state(services=("google ads",)), CLIENT))
        self.assertFalse(possible(_state(readable=False), CLIENT))
        self.assertFalse(possible(_state(images=0), CLIENT))
        names = [a["name"] for a in aa.available(40, "client_chat", _state(), client=INTERNAL)]
        self.assertIn("create_presenter_from_photos", names)

    def test_everything_in_place_gives_the_card(self):
        out = self.propose(["imgA", "imgB"])
        self.assertEqual(out["card"]["action"], "create_presenter_from_photos")
        self.assertTrue(out["card"]["can_decide"])
        self.assertIn("התמונה שבחרתם", out["card"]["what"])
        self.assertIn("עד 15 שניות", out["card"]["what"])          # length said before the click
        self.assertIsNone(VENDOR.search(out["card"]["what"]))

    def test_missing_tool_names_it_and_offers_only_its_card(self):
        aa._presenter_tool_status = lambda cid: "not_connected"
        out = self.propose(["imgA"])
        self.assertEqual(out["card"]["action"], "connect_creative_tool")
        self.assertEqual(self.rows("connect_creative_tool")[0]["params"], {"tool": "higgsfield"})
        label = aa._creative_label("higgsfield", "he")
        self.assertIn(label, out["notice"])
        self.assertIsNone(VENDOR.search(out["notice"]))
        self.assertEqual(self.rows("create_presenter_from_photos"), [])

    def test_missing_consent_is_never_bypassed(self):
        AVATAR["consent"] = False
        out = self.propose(["imgA"])
        self.assertEqual(out["card"]["action"], "connect_creative_tool")    # the card that carries the consent
        self.assertIn("אישור", out["notice"])
        self.assertEqual(self.rows("connect_creative_tool")[0]["params"], {"tool": "higgsfield"})
        self.assertEqual(self.rows("create_presenter_from_photos"), [])

    def test_an_ai_image_we_made_needs_no_likeness_consent(self):
        AVATAR.update(consent=False, generated={"imgA"})
        out = self.propose(["imgA"])
        self.assertEqual(out["card"]["action"], "create_presenter_from_photos")

    def test_not_in_the_package_offers_the_upgrade(self):
        cs.cached_package = lambda cid: {"readable": True, "services": ["google ads"], "not_in_package": ["avatar"]}
        out = self.propose(["imgA"])          # the turn's state still says "in package" — prepare re-reads
        self.assertIn("לא כלולה בחבילה", out["refuse"])
        self.assertEqual(self.rows(), [])

    def test_photos_uploaded_one_by_one_grow_one_card(self):
        first = self.propose(["imgA"])
        second = self.propose(["imgB"])
        rows = self.rows("create_presenter_from_photos")
        self.assertEqual(rows[0]["status"], "cancelled")                 # superseded
        self.assertEqual(sorted(rows[1]["params"]["photo_ids"]), ["imgA", "imgB"])
        self.assertNotEqual(first["card"]["id"], second["card"]["id"])
        again = self.propose(["imgA"])                                    # nothing new: the same card
        self.assertEqual(again["card"]["id"], second["card"]["id"])

    def test_the_click_runs_the_photo_path_with_exactly_those_photos(self):
        rid = self.propose(["imgA", "imgB"])["card"]["id"]
        out = aa.decide(rid, "client", 40, "approve")
        self.assertEqual(out["status"], "done")
        self.assertEqual(AVATAR["calls"], [(40, ["imgA", "imgB"])])

    def test_consent_withdrawn_before_the_click_refuses_plainly(self):
        rid = self.propose(["imgA"])["card"]["id"]
        AVATAR["result"] = {"success": False, "code": "no_consent", "errors": ["no consent"]}
        aa.decide(rid, "client", 40, "approve")
        row = self.rows("create_presenter_from_photos")[0]
        self.assertEqual((row["status"], row["outcome"]), ("failed", "no_consent"))
        self.assertIn("אישור", self.posted[-1][2])

    def test_avatar_agent_checks_consent_before_copying_any_photo(self):
        src = _src("agents", "avatar_agent.py")
        body = src.split("def create_avatar(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(body.index('has_consent(client_id, "likeness")'), body.index("drive.download_file("))
        self.assertLess(body.index('has_consent(client_id, "likeness")'), body.index("drive.upload_bytes("))
        self.assertIn('"code": "no_consent"', body)

    def test_photo_validator_proves_every_file(self):
        aa._media_file = lambda cid, value: (str(value), {"mimeType": "video/mp4"})
        with self.assertRaises(ValueError):
            aa._v_media_images(["vid1"], 40)
        with self.assertRaises(ValueError):
            aa._v_media_images([], 40)
        with self.assertRaises(ValueError):
            aa._v_media_images([f"i{n}" for n in range(aa.PRESENTER_PHOTOS_MAX + 1)], 40)


# ─── Capability visibility ───────────────────────────────────────────────────

class _AccountsDB:
    def __init__(self, platforms):
        self.platforms = platforms

    def table(self, name):
        rows = [{"platform": p, "status": "active"} for p in self.platforms]
        q = types.SimpleNamespace()
        q.select = q.eq = lambda *a, **k: q
        q.execute = lambda: types.SimpleNamespace(data=rows)
        return q


class CapabilityVisibility(unittest.TestCase):
    def test_the_one_rule(self):
        self.assertTrue(cj.step_in_services("tiktok", ["tiktok organic"]))
        self.assertFalse(cj.step_in_services("tiktok", ["google ads"]))
        self.assertIn("tiktok", cj.capabilities_outside(["google ads", "meta ads"]))
        self.assertNotIn("google_ads", cj.capabilities_outside(["google ads"]))

    def test_checklist_lists_what_the_package_lacks(self):
        saved = (cj.required_connections, cj._db)
        try:
            cj._db = lambda: _AccountsDB({"google_ads"})
            cj.required_connections = lambda cid: {"steps": [{"key": "google_ads", "platforms": ["google_ads"]}],
                                                   "services": ["google ads"], "resolved": True}
            status = cj.connection_status(40)
            self.assertIn("tiktok", status["not_in_plan"])
            self.assertNotIn("google_ads", status["not_in_plan"])
            cj.required_connections = lambda cid: {"steps": [], "services": [], "resolved": False}
            self.assertEqual(cj.connection_status(40)["not_in_plan"], [])     # unknown package: say nothing
        finally:
            cj.required_connections, cj._db = saved

    def test_agents_see_it_in_account_state(self):
        saved = cj.required_connections
        try:
            cj.required_connections = lambda cid: {"steps": [], "services": ["google ads"], "resolved": True}
            self.assertIn("tiktok", cs._package(40)["not_in_package"])
            cj.required_connections = lambda cid: {"steps": [], "services": [], "resolved": False}
            self.assertEqual(cs._package(40)["not_in_package"], [])
        finally:
            cj.required_connections = saved

    def test_dashboard_cards_say_it_and_offer_to_ask(self):
        html = _src("dashboard", "client", "index.html")
        guide = html.split("function renderConnectionGuide(status) {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("markNotInPlanCards(status);", guide)
        self.assertLess(guide.index("markNotInPlanCards(status);"), guide.index("guide.style.display = 'none'"))
        marker = html.split("function markNotInPlanCards(status) {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("openAgentWindow('general')", marker)
        for key in ("conn_not_in_plan_note", "conn_not_in_plan_ask", "action_team_request_open"):
            line = next(l for l in html.splitlines() if l.strip().startswith(key + ":"))
            for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
                self.assertIn(lang, line, key)


# ─── The prompts ─────────────────────────────────────────────────────────────

class Prompts(unittest.TestCase):
    def test_manager_not_switchboard(self):
        self.assertIn("NOT A SWITCHBOARD", sa.SYSTEM)
        self.assertIn("never tell the client to open another chat", sa.SYSTEM)

    def test_handoff_only_for_professional_or_commercial_decisions(self):
        self.assertIn("NEVER the answer to a request for\nsomething the system can do", sa.HANDOFF_RULE)
        self.assertIn("handoff_topic", sa.SYSTEM)
        self.assertNotIn("Only if they ask you to go ahead now, set needs_human_followup to true and say the\n"
                         "   team will take it from here", sa.ACCOUNT_STATE_RULE)

    def test_stale_tiktok_line_is_gone(self):
        self.assertNotIn("coming soon", sa.SYSTEM)
        self.assertIn("send_tiktok_to_inbox", sa.SYSTEM)

    def test_package_and_photos_rules_reach_every_chat(self):
        for rule in ("PACKAGE:", "PHOTOS → PRESENTER", "not_in_package", "team_requests"):
            self.assertIn(rule, sa.SYSTEM)
            self.assertIn(rule, sa.PERSONA_SYSTEM_TEMPLATE)

    def test_personas_never_redirect_and_still_format(self):
        self.assertNotIn("redirect to the main uallak chat", sa.PERSONA_SYSTEM_TEMPLATE)
        text = sa.PERSONA_SYSTEM_TEMPLATE.format(name="n", domain="d", voice="v", data_notes="x",
                                                 action_exception="", action_rule="", extra_fields="")
        self.assertIn('"upgrade_request"', text)
        self.assertIn("Automation {is} quoted by hand.", text)    # the automation rule's braces survive

    def test_persona_upgrade_is_the_concierges_build(self):
        body = _src("agents", "support_agent.py").split("def _answer_persona_question(", 1)[1]
        self.assertIn("_build_upgrade_proposal(client_id, client, lead, upgrade_request)", body)
        self.assertIn("_with_handoff(", body)


if __name__ == "__main__":
    unittest.main()
