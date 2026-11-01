"""Structured-output resilience (core/claude_json.safe_claude_json_call +
core/model_routing's router), after ClaudeJSONError alerts #113, #117,
#120-#124 (chat, until 2026-10-31 17:40).

Every JSON call: local repair (no call) -> ONE repair retry on the same model
-> ONE call a tier up per model_routing.TIER_MODELS (skipped for a routed turn,
whose caller escalates) -> ClaudeJSONError. The router never retries: a
malformed answer is FALLBACK_TIER ('standard').

Stdlib only: the Anthropic client is a scripted fake.
Run from the repo root:  python tests/test_structured_output.py -v
"""
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def block(text):
    return types.SimpleNamespace(type="text", text=text)


def response(text, stop="end_turn"):
    return types.SimpleNamespace(stop_reason=stop, content=[block(text)],
                                 usage=types.SimpleNamespace(input_tokens=100, output_tokens=20))


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


COSTS = []
_STUBS = {
    "anthropic": _module("anthropic", Anthropic=lambda **k: None),
    "core.cost_tracker": _module("core.cost_tracker",
                                 claude_cost_ils=lambda *a: 0.01, claude_cost_usd=lambda *a: 0.0,
                                 record_cost=lambda category, ils, **k: COSTS.append(k["details"]["model"])),
}
_saved = {name: sys.modules.get(name) for name in (*_STUBS, "core.claude_json", "core.model_routing")}
sys.modules.update(_STUBS)
sys.modules.pop("core.claude_json", None)
sys.modules.pop("core.model_routing", None)
from core import claude_json as cj      # noqa: E402
from core import model_routing as mr    # noqa: E402

HAIKU, SONNET, OPUS = mr.TIER_MODELS["simple"], mr.TIER_MODELS["standard"], mr.TIER_MODELS["heavy"]
BAD = "Sure! Here is the answer: {reply: not quoted"


class Base(unittest.TestCase):
    def setUp(self):
        COSTS.clear()
        self.saved_client = cj._client

    def tearDown(self):
        cj._client = self.saved_client

    def call(self, script, **kw):
        fake = FakeClient(script)
        cj._client = lambda api_key=None: fake
        with redirect_stdout(io.StringIO()) as out:
            try:
                result = cj.safe_claude_json_call("SYSTEM", "USER", max_tokens=300, **kw)
            except cj.ClaudeJSONError as e:
                result = e
        self.log = out.getvalue()
        return result, fake


class LocalRepair(Base):
    def test_prose_after_the_object_with_braces(self):
        result, fake = self.call([response('{"reply": "ok"} — note: {not json}')], model=SONNET)
        self.assertEqual(result, {"reply": "ok"})
        self.assertEqual(len(fake.calls), 1)

    def test_trailing_comma(self):
        result, fake = self.call([response('```json\n{"a": [1, 2,], "b": "x",}\n```')], model=SONNET)
        self.assertEqual(result, {"a": [1, 2], "b": "x"})
        self.assertEqual(len(fake.calls), 1)


