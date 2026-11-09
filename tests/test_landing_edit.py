"""Surgical edit of a designed landing page (2026-11-10, docs/LANDING_PAGES.md
§surgical-edit).

- A change in words to a designed page: ONE cheap call answers find/replace
  pairs on the CURRENT saved HTML; no copy rewrite, no redesign.
- Each find must match exactly once; the result is checked (placeholders,
  one document, no emoji, size, no new resources / numbers / vendor names).
  One retry with the reason, then ONE full rebuild, then a calm line + an
  admin alert. Never two rebuilds for one request.
- A vague request ("make it nicer") → ONE card with two options; an explicit
  "new design" → straight to the rebuild.
- Every request is kept on the page (content.revisions); the next fix gets it.
- The structured copy follows the edit, or is marked stale so the fallback
  template never contradicts the page.

Stdlib only. Run:  python -I -B tests/test_landing_edit.py -v
"""
import contextlib
import json
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")
os.environ["PUBLIC_APP_URL"] = "https://app.uallak.com"


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


ACTIVITY, ALERTS = [], []

STUBS = {
    "httpx": _module("httpx"),
    "anthropic": _module("anthropic", Anthropic=lambda *a, **k: None),
    "supabase": _module("supabase", create_client=lambda *a, **k: None),
    "agents.client_agent": _module(
        "agents.client_agent",
        get_client=lambda cid: {"id": cid, "language": "he", "billing_exempt": False, "phone": "050-1234567"},
        get_activity=lambda *a, **k: [], get_communications=lambda *a, **k: [],
        log_activity=lambda *a, **k: ACTIVITY.append(a), _db=lambda: None, get_accounts=lambda cid: [],
        log_communication=lambda *a, **k: None),
    "agents.onboarding_agent": _module("agents.onboarding_agent", LANGUAGE_RULE="\n\nLANGUAGE.", PRICING={}),
    "core.automation_service": _module("core.automation_service", VENDORS={},
                                       POLICY_NOTE="Automation {is} quoted by hand.", SIGNUP_RULE_HE=""),
    "core.agent_base": _module("core.agent_base", agent_alert=lambda name, issues: ALERTS.append((name, issues)),
                               log_step=lambda *a, **k: None, timed_step=lambda a, s, fn: fn()),
    "core.competitor_research": _module("core.competitor_research", summary_for_prompt=lambda cid, lens: ""),
    "core.landing_domains": _module("core.landing_domains",
                                    get_state=lambda cid: {"mode": "shared", "hostname": "", "dns": None},
                                    normalize_hostname=lambda raw: (raw or "").strip().lower()),
    "core.cost_tracker": _module(
        "core.cost_tracker", cost_action=lambda *a, **k: contextlib.nullcontext(),
        claude_cost_ils=lambda i, o, m, cr=0, cw=0: round((i * 2 + o * 10 + cr * 0.2 + cw * 2.5) / 1e6 * 3.4, 4),
        claude_cost_usd=lambda i, o, m, cr=0, cw=0: round((i * 2 + o * 10 + cr * 0.2 + cw * 2.5) / 1e6, 4),
        record_cost=lambda *a, **k: None),
    "core.gtm_connection": _module("core.gtm_connection", _connection=lambda cid: {"access_token": "t"}),
    "agents.avatar_agent": _module("agents.avatar_agent", has_consent=lambda cid, scope: True,
                                   create_avatar=lambda *a, **k: {}, list_ready_avatars=lambda cid: [],
                                   generated_image_ids=lambda cid: set(), current_voice_id=lambda cid: ""),
    "agents.seo_agent": _module("agents.seo_agent", _business_context=lambda cid: {"business_name": "Spring"}),
}
_FRESH = ("agents.support_agent", "agents.landing_page_agent", "core.claude_json", "core.landing_creative")
_SAVED = {name: sys.modules.get(name) for name in list(STUBS) + list(_FRESH)}
sys.modules.update(STUBS)
for _name in _FRESH:
    sys.modules.pop(_name, None)

