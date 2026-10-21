"""Empty completions (core/claude_json) and the 2026-10-22 production question
through the concierge chat path (agents/support_agent).

Production: client 27 asked the concierge
  "מה הולך ? אתה יכול להעלות לי סרטון לדוגמא ליוטיוב ממה שיש במדיה ? אל תעלה לי כרגע אני רק שואל"
and got the technical fallback: Haiku 5.5 (adaptive thinking on by default)
ended the turn with thinking blocks and no text block, and the parser read "".

Stdlib only: the Anthropic client is a scripted fake; every platform module
support_agent touches is a stub. claude_json, support_agent, client_state's
helpers and agent_actions run for real.

Run from the repo root:  python tests/test_empty_completion.py -v
"""
import io
import json
import os
import sys
import types
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

QUESTION = "מה הולך ? אתה יכול להעלות לי סרטון לדוגמא ליוטיוב ממה שיש במדיה ? אל תעלה לי כרגע אני רק שואל"


# ─── A scripted Anthropic client ─────────────────────────────────────────────

def block(kind, text=""):
    return types.SimpleNamespace(type=kind, text=text) if kind == "text" \
        else types.SimpleNamespace(type=kind, thinking="")


def response(*blocks, stop="end_turn"):
    return types.SimpleNamespace(stop_reason=stop, content=list(blocks),
                                 usage=types.SimpleNamespace(input_tokens=1000, output_tokens=40))


THINKING_ONLY = lambda: response(block("thinking"))          # the production failure


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


COSTS = []
ALERTS = []
_STUBS = {
    "anthropic": _module("anthropic", Anthropic=lambda **k: None),
    "core.cost_tracker": _module("core.cost_tracker",
                                 claude_cost_ils=lambda i, o, m: 0.0, claude_cost_usd=lambda i, o, m: 0.0,
                                 record_cost=lambda category, ils, **k: COSTS.append(k["details"]["model"])),
}
_saved = {name: sys.modules.get(name) for name in _STUBS}
sys.modules.update(_STUBS)
sys.modules.pop("core.claude_json", None)
from core import claude_json as cj   # noqa: E402


class EmptyCompletion(unittest.TestCase):
    def setUp(self):
        COSTS.clear()
        self.saved_client = cj._client

    def tearDown(self):
        cj._client = self.saved_client

    def run_script(self, script, model=cj.CHAT_TURN_MODEL, **kw):
        fake = FakeClient(script)
        cj._client = lambda api_key=None: fake
        out = io.StringIO()
        with redirect_stdout(out):
            try:
                result = cj.safe_claude_json_call("SYSTEM-SECRET", "USER-SECRET", max_tokens=700,
                                                  model=model, effort="low", **kw)
            except cj.ClaudeJSONError as e:
                result = e
        return result, fake, out.getvalue()

    def test_thinking_only_turn_is_retried_on_the_same_model(self):
        result, fake, _ = self.run_script([THINKING_ONLY(), response(block("thinking"), block("text", '{"reply": "שלום"}'))])
        self.assertEqual(result, {"reply": "שלום"})
        self.assertEqual([c["model"] for c in fake.calls], [cj.CHAT_TURN_MODEL] * 2)
        self.assertEqual(fake.calls[0]["messages"], fake.calls[1]["messages"])   # same input

    def test_then_falls_back_to_the_default_model(self):
        result, fake, _ = self.run_script([THINKING_ONLY(), THINKING_ONLY(),
                                           response(block("text", '{"reply": "ok"}'))])
        self.assertEqual(result, {"reply": "ok"})
        self.assertEqual([c["model"] for c in fake.calls],
                         [cj.CHAT_TURN_MODEL, cj.CHAT_TURN_MODEL, cj.DEFAULT_MODEL])
        self.assertEqual(COSTS, [cj.CHAT_TURN_MODEL, cj.CHAT_TURN_MODEL, cj.DEFAULT_MODEL])

    def test_all_empty_raises_and_logs_shape_not_content(self):
        result, fake, log = self.run_script([THINKING_ONLY(), response(block("text", "   ")),
                                             THINKING_ONLY()])
        self.assertIsInstance(result, cj.ClaudeJSONError)
        self.assertIn("Empty completion", str(result))
        self.assertEqual(len(fake.calls), 3)
        self.assertIn("stop_reason=end_turn", log)
        self.assertIn("blocks=['thinking']", log)
        self.assertIn("output_tokens=40", log)
        self.assertNotIn("SYSTEM-SECRET", log)
        self.assertNotIn("USER-SECRET", log)

    def test_default_model_is_not_tried_twice_as_its_own_fallback(self):
        result, fake, _ = self.run_script([THINKING_ONLY(), THINKING_ONLY()], model=cj.DEFAULT_MODEL)
        self.assertIsInstance(result, cj.ClaudeJSONError)
        self.assertEqual(len(fake.calls), 2)

    def test_text_split_across_blocks_is_joined(self):
        result, _, _ = self.run_script([response(block("thinking"), block("text", '{"reply": "a'),
                                                 block("text", 'b"}'))])
        self.assertEqual(result, {"reply": "ab"})

    def test_truncation_retry_still_works(self):
        result, fake, _ = self.run_script([response(block("text", '{"reply": "cut'), stop="max_tokens"),
                                           response(block("text", '{"reply": "full"}'))])
        self.assertEqual(result, {"reply": "full"})
        self.assertEqual(fake.calls[1]["max_tokens"], 1400)

    def test_unparseable_text_is_still_a_parse_error_not_a_retry(self):
        result, fake, _ = self.run_script([response(block("text", "not json at all"))])
        self.assertIsInstance(result, cj.ClaudeJSONError)
        self.assertEqual(len(fake.calls), 1)


