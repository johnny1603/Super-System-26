"""The two reports the Google specialist was missing (2026-11-29, core/search_reports).

- search terms: what people typed, as a DECISION list (exclude / add) that exists
  only when the account measures results;
- impression share: never one number — lost to budget and lost to rank are
  opposite actions, and more budget is never attached to a rank-led loss;
- Performance Max is never conflated: no typed-searches list, and no share
  shown as a zero;
- client words in five languages, the strategy, the monthly report, the craft layer.
"""
import os
import sys
import unittest
from datetime import date

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from core import client_speech as cs, google_page as gp, search_reports as sr  # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def term(text, clicks, cost, conversions=0.0, status="NONE", keyword="נעלי ריצה", campaign="11", impressions=200):
    return {"searchTermView": {"searchTerm": text, "status": status}, "campaign": {"id": campaign, "name": "חיפוש נעליים"},
            "segments": {"keyword": {"info": {"text": keyword}}},
            "metrics": {"impressions": str(impressions), "clicks": str(clicks), "costMicros": str(int(cost * 1_000_000)),
                        "conversions": conversions}}


def campaign(cid, channel="SEARCH", share=0.4, budget=0.35, rank=0.25, impressions=900, cost=300.0, name=None, **more):
    metrics = {"impressions": str(impressions), "costMicros": str(int(cost * 1_000_000))}
    for key, value in (("searchImpressionShare", share), ("searchBudgetLostImpressionShare", budget),
                       ("searchRankLostImpressionShare", rank), ("searchTopImpressionShare", more.get("top")),
                       ("searchAbsoluteTopImpressionShare", more.get("first"))):
        if value is not None:
            metrics[key] = value
    return {"campaign": {"id": cid, "name": name or f"קמפיין {cid}", "status": "ENABLED", "advertisingChannelType": channel},
            "metrics": metrics}


TERMS = [term("נעלי ריצה זולות", 22, 61.5), term("נעלי ריצה לנשים", 9, 40.0, conversions=2.0),
         term("תיקון נעליים", 14, 35.0, status="EXCLUDED"), term("נעלי ריצה", 30, 80.0, conversions=1.0, status="ADDED"),
         term("נעליים", 4, 9.0), term("נעלי ריצה זולות", 3, 8.5, keyword="נעליים זולות")]


class Stored(unittest.TestCase):
    NAMES = ("_read_store", "_write_store", "_connection", "_search", "_measuring")

    def setUp(self):
        self.saved = {n: getattr(sr, n) for n in self.NAMES}
        self.store, self.calls, self.state = {}, [], "measuring"
        self.google = {"terms": TERMS, "shares": [campaign("11", top=0.6, first=0.2), campaign("22", "PERFORMANCE_MAX", None, None, None)]}
        sr._read_store = lambda cid: self.store.get(cid, {})
        sr._write_store = lambda cid, value: self.store.__setitem__(cid, value)
        sr._connection = lambda cid: {"access_token": "t", "account_id": "111"}
        sr._measuring = lambda cid: self.state

        def search(conn, gaql):
            self.calls.append(gaql)
            return self.google["terms" if "FROM search_term_view" in gaql else "shares"]
        sr._search = search
        sr._cache.clear()

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(sr, name, fn)
        sr._cache.clear()


class SearchTerms(Stored):
    def test_one_row_per_typed_term_with_the_phrase_that_matched(self):
        found = sr.terms(TERMS)
        top = found[0]
        self.assertEqual((top["term"], top["clicks"], top["cost"]), ("נעלי ריצה", 30, 80.0))
        cheap = [t for t in found if t["term"] == "נעלי ריצה זולות"][0]
        self.assertEqual((cheap["clicks"], cheap["cost"], cheap["matched"]), (25, 70.0, "נעלי ריצה"))   # two phrases matched: merged
        self.assertEqual({t["term"]: t["status"] for t in found}["תיקון נעליים"], "excluded")
        self.assertEqual({t["term"]: t["status"] for t in found}["נעלי ריצה"], "added")

    def test_the_output_is_a_decision_list(self):
        out = sr.decide(sr.terms(TERMS), "measuring")
        self.assertEqual([t["term"] for t in out["exclude"]], ["נעלי ריצה זולות"])     # cost money, produced nothing
        self.assertEqual([t["term"] for t in out["add"]], ["נעלי ריצה לנשים"])         # produced results, not yet a phrase
        # already excluded / already a phrase / too little spent to judge: none of them is a candidate
        self.assertNotIn("תיקון נעליים", str(out))
        self.assertNotIn("'נעליים'", str([t["term"] for t in out["exclude"]]))

    def test_no_conclusion_in_an_account_that_is_not_measuring(self):
        for state in ("not_measured", "configured_silent", "unknown", "not_connected", ""):
            out = sr.decide(sr.terms(TERMS), state)
            self.assertEqual((out["exclude"], out["add"]), ([], []), state)
            self.assertTrue(out["gated"])
        self.state = "not_measured"
        value = sr.refresh(27)
        self.assertEqual(value["decisions"]["gated"], "not_measured")
        self.assertEqual(len(value["terms"]), 5)                                      # read and displayed all the same

    def test_a_stored_conclusion_does_not_outlive_the_measurement(self):
        sr.refresh(27)
        self.assertTrue(sr.candidates(27))
        self.state = "configured_silent"                                              # the account stopped counting
        self.assertEqual(sr.candidates(27), {})
        block = sr.prompt_block(27)["search_terms"]
        self.assertIn("not measuring", block["conclusions_withheld"])
        self.assertNotIn("results", block["most_expensive"][0])                       # a zero that means nothing is not passed on