from agents import landing_page_agent as lpa   # noqa: E402
from core import agent_actions as aa           # noqa: E402
from core import claude_json                    # noqa: E402
from core import landing_creative as lc         # noqa: E402
from core import landing_pages as lp            # noqa: E402
from core import operator_flags                 # noqa: E402
from test_landing_own import FakeDB             # noqa: E402

PAGE_HTML = ('<!doctype html><html lang="he" dir="rtl"><head><title>נעליים</title>'
             '<style>.btn{background:#1e40af;color:#fff}</style></head><body>'
             '<header><img src="{{IMG:a1b2c3d4e5f6}}" alt="לוגו"></header>'
             '<h1>נעליים שאין לאף אחד אחר</h1><p class="sub">ציור יד על הנעל שבחרתם</p>'
             '<section id="faq"><h2>שאלות</h2><p>כמה זמן זה לוקח?</p></section>'
             '<a class="btn" href="{{WHATSAPP_URL}}">דברו איתנו</a>'
             '<form method="post" action="{{FORM_ACTION}}">{{FORM_HIDDEN}}<input name="name"><input name="phone">'
             '<button>קבלו הצעה</button><p>{{CONSENT}}</p></form><footer>{{FOOTER}}</footer></body></html>')
COPY = {"headline": "נעליים שאין לאף אחד אחר", "subheadline": "ציור יד על הנעל שבחרתם",
        "benefits": ["ציור יד", "מתנה מיוחדת"], "sections": [], "cta_text": "קבלו הצעה", "form_note": ""}
REDESIGNED = PAGE_HTML.replace("#1e40af", "#0f766e").replace("שאלות", "שאלות נפוצות")


def tearDownModule():
    for name, module in _SAVED.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def answer(pairs, copy_updates=None, needs_redesign=False):
    return {"pairs": pairs, "copy_updates": copy_updates or {}, "needs_redesign": needs_redesign}


def fake_message(text):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
        usage=types.SimpleNamespace(input_tokens=5000, output_tokens=20000, cache_read_input_tokens=15000,
                                    cache_creation_input_tokens=0))


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.posted = []
        ACTIVITY.clear()
        ALERTS.clear()
        self.saved = {"lp": lp._db, "aa": {n: getattr(aa, n) for n in ("_db", "_client", "_post_client_message",
                                                                       "_alert", "_log")},
                      "lpa": {n: getattr(lpa, n) for n in ("generate_copy", "propose_publish")},
                      "safe": claude_json.safe_claude_json_call, "record": claude_json._record,
                      "call": lc._call, "flag": operator_flags.enabled}
        lp._db = aa._db = lambda: self.db
        lp._versions_ok["value"] = True
        aa._client = lambda cid: {"id": cid, "language": "he", "billing_exempt": False}
        aa._post_client_message = lambda cid, persona, text: self.posted.append((persona, text))
        aa._alert = lambda issue: ALERTS.append(("aa", [issue]))
        aa._log = lambda *a, **k: None
        operator_flags.enabled = lambda name: True
        claude_json._record = lambda *a, **k: None
        # The model stand-ins: the edit call, the copy writer and the designer
        self.edit_calls, self.copy_calls, self.design_calls, self.cards = [], [], [], []
        self.answers = []

        def safe(system, message, **kwargs):
            self.edit_calls.append({"system": system, "message": message, **kwargs})
            reply = self.answers.pop(0) if self.answers else answer([])
            if isinstance(reply, Exception):
                raise reply
            return reply
        claude_json.safe_claude_json_call = safe

        def generate_copy(client_id, goal, title="", current=None):
            self.copy_calls.append({"goal": goal, "current": current})
            return {"success": True, "content": lp.normalize_content({**COPY, "headline": "כותרת חדשה"}),
                    "notes": "", "research_grounded": False}
        lpa.generate_copy = generate_copy
        self.design = lambda: fake_message(REDESIGNED)
        lc._call = lambda system, user: (self.design_calls.append(user), self.design())[1]
        lpa.propose_publish = lambda cid, page_id, intro="", persona="website": (
            self.cards.append((page_id, lp.get_page(cid, page_id).get("current_version"))) or {"card": {"id": 1}})

    def tearDown(self):
        lp._db = self.saved["lp"]
        for n, fn in self.saved["aa"].items():
            setattr(aa, n, fn)
        for n, fn in self.saved["lpa"].items():
            setattr(lpa, n, fn)
        claude_json.safe_claude_json_call, claude_json._record = self.saved["safe"], self.saved["record"]
        lc._call, operator_flags.enabled = self.saved["call"], self.saved["flag"]

    def designed_page(self, client_id=27, html=PAGE_HTML, content=None):
        page_id = lp.create_page(client_id, "נעליים", "נעלי סניקרס בציור יד")["page"]["id"]
        lp.update_page(client_id, page_id, {"content": {**(content or COPY), "html": html}}, source="designed")
        return page_id

    def page(self, page_id, client_id=27):
        return lp.get_page(client_id, page_id)

    def content(self, page_id, client_id=27):
        return lp.normalize_content(self.page(page_id, client_id).get("content") or {})


