"""The team identity (2026-10-31): faces, the client's choice of manager, the
flag language switcher, and Make removed.

A. The 7 faces are installed as dashboard/assets/agents/<name>-portrait.* and
   <name>-figure.* — PNG plus WebP (portraits 512x512, figures 900px tall with
   their transparency kept) — and the source folders + lang-flags.html are gone
   with nothing pointing at them.
B. Every agent icon is a portrait (WebP, PNG fallback, then the initial); a
   minimised chat window shows the agent's standing figure, built with the
   window and toggled by CSS only — nothing on the minimise/restore path moves
   a DOM node (the CLAUDE.md trap).
C. core/manager_identity is the ONE source of the concierge's name and face
   (ג'וני / ניסים / אלינור), with a default for clients who never chose; the
   chat prompt, the login greeting, emails and WhatsApp all read it, and the
   dashboard keeps no copy of the names.
D. The language switcher: SVG flag buttons (never emoji — Windows has no flag
   glyphs, the cause of the old switcher's letters), aria-label + title on
   each, the active one ringed, a failing page listener can't stop the rest.
E. Make is gone from every client-facing surface; ManyChat is untouched; a
   client's stored Make row is left intact (ownership/disconnect still see it).

Stdlib only. Run from the repo root:  python -I -B tests/test_team_identity.py -v
"""
import os
import re
import struct
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

AGENTS_DIR = os.path.join(ROOT, "dashboard", "assets", "agents")
NAMES = ("johnny", "nissim", "elinor", "lior", "yoav", "maya", "uri")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _js_function(html: str, name: str) -> str:
    start = html.index(f"function {name}(")
    depth, i = 0, html.index("{", start)
    while True:
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
        i += 1


def _webp_info(path):
    """(width, height, has_alpha) from a WebP header (VP8 / VP8L / VP8X)."""
    with open(path, "rb") as f:
        data = f.read(64)
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", path
    kind = data[12:16]
    if kind == b"VP8X":
        flags = data[20]
        w = 1 + int.from_bytes(data[24:27], "little")
        h = 1 + int.from_bytes(data[27:30], "little")
        return w, h, bool(flags & 0x10)
    if kind == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, bool((bits >> 28) & 1)
    if kind == b"VP8 ":
        w, h = struct.unpack("<HH", data[26:30])
        return w & 0x3FFF, h & 0x3FFF, False
    raise AssertionError(f"unknown WebP chunk {kind!r} in {path}")


_SAVED = {n: sys.modules.get(n) for n in ("core.agent_base", "agents.client_agent", "core.automation_service",
                                          "core.feature_catalog", "core.manager_identity")}
sys.modules["core.agent_base"] = _module("core.agent_base", log_step=lambda *a, **k: None,
                                         agent_alert=lambda *a, **k: None, timed_step=lambda a, s, fn: fn())
CLIENTS = {}
sys.modules["agents.client_agent"] = _module("agents.client_agent", get_client=lambda cid: CLIENTS.get(cid, {}),
                                             get_accounts=lambda cid: [], _db=lambda: None)
for _name in ("core.automation_service", "core.feature_catalog", "core.manager_identity"):
    sys.modules.pop(_name, None)

from core import automation_service          # noqa: E402
from core import connection_help             # noqa: E402
from core import feature_catalog             # noqa: E402
from core import manager_identity as mi      # noqa: E402
from core import ownership                   # noqa: E402


def tearDownModule():
    for name, module in _SAVED.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


# ─── A. the images ───────────────────────────────────────────────────────────

