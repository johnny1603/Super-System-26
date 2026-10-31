"""Prompt caching (2026-11-01): the static part of every agent prompt carries
cache_control, the per-client part never sits inside a cached prefix, and the
cost log prices cached vs uncached input correctly.

Stdlib only: the Anthropic client is a scripted fake; the call sites are
checked from source (they import the whole platform).

Run from the repo root:  python tests/test_prompt_caching.py -v
"""
import ast
import io
import json
import os
import sys
import types
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = types.ModuleType("anthropic")
    sys.modules["anthropic"].Anthropic = lambda **k: None
sys.modules.pop("core.claude_json", None)
sys.modules.pop("core.cost_tracker", None)
from core import claude_json as cj   # noqa: E402
from core import cost_tracker as ct  # noqa: E402


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _constant(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


def _response(text="{\"reply\": \"ok\"}", read=0, write=0, inp=100, out=20):
    usage = types.SimpleNamespace(input_tokens=inp, output_tokens=out,
                                  cache_read_input_tokens=read, cache_creation_input_tokens=write)
    return types.SimpleNamespace(stop_reason="end_turn", usage=usage,
                                 content=[types.SimpleNamespace(type="text", text=text)])


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


def _cached_part(blocks):
    """Everything up to and including the LAST block that carries a marker —
    i.e. the prefix the API caches."""
    last = max((i for i, b in enumerate(blocks) if "cache_control" in b), default=-1)
    return blocks[:last + 1]


class CachedSystem(unittest.TestCase):
    def test_static_block_is_marked(self):
        blocks = cj.cached_system("RULES")
        self.assertEqual(blocks, [{"type": "text", "text": "RULES", "cache_control": {"type": "ephemeral"}}])

    def test_dynamic_block_is_after_the_cached_part_and_unmarked(self):
        blocks = cj.cached_system("RULES", "MANAGER", dynamic="CLIENT DATA")
        self.assertEqual([b["text"] for b in _cached_part(blocks)], ["RULES", "MANAGER"])
        self.assertEqual(blocks[-1], {"type": "text", "text": "CLIENT DATA"})

    def test_breakpoint_limit(self):
        with self.assertRaises(ValueError):
            cj.cached_system("a", "b", "c", "d")

    def test_markers_are_not_shared_objects(self):
        blocks = cj.cached_system("a", "b")
        blocks[0]["cache_control"]["ttl"] = "1h"
        self.assertNotIn("ttl", blocks[1]["cache_control"])
        self.assertNotIn("ttl", cj.CACHE_CONTROL)


class CostLog(unittest.TestCase):
    def setUp(self):
        self.rows = []
        self.saved = (cj._client, ct.record_cost)
        ct.record_cost = lambda category, ils, **k: self.rows.append((category, ils, k))

    def tearDown(self):
        cj._client, ct.record_cost = self.saved

    def test_prices_cached_vs_uncached(self):
        m = "claude-opus-5-5"   # $4 in / $20 out; read 0.05x, write 1.25x
        usd = ct.claude_cost_usd(1000, 100, m, cache_read_tokens=10_000, cache_write_tokens=2000)
        expected = (1000 * 4 + 2000 * 4 * 1.25 + 10_000 * 4 * 0.05 + 100 * 20) / 1_000_000
        self.assertAlmostEqual(usd, round(expected, 6))
        self.assertAlmostEqual(ct.claude_cost_ils(1000, 100, m, 10_000, 2000), round(usd * ct.USD_TO_ILS, 4))
        # Haiku: default 0.1x read
        self.assertAlmostEqual(ct.claude_cost_usd(0, 0, "claude-haiku-5-5", cache_read_tokens=1_000_000),
                               round(0.10 * 0.1, 6))
        # No cache tokens = the old price, unchanged
        self.assertEqual(ct.claude_cost_usd(1000, 100, m), round((1000 * 4 + 100 * 20) / 1e6, 6))

    def test_a_turn_logs_cache_tokens_and_ils(self):
        fake = FakeClient([_response(read=8000, write=0, inp=300, out=50)])
        cj._client = lambda api_key=None: fake
        out = io.StringIO()
        with redirect_stdout(out):
            cj.safe_claude_json_call(cj.cached_system("RULES"), "USER", model="claude-opus-5-5",
                                     client_id=7, cost_category="claude_support")
        self.assertEqual(fake.calls[0]["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(fake.calls[0]["messages"], [{"role": "user", "content": "USER"}])
        category, ils, k = self.rows[0]
        self.assertEqual(k["details"]["cache_read_tokens"], 8000)
        self.assertEqual(k["details"]["cache_write_tokens"], 0)
        self.assertEqual(ils, ct.claude_cost_ils(300, 50, "claude-opus-5-5", 8000, 0))
        line = out.getvalue()
        self.assertIn("cache_read=8000", line)
        self.assertIn("cache_write=0", line)
        self.assertIn(f"ils={ils}", line)
        self.assertNotIn("RULES", line)      # never content
        self.assertNotIn("USER", line)

    def test_truncation_retry_sums_cache_tokens(self):
        first = _response(read=0, write=5000)
        first.stop_reason = "max_tokens"
        fake = FakeClient([first, _response(read=5000, write=0)])
        cj._client = lambda api_key=None: fake
        with redirect_stdout(io.StringIO()):
            cj.safe_claude_json_call(cj.cached_system("RULES"), "U", model="claude-opus-5-5")
        details = self.rows[0][2]["details"]
        self.assertEqual((details["cache_read_tokens"], details["cache_write_tokens"]), (5000, 5000))


class CallSites(unittest.TestCase):
    """Every chat's static prompt goes through cached_system; client data stays
    in the user message or the unmarked dynamic block."""

    def test_support_concierge_search_persona(self):
        s = " ".join(_src("agents", "support_agent.py").split())
        self.assertIn("safe_claude_json_call( cached_system(SYSTEM + manager_line), user_message", s)
        self.assertIn("claude_web_search_call(cached_system(SEARCH_SYSTEM + manager_line), search_payload", s)
        self.assertIn("safe_claude_json_call( cached_system(system), json.dumps(payload", s)

    def test_interview_client_data_is_outside_the_cached_part(self):
        s = _src("agents", "interview_agent.py")
        static = _constant(s, "INTERVIEW_SYSTEM")
        self.assertNotIn("{connected}", static)
        self.assertNotIn("{feature_catalog}", static)
        dynamic = _constant(s, "INTERVIEW_CLIENT_CONTEXT")
        self.assertIn("{connected}", dynamic)
        self.assertIn("{feature_catalog}", dynamic)
        self.assertIn("dynamic=INTERVIEW_CLIENT_CONTEXT.format(", s)
        # the static prompt still formats with only the static fields
        static.format(personas="p", language_rule="l")
        self.assertIn("cached_system(CONNECTION_NUDGE_SYSTEM.format(", s)

    def test_engagement_and_control_room(self):
        e = _src("agents", "engagement_agent.py")
        self.assertIn("cached_system(LOGIN_MOMENT_SYSTEM.format(", e)
        self.assertIn("system = cached_system(FEATURE_ANNOUNCEMENT_SYSTEM.format(", e)
        self.assertIn("system = cached_system(ADMIN_AGENT_SYSTEM.format(", _src("core", "control_room.py"))

    def test_no_chat_prompt_contains_a_timestamp_placeholder(self):
        # A per-turn value inside the cached part would silently miss every time
        for path in (("agents", "support_agent.py"), ("agents", "interview_agent.py"),
                     ("agents", "engagement_agent.py"), ("core", "control_room.py")):
            s = _src(*path)
            for name in ("SYSTEM", "SEARCH_SYSTEM", "PERSONA_SYSTEM_TEMPLATE", "INTERVIEW_SYSTEM",
                         "LOGIN_MOMENT_SYSTEM", "FEATURE_ANNOUNCEMENT_SYSTEM", "ADMIN_AGENT_SYSTEM"):
                try:
                    text = _constant(s, name)
                except (KeyError, ValueError):
                    continue
                for placeholder in ("{now}", "{today}", "{date}", "{time}", "{client"):
                    self.assertNotIn(placeholder, text, f"{name} in {path}")


class OperatorLoop(unittest.TestCase):
    def test_tail_marker_only_on_the_last_block_and_stored_msgs_untouched(self):
        import importlib
        try:
            op = importlib.import_module("core.operator_agent")
        except Exception as e:   # pragma: no cover - platform import missing
            self.skipTest(f"operator_agent not importable here: {e}")
        msgs = [{"role": "user", "content": "first"},
                {"role": "assistant", "content": [{"type": "text", "text": "x"}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "r"}]}]
        sent = op._with_tail_marker(msgs)
        marks = [b for m in sent if isinstance(m["content"], list) for b in m["content"]
                 if isinstance(b, dict) and "cache_control" in b]
        self.assertEqual(len(marks), 1)
        self.assertEqual(sent[-1]["content"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("cache_control", json.dumps(msgs))
        self.assertEqual(op._with_tail_marker([{"role": "user", "content": "hi"}])[0]["content"][0]["text"], "hi")

    def test_loop_sends_cached_system(self):
        s = _src("core", "operator_agent.py")
        self.assertIn("system=cached_system(SYSTEM)", s)
        self.assertIn("messages=_with_tail_marker(msgs)", s)


if __name__ == "__main__":
    unittest.main()
