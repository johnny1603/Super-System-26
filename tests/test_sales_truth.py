"""No invented number before purchase (2026-11-28, core/sales_truth).

The sales chat wrote 90-day result targets and an "average cost per lead in
the industry" with no source anywhere in the system. After this:
- the market paragraph and the kpis are deleted from every proposal;
- a sentence with a number that rests on nothing is removed, and so is a
  market norm said in words;
- the prospect's own figure may be used, marked as theirs;
- how the first period works and what we measure are fixed words, not the model's;
- the same holds for the closer's reply, the one-line reaction, the proposal
  email, and what an agent is given from a proposal already on file.
"""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from core import client_speech, sales_truth as st  # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
PRICE_LIST = {"min_setup_fee": 1500, "single_service_setup_fee": 600, "organic_seo_monthly": 500,
              "platform_management_fees": {"meta": 700, "google": 700, "tiktok": 350}}
ANSWERS = {"marketing_budget": "₪3,000-5,000", "main_goal": "יותר לידים ולקוחות חדשים",
           "intro": "משרד עורכי דין, 8 שנים, רוצה 300 לידים בחודש"}


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def proposal(**over):
    """What the model used to return, invented numbers and all."""
    base = {
        "approved": True, "risk_level": "medium",
        "business_summary": "משרד עורכי דין שפועל 8 שנים. לפי מה שמסרתם, התקציב החודשי שלכם לשיווק הוא 3,000-5,000 ₪.",
        "market_reality": "בתחום עריכת הדין התחרות גבוהה ועלות ליד ממוצעת היא סביב 90 ש\"ח; ריאלי לצפות ל-40-55 לידים.",
        "kpis": {"leads": "40-55", "cpl": "90"},
        "goals_90_days": [
            "העלאת קמפיין חיפוש בגוגל וטיפול שוטף בו",
            "3 פוסטים בשבוע ו-2 מאמרים בחודש, מפורסמים לפי לוח קבוע",
            "כ-40-55 לידים בחודש עד סוף התקופה",
            "צמיחה של כ-20%-30% בעוקבים",
            "דף נחיתה ללכידת פניות, שמגיעות ישר לדשבורד",
            "השגת 300 לידים בחודש, כפי שביקשתם",
        ],
        "cost_disclaimers": ["תקציב הפרסום משולם על ידכם ישירות לפלטפורמה."],
        "honest_note": "בחודש 1 משלמים את דמי ההקמה. בחודש 2 לא נגבים דמי ניהול. בדרך כלל בתחום שלכם רואים תוצאות מהר.",
        "scarcity_note": "אתם אחד מ-20 עסקים שנבחרו החודש.",
        "packages": [{"id": "full", "name": "מלא", "description": "ניהול גוגל ומטא ב-1,400 ₪ לחודש. מביא בממוצע 47 פניות.",
                      "recommended_services": ["google", "meta"], "setup_fee_total": 1500, "monthly_management_total": 1400,
                      "setup_fee_breakdown": {"חבילת הקמה בסיסית": 1500}, "monthly_breakdown": {"ניהול גוגל": 700, "ניהול מטא": 700},
                      "benefit_value": 2800}],
    }
    base.update(over)
    return base


class Guarded(unittest.TestCase):
    def setUp(self):
        self.kept = st._price_list
        st._price_list = lambda: PRICE_LIST

    def tearDown(self):
        st._price_list = self.kept