# ─── The production question, through the real concierge path ───────────────

STATE = {
    "as_of": "2026-10-22T10:00:00+00:00",
    "channels": {"by_channel": {"youtube": {"status": "active"}, "website_tracking": {"status": "active"}}},
    "connections": {"active_platforms": ["youtube", "google_tagmanager"], "missing": []},
    "tracking": {"connected": True, "needs_reconnect": False, "lead_event_live": True},
    "approvals": {"waiting_for_client": []},
    "media_folder": {"exists": True, "counts": {"video": 2, "image": 5},
                     "videos": [{"drive_file_id": "1AbCdEfGhIjKlMnOp", "name": "promo-autumn.mp4",
                                 "size_mb": 12.0, "uploadable": True},
                                {"drive_file_id": "1QrStUvWxYz012345", "name": "intro.mp4",
                                 "size_mb": 80.0, "uploadable": False}],
                     "upload_max_mb": 50, "youtube_uploads_today": 0, "youtube_daily_upload_cap": 3,
                     "complete": True},
    "_failed": [],
}


class ProductionQuestion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = ("supabase", "agents.client_agent", "agents.onboarding_agent", "core.automation_service",
                 "core.agent_base", "core.admin_service", "core.connection_help", "agents.engagement_agent",
                 "agents.google_ads_agent", "agents.meta_ads_agent", "agents.website_agent",
                 "agents.support_agent", "core.chat_fallback", "core.client_state", "core.agent_actions")
        cls.saved = {n: sys.modules.get(n) for n in names}
        not_connected = lambda client_id: {"connected": False}
        sys.modules.update({
            "supabase": _module("supabase", create_client=lambda *a: None),
            "agents.client_agent": _module(
                "agents.client_agent",
                get_client=lambda cid: {"id": cid, "name": "Dana", "package": "pkg", "status": "active",
                                        "language": "he", "billing_exempt": True, "chat_started_at": None},
                get_activity=lambda cid, limit=15: [], get_communications=lambda *a, **k: [],
                log_activity=lambda *a, **k: None),
            "agents.onboarding_agent": _module("agents.onboarding_agent", LANGUAGE_RULE="\n\nLANGUAGE."),
            "core.automation_service": _module("core.automation_service", VENDORS={"m": {"name": "ManyChat"}},
                                               POLICY_NOTE="policy", SIGNUP_RULE_HE="signup"),
            "core.agent_base": _module("core.agent_base", agent_alert=lambda *a, **k: ALERTS.append(a),
                                       log_step=lambda *a, **k: None, timed_step=lambda a, s, fn: fn()),
            "core.admin_service": _module("core.admin_service", display_phone=lambda p: p,
                                          support_whatsapp_number=lambda: ""),
            "core.connection_help": _module("core.connection_help", get=lambda h: {}),
            "agents.engagement_agent": _module("agents.engagement_agent", get_suggestions=lambda *a, **k: []),
            "agents.google_ads_agent": _module("agents.google_ads_agent", get_campaign_performance=not_connected),
            "agents.meta_ads_agent": _module("agents.meta_ads_agent", get_campaign_performance=not_connected),
            "agents.website_agent": _module("agents.website_agent", get_site_overview=not_connected),
        })
        for n in ("agents.support_agent", "core.chat_fallback", "core.client_state", "core.agent_actions"):
            sys.modules.pop(n, None)
        from agents import support_agent
        from core import chat_fallback, client_state
        cls.sa, cls.cf, cls.cs = support_agent, chat_fallback, client_state
        support_agent._latest_lead = lambda client_id: {}
        support_agent._client_knowledge = lambda client_id, persona: {}
        client_state.snapshot = lambda client_id, refresh=False: dict(STATE)
        cls.handoffs = []
        chat_fallback.alert_handoff = lambda *a, **k: cls.handoffs.append((a, k))

    @classmethod
    def tearDownClass(cls):
        for name, module in cls.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self):
        self.handoffs.clear()
        self.saved_client = cj._client

    def tearDown(self):
        cj._client = self.saved_client

    def ask(self, script):
        fake = FakeClient(script)
        cj._client = lambda api_key=None: fake
        with redirect_stdout(io.StringIO()):
            result = self.sa.answer_support_question(27, QUESTION)
        return result, fake

    def model_answer(self, reply, name="", followup=False):
        return response(block("thinking"), block("text", json.dumps({
            "reply": reply, "needs_human_followup": followup, "out_of_scope": False,
            "data_topic": "account", "web_search_query": "", "upgrade_request": "",
            "proposed_action": {"name": name, "params": {}, "summary": ""}}, ensure_ascii=False)))

    def test_production_failure_now_recovers_instead_of_the_technical_fallback(self):
        good = ("כן — יש בתיקיית המדיה שלך 2 סרטונים, האחרון promo-autumn.mp4. "
                "את ההעלאה ליוטיוב עושה הצוות, תמיד כפרטי, כך שאתה מאשר לפני שמשהו יוצא לציבור.")
        result, fake = self.ask([THINKING_ONLY(), self.model_answer(good)])
        technical = self.cf.technical_reply("general", "he")
        self.assertNotEqual(result["reply"], technical)
        self.assertEqual(result["reply"], good)
        self.assertFalse(result["needs_human_followup"])
        self.assertNotIn("action", result)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(self.handoffs, [])

    def test_the_model_gets_the_facts_and_the_rule_for_this_question(self):
        _, fake = self.ask([self.model_answer("x")])
        system, payload = fake.calls[0]["system"], json.loads(fake.calls[0]["messages"][0]["content"])
        self.assertIn("WHEN NO ACTION FITS", system)
        self.assertIn("I'm\nonly asking", system)
        self.assertIn("Never propose one that is not listed there", system)
        self.assertEqual(payload["account_state"]["media_folder"]["videos"][0]["name"], "promo-autumn.mp4")
        self.assertNotIn("_failed", payload["account_state"])
        offered = {a["name"] for a in payload["available_actions"]}
        self.assertNotIn("demo_youtube_upload", offered)          # admin-only, never from client chat
        self.assertNotIn("demo_google_ads_campaign", offered)
        # The real client action IS offered for this question (YouTube healthy, an uploadable video)
        self.assertIn("publish_youtube_from_media", offered)
        self.assertIn("UPLOADING A VIDEO FROM THEIR MEDIA FOLDER", system)

    def test_a_wrongly_proposed_admin_action_never_becomes_a_card(self):
        result, _ = self.ask([self.model_answer("לחצו אישור", name="demo_youtube_upload")])
        self.assertNotIn("action", result)

def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