# ─── The pairs: exactly once ─────────────────────────────────────────────────

class Pairs(Base):
    def test_a_find_that_matches_once_is_applied(self):
        out, reason = lc.apply_pairs(PAGE_HTML, [{"find": "#1e40af", "replace": "#15803d"}])
        self.assertEqual(reason, "")
        self.assertEqual(out, PAGE_HTML.replace("#1e40af", "#15803d"))

    def test_zero_matches_is_refused(self):
        self.assertEqual(lc.apply_pairs(PAGE_HTML, [{"find": "#abcdef", "replace": "x"}]), (None, "find_missing"))

    def test_more_than_one_match_is_refused(self):
        self.assertEqual(lc.apply_pairs(PAGE_HTML, [{"find": "<p", "replace": "<div"}]), (None, "find_ambiguous"))

    def test_overlapping_pairs_and_bad_shapes_are_refused(self):
        pairs = [{"find": "דברו איתנו", "replace": "כתבו לנו"}, {"find": "איתנו</a>", "replace": "x</a>"}]
        self.assertEqual(lc.apply_pairs(PAGE_HTML, pairs)[1], "find_overlap")
        self.assertEqual(lc.apply_pairs(PAGE_HTML, [])[1], "no_pairs")
        self.assertEqual(lc.apply_pairs(PAGE_HTML, [{"find": "", "replace": "x"}])[1], "bad_pair")
        self.assertEqual(lc.apply_pairs(PAGE_HTML, "nope")[1], "no_pairs")
        many = [{"find": "a", "replace": "a"}] * (lc.EDIT_MAX_PAIRS + 1)
        self.assertEqual(lc.apply_pairs(PAGE_HTML, many)[1], "too_many_pairs")


# ─── The checks before anything is stored ───────────────────────────────────

