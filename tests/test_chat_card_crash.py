"""The KeyError 'validate_stored' in client 27's meta chat (2026-11-16).

Client 27 asked מאיה: "אני מוכן לקחת את הנוכחות שלנו ברשתות צעד קדימה. מה הצעד
הבא?" The model proposed `build_content_plan`; its prepare switched to
`approve_content_plan`, which has a prepare hook and had no `validate_stored`,
and `agent_actions.propose` read `spec["validate_stored"]` → KeyError → the
whole persona turn failed → the client saw the fixed "difficulty" line.
The Saturday content-plan run opened the same card the same way.

What must stay true:
- the exact message, replayed through the real propose path, gives the plan card;
- an action with a prepare hook and no stored-row validator falls back to the
  ordinary param check (never a KeyError);
- a crash inside ANY card's code keeps the model's reply, takes the card back
  honestly and alerts — never the technical line, never a dead end;
- the fixed technical line never says "difficulty" and every lane's tail is a
  real next step, in 5 languages.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_content_team as tct                # noqa: E402  (stubs + FakeDB)
from agents import media_agent, support_agent  # noqa: E402
from core import agent_actions as aa           # noqa: E402
from core import chat_fallback                 # noqa: E402
import core.israel_calendar as calendar        # noqa: E402

MESSAGE = "אני מוכן לקחת את הנוכחות שלנו ברשתות צעד קדימה. מה הצעד הבא?"
LANGS = ("he", "en", "fr", "ar", "ru")


class Replay(tct.Base):
    def setUp(self):
        super().setUp()
        # Base fakes propose; this test needs the REAL one (that is where the bug was)
        self.real_propose = next(v for m, n, v in self.saved if m is aa and n == "propose")
        aa.propose = self.real_propose
        self.more = [(media_agent, "plan_week", media_agent.plan_week),
                     (calendar, "upcoming_events", calendar.upcoming_events),
                     (support_agent, "_client_language", support_agent._client_language),
                     (chat_fallback, "alert_handoff", chat_fallback.alert_handoff),
                     (chat_fallback, "alert_capability_gap", chat_fallback.alert_capability_gap)]
        media_agent.plan_week = lambda client, events, request="": tct.ITEMS
        calendar.upcoming_events = lambda *a, **k: []
        support_agent._client_language = lambda cid, msg="": "he"
        self.handoffs = []
        chat_fallback.alert_handoff = lambda *a, **k: self.handoffs.append((a, k)) or True
        chat_fallback.alert_capability_gap = lambda *a, **k: self.handoffs.append((a, k)) or True

    def tearDown(self):
        for module, name, value in self.more:
            setattr(module, name, value)
        super().tearDown()

    def turn(self, proposed):
        model = {"reply": "הצעד הבא הוא תוכנית תוכן שבועית. הכנתי לך אותה לאישור.",
                 "proposed_action": proposed}
        return support_agent._route_proposal(27, "meta", MESSAGE, model, model["reply"], None,
                                             {"id": 27, "language": "he", "billing_exempt": True})

    def test_the_exact_message_gives_the_plan_card(self):
        out = self.turn({"name": "build_content_plan", "params": {}, "summary": ""})
        self.assertTrue(out.get("action"), out)
        self.assertEqual(out["action"].get("action"), "approve_content_plan")
        self.assertNotIn("קושי", out["reply"])
        self.assertEqual(self.handoffs, [])

    def test_the_saturday_path_opens_the_card_too(self):
        out = tct.ct.propose(27, tct.ITEMS, "he", persona="meta")
        self.assertTrue(out.get("card"), out)

    def test_a_crash_in_card_code_keeps_the_reply(self):
        def boom(*a, **k):
            raise KeyError("validate_stored")
        aa.propose = boom
        out = self.turn({"name": "build_content_plan", "params": {}, "summary": ""})
        self.assertIn("תוכנית תוכן שבועית", out["reply"])                      # the model's answer stays
        self.assertIn(aa._pick(aa.NO_CARD, "he"), out["reply"])                # the card is taken back
        self.assertNotIn(chat_fallback._TECHNICAL_OPENING["he"], out["reply"])
        self.assertEqual(len(self.handoffs), 1)                                 # and we are told
        self.assertIn("KeyError", self.handoffs[0][0][2])


class Registry(unittest.TestCase):
    def test_prepare_without_validate_stored_falls_back(self):
        src = open(os.path.join(ROOT, "core", "agent_actions.py"), encoding="utf-8").read()
        self.assertNotIn('spec["validate_stored"](client_id, params)', src)
        self.assertIn('validator = spec.get("validate_stored")', src)

    def test_every_prepared_card_has_its_validator(self):
        missing = [n for n, s in aa.ACTIONS.items() if s.get("prepare") and not s.get("validate_stored")]
        self.assertEqual(missing, [])


class TechnicalLine(unittest.TestCase):
    def test_no_difficulty_and_a_real_next_step(self):
        for lang in LANGS:
            line = chat_fallback._TECHNICAL_OPENING[lang]
            for word in ("קושי", "trouble", "du mal", "صعوبة", "не получается"):
                self.assertNotIn(word, line, lang)
        for persona in ("general", "google", "meta", "website", "media"):
            self.assertEqual(set(chat_fallback._TAILS[persona]), set(LANGS), persona)
            for lang in LANGS:
                reply = chat_fallback.technical_reply(persona, lang)
                self.assertFalse(chat_fallback.has_team_routing(reply), (persona, lang))
                self.assertNotIn("נסות שוב", reply)


if __name__ == "__main__":
    unittest.main()
