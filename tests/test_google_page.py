"""The client's Google page (core/google_page, docs/GOOGLE-PAGE.md): five
sections under governance §9. No database, no Google call, no model: the
module's doors are replaced.

Run: python tests/test_google_page.py
"""
import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import client_speech as cs  # noqa: E402
from core import google_page as gp  # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
TODAY = date(2026, 11, 28)
UNTIL = TODAY - timedelta(days=1)
cs._count = lambda kind, agent: None


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _strings(value)


PMAX = {"id": "abc", "campaign_type": "pmax", "offer": "מבצע חורף", "status": "verified",
        "texts": {"headline": ["כותרת א", "כותרת ב", "כותרת ג"], "long_headline": ["כותרת ארוכה"],
                  "description": ["תיאור א", "תיאור ב"], "business_name": "קפה נועה"},
        "delivery": {"manifest": {"assets": [{"slot": "landscape", "n": 1, "file_id": "F1"}, {"slot": "square", "n": 1, "file_id": "F2"},
                                             {"slot": "logo_square", "n": 1, "file_id": "L1"}]}}}
VIDEO = {"id": "vid", "campaign_type": "youtube", "offer": "סרטון", "status": "verified", "texts": {},
         "delivery": {"manifest": {"assets": [{"slot": "video", "n": 1, "youtube_url": "https://youtu.be/abcdefghijk"}]}}}
SPEC = {"name": "חיפוש קפה", "keywords": [{"text": "קפה בתל אביב", "match_type": "PHRASE"}, {"text": "בית קפה קרוב", "match_type": "EXACT"},
                                           "אספרסו"], "headlines": ["קפה טרי", "קלייה מקומית"], "descriptions": ["בואו לטעום", "פתוח כל יום"]}
DRAFT = {"agent_name": "google_ads_agent", "action_type": "google_ads_campaign_drafted", "result": {"spec": SPEC}}
SEARCH_ID = "s-" + __import__("hashlib").sha1("חיפוש קפה".encode("utf-8")).hexdigest()[:10]


def _days(cost=100.0, results=2.0, n=70):
    return [{"date": (UNTIL - timedelta(days=i)).isoformat(), "impressions": 1000, "clicks": 40, "cost": cost, "conversions": results}
            for i in range(n)]


