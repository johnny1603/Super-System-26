"""How agents speak to clients (knowledge/client-governance.md §9):
core/client_speech (the terms table + the output check) and
core/campaign_presentation (the four elements, the basis of an expectation).

Run: python tests/test_client_speech.py            (add --show to print the sample presentation)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import campaign_presentation as cp  # noqa: E402
from core import client_speech as cs  # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
SKIP_KEYS = {"does", "hint", "params", "personas"}          # read by the model / the code, never by a client


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def keys(text, lang=None, allowed=()):
    return sorted({hit["key"] for hit in cs.find(text, lang, allowed)})


class Terms(unittest.TestCase):
    def test_the_table_is_data_in_one_file_in_five_languages(self):
        for key, (patterns, plain, ordinary) in cs.TERMS.items():
            self.assertTrue(patterns, key)
            if plain is not None:
                self.assertEqual(set(plain), set(LANGS), key)
                for lang in LANGS:                               # the plain words never use a forbidden term themselves
                    self.assertEqual(cs.problems(plain[lang], lang), [], (key, lang))
        for key in ("asset_group", "ad_strength", "bid_strategy", "match_type", "manifest", "render"):
            self.assertIn(key, cs.NEVER)
        for owner_row in ("pmax", "search", "demand_gen", "display", "youtube", "bumper", "shorts_ads", "remarketing",
                          "cpc", "cpm", "ctr", "roas", "conversion", "impressions"):
            self.assertIsNotNone(cs.TERMS[owner_row][1], owner_row)
        # nobody else writes a term's plain words: the campaign card takes them from the table
        actions = _src("core", "media_order_actions.py")
        self.assertIn("types = {t: client_speech.TERMS[t][1] for t in gaf.CAMPAIGN_TYPES}", actions)

    def test_a_term_fails_in_latin_and_in_transliteration(self):
        for text in ("אנחנו מריצים Performance Max", "נריץ פרפורמנס מקס", "בפרפורמנס-מקס יש יתרון", "קמפיין PMax חדש",
                     "حملة بيرفورمانس ماكس", "Запустим Перформанс Макс", "une campagne performance max"):
            self.assertEqual(keys(text), ["pmax"], text)
        self.assertEqual(keys("ה-CTR עלה וה-ROAS ירד, ה-CPC יציב וה-CPM גבוה"), ["cpc", "cpm", "ctr", "roas"])
        self.assertEqual(keys("הסי.טי.אר עלה, הרואס ירד"), ["ctr", "roas"])
        self.assertEqual(keys("דימנד ג'ן ורימרקטינג"), ["demand_gen", "remarketing"])
        self.assertEqual(keys("קמפיין Demand Gen עם RSA"), ["demand_gen", "search"])
        self.assertEqual(keys("הקונברז'ן השתפר והאימפרשנס עלו"), ["conversion", "impressions"])
        self.assertEqual(keys("שיפרנו את ה-asset group ואת ה-ad strength, שינינו bid strategy ו-match type"),
                         ["ad_strength", "asset_group", "bid_strategy", "match_type"])
        self.assertEqual(keys("לפי המניפסט של הרינדור"), ["manifest", "render"])
        self.assertEqual(keys("the media_agent handed it to google_ads_agent"), ["agent_module"])

    def test_ordinary_words_links_and_the_clients_own_platforms_stay(self):
        for text in ("הקמפיין עלה לאוויר והמודעות מוצגות בגוגל, ביוטיוב ובג'ימייל", "קיבלתם 40 פניות החודש",
                     "We display your products on the page", "Your conversions and impressions went up",
                     "Vos conversions ont augmenté", "ראו https://example.com/performance-max/ctr?roas=1",
                     "סרטון ב-YouTube Shorts", "כמה עולה כל קליק"):
            self.assertEqual(cs.problems(text), [], text)
        # in Hebrew text the Latin jargon word IS jargon
        self.assertEqual(keys("ה-conversions עלו"), ["conversion"])

    def test_a_term_the_client_used_is_answered_plainly(self):
        asked = cs.keys_in("זה Performance Max?")
        self.assertEqual(asked, ("pmax",))
        reply = "כן, זה Performance Max: קמפיין שרץ בכל המקומות של גוגל. ה-CTR שלו טוב."
        self.assertEqual(keys(reply, "he", allowed=asked), ["ctr"])        # theirs to hear back; the rest still fails
        import contextvars

        def turn():
            cs.allow_from("זה פרפורמנס מקס?")
            return cs.find("כן, זה Performance Max", "he")
        self.assertEqual(contextvars.copy_context().run(turn), [])
        self.assertEqual(keys("כן, זה Performance Max"), ["pmax"])          # another turn: forbidden again


class Expectations(unittest.TestCase):
    def test_a_bare_number_fails_and_a_based_range_passes(self):
        bare = cs.expectation_problems("נביא לכם 40 פניות")
        self.assertEqual([p["code"] for p in bare], ["expected_without_basis"])
        self.assertEqual(cs.expectation_problems(
            "על בסיס הביצועים שלכם בשלושת החודשים האחרונים, בתקציב הזה אנחנו מכוונים ל-35 עד 45 פניות"), [])
        self.assertEqual([p["code"] for p in cs.expectation_problems("זו הערכה: אנחנו מכוונים ל-40 פניות")],
                         ["estimate_not_a_range"])
        self.assertEqual(cs.expectation_problems("זו הערכה, בהנחה שפנייה עולה 50 עד 80 ₪: אנחנו מכוונים ל-35 עד 60 פניות"), [])
        self.assertEqual([p["code"] for p in cs.expectation_problems("You will get 40 leads this month.")],
                         ["expected_without_basis"])
        self.assertEqual(cs.expectation_problems("Based on your last 30 days, we are aiming for 35 to 45 enquiries."), [])
        # a report of what happened is not a promise
        for text in ("קיבלתם 40 פניות החודש", "היעד שסיכמנו: 40 פניות", "Last month you got 40 leads."):
            self.assertEqual(cs.expectation_problems(text), [], text)


class Enforce(unittest.TestCase):
    def test_it_fails_by_name_and_is_rewritten(self):
        told = []
        out = cs.enforce("אנחנו מריצים Performance Max בתקציב הזה.", "he", where="t", alert=told.append)
        self.assertNotIn("Performance Max", out)
        self.assertIn("חיפוש, יוטיוב, ג'ימייל, מפות ואתרים", out)
        self.assertIn('forbidden term "Performance Max" (pmax)', told[0])
        self.assertEqual(cs.enforce("ה-CTR עלה ל-3%.", "he"), "כמה אחוז מהאנשים שראו את המודעה לחצו עליה עלה ל-3%.")
        clean = "הקמפיין עלה לאוויר."
        self.assertIs(cs.enforce(clean, "he"), clean)                            # clean text is not touched

    def test_a_never_term_takes_its_sentence_and_an_unbased_promise_is_removed(self):
        self.assertEqual(cs.enforce("שיפרנו את ה-asset group. המודעות עלו.", "he"), "המודעות עלו.")
        self.assertEqual(cs.enforce("המודעות עלו. נביא לכם 40 פניות.", "he"), "המודעות עלו.")
        with self.assertRaises(cs.Blocked) as caught:                            # nothing clean is left: it does not go out
            cs.enforce("נביא לכם 40 פניות", "he")
        self.assertEqual(caught.exception.problems[0]["code"], "expected_without_basis")

    def test_the_callers_rewriter_goes_first_and_is_checked_too(self):
        seen = []

        def good(text, lines):
            seen.append(lines)
            return "המודעות שלכם יופיעו בכל המקומות של גוגל."
        self.assertEqual(cs.enforce("נריץ פרפורמנס מקס.", "he", rewrite=good), "המודעות שלכם יופיעו בכל המקומות של גוגל.")
        self.assertIn("pmax", seen[0][0])
        still_bad = cs.enforce("נריץ פרפורמנס מקס.", "he", rewrite=lambda text, lines: "נריץ PMax.")
        self.assertEqual(cs.problems(still_bad, "he"), [])                       # a rewrite that still fails is not trusted

        def boom(text, lines):
            raise RuntimeError("model down")
        self.assertEqual(cs.problems(cs.enforce("נריץ פרפורמנס מקס.", "he", rewrite=boom), "he"), [])
        calls = []
        rewrite = cs.model_rewriter(27, "he", call=lambda system, user: calls.append((system, user)) or {"text": "בסדר."})
        self.assertEqual(rewrite("נריץ Performance Max", ["x"]), "בסדר.")
        self.assertIn("plain_words", calls[0][1])
        self.assertIn("safe_claude_json_call", _src("core", "client_speech.py"))  # the one JSON gate

    def test_wired_on_every_way_out(self):
        agent = _src("agents", "client_agent.py")
        log = agent[agent.index("def log_communication("):agent.index("def get_communications(")]
        self.assertLess(log.index("client_speech.outbound("), log.index('.insert({'))
        self.assertIn('if direction == "outbound":', log)
        self.assertIn("return {}", log)                                          # blocked = nothing stored
        actions = _src("core", "agent_actions.py")
        card = actions[actions.index("def card(row: dict, actor: str"):actions.index("def _card(row: dict")]
        self.assertIn('if actor != "admin":', card)                              # staff see the precise terms
        self.assertIn("client_speech.enforce(out[key], lang, allowed=own", card)
        server = _src("core", "api_server.py")
        turn = server[server.index("def _client_chat_answer("):server.index('@app.post("/api/client-chat/prewarm")')]
        self.assertLess(turn.index("client_speech.allow_from(message)"), turn.index("answer_support_question("))
        self.assertLess(turn.index("client_speech.enforce("), turn.index('log_communication(client_id, "outbound", channel, result["reply"])'))
        self.assertIn("client_speech.model_rewriter(", turn)
        self.assertIn("§9", _src("core", "governance.py"))
        doctrine = _src("knowledge", "client-governance.md")
        self.assertIn("Speaking to the client", doctrine)
        self.assertIn("core/client_speech.py", doctrine)

    def test_the_outbound_backstop(self):
        from core import agent_base
        saved, alerts = agent_base.agent_alert, []
        agent_base.agent_alert = lambda name, lines: alerts.extend(lines)
        try:
            self.assertEqual(cs.outbound("הכול מוכן."), "הכול מוכן.")
            self.assertEqual(alerts, [])
            self.assertNotIn("ROAS", cs.outbound("ה-ROAS שלכם עלה."))
            self.assertEqual(cs.outbound("נביא לכם 40 פניות"), "")               # blocked: the caller stores nothing
            self.assertTrue(any("BLOCKED" in line for line in alerts))
        finally:
            agent_base.agent_alert = saved

    def test_every_fixed_card_text_passes(self):
        from core import agent_actions

        def strings(node, path=""):
            if isinstance(node, dict):
                if set(node) >= {"he", "en"} and all(isinstance(v, str) for v in node.values()):
                    for lang, value in node.items():
                        yield path, lang, value
                else:
                    for key, value in node.items():
                        if key not in SKIP_KEYS:
                            yield from strings(value, f"{path}.{key}")
            elif isinstance(node, (list, tuple)):
                for i, value in enumerate(node):
                    yield from strings(value, f"{path}[{i}]")
        bad = [(path, lang, cs.problem_lines(cs.problems(text, lang)))
               for name, spec in agent_actions.ACTIONS.items() for path, lang, text in strings(spec, name)
               if cs.problems(text, lang)]
        self.assertEqual(bad, [])

    def test_a_card_never_shows_a_term_but_staff_see_it(self):
        from core import agent_actions
        saved = agent_actions._alert
        alerts = []
        agent_actions._alert = alerts.append
        agent_actions.ACTIONS["_t_speech"] = {"title": {"he": "קמפיין Performance Max", "en": "A Performance Max campaign"},
                                              "what": {"he": "נביא לכם 40 פניות", "en": "We will bring you 40 leads"},
                                              "approver": "client", "kind": "execute"}
        try:
            row = {"id": 1, "client_id": 27, "action": "_t_speech", "status": "proposed", "params": {}}
            shown = agent_actions.card(row, "client", "he")
            self.assertNotIn("Performance Max", shown["title"])
            self.assertEqual(shown["what"], "")                                  # an unbased promise is not shown at all
            self.assertTrue(any("pmax" in line for line in alerts))
            self.assertEqual(agent_actions.card(row, "admin", "he")["title"], "קמפיין Performance Max")
        finally:
            del agent_actions.ACTIONS["_t_speech"]
            agent_actions._alert = saved


HISTORY = {"cost": 2400.0, "conversions": 32.0, "clicks": 610}


def presentation(**over):
    base = {"campaign_type": "pmax", "objective": "leads", "budget_total": 3000, "days": 30,
            "expected": cp.from_history("leads", 3000, HISTORY)}
    base.update(over)
    return cp.build(base["campaign_type"], base["objective"], base["budget_total"], base["days"], base["expected"])


class Presentation(unittest.TestCase):
    def test_all_four_or_it_does_not_go_out(self):
        self.assertEqual(cp.problems(presentation()), [])
        for over, code in (({"campaign_type": "mystery"}, "where_missing"), ({"budget_total": None}, "budget_missing"),
                           ({"budget_total": 0}, "budget_missing"), ({"objective": ""}, "goal_missing"),
                           ({"expected": None}, "expected_missing"),
                           ({"expected": {"low": 35, "high": 45, "unit": "leads", "basis": {}}}, "basis_missing"),
                           ({"expected": {"low": 35, "high": 45, "unit": "leads", "basis": {"kind": "history"}}}, "basis_incomplete"),
                           ({"expected": {"low": 35, "high": 45, "unit": "leads", "basis": {"kind": "benchmark"}}}, "basis_incomplete"),
                           ({"expected": {"low": 40, "high": 40, "unit": "leads",
                                          "basis": {"kind": "estimate", "cost_low": 50, "cost_high": 80, "assumptions": "x"}}},
                            "estimate_not_a_range"),
                           ({"expected": {"low": 35, "high": 60, "unit": "leads",
                                          "basis": {"kind": "estimate", "cost_low": 50, "cost_high": 80}}}, "assumptions_missing")):
            p = presentation(**over)
            self.assertIn(code, cp.problems(p), over)
            with self.assertRaises(cp.PresentationIncomplete) as caught:
                cp.render(p, "he")
            self.assertIn(code, caught.exception.codes)

    def test_the_four_in_order_in_every_language_and_clean(self):
        for expected in (cp.from_history("leads", 3000, HISTORY),
                         cp.from_estimate("leads", 3000, 60, 110, "עסק שירות מקומי, בתחום עם תחרות בינונית")):
            for lang in LANGS:
                text = cp.render(presentation(expected=expected), lang)
                lines = text.split("\n")
                self.assertEqual(len(lines), 4, text)
                self.assertEqual(cs.problems(text, lang), [], text)
                self.assertEqual(cp.check_text(text, lang), [])
        he = cp.render(presentation(), "he").split("\n")
        self.assertTrue(he[0].startswith("איפה זה יופיע"))
        self.assertIn("יוטיוב", he[0])
        self.assertNotIn("Performance", he[0])
        self.assertTrue(he[1].startswith("התקציב") and "ליום" in he[1])
        self.assertTrue(he[2].startswith("המטרה"))
        self.assertTrue(he[3].startswith("מה אנחנו מצפים"))

    def test_history_first_then_an_estimate_as_a_range_with_its_assumptions(self):
        e = cp.from_history("leads", 3000, HISTORY)                      # 75 ₪ a lead -> aim 40, margin 15% -> 34
        self.assertEqual((e["low"], e["high"], e["basis"]["kind"], e["basis"]["period_days"]), (34, 40, "history", 30))
        self.assertIn("ב-30 הימים האחרונים", cp.render(presentation(expected=e), "he"))
        for thin in ({}, {"cost": 0, "conversions": 0}, {"cost": 500, "conversions": 2}):
            self.assertIsNone(cp.from_history("leads", 3000, thin), thin)       # too little to stand on
        self.assertIsNone(cp.from_history("awareness", 3000, HISTORY))
        est = cp.from_estimate("leads", 3000, 60, 110, "עסק שירות מקומי")
        self.assertEqual((est["low"], est["high"], est["basis"]["kind"]), (27, 50, "estimate"))
        text = cp.render(presentation(expected=est), "he")
        self.assertIn("הערכה ולא התחייבות", text)
        self.assertIn("עסק שירות מקומי", text)
        for bad in ((80, 80), (80, 85), (0, 100), (None, None)):
            self.assertIsNone(cp.from_estimate("leads", 3000, bad[0], bad[1], "x"), bad)   # a single figure is not an estimate
        jargon = cp.from_estimate("leads", 3000, 60, 110, "לפי ה-asset group")
        with self.assertRaises(cp.PresentationIncomplete):
            cp.render(presentation(expected=jargon), "he")

    def test_the_campaign_card_carries_the_presentation(self):
        from core import agent_actions as aa
        spec = aa.ACTIONS["prepare_campaign"]
        for lang in LANGS:
            self.assertTrue(spec["what"][lang].startswith("{presentation}\n"), lang)
        params = {"campaign_type": "pmax", "objective": "leads", "audience_segment": "home owners",
                  "landing_page_url": "https://example.com/", "offer": "ייעוץ ראשון", "budget_total": 3000, "days": 30,
                  "expected": cp.from_history("leads", 3000, HISTORY)}
        saved = aa._lang
        aa._lang = lambda cid: "he"
        try:
            shown = spec["display"](27, params)
            self.assertEqual(shown["presentation"], cp.render(presentation(), "he"))
            self.assertEqual(spec["validate_stored"](27, params)["budget_total"], 3000.0)
            for missing in ("budget_total", "expected"):
                with self.assertRaises(ValueError):
                    spec["validate_stored"](27, {k: v for k, v in params.items() if k != missing})
        finally:
            aa._lang = saved
        source = _src("core", "media_order_actions.py")
        prepare = source[source.index("    def prepare("):source.index("    def display(")]
        self.assertLess(prepare.index('ask["budget"]'), prepare.index("campaign_presentation.render("))
        self.assertLess(prepare.index("campaign_presentation.from_history("), prepare.index("campaign_presentation.from_estimate("))
        self.assertIn('refusals["presentation"]', prepare)
        self.assertEqual(set(spec["refusals"]["presentation"]), set(LANGS))


def sample() -> str:
    """The Performance Max presentation for client 27, as the card renders it.
    The INPUTS are sample values (no production data is readable from here)."""
    p = cp.build("pmax", "sales", 3000, 30,
                 cp.from_estimate("sales", 3000, 90, 160, "מוצר במחיר של כמה מאות שקלים, בתחום שיש בו לא מעט מפרסמים"))
    return cp.render(p, "he")


if __name__ == "__main__":
    if "--show" in sys.argv:
        sys.argv.remove("--show")
        import io
        out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        text = sample()
        out.write(text + "\n\nchecks: " + str({"forbidden terms": cs.find(text, "he"), "expectation": cs.expectation_problems(text),
                                                "four elements": cp.problems(cp.build("pmax", "sales", 3000, 30, cp.from_estimate(
                                                    "sales", 3000, 90, 160, "x")))}) + "\n")
        out.flush()
        sys.exit(0)
    unittest.main()
