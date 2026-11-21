"""Specialist agents in direct chat (2026-11-22, client 27 / ליאור).

PART 1 — a specialist answers the request it was asked. The exact request
  "I want to prepare a post made of 3 reasons why custom design is a special
  gift ... the post itself should be 3 posts, each stating one reason" is
  replayed through the real persona path (_answer_persona_question) with a
  model reply that drifts into the weekly content plan: what the client sees
  is about the 3 posts and never mentions a weekly plan, and no plan card is
  proposed.
PART 2 — the 3 posts are produced on their own: produce_requested_content,
  ONE card, media_agent per piece, Drive only, nothing published; the tool
  missing → its card (wiring, not a limit).
PART 3 — no fixed opener in the voices; the same sentence twice in one reply
  is said once.
PART 4 — the connect loop: "נחבר את TikTok. יש לכם כבר חשבון שם?" is never
  posted for a connected channel, and for a channel that is not connected it
  is asked ONCE across the replayed sequence (09/11 ×4, 10/11, 17/11 ×3,
  18/11); after "I have an account" it is never asked again — the blocker is
  named instead.
PART 5 — "I can't" only with what CAN be done and what unblocks it (prompt).

Stdlib only. Run from the repo root:  python -I -B tests/test_specialist_scope.py -v
"""
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_media_persona as _stubs        # noqa: E402,F401  (installs the support_agent stubs)
from agents import support_agent as sa     # noqa: E402
from core import agent_actions as aa      # noqa: E402
from core import connection_catalog as cc  # noqa: E402
from core import reply_guards as rg        # noqa: E402

REQUEST_EN = ("I want to prepare a post made of 3 reasons why custom design is a special gift. "
              "The post itself should be 3 posts, each stating one reason.")
REQUEST_HE = ("אני רוצה להכין פוסט שמורכב מ-3 סיבות למה עיצוב בהתאמה אישית הוא מתנה מיוחדת. "
              "הפוסט עצמו צריך להיות 3 פוסטים, כל אחד מציין סיבה אחת.")
PLAN_WORDS = re.compile(r"תוכנית|תכנית|שבוע|weekly|content plan|plan for", re.IGNORECASE)

# What the model answered on 2026-11-22 (in substance): the 3 posts, then the
# weekly plan pitched on top, and the plan card proposed
DRIFT_HE = ("רעיון מצוין. פוסט 1: עיצוב שנולד בשבילו בלבד. פוסט 2: כל פרט מספר את הסיפור שלכם. "
            "פוסט 3: מתנה שאי אפשר לקנות במדף. "
            "בנוסף, אכין לכם את תוכנית התוכן השבועית כדי שהפוסטים ייכנסו ללוח. "
            "את התמונות עצמן אני לא יכול להפיק ולהראות לך מכאן כבדיקה נפרדת מהתוכנית.")
DRIFT_EN = ("Great idea. Post 1: a design made only for them. Post 2: every detail tells your story. "
            "Post 3: a gift no shelf can sell. I'll also put together your weekly content plan so they fit the week.")