class Page(unittest.TestCase):
    def setUp(self):
        self.kept = {}
        self.orders, self.activity, self.alerts = [PMAX], [DRAFT], []
        self.daily = {"connected": False}
        self.per = {"connected": False}
        self.budget = None
        saved = {name: getattr(gp, name) for name in ("_read", "_write", "_orders", "_activity", "_daily", "_per_campaign", "_budget",
                                                      "_alert", "_pending_choice")}
        self.addCleanup(lambda: [setattr(gp, k, v) for k, v in saved.items()])
        gp._read = lambda cid: __import__("copy").deepcopy(self.kept)
        gp._write = lambda cid, value: self.kept.clear() or self.kept.update(__import__("copy").deepcopy(value))
        gp._orders = lambda cid: self.orders
        gp._activity = lambda cid: self.activity
        gp._daily = lambda cid: self.daily
        gp._per_campaign = lambda cid: self.per
        gp._budget = lambda cid: self.budget
        gp._alert = lambda lines: self.alerts.extend(lines)
        gp._pending_choice = lambda cid: False
        saved_action = cs._read_action
        cs._read_action = lambda cid: "sales"
        cs._action_cache.clear()
        self.addCleanup(lambda: (setattr(cs, "_read_action", saved_action), cs._action_cache.clear(), cs.speak_for(None)))

    def live(self, lanes=None):
        self.daily = {"connected": True, "since": "x", "until": UNTIL.isoformat(), "days": _days(), "pulled_at": "2026-11-28T06:00:00+00:00"}
        self.per = {"connected": True, "campaigns": lanes if lanes is not None else [
            {"id": "111", "impressions": 20000, "clicks": 800, "cost": 2000.0, "conversions": 40.0},
            {"id": "222", "impressions": 10000, "clicks": 300, "cost": 1000.0, "conversions": 10.0}]}
        self.activity = [DRAFT,
                         {"agent_name": "google_ads_agent", "action_type": "campaign_created", "details": {"name": "a", "lane": "system"},
                          "result": {"campaign_id": "111"}},
                         {"agent_name": "google_ads_agent", "action_type": "campaign_created", "details": {"name": "b", "lane": "client"},
                          "result": {"campaign_id": "222"}}]
        self.budget = 4000.0

    # ── every line the page can say obeys §9, in five languages ───────────────

    def test_no_line_of_the_page_uses_platform_vocabulary(self):
        for key, row in gp.TEXT.items():
            self.assertEqual(set(row), set(LANGS), key)
            for lang in LANGS:
                self.assertTrue(row[lang].strip(), (key, lang))
                self.assertEqual(cs.find(row[lang], lang), [], (key, lang, row[lang]))
        for lang in ("en", "fr", "ar", "ru"):
            self.assertNotEqual(gp.TEXT["google_decides"][lang], gp.TEXT["google_decides"]["he"])
        self.assertIn("written without a native speaker", _src("core", "google_page.py"))

    def test_the_whole_view_is_clean_in_every_state(self):
        for prepare in (lambda: None, self.live):
            prepare()
            for lang in LANGS:
                for text in _strings(gp.view(27, lang, TODAY, remember=False)):
                    if not text.startswith(("/api/", "https://")):
                        self.assertEqual(cs.find(text, lang), [], (lang, text))

    # ── 1: the ads ────────────────────────────────────────────────────────────

    def test_the_full_asset_set_and_examples_that_are_called_examples(self):
        self.orders = [PMAX, VIDEO]
        ads = gp.view(27, "he", TODAY)["ads"]
        self.assertEqual(ads["google_decides"], "גוגל מחליטה בכל הצגה איזה שילוב להראות, לפי מה שעובד.")
        pmax = ads["campaigns"][0]
        self.assertEqual([i["url"] for i in pmax["images"]], ["/api/client/media-drive/thumb/F1", "/api/client/media-drive/thumb/F2"])
        self.assertTrue(all(i["label"] for i in pmax["images"]))                 # each image says its ratio
        self.assertEqual(len(pmax["logos"]), 1)
        self.assertEqual({f["label"]: len(f["lines"]) for f in pmax["texts"]},
                         {"כותרות": 3, "כותרות ארוכות": 1, "תיאורים": 2, "שם העסק": 1})
        self.assertEqual(len(pmax["examples"]), 3)
        for n, example in enumerate(pmax["examples"], 1):
            self.assertEqual(example["label"], f"דוגמה {n} לשילוב אפשרי. זו לא המודעה.")
        self.assertEqual(len({(e["headline"], e["description"], e["image"]) for e in pmax["examples"]}), 3)
        self.assertNotIn("Performance", pmax["title"])
        video = ads["campaigns"][1]                                              # a video is not assembled: no examples
        self.assertEqual((video["assembled"], video["examples"], video["videos"]), (False, [], ["https://youtu.be/abcdefghijk"]))
        self.assertEqual(video["note"], gp.TEXT["video_is_ad"]["he"])

    def test_a_rejected_order_is_not_shown_and_nothing_prepared_says_so(self):
        self.orders, self.activity = [{**PMAX, "status": "rejected"}], []
        ads = gp.view(27, "he", TODAY)["ads"]
        self.assertEqual((ads["campaigns"], ads["google_decides"]), ([], ""))
        self.assertEqual(ads["empty"], gp.TEXT["no_campaigns"]["he"])

    # ── 2: the numbers ────────────────────────────────────────────────────────

    def test_no_connection_says_what_is_missing_and_what_it_takes_never_empty_charts(self):
        numbers = gp.view(27, "he", TODAY)["numbers"]
        self.assertEqual(numbers["state"], "not_connected")
        self.assertEqual((numbers["missing"], numbers["needs"]), (gp.TEXT["c_not_connected"]["he"], gp.TEXT["n_not_connected"]["he"]))
        self.assertNotIn("budget", numbers)
        self.assertNotIn("lanes", numbers)
        self.assertTrue(gp.view(27, "he", TODAY)["reports"]["blocked"])
        for state, daily in (("reconnect", {"connected": True, "needs_reconnect": True}), ("failed", {"connected": True, "error": "x"})):
            self.daily = daily
            self.assertEqual(gp.view(27, "he", TODAY)["numbers"]["state"], state)
        gp._pending_choice = lambda cid: True
        self.daily = {"connected": False}
        self.assertEqual(gp.view(27, "he", TODAY)["numbers"]["missing"], gp.TEXT["c_choose_account"]["he"])

    def test_the_budget_and_the_two_lanes_separately_never_one_number(self):
        self.live()
        numbers = gp.view(27, "he", TODAY)["numbers"]
        budget = {r["label"]: r["value"] for r in numbers["budget"]}
        spent = 100 * UNTIL.day
        self.assertEqual(budget["התקציב החודשי שסיכמנו"], "₪4,000")
        self.assertEqual(budget["הוצא החודש עד עכשיו"], f"₪{spent:,}")
        self.assertEqual(budget["נשאר"], f"₪{4000 - spent:,}")
        self.assertEqual(budget["קצב יומי (ממוצע של 7 ימים)"], "₪100")
        self.assertEqual(budget["בקצב הזה התקציב יספיק לעוד"], f"{(4000 - spent) // 100} ימים")
        self.assertEqual(budget["ימים שנשארו בחודש"], f"{30 - UNTIL.day} ימים")
        lanes = {block["lane"]: {r["label"]: r["value"] for r in block["rows"]} for block in numbers["lanes"]}
        self.assertEqual(sorted(lanes), ["client", "system"])                    # no unrecorded campaign: no such block
        self.assertEqual((lanes["system"]["מכירות"], lanes["system"]["הוצאה"], lanes["system"]["עלות לכל אחת"]), ("40", "₪2,000", "₪50"))
        self.assertEqual((lanes["client"]["מכירות"], lanes["client"]["הוצאה"], lanes["client"]["עלות לכל אחת"]), ("10", "₪1,000", "₪100"))
        self.assertEqual(lanes["system"]["אחוז ההקלקות"], "4%")                  # the SHORT forms, never the acronyms
        self.assertEqual(lanes["system"]["עלות לקליק"], "₪2.5")
        flat = " ".join(_strings(numbers))
        for blended in ("50", "3,000", "₪60"):                                   # 40+10, 2000+1000, 3000/50
            self.assertNotIn(f" {blended} ", f" {flat} ".replace("₪50", "x"), blended)
        self.assertEqual(numbers["trend_mixed"], gp.TEXT["t_mixed"]["he"])        # two lanes active: no single trend
        self.assertFalse(any(block.get("trend") for block in numbers["lanes"]))
        self.assertEqual(numbers["lanes_note"], gp.TEXT["lanes_note"]["he"])

    def test_one_active_lane_gets_its_trend_and_an_unrecorded_campaign_is_never_guessed_into_a_lane(self):
        self.live(lanes=[{"id": "111", "impressions": 100, "clicks": 10, "cost": 50.0, "conversions": 1.0}])
        self.daily["days"] = _days(results=3.0, n=30) + [
            {"date": (UNTIL - timedelta(days=i)).isoformat(), "impressions": 1, "clicks": 1, "cost": 1.0, "conversions": 2.0} for i in range(30, 60)]
        numbers = gp.view(27, "he", TODAY)["numbers"]
        system = next(b for b in numbers["lanes"] if b["lane"] == "system")
        self.assertEqual(system["trend"], "מכירות: 50% יותר מ-30 הימים שלפני כן")
        self.assertEqual(numbers["trend_mixed"], "")
        self.assertEqual(next(b for b in numbers["lanes"] if b["lane"] == "client")["empty"], gp.TEXT["lane_empty"]["he"])
        self.live(lanes=[{"id": "999", "impressions": 100, "clicks": 10, "cost": 50.0, "conversions": 1.0}])
        blocks = gp.view(27, "he", TODAY)["numbers"]["lanes"]
        self.assertEqual([b["lane"] for b in blocks if b["rows"]], ["unrecorded"])
        self.assertEqual(gp.recorded_lanes([{"action_type": "campaign_created", "details": {"lane": "guess"}, "result": {"campaign_id": "5"}}]), {})
        self.live()
        self.per = {"connected": True, "error": "boom", "campaigns": []}         # the split is unreadable: no results, no blend
        numbers = gp.view(27, "he", TODAY)["numbers"]
        self.assertEqual((numbers["lanes"], numbers["lanes_note"]), ([], gp.TEXT["lanes_unreadable"]["he"]))

    def test_a_number_that_could_not_be_refreshed_carries_its_date_and_is_said_to_be_old(self):
        self.live()
        fresh = gp.view(27, "he", TODAY)["numbers"]
        self.assertFalse(fresh["stale"])
        self.assertEqual(fresh["updated"], "עודכן ב-28/11/2026. הנתונים מגיעים עד 27/11/2026.")
        self.assertEqual(self.kept["numbers"]["state"], "live")                  # remembered
        self.daily = {"connected": True, "needs_reconnect": True}
        old = gp.view(27, "he", TODAY)["numbers"]
        self.assertTrue(old["stale"])
        self.assertEqual(old["updated"], gp.t("stale", "he", date="28/11/2026"))
        self.assertEqual(old["budget"], fresh["budget"])
        self.assertEqual((old["missing"], old["needs"]), (gp.TEXT["c_reconnect"]["he"], gp.TEXT["n_reconnect"]["he"]))
        self.kept.clear()
        self.live()
        gp.view(27, "he", TODAY, remember=False)                                 # staff looking as the client writes nothing
        self.assertEqual(self.kept, {})

    def test_no_stated_budget_is_said_and_nothing_is_derived_from_a_guess(self):
        self.live()
        self.budget = None
        numbers = gp.view(27, "he", TODAY)["numbers"]
        labels = [r["label"] for r in numbers["budget"]]
        self.assertNotIn("נשאר", labels)
        self.assertNotIn("התקציב החודשי שסיכמנו", labels)
        self.assertEqual(numbers["budget_note"], gp.TEXT["b_none"]["he"])

    # ── 3: the phrases ────────────────────────────────────────────────────────

    def test_phrases_only_for_search_and_a_campaign_google_places_shows_themes_as_a_hint(self):
        self.orders = [{**PMAX, "search_themes": ["קפה מיוחד"]}]
        terms = gp.view(27, "he", TODAY)["terms"]
        themes, phrases = terms["blocks"]
        self.assertEqual((themes["kind"], themes["themes"]), ("themes", ["קפה מיוחד"]))
        self.assertIn("לקמפיין הזה אין רשימת ביטויים", themes["intro"])
        self.assertIn("רמז", themes["intro"])
        self.assertNotIn("groups", themes)                                       # never a phrase list on such a campaign
        self.assertEqual(phrases["kind"], "phrases")
        self.assertEqual([x["text"] for x in phrases["groups"][0]["terms"]], ["קפה בתל אביב", "בית קפה קרוב", "אספרסו"])
        self.assertEqual(phrases["note"], gp.TEXT["k_no_why"]["he"])              # no reason was written yet: said, not invented
        self.assertNotIn("PHRASE", str(terms))
        self.assertEqual(terms["excluded_empty"], gp.TEXT["x_none"]["he"])
        self.orders, self.activity = [PMAX], []
        self.assertEqual(gp.view(27, "he", TODAY)["terms"]["empty"], gp.TEXT["k_none"]["he"])

    def test_the_client_flags_a_phrase_and_edits_nothing(self):
        row = gp.flag(27, SEARCH_ID, "אספרסו", "  אצלנו   זה שם של מתחרה ")
        self.assertEqual((row["status"], row["reason"]), ("waiting", "אצלנו זה שם של מתחרה"))
        self.assertEqual(len(self.alerts), 1)
        term = gp.view(27, "he", TODAY)["terms"]["blocks"][-1]["groups"][0]["terms"][2]
        self.assertEqual((term["flag"], term["flag_line"], term["can_flag"]), ("waiting", gp.TEXT["g_waiting"]["he"], False))
        self.assertEqual(gp.campaigns(27)[-1]["keywords"], ["קפה בתל אביב", "בית קפה קרוב", "אספרסו"])     # the list did not move
        for campaign, term, reason, code in ((SEARCH_ID, "מילה אחרת", "סיבה טובה", "unknown_term"), ("o-abc", "אספרסו", "סיבה טובה", "unknown_term"),
                                             (SEARCH_ID, "אספרסו", "סיבה טובה", "already"), (SEARCH_ID, "בית קפה קרוב", "x", "reason")):
            with self.assertRaises(gp.PageError) as caught:
                gp.flag(27, campaign, term, reason)
            self.assertEqual(caught.exception.code, code)
            for lang in LANGS:
                self.assertTrue(gp.error_text(code, lang))
        self.kept["flags"] = [{"id": str(i), "campaign": "x", "term": str(i), "status": "waiting"} for i in range(gp.MAX_OPEN_FLAGS)]
        with self.assertRaises(gp.PageError) as caught:
            gp.flag(27, SEARCH_ID, "בית קפה קרוב", "סיבה טובה")
        self.assertEqual(caught.exception.code, "too_many")

    # ── 5: the strategy ───────────────────────────────────────────────────────

    def answer(self, **changes):
        base = {"money": "כל התקציב הולך למודעות בחיפוש של גוגל, כי מי שמחפש קפה קרוב כבר רוצה לקנות עכשיו.",
                "assumes": ["רוב הלקוחות מגיעים מרדיוס קצר מהעסק."],
                "watching": [{"number": "כמה מכירות הגיעו מהמודעות", "why": "זו המטרה עצמה."},
                             {"number": "כמה עולה כל מכירה", "why": "כך נדע אם זה משתלם."}],
                "change_course": [{"if": "אם עלות כל מכירה גבוהה מהרווח עליה", "by": "2027-01-15", "then": "נעצור ונעביר את הכסף למודעות אחרות."}],
                "changed": "",
                "themes": {SEARCH_ID: [{"theme": "קרוב אליי", "terms": [{"text": "קפה בתל אביב", "why": "מחפשים מקום באזור."},
                                                                        {"text": "בית קפה קרוב", "why": "כוונת ביקור מיידית."}]},
                                       {"theme": "המוצר", "terms": [{"text": "אספרסו", "why": "מה שאנחנו מוכרים."}]}]},
                "flag_decisions": []}
        base.update(changes)
        return base

    def write(self, answer, reason=""):
        return gp.write_strategy(27, "he", reason, call=lambda system, user: answer, knowledge={}, today=TODAY)

    def test_a_strategy_without_a_dated_abandon_condition_is_refused(self):
        for changes, code in (({"change_course": []}, "abandon_missing"),
                              ({"change_course": [{"if": "אם זה לא עובד", "by": "בקרוב", "then": "נשנה"}]}, "abandon_date"),
                              ({"change_course": [{"if": "אם זה לא עובד", "by": "2026-11-01", "then": "נשנה"}]}, "abandon_date"),
                              ({"change_course": [{"if": "אם זה לא עובד", "by": "2028-01-01", "then": "נשנה"}]}, "abandon_date"),
                              ({"watching": [{"number": "מכירות", "why": "המטרה"}]}, "watching"),
                              ({"money": "קצר"}, "money_missing"), ({"assumes": []}, "assumes_missing"),
                              ({"themes": {}}, "terms_uncovered")):
            with self.assertRaises(gp.PageError) as caught:
                self.write(self.answer(**changes))
            self.assertEqual(caught.exception.code, code, changes)
        self.assertEqual(self.kept, {})                                          # a refused strategy stores nothing
        self.assertIn("the date by which we will know", gp.STRATEGY_SYSTEM)
        self.assertIn("Never promise a result", gp.STRATEGY_SYSTEM)

    def test_written_shown_and_the_previous_version_stays_visible(self):
        self.assertEqual(self.write(self.answer())["version"], 1)
        strategy = gp.view(27, "he", TODAY)["strategy"]
        self.assertEqual(strategy["current"]["change_course"][0]["by"], "נדע עד 15/01/2027")
        self.assertEqual((strategy["previous"], strategy["by"]), ([], "כתב: יואב"))
        phrases = gp.view(27, "he", TODAY)["terms"]["blocks"][-1]                # the same run explained every phrase, by theme
        self.assertEqual(([g["theme"] for g in phrases["groups"]], phrases["note"]), (["קרוב אליי", "המוצר"], ""))
        self.assertEqual(phrases["groups"][1]["terms"][0]["why"], "מה שאנחנו מוכרים.")
        with self.assertRaises(gp.PageError) as caught:                          # a rewrite says what changed
            self.write(self.answer())
        self.assertEqual(caught.exception.code, "changed_missing")
        second = self.answer(money="חצי מהתקציב עובר למודעות בכל המקומות של גוגל, כי החיפוש לבדו לא הביא מספיק מכירות.",
                             changed="העברנו חצי מהתקציב, כי עלות כל מכירה בחיפוש עלתה.")
        self.assertEqual(self.write(second, "the search results got expensive")["version"], 2)
        strategy = gp.view(27, "he", TODAY)["strategy"]
        self.assertEqual((strategy["current"]["n"], [v["n"] for v in strategy["previous"]]), (2, [1]))
        self.assertIn("העברנו חצי", strategy["current"]["changed"])
        self.assertIn("כל התקציב הולך", strategy["previous"][0]["money"])         # what we thought before is still there

    def test_jargon_in_the_strategy_never_reaches_the_client_and_nothing_to_plan_is_refused(self):
        self.write(self.answer(money="כל התקציב הולך ל-Performance Max, כי שם ה-CPC הכי נמוך ויש הכי הרבה מכירות למודעה."))
        money = gp.view(27, "he", TODAY)["strategy"]["current"]["money"]
        self.assertEqual(cs.find(money, "he"), [])
        self.assertNotIn("CPC", money)
        self.orders, self.activity, self.budget = [], [], None
        with self.assertRaises(gp.PageError) as caught:
            self.write(self.answer())
        self.assertEqual(caught.exception.code, "nothing_to_plan")

    def test_yoav_decides_every_note_and_an_exclusion_is_not_said_to_be_done(self):
        keep = gp.flag(27, SEARCH_ID, "אספרסו", "זה שם של מתחרה")
        drop = gp.flag(27, SEARCH_ID, "בית קפה קרוב", "אצלנו זה אומר משהו אחר")
        self.alerts.clear()
        seen = {}

        def call(system, user):
            seen["user"] = user
            return self.answer(flag_decisions=[{"id": keep["id"], "decision": "keep", "why": "זה שם של משקה, לא של מתחרה."},
                                               {"id": drop["id"], "decision": "remove", "why": "הביטוי מביא אנשים שמחפשים משהו אחר."},
                                               {"id": "nope", "decision": "remove", "why": "x"}])
        out = gp.write_strategy(27, "he", call=call, knowledge={}, today=TODAY)
        self.assertIn("זה שם של מתחרה", seen["user"])                             # the note reached Yoav
        self.assertEqual((out["decided"], out["removed"]), (2, 1))
        terms = gp.view(27, "he", TODAY)["terms"]
        self.assertEqual(terms["excluded"], [{"text": "בית קפה קרוב", "why": "הביטוי מביא אנשים שמחפשים משהו אחר.",
                                              "state": gp.TEXT["x_pending"]["he"]}])
        self.assertEqual(len(self.alerts), 1)                                    # the owner is told to apply it in the account
        lines = {x["text"]: x["flag_line"] for g in terms["blocks"][-1]["groups"] for x in g["terms"]}
        self.assertTrue(lines["אספרסו"].startswith("יואב החליט להשאיר:"))
        self.assertTrue(lines["בית קפה קרוב"].startswith("יואב החליט להוציא:"))

    # ── 4: reports ────────────────────────────────────────────────────────────

    def test_a_report_only_from_current_figures_kept_readable_and_downloadable(self):
        with self.assertRaises(gp.PageError) as caught:
            gp.build_report(27, "he", TODAY)
        self.assertEqual((caught.exception.code, self.kept), ("no_numbers", {}))
        self.live()
        self.write(self.answer())
        first = gp.build_report(27, "he", TODAY)
        second = gp.build_report(27, "he", TODAY)
        reports = gp.view(27, "he", TODAY)["reports"]
        self.assertEqual([r["id"] for r in reports["reports"]], [second["id"], first["id"]])     # history, newest first
        shown = reports["reports"][0]
        self.assertEqual(shown["period"], "29/10/2026 עד 27/11/2026")             # the dates it covers
        self.assertEqual((shown["view_url"], shown["word_url"]), (f"/api/client/google/report/{second['id']}/view",
                                                                  f"/api/client/google/report/{second['id']}/export"))
        cells = [cell for row in shown["rows"] for cell in row]
        for needle in ("מכירות", "אחוז ההקלקות", "עלות לקליק", gp.TEXT["lane_system"]["he"], gp.TEXT["lane_client"]["he"], "כמה עולה כל מכירה"):
            self.assertIn(needle, cells, needle)
        for lang in LANGS:                                                       # a report is not a technical document
            table = gp.report_table(first, lang, 27)
            for text in _strings([table["title"], table["subtitle"], table["columns"], table["rows"], table["summary"]]):
                self.assertEqual(cs.find(text, lang), [], (lang, text))
        from core import export_service
        table = gp.report_table(first, "he", 27)
        self.assertTrue(export_service.to_docx(table["title"], table["subtitle"], table["columns"], table["rows"],
                                               summary=table["summary"], rtl=table["rtl"]).startswith(b"PK"))
        self.assertEqual(gp.report(27, first["id"])["id"], first["id"])
        self.assertEqual(gp.report(27, "nope"), {})


