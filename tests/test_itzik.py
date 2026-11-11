"""איציק — the operator's persona and floating chat (2026-11-01, docs/OPERATOR.md).

A. Admin-only: only admin pages load dashboard/assets/itzik-chat.js, and it
   draws nothing until the admin-session presence call answers; no client
   surface mentions it.
B. The name: איציק in the menu, page title, chat header, approval-code email
   (subject + body), audit-log display, PR titles; "המפעיל" is gone from
   everything the owner reads. Internal identifiers and routes stay.
C. The badge: new alerts (audit sources never count), pending approvals and
   post-deploy problems; nothing → no badge.
D. The proactive rollback: offered only when the new deploy looks wrong, at
   most once per session per signature (a new deploy = a new signature).
E. Security unchanged: Tier 3 is still refused without the emailed code,
   before any GitHub write; the chat's approval card needs gate_id + code.
F. Persona in the shared prompt; model = the locked top tier from the
   central config.
G. Assets resolve: both faces in both formats, the right sizes, and every
   path the chat and the workspace use exists.

Stdlib only. Run from tests/:  python -I -B test_itzik.py -v
"""
import os
import re
import struct
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from core import email_service as real_email     # noqa: E402  (before test_operator stubs it)
import test_operator as T                         # noqa: E402  (its in-memory DB, fakes and env)
from core import model_routing, operator_actions, operator_agent, operator_changes, operator_presence  # noqa: E402

NAME = "איציק"
JS = os.path.join("dashboard", "assets", "itzik-chat.js")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _js_function(src: str, name: str) -> str:
    start = re.search(r"(async )?function " + name + r"\(", src).start()
    depth, i = 0, src.index("{", start)
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1


def _pages():
    for base, dirs, files in os.walk(os.path.join(ROOT, "dashboard")):
        for fn in files:
            if fn.endswith(".html"):
                rel = os.path.relpath(os.path.join(base, fn), ROOT)
                yield rel.replace(os.sep, "/"), _src(rel)


# ─── A. Admin-only ───────────────────────────────────────────────────────────

class AdminOnly(unittest.TestCase):
    def test_only_admin_pages_load_the_chat(self):
        loaders = [rel for rel, html in _pages() if "itzik-chat.js" in html]
        self.assertEqual(sorted(loaders), ["dashboard/admin/connections/index.html",
                                           "dashboard/admin/drive/index.html",
                                           "dashboard/admin/index.html", "dashboard/admin/landing/index.html",
                                           "dashboard/admin/models/index.html",
                                           "dashboard/admin/operator/index.html"])

    def test_every_admin_page_has_it(self):
        admin = [rel for rel, _ in _pages() if rel.startswith("dashboard/admin/")]
        for rel, html in _pages():
            if rel in admin:
                self.assertIn('src="/assets/itzik-chat.js"', html, rel)

    def test_no_client_surface_mentions_him(self):
        for rel, html in _pages():
            if rel.startswith("dashboard/admin/"):
                continue
            for word in ("itzik", NAME, "/api/admin/operator"):
                self.assertNotIn(word, html, f"{rel}: {word}")
        for js in os.listdir(os.path.join(ROOT, "dashboard", "assets")):
            if js.endswith(".js") and js != "itzik-chat.js":
                self.assertNotIn("itzik", _src("dashboard", "assets", js).lower(), js)

    def test_draws_nothing_without_the_admin_session(self):
        js = _src(JS)
        mount = _js_function(js, "mount")
        # The presence call proves the session BEFORE anything is built; any failure → nothing
        self.assertLess(mount.index("call('/presence"), mount.index("build()"))
        self.assertIn("catch (e) { return false; }", mount)
        self.assertEqual(len(re.findall(r"(?<!function )build\(\)", js)), 1)   # mount is the only builder
        self.assertIn("e.auth = true; throw e;", _js_function(js, "call"))

    def test_presence_route_is_admin_only(self):
        api = _src("core", "api_server.py")
        route = api[api.index('@app.get("/api/admin/operator/presence")'):][:400]
        self.assertIn("_operator_admin(request)", route)


# ─── B. The name ─────────────────────────────────────────────────────────────

