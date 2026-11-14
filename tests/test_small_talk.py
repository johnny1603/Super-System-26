"""Small talk is not work (2026-11-15, docs/CHAT-SPEED.md §Small talk).

A. Rules (core/chat_route, no model call): "מה קורה?", "מה הולך?", "היי",
   "מה נשמע", "תודה", "אחלה" and their equivalents in he/en/fr/ar/ru are the
   SIMPLE kind — cheapest tier, no account reads. A request hidden inside a
   greeting takes the full path. Doubt still goes UP.
B. The turn: no collection wait (core/chat_turns), no account context, nothing
   proposed is acted on; the note asks for a human line, not a status report.
C. The dashboard: its typing indicator never becomes the progress line or a
   time estimate for small talk (the real page script, run in Node).

Stdlib only. Run from the repo root:  python -I -B tests/test_small_talk.py -v
"""
import json
import os
import shutil
import subprocess
import sys
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
NODE = shutil.which("node")

from core import chat_route as cr      # noqa: E402

# The owner's exact phrases, then the same in every supported language
EXACT = ("מה קורה?", "מה הולך?", "היי", "מה נשמע", "תודה", "אחלה")
GREETINGS = {
    "he": ("מה קורה?", "מה הולך?", "היי", "מה נשמע", "היי מה קורה?", "הייי", "בוקר טוב!", "שלום", "אהלן, מה נשמע?",
           "מה שלומך?", "היי ג'וני, מה קורה?", "מה העניינים"),
    "en": ("hi", "hey", "Hello!", "what's up?", "whats up", "how are you?", "Hey, how's it going?", "good morning",
           "hi Maya, how are you doing?", "sup"),
    "fr": ("bonjour", "salut", "ça va ?", "Salut, ça va?", "comment ça va", "bonsoir !", "coucou"),
    "ar": ("مرحبا", "كيف حالك؟", "السلام عليكم", "أهلا", "صباح الخير", "مرحبا، كيفك؟", "شو الأخبار"),
    "ru": ("привет", "Привет, как дела?", "как дела", "здравствуйте", "добрый день!", "как ты?"),
}
ACKS = {
    "he": ("תודה", "אחלה", "תודה רבה!", "סבבה", "מעולה, תודה", "אחלה 👍", "כל הכבוד", "מושלם", "תודה מאיה"),
    "en": ("thanks", "thank you so much", "great", "awesome, thanks!", "ok", "nice", "cool 👍"),
    "fr": ("merci", "merci beaucoup", "super, merci !", "parfait", "d'accord"),
    "ar": ("شكرا", "شكرا جزيلا", "تمام", "ممتاز", "تمام، شكرا"),
    "ru": ("спасибо", "спасибо большое", "отлично", "супер, спасибо!", "хорошо"),
}
HIDDEN_REQUESTS = (
    "היי, תעלה לי את הסרטון", "היי תעלה לי את הסרטון", "מה קורה? תבנה לי דף נחיתה", "תודה, עכשיו תשנה את התקציב",
    "אחלה, תפרסם את זה", "היי, אפשר לקבל דוח?", "מה נשמע, מה עם הקמפיין שלי?", "היי יש לי שאלה",
    "hi, upload my video", "hey can you send the report", "thanks, now publish it", "hello, change the budget",
    "bonjour, publie la vidéo", "salut, envoie le rapport s'il te plaît", "merci, maintenant change le budget",
    "مرحبا، ارفع الفيديو", "شكرا، انشر الإعلان الآن", "привет, загрузи видео", "спасибо, теперь опубликуй пост",
)


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Rules(unittest.TestCase):
    def route(self, message, last=""):
        return cr.classify(message, last)

    def test_the_exact_phrases_are_simple(self):
        for m in EXACT:
            r = self.route(m)
            self.assertEqual((r["kind"], r["tier"]), ("simple", "simple"), m)
            self.assertTrue(r.get("small_talk"), m)

    def test_greetings_in_all_five_languages(self):
        self.assertEqual(set(GREETINGS), {"he", "en", "fr", "ar", "ru"})
        for lang, phrases in GREETINGS.items():
            for m in phrases:
                self.assertEqual(cr.small_talk(m), "greeting", f"{lang}: {m}")
                self.assertEqual(self.route(m)["kind"], "simple", f"{lang}: {m}")

    def test_thanks_and_acknowledgements_in_all_five_languages(self):
        self.assertEqual(set(ACKS), {"he", "en", "fr", "ar", "ru"})
        for lang, phrases in ACKS.items():
            for m in phrases:
                self.assertEqual(cr.small_talk(m), "ack", f"{lang}: {m}")
                self.assertEqual(self.route(m)["kind"], "simple", f"{lang}: {m}")

    def test_a_request_hidden_in_a_greeting_takes_the_full_path(self):
        for m in HIDDEN_REQUESTS:
            self.assertEqual(cr.small_talk(m), "", m)
            r = self.route(m)
            self.assertNotEqual(r["kind"], "simple", m)
            self.assertFalse(r.get("small_talk"), m)
        self.assertEqual(self.route("היי, תעלה לי את הסרטון")["kind"], "work")
        self.assertEqual(self.route("היי, תעלה לי את הסרטון")["tier"], "heavy")

    def test_a_greeting_is_never_a_yes_but_an_ack_after_an_offer_goes_up(self):
        offer = "רוצה שאעלה את הסרטון עכשיו?"
        for m in ("היי", "מה קורה?", "מה נשמע", "hello", "привет"):
            self.assertEqual(self.route(m, offer)["kind"], "simple", m)
        for m in ("אחלה", "תודה", "סבבה", "ok", "great", "d'accord", "تمام", "хорошо"):
            self.assertEqual(self.route(m, offer)["kind"], "work", m)       # may be a yes: doubt goes up
            self.assertEqual(self.route(m, "סיימתי, הכול מוכן.")["kind"], "simple", m)

    def test_doubt_still_goes_up(self):
        for m in ("הממ", "ג'וני", "Maya", "לגבי מה שדיברנו", "מה קורה עם הקמפיין?", "מה המצב?", "מה חדש?",
                  "היי\nתעלה את הסרטון", "כן", "yes", "יאללה", "היי " * 30):
            self.assertNotEqual(self.route(m)["kind"], "simple", m)
        self.assertEqual(self.route("מה קורה עם הקמפיין?")["kind"], "read")
        # a frustrated client's "היי" is never brushed off
        self.assertEqual(cr.classify("היי", "", frustrated=True)["kind"], "work")

    def test_rules_only_no_model_and_no_model_names(self):
        code = _src("core", "chat_route.py")
        for banned in ("anthropic", "claude_json", "safe_claude", "claude-", "requests", "httpx"):
            self.assertNotIn(banned, code, banned)

    def test_the_note_asks_for_a_person_not_a_status_report(self):
        note = cr.SIMPLE_NOTE
        for needed in ("small talk", "NOT a status", "ONE short line", "NOT loaded", "propose no"):
            self.assertIn(needed, note, needed)


