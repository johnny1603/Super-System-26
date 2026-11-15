"""Branding (core/branding, core/brand_mockups, core/branding_actions) — 2026-11-15.

What must stay true:
- mockups are drawn in code: every text is escaped, the business name comes
  from OUR record, no image model, no script on the page, a sandbox CSP;
- 5 free attempts, ONE capped model call each, a failed call uses no attempt,
  nothing runs past the free attempts while the charge is off, nothing after
  an approval;
- approval saves the brand kit that landing pages and media read;
- a production quote is cost × 2, shows the client only prices, and is its
  own approval card;
- 5 languages on every client-facing text; the page has the box and the
  button; deletion clears both settings rows.
"""
import contextlib
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
from core import brand_mockups as bm        # noqa: E402
from core import branding                   # noqa: E402
from core import branding_actions           # noqa: E402
from core import claude_json                # noqa: E402
from test_landing_own import FakeDB          # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
RAW = {"tagline": "קפה שנקלה כאן", "style": "warm",
       "palette": {"ink": "#2B1D14", "paper": "#F7EFE3", "accent": "#C8553D", "secondary": "#E9B872"},
       "fonts": {"display": "secular", "text": "assistant"}, "logo": {"mark": "arch", "layout": "stacked"},
       "copy": {"flyer_headline": "הבוקר שלכם מתחיל אצלנו", "flyer_body": "קפה שנקלה במקום.",
                "poster_headline": "קפה של שכונה", "cta": "בואו לטעום", "site_headline": "בית קלייה קטן",
                "site_sub": "פולים טריים ומאפים חמים.", "services": ["קלייה מקומית", "מאפים", "אירועים"]},
       "rationale": "גוונים חמים מתאימים לבית קפה שכונתי."}


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.alerts, self.posted, self.calls = [], [], []
        self.saved = [(branding, n, getattr(branding, n)) for n in ("_db", "_alert", "_client_facts")]
        self.saved += [(aa, n, getattr(aa, n)) for n in ("_db", "_client", "_post_client_message", "_alert", "_log")]
        self.saved += [(claude_json, "safe_claude_json_call", claude_json.safe_claude_json_call)]
        branding._db = lambda: self.db
        branding._alert = self.alerts.append
        branding._client_facts = lambda cid: ("קפה נועה", {"phone": "03-5551234"}, {"business_summary": "בית קפה"})
        branding._block_cache.clear()
        branding._kit_cache.clear()
        aa._db = lambda: self.db
        aa._client = lambda cid: {"id": cid, "language": "he", "billing_exempt": False}
        aa._post_client_message = lambda cid, persona, text: self.posted.append((cid, persona, text))
        aa._alert = self.alerts.append
        aa._log = lambda *a, **k: None
        self.answer = dict(RAW)

        def fake_call(system, user, **kwargs):
            self.calls.append(kwargs)
            if isinstance(self.answer, Exception):
                raise self.answer
            claude_json._collect(kwargs.get("model"), 100, 200, 0, 0, 0.21)
            return self.answer
        claude_json.safe_claude_json_call = fake_call

    def tearDown(self):
        for module, name, value in self.saved:
            setattr(module, name, value)


