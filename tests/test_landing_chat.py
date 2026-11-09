"""Landing pages through the manager chat (2026-11-02, docs/CHAT_PARITY.md).

- create_landing_page: a proposal card → the client's click builds a DRAFT
  with THE builder (landing_page_agent.create_page, the dashboard button's own
  code) → a publish card with a preview link in the SAME chat thread. Nothing
  is live until the client approves that second card.
- One question at most (the offer), the package's page count from the system.
- edit / revert / unpublish / delete / domain — each one card, one click.
- No team request is ever opened for a landing-page ask; the team topics are
  the five decisions only.
- A client with no landing pages never hears about a draft (the code guard
  behind the prompt rule).
- Per-client isolation: another client's page can't be named or approved.

Stdlib only. Run:  python -I -B tests/test_landing_chat.py -v
"""
import contextlib
import os
import sys
import types
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


ACTIVITY = []
CHAT_LINES = []

STUBS = {
    "httpx": _module("httpx"),
    "anthropic": _module("anthropic", Anthropic=lambda *a, **k: None),
    "supabase": _module("supabase", create_client=lambda *a, **k: None),
    "agents.client_agent": _module(
        "agents.client_agent",
        get_client=lambda cid: {"id": cid, "language": "he", "billing_exempt": False, "phone": ""},
        get_activity=lambda *a, **k: [], get_communications=lambda *a, **k: [],
        log_activity=lambda *a, **k: ACTIVITY.append(a), _db=lambda: None, get_accounts=lambda cid: [],
        log_communication=lambda cid, direction, channel, text: CHAT_LINES.append((cid, channel, text))),
    "agents.onboarding_agent": _module("agents.onboarding_agent", LANGUAGE_RULE="\n\nLANGUAGE.", PRICING={}),
    "core.automation_service": _module("core.automation_service", VENDORS={},
                                       POLICY_NOTE="Automation {is} quoted by hand.", SIGNUP_RULE_HE=""),
    "core.agent_base": _module("core.agent_base", agent_alert=lambda *a, **k: None,
                               log_step=lambda *a, **k: None, timed_step=lambda a, s, fn: fn()),
    "core.competitor_research": _module("core.competitor_research", summary_for_prompt=lambda cid, lens: ""),
    "core.landing_domains": _module("core.landing_domains",
                                    get_state=lambda cid: {"mode": "shared", "hostname": "", "dns": None},
                                    normalize_hostname=lambda raw: (raw or "").strip().lower()),
    "core.cost_tracker": _module("core.cost_tracker", cost_action=lambda *a, **k: contextlib.nullcontext()),
    "core.gtm_connection": _module("core.gtm_connection", _connection=lambda cid: {"access_token": "t"}),
    "agents.avatar_agent": _module("agents.avatar_agent", has_consent=lambda cid, scope: True,
                                   create_avatar=lambda *a, **k: {}, list_ready_avatars=lambda cid: [],
                                   generated_image_ids=lambda cid: set(), current_voice_id=lambda cid: ""),
}
_FRESH = ("agents.support_agent", "agents.landing_page_agent")
_SAVED = {name: sys.modules.get(name) for name in list(STUBS) + list(_FRESH)}
sys.modules.update(STUBS)
for _name in _FRESH:
    sys.modules.pop(_name, None)

from agents import landing_page_agent as lpa   # noqa: E402
from agents import support_agent as sa         # noqa: E402
from core import agent_actions as aa           # noqa: E402
from core import chat_fallback                 # noqa: E402
from core import landing_pages as lp           # noqa: E402
from test_landing_own import FakeDB             # noqa: E402

COPY = {"headline": "סניקרס בעיצוב אישי, רק שלכם", "subheadline": "ציור יד על הנעל שבחרתם",
        "benefits": ["עיצוב לפי בקשה", "ציור יד", "מתנה שאין לאף אחד"], "sections": [],
        "cta_text": "לקבלת הצעה", "form_note": "נחזור אליכם היום"}


