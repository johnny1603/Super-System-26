"""Creative landing pages, freedom first (2026-11-07, docs/LANDING_PAGES.md
§creative): the buttons (root cause + every button followed to its final
destination), the ONE rail (the CSP sandbox on every page response), the
form → lead path with attribution and the chosen interest, click counting,
the designed page's shell (placeholders, images, no emoji), the fallback
renderer when generation fails or the kill switch is off, and the
generation/cost records.

The ROUTES are the real code: their functions are taken out of
core/api_server.py with ast and run against stand-ins for FastAPI, so a
change to a route is a change under test (the app itself needs packages this
stdlib-only suite does not install).

Run:  python -I -B tests/test_landing_creative.py -v
"""
import ast
import asyncio
import contextlib
import json
import os
import re
import sys
import tempfile
import types
import unittest
from urllib.parse import parse_qs, unquote, urlparse

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


ACTIVITY, ALERTS, COSTS = [], [], []
CLIENTS = {7: {"id": 7, "business_name": "Spring Shoes", "phone": "050-123-4567", "language": "he"}}

STUBS = {
    "httpx": _module("httpx"),
    "anthropic": _module("anthropic", Anthropic=lambda *a, **k: None),
    "supabase": _module("supabase", create_client=lambda *a, **k: None),
    "agents.client_agent": _module(
        "agents.client_agent", get_client=lambda cid: CLIENTS.get(cid, {"id": cid}), _db=lambda: None,
        log_activity=lambda *a, **k: ACTIVITY.append(a), log_communication=lambda *a, **k: None),
    "core.agent_base": _module("core.agent_base", agent_alert=lambda name, issues: ALERTS.append((name, issues)),
                               log_step=lambda *a, **k: None, timed_step=lambda a, s, fn: fn()),
    "core.competitor_research": _module("core.competitor_research", summary_for_prompt=lambda cid, lens: ""),
    "core.landing_domains": _module("core.landing_domains",
                                    get_state=lambda cid: {"mode": "shared", "hostname": "", "dns": None}),
    "core.cost_tracker": _module(
        "core.cost_tracker", cost_action=lambda *a, **k: contextlib.nullcontext(),
        claude_cost_ils=lambda i, o, m, cr=0, cw=0: round((i * 4 + o * 20 + cr * 0.2 + cw * 5) / 1e6 * 3.7, 4),
        claude_cost_usd=lambda i, o, m, cr=0, cw=0: round((i * 4 + o * 20 + cr * 0.2 + cw * 5) / 1e6, 4),
        record_cost=lambda *a, **k: COSTS.append((a, k))),
    "agents.seo_agent": _module("agents.seo_agent", _business_context=lambda cid: {
        "business_name": "Spring Shoes", "industry": "shoes", "description": "נעליים בעבודת יד"}),
    "agents.crm_agent": _module("agents.crm_agent", sync_lead_safe=lambda *a, **k: None),
}
_FRESH = ("agents.landing_page_agent", "core.claude_json", "core.landing_creative")
_SAVED = {name: sys.modules.get(name) for name in list(STUBS) + list(_FRESH)}
sys.modules.update(STUBS)
for _name in _FRESH:
    sys.modules.pop(_name, None)

from agents import landing_page_agent as lpa   # noqa: E402
from core import claude_json                    # noqa: E402
from core import client_leads                   # noqa: E402
from core import landing_creative as lc         # noqa: E402
from core import landing_images as li           # noqa: E402
from core import landing_pages as lp            # noqa: E402
from core import landing_tracking as lt         # noqa: E402
from core import operator_flags                 # noqa: E402
from test_landing_own import FakeDB             # noqa: E402

KEY = "a1b2c3d4e5f6"
REFS = [{"id": 5, "key": KEY, "placement": "hero", "alt": "נעל בעבודת יד", "w": 1200, "h": 800},
        {"id": 6, "key": "0f0f0f0f0f0f", "placement": "logo", "alt": "הלוגו", "w": 400, "h": 200}]
CONTACT = {"phone": "050-123-4567", "whatsapp": "050-123-4567", "store_url": "https://shop.example.co.il/p"}
COPY = {"headline": "נעליים שאין לאף אחד אחר", "subheadline": "ציור יד על הנעל שבחרתם",
        "benefits": ["ציור יד", "מתנה אישית"], "sections": [], "cta_text": "קבלו הצעה", "form_note": ""}