class Checks(Base):
    def check(self, find, replace, request=""):
        new, reason = lc.apply_pairs(PAGE_HTML, [{"find": find, "replace": replace}])
        self.assertEqual(reason, "")
        return lc.check_edited(PAGE_HTML, new, request)

    def test_a_clean_edit_passes(self):
        self.assertEqual(self.check("#1e40af", "#15803d"), "")
        self.assertEqual(self.check('<section id="faq"><h2>שאלות</h2><p>כמה זמן זה לוקח?</p></section>', ""), "")

    def test_placeholders_must_stay(self):
        self.assertEqual(self.check("{{CONSENT}}", ""), "placeholders")
        self.assertEqual(self.check("<footer>{{FOOTER}}</footer>", "<footer>{{FOOTER}}{{FOOTER}}</footer>"),
                         "placeholders")
        self.assertEqual(self.check("<h1>", "<h1>{{STORE_URL}}"), "placeholders")

    def test_still_one_document(self):
        self.assertEqual(self.check("</body></html>", "</body></html><html><body>x</body></html>"),
                         "not_one_document")
        self.assertEqual(self.check("</footer></body>", "</footer>"), "not_one_document")

    def test_no_emoji(self):
        self.assertEqual(self.check("דברו איתנו", "דברו איתנו 😀"), "emoji")

    def test_size_limit(self):
        saved = lp.MAX_CREATIVE_BYTES
        lp.MAX_CREATIVE_BYTES = len(PAGE_HTML.encode("utf-8")) + 10
        try:
            self.assertEqual(self.check("<h2>שאלות</h2>", "<h2>" + "שאלות " * 20 + "</h2>"), "too_large")
        finally:
            lp.MAX_CREATIVE_BYTES = saved

    def test_no_invented_numbers_but_the_clients_own_are_fine(self):
        self.assertEqual(self.check("<h1>", "<h1>רק 199 ₪ "), "new_number")
        self.assertEqual(self.check("<h1>", "<h1>רק 199 ₪ ", request="תוסיף שהמחיר 199 ש\"ח"), "")
        self.assertEqual(self.check(".btn{", ".btn{padding:12px 24px;"), "")   # CSS numbers are not facts

    def test_no_new_resources_scripts_or_vendor_names(self):
        self.assertEqual(self.check("</head>", '<script src="https://evil.example/x.js"></script></head>'),
                         "external")
        self.assertEqual(self.check("</body>", "<script>fetch('/api/x')</script></body>"), "forbidden")
        self.assertEqual(self.check("</body>", "<iframe></iframe></body>"), "forbidden")
        self.assertEqual(self.check("<h1>", "<h1>נבנה עם AI "), "vendor")

    def test_the_prompt_is_short_and_keeps_the_rules(self):
        for rule in ("NEVER add a fact", "EXACTLY ONCE", "NO EMOJIS", "עיצוב אישי", "DATA", "{{FORM_ACTION}}"):
            self.assertIn(rule, lc.EDIT_RULES)
        self.assertLess(len(lc.EDIT_RULES), 5000)
        self.assertNotIn("PLAYBOOK", lc.EDIT_RULES.upper())


# ─── The one call, its retry, its model and cost ────────────────────────────

class Call(Base):
    def test_one_call_on_the_standard_tier_with_its_cost_category(self):
        self.answers = [answer([{"find": "#1e40af", "replace": "#15803d"}])]
        out = lc.surgical_edit(27, 1, PAGE_HTML, "תעשה את הכפתור ירוק", ["קודם"], COPY)
        self.assertTrue(out["success"])
        self.assertEqual(len(self.edit_calls), 1)
        call = self.edit_calls[0]
        self.assertEqual(call["model"], "claude-sonnet-5-5")
        self.assertEqual(call["cost_category"], "claude_landing_page")
        self.assertEqual((call["retry_empty"], call["json_retry"]), (False, False))   # no hidden paid retries
        self.assertEqual(call["client_id"], 27)
        # the short rules are cached; the page, the request and the history are data after them
        self.assertEqual(len(call["system"]), 1)
        self.assertIn("cache_control", call["system"][0])
        self.assertIn(PAGE_HTML, call["message"])
        payload = json.loads(call["message"].split("\n", 1)[1].split("\n\nTHE PAGE")[0])
        self.assertEqual((payload["request"], payload["earlier_requests"]), ("תעשה את הכפתור ירוק", ["קודם"]))
        self.assertEqual(out["record"]["kind"], "surgical_edit")
        self.assertEqual((out["record"]["outcome"], out["record"]["pairs"]), ("ok", 1))

    def test_a_failed_check_is_retried_once_with_the_reason(self):
        self.answers = [answer([{"find": "<p", "replace": "<div"}]),
                        answer([{"find": "#1e40af", "replace": "#15803d"}])]
        out = lc.surgical_edit(27, 1, PAGE_HTML, "ירוק")
        self.assertTrue(out["success"])
        self.assertEqual(len(self.edit_calls), 2)
        self.assertIn("find_ambiguous", self.edit_calls[1]["message"])
        self.assertEqual(out["record"]["retries"], 1)

    def test_two_failures_end_it(self):
        self.answers = [answer([{"find": "nope", "replace": "x"}]), RuntimeError("down")]
        out = lc.surgical_edit(27, 1, PAGE_HTML, "ירוק")
        self.assertFalse(out["success"])
        self.assertEqual(len(self.edit_calls), 2)                       # never a third
        self.assertEqual(out["record"]["outcome"], "api_error")

    def test_needs_redesign_is_not_retried(self):
        self.answers = [answer([], needs_redesign=True)]
        out = lc.surgical_edit(27, 1, PAGE_HTML, "דף אחר לגמרי")
        self.assertTrue(out["needs_redesign"])
        self.assertEqual(len(self.edit_calls), 1)