def tearDownModule():
    for name, module in _SAVED.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.posted, self.alerts, self.gaps = [], [], []
        ACTIVITY.clear()
        CHAT_LINES.clear()
        self.saved = {n: getattr(aa, n) for n in ("_db", "_client", "_post_client_message", "_alert", "_log")}
        self.saved_lp = lp._db
        self.saved_copy = lpa.generate_copy
        self.saved_gap = chat_fallback.alert_capability_gap
        self.saved_handoff = chat_fallback.alert_handoff
        lp._db = lambda: self.db
        lp._versions_ok["value"] = True
        aa._db = lambda: self.db
        aa._client = lambda cid: {"id": cid, "language": "he", "billing_exempt": False}
        aa._post_client_message = lambda cid, persona, text: self.posted.append((cid, persona, text))
        aa._alert = self.alerts.append
        aa._log = lambda *a, **k: None
        self.copy_calls = []

        def generate_copy(client_id, goal, title="", current=None):
            self.copy_calls.append((client_id, goal, title))
            return {"success": True, "content": lp.normalize_content(COPY), "notes": "", "research_grounded": False}
        lpa.generate_copy = generate_copy
        chat_fallback.alert_capability_gap = lambda *a, **k: self.gaps.append(a)
        chat_fallback.alert_handoff = lambda *a, **k: self.alerts.append(("handoff", a, k))

    def tearDown(self):
        for n, fn in self.saved.items():
            setattr(aa, n, fn)
        lp._db = self.saved_lp
        lpa.generate_copy = self.saved_copy
        chat_fallback.alert_capability_gap = self.saved_gap
        chat_fallback.alert_handoff = self.saved_handoff

    # helpers
    def rows(self, action=None, client_id=None):
        return [r for r in self.db.tables.get(aa.TABLE, [])
                if (not action or r.get("action") == action)
                and (client_id is None or r.get("client_id") == client_id)]

    def propose(self, name, params, client_id=27, persona="general", state=None):
        return aa.propose(client_id, "client_chat", persona, {"name": name, "params": params},
                          state=state, context={"language": "he"})

    def create_card(self, client_id=27, persona="general"):
        return self.propose("create_landing_page",
                            {"title": "עיצוב אישי לנעליים", "goal": "נעלי סניקרס בעיצוב אישי בציור יד, לצעירים"},
                            client_id=client_id, persona=persona)

    def approve(self, card_id, client_id=27):
        return aa.decide(card_id, "client", client_id, "approve")


# ─── Create: proposal → draft → preview card → publish only on approval ────