DESIGNED = ("<!doctype html><html lang=\"he\" dir=\"rtl\"><head><title>t</title></head><body>"
            "<header><img src=\"{{IMG:" + KEY + "}}\" alt=\"x\"><img src=\"{{IMG:ffffffffffff}}\" alt=\"y\"></header>"
            "<a class=\"btn\" href=\"{{WHATSAPP_URL}}\" data-wa>דברו איתנו</a>"
            "<a href=\"{{CALL_URL}}\">התקשרו</a><a href=\"{{STORE_URL}}\">לחנות</a>"
            "<form id=\"lf\" method=\"post\" action=\"{{FORM_ACTION}}\">{{FORM_HIDDEN}}"
            "<input type=\"radio\" name=\"interest\" value=\"נעליים\"><input name=\"name\"><input name=\"phone\">"
            "<button>שלחו 😀</button><p>{{CONSENT}}</p></form><footer>{{FOOTER}}</footer>"
            "<script>var x='✨';</script></body></html>")


def tearDownModule():
    for name, module in _SAVED.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


# ─── The real route code, out of core/api_server.py ──────────────────────────

API_SRC = _src("core", "api_server.py")
API_TREE = ast.parse(API_SRC)


class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(status_code)
        self.status_code, self.detail = status_code, detail


class RedirectResponse:
    def __init__(self, url, status_code=307):
        self.url, self.status_code = url, status_code


class HTMLResponse:
    def __init__(self, content, headers=None):
        self.body, self.headers = content, headers or {}


class Tasks:
    def __init__(self):
        self.calls = []

    def add_task(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))


class URL:
    def __init__(self, text):
        self.text = text
        self.path = urlparse(text).path

    def replace(self, query=None, scheme=None):
        p = urlparse(self.text)
        return URL(p._replace(query=query if query is not None else p.query, scheme=scheme or p.scheme).geturl())

    def __str__(self):
        return self.text


class Request:
    def __init__(self, url, headers=None, query=None, body=b""):
        self.url, self.headers = URL(url), {k.lower(): v for k, v in (headers or {}).items()}
        self.query_params, self._body = query or {}, body

    async def body(self):
        return self._body


def route_namespace(**extra) -> dict:
    ns = {"re": re, "os": os, "json": json, "HTTPException": HTTPException, "RedirectResponse": RedirectResponse,
          "HTMLResponse": HTMLResponse, "BackgroundTasks": Tasks, "Request": Request, **extra}
    for name in ("_lp_client", "_lp_live_page", "_lp_business", "_lp_links", "_lp_preview_links", "_lp_host_of",
                 "_lp_legal", "landing_go", "serve_landing_page", "client_landing_preview", "public_lead_capture"):
        node = next(n for n in ast.walk(API_TREE) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == name)
        exec(compile(ast.get_source_segment(API_SRC, node), "core/api_server.py", "exec"), ns)
    return ns


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.saved = {"lp": lp._db, "li": li._db, "cl": client_leads._db, "call": lc._call,
                      "record": claude_json._record, "flag": operator_flags.enabled}
        lp._db = li._db = client_leads._db = lambda: self.db
        lp._versions_ok["value"] = True
        lt._stats_ok["value"] = True
        lt._ip_hits.clear()
        ACTIVITY.clear()
        ALERTS.clear()
        COSTS.clear()
        self.cost_rows = []
        claude_json._record = lambda *a, **k: self.cost_rows.append((a, k))
        operator_flags.enabled = lambda name: True
        self.bumps = []

    def tearDown(self):
        lp._db, li._db, client_leads._db = self.saved["lp"], self.saved["li"], self.saved["cl"]
        lc._call, claude_json._record = self.saved["call"], self.saved["record"]
        operator_flags.enabled = self.saved["flag"]

    def live_page(self, content=None, contact=None, title="Spring", client_id=7):
        created = lp.create_page(client_id, title, "goal")
        page_id = created["page"]["id"]
        updated = lp.update_page(client_id, page_id, {"content": content or dict(COPY),
                                                      "contact": contact or CONTACT}, source="generated")
        self.assertTrue(updated["success"], updated)
        self.assertTrue(lp.publish(client_id, page_id)["success"])
        return lp.get_page(client_id, page_id)


def fake_message(text, stop="end_turn", usage=(1200, 18000, 15000, 0)):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="thinking", thinking=""), types.SimpleNamespace(type="text", text=text)],
        stop_reason=stop,
        usage=types.SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1],
                                    cache_read_input_tokens=usage[2], cache_creation_input_tokens=usage[3]))


# ─── 0. The buttons ──────────────────────────────────────────────────────────

