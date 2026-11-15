"""The organic SEO report (core/seo_report, core/seo_report_actions) — 2026-11-15.

What must stay true:
- the report is FREE and gated by the package: no research and no model call
  for a client whose package does not cover organic; that client gets the ONE
  upgrade card, never a one-off price for a report;
- a paid link, link exchange or PBN is dropped; a promised ranking is removed;
  a site the search did not return is dropped; SEO vendors are never named;
- the quality score says what it is made of (judgment only without the public
  authority number);
- the paid backlink connector is disabled and never called;
- nothing here sends anything; 5 languages on every client-facing text.
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")

for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}), ("httpx", {}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:                 # the container has them; this machine's test python does not
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from core import agent_actions as aa        # noqa: E402
from core import claude_json                # noqa: E402
from core import dataforseo_connector       # noqa: E402
from core import open_pagerank              # noqa: E402
from core import seo_report                 # noqa: E402
from test_landing_own import FakeDB          # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
SOURCED = ["https://www.b144.co.il/cafes", "https://cafe-mag.co.il/write-for-us", "https://podcast.example.co.il/"]
RAW = {
    "summary": "האתר יציב אבל כמעט לא מתעדכן.",
    "site_state": ["באתר 4 עמודים ואין בו מאמרים.", "We guarantee you will rank first."],
    "keyword_opportunities": [{"keyword": "בית קפה בחיפה", "intent": "מחפשים מקום לשבת", "action": "עמוד ייעודי"},
                              {"keyword": "", "intent": "x", "action": "y"}],
    "backlink_opportunities": [
        {"site": "b144.co.il", "type": "directory", "why_it_fits": "אינדקס עסקים מקומי", "what_to_do": "לפתוח כרטיס עסק",
         "outreach_draft": "שלום, נשמח להופיע אצלכם. קפה נועה", "relevance": 4, "content_quality": 4},
        {"site": "https://www.cafe-mag.co.il/x", "type": "guest_post", "why_it_fits": "מגזין קפה, נמדד ב-Ahrefs",
         "what_to_do": "להציע כתבה", "outreach_draft": "שלום", "relevance": 5, "content_quality": 5},
        {"site": "podcast.example.co.il", "type": "partnership", "why_it_fits": "פודקאסט",
         "what_to_do": "Buy a few backlinks from them", "outreach_draft": "", "relevance": 5, "content_quality": 5},
        {"site": "invented-portal.co.il", "type": "directory", "why_it_fits": "x", "what_to_do": "y",
         "outreach_draft": "z", "relevance": 5, "content_quality": 5},
        {"site": "noa-cafe.co.il", "type": "unlinked_mention", "why_it_fits": "x", "what_to_do": "y",
         "outreach_draft": "z", "relevance": 5, "content_quality": 5},
        {"site": "b144.co.il", "type": "directory", "why_it_fits": "again", "what_to_do": "again",
         "outreach_draft": "again", "relevance": 2, "content_quality": 2},
        {"site": "", "type": "google_business_profile", "why_it_fits": "הפרופיל חסר שעות", "what_to_do": "להשלים שעות ותמונות",
         "outreach_draft": "should vanish", "relevance": 5, "content_quality": 3},
        {"site": "b144.co.il", "type": "link_farm", "why_it_fits": "x", "what_to_do": "y", "outreach_draft": "z"},
    ],
    "next_steps": ["לפתוח כרטיס באינדקס", "מקום ראשון בגוגל תוך חודש"],
}


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.alerts, self.searches, self.writes, self.sources = [], [], [], []
        self.cover = {"readable": True, "covered": True}
        self.saved = [(seo_report, n, getattr(seo_report, n)) for n in ("_db", "coverage", "_sources")]
        self.saved += [(aa, n, getattr(aa, n)) for n in ("_db", "_client", "_alert")]
        self.saved += [(claude_json, n, getattr(claude_json, n))
                       for n in ("safe_claude_json_call", "claude_web_search_call")]
        self.saved += [(open_pagerank, "_get", open_pagerank._get)]
        self.env = os.environ.pop(open_pagerank.KEY_ENV, None)
        seo_report._db = lambda: self.db
        seo_report.coverage = lambda cid: dict(self.cover)
        seo_report._block_cache.clear()
        aa._db = lambda: self.db
        aa._client = lambda cid: {"id": cid, "language": "he", "billing_exempt": False}
        aa._alert = self.alerts.append
        self.domain = "noa-cafe.co.il"

        def fake_sources(client_id, focus):
            self.sources.append(client_id)
            return {"domain": self.domain, "business": {"name": "קפה נועה"}, "audit": {"connected": True, "pages_total": 4},
                    "research": {}, "focus": focus}
        seo_report._sources = fake_sources

        def fake_search(system, user, **kwargs):
            self.searches.append(kwargs)
            return "directory | https://www.b144.co.il/cafes | index | local", list(SOURCED)
        claude_json.claude_web_search_call = fake_search
        self.answer = RAW

        def fake_write(system, user, **kwargs):
            self.writes.append((system, user, kwargs))
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer
        claude_json.safe_claude_json_call = fake_write

    def tearDown(self):
        for module, name, value in self.saved:
            setattr(module, name, value)
        os.environ.pop(open_pagerank.KEY_ENV, None)
        if self.env is not None:
            os.environ[open_pagerank.KEY_ENV] = self.env


class Coverage(Base):
    def test_package_rule(self):
        for services in (["seo"], ["Organic SEO"], ["google_ads", "קידום אורגני"]):
            self.assertTrue(seo_report.covered(services), services)
        for services in ([], None, ["google_ads", "meta_ads", "website"]):
            self.assertFalse(seo_report.covered(services), services)

    def test_no_research_for_a_client_who_is_not_covered(self):
        for cover, code in (({"readable": True, "covered": False}, "not_covered"),
                            ({"readable": False, "covered": False}, "package_unreadable")):
            self.cover = cover
            with self.assertRaises(seo_report.ReportError) as caught:
                seo_report.build(27, "focus", "he")
            self.assertEqual(caught.exception.code, code)
        self.assertEqual((self.sources, self.searches, self.writes), ([], [], []))
        self.assertEqual(seo_report.latest(27), {})

    def test_unreadable_package_fails_closed(self):
        seo_report.coverage = self.saved[1][2]          # the real one
        import core.client_journey as journey
        original = journey._chosen_package
        try:
            journey._chosen_package = lambda cid: (_ for _ in ()).throw(RuntimeError("db"))
            self.assertEqual(seo_report.coverage(27), {"readable": False, "covered": False})
            journey._chosen_package = lambda cid: {"recommended_services": ["seo", "website"]}
            self.assertEqual(seo_report.coverage(27), {"readable": True, "covered": True})
        finally:
            journey._chosen_package = original


class Cleaning(unittest.TestCase):
    def setUp(self):
        self.report = seo_report.clean(RAW, SOURCED, "noa-cafe.co.il", {})

    def test_only_earned_links_from_the_search(self):
        sites = [(b["site"], b["type"]) for b in self.report["backlink_opportunities"]]
        self.assertEqual(sorted(sites), sorted([("b144.co.il", "directory"), ("cafe-mag.co.il", "guest_post"),
                                                ("", "google_business_profile")]))
        self.assertEqual(self.report["dropped"], {"paid_or_scheme": 1, "not_from_search": 2, "duplicate": 1})

    def test_paid_links_and_schemes_in_every_language(self):
        for text in ("buy backlinks", "paid links from blogs", "a link exchange with them", "use a PBN",
                     "private blog network", "קניית קישורים", "קישורים בתשלום", "החלפת קישורים", "achat de liens",
                     "liens payants", "شراء روابط", "покупка ссылок", "sponsored post with a link"):
            raw = {"backlink_opportunities": [{"site": "b144.co.il", "type": "directory", "why_it_fits": "ok",
                                               "what_to_do": text, "outreach_draft": ""}]}
            self.assertEqual(seo_report.clean(raw, SOURCED)["backlink_opportunities"], [], text)

    def test_no_promised_ranking_survives(self):
        text = repr(self.report)
        for bad in ("guarantee", "will rank", "מקום ראשון בגוגל"):
            self.assertNotIn(bad, text)
        self.assertEqual(self.report["site_state"], ["באתר 4 עמודים ואין בו מאמרים."])
        self.assertEqual(self.report["next_steps"], ["לפתוח כרטיס באינדקס"])

    def test_vendors_are_never_named(self):
        self.assertIsNone(seo_report.VENDORS.search(repr(self.report)))
        self.assertEqual(seo_report._line("data from SEMrush and Open PageRank", 80), "data from and")

    def test_google_profile_has_no_site_and_no_draft(self):
        profile = [b for b in self.report["backlink_opportunities"] if b["type"] == "google_business_profile"][0]
        self.assertEqual((profile["site"], profile["outreach_draft"]), ("", ""))

    def test_limits_and_empty_keywords(self):
        self.assertEqual([k["keyword"] for k in self.report["keyword_opportunities"]], ["בית קפה בחיפה"])
        many = {"backlink_opportunities": [{"site": f"s{i}.co.il", "type": "directory", "why_it_fits": "a",
                                            "what_to_do": "b"} for i in range(40)]}
        out = seo_report.clean(many, [f"https://s{i}.co.il/" for i in range(40)])
        self.assertEqual(len(out["backlink_opportunities"]), seo_report.MAX_BACKLINKS)
        self.assertEqual(seo_report.clean("garbage", None)["backlink_opportunities"], [])


class Scores(Base):
    def test_judgment_only_without_the_key(self):
        self.assertFalse(open_pagerank.enabled())
        open_pagerank._get = lambda domains: self.fail("called without a key")
        self.assertEqual(open_pagerank.scores(["b144.co.il"]), {})
        self.assertEqual(seo_report.score_of(4, 4), 80)
        value = seo_report.build(27, "", "he")
        self.assertEqual(value["score_basis"], "judgment_only")
        self.assertTrue(all(b["score_basis"] == "judgment_only" for b in value["report"]["backlink_opportunities"]))
        page = seo_report.page_html(value)
        self.assertIn(seo_report.t("score_judgment", "he"), page)
        self.assertIn(seo_report.t("judgment_tag", "he"), page)
        self.assertNotIn(seo_report.t("score_both", "he"), page)
        self.assertIn(seo_report.t("judgment_tag", "he"), seo_report.export_table(value)["rows"][0][2])

    def test_blended_with_the_public_number(self):
        os.environ[open_pagerank.KEY_ENV] = "test-key"
        asked = []

        def fake_get(domains):
            asked.append(list(domains))
            return {"response": [{"status_code": 200, "domain": "b144.co.il", "page_rank_decimal": 6},
                                 {"status_code": 404, "domain": "cafe-mag.co.il", "page_rank_decimal": 0},
                                 {"status_code": 200, "domain": "other.com", "page_rank_decimal": 9}]}
        open_pagerank._get = fake_get
        value = seo_report.build(27, "", "he")
        self.assertEqual(len(asked), 1)
        self.assertNotIn("noa-cafe.co.il", asked[0])              # third-party domains only
        rows = {b["site"]: b for b in value["report"]["backlink_opportunities"]}
        self.assertEqual((rows["b144.co.il"]["score"], rows["b144.co.il"]["score_basis"]), (70, "authority_and_judgment"))
        self.assertEqual(rows["cafe-mag.co.il"]["score_basis"], "judgment_only")
        self.assertIn(seo_report.t("score_both", "he"), seo_report.page_html(value))

    def test_a_failed_lookup_is_judgment_only(self):
        os.environ[open_pagerank.KEY_ENV] = "test-key"
        open_pagerank._get = lambda domains: (_ for _ in ()).throw(RuntimeError("down"))
        self.assertEqual(open_pagerank.scores(["b144.co.il"]), {})


class PaidConnector(unittest.TestCase):
    """OFF by default; ON = the admin setting AND the owner's login (2026-11-16)."""

    def setUp(self):
        self.saved = (dataforseo_connector._setting_on, dataforseo_connector._login)

    def tearDown(self):
        dataforseo_connector._setting_on, dataforseo_connector._login = self.saved

    def calls(self):
        return (lambda: dataforseo_connector.summary("a.co.il"),
                lambda: dataforseo_connector.referring_domains("a.co.il"),
                lambda: dataforseo_connector.link_gap("a.co.il", ["b.co.il"]),
                lambda: dataforseo_connector.keyword_volumes(["קפה"]),
                lambda: dataforseo_connector._post("/x", {}))

    def test_off_by_default_and_with_only_one_half(self):
        from core import admin_service
        self.assertIs(admin_service.DEFAULT_SETTINGS["dataforseo_enabled"], False)
        for setting, login in ((False, ("", "")), (True, ("", "")), (False, ("l", "p"))):
            dataforseo_connector._setting_on = lambda s=setting: s
            dataforseo_connector._login = lambda l=login: l
            self.assertFalse(dataforseo_connector.enabled())
            for call in self.calls():
                with self.assertRaises(dataforseo_connector.Disabled):
                    call()                                   # refused BEFORE any request
        dataforseo_connector._setting_on = lambda: True
        dataforseo_connector._login = lambda: ("l", "p")
        self.assertTrue(dataforseo_connector.enabled())
        self.assertNotIn("p", str(dataforseo_connector.status().values()).replace("True", "").replace("False", ""))

    def test_no_startup_key_and_every_caller_checks_the_switch(self):
        self.assertNotIn("DATAFORSEO", _src("agents", "keys_agent.py"))   # never nags; the owner adds it
        for folder in ("core", "agents"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if not name.endswith(".py") or name == "dataforseo_connector.py":
                    continue
                text = _src(folder, name)
                for function in ("summary", "referring_domains", "link_gap", "keyword_volumes", "_post"):
                    at = text.find(f"dataforseo_connector.{function}(")
                    if at >= 0:
                        self.assertIn("dataforseo_connector.enabled()", text[:at], f"{folder}/{name}: {function}")


class Building(Base):
    def test_one_search_one_writing_call_and_stored(self):
        value = seo_report.build(27, "קפה בחיפה", "he")
        self.assertEqual((len(self.searches), len(self.writes)), (1, 1))
        writer_input = self.writes[0][1]
        self.assertIn("b144.co.il", writer_input)
        self.assertIn("urls_from_search", writer_input)
        stored = seo_report.latest(27)
        self.assertEqual(stored["built_at"], value["built_at"])
        self.assertEqual(stored["domain"], "noa-cafe.co.il")
        self.assertTrue(seo_report.fresh_enough(stored))
        status = seo_report.status(27)
        self.assertEqual((status["has_report"], status["opportunities"], status["view_url"]),
                         (True, 3, seo_report.VIEW_PATH))

    def test_a_failed_search_lists_no_sites(self):
        claude_json.claude_web_search_call = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("search down"))
        value = seo_report.build(27, "", "he")
        self.assertEqual([b["type"] for b in value["report"]["backlink_opportunities"]], ["google_business_profile"])

    def test_no_site_and_failed_writing(self):
        self.domain = ""
        with self.assertRaises(seo_report.ReportError) as caught:
            seo_report.build(27)
        self.assertEqual(caught.exception.code, "no_site")
        self.assertEqual(self.searches, [])
        self.domain, self.answer = "noa-cafe.co.il", RuntimeError("model down")
        with self.assertRaises(seo_report.ReportError) as caught:
            seo_report.build(27)
        self.assertEqual(caught.exception.code, "writing_failed")
        self.assertEqual(seo_report.latest(27), {})

    def test_prompts_carry_the_hard_rules(self):
        for prompt in (seo_report.SEARCH_SYSTEM, seo_report.REPORT_SYSTEM):
            self.assertIn("NEVER", prompt)
            self.assertIn("PBN", prompt)
        self.assertIn("NEVER promise", seo_report.REPORT_SYSTEM)
        self.assertIn("urls_from_search", seo_report.REPORT_SYSTEM)

    def test_nothing_is_sent_by_this_module(self):
        source = _src("core", "seo_report.py") + _src("core", "seo_report_actions.py")
        for sender in ("gmail", "send_email", "smtplib", "whatsapp", "send_message"):
            self.assertNotIn(sender, source.lower(), sender)

    def test_prompt_block(self):
        self.assertEqual(seo_report.prompt_block(27), {"covered_by_package": True, "has_report": False, "built_at": "",
                                                       "is_free_part_of_the_organic_service": True})
        seo_report.build(27)
        self.assertTrue(seo_report.prompt_block(27)["has_report"])      # the build dropped the cached block
        seo_report._block_cache.clear()
        self.cover = {"readable": False, "covered": False}
        self.assertEqual(seo_report.prompt_block(27), {})


