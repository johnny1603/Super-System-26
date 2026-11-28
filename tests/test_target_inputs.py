"""No target before its inputs (2026-11-28, knowledge/client-governance.md §5).

- B1: while a required input is missing there is NO card — the client reads
  what is missing (the goals card and the Google strategy card alike);
- B2: every number on the goals card carries what it rests on; a result
  target with no basis is refused, never printed bare;
- B3: a thin baseline (threshold in code, printed on the card) gets a
  learning period — what we spend, what we learn, when — and no result number.
"""
import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from core import client_speech, quarter_goals as qg, target_inputs as ti  # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
BRIEF_27 = ("## offer\n- lead_time: 15–25 business days from order to shipment [observed].\n"
            "## conversion\n\n- what_counts: **sales on the site** [stated]\n")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Doors(unittest.TestCase):
    """Every read of target_inputs replaced: nothing here touches a store."""
    NAMES = ("_read", "_write", "_forecast_budget", "_brief_text", "_conversion", "_leads_30", "_sales_30", "_ad_spend_30")

    def setUp(self):
        self.saved = {n: getattr(ti, n) for n in self.NAMES}
        self.store = {}
        ti._read = lambda cid: self.store.get(cid, {})
        ti._write = lambda cid, value: self.store.__setitem__(cid, value)
        ti._forecast_budget = lambda cid: None
        ti._brief_text = lambda cid: BRIEF_27
        ti._conversion = lambda cid: {"types": ["form_submit"], "confirmed": False}
        ti._leads_30 = lambda cid: 0
        ti._sales_30 = lambda cid: {"paid_orders": 2, "net_revenue": 527}
        ti._ad_spend_30 = lambda cid: 0.0
        ti._block_cache.clear()

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(ti, name, fn)
        ti._block_cache.clear()

    def busy_leads_client(self):
        """Enquiries are the result, 20 of them for ₪1,000 of advertising, all inputs on record."""
        ti._brief_text = lambda cid: "- lead_time: 3 days\n"
        ti._conversion = lambda cid: {"types": ["form_submit"], "confirmed": True}
        ti._leads_30 = lambda cid: 20
        ti._sales_30 = lambda cid: None
        ti._ad_spend_30 = lambda cid: 1000.0
        ti.record(27, {"budget_ils": 2000, "sells": "שיפוצים", "margin_pct": 30})


class RequiredInputs(Doors):
    def test_client_27_as_recorded_today(self):
        now = ti.state(27)
        self.assertEqual(now["missing"], ["budget", "offer"])            # lead time and conversion are in the brief
        self.assertEqual(now["inputs"]["constraints"]["source"], "brief")
        self.assertIn("15–25 business days", now["inputs"]["constraints"]["value"])
        self.assertIn("sales on the site", now["inputs"]["conversion"]["value"])   # the line, not the heading
        self.assertEqual(now["kind"], "sales")                           # Shin Sekai counts sales, not enquiries
        self.assertEqual(now["thin"], ["few_results", "no_spend"])

    def test_nothing_is_inferred(self):
        ti._brief_text = lambda cid: ""
        ti._sales_30 = lambda cid: None
        ti._leads_30 = lambda cid: (_ for _ in ()).throw(RuntimeError("down"))
        self.assertEqual(ti.state(27)["missing"], list(ti.REQUIRED))

    def test_the_budget_sources(self):
        ti._forecast_budget = lambda cid: 3000.0
        self.assertEqual(ti.budget(27), {"value": 3000.0, "source": "sales_chat"})
        ti.record(27, {"budget_ils": "1500"})
        self.assertEqual(ti.budget(27), {"value": 1500.0, "source": "recorded"})

    def test_record_validates(self):
        for bad in ({"budget_ils": 0}, {"margin_pct": 100}, {"margin_pct": -3}, {}):
            with self.assertRaises(ValueError, msg=bad):
                ti.record(27, bad)
        ti.record(27, {"sells": "נעליים בעיצוב אישי", "margin_pct": 40})
        self.assertNotIn("offer", ti.missing(27))
        self.assertEqual(self.store[27]["history"][0]["fields"], ["margin_pct", "sells"])

    def test_the_agents_block(self):
        block = ti.prompt_block(27)
        self.assertEqual(block["missing"], ["budget", "offer"])
        self.assertTrue(block["baseline_too_thin_for_a_result_target"])
        self.assertIn("do NOT propose", block["rule"])


