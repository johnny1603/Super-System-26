"""ליאור as a real brand designer (2026-11-16, docs/BRANDING.md §The designer conversation).

What must stay true:
- before ANY mockup: a human conversation, one question at a time, each a
  proposal, three layers (general, the trade, open taste and inspiration),
  a one-or-two-line summary and "did I get that right?";
- code enforces the end of it: no business name or field from the owner =
  the card asks for it; no "yes" in the client's message = the card asks the
  summary question; only a yes opens the card, and the card shows what was
  understood;
- the name on the mockups is the one the owner confirmed, never the account
  name (often the owner's own name), never another client's;
- 5 free attempts, mockups only, no website building: unchanged.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_branding as tb                  # noqa: E402  (stubs, FakeDB, the Base)
from core import agent_actions as aa        # noqa: E402
from core import branding                   # noqa: E402

BRIEF = {"business_name": "נגריית אלון", "field": "נגרות בהזמנה אישית לבתים פרטיים",
         "understood": "נגרייה בעבודת יד, חמה וטבעית, לא תעשייתית"}


class Confirmation(unittest.TestCase):
    def test_yes_in_five_languages_and_a_no_wins(self):
        for yes in ("כן", "כן, בדיוק", "נכון מאוד", "סגור, יאללה", "yes", "Exactly right", "oui, parfait",
                    "نعم صحيح", "да, верно"):
            self.assertTrue(branding.confirms(yes), yes)
        for no in ("לא נכון", "כן אבל לא", "not quite", "no, warmer", "non", "لا", "нет", "", "תעשה משהו כחול"):
            self.assertFalse(branding.confirms(no), no)
        self.assertFalse(branding.confirms("כן " + "ו" * 200))              # a long message is a brief, not a yes


class Flow(tb.Base):
    def propose(self, message, **params):
        return aa.propose(27, "client_chat", "media",
                          {"name": "create_brand_mockups", "params": {"direction": "אלון וגרפיט, לוגו נקי", **params}},
                          context={"language": "he", "message": message})

    def test_no_name_no_field_no_yes_no_card(self):
        self.assertIn("איך בדיוק קוראים לעסק", self.propose("כן")["clarify"])
        self.assertIn("מה העסק עושה", self.propose("כן", business_name="נגריית אלון")["clarify"])
        asked = self.propose("תעשה כבר", **BRIEF)
        self.assertNotIn("card", asked)
        self.assertIn(BRIEF["understood"], asked["clarify"])
        self.assertIn("הבנתי נכון?", asked["clarify"])
        self.assertEqual(self.calls, [])                                     # nothing designed on the way

    def test_a_yes_opens_the_card_with_what_was_understood(self):
        card = self.propose("כן, בדיוק", **BRIEF)["card"]
        self.assertIn("נגריית אלון", card["what"])
        self.assertIn(BRIEF["understood"], card["what"])
        done = aa.decide(card["id"], "client", 27, "approve")
        self.assertEqual(done["status"], "done")
        attempt = branding.attempt(27, 1)
        self.assertEqual(attempt["concept"]["name"], "נגריית אלון")         # the confirmed name, not the record's
        self.assertEqual(attempt["brief"]["field"], BRIEF["field"])
        self.assertEqual(branding.summary(27)["confirmed_brief"]["business_name"], "נגריית אלון")

    def test_the_next_attempt_reuses_the_confirmed_brief(self):
        aa.decide(self.propose("כן", **BRIEF)["card"]["id"], "client", 27, "approve")
        out = branding.create_attempt(27, "אותו דבר עם ירוק זית", "he")
        self.assertEqual(out["concept"]["name"], "נגריית אלון")


class NeverInvented(unittest.TestCase):
    def test_the_account_name_is_never_the_business_name(self):
        src = open(os.path.join(ROOT, "core", "branding.py"), encoding="utf-8").read()
        facts = src[src.index("def _client_facts"):src.index("def pick_model")]
        self.assertIn('name = client.get("business_name") or ""', facts)
        self.assertNotIn('client.get("name")', facts)

    def test_the_prompt_rules(self):
        support = open(os.path.join(ROOT, "agents", "support_agent.py"), encoding="utf-8").read()
        rule = support[support.index('STUDIO_RULE = """'):support.index('ACCOUNT_STATE_RULE = """')]
        for needle in ("ONE question per message", "NEVER A FORM", "EVERY QUESTION IS A PROPOSAL", "THREE LAYERS",
                       "Their TRADE", "including an image they share", "You cannot see", "NEVER INVENT",
                       "the owner's own name", "did I get that right?", "branding.confirmed_brief",
                       "MOCKUPS ONLY", "NEVER includes"):
            self.assertIn(needle, rule, needle)
        self.assertNotIn("{", rule)

    def test_card_texts_in_five_languages(self):
        what = aa.ACTIONS["create_brand_mockups"]["what"]
        for lang in tb.LANGS:
            for placeholder in ("{business_name}", "{understood}", "{direction}", "{n}", "{free}"):
                self.assertIn(placeholder, what[lang], (lang, placeholder))


if __name__ == "__main__":
    unittest.main()