class ImpressionShare(Stored):
    def test_never_one_number(self):
        found = sr.shares(self.google["shares"])
        search = found[0]
        self.assertEqual((search["share"], search["lost_budget"], search["lost_rank"], search["dominant"]), (0.4, 0.35, 0.25, "budget"))
        for lang in LANGS:
            rows = sr.share_rows(search, lang)
            labels = [r["label"] for r in rows]
            self.assertIn(cs.words("lost_budget", lang), labels, lang)
            self.assertIn(cs.words("lost_rank", lang), labels, lang)
        # a share with no split is not shown as a share at all
        partial = sr.shares([campaign("33", share=0.23, budget=None, rank=None)])[0]
        self.assertFalse(partial["available"])
        self.assertNotIn("share", partial)

    def test_which_loss_leads(self):
        self.assertEqual(sr.dominant(0.35, 0.10), "budget")
        self.assertEqual(sr.dominant(0.10, 0.45), "rank")
        self.assertEqual(sr.dominant(0.30, 0.28), "both")
        self.assertEqual(sr.dominant(0.04, sr.LOW_MARK), "none")                      # "under 10%" is not a loss to act on
        self.assertEqual(sr.dominant(None, None), "none")

    def test_more_budget_is_never_attached_to_a_rank_led_loss(self):
        for lang in LANGS:
            budget, rank, both = sr.T["act_budget"][lang], sr.T["act_rank"][lang], sr.T["act_both"][lang]
            self.assertEqual(sr.action_line({"dominant": "rank"}, "measuring", lang), rank)
            self.assertEqual(sr.action_line({"dominant": "both"}, "measuring", lang), both)
            self.assertEqual(sr.action_line({"dominant": "budget"}, "measuring", lang), budget)
            # ...and never before the account measures what a showing brings
            self.assertEqual(sr.action_line({"dominant": "budget"}, "not_measured", lang), sr.T["act_gated"][lang])
            self.assertEqual(sr.action_line({"dominant": "rank"}, "not_measured", lang), rank)
        self.assertIn("NEVER recommend more budget", sr.RULE)
        self.assertIn("NEVER propose more budget for a campaign whose", gp.STRATEGY_SYSTEM)

    def test_performance_max_is_said_not_zeroed(self):
        pmax = sr.shares(self.google["shares"])[1]
        self.assertEqual((pmax["channel"], pmax["available"]), ("PERFORMANCE_MAX", False))
        shown = sr.view(27, "he", sr.refresh(27))
        card = shown["share"]["campaigns"][1]
        self.assertEqual((card["rows"], card["note"]), ([], sr.T["is_unavailable"]["he"]))
        self.assertIn(sr.T["st_pmax"]["he"], shown["terms"]["notes"])                 # no typed-searches list for it, and why
        self.assertEqual(sr.percent(sr.LOW_MARK, "he"), "פחות מ-10%")
        self.assertEqual(sr.percent(None, "he"), "")
        self.assertNotIn("search_term_insight", sr.TERMS_GAQL + sr.SHARE_GAQL)        # category insights are not read as search terms
        self.assertIn("advertising_channel_type = 'SEARCH'", sr.TERMS_GAQL)