class Name(unittest.TestCase):
    def test_one_source(self):
        self.assertEqual(operator_agent.DISPLAY_NAME, NAME)

    def test_menu_page_and_header(self):
        self.assertRegex(_src("dashboard", "admin", "index.html"), r'href="/admin/operator/"[^\n]*איציק</div></a>')
        page = _src("dashboard", "admin", "operator", "index.html")
        self.assertIn("<title>uallak — איציק</title>", page)
        self.assertIn("allak</span> · איציק", page)
        js = _src(JS)
        self.assertIn("const NAME = 'איציק';", js)
        self.assertIn("el('span', 'itz-title', NAME)", js)          # the open chat's header
        self.assertIn("el('span', 'itz-name', NAME)", js)           # under the standing figure

    def test_approval_code_email(self):
        sent = []
        saved = real_email._send
        real_email._send = lambda msg, to: sent.append(msg) or True
        try:
            self.assertTrue(real_email.send_operator_code("owner@example.com", "123456", "פתיחת PR", 10))
        finally:
            real_email._send = saved
        msg = sent[0]
        from email.header import decode_header
        subject = "".join(p.decode(c or "utf-8") if isinstance(p, bytes) else p for p, c in decode_header(msg["Subject"]))
        self.assertIn(NAME, subject)
        body = msg.get_payload()[0].get_payload(decode=True).decode("utf-8")
        self.assertIn("קוד אישור מאיציק", body)
        self.assertNotIn("המפעיל", body)

    def test_audit_display_name(self):
        T.DB.tables.clear(); T.DB.fail.clear()
        operator_actions.audit("health_check", "operator agent for admin@1.2.3.4")
        entry = operator_actions.recent_audit()[0]["entry"]
        self.assertTrue(entry.startswith("health_check by איציק for admin"), entry)
        # the stored row keeps the internal actor name
        self.assertIn("operator agent", T.DB.tables["alerts"][0]["issues"][0])

    def test_presence_and_pr_titles(self):
        self.assertEqual(operator_presence.snapshot.__module__, "core.operator_presence")
        self.assertIn('f"איציק: {change[\'title\']}"', _src("core", "operator_changes.py"))
        self.assertNotIn('"Operator: ', _src("core", "operator_changes.py"))

    def test_old_name_gone_from_what_the_owner_reads(self):
        for parts in (("dashboard", "admin", "index.html"), ("dashboard", "admin", "operator", "index.html"),
                      ("dashboard", "assets", "itzik-chat.js"), ("core", "email_service.py"),
                      ("core", "operator_agent.py"), ("core", "operator_github.py"), ("core", "operator_presence.py"),
                      ("core", "operator_actions.py"), ("core", "operator_changes.py")):
            self.assertNotIn("המפעיל", _src(*parts), "/".join(parts))

    def test_internal_routes_unchanged(self):
        self.assertIn("const API = '/api/admin/operator';", _src(JS))
        self.assertIn("const WORKSPACE = '/admin/operator/';", _src(JS))
        self.assertEqual(operator_actions.AUDIT_SOURCE, "operator")


# ─── C. The badge ────────────────────────────────────────────────────────────