class RepairThenEscalate(Base):
    def test_repair_retry_on_the_same_model_succeeds(self):
        result, fake = self.call([response(BAD), response('{"reply": "fixed"}')], model=HAIKU)
        self.assertEqual(result, {"reply": "fixed"})
        self.assertEqual([c["model"] for c in fake.calls], [HAIKU, HAIKU])
        retry_msg = fake.calls[1]["messages"][0]["content"]
        self.assertTrue(retry_msg.startswith("USER"))
        self.assertIn("not valid JSON", retry_msg)
        self.assertNotIn("USER", self.log)          # never content in the log
        self.assertIn("MALFORMED JSON", self.log)

    def test_then_one_tier_up(self):
        result, fake = self.call([response(BAD), response(BAD), response('{"reply": "up"}')], model=HAIKU)
        self.assertEqual(result, {"reply": "up"})
        self.assertEqual([c["model"] for c in fake.calls], [HAIKU, HAIKU, SONNET])
        self.assertEqual(COSTS, [HAIKU, HAIKU, SONNET])   # each attempt costed on its own model

    def test_default_model_counts_as_standard_and_escalates_to_heavy(self):
        result, fake = self.call([response(BAD), response(BAD), response('{"ok": true}')])
        self.assertEqual(result, {"ok": True})
        self.assertEqual([c["model"] for c in fake.calls], [cj.DEFAULT_MODEL, cj.DEFAULT_MODEL, OPUS])

    def test_top_tier_has_nothing_above(self):
        result, fake = self.call([response(BAD), response(BAD)], model=OPUS)
        self.assertIsInstance(result, cj.ClaudeJSONError)
        self.assertEqual([c["model"] for c in fake.calls], [OPUS, OPUS])

    def test_all_fail_raises_with_the_path(self):
        result, fake = self.call([response(BAD)] * 3, model=HAIKU)
        self.assertIsInstance(result, cj.ClaudeJSONError)
        self.assertIn(f"{HAIKU} -> {HAIKU} -> {SONNET}", str(result))
        self.assertEqual(len(fake.calls), 3)

    def test_routed_turn_does_not_escalate_inside(self):
        # retry_empty=False = model_routing.routed_turn escalates itself
        result, fake = self.call([response(BAD), response(BAD)], model=HAIKU, retry_empty=False)
        self.assertIsInstance(result, cj.ClaudeJSONError)
        self.assertEqual([c["model"] for c in fake.calls], [HAIKU, HAIKU])

    def test_json_retry_off_means_one_call(self):
        result, fake = self.call([response(BAD)], model=HAIKU, retry_empty=False, json_retry=False)
        self.assertIsInstance(result, cj.ClaudeJSONError)
        self.assertEqual(len(fake.calls), 1)

    def test_content_block_messages_get_the_note_as_a_block(self):
        out = cj._with_repair_note([{"type": "text", "text": "hi"}])
        self.assertEqual(out[-1]["type"], "text")
        self.assertIn("not valid JSON", out[-1]["text"])

    def test_escalation_table(self):
        self.assertEqual(cj.escalation_model(HAIKU), SONNET)
        self.assertEqual(cj.escalation_model(SONNET), OPUS)
        self.assertIsNone(cj.escalation_model(OPUS))
        self.assertEqual(cj.escalation_model(cj.DEFAULT_MODEL), OPUS)


class RoutedTurnEscalation(Base):
    """A specialist turn on haiku that answers malformed twice ends on sonnet."""

    def setUp(self):
        super().setUp()
        self.saved = (mr.decide, mr._log)
        mr.decide = lambda *a, **k: {"tier": "simple", "model": HAIKU, "reason": "r", "source": "router",
                                     "router_ils": 0.0}
        mr._log = lambda *a, **k: None

    def tearDown(self):
        mr.decide, mr._log = self.saved
        super().tearDown()

    def test_malformed_twice_then_one_tier_up(self):
        fake = FakeClient([response(BAD), response(BAD), response('{"reply": "שלום"}')])
        cj._client = lambda api_key=None: fake
        with redirect_stdout(io.StringIO()):
            result = mr.routed_turn("google_ads", lambda model, last: cj.safe_claude_json_call(
                "S", "U", model=model, retry_empty=last), message="מה המצב?", client_id=27)
        self.assertEqual(result, {"reply": "שלום"})
        self.assertEqual([c["model"] for c in fake.calls], [HAIKU, HAIKU, SONNET])


class RouterFallsBackToStandard(Base):
    def test_malformed_router_answer_is_standard_with_one_call(self):
        fake = FakeClient([response("simple, I think")])
        cj._client = lambda api_key=None: fake
        with redirect_stdout(io.StringIO()):
            out = mr.classify("מה עם הקמפיין שלי בגוגל?", [], client_id=27)
        self.assertEqual(out["tier"], "standard")
        self.assertEqual(out["source"], "invalid")
        self.assertEqual(out["reason"], "router answer malformed")
        self.assertEqual(len(fake.calls), 1)                # no paid retry for the router

    def test_unknown_tier_is_standard(self):
        fake = FakeClient([response('{"tier": "ultra", "reason": "x"}')])
        cj._client = lambda api_key=None: fake
        with redirect_stdout(io.StringIO()):
            out = mr.classify("שאלה", [], client_id=27)
        self.assertEqual((out["tier"], out["source"]), ("standard", "invalid"))

    def test_router_call_passes_no_retry(self):
        with open(os.path.join(ROOT, "core", "model_routing.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("retry_empty=False,\n                                 json_retry=False)", src)


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