class TheRead(Stored):
    def test_two_operations_stored(self):
        value = sr.refresh(27)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((value["terms_total"], len(value["shares"]), value["measurement"]), (5, 2, "measuring"))
        self.assertEqual(self.store[27]["decisions"]["exclude"][0]["term"], "נעלי ריצה זולות")
        # each read goes through google_ads_service.search, which counts the operation (core/google_ads_quota)
        self.assertIn("gads.search(conn[\"access_token\"], conn[\"account_id\"], gaql)", _read("core", "search_reports.py"))
        self.assertIn("_count_operation", _read("core", "google_ads_service.py"))

    def test_a_refused_query_concludes_nothing(self):
        def search(conn, gaql):
            if "search_term_view" in gaql:
                raise RuntimeError("UNRECOGNIZED_FIELD")
            return self.google["shares"]
        sr._search = search
        value = sr.refresh(27)
        self.assertIn("UNRECOGNIZED_FIELD", value["terms_error"])
        self.assertNotIn("decisions", value)
        self.assertEqual(len(value["shares"]), 2)                                     # the other report still stands
        self.assertEqual(sr.view(27, "he", value)["terms"]["empty"], sr.T["unreadable"]["he"])

    def test_no_campaign_has_run_is_said_as_expected(self):
        self.google = {"terms": [], "shares": [campaign("11", impressions=0)]}
        shown = sr.view(27, "he", sr.refresh(27))
        self.assertEqual(shown["terms"]["empty"], sr.T["st_empty"]["he"])
        self.assertEqual(shown["share"]["empty"], sr.T["is_empty"]["he"])
        sr._connection = lambda cid: {}
        self.assertEqual(sr.view(27, "he", sr.refresh(27)), {})


class ClientWords(Stored):
    def test_the_four_rows(self):
        self.assertEqual(cs.words("search_terms", "he"), "מה אנשים חיפשו")
        self.assertEqual(cs.words("search_terms", "he", "long"), "המילים שאנשים הקלידו בגוגל כשהמודעה שלכם הוצגה")
        self.assertEqual(cs.words("impression_share", "he"), "נתח החשיפות")
        self.assertEqual(cs.words("impression_share", "he", "long"), "איזה אחוז מהחיפושים הרלוונטיים המודעה שלכם הופיעה בהם")
        self.assertEqual(cs.words("lost_budget", "he"), "הפסדנו בגלל תקציב")
        self.assertEqual(cs.words("lost_rank", "he"), "הפסדנו בגלל דירוג")
        for key in ("search_terms", "impression_share", "lost_budget", "lost_rank"):
            for lang in LANGS:
                for form in ("short", "long"):
                    text = cs.words(key, lang, form)
                    self.assertTrue(text, (key, lang, form))
                    self.assertEqual(cs.find(text, lang), [], (key, lang, form))      # the plain words are not themselves jargon
        self.assertTrue(cs.find("ה-impression share ירד", "he"))
        self.assertTrue(cs.find("the search terms report shows it", "en"))

    def test_everything_shown_is_plain_in_five_languages(self):
        value = sr.refresh(27)
        for lang in LANGS:
            shown = sr.view(27, lang, value)
            texts = [shown["terms"]["title"], shown["terms"]["long"], *shown["terms"]["notes"], shown["share"]["title"], shown["share"]["long"]]
            texts += [g["title"] for g in shown["terms"]["groups"]] + [r["why"] for g in shown["terms"]["groups"] for r in g["rows"]]
            texts += shown["terms"]["table"]["columns"]
            for card in shown["share"]["campaigns"]:
                texts += [card["action"], card["note"]] + [r["label"] for r in card["rows"]]
            for text in filter(None, texts):
                self.assertEqual(cs.problems(text, lang), [], (lang, text))
            self.assertEqual([g["kind"] for g in shown["terms"]["groups"]], ["exclude", "add"])
        for key, row in sr.T.items():
            self.assertEqual(set(row), set(LANGS), key)
            self.assertTrue(all(row.values()), key)
            for lang in LANGS:
                self.assertEqual(cs.find(row[lang], lang), [], (key, lang))

    def test_not_measuring_shows_the_searches_and_withholds_the_decisions(self):
        self.state = "not_measured"
        shown = sr.view(27, "he", sr.refresh(27))
        self.assertEqual(shown["terms"]["groups"], [])
        self.assertIn(sr.T["st_gated"]["he"], shown["terms"]["notes"])
        self.assertEqual(len(shown["terms"]["table"]["rows"]), 5)
        self.assertEqual(shown["share"]["campaigns"][0]["action"], sr.T["act_gated"]["he"])

    def test_the_monthly_report_keeps_the_split_and_its_action(self):
        sr.refresh(27)
        kept = sr.snapshot(27)
        rows = sr.report_rows(kept, "he")
        flat = " | ".join(a + " " + b for a, b in rows)
        for needed in ("הפסדנו בגלל תקציב 35%", "הפסדנו בגלל דירוג 25%", sr.T["act_budget"]["he"], sr.T["is_unavailable"]["he"],
                       sr.T["st_exclude"]["he"], "נעלי ריצה זולות"):
            self.assertIn(needed, flat, needed)
        self.assertEqual(sr.report_rows({}, "he"), [])