class CreateThroughChat(Base):
    def test_the_proposal_builds_nothing_until_the_click(self):
        out = self.create_card()
        self.assertEqual(out["card"]["action"], "create_landing_page")
        self.assertTrue(out["card"]["can_decide"])
        self.assertEqual(out["card"]["details"]["title"], "עיצוב אישי לנעליים")
        self.assertIn("שום דבר לא עולה לאוויר", out["card"]["what"])
        self.assertEqual(self.db.tables.get("landing_pages", []), [])
        self.assertEqual(self.copy_calls, [])

    def test_the_click_builds_a_draft_with_the_shared_builder_and_a_preview_card(self):
        card = self.create_card(persona="general")["card"]
        out = self.approve(card["id"])
        self.assertEqual(out["status"], "done", out)
        pages = self.db.tables["landing_pages"]
        self.assertEqual(len(pages), 1)
        page = pages[0]
        self.assertEqual((page["client_id"], page["status"]), (27, "draft"))
        self.assertEqual(page["content"]["headline"], COPY["headline"])
        # the dashboard button's builder ran (the copy writer got the client's real offer)
        self.assertEqual(self.copy_calls, [(27, "נעלי סניקרס בעיצוב אישי בציור יד, לצעירים", "עיצוב אישי לנעליים")])
        self.assertTrue(any(a[2] == "landing_page_created" for a in ACTIVITY))
        # the preview card, in the SAME thread the client asked in
        publish = self.rows("publish_landing_page")
        self.assertEqual(len(publish), 1)
        self.assertEqual((publish[0]["persona"], publish[0]["status"]), ("general", "proposed"))
        preview = aa.card(publish[0], "client", "he")
        self.assertEqual(preview["details"]["preview_url"], f"/api/client/landing-pages/{page['id']}/preview?version=1")
        # one done line in that thread, saying where the preview is — no duplicate intro
        self.assertEqual([p[1] for p in self.posted], ["general"])
        self.assertIn("תצוגה מקדימה", self.posted[0][2])
        self.assertIn("עיצוב אישי לנעליים", self.posted[0][2])

    def test_publishing_needs_the_second_approval(self):
        self.approve(self.create_card()["card"]["id"])
        page_id = self.db.tables["landing_pages"][0]["id"]
        publish = self.rows("publish_landing_page")[0]
        self.assertEqual(lp.get_page(27, page_id)["status"], "draft")
        aa.decide(publish["id"], "client", 27, "cancel")
        self.assertEqual(lp.get_page(27, page_id)["status"], "draft")
        # a fresh card, approved this time → live at the previewed version
        again = self.propose("publish_landing_page", {"page_id": page_id})["card"]
        saved = lpa._offer_tracking, lpa._limit_alert
        lpa._offer_tracking, lpa._limit_alert = (lambda cid: False), (lambda cid: None)
        try:
            self.assertEqual(self.approve(again["id"])["status"], "done")
        finally:
            lpa._offer_tracking, lpa._limit_alert = saved
        page = lp.get_page(27, page_id)
        self.assertEqual((page["status"], page["published_version"]), ("published", 1))

    def test_a_specialist_thread_keeps_its_own_cards(self):
        self.approve(self.create_card(persona="website")["card"]["id"])
        self.assertEqual(self.rows("publish_landing_page")[0]["persona"], "website")

    def test_at_most_one_question_the_offer(self):
        out = self.propose("create_landing_page", {"title": "דף נחיתה"})
        self.assertEqual(set(out), {"clarify"})
        self.assertEqual(out["clarify"].count("?"), 1)
        self.assertEqual(self.rows(), [])

    def test_the_package_count_comes_from_the_system(self):
        for i in range(lp.MAX_PAGES_PER_CLIENT):
            lp.create_page(27, f"דף {i}")
        out = self.create_card()
        self.assertEqual(set(out), {"refuse"})
        self.assertIn(str(lp.MAX_PAGES_PER_CLIENT), out["refuse"])
        self.assertEqual(self.rows("create_landing_page"), [])
        self.assertEqual(self.rows("team_request"), [])

    def test_copy_failure_is_said_plainly(self):
        lpa.generate_copy = lambda *a, **k: {"success": False, "errors": ["x"]}
        out = self.approve(self.create_card()["card"]["id"])
        self.assertEqual((out["status"], out["code"]), ("failed", "copy_failed"))
        self.assertEqual(self.rows("publish_landing_page"), [])
        self.assertIn("כתבו לי", out["message"])

    def test_a_changed_proposal_replaces_the_open_card(self):
        first = self.create_card()["card"]
        second = self.propose("create_landing_page", {"title": "עיצוב אישי לנעליים", "goal": "מתנה אישית לחג"})["card"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.approve(first["id"]).get("already"), True)
        self.assertEqual(self.db.tables.get("landing_pages", []), [])

    def test_offered_only_when_the_landing_section_was_read(self):
        names = lambda state: [a["name"] for a in aa.available(27, "client_chat", state,
                                                               client={"id": 27, "billing_exempt": False})]
        self.assertIn("create_landing_page", names({"landing_pages": {"pages": [], "used": 0, "included": 3}}))
        self.assertNotIn("create_landing_page", names({}))
        self.assertNotIn("edit_landing_page", names({"landing_pages": {"pages": []}}))
        self.assertIn("edit_landing_page", names({"landing_pages": {"pages": [{"id": 1, "status": "draft"}]}}))
        self.assertNotIn("unpublish_landing_page", names({"landing_pages": {"pages": [{"id": 1, "status": "draft"}]}}))


# ─── The rest of the lifecycle ───────────────────────────────────────────────