class Buttons(Base):
    def test_root_cause_local_whatsapp_numbers_become_international(self):
        # wa.me/0501234567 is a dead link: wa.me needs the country code
        for raw in ("050-123-4567", "0501234567", "+972 50 123 4567", "00972501234567", "972-50-1234567"):
            self.assertEqual(lp.normalize_contact({"whatsapp": raw})["whatsapp"], "972501234567", raw)
        self.assertEqual(lp.normalize_contact({"whatsapp": "12345"}), {})

    def test_every_button_on_the_live_page_lands_on_its_final_destination(self):
        page = self.live_page(content={**COPY, "html": DESIGNED})
        ns = route_namespace()
        tasks = Tasks()
        # 1) the page as a visitor gets it
        html_text = ns["serve_landing_page"]("spring-shoes-7", page["slug"],
                                             Request(f"http://app.uallak.com/lp/spring-shoes-7/{page['slug']}"),
                                             tasks).body
        hrefs = dict(re.findall(r'<a [^>]*href="([^"]+)"[^>]*>(דברו איתנו|התקשרו|לחנות)', html_text))
        hrefs = {label: href for href, label in hrefs.items()}
        base = f"/lp/spring-shoes-7/{page['slug']}/go/"
        self.assertEqual(hrefs, {"דברו איתנו": base + "whatsapp", "התקשרו": base + "call", "לחנות": base + "store"})
        # 2) the page's own script appends the visitor's message (as the exemplar does)
        chosen = "היי, הגעתי מהדף ואני מחפש/ת נעליים"
        clicked = hrefs["דברו איתנו"] + "?text=" + __import__("urllib.parse").parse.quote(chosen)
        # 3) the counting redirect, followed
        finals = {}
        for label, href in (("whatsapp", clicked), ("call", hrefs["התקשרו"]), ("store", hrefs["לחנות"])):
            path, _, query = href.partition("?")
            seg, slug, _go, kind = path.strip("/").split("/")[1:]
            text = parse_qs(query).get("text", [""])[0]
            response = ns["landing_go"](seg, slug, kind, tasks, text)
            self.assertEqual(response.status_code, 302)
            finals[label] = response.url
        self.assertEqual(finals["call"], "tel:0501234567")
        self.assertEqual(finals["store"], "https://shop.example.co.il/p")
        wa = urlparse(finals["whatsapp"])
        self.assertEqual((wa.scheme, wa.netloc, wa.path), ("https", "wa.me", "/972501234567"))
        self.assertEqual(unquote(parse_qs(wa.query)["text"][0]), chosen)          # the prefilled text arrives
        # every click counted, once, on the right counter
        counted = [args[2] for fn, args, _ in tasks.calls if fn is lt.bump and args[2] != "views"]
        self.assertEqual(counted, ["whatsapp", "calls", "store"])

    def test_whatsapp_without_text_gets_a_default_line_naming_the_page(self):
        page = self.live_page()
        response = route_namespace()["landing_go"]("c7", page["slug"], "whatsapp", Tasks(), "")
        text = parse_qs(urlparse(response.url).query)["text"][0]
        self.assertIn("Spring", text)

    def test_the_text_never_changes_the_number_and_is_sanitized(self):
        page = self.live_page()
        response = route_namespace()["landing_go"](
            "c7", page["slug"], "whatsapp", Tasks(), "<script>x</script>https://evil.example/?phone=1" + "א" * 900)
        wa = urlparse(response.url)
        self.assertEqual(wa.netloc + wa.path, "wa.me/972501234567")
        text = parse_qs(wa.query)["text"][0]
        self.assertNotIn("<script", text)
        self.assertLessEqual(len(text), lp.MAX_WHATSAPP_TEXT)

    def test_a_missing_target_and_an_unknown_kind_are_404(self):
        page = self.live_page(contact={"phone": "0501234567"})
        ns = route_namespace()
        for kind in ("whatsapp", "store", "sms"):
            with self.assertRaises(HTTPException) as caught:
                ns["landing_go"]("c7", page["slug"], kind, Tasks(), "")
            self.assertEqual(caught.exception.status_code, 404)

    def test_preview_buttons_open_the_real_destination_uncounted(self):
        page = self.live_page()
        ns = route_namespace(_require_session=lambda request: 7)
        tasks_before = list(self.bumps)
        text = ns["client_landing_preview"](page["id"], Request(
            f"https://app.uallak.com/api/client/landing-pages/{page['id']}/preview"), 0).body
        self.assertIn('href="tel:0501234567"', text)
        self.assertIn('href="https://wa.me/972501234567?text=', text)
        self.assertIn('href="https://shop.example.co.il/p"', text)
        self.assertNotIn('href="#"', text)
        self.assertEqual(self.bumps, tasks_before)

    def test_fallback_template_has_no_emoji_and_absolute_links(self):
        page = self.live_page()
        ns = route_namespace()
        text = ns["serve_landing_page"]("c7", page["slug"], Request(f"https://app.uallak.com/lp/c7/{page['slug']}"),
                                        Tasks()).body
        self.assertIsNone(lc._EMOJI.search(text))
        self.assertIn(f'href="/lp/c7/{page["slug"]}/go/call"', text)
        self.assertIn('href="/lp/c7/privacy"', text)

    def test_custom_domain_links_are_on_their_host(self):
        links = route_namespace()["_lp_links"]("spring", {"phone": "1234567", "whatsapp": "972501234567"}, base="")
        self.assertEqual(links["call"], "/spring/go/call")
        self.assertEqual(links["privacy"], "/privacy")