class TheStrategy(Stored):
    RAW = {"money": "כל התקציב הולך למודעות בחיפוש של גוגל, כי מי שמחפש נעלי ריצה כבר רוצה לקנות עכשיו.",
           "assumes": ["רוב הקונים מזמינים מהאתר."],
           "watching": [{"number": "כמה מכירות הגיעו מהמודעות", "why": "זו המטרה."}, {"number": "כמה עולה כל מכירה", "why": "כדאיות."}],
           "change_course": [{"if": "אם עלות כל מכירה גבוהה מהרווח", "by": "2027-01-15", "then": "נעצור ונשנה."}]}

    def test_yoav_decides_only_what_code_offered(self):
        sr.refresh(27)
        offered = sr.candidates(27)
        waste = [k for k, v in offered.items() if v["group"] == "exclude"][0]
        works = [k for k, v in offered.items() if v["group"] == "add"][0]
        raw = {**self.RAW, "term_decisions": [
            {"id": waste, "decision": "exclude", "why": "מי שמחפש את זה רוצה מחיר שאין לנו."},
            {"id": works, "decision": "exclude", "why": "סתם."},                         # a working term is never excluded
            {"id": "made-up", "decision": "exclude", "why": "לא מועמד."}]}
        out = gp.clean_strategy(raw, [], [], {}, "he", date(2026, 11, 29), speak=lambda text: text, candidates=offered)
        self.assertEqual([(d["text"], d["decision"]) for d in out["term_decisions"]], [("נעלי ריצה זולות", "exclude")])
        none = gp.clean_strategy(raw, [], [], {}, "he", date(2026, 11, 29), speak=lambda text: text)
        self.assertEqual(none["term_decisions"], [])

    def test_the_excluded_list_is_filled_from_the_account(self):
        seen = sr.view(27, "he", sr.refresh(27))
        shown = gp.terms_view([], {"to_add": [{"text": "נעלי ריצה לנשים", "why": "מביא מכירות.", "applied": False}]}, "he", seen)
        self.assertEqual(shown["excluded"], [{"text": "תיקון נעליים", "why": sr.T["x_in_account"]["he"], "state": gp.TEXT["x_done"]["he"]}])
        self.assertEqual(shown["excluded_empty"], "")
        self.assertEqual(shown["to_add"][0]["state"], sr.T["add_pending"]["he"])
        self.assertEqual(gp.terms_view([], {}, "he")["excluded"], [])                   # nothing read: the page still says "none yet"

    def test_wiring(self):
        page = _read("core", "google_page.py")
        for needle in ('"search_reports": _search_block(client_id)', "candidates=_search_candidates(client_id)",
                       '"search": searched', "search_reports.report_rows(report[\"search\"], lang)",
                       '"search_terms": seen.get("terms")', '"demand_share": seen.get("share")'):
            self.assertIn(needle, page, needle)
        self.assertIn("search_reports.refresh(client_id, conn, measure.get(\"state\"))", _read("agents", "google_ads_agent.py"))
        self.assertIn('"/api/admin/google-ads/search-reports"', _read("core", "api_server.py"))
        for name in ("client_deletion.py", "deletion_guard.py", "admin_service.py"):
            self.assertIn("search_reports:", _read("core", name), name)
        client = _read("dashboard", "client", "index.html")
        for needle in ("function ggSearched(", "function ggShare(", "d.demand_share", "d.search_terms", "s.to_add"):
            self.assertIn(needle, client, needle)
        self.assertIn("gaSearchBtn", _read("dashboard", "admin", "connections", "index.html"))
        craft = _read("knowledge", "paid-media-craft.md")
        for line in ("Impression share is never reported without its split", "read for waste first and opportunity second",
                     "Check that first, always"):
            self.assertIn(line, craft, line)


if __name__ == "__main__":
    unittest.main()