class Assets(unittest.TestCase):
    def test_every_face_in_both_formats(self):
        for name in NAMES:
            for kind in ("portrait", "figure"):
                for ext in ("png", "webp"):
                    path = os.path.join(AGENTS_DIR, f"{name}-{kind}.{ext}")
                    self.assertTrue(os.path.isfile(path), path)
                    self.assertGreater(os.path.getsize(path), 1000, path)

    def test_png_files_are_png(self):
        for name in NAMES:
            for kind in ("portrait", "figure"):
                with open(os.path.join(AGENTS_DIR, f"{name}-{kind}.png"), "rb") as f:
                    self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n", f"{name}-{kind}.png")

    def test_webp_sizes_and_transparency(self):
        for name in NAMES:
            w, h, _ = _webp_info(os.path.join(AGENTS_DIR, f"{name}-portrait.webp"))
            self.assertEqual((w, h), (512, 512), name)
            w, h, alpha = _webp_info(os.path.join(AGENTS_DIR, f"{name}-figure.webp"))
            self.assertEqual(h, 900, name)
            self.assertLess(w, h, name)
            self.assertTrue(alpha, f"{name}-figure.webp lost its transparency")

    def test_webp_is_the_light_one(self):
        for name in NAMES:
            for kind in ("portrait", "figure"):
                webp = os.path.getsize(os.path.join(AGENTS_DIR, f"{name}-{kind}.webp"))
                self.assertLess(webp, 150_000, f"{name}-{kind}.webp")

    def test_sources_removed_and_unreferenced(self):
        for gone in ("profiles photos", "uallak avatars", "lang-flags.html"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, gone)), gone)
        pattern = re.compile(r"profiles photos|uallak avatars|lang-flags\.html")
        hits = []
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
            for fn in files:
                if not fn.endswith((".py", ".html", ".js", ".css", ".md", ".txt", ".sql", ".json")):
                    continue
                path = os.path.join(base, fn)
                if os.path.abspath(path) == os.path.abspath(__file__):
                    continue
                with open(path, encoding="utf-8", errors="ignore") as f:
                    if pattern.search(f.read()):
                        hits.append(os.path.relpath(path, ROOT))
        self.assertEqual(hits, [])


# ─── B. faces in the dashboard, the standing figure ──────────────────────────

DOM_MOVES = re.compile(r"(?<!classList)\.(appendChild|insertBefore|prepend|append|replaceChild|replaceWith|before|after|remove)\(")


class Faces(unittest.TestCase):
    def setUp(self):
        self.html = _src("dashboard", "client", "index.html")

    def test_no_drawn_icons_left(self):
        for gone in ("PERSONA_SVGS", "p.svg", "dock-pill", "id=\"chatDock\"", "renderDock", "choosePersona("):
            self.assertNotIn(gone, self.html)

    def test_specialists_use_their_portraits(self):
        for persona, face in mi.SPECIALIST_FACES.items():
            self.assertRegex(self.html, rf"{persona}: \{{ name: '[^']+', roleKey: 'team_role_{persona}', face: '{face}' \}}")
        self.assertEqual(mi.SPECIALIST_FACES, {"google": "yoav", "meta": "maya", "website": "uri", "media": "lior"})

    def test_webp_first_png_fallback_then_initial(self):
        pic = _js_function(self.html, "agentPictureHtml")
        self.assertIn('<source srcset="${base}.webp" type="image/webp">', pic)
        self.assertIn('<img src="${base}.png"', pic)
        self.assertIn('onerror="agentFaceFailed(this)"', pic)
        failed = _js_function(self.html, "agentFaceFailed")
        self.assertIn("source.remove()", failed)
        self.assertIn("img.src = img.dataset.png", failed)
        self.assertIn("holder.classList.add('failed')", failed)
        self.assertIn(".agent-face.failed::after { content: attr(data-initial)", self.html)

    def test_every_place_draws_a_face(self):
        self.assertIn('<span class="launcher-ava">${faceHtml(p.face, p.initial)}</span>', _js_function(self.html, "renderLauncher"))
        self.assertIn('<span class="cw-ava" data-role="ava">${faceHtml(p.face, p.initial)}</span>', _js_function(self.html, "buildWindow"))
        self.assertIn("faceHtml(meta.face, meta.initial)", _js_function(self.html, "appendActionCard"))
        self.assertIn("fab.innerHTML = faceHtml(", _js_function(self.html, "applyManager"))

    def test_figure_is_built_with_the_window(self):
        build = _js_function(self.html, "buildWindow")
        self.assertIn("fig.className = 'cw-figure'", build)
        self.assertIn("figureHtml(p.face, p.initial)", build)
        self.assertIn('data-anim="idle"', build)          # the animation hook
        self.assertIn("root.appendChild(fig)", build)
        self.assertLess(build.index("root.appendChild(fig)"), build.index("root.addEventListener('mousedown'"))
        self.assertIn("[data-act=\"restore\"]').onclick", build)
        self.assertIn("[data-act=\"figclose\"]').onclick", build)

    def test_minimise_and_restore_never_move_a_node(self):
        for name in ("minimizeWindow", "restoreWindow", "refreshWindowIdentities", "focusWindow"):
            body = _js_function(self.html, name)
            self.assertIsNone(DOM_MOVES.search(body), f"{name} adds/moves/removes DOM nodes")
        self.assertIn("w.root.classList.add('minimized')", _js_function(self.html, "minimizeWindow"))
        self.assertIn("w.root.classList.remove('minimized')", _js_function(self.html, "restoreWindow"))

    def test_minimised_state_is_css_only(self):
        self.assertIn(".chat-window.minimized > :not(.cw-figure) { display: none !important; }", self.html)
        self.assertIn(".chat-window.minimized .cw-figure { display: flex;", self.html)
        self.assertNotIn(".chat-window.minimized { display: none; }", self.html)

    def test_phone_layout_keeps_figures_out_from_behind_the_window(self):
        self.assertIn(".chat-window.minimized { position: relative; left: auto; right: auto; bottom: auto;", self.html)
        self.assertIn(".cw-figure-stage { height: 68px; }", self.html)
        self.assertIn(".conn-grid { grid-template-columns: minmax(0, 1fr); }", self.html)