# ─── 1. The rail: the CSP sandbox ────────────────────────────────────────────

class Sandbox(Base):
    def test_the_policy(self):
        csp = lp.page_csp(["lp.spring.co.il"])
        self.assertTrue(csp.startswith("sandbox allow-scripts allow-forms allow-popups "
                                       "allow-popups-to-escape-sandbox;"))
        self.assertNotIn("allow-same-origin", csp)
        self.assertNotIn("'self'", csp)                       # explicit hosts only
        directives = dict(d.strip().split(" ", 1) for d in csp.split(";")[1:])
        for cdn in ("https://cdnjs.cloudflare.com", "https://cdn.jsdelivr.net", "https://unpkg.com"):
            self.assertIn(cdn, directives["script-src"])
        self.assertIn("'unsafe-inline'", directives["script-src"])
        self.assertIn("https://fonts.googleapis.com", directives["style-src"])
        self.assertIn("https://fonts.gstatic.com", directives["font-src"])
        self.assertEqual(directives["form-action"], "https://app.uallak.com https://lp.spring.co.il")
        self.assertIn("https://app.uallak.com", directives["img-src"])
        self.assertIn("data:", directives["img-src"])
        self.assertEqual(directives["connect-src"], "'none'")
        self.assertNotIn("googletagmanager", csp)
        with_tags = lp.page_csp(gtm=True)
        self.assertIn("https://www.googletagmanager.com", with_tags)
        self.assertNotIn("allow-same-origin", with_tags)

    def test_live_legal_and_preview_responses_all_carry_it(self):
        page = self.live_page(content={**COPY, "html": DESIGNED})
        ns = route_namespace(_require_session=lambda request: 7)
        responses = [
            ns["serve_landing_page"]("c7", page["slug"], Request(f"https://app.uallak.com/lp/c7/{page['slug']}"),
                                     Tasks()),
            ns["serve_landing_page"]("c7", page["slug"], Request(
                f"https://app.uallak.com/lp-host/{page['slug']}", headers={lt.LP_HOST_HEADER: "lp.spring.co.il"}),
                Tasks()),
            ns["_lp_legal"]("c7", "privacy", Request("https://app.uallak.com/lp/c7/privacy")),
            ns["_lp_legal"]("c7", "terms", Request("https://app.uallak.com/lp/c7/terms")),
            ns["client_landing_preview"](page["id"], Request(
                f"https://app.uallak.com/api/client/landing-pages/{page['id']}/preview"), 0),
        ]
        for response in responses:
            csp = response.headers.get("Content-Security-Policy", "")
            self.assertTrue(csp.startswith(lp.SANDBOX), csp)
            self.assertNotIn("allow-same-origin", csp)
        self.assertIn("https://lp.spring.co.il", responses[1].headers["Content-Security-Policy"])
        self.assertEqual(responses[4].headers.get("X-Robots-Tag"), "noindex")

    def test_no_landing_html_response_without_the_headers(self):
        # Static guard: every HTMLResponse that serves a rendered landing page
        # in api_server goes through landing_pages.page_headers
        for match in re.finditer(r"HTMLResponse\(([^\n]*(?:\n[^\n]*){0,2})", API_SRC):
            call = match.group(1)
            if any(t in call for t in ("page_html", "html_text", "render_legal", "landing_pages.render")):
                self.assertIn("page_headers(", call, call)

    def test_the_exemplar_runs_under_the_policy(self):
        """design-reference/shin-sekai-gold.html against page_csp: inline
        script + style allowed, native POST form, <dialog>.showModal (no
        sandbox flag gates it), no fetch / storage / cookies / external
        script. The ONE conflict: its preview copy loads local font files
        (url(f/…)) that font-src blocks — the prompt makes the model use
        Google Fonts instead."""
        page = _src("design-reference", "shin-sekai-gold.html")
        csp = lp.page_csp()
        allowed = ("https://fonts.googleapis.com", "https://fonts.gstatic.com", "https://cdnjs.cloudflare.com",
                   "https://cdn.jsdelivr.net", "https://unpkg.com", "https://app.uallak.com")
        urls = re.findall(r'(?:src|href)="([^"{#][^"]*)"', page) + re.findall(r"url\(([^)]+)\)", page)
        urls = [u.strip("'\" ") for u in urls]
        # img/… are the preview copy's photos: in a stored page they are
        # {{IMG:key}} tokens resolving to our own media host (img-src allows it)
        blocked = sorted({u for u in urls if not u.startswith(allowed) and not u.startswith("data:")
                          and not u.startswith(("tel:", "mailto:", "img/"))})
        self.assertTrue(blocked and all(u.startswith("f/") and u.endswith(".woff2") for u in blocked), blocked)
        for forbidden in ("fetch(", "XMLHttpRequest", "localStorage", "sessionStorage", "document.cookie",
                          "window.top", "alert(", "confirm("):
            self.assertNotIn(forbidden, page)
        self.assertIn('method="post" action="{{FORM_ACTION}}"', page)
        self.assertIn("showModal()", page)
        self.assertIn("allow-forms", csp)
        self.assertIn("allow-scripts", csp)
        self.assertIn("Google Fonts", lc.CREATIVE_RULES)