class Mockups(unittest.TestCase):
    def test_six_mockups_all_text_from_code(self):
        concept = branding.clean_concept(RAW, "קפה נועה", {"phone": "03-5551234"})
        svgs = bm.render(concept, "he")
        self.assertEqual(tuple(svgs), bm.ITEMS)
        for item, svg in svgs.items():
            self.assertTrue(svg.startswith("<svg "), item)
            self.assertIn("קפה נועה", svg, item)            # the name is composed as SVG text
            self.assertNotIn("<image", svg)                 # nothing generated, nothing embedded
            self.assertNotIn("<script", svg)
        self.assertIn("03-5551234", svgs["business_card"])

    def test_markup_in_a_concept_is_escaped(self):
        raw = dict(RAW, tagline='<script>alert(1)</script>', rationale='"><img src=x onerror=1>')
        concept = branding.clean_concept(raw, '<b onload="x">Biz</b>', {})
        page = bm.page_html(concept, "en", 1)
        self.assertNotIn("<script", page)
        self.assertNotIn("<img", page)
        self.assertNotIn("<b onload", page)

    def test_every_mark_and_layout_draws(self):
        for mark in bm.MARKS:
            for layout in bm.LAYOUTS:
                concept = branding.clean_concept(dict(RAW, logo={"mark": mark, "layout": layout}), "Noa Roasters", {})
                for lang in ("he", "en"):
                    self.assertEqual(len(bm.render(concept, lang)), 6)

    def test_the_page_says_what_it_is_in_five_languages(self):
        concept = branding.clean_concept(RAW, "קפה נועה", {})
        for lang in LANGS:
            page = bm.page_html(concept, lang, 3)
            self.assertIn(f'lang="{lang}"', page)
            self.assertIn('dir="rtl"' if lang in ("he", "ar") else 'dir="ltr"', page)
            self.assertIn(bm._e(bm.PAGE_TEXT["only_mockups"][lang]), page)
            self.assertIn(bm._e(bm.PAGE_TEXT["heart"][lang]), page)
            for item in bm.ITEMS:
                for key in ("name", "get"):
                    self.assertTrue(bm.ITEM_TEXT[item][key][lang].strip(), (item, key, lang))
        # the logo line offers the human designer; the website line says branding never builds a site
        self.assertIn("human designer", bm.ITEM_TEXT["logo"]["get"]["en"])
        self.assertIn("does not include building a website", bm.ITEM_TEXT["website"]["get"]["en"])
        self.assertIn("paid separately", bm.ITEM_TEXT["website"]["get"]["en"])

    def test_page_headers_are_a_sandbox_without_scripts(self):
        csp = bm.page_headers()["Content-Security-Policy"]
        self.assertIn("sandbox", csp.split("; "))
        self.assertNotIn("allow-same-origin", csp)
        self.assertNotIn("script-src", csp)
        self.assertIn("default-src 'none'", csp)

    def test_no_model_or_network_in_the_renderer(self):
        code = _src("core", "brand_mockups.py").split('"""', 2)[2]
        for word in ("import httpx", "requests", "claude", "generate_image", "urllib"):
            self.assertNotIn(word, code, word)

    def test_clean_concept_enforces_everything(self):
        raw = {"palette": {"ink": "#FFFFFF", "paper": "#111111", "accent": "#ff0000", "secondary": "nope"},
               "fonts": {"display": "comic", "text": "x"}, "logo": {"mark": "dragon", "layout": "q"},
               "style": "weird", "tagline": "t" * 200, "copy": {"services": ["a" * 99, "", "b", "c", "d"]}}
        concept = branding.clean_concept(raw, "N" * 99, {"phone": "1"})
        self.assertEqual(concept["palette"]["ink"], "#111111")           # dark/light put the right way round
        self.assertEqual(concept["palette"]["secondary"], "#FF0000")
        self.assertEqual(concept["fonts"], {"display": "heebo", "text": "assistant"})
        self.assertEqual(concept["logo"], {"mark": "circle", "layout": "stacked"})
        self.assertEqual(concept["style"], "modern")
        self.assertEqual((len(concept["tagline"]), len(concept["name"])), (40, 40))
        self.assertEqual(len(concept["copy"]["services"]), 3)
        self.assertGreaterEqual(bm.contrast(concept["palette"]["ink"], concept["palette"]["paper"]), 4.5)
        with self.assertRaises(ValueError):
            branding.clean_concept({"palette": {"ink": "red"}}, "x", {})