class TheProposal(Guarded):
    def test_the_invented_fields_do_not_exist(self):
        out = st.enforce(proposal(), ANSWERS, "he")
        self.assertNotIn("market_reality", out)
        self.assertNotIn("kpis", out)

    def test_a_result_number_is_removed_and_the_actions_stay(self):
        out = st.enforce(proposal(), ANSWERS, "he")
        goals = out["goals_90_days"]
        self.assertEqual(goals, ["העלאת קמפיין חיפוש בגוגל וטיפול שוטף בו",
                                 "3 פוסטים בשבוע ו-2 מאמרים בחודש, מפורסמים לפי לוח קבוע",
                                 "דף נחיתה ללכידת פניות, שמגיעות ישר לדשבורד"])
        everything = " ".join([out["business_summary"], out["honest_note"], out["packages"][0]["description"], *goals])
        for invented in ("40-55", "47 פניות", "20%", "90 ש", "300"):
            self.assertNotIn(invented, everything, invented)

    def test_a_market_norm_in_words_is_removed(self):
        out = st.enforce(proposal(), ANSWERS, "he")
        self.assertNotIn("בדרך כלל בתחום", out["honest_note"])
        self.assertIn("בחודש 2 לא נגבים דמי ניהול", out["honest_note"])       # the payment timeline is a fact of the package
        for said in ("In this industry the average cost per lead is high.", "Typically in your field results come fast.",
                     "Dans ce domaine, la moyenne du secteur est élevée.", "В среднем по отрасли это дорого.",
                     "متوسط السوق مرتفع في هذا المجال."):
            self.assertIsNone(st.basis_of(said, set(), set()), said)

    def test_every_number_that_stays_has_a_basis(self):
        out = st.enforce(proposal(), ANSWERS, "he")
        self.assertTrue(out["figures"])
        for figure in out["figures"]:
            self.assertIn(figure["basis"]["kind"], st.KINDS, figure)
        self.assertIn("1,400", out["packages"][0]["description"])            # our own price: basis = package
        kinds = {(f["value"], f["basis"]["kind"]) for f in out["figures"]}
        self.assertIn(("1400", "package"), kinds)
        self.assertIn(("3000", "prospect"), kinds)                           # their budget, said as theirs
        self.assertEqual(st.problems(out, ANSWERS), [])
        self.assertTrue(st.problems(proposal(), ANSWERS))                    # the proposal as the model wrote it fails

    def test_the_prospects_own_figure_is_theirs_or_it_is_not_said(self):
        own = st.prospect_numbers(ANSWERS)
        said = st.basis_of("לפי מה שמסרתם, המטרה שלכם היא 300 לידים בחודש.", set(), own)
        self.assertEqual(said, [{"value": "300", "basis": {"kind": "prospect"}}])
        self.assertIsNone(st.basis_of("300 לידים בחודש.", set(), own))       # their number, not marked as theirs
        self.assertIsNone(st.basis_of("לפי מה שמסרתם, המטרה שלכם היא 300 לידים.", set(), own, actions_only=True))
        self.assertIsNone(st.basis_of("נביא 120 לידים, כפי שביקשתם.", set(), own))   # not their number

    def test_what_can_be_said_is_fixed_words(self):
        for lang in LANGS:
            out = st.enforce(proposal(), ANSWERS, lang)
            first, measured, own = out["first_period"], out["measured"], out["your_figures"]
            self.assertEqual(first["text"], st.T["first_text"][lang])
            self.assertIn("30", first["text"])
            self.assertEqual(first["basis"], {"kind": "package"})
            self.assertEqual(len(measured["items"]), 3)                      # work, enquiries, advertising (no organic here)
            self.assertEqual(own["items"][0]["value"], "₪3,000-5,000")
            self.assertEqual(own["items"][0]["basis"], {"kind": "prospect"})
            for text in [first["text"], first["title"], measured["title"], own["title"], *measured["items"], st.T["no_figure"][lang]]:
                self.assertEqual(client_speech.problems(text, lang), [], (lang, text))
        for key, row in st.T.items():
            self.assertEqual(set(row), set(LANGS), key)
            self.assertTrue(all(row.values()), key)

    def test_an_emptied_list_is_refilled_with_what_the_package_contains(self):
        out = st.enforce(proposal(goals_90_days=["כ-40 לידים בחודש", "מקום 1 בגוגל"]), ANSWERS, "he")
        self.assertGreaterEqual(len(out["goals_90_days"]), 3)
        self.assertIn(st.T["a_ads"]["he"], out["goals_90_days"])
        self.assertIn(st.T["a_setup"]["he"], out["goals_90_days"])           # a standard package: the setup contents
        single = proposal(goals_90_days=[])
        single["packages"][0].update(setup_fee_total=600, recommended_services=["organic_seo"])
        lines = st.enforce(single, ANSWERS, "he")["goals_90_days"]
        self.assertNotIn(st.T["a_setup"]["he"], lines)                       # never a deliverable the package does not hold
        self.assertIn(st.T["a_organic"]["he"], lines)

    def test_running_twice_changes_nothing(self):
        once = st.enforce(proposal(), ANSWERS, "he")
        import copy
        twice = st.enforce(copy.deepcopy(once), ANSWERS, "he")
        self.assertEqual(once, twice)

    def test_a_rejected_or_upgrade_proposal_gets_no_first_period(self):
        self.assertNotIn("first_period", st.enforce(proposal(approved=False, packages=[]), ANSWERS, "he"))
        self.assertNotIn("first_period", st.enforce(proposal(), ANSWERS, "he", prospect=False))