# ─── The form → the lead ─────────────────────────────────────────────────────

class FormToLead(Base):
    def submit(self, page, fields, referer=""):
        from urllib.parse import urlencode
        ns = route_namespace(
            verify_lead_capture_token=lambda token: 7 if token == "T7" else None,
            deletion_guard=types.SimpleNamespace(is_deleting=lambda cid: False),
            _request_ip=lambda request: "5.5.5.5", client_leads_service=client_leads)
        request = Request("https://app.uallak.com/api/leads/capture/T7",
                          headers={"content-type": "application/x-www-form-urlencoded", "referer": referer},
                          body=urlencode(fields).encode())
        return asyncio.run(ns["public_lead_capture"]("T7", request, Tasks()))

    def form_fields(self, page):
        """The hidden fields exactly as the served designed page carries them."""
        ns = route_namespace()
        html_text = ns["serve_landing_page"]("c7", page["slug"], Request(
            f"https://app.uallak.com/lp/c7/{page['slug']}", query={"utm_source": "google", "gclid": "G-1"}),
            Tasks()).body
        form = html_text[html_text.index("<form"):html_text.index("</form>")]
        self.assertIn('action="https://app.uallak.com/api/leads/capture/', form)
        return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form))

    def test_designed_form_creates_a_lead_with_attribution_and_the_interest(self):
        page = self.live_page(content={**COPY, "html": DESIGNED})
        hidden = self.form_fields(page)
        stamp_payload = hidden["lp_stamp"]
        lt.MIN_FILL_SECONDS, saved_min = 0, lt.MIN_FILL_SECONDS
        try:
            response = self.submit(page, {**hidden, "lp_stamp": stamp_payload, "name": "דנה",
                                          "phone": "0521112222", "interest": "נעליים", "message": "למתנה",
                                          "website": ""}, referer="")   # the sandbox sends no Referer
        finally:
            lt.MIN_FILL_SECONDS = saved_min
        self.assertIsInstance(response, RedirectResponse)              # back to the page, not bare JSON
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.url, f"https://app.uallak.com/lp/c7/{page['slug']}?sent=1")
        lead = self.db.tables["client_leads"][0]
        self.assertEqual(lead["source_detail"], f"lp:{page['slug']}")
        self.assertTrue(lead["message"].startswith("מתעניין/ת ב: נעליים"))
        self.assertIn("למתנה", lead["message"])
        self.assertEqual(lead["utm_source"], "google")
        self.assertEqual(lead["click_ids"], {"gclid": "G-1"})
        self.assertNotIn("lp_stamp", json.dumps(lead, ensure_ascii=False))

    def test_redirect_only_back_to_this_clients_pages(self):
        self.assertTrue(lt.redirect_allowed(7, "https://app.uallak.com/lp/spring-shoes-7/spring?sent=1"))
        self.assertTrue(lt.redirect_allowed(7, "https://app.uallak.com/lp/c7/spring"))
        self.assertFalse(lt.redirect_allowed(7, "https://app.uallak.com/lp/c8/spring"))
        self.assertFalse(lt.redirect_allowed(7, "https://app.uallak.com/api/client/account"))
        self.assertFalse(lt.redirect_allowed(7, "http://app.uallak.com/lp/c7/spring"))
        self.assertFalse(lt.redirect_allowed(7, "https://evil.example/lp/c7/spring"))
        self.assertFalse(lt.redirect_allowed(7, "https://app.uallak.com@evil.example/lp/c7/x"))
        domains = sys.modules["core.landing_domains"]
        saved = domains.get_state
        domains.get_state = lambda cid: {"mode": "active", "hostname": "lp.spring.co.il"}
        try:
            self.assertTrue(lt.redirect_allowed(7, "https://lp.spring.co.il/spring?sent=1"))
            self.assertFalse(lt.redirect_allowed(7, "https://lp.other.co.il/spring"))
        finally:
            domains.get_state = saved

    def test_a_site_form_keeps_the_referer_rule(self):
        page = self.live_page()
        out = self.submit(page, {"name": "x", "redirect": "https://app.uallak.com/lp/c7/spring?sent=1"},
                          referer="https://somesite.co.il/contact")
        self.assertEqual(out, {"success": True, "stored": True})        # not a landing submit: no redirect


# ─── 2. The creative engine ──────────────────────────────────────────────────