class Attempts(Base):
    def test_one_capped_call_per_attempt_and_the_cost_is_kept(self):
        result = branding.create_attempt(27, "צבעים חמים ולוגו מינימלי", "he")
        self.assertEqual((result["n"], result["left"]), (1, 4))
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual((call["retry_empty"], call["json_retry"]), (False, False))   # ONE call, no paid retry
        self.assertEqual((call["client_id"], call["cost_category"]), (27, "media"))
        self.assertEqual(call["max_tokens"], branding.ATTEMPT_MAX_TOKENS)
        stored = branding.state(27)["attempts"][0]
        self.assertEqual(stored["cost_ils"], 0.21)
        self.assertEqual(stored["concept"]["name"], "קפה נועה")          # our record, not the model's

    def test_the_cap_is_checked_before_the_call(self):
        from core.cost_tracker import claude_cost_ils
        model = branding.pick_model(6000)
        self.assertTrue(model)
        self.assertLessEqual(claude_cost_ils(3200, branding.ATTEMPT_MAX_TOKENS, model), branding.ATTEMPT_COST_CAP_ILS)
        self.assertEqual(branding.pick_model(10 ** 9), "")                # nothing fits: nothing runs
        saved = branding.pick_model
        branding.pick_model = lambda chars: ""
        try:
            with self.assertRaises(branding.BrandingError) as ctx:
                branding.create_attempt(27, "כיוון כלשהו", "he")
        finally:
            branding.pick_model = saved
        self.assertEqual(ctx.exception.code, "cost_cap")
        self.assertEqual(self.calls, [])

    def test_a_failed_design_uses_no_attempt(self):
        self.answer = RuntimeError("boom")
        with self.assertRaises(branding.BrandingError) as ctx:
            branding.create_attempt(27, "כיוון כלשהו", "he")
        self.assertEqual(ctx.exception.code, "design_failed")
        self.assertEqual(branding.attempts_used(branding.state(27)), 0)

    def test_five_free_then_nothing_runs_while_the_charge_is_off(self):
        self.assertFalse(branding.EXTRA_ATTEMPT_CHARGE_ENABLED)          # the owner's decision, not ours
        for i in range(branding.FREE_ATTEMPTS):
            branding.create_attempt(27, f"כיוון {i}", "he")
        with self.assertRaises(branding.BrandingError) as ctx:
            branding.create_attempt(27, "עוד אחד", "he")
        self.assertEqual(ctx.exception.code, "attempts_used")
        self.assertEqual(len(self.calls), branding.FREE_ATTEMPTS)
        summary = branding.summary(27)
        self.assertEqual((summary["attempts_left_free"], summary["can_try_now"]), (0, False))
        branding.grant_attempts(27, 2)                                    # staff's manual answer
        self.assertEqual(branding.create_attempt(27, "אחרי הענקה", "he")["left"], 1)

    def test_extra_attempt_price_is_cost_plus_a_small_margin(self):
        value = {"attempts": [{"cost_ils": 0.2}, {"cost_ils": 0.4}]}
        self.assertEqual(branding.extra_attempt_price_ils(value), branding.EXTRA_ATTEMPT_MIN_ILS)
        self.assertEqual(branding.extra_attempt_price_ils({"attempts": [{"cost_ils": 20}]}), 25)

    def test_approval_saves_the_kit_and_closes_the_attempts(self):
        branding.create_attempt(27, "כיוון", "he")
        self.assertEqual(branding.kit(27), {})
        branding._kit_cache.clear()
        kit = branding.approve(27, 1)
        self.assertEqual(kit["palette"]["accent"], "#C8553D")
        self.assertEqual(branding.kit_for_prompt(27)["fonts"], {"display": "Secular One", "text": "Assistant"})
        self.assertEqual(branding.kit_palette_list(27)[0], "#C8553D")
        with self.assertRaises(branding.BrandingError) as ctx:
            branding.create_attempt(27, "עוד כיוון", "he")
        self.assertEqual(ctx.exception.code, "already_approved")
        with self.assertRaises(branding.BrandingError):
            branding.approve(27, 1)

    def test_terms_and_value_lines(self):
        for lang in LANGS:
            line = branding.terms_line(lang)
            self.assertIn(str(branding.FREE_ATTEMPTS), line)
            self.assertNotIn("{", line)
            self.assertTrue(branding.VALUE[lang].strip())
        self.assertIn("free up to the approval point", branding.VALUE["en"])
        block = branding.prompt_block(27, "en")
        self.assertEqual(block["terms_line"], branding.terms_line("en"))
        self.assertEqual(block["attempts_used"], 0)


class Quote(Base):
    def test_price_is_cost_times_two_and_never_a_website(self):
        quote = branding.build_quote({"logo": 400, "business_card": 180.5, "website": 5000})
        self.assertEqual([(i["item"], i["price_ils"]) for i in quote["items"]], [("logo", 800), ("business_card", 361)])
        self.assertEqual(quote["total_ils"], 1161)
        self.assertNotIn("website", branding.QUOTE_ITEMS)
        for bad in ({}, {"logo": -1}, {"logo": 0}):
            with self.assertRaises(ValueError):
                branding.build_quote(bad)

    def test_the_client_sees_prices_never_our_cost(self):
        quote = branding.build_quote({"logo": 400, "flyer": 90})
        lines = branding.quote_lines(quote, "en")
        self.assertIn("₪800", lines)
        self.assertIn("₪180", lines)
        self.assertNotIn("400", lines)
        self.assertNotIn("90", lines.replace("₪180", ""))

    def test_a_quote_needs_an_approved_direction_and_is_one_card(self):
        with self.assertRaises(branding.BrandingError):
            branding_actions.send_quote(27, {"logo": 400})
        branding.create_attempt(27, "כיוון", "he")
        branding.approve(27, 1)
        sent = branding_actions.send_quote(27, {"logo": 400, "signage": 1500}, "כולל התקנה")
        card = sent["card"]
        self.assertEqual((card["action"], card["persona"], card["can_decide"]), ("approve_production_quote", "media", True))
        self.assertIn("₪3800", card["what"])
        self.assertNotIn("1500", card["what"])
        done = aa.decide(card["id"], "client", 27, "approve")
        self.assertEqual(done["status"], "done")
        self.assertEqual(branding.quote(27)["status"], "approved")
        self.assertTrue(any("APPROVED the branding production quote" in str(a) for a in self.alerts))


