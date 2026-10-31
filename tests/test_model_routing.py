"""Per-turn model routing (core/model_routing, 2026-11-01).

Locked roles always get opus; simple / standard / heavy examples classify as
expected; escalation fires on a forced failure; a router timeout falls back
to standard; flag off -> everything opus; the cost log records the right
model's price; the persona rules are identical across models; and no chat
model is named outside the one config.

Stdlib only: the router and the Anthropic client are fakes.
Run from the repo root:  python tests/test_model_routing.py -v
"""
import io
import os
import re
import sys
import time
import types
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = types.ModuleType("anthropic")
    sys.modules["anthropic"].Anthropic = lambda **k: None
for name in ("core.claude_json", "core.cost_tracker", "core.model_routing", "core.operator_flags"):
    sys.modules.pop(name, None)
from core import claude_json as cj      # noqa: E402
from core import cost_tracker as ct     # noqa: E402
from core import model_routing as mr    # noqa: E402

OPUS, SONNET, HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class FakeTable:
    def __init__(self, rows):
        self.rows = rows

    def insert(self, row):
        self.rows.append(row)
        return self

    def execute(self):
        return types.SimpleNamespace(data=[])


class Base(unittest.TestCase):
    def setUp(self):
        self.logged, self.router_calls = [], []
        self.saved = (mr._db, mr._ask_router, mr.ROUTER_TIMEOUT_SECONDS, os.environ.get(mr.FLAG))
        mr._db = lambda: types.SimpleNamespace(table=lambda name: FakeTable(self.logged))
        self.router("standard")
        os.environ.pop(mr.FLAG, None)          # default: routing ON

    def tearDown(self):
        mr._db, mr._ask_router, mr.ROUTER_TIMEOUT_SECONDS, flag = self.saved
        if flag is None:
            os.environ.pop(mr.FLAG, None)
        else:
            os.environ[mr.FLAG] = flag

    def router(self, tier=None, fn=None):
        def ask(message, recent, client_id):
            self.router_calls.append((message, recent))
            if fn:
                return fn()
            return {"tier": tier, "reason": "test"}
        mr._ask_router = ask

    def turn(self, role, message, call, recent=None):
        with redirect_stdout(io.StringIO()):
            return mr.routed_turn(role, call, message=message, recent=recent or [], client_id=5)


GOOD = {"reply": "תשובה", "confidence": "high"}


class Locked(Base):
    def test_locked_roles_always_get_opus(self):
        os.environ[mr.FLAG] = "1"
        for role in ("manager", "interview"):
            for message in ("תודה", "כמה קליקים היו?", "תמחק הכול"):
                d = mr.decide(role, message, [], 5)
                self.assertEqual((d["model"], d["source"]), (OPUS, "locked"), (role, message))
        self.assertEqual(self.router_calls, [])

    def test_locked_turn_never_escalates_and_logs(self):
        models = []
        self.turn("manager", "היי", lambda model, last: models.append((model, last)) or GOOD)
        self.assertEqual(models, [(OPUS, True)])
        self.assertEqual(self.logged[0]["role"], "manager")
        self.assertEqual(self.logged[0]["model"], OPUS)