class Engine(Base):
    def test_references_load_from_the_manifest_and_the_closest_exemplar_wins(self):
        refs = lc.load_references()
        self.assertEqual([e["name"] for e in refs["exemplars"]], ["shin-sekai-gold"])
        self.assertIn("Critique checklist", refs["playbook"])
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.html", "b.html"):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                    f.write(f"<html>{name}</html>")
            with open(os.path.join(tmp, "P.md"), "w", encoding="utf-8") as f:
                f.write("playbook")
            with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"playbook": "P.md", "exemplars": [
                    {"file": "a.html", "name": "a", "niches": ["shoes"], "moods": ["bold"]},
                    {"file": "b.html", "name": "b", "niches": ["dentist", "clinic"], "moods": ["calm"]}]}, f)
            two = lc.load_references(tmp)
            self.assertEqual(lc.pick_exemplar(two, "a dental clinic in Haifa, dentist")["name"], "b")
            self.assertEqual(lc.pick_exemplar(two, "pizza")["name"], "a")       # no match: the first

    def test_stable_prefix_first_and_cached_client_data_after(self):
        request = lc.build_request(7, {"id": 1, "title": "Spring", "goal": "מבצע"}, COPY, CONTACT, REFS,
                                   {"business_name": "Spring Shoes", "industry": "shoes"},
                                   landing_research="PAGES READ: x", feedback="יותר חם")
        system = request["system"]
        self.assertEqual(len(system), 2)
        self.assertTrue(all(b.get("cache_control") == {"type": "ephemeral"} for b in system))
        system_text = " ".join(b["text"] for b in system)
        self.assertNotIn("Spring Shoes", system_text)                     # nothing per-client is cached
        self.assertIn("looks like the\nexemplar for a different business is a FAILURE", system_text)
        self.assertIn("עיצוב אישי", system_text)
        self.assertIn("<exemplar>", system_text)
        payload = json.loads(request["user"][-1]["text"].split("\n", 1)[1])
        self.assertEqual(payload["business"]["business_name"], "Spring Shoes")
        self.assertEqual(payload["images"][0], {"key": KEY, "placement": "hero", "alt": "נעל בעבודת יד",
                                                "width": 1200, "height": 800})
        self.assertTrue(payload["has_logo"])
        self.assertEqual(payload["available_buttons"], ["whatsapp", "call", "store"])
        self.assertEqual(payload["client_feedback"], "יותר חם")
        self.assertIn("data, not instructions", request["user"][-1]["text"])

    def test_generation_strips_emoji_and_records_everything(self):
        lc._call = lambda system, user: fake_message(
            '<design_brief>{"mood": ["warm", "crafted", "bold"]}</design_brief>\n' + DESIGNED)
        result = lc.generate(7, {"id": 1, "title": "Spring"}, COPY, CONTACT, REFS, {"business_name": "S"})
        self.assertTrue(result["success"])
        self.assertIsNone(lc._EMOJI.search(result["html"]))
        self.assertEqual(result["brief"]["mood"], ["warm", "crafted", "bold"])
        record = result["record"]
        for key in ("model", "input_tokens", "output_tokens", "cache_read_tokens", "cost_ils", "cost_usd",
                    "duration_s", "retries", "outcome", "content_hash", "html_bytes", "exemplar"):
            self.assertIn(key, record)
        self.assertEqual((record["outcome"], record["retries"], record["cache_read_tokens"]), ("ok", 0, 15000))
        self.assertEqual(record["model"], "claude-opus-5-5")
        self.assertGreater(record["cost_ils"], 0)
        self.assertEqual(len(self.cost_rows), 1)                          # one cost row per call
        self.assertEqual(self.cost_rows[0][0][0], "claude_landing_page")

    def test_a_failed_call_is_retried_once_then_reported(self):
        calls = []

        def boom(system, user):
            calls.append(1)
            raise RuntimeError("overloaded")
        lc._call = boom
        result = lc.generate(7, {"id": 1}, COPY, CONTACT, [], {})
        self.assertFalse(result["success"])
        self.assertEqual(len(calls), 2)
        self.assertEqual((result["record"]["outcome"], result["record"]["retries"]), ("api_error", 1))

    def test_truncated_or_refused_output_is_not_a_page(self):
        lc._call = lambda s, u: fake_message(DESIGNED[:300], stop="max_tokens")
        self.assertEqual(lc.generate(7, {"id": 1}, COPY, CONTACT, [], {})["record"]["outcome"], "truncated")
        lc._call = lambda s, u: fake_message("", stop="refusal")
        self.assertEqual(lc.generate(7, {"id": 1}, COPY, CONTACT, [], {})["record"]["outcome"], "refusal")

    def test_placeholders_and_images_resolve(self):
        out = lp.render_creative({"id": 1, "slug": "spring", "client_id": 7}, lc.strip_emoji(DESIGNED),
                                 "https://app.uallak.com/api/leads/capture/T", "https://app.uallak.com/lp/c7/spring",
                                 visible={"content": {**COPY, "images": REFS}},
                                 links={"call": "/lp/c7/spring/go/call", "privacy": "/lp/c7/privacy"},
                                 business_name="Spring", form_stamp="S")
        self.assertNotIn("{{", out)
        self.assertIn(f'src="https://app.uallak.com/lp-media/7/5-{KEY}.webp"', out)
        self.assertIn('src="data:image/gif;base64', out)                  # an unknown key: a blank pixel
        self.assertIn('href="/lp/c7/spring/go/call" data-wa', out)        # no WhatsApp: falls back to call
        self.assertIn('name="lp_stamp" value="S"', out)
        self.assertIn('name="source_detail" value="lp:spring"', out)
        self.assertIn('name="website"', out)                              # the honeypot
        self.assertIn("מדיניות הפרטיות", out)
        self.assertIn("נבנה ומתארח על ידי uallak", out)

    def test_a_forgotten_token_is_repaired_not_rejected(self):
        bare = '<html><body><form id="f" action="https://other.example/x"><input name="name"></form></body></html>'
        out = lc.fill(bare, client_id=7, refs=[], values={"FORM_ACTION": "https://app/api/leads/capture/T",
                                                          "FORM_HIDDEN": "<i>H</i>", "CONSENT": "C",
                                                          "FOOTER": "F"})
        self.assertIn('action="https://app/api/leads/capture/T"', out)
        self.assertNotIn("other.example", out)
        self.assertIn('method="post"', out)
        self.assertIn("<i>H</i>", out)
        self.assertIn("C</p></form>", out)
        self.assertIn("F</footer></body>", out)

    def test_kill_switch_off_renders_the_old_template(self):
        page = {"id": 1, "slug": "spring", "client_id": 7, "title": "Spring",
                "content": {**COPY, "html": DESIGNED}}
        on = lp.render_page(page, "https://a/c", "https://a/lp/c7/spring", creative=True)
        off = lp.render_page(page, "https://a/c", "https://a/lp/c7/spring", creative=False)
        self.assertIn("data-wa", on)
        self.assertNotIn("data-wa", off)
        self.assertIn('<form class="lead"', off)
        operator_flags.enabled = lambda name: name != lc.FLAG
        self.assertIn('<form class="lead"', lp.render_page(page, "https://a/c", "https://a/lp/c7/spring"))
        self.assertTrue(operator_flags.FLAGS[lc.FLAG]["default"])         # default ON

    def test_html_is_kept_through_versions_and_revert(self):
        page = self.live_page(content={**COPY, "html": DESIGNED})
        first = page["current_version"]
        self.assertTrue(lp.update_page(7, page["id"], {"content": dict(COPY)}, source="client_edit")["success"])
        newest = lp.get_page(7, page["id"])
        self.assertNotIn("html", lp.visible_of(7, newest, newest["current_version"])["content"])
        self.assertIn("html", lp.visible_of(7, newest)["content"])        # the live one is untouched
        reverted = lp.revert(7, page["id"], first)
        self.assertTrue(reverted["success"])
        live = lp.visible_of(7, lp.get_page(7, page["id"]))
        self.assertIn("{{FORM_ACTION}}", live["content"]["html"])