# ─── C. the client's manager ─────────────────────────────────────────────────

class Manager(unittest.TestCase):
    def test_three_managers_in_five_languages(self):
        self.assertEqual(mi.ORDER, ("johnny", "nissim", "elinor"))
        self.assertEqual(mi.MANAGERS["johnny"]["name"]["he"], "ג'וני")
        self.assertEqual(mi.MANAGERS["nissim"]["name"]["he"], "ניסים")
        self.assertEqual(mi.MANAGERS["elinor"]["name"]["he"], "אלינור")
        for key in mi.ORDER:
            self.assertEqual(set(mi.MANAGERS[key]["name"]), set(mi.LANGS))

    def test_choice_wins_and_default_is_sensible(self):
        self.assertEqual(mi.key_for({"manager_persona": "nissim", "owner_gender": "female"}), "nissim")
        self.assertEqual(mi.key_for({}), "johnny")
        self.assertEqual(mi.key_for(None), "johnny")
        self.assertEqual(mi.key_for({"owner_gender": "female"}), "elinor")   # had picked the female companion
        self.assertEqual(mi.key_for({"owner_gender": "male"}), "johnny")
        self.assertEqual(mi.key_for({"manager_persona": "dana"}), "johnny")  # never an unknown value
        self.assertFalse(mi.for_client({})["chosen"])
        self.assertTrue(mi.for_client({"manager_persona": "elinor"})["chosen"])

    def test_view_carries_names_and_existing_faces(self):
        v = mi.view("elinor", "en")
        self.assertEqual(v["name"], "Elinor")
        self.assertEqual(v["gender"], "female")
        for field in ("portrait", "portrait_png", "figure", "figure_png"):
            path = os.path.join(ROOT, "dashboard", v[field].lstrip("/"))
            self.assertTrue(os.path.isfile(path), path)

    def test_for_client_id_never_raises(self):
        CLIENTS[5] = {"id": 5, "manager_persona": "nissim"}
        self.assertEqual(mi.for_client_id(5)["key"], "nissim")
        self.assertEqual(mi.for_client_id(999)["key"], "johnny")
        broken = sys.modules["agents.client_agent"].get_client
        sys.modules["agents.client_agent"].get_client = lambda cid: 1 / 0
        try:
            self.assertEqual(mi.for_client_id(5)["key"], "johnny")
        finally:
            sys.modules["agents.client_agent"].get_client = broken

    def test_prompt_line_changes_the_name_only(self):
        line = mi.prompt_line(mi.view("elinor"))
        self.assertIn("Elinor", line)
        self.assertIn("אלינור", line)
        self.assertIn("female", line)
        self.assertIn("changes NOTHING else", line)
        self.assertNotIn("{", line)                      # safe inside .format()-ed prompts
        self.assertIn(mi.MANAGER_VOICE, line)            # the one shared, written voice
        for key in mi.ORDER:
            self.assertTrue(mi.prompt_line(mi.view(key)).endswith("\n\n" + mi.MANAGER_VOICE), key)

    def test_signoff(self):
        self.assertEqual(mi.signoff(mi.view("elinor"), "he"), "אלינור — מנהלת הלקוח שלך ב-uallak")
        self.assertEqual(mi.signoff(mi.view("nissim", "en"), "en"), "Nissim — your account manager at uallak")

    def test_save_rejects_an_unknown_key(self):
        self.assertEqual(mi.save_choice(5, "dana"), {"success": False, "code": "ERR_MANAGER_UNKNOWN"})

    def test_everything_reads_the_one_source(self):
        support = _src("agents", "support_agent.py")
        self.assertIn("manager_line = manager_identity.prompt_line(manager_identity.for_client(client))", support)
        self.assertIn("safe_claude_json_call(SYSTEM + manager_line", support)
        self.assertIn("claude_web_search_call(SEARCH_SYSTEM + manager_line", support)
        self.assertNotIn('You are presented as "the uallak', support)
        engagement = _src("agents", "engagement_agent.py")
        self.assertIn("LOGIN_MOMENT_SYSTEM = \"\"\"You are {manager_name}", engagement)
        self.assertIn("manager_name=manager_name", engagement)
        self.assertIn('if persona_id == "general":', engagement)
        self.assertIn("manager_identity.signoff(", engagement)               # WhatsApp
        interview = _src("agents", "interview_agent.py")
        self.assertEqual(interview.count("manager_identity.prompt_line(manager_identity.for_client(client))"), 2)
        email = _src("core", "email_service.py")
        self.assertEqual(email.count("msg['From'] = _from(manager)"), 3)
        self.assertEqual(email.count("{_signoff_html(manager, lang)}"), 3)
        api = _src("core", "api_server.py")
        self.assertIn('"manager": manager_identity.for_client(client', api)
        self.assertIn('"manager_options": manager_identity.options(', api)
        self.assertIn("manager_persona: str | None = None", api)
        self.assertIn('manager_identity.change(client_id, req.manager_persona, by="client")', api)

    def test_dashboard_keeps_no_copy_of_the_names(self):
        html = _src("dashboard", "client", "index.html")
        # Comments may mention the agency owner (Johnny); code and text may not name a manager
        code = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
        code = re.sub(r"(?m)^\s*//.*$", "", code)
        for key in mi.ORDER:
            for nm in mi.MANAGERS[key]["name"].values():
                self.assertNotIn(nm, code, nm)
        self.assertIn("initManager(d.manager, d.manager_options)", html)
        self.assertIn("body: JSON.stringify({ manager: key })", _js_function(html, "chooseManager"))
        self.assertNotIn("persona_male", html)

    def test_migration(self):
        sql = _src("migrations", "2026-10-31-manager-persona.sql")
        self.assertIn("add column if not exists manager_persona text", sql)
        self.assertIn("('johnny', 'nissim', 'elinor')", sql)