class ThinBaseline(unittest.TestCase):
    def test_the_threshold_is_code(self):
        self.assertEqual(ti.MIN_BASELINE_RESULTS, 5)
        self.assertEqual(ti.thin({"leads": 5, "spend": 200}), [])
        self.assertEqual(ti.thin({"leads": 4, "spend": 200}), ["few_results"])
        self.assertEqual(ti.thin({"leads": 40, "spend": 0}), ["no_spend"])
        self.assertEqual(ti.thin({"leads": 40, "spend": None}), ["no_spend"])     # not connected is not a history
        self.assertEqual(ti.thin({"leads": 40, "orders": 2, "spend": 300}, "sales"), ["few_results"])

    def test_the_result_kind_comes_from_the_record(self):
        self.assertEqual(ti.result_kind({"value": ["form_submit", "store_sales"]}), "sales")
        self.assertEqual(ti.result_kind({"value": ["form_submit"]}), "leads")
        self.assertEqual(ti.result_kind({"value": "what_counts: sales on the site"}), "sales")
        self.assertEqual(ti.result_kind({}), "leads")


class Basis(unittest.TestCase):
    def test_a_number_without_a_basis_is_refused(self):
        for bad in (None, {}, {"kind": "guess"}, {"kind": "history", "leads": 9}):
            with self.assertRaises(ti.BasisMissing, msg=bad):
                ti.basis_line(bad, "he")
        with self.assertRaises(ValueError):
            qg.build_targets([], 3, {"leads_30d": 23})                   # a result target with nothing under it

    def test_the_fixed_sentences(self):
        basis = {"kind": "history", "leads": 20, "spend": 1000, "budget": 2000}
        for lang in LANGS:
            line = ti.basis_line(basis, lang)
            self.assertRegex(line, r"\b20\b.*1\W?000.*2\W?000", lang)    # the three figures it rests on
            self.assertTrue(ti.basis_line({"kind": "own_output"}, lang))
            self.assertTrue(ti.basis_line({"kind": "plan"}, lang))
        self.assertEqual(ti.history_ceiling(basis), 40)                  # ₪2,000 at ₪50 an enquiry

    def test_every_table_has_five_languages(self):
        for table in (ti.NAMES, ti.T, ti.BASIS, ti.UNIT, ti.UNIT_ONE, ti.TYPE_NAMES):
            for key, row in table.items():
                self.assertEqual(set(row), set(LANGS), key)
                self.assertTrue(all(row.values()), key)