class Wiring(unittest.TestCase):
    def test_the_routes_the_view_and_the_cards(self):
        server = _src("core", "api_server.py")
        for route in ('@app.get("/api/client/google")', '@app.post("/api/client/google/flag")',
                      '@app.get("/api/client/google/report/{report_id}/view")', '@app.get("/api/client/google/report/{report_id}/export")'):
            body = server[server.index(route):][:900]
            self.assertIn("_require_session(request)", body, route)
        from core import admin_view
        self.assertIn("client_google", admin_view.READ_ENDPOINTS)
        self.assertNotIn("client_google_flag", admin_view.READ_ENDPOINTS)        # staff viewing as the client cannot flag
        self.assertNotIn("client_google_report_export", admin_view.READ_ENDPOINTS)
        page = _src("dashboard", "client", "index.html")
        for needle in ('data-view="google"', 'id="viewGoogle"', "'brand', 'google'];", "if (name === 'google') loadGooglePage();",
                       "fetch('/api/client/google'", "'/api/client/google/flag'", "nav_google:"):
            self.assertIn(needle, page, needle)
        script = page[page.index("// ---------- GOOGLE page"):page.index("async function loadBrandPage()")]
        self.assertEqual(cs.find(script.replace("google", " "), "en"), [])          # the layout code adds no words of its own
        for path in (("core", "client_deletion.py"), ("core", "deletion_guard.py"), ("core", "admin_service.py")):
            self.assertIn("google_page:", _src(*path), path)
        self.assertIn('"lane": _governance.clean_lane(spec.get("lane")) or _governance.current_lane()', _src("agents", "google_ads_agent.py"))

    def test_the_two_cards(self):
        from core import agent_actions as aa
        for name in ("write_google_strategy", "build_google_report"):
            action = aa.ACTIONS[name]
            self.assertTrue(action["client_requestable"])
            for key in ("title", "what", "done"):
                self.assertEqual(set(action[key]), set(LANGS), (name, key))
                for lang in LANGS:
                    self.assertEqual(cs.find(action[key][lang], lang), [], (name, key, lang))
            for code, row in action["refusals"].items():
                for lang in LANGS:
                    self.assertEqual(cs.find(row[lang], lang), [], (name, code, lang))
        self.assertIn("write_google_strategy", aa.STOP_DISCARD)                   # the slow one is in exactly one stop set
        self.assertNotIn("slow", aa.ACTIONS["build_google_report"])
        self.assertIn("safe_claude_json_call", _src("core", "google_page.py"))


if __name__ == "__main__":
    unittest.main()
