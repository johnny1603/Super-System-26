"""The brand's voice as the client meets it (core/brand_voice, 2026-11-27).

The client does not curate the agents' tone: the positive tone is the
system's, shown read-only. ONE question is theirs — what may never be said
about them — asked once in onboarding, shown on the branding page as a short
line with an edit link, and a HARD CONSTRAINT on every word produced for them.

Run: python tests/test_brand_voice.py
"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

try:
    import anthropic                             # noqa: F401
except ImportError:                              # a bare test machine: the SDK is faked (the client is replaced below)
    _sdk = types.ModuleType("anthropic")
    _sdk.Anthropic = type("Anthropic", (), {"__init__": lambda self, **kw: None})
    sys.modules["anthropic"] = _sdk

import test_brand_page as tbp                    # noqa: E402  (the brief + Drive harness)
from core import brand_brief, brand_page as bp, brand_voice as bv        # noqa: E402

ANSWER = "לא להגיד שאנחנו הכי זולים, ולא להזכיר את הסניף שנסגר"


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(tbp.Base):
    def setUp(self):
        super().setUp()
        bp.view(27, "he")                         # client 27's brief is on its row


class TheOneQuestion(Base):
    def test_the_wording_is_one_plain_question_in_five_languages(self):
        self.assertEqual(bv.TEXT["question"]["he"], "יש משהו שאסור לנו להגיד עליכם?")
        for table in bv.TEXT.values():
            self.assertEqual(sorted(table), ["ar", "en", "fr", "he", "ru"])
        he = bv.TEXT["explain"]["he"]
        for part in ("מתחרה", "בתחום שלכם", "השתבש", "סגנון"):          # what it is for, and what it is not for
            self.assertIn(part, he)
        for part in ("כלל מחייב", "כיתובים", "כותרות", "דפי נחיתה", "תשובות ללקוחות"):
            self.assertIn(part, bv.TEXT["binding"]["he"])                # said plainly beside the field

    def test_the_answer_is_free_text_kept_as_the_client_wrote_it(self):
        n = bp.view(27, "he")["never_say"]
        self.assertFalse(n["set"])
        self.assertEqual(n["line"], bv.TEXT["line_empty"]["he"])
        before = bp.read_field(brand_brief.current(27)["text"], "voice", "avoid")
        bp.save_never_say(27, "  " + ANSWER + "  ")
        cur = brand_brief.current(27)
        self.assertEqual(cur["by"], "client")
        self.assertRegex(cur["text"], r"- never_say: " + ANSWER + r" \[stated — the client, branding page, \d{4}-\d\d-\d\d\]")
        self.assertEqual(bp.read_field(cur["text"], "voice", "avoid"), before)       # what was already stored stays
        n = bp.view(27, "he")["never_say"]
        self.assertEqual((n["set"], n["text"]), (True, ANSWER))                      # a sentence with a comma is ONE answer
        self.assertIn(ANSWER, n["line"])
        bp.save_never_say(27, "")                                                    # the client may clear it
        self.assertFalse(bp.view(27, "he")["never_say"]["set"])
        self.assertEqual(bv.read(27)["text"], "")

    def test_markup_cannot_ride_in_on_the_answer(self):
        self.assertEqual(bv.clean("## voice\n- avoid: [stated] `x` {y}"), "voice - avoid: stated x y")
        self.assertLessEqual(len(bv.clean("א" * 5000)), bv.MAX_CHARS)


class AHardConstraintOnEveryWord(Base):
    def test_nothing_set_adds_nothing(self):
        self.assertEqual(bv.rule(555), "")
        self.assertEqual(bv.with_rule("SYSTEM", 555), "SYSTEM")
        self.assertEqual(bv.with_rule("SYSTEM", None), "SYSTEM")
        self.assertEqual(bv.rule("not a client"), "")                    # never raises: a prompt is never lost to this

    def test_the_clients_words_and_the_older_list_are_both_in_the_rule(self):
        bp.save_never_say(27, ANSWER)
        rule = bv.rule(27)
        self.assertIn("HARD CONSTRAINT", rule)
        self.assertIn(f'"{ANSWER}"', rule)
        for item in bv.read(27)["list"]:                                  # the brief's does-not-say list stays enforced
            self.assertIn(item, rule)
        self.assertTrue(bv.read(27)["list"])
        for where in ("captions", "headlines", "landing", "replies to its customers"):
            self.assertIn(where, rule)

    def test_it_rides_on_every_model_call_made_for_the_client(self):
        from core import claude_json
        bp.save_never_say(27, ANSWER)
        seen = []

        class Messages:
            def create(self, **kw):
                seen.append(kw["system"])
                return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text='{"ok": true}')],
                                             stop_reason="end_turn",
                                             usage=types.SimpleNamespace(input_tokens=1, output_tokens=1,
                                                                         cache_read_input_tokens=0, cache_creation_input_tokens=0))

        fake = types.SimpleNamespace(messages=Messages())
        saved = (claude_json._client, claude_json._record)
        claude_json._client = lambda api_key=None: fake
        claude_json._record = lambda *a, **k: None
        self.addCleanup(lambda: [setattr(claude_json, n, v) for n, v in zip(("_client", "_record"), saved)])
        self.assertEqual(claude_json.safe_claude_json_call("WRITE A CAPTION", "{}", client_id=27), {"ok": True})
        self.assertTrue(seen[0].startswith("WRITE A CAPTION"))
        self.assertIn(ANSWER, seen[0])
        blocks = claude_json.cached_system("STATIC RULES", dynamic="client data")
        claude_json.safe_claude_json_call(blocks, "{}", client_id=27)
        self.assertEqual(seen[1][:2], blocks)                             # the cached prefix is untouched
        self.assertIn(ANSWER, seen[1][-1]["text"])
        self.assertNotIn("cache_control", seen[1][-1])
        claude_json.safe_claude_json_call("INTERNAL WORK", "{}")          # no client: nothing added
        self.assertEqual(seen[2], "INTERNAL WORK")
        src = _src("core", "claude_json.py")
        self.assertIn("system = _with_client_rules(system, client_id)", src)
        for writer in (("agents", "landing_page_agent.py"), ("core", "landing_creative.py"), ("agents", "support_agent.py"),
                       ("agents", "media_agent.py"), ("agents", "engagement_agent.py")):
            self.assertIn("safe_claude_json_call(", _src(*writer))        # the writers all go through the choke point


class TheToneIsTheSystems(Base):
    def test_the_page_shows_the_tone_and_offers_no_way_to_edit_it(self):
        tone = bp.view(27, "he")["tone"]
        self.assertTrue(tone["says"])
        self.assertIn("מהאפיון של העסק", tone["sub"])
        html = _src("dashboard", "client", "index.html")
        for gone in ("data-br-chip-add", "data-br-chip-del", "data-br-save=\"tone\"", "br_tone_says", "/api/client/brand-identity/tone",
                     "כך המותג מדבר"):
            self.assertNotIn(gone, html)
        api = _src("core", "api_server.py")
        self.assertNotIn('"/api/client/brand-identity/tone"', api)
        self.assertNotIn("def save_tone", _src("core", "brand_page.py"))
        voice = html[html.index("function brVoiceHtml()"):html.index("function brNeverHtml()")]
        self.assertNotIn("<input", voice)
        self.assertNotIn("<textarea", voice)

    def test_the_page_shows_a_short_line_with_an_edit_link_not_a_panel(self):
        html = _src("dashboard", "client", "index.html")
        never = html[html.index("function brNeverHtml()"):html.index("// What a branding package would add")]
        closed = never[never.index("return `<div class=\"br-card wide br-never\" id"):]
        self.assertIn("n.line", closed)
        self.assertIn("data-br-never-edit", closed)
        self.assertNotIn("<textarea", closed)                             # the field appears only after the edit link
        for shown in ("n.question", "n.explain", "n.binding", "<textarea id=\"brNeverText\""):
            self.assertIn(shown, never)
        self.assertNotIn("יש משהו שאסור", html)                            # one wording, the server's
        self.assertIn("brJson('/api/client/brand-identity/never-say', { text:", html)

    def test_tags_are_shown_in_the_clients_language_from_one_cached_translation(self):
        stored = bv.read(27)
        self.assertFalse(all(bv._native(i, "he") for i in stored["says"] + stored["list"]))   # the brief's tags are English
        calls = []

        def model(client_id, lang, voice, section):
            calls.append(lang)
            return {"says": ["עב:" + s for s in voice["says"]], "not_says": ["עב:" + s for s in voice["not_says"]],
                    "why": "כך עולה מהאפיון שלכם."}
        saved = bv._ask_model
        bv._ask_model = model
        self.addCleanup(setattr, bv, "_ask_model", saved)
        self.assertEqual(bp.view(27, "he")["tone"]["says"], stored["says"])           # reading the page never calls a model
        self.assertEqual(calls, [])
        self.assertTrue(bv.ensure_shown(27, "he"))
        self.assertFalse(bv.ensure_shown(27, "he"))                                   # once per change
        self.assertEqual(calls, ["he"])
        view = bp.view(27, "he")
        self.assertEqual(view["tone"]["says"], ["עב:" + s for s in stored["says"]])
        self.assertEqual(view["never_say"]["list"], ["עב:" + s for s in stored["list"]])
        self.assertEqual(view["tone"]["why"], "כך עולה מהאפיון שלכם.")
        self.assertEqual(bv.read(27)["list"], stored["list"])                         # the rule keeps the stored words
        self.assertFalse(bv.ensure_shown(27, "en"))                                   # English tags for an English client: as they are
        bv._ask_model = lambda *a: {"says": ["only one"], "not_says": [], "why": ""}
        brand_brief.set_page_state(27, voice_shown={})
        self.assertFalse(bv.ensure_shown(27, "he"))                                   # an answer that does not match is not shown
        bv._ask_model = lambda *a: (_ for _ in ()).throw(RuntimeError("model down"))
        self.assertFalse(bv.ensure_shown(27, "he"))                                   # and a failure never breaks the page
        self.assertIn("brand_voice.ensure_shown(client_id, lang)", _src("core", "api_server.py"))


class AskedOnceInOnboarding(Base):
    def test_the_interview_asks_it_once_and_keeps_the_clients_own_words(self):
        src = _src("agents", "interview_agent.py")
        self.assertIn('"יש משהו שאסור לנו להגיד עליכם?"', src)
        self.assertIn("captured.never_say IN THEIR OWN", src)
        self.assertIn("NEVER ask again", src)
        self.assertIn('"never_say_set": _never_say_set(client_id)', src)
        self.assertIn('"never_say": "",', src)
        body = src[src.index("def _store_never_say"):src.index("def _store_captured")]
        self.assertIn('brand_voice.save(client_id, text, "onboarding")', body)

    def test_an_onboarding_answer_lands_in_the_brief_as_the_clients(self):
        bv.save(27, ANSWER, "onboarding")
        self.assertRegex(brand_brief.current(27)["text"], r"- never_say: " + ANSWER + r" \[stated — the client, onboarding, ")
        self.assertTrue(bp.view(27, "he")["never_say"]["set"])            # and the page shows it, with the edit link


if __name__ == "__main__":
    unittest.main()