class Cards(Base):
    def _propose(self, direction="צבעים חמים ולוגו מינימלי"):
        return aa.propose(27, "client_chat", "media", {"name": "create_brand_mockups", "params": {"direction": direction}},
                          context={"language": "he"})

    def test_the_whole_flow_through_the_cards(self):
        first = self._propose()
        card = first["card"]
        self.assertIn(branding.terms_line("he"), card["what"])           # the one clear line, BEFORE starting
        self.assertIn("ניסיון 1 מתוך 5", card["what"])
        self.assertEqual(self.calls, [])                                  # proposing runs nothing
        done = aa.decide(card["id"], "client", 27, "approve")
        self.assertEqual(done["status"], "done")
        self.assertEqual(len(self.calls), 1)
        follow = done["next_card"]
        self.assertEqual(follow["action"], "approve_branding")
        self.assertEqual(follow["details"]["preview_url"], "/api/client/branding/view/1")
        self.assertIn("4", done["message"])                               # free attempts left
        # cancelling the approval card approves nothing
        aa.decide(follow["id"], "client", 27, "cancel")
        self.assertFalse(branding.summary(27)["approved"])
        # a second attempt, approved this time
        second = aa.decide(self._propose("כיוון אחר")["card"]["id"], "client", 27, "approve")
        approved = aa.decide(second["next_card"]["id"], "client", 27, "approve")
        self.assertEqual(approved["status"], "done")
        self.assertEqual(branding.summary(27)["approved_attempt"], 2)
        self.assertTrue(branding.kit(27))
        # after approval: no more attempts, said plainly, no card
        again = self._propose("עוד אחד")
        self.assertNotIn("card", again)
        self.assertEqual(again["refuse"], aa.ACTIONS["create_brand_mockups"]["refusals"]["already_approved"]["he"])

    def test_past_the_free_attempts_is_a_plain_line_and_one_alert(self):
        for i in range(branding.FREE_ATTEMPTS):
            branding.create_attempt(27, f"כיוון {i}", "he")
        self.alerts.clear()
        for _ in range(2):
            answer = self._propose("עוד אחד")
            self.assertNotIn("card", answer)
            self.assertIn("לא הרצתי כלום", answer["refuse"])
        self.assertEqual(len([a for a in self.alerts if "free branding attempts" in str(a)]), 1)
        self.assertEqual(len(self.calls), branding.FREE_ATTEMPTS)

    def test_a_model_cannot_open_the_code_only_cards(self):
        for name in ("approve_branding", "approve_production_quote"):
            self.assertTrue(aa.ACTIONS[name]["code_only"])
            out = aa.propose(27, "client_chat", "media", {"name": name, "params": {"attempt": 1, "quote": "x"}})
            self.assertEqual(out.get("error"), "not_allowed_here")

    def test_the_human_designer_card(self):
        card = aa.propose(27, "client_chat", "media", {"name": "request_logo_designer", "params": {}})["card"]
        self.assertEqual(aa.decide(card["id"], "client", 27, "approve")["status"], "done")
        self.assertTrue(branding.summary(27)["human_designer_requested"])
        self.assertEqual(len([a for a in self.alerts if "human designer" in str(a)]), 1)
        self.assertNotRegex(aa.ACTIONS["request_logo_designer"]["what"]["en"], r"₪|\d")   # no price, ever

    def test_every_card_text_in_five_languages_and_no_vendor_names(self):
        for name in ("create_brand_mockups", "approve_branding", "request_logo_designer", "approve_production_quote"):
            spec = aa.ACTIONS[name]
            texts = [spec["title"], spec["what"], spec["done"]] + list((spec.get("refusals") or {}).values())
            texts += [spec[k] for k in ("button", "cancelled") if spec.get(k)]
            if spec.get("slow"):
                texts.append(spec["slow"]["eta"])
            for table in texts:
                for lang in LANGS:
                    self.assertTrue(str(table.get(lang) or "").strip(), (name, lang))
                    for vendor in ("Claude", "Anthropic", "OpenAI", "PayPal", "Higgsfield", "SVG"):
                        self.assertNotIn(vendor, table[lang], (name, lang, vendor))
        what = aa.ACTIONS["approve_branding"]["what"]["en"]
        self.assertIn("live with every day", what)                        # the owner's, not ours
        self.assertIn("human designer", what)