class PersonaReplay(unittest.TestCase):
    """The real _answer_persona_question; only its reads and the model are stubbed."""

    def setUp(self):
        names = ("get_client", "_thread_context", "_decide_turn", "_load_context", "_proposal_for_chat",
                 "_cards_payload", "_card_decision", "_data_unavailable", "_route_proposal",
                 "_client_language", "_turn_flags")
        self.saved = {n: getattr(sa, n) for n in names}
        self.saved_routed = sa.model_routing.routed_turn
        self.routed = []
        sa.get_client = lambda cid: {"id": cid, "name": "x", "package": "p", "status": "active"}
        sa._thread_context = lambda *a, **k: ([], None)
        sa._decide_turn = lambda *a, **k: {"kind": "full"}
        sa._load_context = lambda *a, **k: {"lead": {}, "state": {}, "failed": set(), "client_knowledge": {},
                                            "recent_activity": [], "reads": {}, "store": {}, "account": {}}
        sa._proposal_for_chat = lambda lead: {}
        sa._cards_payload = lambda *a, **k: None
        sa._card_decision = lambda *a, **k: None
        sa._data_unavailable = lambda *a, **k: None
        sa._turn_flags = lambda *a, **k: None
        sa._route_proposal = lambda cid, pid, msg, result, reply, state, client: (
            self.routed.append(sa._proposed_name(result)) or {"reply": reply, "action": {"name": sa._proposed_name(result)}})

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(sa, n, v)
        sa.model_routing.routed_turn = self.saved_routed

    def _turn(self, message, model_reply, proposed, lang="he", persona="media"):
        sa._client_language = lambda cid, msg: lang
        sa.model_routing.routed_turn = lambda *a, **k: {
            "reply": model_reply, "confidence": "high", "needs_human_followup": False, "out_of_scope": False,
            "proposed_action": {"name": proposed, "params": {}, "summary": ""} if proposed else None}
        return sa._answer_persona_question(27, persona, sa.PERSONAS[persona], message)

    def test_three_posts_he_never_the_weekly_plan(self):
        out = self._turn(REQUEST_HE, DRIFT_HE, "build_content_plan")
        self.assertIn("פוסט 1", out["reply"])
        self.assertIn("פוסט 3", out["reply"])
        self.assertNotRegex(out["reply"], PLAN_WORDS)
        self.assertNotIn("build_content_plan", self.routed)
        self.assertNotIn("action", out)

    def test_three_posts_en_never_the_weekly_plan(self):
        out = self._turn(REQUEST_EN, DRIFT_EN, "approve_content_plan", lang="en")
        self.assertIn("Post 1", out["reply"])
        self.assertNotRegex(out["reply"], PLAN_WORDS)
        self.assertEqual(self.routed, [])

    def test_the_produce_card_for_those_posts_goes_through(self):
        out = self._turn(REQUEST_HE, "פוסט 1, פוסט 2, פוסט 3 — מכין לכם אותם.", "produce_requested_content")
        self.assertEqual(self.routed, ["produce_requested_content"])
        self.assertEqual(out["action"]["name"], "produce_requested_content")

    def test_an_explicit_plan_ask_keeps_the_plan(self):
        out = self._turn("תכין לי את תוכנית התוכן לשבוע הבא", "מכין את תוכנית התוכן השבועית.", "build_content_plan")
        self.assertEqual(self.routed, ["build_content_plan"])
        self.assertIn("תוכנית", out["reply"])

    def test_the_manager_still_orchestrates(self):
        self.assertFalse(rg.plan_card_off_request("general", "build_content_plan", REQUEST_HE))
        self.assertTrue(rg.plan_card_off_request("media", "build_content_plan", REQUEST_HE))
        self.assertTrue(rg.plan_card_off_request("meta", "approve_content_plan", REQUEST_EN))

    def test_a_repeated_sentence_is_said_once(self):
        value = "בחוץ, תהליך כזה של לראות ולהחליט לפני שמתחייבים עולה אלפי שקלים."
        out = self._turn("מה זה המיתוג הזה?", f"{value} כאן הוא בחינם עד לנקודת האישור. {value}", "")
        self.assertEqual(out["reply"].count("אלפי שקלים"), 1)
        self.assertIn("בחינם", out["reply"])


class Guards(unittest.TestCase):
    def test_plan_talk_is_kept_when_asked(self):
        reply, dropped = rg.drop_plan_talk("Here is the weekly content plan.", "can you make my plan for the week?")
        self.assertEqual(dropped, 0)

    def test_plan_talk_never_empties_a_reply(self):
        reply, dropped = rg.drop_plan_talk("I'll build your weekly content plan.", REQUEST_EN)
        self.assertEqual((reply, dropped), ("I'll build your weekly content plan.", 0))

    def test_dedupe_keeps_short_repeats_and_order(self):
        text = "כן. כן. זה עולה אלפי שקלים בחוץ.\nשורה אחרת.\nזה עולה אלפי שקלים בחוץ!"
        out, dropped = rg.dedupe_sentences(text)
        self.assertEqual(dropped, 1)
        self.assertEqual(out, "כן. כן. זה עולה אלפי שקלים בחוץ.\nשורה אחרת.")

    def test_dedupe_in_every_language(self):
        for s in ("Outside this costs thousands of shekels.", "Ailleurs cela coûte des milliers de shekels.",
                  "في الخارج يكلف ذلك آلاف الشواكل.", "На стороне это стоит тысячи шекелей."):
            out, dropped = rg.dedupe_sentences(f"{s} Yes indeed, really. {s}")
            self.assertEqual((out.count(s), dropped), (1, 1), s)

    def test_connected_offer_dropped_only_when_connected(self):
        reply = "הסרטון מוכן. נחבר את TikTok. יש לכם כבר חשבון שם?"
        out, keys = rg.drop_connected_offers(27, reply, is_live=lambda cid, key: key == "tiktok")
        self.assertEqual((out, keys), ("הסרטון מוכן.", ["tiktok"]))
        out, keys = rg.drop_connected_offers(27, reply, is_live=lambda cid, key: False)
        self.assertEqual((out, keys), (reply, []))
        out, keys = rg.drop_connected_offers(27, "Let's connect YouTube now. The clip is ready.",
                                             is_live=lambda cid, key: key == "youtube")
        self.assertEqual((out, keys), ("The clip is ready.", ["youtube"]))

    def test_a_connected_status_line_is_kept(self):
        out, keys = rg.drop_connected_offers(27, "TikTok מחובר ✓ אפשר לשלוח.", is_live=lambda c, k: True)
        self.assertEqual(keys, [])