# ─── The agent: designed version, fallback, records ──────────────────────────

class Agent(Base):
    def setUp(self):
        super().setUp()
        self.cards = []
        self.saved_agent = {n: getattr(lpa, n) for n in ("generate_copy", "propose_publish")}
        lpa.generate_copy = lambda client_id, goal, title="", current=None: {
            "success": True, "content": lp.normalize_content(COPY), "notes": "", "research_grounded": False}
        lpa.propose_publish = lambda cid, page_id, intro="", persona="website": (
            self.cards.append((page_id, lp.get_page(cid, page_id).get("current_version"))) or {"card": {"id": 1}})

    def tearDown(self):
        for n, fn in self.saved_agent.items():
            setattr(lpa, n, fn)
        super().tearDown()

    def generation_rows(self):
        return [a for a in ACTIVITY if a[2] == lc.ACTIVITY_TYPE]

    def test_create_designs_the_page_and_the_card_offers_the_designed_version(self):
        lc._call = lambda s, u: fake_message(DESIGNED)
        result = lpa.create_page(7, "Spring", "מבצע")
        self.assertTrue(result["designed"])
        page = lp.get_page(7, result["page"]["id"])
        versions = lp.list_versions(7, page["id"])
        self.assertEqual([v["source"] for v in versions][:2], ["designed", "generated"])
        self.assertEqual(self.cards, [(page["id"], page["current_version"])])
        self.assertIn("html", lp.visible_of(7, page, page["current_version"])["content"])
        rows = self.generation_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3]["version"], page["current_version"])
        self.assertEqual(rows[0][3]["outcome"], "ok")

    def test_failed_generation_falls_back_to_the_template_and_alerts(self):
        lc._call = lambda s, u: (_ for _ in ()).throw(RuntimeError("down"))
        result = lpa.create_page(7, "Spring", "מבצע")
        self.assertFalse(result["designed"])
        page = lp.get_page(7, result["page"]["id"])
        content = lp.visible_of(7, page, page["current_version"])["content"]
        self.assertNotIn("html", content)
        html_text = lp.render_page(page, "https://a/c", "https://a/lp/c7/x", visible={"content": content})
        self.assertIn('<form class="lead"', html_text)                    # the fixed template
        self.assertEqual(len(self.cards), 1)                              # the card still comes
        self.assertTrue(any("designed landing page" in issue for _, issues in ALERTS for issue in issues))
        self.assertEqual(self.generation_rows()[0][3]["outcome"], "api_error")

    def test_dashboard_button_designs_in_the_background(self):
        lc._call = lambda s, u: fake_message(DESIGNED)
        tasks = Tasks()
        result = lpa.create_page(7, "Spring", "מבצע", defer=tasks.add_task)
        self.assertTrue(result["designing"])
        self.assertEqual(self.cards, [])                                  # nothing until it is designed
        fn, args, _ = tasks.calls[0]
        fn(*args)
        page = lp.get_page(7, result["page"]["id"])
        self.assertEqual(self.cards, [(page["id"], page["current_version"])])
        self.assertIn("html", lp.visible_of(7, page, page["current_version"])["content"])

    def test_a_photo_only_edit_designs_again_without_the_old_html(self):
        lc._call = lambda s, u: fake_message(DESIGNED)
        page_id = lpa.create_page(7, "Spring", "מבצע")["page"]["id"]
        seen = []
        lc._call = lambda s, u: (seen.append(u), fake_message(DESIGNED.replace("דברו איתנו", "שוב")))[1]
        result = lpa.regenerate_copy(7, page_id, "יותר חם")
        self.assertTrue(result["designed"])
        self.assertIn("יותר חם", seen[0][-1]["text"])                     # the feedback reaches the designer
        live_html = lp.visible_of(7, lp.get_page(7, page_id), None)["content"]
        self.assertTrue(live_html.get("html", "").count("שוב") == 1 or "html" not in live_html)

    def test_published_log_carries_version_and_hash(self):
        lc._call = lambda s, u: fake_message(DESIGNED)
        page_id = lpa.create_page(7, "Spring", "מבצע")["page"]["id"]
        lpa._offer_tracking, saved = (lambda cid: False), lpa._offer_tracking
        try:
            out = lpa.publish_page(7, page_id, notify=False)
        finally:
            lpa._offer_tracking = saved
        self.assertTrue(out["success"])
        published = [a for a in ACTIVITY if a[2] == "landing_page_published"][0][3]
        self.assertEqual(published["version"], lp.get_page(7, page_id)["published_version"])
        self.assertTrue(re.match(r"^[0-9a-f]{16}$", published["content_hash"]))
        self.assertTrue(published["designed"])