class Wiring(unittest.TestCase):
    def test_the_prompt_rules(self):
        support = _src("agents", "support_agent.py")
        rule = support[support.index('STUDIO_RULE = """'):support.index('ACCOUNT_STATE_RULE = """')]
        for needle in ("branding.terms_line once, WORD FOR", "NEVER A FORM", "create_brand_mockups",
                       "request_logo_designer", "NEVER includes", "HUMAN HANDS ONLY", "do not refuse bluntly",
                       "Never state or estimate a price", "landing page connected to their existing site",
                       "never send the client to another chat"):
            self.assertIn(needle, rule, needle)
        self.assertNotIn("{", rule)                                       # the persona template is .format()-ed
        self.assertEqual(support.count("+ STUDIO_RULE +"), 2)             # the manager AND the specialists

    def test_the_brand_kit_has_its_readers(self):
        self.assertIn("branding.kit_palette_list(client_id)", _src("agents", "media_agent.py"))
        landing = _src("core", "landing_creative.py")
        self.assertIn('payload["client_approved_brand_kit"]', landing)
        self.assertIn('palette_source "brand_kit"', landing)

    def test_deleted_with_the_client(self):
        for name in ("client_deletion.py", "deletion_guard.py", "admin_service.py"):
            code = _src("core", name)
            self.assertIn("branding:", code, name)
            self.assertIn("brand_kit:", code, name)

    def test_routes_parity_admin_view_and_catalog(self):
        api = _src("core", "api_server.py")
        for route in ('@app.get("/api/client/branding")', '@app.get("/api/client/branding/view/{attempt}")',
                      '@app.post("/api/admin/branding/{client_id}/quote")',
                      '@app.post("/api/admin/branding/{client_id}/grant")'):
            self.assertIn(route, api)
        view = api[api.index("def client_branding_view"):api.index("class BrandingQuoteRequest")]
        self.assertIn("brand_mockups.page_headers()", view)
        self.assertIn("_require_session(request)", view)
        admin = api[api.index("def admin_branding_quote"):api.index("def admin_branding_grant")]
        self.assertIn("_require_admin(request)", admin)
        from core import admin_view, chat_parity, feature_catalog
        self.assertIn("client_branding", admin_view.READ_ENDPOINTS)
        self.assertIn("client_branding_view", admin_view.READ_ENDPOINTS)
        self.assertEqual(chat_parity.PARITY["GET /api/client/branding"], (chat_parity.CHAT, "create_brand_mockups"))
        entry = next(f for f in feature_catalog.FEATURES if f["key"] == "branding_mockups")
        self.assertEqual((entry["persona"], entry["access"]), ("media", "self_serve"))

    def test_the_dashboard_box(self):
        page = _src("dashboard", "client", "index.html")
        for needle in ('id="brandBox"', 'onclick="brandStart()"', "async function loadBranding()",
                       "fetch('/api/client/branding'", "openAgentWindow('media')", "sendInWindow('media')",
                       "t('brand_d_preview')"):
            self.assertIn(needle, page, needle)
        for key in ("brand_title", "brand_sub", "brand_btn", "brand_btn_again", "brand_btn_produce", "brand_view",
                    "brand_state_left", "brand_state_none", "brand_state_approved", "brand_chat_msg",
                    "brand_chat_again", "brand_chat_produce", "brand_d_preview"):
            line = re.search(r"^\s*" + key + r": \{(.*)\},?\s*$", page, re.M)
            self.assertTrue(line, key)
            for lang in LANGS:
                self.assertIn(f"{lang}:'", line.group(1), (key, lang))
        # the terms line is the server's fixed text, set as TEXT
        self.assertIn("document.getElementById('brandTerms').textContent", page)
        # only our own mockup path becomes a link
        self.assertIn(r"/^\/api\/client\/branding\/view\/\d+$/.test(d.preview_url)", page)


if __name__ == "__main__":
    unittest.main()