# ─── D. the language switcher ────────────────────────────────────────────────

class Flags(unittest.TestCase):
    def setUp(self):
        self.js = _src("dashboard", "assets", "i18n.js")

    def test_no_emoji_flags(self):
        # Regional-indicator letters: Windows draws them as "IL", "US", ...
        self.assertIsNone(re.search("[\U0001F1E6-\U0001F1FF]", self.js))
        self.assertNotIn("switcherFace", self.js)

    def test_five_svg_flags_uae_for_arabic(self):
        body = self.js[self.js.index("var FLAG_BODY"):self.js.index("var flagSeq")]
        for lang in ("he", "en", "fr", "ar", "ru"):
            self.assertIn(f"    {lang}: '<", body)
        for colour in ("#009A3E", "#CE1126", "#000"):         # UAE: green, white, black, red hoist
            self.assertIn(colour, body.split("ar:")[1].split("ru:")[0])

    def test_each_button_is_labelled_and_the_active_one_ringed(self):
        make = _js_function(self.js, "makeFlagButton")
        self.assertIn("btn.setAttribute('aria-label', NATIVE_NAMES[code])", make)
        self.assertIn("btn.setAttribute('title', NATIVE_NAMES[code])", make)
        self.assertIn("btn.innerHTML = flagSvg(code)", make)
        mount = _js_function(self.js, "mountSwitcher")
        self.assertIn("setLanguage(code)", mount)
        paint = _js_function(self.js, "paintActive")
        self.assertIn("aria-current", paint)
        self.assertIn("current.setAttribute('aria-label'", paint)        # the one visible button
        self.assertIn("current.setAttribute('title'", paint)
        self.assertIn('.lang-btn[aria-current="true"]{box-shadow:0 0 0 2px var(--bg,#0D0D0D),0 0 0 4px var(--accent,#FF4C1F);', self.js)
        self.assertIn("filter:drop-shadow(0 3px 5px rgba(0,0,0,.55))", self.js)   # depth kept

    def test_each_flag_has_its_own_defs(self):
        svg = _js_function(self.js, "flagSvg")
        self.assertIn("'uallak-flag-' + (++flagSeq) + '-'", svg)
        self.assertIn("clip-path=\"url(#' + p + 'c)\"", svg)

    def test_one_listener_failing_does_not_stop_the_rest(self):
        body = _js_function(self.js, "setLanguage")
        self.assertIn("try { fn(lang); } catch (e)", body)

    def test_phone_sizing(self):
        self.assertIn("@media (max-width:640px){.uallak-lang-pop{gap:8px;padding:7px 8px;}.lang-btn{width:28px;height:28px;}}", self.js)