# ─── The whole change: surgical, history, copy, fallback ────────────────────

class Change(Base):
    def test_a_small_fix_edits_the_existing_page_only(self):
        page_id = self.designed_page()
        before = self.page(page_id)["current_version"]
        self.answers = [answer([{"find": "#1e40af", "replace": "#15803d"}])]
        out = lpa.change_page(27, page_id, "תעשה את הכפתור ירוק")
        self.assertTrue(out["success"])
        self.assertEqual((self.copy_calls, self.design_calls), ([], []))   # no rewrite, no redesign
        content = self.content(page_id)
        self.assertEqual(content["html"], PAGE_HTML.replace("#1e40af", "#15803d"))   # the rest byte-for-byte
        self.assertEqual(self.page(page_id)["current_version"], before + 1)
        self.assertEqual(content["revisions"][-1]["request"], "תעשה את הכפתור ירוק")
        self.assertEqual(content["revisions"][-1]["mode"], "edit")
        self.assertNotIn("copy_stale", content)                          # a colour: the copy still matches
        self.assertEqual(self.cards, [(page_id, before + 1)])             # the preview card for the new version
        rows = [a for a in ACTIVITY if a[2] == lc.ACTIVITY_TYPE]
        self.assertEqual(rows[-1][3]["kind"], "surgical_edit")
        self.assertEqual(rows[-1][3]["version"], before + 1)

    def test_the_second_fix_knows_the_first_and_edits_the_saved_html(self):
        page_id = self.designed_page()
        self.answers = [answer([{"find": "#1e40af", "replace": "#15803d"}])]
        lpa.change_page(27, page_id, "כפתור ירוק")
        self.answers = [answer([{"find": "<h2>שאלות</h2>", "replace": "<h2>שאלות נפוצות</h2>"}])]
        lpa.change_page(27, page_id, "תשנה את הכותרת של השאלות")
        second = self.edit_calls[-1]["message"]
        self.assertIn("#15803d", second)                                  # built on the first fix
        self.assertIn('"earlier_requests": ["כפתור ירוק"]', second)
        content = self.content(page_id)
        self.assertIn("#15803d", content["html"])
        self.assertIn("שאלות נפוצות", content["html"])
        self.assertEqual([r["request"] for r in content["revisions"]], ["כפתור ירוק", "תשנה את הכותרת של השאלות"])

    def test_the_copy_follows_the_edit(self):
        page_id = self.designed_page()
        self.answers = [answer([{"find": "<h1>נעליים שאין לאף אחד אחר</h1>", "replace": "<h1>נעליים רק שלכם</h1>"}])]
        lpa.change_page(27, page_id, "תשנה את הכותרת ל'נעליים רק שלכם'")
        content = self.content(page_id)
        self.assertEqual(content["headline"], "נעליים רק שלכם")
        self.assertNotIn("copy_stale", content)

    def test_the_models_copy_updates_are_used(self):
        page_id = self.designed_page()
        self.answers = [answer([{"find": "<button>קבלו הצעה</button>", "replace": "<button>שריינו נעל</button>"}],
                               copy_updates={"cta_text": "שריינו נעל", "unknown": "x"})]
        lpa.change_page(27, page_id, "טקסט אחר לכפתור")
        content = self.content(page_id)
        self.assertEqual(content["cta_text"], "שריינו נעל")
        self.assertNotIn("copy_stale", content)

    def test_text_that_cannot_be_mapped_marks_the_copy_stale(self):
        page_id = self.designed_page()
        self.answers = [answer([{"find": "<p>כמה זמן זה לוקח?</p>", "replace": "<p>איך מזמינים?</p>"}])]
        lpa.change_page(27, page_id, "תחליף את השאלה")
        content = self.content(page_id)
        self.assertTrue(content["copy_stale"])
        page = self.page(page_id)
        fallback = lp.render_page(page, "https://a/c", "https://a/lp/c27/x", visible={"title": "נעליים",
                                                                                      "content": content},
                                  creative=False)
        self.assertIn('<form class="lead"', fallback)
        self.assertNotIn(COPY["subheadline"], fallback)                   # nothing that could contradict the page
        self.assertNotIn(COPY["benefits"][0], fallback)
        # a full rebuild makes fresh copy: no longer stale
        self.assertTrue(lpa.change_page(27, page_id, "עיצוב חדש לגמרי", mode="redesign")["success"])
        self.assertNotIn("copy_stale", self.content(page_id))
        self.assertIsNone(self.copy_calls[-1]["current"])                 # stale copy is never fed back

    def test_a_failed_edit_falls_back_to_one_rebuild(self):
        page_id = self.designed_page()
        self.answers = [answer([{"find": "nope", "replace": "x"}]), answer([{"find": "{{CONSENT}}", "replace": ""}])]
        out = lpa.change_page(27, page_id, "תעשה את הכפתור ירוק")
        self.assertTrue(out["success"])
        self.assertTrue(out["fell_back"])
        self.assertEqual((len(self.edit_calls), len(self.copy_calls), len(self.design_calls)), (2, 1, 1))
        content = self.content(page_id)
        self.assertEqual(content["html"], REDESIGNED)
        self.assertEqual(content["revisions"][-1]["mode"], "redesign")
        self.assertIn("תעשה את הכפתור ירוק", self.copy_calls[0]["goal"])
        self.assertEqual(self.copy_calls[0]["current"]["headline"], COPY["headline"])   # keeps the current text

    def test_when_everything_fails_nothing_is_stored_and_the_team_is_told(self):
        page_id = self.designed_page()
        versions = len(lp.list_versions(27, page_id))
        self.answers = [RuntimeError("down"), RuntimeError("down")]
        self.design = lambda: (_ for _ in ()).throw(RuntimeError("down"))
        out = lpa.change_page(27, page_id, "תעשה את הכפתור ירוק")
        self.assertEqual(out, {"success": False, "code": "ERR_LANDING_IN_HAND"})
        self.assertEqual(len(lp.list_versions(27, page_id)), versions)    # no plain-template version left
        self.assertEqual(self.content(page_id)["html"], PAGE_HTML)
        self.assertEqual(len(self.copy_calls), 1)                         # ONE rebuild, never two
        self.assertLessEqual(len(self.design_calls), lc.MAX_ATTEMPTS)
        self.assertTrue(any("change to page" in i for _, issues in ALERTS for i in issues))
        self.assertEqual(self.cards, [])

    def test_explicit_redesign_skips_the_edit(self):
        page_id = self.designed_page()
        out = lpa.change_page(27, page_id, "תעצב לי את הדף מחדש", mode="redesign")
        self.assertTrue(out["success"])
        self.assertEqual(self.edit_calls, [])
        self.assertEqual(len(self.design_calls), 1)

    def test_pages_without_a_design_and_photo_changes_keep_the_rewrite(self):
        page_id = lp.create_page(27, "נעליים", "goal")["page"]["id"]
        lp.update_page(27, page_id, {"content": dict(COPY)}, source="generated")
        lpa.change_page(27, page_id, "קצר יותר")
        self.assertEqual(self.edit_calls, [])
        self.assertIn("CLIENT FEEDBACK ON THE PREVIOUS VERSION: קצר יותר", self.copy_calls[0]["goal"])
        self.assertEqual(self.copy_calls[0]["current"]["headline"], COPY["headline"])
        lpa.change_page(27, page_id, "ועם המחיר למעלה")
        self.assertIn("EARLIER CHANGE REQUESTS", self.copy_calls[1]["goal"])
        self.assertIn("- קצר יותר", self.copy_calls[1]["goal"])

    def test_history_survives_other_edits_and_is_bounded(self):
        page_id = self.designed_page()
        self.answers = [answer([{"find": "#1e40af", "replace": "#15803d"}])]
        lpa.change_page(27, page_id, "כפתור ירוק")
        lp.update_page(27, page_id, {"content": {**COPY, "headline": "שלי"}}, source="client_edit")
        self.assertEqual([r["request"] for r in self.content(page_id)["revisions"]], ["כפתור ירוק"])
        many = lp.normalize_revisions([{"request": f"r{i}", "mode": "edit"} for i in range(40)] + ["junk", {}])
        self.assertEqual(len(many), lp.MAX_REVISIONS)
        self.assertEqual(many[-1]["request"], "r39")
        long_one = lp.normalize_revisions([{"request": "x" * 2000}])[0]["request"]
        self.assertEqual(len(long_one), lp.MAX_REVISION_CHARS)

    def test_the_kill_switch_off_keeps_the_rewrite(self):
        page_id = self.designed_page()
        operator_flags.enabled = lambda name: name != lc.FLAG
        lpa.change_page(27, page_id, "כפתור ירוק")
        self.assertEqual(self.edit_calls, [])
        self.assertEqual(len(self.copy_calls), 1)


