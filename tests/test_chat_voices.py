"""Chat quality (2026-11-01): the strong model for every client-facing chat
turn, the cheap one only for classification, and real written characters.

A. core/claude_json: CHAT_TURN_MODEL is the strong model, CLASSIFY_MODEL the
   cheap one; every client-facing call site uses CHAT_TURN_MODEL (+ effort and
   max tokens), CLASSIFY_MODEL is used ONLY by question_filter, and the cost
   tracker knows the new model's price — so the increase is measured, never
   recorded at a fallback rate.
B. The four specialists each have a written character (the same facets, no
   braces — the voice is .format()-ed into the persona template), and no two
   are alike.
C. The three managers share ONE written voice (manager_identity.MANAGER_VOICE),
   used by the concierge (prompt_line), the login moment and the general
   feature announcement.
D. Character never outruns the rules: the persona template still carries
   YOU EXECUTE NOTHING YOURSELF, the propose-then-approve path and
   ACCOUNT_STATE_RULE ("never I can't see it").

Stdlib only, no imports of the agents (static reads + ast). Run from tests/:
    python -I -B test_chat_voices.py -v
"""
import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _constant(source: str, name: str):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return node.value
    raise AssertionError(f"{name} not found")


def _persona_voices() -> dict:
    personas = _constant(_src("agents", "support_agent.py"), "PERSONAS")
    voices = {}
    for key, entry in zip(personas.keys, personas.values):
        for k, v in zip(entry.keys, entry.values):
            if k.value == "voice":
                voices[key.value] = v.value
    return voices


FACETS = ("- Opening:", "- Rhythm:", "- Lights up:", "- Refuses to:", "- Speech habit:",
          "- Bad news:", "- Sounds nothing like:")


class Models(unittest.TestCase):
    def setUp(self):
        self.cj = _src("core", "claude_json.py")

    def test_constants(self):
        self.assertIn('CHAT_TURN_MODEL = "claude-opus-5-5"', self.cj)
        self.assertIn('CHAT_TURN_EFFORT = "low"', self.cj)
        self.assertIn('CLASSIFY_MODEL = "claude-haiku-5-5"', self.cj)
        self.assertIn('DEFAULT_MODEL = "claude-sonnet-4-6"', self.cj)       # deliverables unchanged

    def test_every_client_facing_turn_uses_the_chat_model(self):
        expected = {("agents", "support_agent.py"): 3,      # concierge, web-search answer, persona
                    ("agents", "interview_agent.py"): 2,    # connection nudge, interview turn
                    ("agents", "engagement_agent.py"): 2}   # login moment, feature announcement
        for parts, n in expected.items():
            body = _src(*parts)
            self.assertEqual(body.count("model=CHAT_TURN_MODEL"), n, parts)
        # The JSON turns also carry the effort and the token room for thinking
        for parts, n in (((("agents", "support_agent.py")), 2), (("agents", "interview_agent.py"), 2),
                         (("agents", "engagement_agent.py"), 2)):
            body = re.sub(r"\s+", " ", _src(*parts))
            self.assertEqual(len(re.findall(r"max_tokens=CHAT_TURN_MAX_TOKENS, model=CHAT_TURN_MODEL, "
                                            r"effort=CHAT_TURN_EFFORT", body)), n, parts)

    def test_search_answer_runs_on_the_chat_model(self):
        body = re.sub(r"\s+", " ", _src("agents", "support_agent.py"))
        self.assertRegex(body, r"claude_web_search_call\(SEARCH_SYSTEM \+ manager_line, search_payload, "
                               r"max_tokens=\d+, model=CHAT_TURN_MODEL")

    def test_cheap_model_only_for_classification(self):
        users = []
        for folder in ("agents", "core"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if name.endswith(".py") and name != "claude_json.py" \
                        and "model=CLASSIFY_MODEL" in _src(folder, name):
                    users.append(name)
        self.assertEqual(users, ["question_filter.py"])
        self.assertIn("model=CLASSIFY_MODEL", _src("agents", "question_filter.py"))
        # and the haiku id is never hard-coded at a call site
        for folder in ("agents",):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if name.endswith(".py"):
                    self.assertNotIn('model="claude-haiku', _src(folder, name), name)

    def test_cost_is_recorded_at_the_real_price(self):
        prices = _src("core", "cost_tracker.py")
        self.assertIn('"claude-opus-5-5": (4.00, 20.00)', prices)
        self.assertIn('"claude-haiku-5-5"', prices)
        # every JSON call is still recorded against the model that ran it
        self.assertIn('details={"input_tokens": input_tokens, "output_tokens": output_tokens, "model": model}',
                      self.cj)


class Specialists(unittest.TestCase):
    def setUp(self):
        self.voices = _persona_voices()

    def test_four_written_characters(self):
        self.assertEqual(set(self.voices), {"google", "meta", "website", "media"})
        starts = {"google": "Yoav is precise", "meta": "Maya is energetic",
                  "website": "Uri is methodical", "media": "Lior is creative and playful"}
        for key, voice in self.voices.items():
            self.assertTrue(voice.startswith(starts[key]), key)
            self.assertGreater(len(voice), 600, key)
            for facet in FACETS:
                self.assertIn(facet, voice, (key, facet))
            self.assertNotIn("{", voice, key)                 # .format()-ed into the template
            self.assertNotIn("}", voice, key)

    def test_no_two_alike(self):
        lines = {k: {ln.strip() for ln in v.splitlines() if ln.strip()} for k, v in self.voices.items()}
        keys = sorted(lines)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                shared = lines[a] & lines[b]
                self.assertEqual(shared, set(), (a, b))

    def test_template_frames_the_character(self):
        src = _src("agents", "support_agent.py")
        self.assertIn("YOUR CHARACTER — write every reply in it", src)
        self.assertIn("It shapes HOW you say things, never WHAT is true or what you may do:\n{voice}", src)
        self.assertIn("character never outruns the data", src)
        self.assertNotIn("Energetic and positive", src)       # the old one-size voice line


class Managers(unittest.TestCase):
    def test_one_written_voice(self):
        src = _src("core", "manager_identity.py")
        voice = _constant(src, "MANAGER_VOICE").value
        self.assertTrue(voice.startswith("HOW YOU SOUND (the same for every account manager at uallak):"))
        self.assertGreater(len(voice), 600)
        self.assertNotIn("{", voice)
        for name in ("Johnny", "Nissim", "Elinor", "ג'וני", "ניסים", "אלינור"):
            self.assertNotIn(name, voice)                     # one voice, whichever name
        self.assertIn('+ MANAGER_VOICE)', src)                 # prompt_line carries it

    def test_engagement_uses_it(self):
        src = _src("agents", "engagement_agent.py")
        self.assertIn("{manager_voice}", src)
        self.assertIn("manager_voice=manager_identity.MANAGER_VOICE", src)
        self.assertIn('"voice": manager_identity.MANAGER_VOICE', src)


class RulesKept(unittest.TestCase):
    def test_persona_rules(self):
        src = _src("agents", "support_agent.py")
        self.assertIn("YOU EXECUTE NOTHING YOURSELF — THIS IS A FACT, NOT A POLICY.", src)
        self.assertIn("+ HANDOFF_RULE + KNOWLEDGE_RULE + ACCOUNT_STATE_RULE + LANGUAGE_RULE", src)
        self.assertIn("+ ACCOUNT_STATE_RULE + LANGUAGE_RULE + _AUTOMATION_RULE", src)
        self.assertIn("proposed_action", src)


if __name__ == "__main__":
    unittest.main()