class AdminRecords(Base):
    def test_generations_show_per_page(self):
        from core import landing_admin
        self.db.table("client_activity").insert({"client_id": 7, "action_type": lc.ACTIVITY_TYPE, "details": {
            "page_id": 3, "outcome": "api_error", "cost_ils": 0.5}, "created_at": "2026-11-07T08:00:00Z"}).execute()
        self.db.table("client_activity").insert({"client_id": 7, "action_type": lc.ACTIVITY_TYPE, "details": {
            "page_id": 3, "outcome": "ok", "cost_ils": 2.25, "model": "claude-opus-5-5", "input_tokens": 9,
            "cache_read_tokens": 30000, "output_tokens": 20000, "duration_s": 300.5, "retries": 0, "version": 4,
            "content_hash": "abc"}, "created_at": "2026-11-07T09:00:00Z"}).execute()
        saved = landing_admin._db
        landing_admin._db = lambda: self.db
        try:
            out = landing_admin.generations_by_page()
        finally:
            landing_admin._db = saved
        self.assertEqual((out[3]["count"], out[3]["ok"], out[3]["failed"], out[3]["cost_ils"]), (2, 1, 1, 2.75))
        self.assertEqual(out[3]["latest"]["outcome"], "ok")
        self.assertEqual(out[3]["latest"]["version"], 4)
        page = _src("dashboard", "admin", "landing", "index.html")
        for shown in ("cache_read_tokens", "cost_ils", "duration_s", "retries", "outcome", "content_hash"):
            self.assertIn(shown, page)


if __name__ == "__main__":
    unittest.main()