# ─── PART 4: the connect loop, replayed ──────────────────────────────────────

class _Query:
    def __init__(self, db, table, op="select", payload=None):
        self.db, self.table, self.op, self.payload, self.filters = db, table, op, payload, []

    def select(self, *_a, **_k): return self
    def limit(self, *_a): return self
    def order(self, *_a, **_k): return self
    def eq(self, f, v): self.filters.append(lambda r: r.get(f) == v); return self
    def in_(self, f, vs): self.filters.append(lambda r: r.get(f) in vs); return self
    def gte(self, f, v): self.filters.append(lambda r: str(r.get(f, "")) >= v); return self
    def like(self, f, p): self.filters.append(lambda r: str(r.get(f, "")).startswith(p.rstrip("%"))); return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            rows[:] = [r for r in rows if r.get("key") != self.payload.get("key")] + [dict(self.payload)]
            return type("R", (), {"data": [self.payload]})()
        if self.op == "delete":
            rows[:] = [r for r in rows if not all(f(r) for f in self.filters)]
            return type("R", (), {"data": []})()
        return type("R", (), {"data": sorted((r for r in rows if all(f(r) for f in self.filters)),
                                             key=lambda r: r.get("created_at", ""))})()


class _Table:
    def __init__(self, db, name): self.db, self.name = db, name
    def select(self, *a, **k): return _Query(self.db, self.name)
    def upsert(self, payload, on_conflict=None): return _Query(self.db, self.name, "upsert", payload)
    def delete(self): return _Query(self.db, self.name, "delete")


class _DB:
    def __init__(self): self.tables = {}
    def table(self, name): return _Table(self, name)


# The days the question fired for client 27 (2026-11)
SEQUENCE = ["2026-11-09T08:00", "2026-11-09T08:05", "2026-11-09T11:40", "2026-11-09T19:02",
            "2026-11-10T09:10", "2026-11-17T10:00", "2026-11-17T10:03", "2026-11-17T16:30",
            "2026-11-18T09:45"]
ROWS = [{"key": "tiktok", "tier": "free_account", "signup_url": "https://signup.example/tt", "pricing_url": "",
         "recommended_plan_note": "", "help_notes": "", "enabled": True},
        {"key": "youtube", "tier": "free_account", "signup_url": "", "pricing_url": "",
         "recommended_plan_note": "", "help_notes": "", "enabled": True}]


