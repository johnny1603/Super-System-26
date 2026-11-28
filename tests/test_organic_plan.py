"""Organic in the professional order + goals with a dated baseline (2026-11-16,
docs/ORGANIC-PLAN.md).

What must stay true:
- no keyword reaches the client before research; the order is state →
  research → the client's choice (1-5 researched phrases only) → backlinks →
  strategy, and the goals card waits for it (organic packages);
- volume/competition are labelled estimates unless real data is on, and the
  paid connector is never called while it is off;
- the time estimate: internal, and committed = internal × 1.15 rounded UP to
  half a month; both are estimates; a promised ranking is removed;
- the goals click captures a dated baseline (measured or None, never a guess)
  and the monthly report is posted once per month against it, on the agreed
  keywords only;
- the terms of measurement are on the card before the click, in 5 languages.
"""
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")

for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}), ("httpx", {}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from core import agent_actions as aa              # noqa: E402
from core import claude_json, dataforseo_connector, organic_plan as op, quarter_goals as qg, seo_report   # noqa: E402
from test_landing_own import FakeDB               # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


RAW_CANDIDATES = {"candidates": [
    {"keyword": "קפה בחיפה", "source": "client", "intent": "local", "volume": "medium", "competition": "medium", "why": "the core"},
    {"keyword": "בית קפה עם חניה בחיפה", "source": "related", "intent": "local", "volume": "low", "competition": "low", "why": "specific"},
    {"keyword": "קפה מיוחד", "source": "competitor", "intent": "commercial", "volume": "high", "competition": "high", "why": "hard"},
    {"keyword": "x", "intent": "local"},
    {"keyword": "ברנץ' בחיפה", "intent": "nonsense", "volume": "huge", "competition": "low", "why": "מבטיח מקום ראשון"},
]}


class Pure(unittest.TestCase):
    def test_the_committed_target_is_internal_plus_15_percent_rounded_up(self):
        for internal, committed in ((1, 1.5), (3, 3.5), (4, 5.0), (6, 7.0), (2, 2.5)):
            self.assertEqual(op.committed_months(internal), committed, internal)
            self.assertGreater(op.committed_months(internal), internal)

    def test_candidates_keep_the_clients_words_and_drop_what_is_not_true(self):
        rows = op.clean_candidates(RAW_CANDIDATES["candidates"], ["קפה בחיפה", "עוגות"])
        words = [r["keyword"] for r in rows]
        self.assertEqual(words[:2], ["קפה בחיפה", "עוגות"])              # the client's own, first
        self.assertEqual(rows[0]["source"], "client")
        self.assertEqual(rows[0]["intent"], "local")                       # the analysis folded into their row
        self.assertNotIn("x", words)
        brunch = [r for r in rows if r["keyword"].startswith("ברנץ")][0]
        self.assertEqual((brunch["intent"], brunch["volume"], brunch["why"]), ("", "", ""))  # bad values + promise gone
        self.assertTrue(all(r["basis"] == "estimate" for r in rows))
        real = op.clean_candidates(RAW_CANDIDATES["candidates"], [], {"קפה בחיפה": {"volume": 880, "competition": 0.4}})
        self.assertEqual((real[0]["monthly_searches"], real[0]["basis"]), (880, "measured"))

    def test_only_a_small_researched_set_can_be_approved(self):
        candidates = op.clean_candidates(RAW_CANDIDATES["candidates"], [])
        self.assertEqual(op.check_approval("קפה בחיפה, קפה מיוחד", candidates), ["קפה בחיפה", "קפה מיוחד"])
        for bad in ("קפה בחיפה, משהו שלא נחקר", "", ",".join(f"k{i}" for i in range(6))):
            with self.assertRaises(ValueError):
                op.check_approval(bad, candidates)
        many = [{"keyword": f"k{i}"} for i in range(8)]
        with self.assertRaises(ValueError):
            op.check_approval([f"k{i}" for i in range(6)], many)

    def test_the_stage_order(self):
        value = {}
        order = []
        for key, add in (("state", {"x": 1}), ("research", {"candidates": []}), ("approved", {"keywords": ["a"]}),
                         ("backlinks", {"x": 1}), ("strategy", {"steps": []})):
            order.append(op.stage(value))
            value[key] = add
        order.append(op.stage(value))
        self.assertEqual(order, ["state", "keywords", "approve", "backlinks", "strategy", "ready"])


class Steps(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.calls = []
        self.saved = [(op, "_db", op._db), (qg, "_db", qg._db), (op, "_client_words", op._client_words),
                      (op, "_business", op._business), (op, "_research", op._research), (op, "_positions", op._positions),
                      (claude_json, "claude_web_search_call", claude_json.claude_web_search_call),
                      (claude_json, "safe_claude_json_call", claude_json.safe_claude_json_call),
                      (seo_report, "latest", seo_report.latest), (seo_report, "fresh_enough", seo_report.fresh_enough),
                      (dataforseo_connector, "enabled", dataforseo_connector.enabled),
                      (dataforseo_connector, "_post", dataforseo_connector._post)]
        op._db = qg._db = lambda: self.db
        op._block_cache.clear()
        op._client_words = lambda cid: ["קפה בחיפה"]
        op._business = lambda cid: {"name": "קפה נועה"}
        op._research = lambda cid: {"data": {"competitors": [{"domain": "rival.co.il"}]}}
        op._positions = lambda cid, words: {}
        seo_report.latest = lambda cid: {"domain": "noa.co.il", "built_at": datetime.now(timezone.utc).isoformat(),
                                         "report": {"summary": "s", "site_state": ["f"],
                                                    "backlink_opportunities": [{"site": "b144.co.il", "type": "directory"}]}}
        seo_report.fresh_enough = lambda value: True
        dataforseo_connector.enabled = lambda: False
        dataforseo_connector._post = lambda *a, **k: self.calls.append("PAID") or {}

        def search(system, user, **kw):
            self.calls.append("search")
            return "notes"
        claude_json.claude_web_search_call = search

        def write(system, user, **kw):
            self.calls.append("write")
            if "strategy" in system.lower() and "internal_months" in system:
                return {"steps": [{"what": "דף שירות לכל ביטוי", "why": "כוונה מקומית"},
                                  {"what": "נבטיח מקום ראשון", "why": "x"}], "internal_months": 4, "reason": "r"}
            return RAW_CANDIDATES
        claude_json.safe_claude_json_call = write

    def tearDown(self):
        for module, name, value in self.saved:
            setattr(module, name, value)

    def test_the_whole_order_with_no_paid_call_while_off(self):
        with self.assertRaises(op.PlanError):
            op.research_keywords(27)                                        # never before the state report
        op.build_state(27)
        self.assertEqual(op.current(27)["state"]["competitors"], ["rival.co.il"])
        self.assertFalse(op.current(27)["state"]["ranks_measured"])
        with self.assertRaises(op.PlanError):
            op.build_strategy(27)
        value = op.research_keywords(27)
        self.assertEqual(value["research"]["basis"], "judgment")
        self.assertEqual(op.stage(value), "approve")
        block = op.prompt_block(27)
        self.assertTrue(block["research_done"])
        self.assertIn("candidates", block)
        op.approve(27, ["קפה בחיפה", "קפה מיוחד"])
        op.check_backlinks(27)
        value = op.build_strategy(27)
        self.assertEqual(op.stage(value), "ready")
        self.assertEqual((value["strategy"]["internal_months"], value["strategy"]["committed_months"]), (4.0, 5.0))
        self.assertNotIn("נבטיח", str(value["strategy"]["steps"]))           # a promise is removed
        self.assertNotIn("PAID", self.calls)

    def test_no_candidates_in_the_block_before_research(self):
        self.assertNotIn("candidates", op.prompt_block(27))
        self.assertFalse(op.prompt_block(27)["research_done"])


class GoalsCard(Steps):
    def setUp(self):
        super().setUp()
        self.saved += [(seo_report, "coverage", seo_report.coverage), (qg, "_count_leads", qg._count_leads),
                       (qg, "_sales_30", qg._sales_30), (qg, "_visitors_30", qg._visitors_30),
                       (qg, "_positions", qg._positions), (aa, "_post_client_message", aa._post_client_message),
                       (aa, "_lang", aa._lang)]
        seo_report.coverage = lambda cid: {"readable": True, "covered": True}
        from core import target_inputs
        self.saved.append((target_inputs, "state", target_inputs.state))      # every required input on record (core/target_inputs)
        target_inputs.state = lambda cid: {"missing": [], "thin": [], "kind": "leads", "baseline": {"leads": 7, "spend": 700.0},
                                           "inputs": {"budget": {"known": True, "value": 2000.0}}}
        qg._count_leads = lambda cid, a, b: 7
        qg._sales_30 = lambda cid: None
        qg._visitors_30 = lambda cid: (_ for _ in ()).throw(RuntimeError("off"))
        qg._positions = lambda cid, words: {"קפה בחיפה": 14}
        self.posted = []
        aa._post_client_message = lambda cid, persona, text: self.posted.append((persona, text))
        aa._lang = lambda cid: "he"
        self.spec = aa.ACTIONS["agree_quarter_goals"]

    def test_waits_for_the_plan_and_then_uses_only_the_approved_set(self):
        out = self.spec["prepare"](27, {"keywords": ["משהו שהמודל המציא"]}, {"language": "he"}, [])
        self.assertEqual(out["switch_to"], "advance_organic_plan")
        op.build_state(27)
        op.research_keywords(27)
        self.assertIn("clarify", self.spec["prepare"](27, {}, {"language": "he"}, []))
        op.approve(27, ["קפה בחיפה"])
        op.check_backlinks(27)
        op.build_strategy(27)
        out = self.spec["prepare"](27, {"keywords": ["משהו שהמודל המציא"], "posts_per_week": 3}, {"language": "he"}, [])
        params = self.spec["validate_stored"](27, out["params"])
        self.assertEqual(params["keywords"], ["קפה בחיפה"])
        self.assertEqual(params["committed_months"], 5.0)
        shown = self.spec["display"](27, params)
        self.assertIn("קפה בחיפה", shown["terms"])
        self.assertIn("5", shown["lines"])
        self.spec["run"](27, params, None)
        value = qg.agreed(27)
        base = value["baseline"]
        self.assertEqual((base["leads_30d"], base["sales_30d"], base["visitors_30d"]), (7, None, None))
        self.assertEqual(base["positions"], {"קפה בחיפה": 14})
        self.assertTrue(base["captured_at"])
        self.assertEqual(value["plan"]["committed_months"], 5.0)

    def test_no_organic_in_the_package_means_no_organic_track(self):
        seo_report.coverage = lambda cid: {"readable": True, "covered": False}
        out = self.spec["prepare"](27, {"keywords": ["x y"]}, {"language": "he"}, [])
        self.assertEqual(out["params"]["keywords"], [])

    def test_the_monthly_report_is_posted_once_against_the_baseline(self):
        qg.save(27, ["קפה בחיפה"], 3)
        self.assertFalse(qg.post_monthly_if_due(27))                       # month 0: nothing due
        row = [r for r in self.db.tables["app_settings"] if r["key"] == "quarter_goals:27"][0]
        row["value"]["agreed_at"] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        qg._positions = lambda cid, words: {"קפה בחיפה": 9}
        self.assertTrue(qg.post_monthly_if_due(27))
        self.assertFalse(qg.post_monthly_if_due(27))                       # once
        text = self.posted[0][1]
        self.assertIn("דוח חודש 1", text)
        self.assertIn("קפה בחיפה: מיקום 9 (בהתחלה: 14)", text)
        self.assertIn("נמדד רק על הביטויים", text)

    def test_texts_in_five_languages(self):
        from core import quarter_goals_actions as qga
        for lang in LANGS:
            self.assertIn("{k}", qga.TERMS[lang])
            self.assertIn("{terms}", self.spec["what"][lang])
            for key in ("baseline", "estimate", "monthly_title", "monthly_scope"):
                self.assertTrue(qg.T[key][lang])
            for table in op.TEXT.values():
                self.assertTrue(table[lang])
        for name in ("advance_organic_plan", "approve_organic_keywords"):
            for key in ("title", "what"):
                self.assertEqual(set(aa.ACTIONS[name][key]), set(LANGS), (name, key))


class Rules(unittest.TestCase):
    def test_the_prompt_rule_and_the_wiring(self):
        from agents import support_agent
        rule = support_agent.STUDIO_RULE
        for needle in ("NO KEYWORD IN CONVERSATION BEFORE", "client_phrases_for_research_only", "advance_organic_plan",
                       "approve_organic_keywords", "ESTIMATE", "Never promise a ranking"):
            self.assertIn(needle, rule, needle)
        self.assertNotIn("{", rule)
        self.assertIn('payload["organic_plan"]', _src("agents", "support_agent.py"))
        self.assertIn("post_monthly_if_due", _src("core", "api_server.py"))
        for path in (("core", "deletion_guard.py"), ("core", "admin_service.py")):
            self.assertIn('"organic_plan:"', _src(*path))
        self.assertIn('f"organic_plan:{int(i)}"', _src("core", "client_deletion.py"))


if __name__ == "__main__":
    unittest.main()
