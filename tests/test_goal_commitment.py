"""The results commitment = terms section 6, enforced in code (2026-11-19).

- the terms carry the owner's exact Hebrew section 6 (+ 4 translations, Hebrew prevails);
- the landing page promises nothing wider and links to it;
- goal_met: 90% of a numeric target counts as met (THE rule);
- goals are numbers; a yes/no goal is refused at the card;
- the internal 15% buffer never reaches a client or an agent;
- home + the monthly report show target, 90% threshold, dated baseline, now.
"""
import os
import re
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from core import quarter_goals as qg  # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


SECTION_6 = [
    "6.1 דמי ההקמה הם תשלום חד פעמי בתחילת ההתקשרות. דמי הניהול נגבים מדי חודש, עבור אותו חודש שירות.",
    "6.2 התחייבות ליעדים: בתום תקופת היעדים שסוכמה (שלושה חודשים ממועד אישור היעדים), אם לא עמדנו ביעדים שאושרו על ידך מראש, יוחזרו לך דמי הניהול שנגבו באותם שלושה חודשים.",
    "6.3 מה נחשב עמידה ביעד: הגעה ל-90% ומעלה מהיעד המספרי שאושר נחשבת עמידה ביעד. המדידה נעשית רק על היעדים, הביטויים והמדדים שאישרת מראש, מול נקודת הבסיס שנרשמה במערכת במועד אישור היעדים. המערכת מתעדת את נקודת הבסיס, את היעדים שאושרו ואת ההתקדמות החודשית.",
    "6.4 מה מוחזר: דמי הניהול בלבד, עבור תקופת היעדים בלבד. אינם מוחזרים דמי ההקמה, תקציבי פרסום ששולמו לפלטפורמות, עלויות ייצור, רכישות מצד שלישי ותשלומים חד פעמיים אחרים.",
    "6.5 מתי ההתחייבות אינה חלה: אם לא אושרו על ידך עבודות שהוצעו ונדרשו להשגת היעדים, אם חשבונות או הרשאות לא חוברו או נותקו, אם בוצעו שינויים באתר או בקמפיינים שלא דרכנו, אם קמפיינים הושהו או תקציבם הופחת, או אם חומרים שנדרשו ממך לא סופקו תוך זמן סביר.",
    "6.6 ניתן להפסיק את השירות בכל עת. לא יבוצע חיוב עבור החודשים שלאחר מכן.",
]


class TermsSection6(unittest.TestCase):
    def test_exact_hebrew_text(self):
        html = _read("dashboard", "terms", "index.html")
        self.assertIn('<span data-i18n="s6_title">תשלומים והחזר</span>', html)
        for para in SECTION_6:
            self.assertEqual(html.count(para), 2, para[:20])     # the page + the i18n table
        self.assertNotIn("אינו מזכה בהחזר", html)                  # the old no-refund line is gone
        self.assertIn('id="s6"', html)

    def test_translations_and_the_hebrew_prevails_line(self):
        html = _read("dashboard", "terms", "index.html")
        for key in ("s6_title", "s6_1", "s6_2", "s6_3", "s6_4", "s6_5", "s6_6", "s6_note"):
            line = re.search(r"^  %s: \{(.*)\},$" % key, html, re.M)
            self.assertTrue(line, key)
            for lang in ("en", "fr", "ar", "ru"):
                self.assertRegex(line.group(1), r"\b%s:'[^']" % lang, (key, lang))
        note = re.search(r"^  s6_note: \{(.*)\},$", html, re.M).group(1)
        self.assertIn("he:''", note)                               # shown only in translation
        self.assertIn("Hebrew version is the binding one", note)


class LandingMatchesTheTerms(unittest.TestCase):
    def test_no_wider_promise_and_a_link(self):
        html = _read("dashboard", "landing", "index.html")
        self.assertNotIn("אין תשלום דמי ניהול. נקודה", html)
        self.assertNotIn("no management fee. Period", html)
        for key in ("commit_note", "faq_3_a"):
            line = re.search(r"^  %s: \{(.*)\},$" % key, html, re.M).group(1)
            self.assertEqual(line.count('href="/terms/#s6"'), 5, key)
            self.assertEqual(line.count("90"), 5, key)
        self.assertIn('data-i18n-html="commit_note"', html)
        self.assertIn('data-i18n-html="faq_3_a"', html)

    def test_prompts_carry_the_exact_commitment(self):
        self.assertIn("management fees", qg.COMMITMENT_RULE.lower())
        self.assertIn("90%", qg.COMMITMENT_RULE)
        self.assertIn("_commitment_rule()", _read("agents", "onboarding_agent.py"))
        self.assertIn("section 6 of the terms", _read("agents", "support_agent.py"))