class Turn(unittest.TestCase):
    """core/chat_turns: small talk does not wait for the collection window."""

    def setUp(self):
        self.saved = sys.modules.get("agents.client_agent")
        self.rows = []
        sys.modules["agents.client_agent"] = types.SimpleNamespace(
            __name__="agents.client_agent", get_communications=lambda *a, **k: list(self.rows),
            _db=lambda: (_ for _ in ()).throw(RuntimeError("no db")))
        from core import chat_turns as t
        self.t = t
        t._db_lease_ok[0] = False
        t._locks.clear(); t._arrivals.clear(); t._covered.clear()
        self.saved_window = t.window_seconds
        t.window_seconds = lambda: 1.2

    def tearDown(self):
        self.t.window_seconds = self.saved_window
        self.t._db_lease_ok[0] = True
        if self.saved is None:
            sys.modules.pop("agents.client_agent", None)
        else:
            sys.modules["agents.client_agent"] = self.saved

    def _queue(self, text, row_id):
        row = {"id": row_id, "direction": "inbound", "content": text}
        self.rows.append(row)
        self.t.note_arrival(9, "c")
        started = time.monotonic()
        with self.t.Turn(9, "c", row, text) as turn:
            waited = time.monotonic() - started
            turn.done()
            return waited, turn.message

    def test_small_talk_is_answered_at_once_and_real_messages_still_collect(self):
        for i, m in enumerate(EXACT, start=1):
            waited, message = self._queue(m, i)
            self.assertLess(waited, 0.4, m)
            self.assertEqual(message, m)
        waited, _ = self._queue("תעלה את הסרטון", 50)
        self.assertGreaterEqual(waited, 1.0)                 # the window is untouched for real work
        waited, _ = self._queue("היי, תעלה לי את הסרטון", 51)
        self.assertGreaterEqual(waited, 1.0)

    def test_wiring_of_the_light_turn(self):
        agent = _src("agents", "support_agent.py")
        self.assertIn('chat_timing.flag("small_talk", True)', agent)
        self.assertEqual(agent.count('light = decision["kind"] == "simple"'), 2)     # manager + specialists
        light = agent[agent.index("def _light_payload"):agent.index("def _strip_light_result")]
        self.assertIn("chat_route.SIMPLE_NOTE", light)
        self.assertNotIn("_load_context", light)                                     # no account reads
        strip = agent[agent.index("def _strip_light_result"):agent.index("def _suggestions_block")]
        for key in ("proposed_action", "upgrade_request", "web_search_query"):       # no card, no search
            self.assertIn(key, strip)
        self.assertIn("chat_route.small_talk(self.own_text)", _src("core", "chat_turns.py"))