class Lifecycle(Base):
    def page(self, client_id=27, published=False):
        created = lp.create_page(client_id, "עיצוב אישי", "נעליים")
        pid = created["page"]["id"]
        lp.update_page(client_id, pid, {"content": {"headline": "גרסה 1", "cta_text": "שלחו"}}, source="generated")
        if published:
            lp.publish(client_id, pid)
        return pid

    def test_edit_in_words_is_a_new_version_and_live_stays_until_approved(self):
        pid = self.page(published=True)
        card = self.propose("edit_landing_page", {"page_id": pid, "change_request": "קצר יותר, עם המחיר למעלה"})["card"]
        self.assertEqual(card["details"]["description"], "קצר יותר, עם המחיר למעלה")
        self.assertEqual(self.approve(card["id"])["status"], "done")
        page = lp.get_page(27, pid)
        self.assertEqual((page["current_version"], page["published_version"]), (2, 1))
        self.assertIn("CLIENT FEEDBACK ON THE PREVIOUS VERSION: קצר יותר", self.copy_calls[-1][1])
        publish = self.rows("publish_landing_page")
        self.assertEqual((len(publish), publish[0]["params"]["version"], publish[0]["persona"]), (1, 2, "general"))

    def test_revert_needs_a_real_version(self):
        pid = self.page()
        self.assertIn("refuse", self.propose("revert_landing_page", {"page_id": pid, "version": 9}))
        lp.update_page(27, pid, {"content": {"headline": "גרסה 2", "cta_text": "x"}})
        card = self.propose("revert_landing_page", {"page_id": pid, "version": 1})["card"]
        self.assertEqual(card["details"]["preview_url"], f"/api/client/landing-pages/{pid}/preview?version=1")
        out = self.approve(card["id"])
        self.assertEqual(out["status"], "done")
        self.assertIn("גרסה 1", out["message"])
        self.assertEqual(lp.visible_of(27, lp.get_page(27, pid), 3)["content"]["headline"], "גרסה 1")

    def test_unpublish_only_a_live_page(self):
        draft = self.page()
        self.assertIn("refuse", self.propose("unpublish_landing_page", {"page_id": draft}))
        live = self.page(published=True)
        out = self.approve(self.propose("unpublish_landing_page", {"page_id": live})["card"]["id"])
        self.assertEqual(out["status"], "done")
        self.assertEqual(lp.get_page(27, live)["status"], "draft")
        self.assertIn("עיצוב אישי", out["message"])

    def test_delete_keeps_the_title_for_the_done_line(self):
        pid = self.page()
        card = self.propose("delete_landing_page", {"page_id": pid})["card"]
        self.assertIn("עיצוב אישי", card["what"])
        out = self.approve(card["id"])
        self.assertEqual(out["status"], "done")
        self.assertEqual(lp.get_page(27, pid), {})
        self.assertIn("עיצוב אישי", out["message"])


# ─── Per-client isolation ────────────────────────────────────────────────────

class Isolation(Base):
    def test_another_clients_page_cannot_be_named(self):
        pid = lp.create_page(27, "עיצוב אישי")["page"]["id"]
        for name, params in (("edit_landing_page", {"page_id": pid, "change_request": "שנה"}),
                             ("revert_landing_page", {"page_id": pid, "version": 1}),
                             ("unpublish_landing_page", {"page_id": pid}),
                             ("delete_landing_page", {"page_id": pid}),
                             ("publish_landing_page", {"page_id": pid})):
            self.assertEqual(self.propose(name, params, client_id=8), {"error": "invalid_params"}, name)
        self.assertEqual(self.rows(client_id=8), [])

    def test_another_client_cannot_approve_the_card(self):
        card = self.create_card(client_id=27)["card"]
        self.assertEqual(aa.decide(card["id"], "client", 8, "approve")["code"], "not_found")
        self.assertEqual(self.db.tables.get("landing_pages", []), [])

    def test_the_draft_belongs_to_the_client_who_approved(self):
        self.approve(self.create_card(client_id=27)["card"]["id"])
        self.assertEqual(lp.list_pages(8), [])
        self.assertEqual(len(lp.list_pages(27)), 1)
        self.assertEqual({r["client_id"] for r in self.rows()}, {27})


# ─── No team request for a landing page; no invented drafts ─────────────────