class Page(Base):
    def test_page_in_five_languages(self):
        value = seo_report.build(27, "", "he")
        for lang in LANGS:
            page = seo_report.page_html(dict(value, language=lang))
            self.assertIn(f'dir="{"rtl" if lang in ("he", "ar") else "ltr"}"', page)
            for key in ("title", "no_promise", "clean_links", "draft_note"):
                self.assertIn(seo_report._e(seo_report.t(key, lang)), page, (lang, key))
            self.assertNotIn("<script", page)
        for key, table in list(seo_report.TEXT.items()) + list(seo_report.TYPE_TEXT.items()):
            self.assertEqual(set(table), set(LANGS), key)
            self.assertTrue(all(table.values()), key)

    def test_model_text_is_escaped_and_the_page_is_sandboxed(self):
        raw = dict(RAW, summary='<img src=x onerror=alert(1)> "quoted"')
        page = seo_report.page_html({"report": seo_report.clean(raw, SOURCED), "language": "en", "domain": "a.co.il"})
        self.assertNotIn("<img", page)
        self.assertIn("&lt;img", page)
        headers = seo_report.page_headers()
        self.assertTrue(headers["Content-Security-Policy"].startswith("sandbox;"))
        self.assertNotIn("allow-", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_empty_list_says_so(self):
        page = seo_report.page_html({"report": {"site_state": ["x"]}, "language": "en"})
        self.assertIn(seo_report._e(seo_report.t("none_found", "en")), page)

    def test_export_table(self):
        table = seo_report.export_table(seo_report.build(27, "", "he"))
        self.assertEqual(len(table["columns"]), 6)
        self.assertEqual(len(table["rows"]), 3)
        self.assertTrue(table["rtl"])
        self.assertIn(seo_report.t("no_promise", "he"), table["summary"])
        self.assertIn("Google", [row[0] for row in table["rows"]])


class Cards(Base):
    def spec(self, name):
        return aa.ACTIONS[name]

    def test_registered_client_approved_and_in_five_languages(self):
        for name in ("build_seo_report", "upgrade_to_organic"):
            spec = self.spec(name)
            self.assertEqual((spec["approver"], spec["internal_only"]), ("client", False))
            for key in ("title", "what", "done"):
                self.assertEqual(set(spec[key]), set(LANGS), (name, key))
                self.assertTrue(all(spec[key].values()), (name, key))
            for code, texts in spec["refusals"].items():
                self.assertEqual(set(texts), set(LANGS), (name, code))
            text = repr([spec[k] for k in ("title", "what", "done", "refusals")])
            self.assertIsNone(seo_report.VENDORS.search(text), name)

    def test_the_report_card_says_free_and_promises_nothing(self):
        what = self.spec("build_seo_report")["what"]
        self.assertIn("costs nothing", what["en"])
        self.assertIn("ואינו כרוך בתשלום", what["he"])
        self.assertIn("no one can promise", what["en"])
        self.assertNotIn("₪", repr(what))

    def test_not_covered_gets_the_one_upgrade_card_and_no_report(self):
        self.cover = {"readable": True, "covered": False}
        out = self.spec("build_seo_report")["prepare"](27, {"focus": "x"}, {"language": "en"}, [])
        self.assertEqual((out["switch_to"], out["switch_params"]), ("upgrade_to_organic", {}))
        self.assertIn("never sold separately", out["notice"])
        self.assertNotIn("params", out)
        with self.assertRaises(aa.ActionFailed) as caught:      # even a stored card cannot build it
            self.spec("build_seo_report")["run"](27, {}, None, {"language": "he"})
        self.assertEqual(caught.exception.code, "not_covered")
        self.assertEqual((self.sources, self.searches, self.writes), ([], [], []))

    def test_unreadable_package_refuses(self):
        self.cover = {"readable": False, "covered": False}
        out = self.spec("build_seo_report")["prepare"](27, {}, {"language": "he"}, [])
        self.assertEqual(set(out), {"refuse"})

    def test_covered_proposes_then_builds(self):
        prepare, run = self.spec("build_seo_report")["prepare"], self.spec("build_seo_report")["run"]
        self.assertEqual(prepare(27, {"focus": "קפה בחיפה"}, {"language": "he"}, []), {"params": {"focus": "קפה בחיפה"}})
        self.assertEqual(prepare(27, {}, {"language": "he"}, []), {"params": {}})
        result = run(27, {"focus": "קפה בחיפה"}, None, {"language": "he"})
        self.assertEqual(result["count"], 3)
        for lang in LANGS:
            line = self.spec("build_seo_report")["done_message"](result, lang)
            self.assertIn("3", line)
            self.assertNotIn("{", line)
        fresh = prepare(27, {}, {"language": "en"}, [])            # a fresh report is not rebuilt
        self.assertIn("still fresh", fresh["refuse"])
        self.assertNotIn("{", fresh["refuse"])

    def test_stored_params_are_rechecked(self):
        validate = self.spec("build_seo_report")["validate_stored"]
        self.assertEqual(validate(27, {}), {})
        with self.assertRaises(ValueError):
            validate(27, {"focus": "x", "price": 5})

    def test_failed_writing_alerts_and_refuses_calmly(self):
        self.answer = RuntimeError("model down")
        with self.assertRaises(aa.ActionFailed) as caught:
            self.spec("build_seo_report")["run"](27, {}, None, {"language": "he"})
        self.assertEqual(caught.exception.code, "writing_failed")
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("client 27", self.alerts[0])

    def test_upgrade_card(self):
        spec = self.spec("upgrade_to_organic")
        self.assertTrue(spec["available"]({"package": {"readable": True, "services": ["google_ads"]}}, {}))
        self.assertFalse(spec["available"]({"package": {"readable": True, "services": ["seo"]}}, {}))
        self.assertFalse(spec["available"]({"package": {"readable": False}}, {}))
        self.assertFalse(spec["available"]({}, {}))
        for lang in LANGS:
            self.assertIn("{price}", spec["what"][lang], lang)
        self.assertIn("never sold separately", spec["what"]["en"])
        self.assertIn("nothing is charged before you approve", spec["what"]["en"])
        with self.assertRaises(aa.ActionFailed) as caught:           # covered → nothing to upgrade
            spec["run"](27, {}, None)
        self.assertEqual(caught.exception.code, "already_covered")

    def test_upgrade_uses_the_existing_pricing_path(self):
        source = _src("core", "seo_report_actions.py")
        self.assertIn("_build_upgrade_proposal", source)
        for forbidden in ("create_invoice", "paypal", "create_order"):
            self.assertNotIn(forbidden, source.lower())


class Wiring(unittest.TestCase):
    def test_routes_parity_and_admin_view(self):
        api = _src("core", "api_server.py")
        for route in ('"/api/client/seo-report"', '"/api/client/seo-report/view"', '"/api/client/seo-report/export"'):
            self.assertIn(route, api)
        self.assertLess(api.index('"/api/client/seo-report"'), api.rindex("app.mount("))
        from core import admin_view, chat_parity
        self.assertIn("client_seo_report", admin_view.READ_ENDPOINTS)
        self.assertIn("GET /api/client/seo-report", chat_parity.PARITY)
        doc = _src("docs", "CHAT_PARITY.md")
        for name in ("build_seo_report", "upgrade_to_organic"):
            self.assertIn(f"`{name}`", doc)

    def test_deletion_clears_the_stored_report(self):
        self.assertIn("seo_report:", _src("core", "client_deletion.py"))
        self.assertIn('"seo_report:"', _src("core", "deletion_guard.py"))
        self.assertIn('"seo_report:"', _src("core", "admin_service.py"))

    def test_optional_key_is_registered_and_never_required(self):
        self.assertIn("OPEN_PAGERANK_API_KEY", _src("agents", "keys_agent.py"))

    def test_agents_know_the_rules(self):
        source = _src("agents", "support_agent.py")
        for needle in ("THE ORGANIC SEO REPORT", "IT IS FREE", "upgrade_to_organic", "build_seo_report",
                       "NEVER buy, sell, trade or suggest paid links", "NEVER promise a ranking",
                       'payload["seo_report"]'):
            self.assertIn(needle, source, needle)
        from agents import support_agent
        self.assertNotIn("{", support_agent.STUDIO_RULE)      # it is appended to a .format()-ed template

    def test_feature_catalog(self):
        from core import feature_catalog
        self.assertIn("organic_seo_report", _src("core", "feature_catalog.py"))
        self.assertTrue(feature_catalog)

    def test_dashboard_box(self):
        page = _src("dashboard", "client", "index.html")
        for needle in ('id="seoBox"', "function loadSeoReport", "function seoReportAsk", "/api/client/seo-report",
                       "seo_box_title", "seo_chat_build", "seo_chat_upgrade"):
            self.assertIn(needle, page, needle)
        for key in re.findall(r"\b(seo_box_\w+|seo_chat_\w+)\s*:\s*\{", page):
            block = page[page.index(key + ":"):].split("\n")[0][:3000]
            for lang in LANGS:
                self.assertRegex(block, rf"\b{lang}\s*:", (key, lang))


if __name__ == "__main__":
    unittest.main()