class OneFlag(unittest.TestCase):
    """Part B (2026-11-01): ONE round flag showing the current language; it
    opens the other four, a pick switches and collapses, outside click and
    Escape close it, keyboard works, and it can never cause a horizontal scroll."""

    def setUp(self):
        self.js = _src("dashboard", "assets", "i18n.js")
        self.mount = _js_function(self.js, "mountSwitcher")

    def test_one_visible_button_is_a_disclosure(self):
        self.assertEqual(self.mount.count("makeFlagButton(lang)"), 1)
        self.assertIn("current.setAttribute('aria-haspopup', 'true')", self.mount)
        self.assertIn("current.setAttribute('aria-expanded', 'false')", self.mount)
        self.assertIn("current.setAttribute('aria-controls', popId)", self.mount)
        self.assertIn("'.uallak-lang-pop{position:fixed;z-index:950;display:none;", self.js)
        self.assertIn(".uallak-lang-pop.open{display:flex;}", self.js)

    def test_the_popup_holds_the_others_never_the_current(self):
        self.assertIn("SUPPORTED.forEach(function (code) {\n      var btn = makeFlagButton(code);", self.mount)
        paint = _js_function(self.js, "paintActive")
        self.assertIn("btn.hidden = btn.getAttribute('data-lang') === lang;", paint)

    def test_a_pick_switches_and_collapses(self):
        self.assertIn("close(true);\n        if (code !== lang) setLanguage(code);", self.mount)
        close = _js_function(self.mount, "close")
        self.assertIn("pop.classList.remove('open')", close)
        self.assertIn("current.setAttribute('aria-expanded', 'false')", close)

    def test_outside_click_and_escape_close_it(self):
        open_ = _js_function(self.mount, "open")
        self.assertIn("document.addEventListener('pointerdown', onOutside, true)", open_)
        self.assertIn("document.addEventListener('keydown', onKey, true)", open_)
        self.assertIn("if (!inside(e.target)) close(false);", _js_function(self.mount, "onOutside"))
        self.assertIn("e.key === 'Escape'", _js_function(self.mount, "onKey"))
        # every listener added on open is removed on close
        close = _js_function(self.mount, "close")
        for added in re.findall(r"(?:document|window)\.addEventListener\(([^)]*)\)", open_):
            self.assertIn(added, close.replace("removeEventListener", "addEventListener"))

    def test_keyboard(self):
        self.assertIn("open(e.detail === 0)", self.mount)              # Enter/Space focus the first flag
        self.assertIn("e.key === 'ArrowDown' || e.key === 'ArrowUp'", self.mount)
        on_key = _js_function(self.mount, "onKey")
        self.assertIn("RTL.indexOf(lang) !== -1", on_key)              # arrows follow the visual order
        self.assertIn("e.key === 'Home'", on_key)
        self.assertIn("e.key === 'End'", on_key)
        self.assertIn("pop.addEventListener('focusout', onFocusOut)", self.mount)

    def test_never_a_horizontal_scroll(self):
        place = _js_function(self.mount, "place")
        self.assertIn("left = Math.max(8, Math.min(left, vw - w - 8));", place)
        self.assertIn("if (top + h > vh - 8)", place)                  # opens upwards when no room
        # on <body>: a transformed/filtered header would otherwise re-anchor position:fixed
        self.assertIn("document.body.appendChild(pop);", self.mount)
        self.assertIn("if (container._uallakLangPop) container._uallakLangPop.remove();", self.mount)

    def test_every_page_still_mounts_it(self):
        for page in ("client", "login", "profile", "landing", "onboarding", "terms"):
            self.assertIn("uallakI18n.mountSwitcher(document.getElementById('langRow'))",
                          _src("dashboard", page, "index.html"), page)