class Classify(Base):
    def test_simple_examples(self):
        for message in ("תודה!", "היי", "סבבה 👍", "thanks"):
            d = mr.decide("specialist", message, [], 5)
            self.assertEqual((d["tier"], d["model"]), ("simple", HAIKU), message)
        self.assertEqual(self.router_calls, [])

    def test_heavy_examples(self):
        cases = {
            "אני ממש מתוסכל, הקמפיין שוב לא עובד": "frustrated client",
            "תמחק את הקמפיין של הקיץ": "irreversible decision",
            "אני רוצה לבטל את המנוי": "irreversible decision",
            "מה האסטרטגיה שלנו לחודש הבא?": "strategy",
            "קודם תבדוק את האתר ואז תעדכן את המודעות": "multi-step request",
            "1. תבדוק את האתר\n2. תעדכן מודעה\n3. תשלח לי דוח": "multi-step request",
        }
        for message, reason in cases.items():
            d = mr.decide("specialist", message, [], 5)
            self.assertEqual((d["tier"], d["model"], d["reason"]), ("heavy", OPUS, reason), message)
        self.assertEqual(self.router_calls, [])

    def test_standard_example_goes_to_the_router_with_the_last_two_turns(self):
        history = [{"from": "client", "text": f"m{i}"} for i in range(10)]
        d = mr.decide("specialist", "כמה קליקים היו לי השבוע בגוגל?", history, 5)
        self.assertEqual((d["tier"], d["model"], d["source"]), ("standard", SONNET, "router"))
        message, recent = self.router_calls[0]
        self.assertEqual(message, "כמה קליקים היו לי השבוע בגוגל?")
        self.assertEqual(recent, history)          # trimmed to RECENT_TURNS inside _ask_router

    def test_router_payload_is_the_message_plus_last_two_turns(self):
        sent = {}
        saved = cj.safe_claude_json_call
        cj.safe_claude_json_call = lambda system, user, **k: sent.update(user=user, k=k) or {"tier": "simple"}
        try:
            mr._ask_router = self.saved[1]
            history = [{"from": "client", "text": f"m{i}"} for i in range(10)]
            out = mr._ask_router("שאלה", history, 5)
        finally:
            cj.safe_claude_json_call = saved
        import json
        payload = json.loads(sent["user"])
        self.assertEqual(out, {"tier": "simple"})
        self.assertEqual([t["text"] for t in payload["last_turns"]], ["m6", "m7", "m8", "m9"])
        self.assertEqual(sent["k"]["model"], HAIKU)
        self.assertFalse(sent["k"]["retry_empty"])

    def test_router_answer_is_respected(self):
        for tier, model in (("simple", HAIKU), ("standard", SONNET), ("heavy", OPUS)):
            self.router(tier)
            self.assertEqual(mr.decide("specialist", "מה המצב של הקמפיין?", [], 5)["model"], model)


class Fallback(Base):
    def test_router_timeout_falls_back_to_standard(self):
        mr.ROUTER_TIMEOUT_SECONDS = 0.2
        self.router(fn=lambda: time.sleep(1.0) or {"tier": "heavy"})
        started = time.time()
        d = mr.decide("specialist", "מה המצב של הקמפיין?", [], 5)
        self.assertLess(time.time() - started, 0.9)
        self.assertEqual((d["tier"], d["model"], d["source"]), ("standard", SONNET, "timeout"))

    def test_router_error_or_nonsense_falls_back_to_standard(self):
        def boom():
            raise RuntimeError("down")
        self.router(fn=boom)
        self.assertEqual(mr.decide("specialist", "מה המצב?", [], 5)["source"], "error")
        self.router("ultra")
        d = mr.decide("specialist", "מה המצב?", [], 5)
        self.assertEqual((d["tier"], d["source"]), ("standard", "invalid"))

    def test_flag_off_sends_everything_to_opus(self):
        os.environ[mr.FLAG] = "0"
        for message in ("תודה", "מה המצב של הקמפיין?", "תמחק הכול"):
            d = mr.decide("specialist", message, [], 5)
            self.assertEqual((d["model"], d["source"]), (OPUS, "flag_off"))
        self.assertEqual(self.router_calls, [])
        models = []
        self.turn("specialist", "תודה", lambda model, last: models.append(model) or {"reply": ""})
        self.assertEqual(models, [OPUS])           # nothing above opus, no escalation

    def test_flag_is_on_by_default_and_listed(self):
        from core import operator_flags
        self.assertTrue(operator_flags.FLAGS[mr.FLAG]["can_turn_on"])
        self.assertTrue(mr.routing_enabled())