class NoTeamFallback(Base):
    def test_a_landing_page_ask_never_opens_a_team_request(self):
        # Whatever topic the model reaches for, a landing page is not a team decision
        for topic in ("manual_task", "other", "action_not_offered", ""):
            out = sa._follow_up(27, "general", "תבנה לי דף נחיתה לעיצוב אישי", "אכין הצעה.",
                                {"needs_human_followup": True, "handoff_topic": topic,
                                 "capability_gap": ""}, out_of_scope=False)
            self.assertNotIn("action", out)
            self.assertFalse(out["needs_human_followup"])
        self.assertEqual(self.rows("team_request"), [])
        self.assertEqual(len(self.gaps), 4)

    def test_a_refused_card_is_not_a_team_request_either(self):
        out = sa._route_proposal(27, "general", "תבנה לי דף נחיתה",
                                 {"proposed_action": {"name": "create_landing_page", "params": {"title": "x"}}},
                                 "מכין לך כרטיס", state={}, client={"id": 27, "billing_exempt": False})
        self.assertEqual(self.rows("team_request"), [])
        self.assertNotIn("action", out)

    def test_the_prompts_offer_the_card_and_never_the_team(self):
        rule = sa.ACCOUNT_STATE_RULE
        self.assertIn("create_landing_page", rule)
        self.assertIn("NOTHING THAT ISN'T THERE", rule)
        self.assertIn("at most ONE short question", rule)
        for prompt in (sa.SYSTEM, sa.PERSONA_SYSTEM_TEMPLATE, sa.HANDOFF_RULE, rule):
            self.assertNotIn("manual_task", prompt)
            self.assertNotIn("our team does it", prompt)
        self.assertNotIn("changing its design is done", rule)
        self.assertIn('"capability_gap"', sa.SYSTEM)
        self.assertIn('"capability_gap"', sa.PERSONA_SYSTEM_TEMPLATE)
        self.assertEqual(aa.TEAM_REQUEST_TOPICS, ("legal", "tax", "contract", "billing", "package_exception"))

    def test_capability_gap_alert_is_admin_only_and_deduped(self):
        chat_fallback.alert_capability_gap = self.saved_gap
        sent = []
        saved = chat_fallback.alert_once
        chat_fallback.alert_once = lambda source, cid, key, message, days: sent.append((source, cid, key, message, days))
        try:
            chat_fallback.alert_capability_gap(27, "general", "תעלו לאינסטגרם", "post to Instagram")
        finally:
            chat_fallback.alert_once = saved
        self.assertEqual(sent[0][0], "capability_gap")
        self.assertEqual(sent[0][1], 27)
        self.assertIn("CAPABILITY GAP", sent[0][3])
        self.assertEqual(sent[0][4], chat_fallback.CAPABILITY_GAP_DAYS)
        self.assertEqual(self.posted, [])          # nothing reaches the client's chat