class ConnectLoop(unittest.TestCase):
    def setUp(self):
        self.saved = (cc.rows, cc.is_connected, cc._db, cc._now, cc._health)
        self.db = _DB()
        cc.rows = lambda fresh=False: [dict(r) for r in ROWS]
        self.connected = set()
        self.health = {}
        cc.is_connected = lambda cid, key: (cid, key) in self.connected
        cc._health = lambda cid, key: self.health.get(key, "")
        cc._db = lambda: self.db
        self.clock = datetime(2026, 11, 9, tzinfo=timezone.utc)
        cc._now = lambda: self.clock
        from core import chat_fallback, client_language
        self.saved_alert, self.saved_resolve = chat_fallback.alert_once, client_language.resolve
        self.alerts = []
        chat_fallback.alert_once = lambda *a, **k: self.alerts.append(a) or True
        client_language.resolve = lambda cid, lang=None: lang or "he"

    def tearDown(self):
        cc.rows, cc.is_connected, cc._db, cc._now, cc._health = self.saved
        from core import chat_fallback, client_language
        chat_fallback.alert_once, client_language.resolve = self.saved_alert, self.saved_resolve

    def _press(self, key, step, at, lang="he"):
        """The api route (core/api_server.client_connect_chat): the chip in, the messages out."""
        self.clock = datetime.fromisoformat(at).replace(tzinfo=timezone.utc)
        result = cc.flow(27, key, step, lang=lang)
        rows = self.db.tables.setdefault("client_communications", [])
        channel = "dashboard_chat:media"
        chip = cc.chip_label(step, lang)
        if result.get("show") and chip:
            rows.append({"client_id": 27, "channel": channel, "direction": "inbound", "content": chip,
                         "created_at": self.clock.isoformat()})
        for i, text in enumerate(result.get("messages") or []):
            rows.append({"client_id": 27, "channel": channel, "direction": "outbound", "content": text,
                         "created_at": (self.clock + timedelta(seconds=i + 1)).isoformat()})
        return result

    def _asked(self, name="TikTok"):
        q = cc.T["start"]["he"].format(name=name)
        return sum(1 for r in self.db.tables.get("client_communications", []) if r["content"] == q)

    def test_connected_channels_are_never_offered(self):
        self.connected = {(27, "tiktok"), (27, "youtube")}
        for at in SEQUENCE:
            r = self._press("tiktok", "start", at)
            self.assertTrue(r["connected"])
            self.assertEqual(r["chips"], [])
            self.assertIn("כבר מחובר", r["messages"][0])
        for at in ("2026-11-11T10:00", "2026-11-13T10:00"):
            r = self._press("youtube", "start", at)
            self.assertNotIn("יש לכם כבר חשבון", r["messages"][0])
        self.assertEqual((self._asked("TikTok"), self._asked("YouTube")), (0, 0))

    def test_not_connected_the_question_is_raised_once(self):
        for at in SEQUENCE[:5]:      # 09/11 ×4 and 10/11, never answered
            self._press("tiktok", "start", at)
        self.assertEqual(self._asked(), 1)
        repeat = self._press("tiktok", "start", "2026-11-10T12:00")
        self.assertEqual(repeat["messages"], [])
        self.assertEqual([c["step"] for c in repeat["chips"]], ["has", "none"])   # the chips again, alone

    def test_after_i_have_an_account_never_asked_again(self):
        self._press("tiktok", "start", SEQUENCE[0])
        self._press("tiktok", "has", "2026-11-09T08:01")     # "יש לי חשבון", the approval never completed
        for at in SEQUENCE[1:]:
            r = self._press("tiktok", "start", at)
            self.assertTrue(r.get("stuck"))
            self.assertEqual(r["connect"], "tiktok")          # the connect opens directly
            self.assertIn("לא הושלם", r["messages"][0])        # what blocks it
            self.assertEqual(r["chips"], [])
        self.assertEqual(self._asked(), 1)
        self.assertTrue(self.alerts)                           # the team hears it is stuck

    def test_the_whole_sequence_asks_once(self):
        """09/11 ×4 → 10/11 → (a week later) 17/11 ×3 → 18/11, never answered:
        once, and once more only after PROMPT_CAP_DAYS with no answer."""
        for at in SEQUENCE:
            self._press("tiktok", "start", at)
        self.assertEqual(self._asked(), 2)                     # 09/11, then 17/11 (8 days later)
        self.assertLessEqual(self._asked(), 1 + len(SEQUENCE) // cc.PROMPT_CAP_DAYS)

    def test_connected_but_blocked_names_the_blocker(self):
        self.connected = {(27, "tiktok")}
        self.health = {"tiktok": "upload_permission"}
        r = self._press("tiktok", "start", SEQUENCE[0])
        self.assertEqual(r["connect"], "tiktok")
        self.assertIn("בלי ההרשאה לשלוח סרטונים", r["messages"][0])
        self.assertEqual(self._asked(), 0)

    def test_every_line_in_five_languages(self):
        for key, table in cc.T.items():
            self.assertEqual(set(table), set(cc.LANGS), key)
        for step, labels in cc.CHIPS.items():
            self.assertEqual(set(labels), set(cc.LANGS), step)
        self.assertEqual(cc.chip_label("has", "fr"), "J'ai un compte")


# ─── PART 2: the 3 posts, produced on their own ─────────────────────────────

class ProduceRequested(unittest.TestCase):
    def setUp(self):
        import types
        self.calls = []
        fake = types.ModuleType("agents.media_agent")
        fake._NO_ACCOUNT_ERROR = "no account"
        fake.generate_image = lambda cid, brief, platform="instagram": (
            self.calls.append(("image", brief, platform)) or {"success": True, "link": f"https://drive/{len(self.calls)}"})
        fake.generate_video = lambda cid, brief, platform="instagram": (
            self.calls.append(("video", brief, platform)) or {"success": True, "link": "https://drive/v"})
        self.saved_media = sys.modules.get("agents.media_agent")
        sys.modules["agents.media_agent"] = fake
        import agents
        self.saved_attr = getattr(agents, "media_agent", None)
        agents.media_agent = fake
        self.saved = (aa._media_tool_status,)
        self.tool = "connected"
        aa._media_tool_status = lambda cid: self.tool
        from core import client_state
        self.saved_pkg = client_state.cached_package
        client_state.cached_package = lambda cid: {"readable": True, "services": ["content & media"]}

    def tearDown(self):
        (aa._media_tool_status,) = self.saved
        sys.modules["agents.media_agent"] = self.saved_media
        import agents
        agents.media_agent = self.saved_attr
        from core import client_state
        client_state.cached_package = self.saved_pkg

    PIECES = [{"kind": "image", "brief": "Reason 1: a design made only for them"},
              {"kind": "image", "brief": "Reason 2: every detail tells their story"},
              {"kind": "image", "brief": "Reason 3: a gift no shelf can sell"}]

    def test_spec(self):
        spec = aa.ACTIONS["produce_requested_content"]
        self.assertEqual(spec["approver"], "client")
        self.assertTrue(spec["client_requestable"])
        self.assertFalse(spec["internal_only"])
        self.assertIn("produce_requested_content", aa.STOP_FINISH)
        for field in ("title", "what", "done"):
            self.assertEqual(set(spec[field]), {"he", "en", "fr", "ar", "ru"}, field)
            for text in spec[field].values():
                self.assertNotRegex(text, re.compile(r"higgsfield|heygen|eleven\s?labs", re.I))
                self.assertNotRegex(text, re.compile(r"publish(?:ed)? (?:to|on) ", re.I))

    def test_three_posts_three_pieces_one_card(self):
        params = aa._v_pieces(self.PIECES, 27)
        self.assertEqual([p["kind"] for p in params], ["image"] * 3)
        self.assertEqual({p["platform"] for p in params}, {"instagram"})
        prepared = aa._prepare_produce(27, {"pieces": params}, {"language": "he"}, [])
        self.assertEqual(prepared, {"params": {"pieces": params}})
        shown = aa._produce_display(27, {"pieces": params})
        self.assertEqual(shown["count"], "3")
        self.assertEqual(shown["pieces"].count("\n"), 2)

    def test_run_each_piece_to_drive_nothing_published(self):
        result = aa._run_produce(27, {"pieces": aa._v_pieces(self.PIECES, 27)}, None)
        self.assertEqual([c[0] for c in self.calls], ["image"] * 3)
        self.assertEqual(result["made"], 3)
        text = aa._produce_done_message(result, "he")
        self.assertIn("Drive", text)
        self.assertIn("שום דבר לא פורסם", text)

    def test_the_tool_missing_switches_to_its_card(self):
        self.tool = "not_connected"
        prepared = aa._prepare_produce(27, {"pieces": aa._v_pieces(self.PIECES, 27)}, {"language": "en"}, [])
        self.assertEqual(prepared["switch_to"], "connect_creative_tool")
        self.assertIn("separate from the plan", prepared["notice"])
        self.assertNotRegex(prepared["notice"], re.compile("higgsfield", re.I))

    def test_bad_pieces_refused(self):
        for bad in ([], [{"kind": "gif", "brief": "a long enough brief"}], [{"kind": "image", "brief": "x"}],
                    [{"kind": "image", "brief": "a long enough brief"}] * 7):
            with self.assertRaises(ValueError):
                aa._v_pieces(bad, 27)


# ─── PART 3 + 5: the prompts ────────────────────────────────────────────────

class Prompts(unittest.TestCase):
    def test_no_fixed_opener(self):
        for pid, persona in sa.PERSONAS.items():
            opening = next(l for l in persona["voice"].splitlines() if l.startswith("- Opening:"))
            self.assertNotIn("(\"", opening, pid)            # no quoted template opener to copy
        lior = sa.PERSONAS["media"]["voice"]
        self.assertNotIn("Opening: starts from a picture", lior)
        self.assertNotIn("almost every time", lior)
        self.assertIn("No fixed opener", lior)

    def test_scope_and_cant_rules(self):
        template = sa.PERSONA_SYSTEM_TEMPLATE
        self.assertIn("A SPECIALIST, NOT THE ORCHESTRATOR", template)
        self.assertIn("A SPECIFIC PIECE IS NOT A PLAN", template)
        self.assertIn("\"I CAN'T\" ONLY WHEN IT IS TRUE", template)
        self.assertIn("produce_requested_content", sa.PERSONAS["media"]["data_notes"])
        self.assertIn("ONLY when the client explicitly asks", open(os.path.join(ROOT, "core", "content_team_actions.py"),
                                                                     encoding="utf-8").read())
        template.format(name="n", domain="d", voice="v", data_notes="x", action_exception="", action_rule="",
                        extra_fields="")   # still formats


if __name__ == "__main__":
    unittest.main()