class Escalation(Base):
    def test_api_error_retries_one_tier_up_invisibly(self):
        models = []

        def call(model, last):
            models.append((model, last))
            if model == HAIKU:
                raise RuntimeError("tool error")
            return GOOD
        self.assertEqual(self.turn("specialist", "תודה", call), GOOD)
        self.assertEqual(models, [(HAIKU, False), (SONNET, True)])
        row = self.logged[0]
        self.assertTrue(row["escalated"])
        self.assertEqual((row["routed_tier"], row["tier"], row["model"]), ("simple", "standard", SONNET))
        self.assertEqual(row["escalation_cause"], "error:RuntimeError")

    def test_empty_malformed_and_low_confidence_escalate(self):
        for bad in ({"reply": "  "}, {"text": "no reply key"}, ["not", "a", "dict"],
                    {"reply": "לא בטוח", "confidence": "low"}):
            self.logged.clear()
            models = []
            out = self.turn("specialist", "מה המצב של הקמפיין?",
                            lambda model, last: models.append(model) or (bad if model == SONNET else GOOD))
            self.assertEqual(models, [SONNET, OPUS], bad)
            self.assertEqual(out, GOOD)
            self.assertTrue(self.logged[0]["escalated"])

    def test_good_answer_does_not_escalate(self):
        models = []
        self.turn("specialist", "מה המצב?", lambda model, last: models.append(model) or GOOD)
        self.assertEqual(models, [SONNET])
        self.assertFalse(self.logged[0]["escalated"])

    def test_heavy_has_nothing_above_and_keeps_its_own_retries(self):
        models = []
        out = self.turn("specialist", "מה האסטרטגיה?", lambda model, last: models.append((model, last)) or {"reply": ""})
        self.assertEqual(models, [(OPUS, True)])
        self.assertEqual(out, {"reply": ""})       # the caller's empty-reply path takes over

    def test_escalation_failure_keeps_a_low_confidence_answer(self):
        low = {"reply": "אולי", "confidence": "low"}

        def call(model, last):
            if model == OPUS:
                raise RuntimeError("overloaded")
            return low
        self.assertEqual(self.turn("specialist", "מה המצב?", call), low)

    def test_escalation_failure_after_an_error_raises(self):
        def call(model, last):
            raise RuntimeError("down")
        with self.assertRaises(RuntimeError):
            self.turn("specialist", "מה המצב?", call)
        self.assertEqual(len(self.logged), 1)


class CostLog(Base):
    def test_cost_log_records_the_right_model_price(self):
        def response(model):
            usage = types.SimpleNamespace(input_tokens=1000, output_tokens=200,
                                          cache_read_input_tokens=6000, cache_creation_input_tokens=0)
            return types.SimpleNamespace(stop_reason="end_turn", usage=usage, model=model,
                                         content=[types.SimpleNamespace(type="text", text='{"reply": "x"}')])

        class Fake:
            def __init__(self):
                self.messages = self

            def create(self, **k):
                return response(k["model"])
        costs = []
        saved = (cj._client, ct.record_cost)
        cj._client = lambda api_key=None: Fake()
        ct.record_cost = lambda category, ils, **k: costs.append((ils, k["details"]["model"]))
        try:
            self.turn("specialist", "מה המצב של הקמפיין?",
                      lambda model, last: cj.safe_claude_json_call(cj.cached_system("S"), "U", model=model,
                                                                   retry_empty=last))
        finally:
            cj._client, ct.record_cost = saved
        expected = ct.claude_cost_ils(1000, 200, SONNET, 6000, 0)
        self.assertEqual(costs, [(expected, SONNET)])
        row = self.logged[0]
        self.assertEqual((row["model"], row["cost_ils"]), (SONNET, expected))
        self.assertEqual((row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]), (1000, 200, 6000))
        # the three tiers have real prices — never the fallback rate
        for model in mr.TIER_MODELS.values():
            self.assertIn(model, ct.MODEL_PRICES_USD_PER_MTOK)
        self.assertEqual(ct.MODEL_PRICES_USD_PER_MTOK[SONNET], (2.00, 10.00))

    def test_summary_by_tier(self):
        rows = [{"tier": "simple", "escalated": False, "cost_ils": 0.01, "router_cost_ils": 0.001,
                 "input_tokens": 100, "cache_read_tokens": 900, "cache_write_tokens": 0, "source": "rule"},
                {"tier": "standard", "escalated": True, "cost_ils": 0.1, "router_cost_ils": 0.001,
                 "input_tokens": 100, "cache_read_tokens": 0, "cache_write_tokens": 900, "source": "router"},
                {"tier": "heavy", "escalated": False, "cost_ils": 0.3, "router_cost_ils": 0,
                 "input_tokens": 100, "cache_read_tokens": 900, "cache_write_tokens": 0, "source": "locked"},
                {"tier": "heavy", "escalated": False, "cost_ils": 0.3, "router_cost_ils": 0,
                 "input_tokens": 100, "cache_read_tokens": 900, "cache_write_tokens": 0, "source": "locked"}]
        s = mr.summarize(rows)
        self.assertEqual(s["turns"], 4)
        self.assertEqual(s["tiers"]["heavy"]["pct"], 50.0)
        self.assertEqual(s["tiers"]["heavy"]["cost_per_turn_ils"], 0.3)
        self.assertEqual(s["tiers"]["simple"]["cost_per_turn_ils"], 0.011)
        self.assertEqual(s["escalation_rate_pct"], 25.0)
        self.assertEqual(s["cache_hit_rate_pct"], round(100 * 2700 / 4000, 1))