@unittest.skipUnless(NODE, "node is not installed")
class Dashboard(unittest.TestCase):
    """The page's own check, cut out of the real file and run in Node."""

    @classmethod
    def setUpClass(cls):
        page = _src("dashboard", "client", "index.html")
        cls.page = page
        block = "const window = {};\n" + _src("dashboard", "assets", "small-talk.js") \
            + "\nconst chatSmallTalk = window.uallakSmallTalk;\n"
        cases = list(EXACT) + [m for v in GREETINGS.values() for m in v] + [m for v in ACKS.values() for m in v]
        work = list(HIDDEN_REQUESTS) + ["Maya", "ג'וני", "היי\nתעלה את הסרטון", "מה המצב?", "כן"]
        script = block + "\nconst cases = " + json.dumps({"talk": cases, "work": work}) + ";\n" \
            "console.log(JSON.stringify({talk: cases.talk.filter(m => !chatSmallTalk(m)), " \
            "work: cases.work.filter(m => chatSmallTalk(m))}));"
        done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if done.returncode != 0:
            raise AssertionError(done.stderr)
        cls.result = json.loads(done.stdout)

    def test_the_exact_phrases_never_show_the_progress_line(self):
        missed = [m for m in self.result["talk"] if m in EXACT]
        self.assertEqual(missed, [])

    def test_the_page_never_calls_a_real_request_small_talk(self):
        self.assertEqual(self.result["work"], [])

    def test_most_of_the_server_list_is_known_to_the_page(self):
        # display only: a phrase the page misses just behaves as before
        self.assertLessEqual(len(self.result["talk"]), 12, self.result["talk"])

    def test_wiring(self):
        text = self.page[self.page.index("function chatTypingText(w) {"):][:300]
        self.assertIn("if (w.smallTalk) return uallakI18n.t('chat_typing');", text)
        self.assertIn("w.smallTalk = chatSmallTalk(text) && !extra && (!w.pending || w.smallTalk);", self.page)
        self.assertIn("w.pendingSince = 0; w.smallTalk = false;", self.page)
        self.assertEqual(self.page.count('<script src="/assets/small-talk.js" defer></script>'), 1)
        self.assertIn("window.uallakSmallTalk && window.uallakSmallTalk(text)", self.page)
        code = _src("dashboard", "assets", "small-talk.js").split("*/", 1)[1]
        for banned in ("fetch(", "XMLHttpRequest", "localStorage", "document."):
            self.assertNotIn(banned, code, banned)


if __name__ == "__main__":
    unittest.main()