# ─── The card: small, redesign, or both options for a vague request ────────

class Card(Base):
    def propose(self, page_id, request, scope=None):
        params = {"page_id": page_id, "change_request": request}
        if scope:
            params["scope"] = scope
        return aa.propose(27, "client_chat", "general", {"name": "edit_landing_page", "params": params},
                          context={"language": "he"})

    def row(self, card_id):
        return next(r for r in self.db.tables[aa.TABLE] if r["id"] == card_id)

    def test_classify(self):
        self.assertEqual(lpa.classify_request("תעשה את הדף יותר יפה"), "unclear")
        self.assertEqual(lpa.classify_request("make it nicer"), "unclear")
        self.assertEqual(lpa.classify_request("שדרג אותו"), "unclear")
        self.assertEqual(lpa.classify_request("תעשה את הכפתור ירוק"), "small")
        self.assertEqual(lpa.classify_request("תוריד את החלק של השאלות"), "small")
        self.assertEqual(lpa.classify_request("יותר מקצועי, ותשנה את הכותרת"), "small")
        self.assertEqual(lpa.classify_request("אני רוצה עיצוב חדש לגמרי"), "redesign")
        self.assertEqual(lpa.classify_request("redesign it from scratch"), "redesign")

    def test_a_vague_request_gets_one_card_with_two_options(self):
        page_id = self.designed_page()
        out = self.propose(page_id, "תעשה את הדף יותר מקצועי")
        self.assertNotIn("clarify", out)                                  # never an open question
        card = out["card"]
        self.assertEqual([c["key"] for c in card["choices"]], ["edit", "redesign"])
        self.assertEqual([c["label"] for c in card["choices"]], ["כן, שינוי קטן בדף הקיים", "עיצוב חדש לגמרי"])
        self.assertIn("מאשר?", card["what"])
        self.assertEqual(self.row(card["id"])["params"]["mode"], "ask")
        self.assertEqual(self.edit_calls + self.copy_calls, [])           # nothing ran yet

    def test_the_click_picks_the_path(self):
        page_id = self.designed_page()
        card = self.propose(page_id, "יותר מודרני")["card"]
        out = aa.decide(card["id"], "client", 27, "approve", choice="redesign")
        self.assertEqual(out["status"], "done", out)
        self.assertEqual((self.edit_calls, len(self.design_calls)), ([], 1))
        self.assertEqual(self.row(card["id"])["params"]["mode"], "redesign")
        card = self.propose(page_id, "יותר מודרני בבקשה")["card"]
        self.answers = [answer([{"find": "#0f766e", "replace": "#111827"}])]
        out = aa.decide(card["id"], "client", 27, "approve", choice="edit")
        self.assertEqual(out["status"], "done", out)
        self.assertEqual(len(self.edit_calls), 1)
        self.assertEqual(len(self.design_calls), 1)                       # no second redesign

    def test_a_choice_that_is_not_offered_is_refused(self):
        page_id = self.designed_page()
        card = self.propose(page_id, "יותר יפה")["card"]
        self.assertEqual(aa.decide(card["id"], "client", 27, "approve", choice="publish")["code"], "bad_choice")
        self.assertEqual(self.row(card["id"])["status"], "proposed")

    def test_explicit_redesign_and_small_requests_get_a_plain_card(self):
        page_id = self.designed_page()
        card = self.propose(page_id, "אני רוצה עיצוב חדש לגמרי")["card"]
        self.assertEqual(card["choices"], [])
        self.assertEqual(self.row(card["id"])["params"]["mode"], "redesign")
        self.assertIn("מחדש לגמרי", card["what"])
        card = self.propose(page_id, "תעשה את הכפתור ירוק", scope="small")["card"]
        self.assertEqual(card["choices"], [])
        self.assertEqual(self.row(card["id"])["params"]["mode"], "edit")
        self.assertIn("העיצוב נשאר", card["what"])
        # the model's own scope wins for a vague one it read as concrete... but an explicit redesign never edits
        card = self.propose(page_id, "עיצוב חדש לגמרי בבקשה", scope="small")["card"]
        self.assertEqual(self.row(card["id"])["params"]["mode"], "redesign")

    def test_a_page_without_a_design_gets_the_old_card(self):
        page_id = lp.create_page(27, "נעליים", "goal")["page"]["id"]
        lp.update_page(27, page_id, {"content": dict(COPY)}, source="generated")
        card = self.propose(page_id, "יותר יפה")["card"]
        self.assertEqual(card["choices"], [])
        self.assertNotIn("mode", self.row(card["id"])["params"])

    def test_failure_reads_one_calm_line(self):
        page_id = self.designed_page()
        card = self.propose(page_id, "תעשה את הכפתור ירוק")["card"]
        self.answers = [RuntimeError("down"), RuntimeError("down")]
        self.design = lambda: (_ for _ in ()).throw(RuntimeError("down"))
        out = aa.decide(card["id"], "client", 27, "approve")
        self.assertEqual((out["status"], out["code"]), ("failed", "in_hand"))
        self.assertTrue(out["message"].startswith("עובדים על זה, אעדכן כשמוכן"))
        for word in ("שגיאה", "error", "מודל", "AI", "Claude"):
            self.assertNotIn(word, out["message"])
        self.assertEqual(self.posted[-1][1], out["message"])

    def test_the_progress_line_fits_a_small_edit(self):
        spec = aa.ACTIONS["edit_landing_page"]
        self.assertIn("דקה-שתיים", aa.progress_text(spec, "he", {"mode": "edit"}))
        self.assertIn("5-10", aa.progress_text(spec, "he", {"mode": "redesign"}))


class Wiring(Base):
    def test_the_ui_sends_the_choice_and_the_route_passes_it(self):
        page = _src("dashboard", "client", "index.html")
        self.assertIn("a.choices", page)
        self.assertIn("body.choice = choice", page)
        api = _src("core", "api_server.py")
        self.assertIn('choice: str = ""', api)
        self.assertEqual(api.count("choice=req.choice"), 2)

    def test_the_chat_prompt_explains_scope(self):
        prompt = _src("agents", "support_agent.py")
        self.assertIn("scope", prompt)
        self.assertIn("never ask them an open question", prompt)

    def test_docs(self):
        self.assertIn("surgical", _src("docs", "LANDING_PAGES.md").lower())
        self.assertIn("surgical", _src("CLAUDE.md").lower())
        self.assertIn("surgical", _src(".claude", "skills", "landing-pages", "SKILL.md").lower())


if __name__ == "__main__":
    unittest.main()