class TheCloser(Guarded):
    def test_a_reply_with_an_invented_figure_says_the_true_thing_instead(self):
        packages = proposal()["packages"]
        reply = st.guard_reply("שאלה טובה. בתחום שלכם עלות ליד היא בערך 90 ש\"ח. החבילה המלאה עולה 1,400 ₪ לחודש.",
                               packages, ANSWERS, "he")
        self.assertNotIn("90", reply)
        self.assertIn("1,400", reply)
        self.assertIn(st.T["no_figure"]["he"], reply)
        clean = "החבילה המלאה עולה 1,400 ₪ לחודש."
        self.assertEqual(st.guard_reply(clean, packages, ANSWERS, "he"), clean)

    def test_a_reaction_is_whole_or_absent(self):
        self.assertEqual(st.guard_reaction("8 שנים זה ותק רציני.", "אנחנו פועלים 8 שנים"), "8 שנים זה ותק רציני.")
        self.assertEqual(st.guard_reaction("עסקים כמוכם מגיעים ל-50 לידים בחודש.", "אנחנו פועלים 8 שנים"), "")


class WhatIsAlreadyOnFile(Guarded):
    def test_an_agent_is_not_given_the_old_invented_numbers(self):
        old = st.stored(proposal())
        self.assertNotIn("market_reality", old)
        self.assertNotIn("kpis", old)
        self.assertEqual(len(old["goals_90_days"]), 3)
        self.assertEqual(st.stored(None)["goals_90_days"], [])


class Wiring(unittest.TestCase):
    def test_the_capability_is_gone_from_the_prompts(self):
        sales = _read("agents", "onboarding_agent.py")
        for gone in ("market_reality", '"kpis"', "typical cost-per-lead", "realistic, well-known", "outcome projections",
                     "MAY confidently cite general market knowledge", "כ-40-55"):
            self.assertNotIn(gone, sales, gone)
        self.assertIn("uallak holds NO market or industry data", sales)
        qa = _read("agents", "qa_agent_content.py")
        self.assertNotIn("Keep the field in the returned proposal", qa)
        self.assertIn("sales_truth.enforce(proposal, answers, lang)", qa)
        self.assertEqual(qa.count("_enforce_invariants(") - 1, qa.count(", answers)"))      # both ship paths pass the answers

    def test_every_pre_purchase_surface_goes_through_it(self):
        sales = _read("agents", "onboarding_agent.py")
        self.assertIn("sales_truth.guard_reply(", sales)
        self.assertIn("sales_truth.guard_reaction(", sales)
        self.assertIn("sales_truth.enforce(proposal, answers, prospect=False)", _read("agents", "support_agent.py"))
        email = _read("core", "email_service.py")
        self.assertIn("p = sales_truth.stored(proposal)", email)
        for gone in ("market_title", "goals_sub", "נתוני שוק"):
            self.assertNotIn(gone, email, gone)
        page = _read("dashboard", "onboarding", "index.html")
        for gone in ("proposal_market", "p.market_reality", "הערכות מבוססות ניסיון"):
            self.assertNotIn(gone, page, gone)
        for shown in ("block(p.first_period)", "block(p.measured)", "block(p.your_figures)"):
            self.assertIn(shown, page, shown)

    def test_agents_of_existing_clients_no_longer_read_it(self):
        self.assertNotIn('"market_reality"', _read("agents", "budget_agent.py"))
        self.assertNotIn("market_reality", _read("agents", "google_ads_agent.py"))
        self.assertNotIn("market_reality", _read("core", "competitor_research.py"))
        knowledge = _read("core", "client_knowledge.py")
        self.assertNotIn("market_reality_at_sale_time", knowledge)
        self.assertNotIn('proposal["kpis"]', knowledge)


if __name__ == "__main__":
    unittest.main()