class NinetyPercentRule(unittest.TestCase):
    def test_goal_met_boundaries(self):
        self.assertTrue(qg.goal_met(9, 10))
        self.assertFalse(qg.goal_met(8.99, 10))
        self.assertTrue(qg.goal_met(6.3, 7))
        self.assertFalse(qg.goal_met(6.2, 7))
        self.assertTrue(qg.goal_met(12, 10))
        self.assertIsNone(qg.goal_met(None, 10))
        self.assertEqual(qg.threshold(20), 18)
        self.assertEqual(qg.MET_RATIO, 0.9)

    def test_the_committed_target_carries_the_internal_buffer(self):
        self.assertEqual(qg.committed_target("leads_30d", 23), 20)          # 23 / 1.15
        self.assertEqual(qg.committed_target("positions_gained", 5), 4.3)
        self.assertEqual(qg.committed_target("posts_per_week", 3), 3)        # our own output: no buffer
        with self.assertRaises(ValueError):
            qg.committed_target("leads_30d", 0)

    def test_evaluate_and_the_report(self):
        saved = (qg._count_leads, qg._count_activity, qg._positions)
        qg._count_leads = lambda cid, a, b: 18
        qg._count_activity = lambda cid, types, days: 10
        qg._positions = lambda cid, words: {"שיפוצים בחיפה": 9}
        try:
            value = {"agreed_at": "2026-08-01T00:00:00+00:00", "keywords": ["שיפוצים בחיפה"],
                     "baseline": {"captured_at": "2026-08-01T00:00:00+00:00", "leads_30d": 7,
                                  "positions": {"שיפוצים בחיפה": 14}, "posts_week": 1.0},
                     "targets": qg.build_targets(["שיפוצים בחיפה"], 3, {"leads_30d": 23, "positions_gained": 7},
                                                 {"leads_30d": 7, "posts_week": 1.0},
                                                 {"leads_30d": {"kind": "history", "leads": 7, "spend": 500, "budget": 1500}})}
            rows = {r["metric"]: r for r in qg.evaluate(27, value)}
            self.assertEqual((rows["leads_30d"]["target"], rows["leads_30d"]["threshold"]), (20, 18))
            self.assertTrue(rows["leads_30d"]["met"])                        # 18 = exactly 90%
            self.assertEqual(rows["positions_gained"]["current"], 5)         # 14 -> 9
            self.assertFalse(rows["positions_gained"]["met"])               # 5 < 0.9 * 6.1
            self.assertFalse(rows["posts_per_week"]["met"])                 # 2.5 a week < 2.7
            self.assertEqual(rows["leads_30d"]["baseline_date"], "2026-08-01")
            for lang in LANGS:
                report = qg.monthly_report(27, value, lang)
                self.assertIn("2026-08-01", report)
                self.assertIn("18", report)                                  # the 90% threshold
                self.assertIn("/terms/#s6", report)
                self.assertNotIn("23", report)                               # the internal aim, never
        finally:
            qg._count_leads, qg._count_activity, qg._positions = saved


class NumbersOnlyAndNoBuffer(unittest.TestCase):
    def test_a_yes_no_goal_is_refused(self):
        from core import agent_actions as aa
        v = aa.ACTIONS["agree_quarter_goals"]["params"]["improve"]["validate"]
        for text in ("להגיע לעמוד הראשון בגוגל", "reach page one", "top 3 on Google", "première page",
                     "الصفحة الأولى", "первая страница"):
            with self.assertRaises(ValueError, msg=text):
                v(text, 27)
        self.assertEqual(v("יותר שיחות טלפון", 27), "יותר שיחות טלפון")

    def test_the_card_shows_committed_numbers_and_the_90_line(self):
        from core import agent_actions as aa
        spec = aa.ACTIONS["agree_quarter_goals"]
        saved = aa._lang
        try:
            for lang in LANGS:
                aa._lang = lambda cid, _l=lang: _l
                shown = spec["display"](27, {"keywords": ["a"], "posts_per_week": 3, "leads_aim": 23,
                                             "leads_basis": {"kind": "history", "leads": 9, "spend": 600, "budget": 1800},
                                             "positions_aim": 5, "internal_months": 4, "committed_months": 4.5})
                self.assertIn("20", shown["lines"])
                self.assertIn("18", shown["lines"])
                self.assertNotIn("23", shown["lines"])
                self.assertIn("4.5", shown["lines"])                       # the committed months only
        finally:
            aa._lang = saved

    def test_no_internal_figure_in_client_text_or_agent_data(self):
        from core import organic_plan, quarter_goals_actions
        words = re.compile(r"internal|interne|פנימי|داخلي|внутренн|15\s*%", re.I)
        for table in (qg.T, organic_plan.TEXT, quarter_goals_actions.TERMS):
            for key, value in table.items():
                for lang, text in (value.items() if isinstance(value, dict) else [("", value)]):
                    self.assertNotRegex(str(text), words, (key, lang))
        saved = qg.agreed
        qg.agreed = lambda cid: {"agreed_at": "2026-11-01T00:00:00+00:00", "keywords": [], "posts_per_week": 3,
                                 "plan": {"committed_months": 4.5}, "internal": {"leads_30d": 23, "months": 4},
                                 "targets": [{"metric": "leads_30d", "target": 20, "baseline": 7}]}
        qg._block_cache.clear()
        try:
            block = qg.prompt_block(27)
            self.assertNotIn("internal", repr(block))
            self.assertNotIn("23", repr(block))
            self.assertIn("90%", block["met_rule"])
        finally:
            qg.agreed = saved
            qg._block_cache.clear()
        self.assertNotIn('"internal_months": s["internal_months"]', _read("core", "organic_plan.py"))

    def test_home_draws_the_targets(self):
        html = _read("dashboard", "client", "index.html")
        self.assertIn('id="gtTargets"', html)
        self.assertIn("gt_terms_link", html)
        self.assertIn("r.text || ''", html)


if __name__ == "__main__":
    unittest.main()