class TheGoalsCard(Doors):
    def setUp(self):
        super().setUp()
        from core import agent_actions as aa, seo_report
        self.aa, self.seo = aa, seo_report
        self.kept = (aa._lang, seo_report.coverage)
        aa._lang = lambda cid: "he"
        seo_report.coverage = lambda cid: {"readable": True, "covered": False}
        self.spec = aa.ACTIONS["agree_quarter_goals"]

    def tearDown(self):
        self.aa._lang, self.seo.coverage = self.kept
        super().tearDown()

    def test_b1_no_card_while_an_input_is_missing(self):
        out = self.spec["prepare"](27, {"posts_per_week": 3, "leads_aim": 6}, {"language": "he"}, [])
        self.assertEqual(set(out), {"clarify"})                          # words, no card, no number proposed
        text = out["clarify"]
        self.assertIn("התקציב החודשי לשיווק", text)
        self.assertIn("מה נמכר, ובאיזה מרווח", text)
        self.assertIn("מגבלות: רשום אצלנו", text)                         # what we already hold is said...
        self.assertNotRegex(text, r"lead_time|what_counts|business days")  # ...never as a raw line of the English brief
        self.assertIn("מה נחשב אצלכם תוצאה: מכירות", text)
        self.assertIn("2 מכירות (₪527 נטו)", text)
        self.assertIn("תקופת למידה", text)
        self.assertNotIn("פניות ב-30 יום: ", text)

    def test_b3_client_27_with_a_budget_gets_a_learning_period(self):
        ti.record(27, {"budget_ils": 1500, "sells": "נעליים בעיצוב אישי", "margin_pct": 40})
        out = self.spec["prepare"](27, {"posts_per_week": 3, "leads_aim": 6}, {"language": "he"}, [])
        params = self.spec["validate_stored"](27, out["params"])
        self.assertNotIn("leads_aim", params)                            # the model's number is dropped
        self.assertEqual(params["learning"]["kind"], "sales")
        for lang in LANGS:
            self.aa._lang = lambda cid, _l=lang: _l
            shown = self.spec["display"](27, params)["lines"]
            self.assertNotIn(qg.pick(qg.T["m_leads_30d"], lang), shown, lang)     # no enquiries target at all
            self.assertIn(str(ti.MIN_BASELINE_RESULTS), shown, lang)              # the threshold is on the card
            self.assertIn(params["learning"]["until"][:4], shown, lang)           # when we will know (a date)
            self.assertNotIn(qg.pick(qg.T["improve_default"], lang), shown, lang) # this client counts sales, not enquiries
            self.assertIn(ti.basis_line({"kind": "own_output"}, lang), shown, lang)
            self.assertEqual(client_speech.expectation_problems(shown), [], lang)

    def test_b2_a_result_target_carries_the_clients_own_figures(self):
        self.busy_leads_client()
        out = self.spec["prepare"](27, {"posts_per_week": 3, "leads_aim": 400}, {"language": "he"}, [])
        params = self.spec["validate_stored"](27, out["params"])
        self.assertEqual(params["leads_aim"], 40)                        # never above what the history supports
        shown = self.spec["display"](27, params)["lines"]
        self.assertIn("על בסיס הנתונים שלכם: 20 פניות ב-30 הימים האחרונים בהוצאה של ₪1,000", shown)
        self.assertNotIn("תקופת למידה", shown)
        for line in shown.splitlines():
            if line.startswith("• ") and ("סף 90%" in line):
                self.assertIn(" · ", line)                               # every numeric target has its basis beside it

    def busy_store_client(self):
        """Sales are the result: 12 paid orders, ₪600 of advertising, every input on record."""
        ti._sales_30 = lambda cid: {"paid_orders": 12, "net_revenue": 4200}
        ti._ad_spend_30 = lambda cid: 600.0
        ti.record(27, {"budget_ils": 1500, "sells": "נעליים בעיצוב אישי", "margin_pct": 40})

    def test_a_store_client_past_the_threshold_gets_a_sales_target(self):
        self.busy_store_client()
        now = ti.state(27)
        self.assertEqual((now["kind"], now["thin"], now["missing"]), ("sales", [], []))
        basis = ti.history_basis(now)
        self.assertEqual(basis, {"kind": "history", "unit": "sales", "orders": 12, "spend": 600.0, "budget": 1500.0})
        self.assertEqual(ti.history_ceiling(basis), 30)                  # ₪1,500 at ₪50 an order
        self.assertEqual(ti.metric_of(now["kind"]), "sales_30d")
        # the model aims high AND gives an enquiries aim: the card carries ONE target, in sales, capped
        out = self.spec["prepare"](27, {"posts_per_week": 3, "sales_aim": 500, "leads_aim": 80}, {"language": "he"}, [])
        params = self.spec["validate_stored"](27, out["params"])
        self.assertEqual(params["sales_aim"], 30)
        self.assertNotIn("leads_aim", params)
        self.assertNotIn("learning", params)
        for lang in LANGS:
            self.aa._lang = lambda cid, _l=lang: _l
            shown = self.spec["display"](27, params)["lines"]
            self.assertIn(qg.pick(qg.T["m_sales_30d"], lang), shown, lang)
            self.assertNotIn(qg.pick(qg.T["m_leads_30d"], lang), shown, lang)
            self.assertIn(ti.basis_line(basis, lang), shown, lang)       # the sales number rests on their own store
            self.assertEqual(client_speech.expectation_problems(shown), [], lang)
        shown = self.spec["display"](27, dict(params))["lines"] if not setattr(self.aa, "_lang", lambda cid: "he") else ""
        self.assertIn("מכירות ב-30 יום: 26 ", shown)                      # 30 less the internal margin, floored
        self.assertIn("12 מכירות בחנות ב-30 הימים האחרונים (כל המכירות, לא רק אלה שהגיעו מפרסום)", shown)

    def test_the_sales_target_is_built_measured_and_judged(self):
        basis = {"kind": "history", "unit": "sales", "orders": 12, "spend": 600.0, "budget": 1500.0}
        targets = qg.build_targets([], 3, {"sales_30d": 30}, baseline={"sales_30d": {"paid_orders": 12}},
                                   bases={"sales_30d": basis})
        row = [t for t in targets if t["metric"] == "sales_30d"][0]
        self.assertEqual((row["target"], row["baseline"], row["basis"]), (26, 12, basis))
        with self.assertRaises(ValueError):
            qg.build_targets([], 3, {"sales_30d": 30})                   # no basis, no sales target
        kept = qg._sales_30
        try:
            qg._sales_30 = lambda cid: {"paid_orders": 24, "net_revenue": 9000}
            rows = qg.evaluate(27, {"targets": targets, "baseline": {"captured_at": "2026-11-28T00:00:00+00:00"}})
            sales = [r for r in rows if r["metric"] == "sales_30d"][0]
            self.assertEqual((sales["current"], sales["threshold"], sales["met"]), (24, 23.4, True))
            for lang in LANGS:
                self.assertIn(qg.pick(qg.T["m_sales_30d"], lang), qg.target_line(sales, lang), lang)
            qg._sales_30 = lambda cid: None                               # the store is not readable: not measured, never zero
            self.assertIsNone(qg.current_value(27, {}, "sales_30d"))
        finally:
            qg._sales_30 = kept
        self.assertIn("sales_30d", qg.METRICS)
        self.assertIn("sales_30d", qg.BUFFERED)

    def test_a_stored_sales_target_needs_its_own_basis(self):
        good = {"kind": "history", "unit": "sales", "orders": 12, "spend": 600.0, "budget": 1500.0}
        for bad in ({"keywords": [], "sales_aim": 9},
                    {"keywords": [], "sales_aim": 9, "sales_basis": {"kind": "history", "leads": 20, "spend": 600, "budget": 1500}},
                    {"keywords": [], "sales_aim": 9, "sales_basis": {**good, "orders": 3}},
                    {"keywords": [], "sales_aim": 9, "sales_basis": good, "leads_aim": 5,
                     "leads_basis": {"kind": "history", "leads": 20, "spend": 600, "budget": 1500}}):
            with self.assertRaises((ValueError, KeyError), msg=bad):
                self.spec["validate_stored"](27, bad)
        self.assertEqual(self.spec["validate_stored"](27, {"keywords": [], "sales_aim": 9, "sales_basis": good})["sales_aim"], 9)

    def test_a_stored_result_target_without_a_basis_is_refused(self):
        for bad in ({"keywords": [], "leads_aim": 6},
                    {"keywords": [], "leads_aim": 6, "leads_basis": {"kind": "history", "leads": 2, "spend": 0, "budget": 900}},
                    {"keywords": [], "leads_aim": 6, "leads_basis": {"kind": "history", "leads": 20, "spend": 500, "budget": 900},
                     "learning": {"budget": 900, "until": "2026-12-28"}}):
            with self.assertRaises((ValueError, KeyError), msg=bad):
                self.spec["validate_stored"](27, bad)

    def test_the_agreed_learning_period_is_kept_and_shown(self):
        saved = (qg._db, qg.capture_baseline)
        rows = {}

        class _Q:
            def __init__(self): self.key = None
            def table(self, name): return self
            def select(self, *_): return self
            def eq(self, _c, v): self.key = v; return self
            def limit(self, _n): return self
            def upsert(self, row, **_): rows[row["key"]] = row; return self
            def execute(self):
                return type("R", (), {"data": [rows[self.key]] if self.key in rows else []})()
        qg._db = lambda: _Q()
        qg.capture_baseline = lambda cid, words: {"captured_at": "2026-11-28T00:00:00+00:00"}
        try:
            plan = ti.learning({"baseline": {"leads": 0, "orders": 2, "revenue": 527, "spend": 0.0}, "kind": "sales",
                                "inputs": {"budget": {"value": 1500}}, "thin": ["few_results", "no_spend"]},
                               today=datetime(2026, 11, 28, tzinfo=timezone.utc))
            value = qg.save(27, [], 3, learning=plan)
            self.assertEqual(value["learning"]["until"], "2026-12-28")
            self.assertEqual([t["metric"] for t in value["targets"]], ["posts_per_week"])
            self.assertEqual(len(qg.learning_lines(value, "he")), 5)
        finally:
            qg._db, qg.capture_baseline = saved