class Badge(unittest.TestCase):
    def test_nothing_needs_him(self):
        out = operator_presence.summarize({"recent_deploy": False}, [{"status": "deployed"}], 0)
        self.assertEqual((out["badge"], out["items"], out["proactive"]), (0, [], None))
        self.assertEqual(operator_presence.summarize(None, None, None)["badge"], 0)

    def test_new_alerts(self):
        out = operator_presence.summarize(None, [], 3)
        self.assertEqual(out["badge"], 3)
        self.assertEqual(out["items"][0]["text_he"], "3 התראות חדשות")
        self.assertEqual(operator_presence.summarize(None, [], 1)["items"][0]["text_he"], "התראה חדשה אחת")

    def test_pending_approvals(self):
        out = operator_presence.summarize(None, [{"status": "proposed"}, {"status": "pr_open"}, {"status": "merged"}], 0)
        self.assertEqual(out["badge"], 2)
        self.assertEqual(out["items"][0]["kind"], "approval")

    def test_errors_after_a_recent_deploy(self):
        out = operator_presence.summarize({"recent_deploy": True, "errors": 2, "suspicious": False}, [], 0)
        self.assertEqual(out["badge"], 1)
        self.assertEqual(out["items"][0]["kind"], "deploy_errors")
        self.assertIsNone(out["proactive"])                         # below the threshold: watched, not pushed
        old = operator_presence.summarize({"recent_deploy": False, "errors": 9}, [], 0)
        self.assertEqual(old["badge"], 0)

    def test_failed_change_deploy(self):
        out = operator_presence.summarize(None, [{"id": "ab12", "status": "deploy_failed"}], 0)
        self.assertEqual((out["badge"], out["items"][0]["kind"]), (1, "deploy_failed"))

    def test_audit_rows_never_count_as_alerts(self):
        class Q:
            def __init__(self, rows): self.rows = rows
            def __getattr__(self, _name): return lambda *a, **k: self
            def execute(self): return type("R", (), {"data": self.rows})()
        rows = [{"id": 1, "source": "monitor_agent"}, {"id": 2, "source": "operator"},
                {"id": 3, "source": "admin_view"}, {"id": 4, "source": "bulk_delete"}]
        saved = operator_presence._db
        operator_presence._db = lambda: type("D", (), {"table": lambda self, n: Q(rows)})()
        try:
            self.assertEqual(operator_presence._new_alerts("2026-11-01T00:00:00+00:00"), 1)
        finally:
            operator_presence._db = saved

    def test_since_is_clamped(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        week = datetime.fromisoformat(operator_presence._since("2000-01-01T00:00:00Z"))
        self.assertLess(abs((now - week) - timedelta(days=7)), timedelta(minutes=1))
        default = datetime.fromisoformat(operator_presence._since("not a date"))
        self.assertLess(abs((now - default) - timedelta(hours=24)), timedelta(minutes=1))
        future = datetime.fromisoformat(operator_presence._since((now + timedelta(days=3)).isoformat()))
        self.assertLessEqual(future, datetime.now(timezone.utc))

    def test_chat_draws_the_badge_from_presence(self):
        js = _src(JS)
        self.assertIn("const n = (presence && presence.badge) || 0; return n > 0 ? (n > 9 ? '9+' : String(n)) : '';",
                      _js_function(js, "badgeText"))
        render = _js_function(js, "renderPresence")
        self.assertIn("parts.badge.hidden = !text;", render)
        # Opening the chat marks the alerts as seen; pending approvals and deploy problems stay
        self.assertIn("setStore('localStorage', SEEN_KEY, new Date().toISOString());", _js_function(js, "open"))
        self.assertIn("'/presence?since=' + encodeURIComponent(since)", _js_function(js, "refresh"))


# ─── D. The proactive rollback ───────────────────────────────────────────────

class Proactive(unittest.TestCase):
    BAD = {"recent_deploy": True, "errors": 12, "suspicious": True, "revision": "super-system-00042",
           "rollback_to": "super-system-00041"}

    def test_offered_when_the_new_deploy_looks_wrong(self):
        out = operator_presence.summarize(self.BAD, [], 0)
        p = out["proactive"]
        self.assertEqual((p["kind"], p["revision"]), ("rollback", "super-system-00041"))
        self.assertIn("אני ממליץ לחזור לגרסה הקודמת", p["text_he"])
        self.assertIn("בדקתי", p["text_he"])
        self.assertGreaterEqual(out["badge"], 1)

    def test_not_offered_without_a_target_or_a_problem(self):
        self.assertIsNone(operator_presence.summarize({**self.BAD, "rollback_to": ""}, [], 0)["proactive"])
        self.assertIsNone(operator_presence.summarize({**self.BAD, "suspicious": False}, [], 0)["proactive"])

    def test_new_deploy_new_signature(self):
        a = operator_presence.summarize(self.BAD, [], 0)["proactive"]["signature"]
        b = operator_presence.summarize({**self.BAD, "errors": 30}, [], 5)["proactive"]["signature"]
        c = operator_presence.summarize({**self.BAD, "revision": "super-system-00043",
                                         "rollback_to": "super-system-00042"}, [], 0)["proactive"]["signature"]
        self.assertEqual(a, b)              # more errors on the same deploy: not new, no second line
        self.assertNotEqual(a, c)           # another deploy: new

    def test_once_per_session_per_signature(self):
        js = _src(JS)
        self.assertIn("return p && p.signature && p.signature !== shownSignature ? p : null;",
                      _js_function(js, "proactiveToShow"))
        render = _js_function(js, "renderPresence")
        self.assertIn("proactiveToShow(presence, getStore('sessionStorage', PROACTIVE_KEY))", render)
        # remembered BEFORE it is drawn, so a second poll can't draw it twice
        self.assertLess(render.index("setStore('sessionStorage', PROACTIVE_KEY, p.signature)"),
                        render.index("addMessage("))
        self.assertIn("action: 'rollback', params: { revision: p.revision }", render)
        # the one tap goes to the same audited Tier 2 route as the workspace button
        self.assertIn("post('/actions/' + name, { params: params || {} })", _js_function(js, "runAction"))


# ─── E. Security unchanged ───────────────────────────────────────────────────

class Tier3StillGated(unittest.TestCase):
    def setUp(self):
        T.DB.tables.clear(); T.DB.fail.clear(); T.SENT_CODES.clear()
        T._env(self, T.ENV_ON)
        self.gh = T.FakeGitHub(); self.gh.install(self)
        self.change = operator_changes.propose("Greeting", "מחליף ברכה", "בדיקה", T.EDIT)

    def test_no_code_no_pr(self):
        for gate_id, code in (("", ""), ("made-up", "123456")):
            with self.assertRaises(Exception):
                operator_changes.open_pr(self.change["id"], gate_id, code, "fp-1", "admin")
        self.assertEqual(self.gh.writes, [])

    def test_chat_card_sends_the_code_and_nothing_else(self):
        js = _src(JS)
        change = _js_function(js, "renderChange")
        self.assertIn("post('/changes/' + ch.id + '/request-code', { step: step[0] })", change)
        self.assertIn("post('/changes/' + ch.id + step[2], { gate_id: g.gate_id, code: inp.value.trim() })", change)
        self.assertEqual(js.count("/open-pr"), 1)
        self.assertEqual(js.count("/merge-deploy"), 1)

    def test_gate_still_checked_in_the_protected_module(self):
        src = _src("core", "operator_changes.py")
        self.assertIn("operator_gate.verify(gate_id, code, purpose, gate_subject(change, purpose), admin_fp)", src)


# ─── F. Persona + model ──────────────────────────────────────────────────────

class Persona(unittest.TestCase):
    def test_persona_in_the_shared_prompt(self):
        p = " ".join(operator_agent.SYSTEM.split())
        for line in ("You are Itzik (איציק)", "a man in his thirties with a hi-tech background",
                     "Calm, sharp, concise", "dry, understated sense of humour", '"בדקתי"', '"מצאתי"', '"אני ממליץ"',
                     "what I found, the evidence, what I recommend", "The owner is the team.",
                     '"passing it to the team"', "exact numbered steps", "plain Hebrew, short, no jargon"):
            self.assertIn(line, p)
        # one prompt for any model: nothing model-specific in it
        self.assertNotRegex(p, r"claude-|opus|sonnet|haiku")

    def test_locked_top_model_from_the_central_config(self):
        self.assertEqual(model_routing.ROLE_OVERRIDES["operator"], "heavy")
        self.assertEqual(operator_agent._model(), model_routing.TIER_MODELS["heavy"])
        self.assertNotRegex(_src("core", "operator_agent.py"), r"[\"']claude-(haiku|sonnet|opus)")


# ─── G. Assets resolve ───────────────────────────────────────────────────────

AGENTS = os.path.join(ROOT, "dashboard", "assets", "agents")


def _png_size(path):
    with open(path, "rb") as f:
        head = f.read(26)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    w, h, depth, ctype = struct.unpack(">IIBB", head[16:26])
    return w, h, ctype


class Assets(unittest.TestCase):
    def test_both_faces_both_formats(self):
        for kind in ("portrait", "figure"):
            for ext in ("png", "webp"):
                path = os.path.join(AGENTS, f"itzik-{kind}.{ext}")
                self.assertTrue(os.path.isfile(path), path)
        self.assertEqual(_png_size(os.path.join(AGENTS, "itzik-figure.png")), (752, 1344, 6))   # RGBA: background gone
        self.assertEqual(_png_size(os.path.join(AGENTS, "itzik-portrait.png"))[:2], (1536, 1536))

    def test_every_referenced_path_exists(self):
        refs = set(re.findall(r"/assets/agents/(itzik-[a-z]+\.(?:png|webp))", _src("dashboard", "admin", "operator", "index.html")))
        js = _src(JS)
        self.assertIn("const FACE = '/assets/agents/itzik-';", js)
        for kind in re.findall(r"picture\('([a-z]+)'", js):
            refs.update({f"itzik-{kind}.png", f"itzik-{kind}.webp"})
        self.assertEqual(refs, {"itzik-portrait.png", "itzik-portrait.webp", "itzik-figure.png", "itzik-figure.webp"})
        for ref in refs:
            self.assertTrue(os.path.isfile(os.path.join(AGENTS, ref)), ref)

    def test_originals_gone_from_the_root(self):
        for name in ("itzik-portrait.png", "itzik-standing.png"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, name)), name)

    def test_broken_image_falls_back(self):
        pic = _js_function(_src(JS), "picture")
        self.assertIn("src.remove(); img.src = FACE + kind + '.png'", pic)
        self.assertIn("holder.classList.add('failed')", pic)

    def test_minimise_is_css_only(self):
        js = _src(JS)
        for fn in ("open", "minimize"):
            body = _js_function(js, fn)
            self.assertNotRegex(body, r"(?<!classList)\.(appendChild|insertBefore|replaceWith|remove)\(", fn)
        self.assertIn(".itz-window.minimized > :not(.itz-figure) { display:none !important; }", js)


if __name__ == "__main__":
    unittest.main()