class OldTeamRequest(Base):
    """Request #6 (client 27): asked for a landing page before the chat could build one."""

    def open_request(self, client_id=27, topic="manual_task", status="awaiting_staff"):
        self.db.tables.setdefault(aa.TABLE, []).append({
            "id": 6, "client_id": client_id, "action": "team_request", "origin": "client_chat",
            "persona": "general", "approver": "admin", "status": status,
            "params": {"topic": topic, "request": "תבנה לי דף נחיתה לעיצוב אישי"},
            "created_at": datetime.now(timezone.utc).isoformat()})

    def test_dry_run_shows_the_request_and_changes_nothing(self):
        self.open_request()
        out = lpa.fulfil_team_request(27, 6, "עיצוב אישי", dry_run=True)
        self.assertEqual((out["request"], out["topic"], out["pages_used"]), ("תבנה לי דף נחיתה לעיצוב אישי", "manual_task", 0))
        self.assertEqual(self.db.tables.get("landing_pages", []), [])
        self.assertEqual(self.rows("team_request")[0]["status"], "awaiting_staff")

    def test_builds_the_draft_keeps_the_request_and_marks_it_handled(self):
        self.open_request()
        out = lpa.fulfil_team_request(27, 6, "עיצוב אישי")
        self.assertTrue(out["success"], out)
        page = lp.get_page(27, out["page_id"])
        self.assertEqual(page["status"], "draft")                  # never published here
        self.assertEqual(self.copy_calls[-1][1], "תבנה לי דף נחיתה לעיצוב אישי")
        publish = self.rows("publish_landing_page")
        self.assertEqual((len(publish), publish[0]["persona"], publish[0]["status"]), (1, "general", "proposed"))
        request = self.rows("team_request")[0]                     # kept, not deleted
        self.assertEqual((request["status"], request["outcome"], request["decided_by"]),
                         ("done", "handled_by_system", "system"))
        self.assertIn("draft", request["params"]["system_note"])
        self.assertEqual(aa.card(request, "client", "he")["result"], aa._pick(aa.HANDLED_BY_SYSTEM, "he"))
        self.assertEqual(aa.team_requests_view(27)[0]["status"], "handled")

    def test_another_clients_or_a_closed_request_is_not_touched(self):
        self.open_request(client_id=27)
        self.assertEqual(lpa.fulfil_team_request(8, 6, "x")["code"], "ERR_REQUEST_NOT_FOUND")
        self.db.tables[aa.TABLE][0]["status"] = "done"
        self.assertEqual(lpa.fulfil_team_request(27, 6, "x")["code"], "ERR_REQUEST_NOT_FOUND")
        self.assertEqual(self.db.tables.get("landing_pages", []), [])

    def test_a_failed_copy_leaves_the_request_open(self):
        self.open_request()
        lpa.generate_copy = lambda *a, **k: {"success": False, "errors": ["x"]}
        out = lpa.fulfil_team_request(27, 6, "עיצוב אישי")
        self.assertEqual(out["code"], "ERR_LANDING_COPY_FAILED")
        self.assertEqual(self.rows("team_request")[0]["status"], "awaiting_staff")

    def test_the_admin_route_is_admin_only_and_dry_run_by_default(self):
        src = _src("core", "api_server.py")
        self.assertIn('@app.post("/api/team-requests/{request_id}/fulfil-landing", dependencies=_admin_only)', src)
        self.assertIn("dry_run: bool = True", src)


class NoInventedDrafts(Base):
    INVENTED = 'הטיוטה "עיצוב אישי 2026" שלך כבר מוכנה, רק צריך לאשר.'

    def test_a_client_without_pages_never_hears_about_a_draft(self):
        state = {"landing_pages": {"pages": [], "used": 0, "included": 3}}
        out = sa._guard_invented(27, "general", "מה עם הדף?", self.INVENTED, state, {})
        self.assertEqual(out, sa._NO_LANDING_YET["he"])
        self.assertNotIn("2026", out)
        self.assertEqual(len(self.alerts), 1)            # the admin side hears the model invented one
        for line in ('יש לכם כבר טיוטה של דף נחיתה.', "Your draft is ready.", 'דף הנחיתה שלכם באוויר.'):
            self.assertEqual(sa._guard_invented(27, "general", "מה עם הדף?", line, state, {}),
                             sa._NO_LANDING_YET["he"], line)

    def test_real_pages_and_offers_are_left_alone(self):
        empty = {"landing_pages": {"pages": []}}
        offer = "אין לכם עדיין דף נחיתה. רוצים שאכתוב אחד?"
        self.assertEqual(sa._guard_invented(27, "general", "x", offer, empty, {}), offer)
        real = {"landing_pages": {"pages": [{"id": 1, "title": "עיצוב אישי", "status": "draft"}]}}
        self.assertEqual(sa._guard_invented(27, "general", "x", self.INVENTED, real, {}), self.INVENTED)
        # unread section: no claim either way
        self.assertEqual(sa._guard_invented(27, "general", "x", self.INVENTED, {}, {}), self.INVENTED)
        # the reply that comes with the create card speaks of the page it is about to write
        proposing = {"proposed_action": {"name": "create_landing_page"}}
        self.assertEqual(sa._guard_invented(27, "general", "x", self.INVENTED, empty, proposing), self.INVENTED)

    def test_both_chat_paths_run_the_guard(self):
        src = _src("agents", "support_agent.py")
        self.assertEqual(src.count("reply = _guard_invented("), 2)


if __name__ == "__main__":
    unittest.main()
