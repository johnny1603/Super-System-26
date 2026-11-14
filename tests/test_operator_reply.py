"""איציק: the reply text always carries the findings (bug 2026-11-11).

The owner asked twice and got the same line twice: "סיימתי לבדוק — הפרטים
בכרטיסים למטה." with NO card under it. Cause: the final model step came back
without text (thinking only / everything said next to a tool call), and
operator_agent.ask replaced the empty text with that fixed line whether or not
a card existed; the line was then fed back as history, so the model learned it.

Here: (a) the findings arrive as text in the message itself, (b) a card the
chat cannot draw becomes text, (c) no "details below" unless a drawable card
is attached. docs/OPERATOR.md §Reply text.

Stdlib only. Run from tests/:  python -I -B test_operator_reply.py -v
"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_operator as T                          # noqa: E402  (in-memory DB + stubs)
import test_operator_budget as B                   # noqa: E402  (FakeClient, replies)
from core import operator_agent as op              # noqa: E402

LEGACY = "סיימתי לבדוק — הפרטים בכרטיסים למטה."
QUESTION = "למה הלידים של לקוח 27 הפסיקו להגיע?"      # a read / diagnose message


def empty_reply():
    """The production case: the last step ends with no text (thinking only)."""
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="thinking", thinking="", signature="s")],
                                 stop_reason="end_turn", usage=B._usage())


def said_and_tool(text, name, args=None):
    """A step that states findings AND calls a tool in the same response."""
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text),
                                          types.SimpleNamespace(type="tool_use", id="tu9", name=name,
                                                                input=args or {})],
                                 stop_reason="tool_use", usage=B._usage())


def no_pointer(test, text):
    test.assertNotIn(LEGACY, text)
    test.assertNotRegex(text, r"(כרטיס\w*|פרטים)[^.\n]*למטה")


class Replay(B.Base):
    def setUp(self):
        super().setUp()
        self.ran = []

        def fake_tool(name, args, turn):
            self.ran.append(name)
            if name == "write_report":
                report = {"title": "לידים של לקוח 27", "summary_he": "הטופס באתר שובר את השליחה מאז אתמול.",
                          "cause_he": "שינוי בתבנית הטופס", "evidence": ["log 10:02 form 500"]}
                turn.cards.append({"type": "report", "report": report, "message_id": None})
                return {"saved": True}
            return {"alerts": [{"text": "form submit 500 since 10:02"}]}
        self._patch(op, "run_tool", fake_tool)

    def test_exact_case_empty_final_answer_twice(self):
        # Ask #1: a read, then the final step is empty → the findings call answers
        fake = self.client(B.tool_reply("recent_alerts"), empty_reply(),
                           B.text_reply("מצאתי: שליחת הטופס נכשלת (500) מאז 10:02. אני ממליץ לבדוק את התבנית."))
        out = op.ask(QUESTION, "admin")
        self.assertIn("שליחת הטופס נכשלת", out["reply"])
        no_pointer(self, out["reply"])
        self.assertEqual(out["cards"], [])
        last = fake.calls[-1]
        self.assertEqual(last["tool_choice"], {"type": "none"})
        self.assertIn("Do NOT call tools", str(last["messages"][-1]["content"]))
        # Ask #2, same question, the old bad line sits in history: it is NOT fed back
        T.DB.tables.setdefault(op.TABLE, []).extend([
            {"id": 900, "created_at": "2026-11-11T09:00:00", "role": "owner", "kind": "message",
             "content": QUESTION, "cards": []},
            {"id": 901, "created_at": "2026-11-11T09:01:00", "role": "operator", "kind": "message",
             "content": LEGACY, "cards": []}])
        fake = self.client(B.tool_reply("recent_alerts"), empty_reply(), empty_reply())
        out = op.ask(QUESTION, "admin")
        self.assertNotIn(LEGACY, str(fake.calls[0]["messages"]))
        no_pointer(self, out["reply"])
        self.assertIn("לא הצלחתי לנסח תשובה", out["reply"])        # honest, no cards claimed
        self.assertIn("התראות אחרונות", out["reply"])               # what was checked, in words
        stored = self.stored()[-1]
        self.assertEqual(stored["content"], out["reply"])

    def test_findings_said_next_to_the_report_reach_the_text(self):
        # The model wrote the findings with its write_report call, then nothing
        self.client(B.tool_reply("recent_alerts"), said_and_tool("הטופס שבור מאז 10:02.", "write_report"),
                    empty_reply(), empty_reply())
        out = op.ask(QUESTION, "admin")
        self.assertIn("שובר את השליחה", out["reply"])        # the report's own content, as text
        self.assertFalse([c for c in out["cards"] if c["type"] == "report"])   # not drawn twice
        no_pointer(self, out["reply"])

    def test_model_pointer_without_a_card_is_removed(self):
        self.client(B.tool_reply("recent_alerts"), B.text_reply(LEGACY))
        out = op.ask(QUESTION, "admin")
        no_pointer(self, out["reply"])
        self.assertTrue(out["reply"].strip())

    def test_findings_call_respects_the_cap(self):
        seen = []
        # the two loop steps fit; the extra findings call would not
        self._patch(op, "estimate_step_usd", lambda *a, **k: 0.0 if seen.append(1) or len(seen) <= 2 else 10.0)
        fake = self.client(B.tool_reply("recent_alerts"), empty_reply())
        out = op.ask(QUESTION, "admin")
        self.assertEqual(len(fake.calls), 2)
        no_pointer(self, out["reply"])


class Compose(unittest.TestCase):
    def test_unknown_card_becomes_text(self):
        text, cards = op.compose_reply("בדקתי.", [{"type": "chart", "label_he": "גרף לידים: ירידה של 80%"}], [])
        self.assertEqual(cards, [])
        self.assertIn("גרף לידים: ירידה של 80%", text)

    def test_broken_card_becomes_text(self):
        text, cards = op.compose_reply("", [{"type": "change", "change": "oops", "label_he": "תיקון"}], ["read_code"])
        self.assertEqual(cards, [])
        self.assertIn("תיקון", text)

    def test_pointer_kept_only_with_a_real_card(self):
        card = {"type": "action", "action": "rollback", "params": {}, "label_he": "חזרה לגרסה הקודמת",
                "reason_he": "שגיאות מאז הפריסה", "undo_he": "x"}
        text, cards = op.compose_reply("שמתי כפתור, הפרטים בכרטיס למטה.", [card], ["cloud_run_status"])
        self.assertEqual(cards, [card])
        no_pointer(self, op.compose_reply("הפרטים בכרטיס למטה.", [], [])[0])

    def test_empty_reply_with_a_card_says_what_it_is(self):
        card = {"type": "action", "action": "rollback", "params": {}, "label_he": "חזרה לגרסה הקודמת",
                "reason_he": "שגיאות מאז הפריסה"}
        text, cards = op.compose_reply("", [card], ["cloud_run_status"])
        self.assertIn("חזרה לגרסה הקודמת", text)
        self.assertIn("שגיאות מאז הפריסה", text)

    def test_ordinary_card_words_are_not_stripped(self):
        line = "כרטיס הפעולה של לקוח 3 תקוע ב-running כבר שעתיים."
        self.assertEqual(op.strip_card_pointers(line), line)


class Source(unittest.TestCase):
    def test_no_fixed_fallback_line(self):
        src = B._src("core", "operator_agent.py")
        self.assertNotIn('task.reply or "', src)
        self.assertNotIn(LEGACY, src)

    def test_chat_draws_unknown_cards_as_text(self):
        js = B._src("dashboard", "assets", "itzik-chat.js")
        self.assertIn("function cardText(c)", js)
        body = js.split("function renderCard(c)")[1].split("\n  }\n")[0]
        self.assertIn("try {", body)
        self.assertIn("cardText(c)", body)
        self.assertSetEqual(set(op.RENDERED_CARD_TYPES), {"action", "budget", "done", "change", "report", "idea"})
        for t in op.RENDERED_CARD_TYPES:
            self.assertIn(f"'{t}'", js)


if __name__ == "__main__":
    unittest.main()