class TheInputsCard(Doors):
    def setUp(self):
        super().setUp()
        from core import agent_actions as aa
        self.aa, self.kept = aa, aa._lang
        aa._lang = lambda cid: "he"
        self.spec = aa.ACTIONS["record_target_inputs"]

    def tearDown(self):
        self.aa._lang = self.kept
        super().tearDown()

    def test_the_click_records_and_says_what_is_still_missing(self):
        out = self.spec["prepare"](27, {"budget_ils": 1500}, {"language": "he"}, [])
        params = self.spec["validate_stored"](27, out["params"])
        self.assertIn("₪1,500", self.spec["display"](27, params)["lines"])
        result = self.spec["run"](27, params, None)
        self.assertEqual(result["missing"], ["offer"])
        self.assertIn("מה נמכר, ובאיזה מרווח", self.spec["done_message"](result, "he"))
        self.assertIn("יעדים", self.spec["done_message"]({"missing": []}, "he"))

    def test_nothing_to_record_is_words_not_a_card(self):
        self.assertIn("clarify", self.spec["prepare"](27, {}, {"language": "he"}, []))
        with self.assertRaises(ValueError):
            self.spec["validate_stored"](27, {"budget_ils": 5, "guess": 1})

    def test_five_languages(self):
        for key in ("title", "what", "done", "button"):
            self.assertEqual(set(self.spec[key]), set(LANGS), key)


class Wiring(Doors):
    def test_the_strategy_card_has_the_same_gate(self):
        from core import agent_actions as aa
        out = aa.ACTIONS["write_google_strategy"]["prepare"](27, {}, {"language": "en"}, [])
        self.assertIn("the monthly marketing budget", out["clarify"])

    def test_the_rule_reaches_the_agents_and_the_doctrine(self):
        from core import governance
        self.assertIn("NO TARGET BEFORE ITS INPUTS", governance.WIRING_RULE)
        support = _read("agents", "support_agent.py")
        self.assertIn('payload["target_inputs"]', support)
        self.assertIn("NO TARGET BEFORE ITS INPUTS", support)
        doctrine = _read("knowledge", "client-governance.md")
        self.assertIn("No target before its inputs", doctrine)
        self.assertIn("A thin baseline is not extrapolated", doctrine)

    def test_the_store_is_deleted_with_the_client(self):
        for name in ("client_deletion.py", "deletion_guard.py", "admin_service.py"):
            self.assertIn("target_inputs:", _read("core", name), name)

    def test_home_draws_the_learning_period(self):
        self.assertIn("g.learning", _read("dashboard", "client", "index.html"))


if __name__ == "__main__":
    unittest.main()