# ─── E. Make removed ─────────────────────────────────────────────────────────

MAKE = re.compile(r"\bMake\b(?! sure| it | the )|make\.com|'make'|\"make\"")


class MakeRemoved(unittest.TestCase):
    def test_vendor_gone_manychat_untouched(self):
        self.assertEqual(list(automation_service.VENDORS), ["manychat"])
        self.assertEqual(automation_service.VENDORS["manychat"]["name"], "ManyChat")
        self.assertEqual(automation_service.RETIRED_VENDORS, ("make",))
        self.assertNotIn("Make", automation_service.POLICY_NOTE)
        self.assertEqual(automation_service.vendor("make"), {})
        self.assertFalse(automation_service.save_token(5, "make", "tok")["success"])

    def test_no_connection_help_entry(self):
        ids = [c["id"] for c in connection_help.CONNECTIONS]
        self.assertNotIn("make", ids)
        self.assertIn("manychat", ids)

    def test_feature_catalog(self):
        entry = feature_catalog.feature("marketing_automation")
        for field in ("name_he", "what_he", "where_he"):
            self.assertNotIn("Make", entry[field])
        self.assertIn("ManyChat", entry["name_he"])

    def test_client_facing_text(self):
        for parts in (("dashboard", "client", "index.html"), ("agents", "onboarding_agent.py"),
                      ("agents", "qa_agent_content.py"), ("core", "feature_catalog.py"),
                      ("core", "connection_help.py")):
            text = _src(*parts)
            text = text.replace("(Make was retired 2026-10-31 — those automations are built in-house.)", "")
            self.assertIsNone(MAKE.search(text), "/".join(parts))
        html = _src("dashboard", "client", "index.html")
        for gone in ("make_card", "make_note", "askAboutAutomation('make')"):
            self.assertNotIn(gone, html)
        self.assertIn("const AUTOMATION_NAMES = { manychat: 'ManyChat' };", html)
        self.assertIn("askAboutAutomation('manychat')", html)

    def test_admin_labels(self):
        admin = _src("dashboard", "admin", "index.html")
        self.assertNotIn("Make", admin)
        self.assertIn("ManyChat", admin)

    def test_stored_rows_left_intact(self):
        # The owner can still see and disconnect an old Make row; nothing deletes it
        self.assertIn("make", ownership.GROUPS)
        self.assertIn('"make": ["make"]', _src("core", "api_server.py"))
        self.assertIn('"make"', _src("core", "secrets_vault.py"))   # still never runs on another client's key


if __name__ == "__main__":
    unittest.main()