class Persona(unittest.TestCase):
    def test_persona_rules_identical_across_models(self):
        s = _src("agents", "support_agent.py")
        body = s[s.index("def _answer_persona_question"):]
        body = body[:body.index("persona_llm_call") + 1500]
        # the prompt is built once, BEFORE routing, and never from the model
        self.assertLess(body.index("system = PERSONA_SYSTEM_TEMPLATE.format("), body.index("routed_turn("))
        template = s[s.index("PERSONA_SYSTEM_TEMPLATE = "):]
        template = template[:template.index("\ndef ")]
        self.assertNotRegex(template, r"\{model|haiku|sonnet|opus")
        self.assertIn('lambda model, last_try: safe_claude_json_call(\n'
                      '                    cached_system(system), json.dumps(payload', body)

    def test_same_prompt_on_escalation(self):
        seen = []
        system = cj.cached_system("PERSONA RULES")
        saved = (mr._db, mr._ask_router)
        mr._db = lambda: types.SimpleNamespace(table=lambda name: FakeTable([]))
        mr._ask_router = lambda *a: {"tier": "simple"}
        os.environ.pop(mr.FLAG, None)
        try:
            with redirect_stdout(io.StringIO()):
                mr.routed_turn("specialist",
                               lambda model, last: seen.append((model, system)) or {"reply": ""},
                               message="מה המצב?", client_id=1)
        finally:
            mr._db, mr._ask_router = saved
        self.assertEqual([m for m, _ in seen], [HAIKU, SONNET])
        self.assertEqual(seen[0][1], seen[1][1])


class OneConfig(unittest.TestCase):
    def test_no_chat_model_named_outside_the_config(self):
        pattern = re.compile(r"[\"']claude-(haiku|sonnet|opus)-5-5[\"']")
        allowed = {os.path.join("core", "model_routing.py"), os.path.join("core", "cost_tracker.py")}
        found = []
        for folder in ("agents", "core"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                rel = os.path.join(folder, name)
                if name.endswith(".py") and rel not in allowed and pattern.search(_src(folder, name)):
                    found.append(rel)
        self.assertEqual(found, [])
        self.assertIn('CHAT_TURN_MODEL = TIER_MODELS["heavy"]', _src("core", "claude_json.py"))
        self.assertIn('CLASSIFY_MODEL = TIER_MODELS["simple"]', _src("core", "claude_json.py"))

    def test_locked_roles_in_config(self):
        self.assertEqual(mr.ROLE_OVERRIDES, {"manager": "heavy", "interview": "heavy"})
        self.assertEqual(mr.TIER_MODELS, {"simple": HAIKU, "standard": SONNET, "heavy": OPUS})
        self.assertEqual(mr.FALLBACK_TIER, "standard")

    def test_call_sites(self):
        s = _src("agents", "support_agent.py")
        self.assertIn('model_routing.routed_turn(\n                "manager"', s)
        self.assertIn('model_routing.routed_turn(\n                "specialist"', s)
        self.assertIn('model_routing.routed_turn(\n                "interview"', _src("agents", "interview_agent.py"))

    def test_admin_view_is_admin_only(self):
        api = _src("core", "api_server.py")
        route = api[api.index('@app.get("/api/admin/model-costs")'):]
        self.assertIn("_require_admin(request)", route[:400])


if __name__ == "__main__":
    unittest.main()
