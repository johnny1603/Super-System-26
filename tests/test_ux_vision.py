"""UX vision pass 1 — the client dashboard feels like staff, not software.

1. Labels: no CLIENT-FACING string names HeyGen / ElevenLabs / Higgsfield —
   the dashboard's visible markup and every i18n value (5 languages), the
   profile page, the server's client messages (missing-tool block, provider
   labels, connect errors, chat-card texts, consent statements, chat lines),
   and a code-level scrub of chat replies. The vendor is named only inside
   the connection help flow, where the client opens the account.
2. Help is the default path: each creative tool's card opens a guided flow
   (core/connection_help.guide_view) with "stuck? we'll do it with you" (a
   staff request) and the key field only as "I already have a key".
3. Waiting is a card, not a spinner: a slow client-approved action answers
   the click at once with a progress card, runs in the background, and the
   same card turns into the result; a provider failure reaches the client
   only as a plain line, with an admin alert.

Stdlib only. Run from the tests folder:  python -I -B test_ux_vision.py -v
"""
import ast
import os
import re
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

# httpx / supabase are not needed by anything tested here — stubs keep the
# imports stdlib-only
sys.modules.setdefault("httpx", types.ModuleType("httpx"))
_supabase = types.ModuleType("supabase")
_supabase.create_client = lambda *a, **k: None
sys.modules.setdefault("supabase", _supabase)

from test_agent_actions import CLIENTS, FakeDB   # noqa: E402  (the in-memory DB)
from core import agent_actions as aa             # noqa: E402
from core import avatar_video as av              # noqa: E402
from core import connection_help as ch           # noqa: E402
from core import vendor_tools as vt              # noqa: E402

VENDOR = re.compile(r"heygen|eleven\s?labs|higgsfield", re.IGNORECASE)
LANGS = ("he", "en", "fr", "ar", "ru")
TOOLS = ("heygen", "elevenlabs", "higgsfield")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _i18n_values(html: str) -> dict:
    """{key: [the 5 language strings]} from the page's i18n table lines."""
    out = {}
    for m in re.finditer(r"^  (\w+): \{ he:'((?:[^'\\]|\\.)*)', en:'((?:[^'\\]|\\.)*)', fr:'((?:[^'\\]|\\.)*)', "
                         r"ar:'((?:[^'\\]|\\.)*)', ru:'((?:[^'\\]|\\.)*)' \},$", html, re.M):
        out[m.group(1)] = list(m.groups()[1:])
    return out


def _visible_markup(html: str) -> str:
    """The page's markup as a client reads it: no scripts, styles, comments
    or tags — but placeholders, titles and labels kept."""
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    # (?<![-\w]) keeps data-i18n-placeholder="key_name" (a key, not text) out
    attrs = " ".join(re.findall(r'(?<![-\w])(?:placeholder|title|alt|aria-label)="([^"]*)"', html))
    return re.sub(r"<[^>]+>", " ", html) + " " + attrs


def _texts(value):
    """Every string inside a nested dict/list (the action registry texts)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _texts(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _texts(v)


# ─── 1. Labels ───────────────────────────────────────────────────────────────

class DashboardLabels(unittest.TestCase):
    def setUp(self):
        self.html = _src("dashboard", "client", "index.html")
        self.i18n = _i18n_values(self.html)

    def test_i18n_table_parsed(self):
        # The parser must actually see the table, or the next test proves nothing
        self.assertGreater(len(self.i18n), 500)
        for key in ("tool_heygen_card", "tool_eleven_card", "higgsfield_card", "provider_heygen"):
            self.assertIn(key, self.i18n)

    def test_no_vendor_name_in_any_i18n_value(self):
        hits = {k: v for k, values in self.i18n.items() for v in values if VENDOR.search(v)}
        self.assertEqual(hits, {}, "client-facing i18n text names a vendor")

    def test_no_vendor_name_in_visible_markup(self):
        hits = VENDOR.findall(_visible_markup(self.html))
        self.assertEqual(hits, [], "visible markup / placeholder names a vendor")

    def test_capability_labels_in_all_languages(self):
        expected = {"tool_eleven_card": "הקול שלך", "tool_heygen_card": "הדמות שבסרטונים — דמות מצולמת שלך",
                    "higgsfield_card": "הדמות שבסרטונים — דמות AI", "provider_heygen": "דמות מצולמת שלך",
                    "provider_higgsfield": "דמות AI"}
        for key, he in expected.items():
            self.assertEqual(self.i18n[key][0], he)
            self.assertTrue(all(self.i18n[key]), key)
        for tool in TOOLS:
            self.assertTrue(all(self.i18n["tool_name_" + tool]), tool)

    def test_notices_use_capability_names_not_vendor_constants(self):
        self.assertNotIn("TOOL_LABELS", self.html)
        self.assertIn("function toolName(tool) { return uallakI18n.t('tool_name_' + tool); }", self.html)

    def test_profile_page_has_no_vendor_name(self):
        html = _src("dashboard", "profile", "index.html")
        hits = {k: v for k, values in _i18n_values(html).items() for v in values if VENDOR.search(v)}
        self.assertEqual(hits, {})
        self.assertEqual(VENDOR.findall(_visible_markup(html)), [])


class ServerLabels(unittest.TestCase):
    def test_client_labels_complete_and_vendor_free(self):
        for tool in TOOLS:
            for lang in LANGS:
                label = vt.client_label(tool, lang)
                self.assertTrue(label, (tool, lang))
                self.assertIsNone(VENDOR.search(label), (tool, lang))
        self.assertEqual(vt.client_label("elevenlabs"), "הקול שלך")
        self.assertIn("דמות מצולמת שלך", vt.client_label("heygen"))
        self.assertIn("דמות AI", vt.client_label("higgsfield"))
        # Internal names stay (code, logs, admin, cost rows)
        self.assertEqual(vt.TOOLS["heygen"]["label"], "HeyGen")

    def test_missing_block_names_capabilities_only(self):
        for combo in (["heygen"], ["elevenlabs"], ["higgsfield"], ["heygen", "elevenlabs"],
                      ["higgsfield", "elevenlabs"]):
            block = vt.missing_block(combo)
            self.assertIsNone(VENDOR.search(block["message_he"]), combo)
            for button in block["buttons"]:
                self.assertIsNone(VENDOR.search(button["label"]), combo)

    def test_provider_labels(self):
        self.assertEqual(av.PROVIDERS["heygen"].label_he, "דמות מצולמת שלך")
        self.assertEqual(av.PROVIDERS["higgsfield"].label_he, "דמות AI")

    def test_avatar_video_client_messages(self):
        # Every Hebrew sentence in the provider layer (refusals the client may read)
        tree = ast.parse(_src("core", "avatar_video.py"))
        hebrew = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                  and re.search("[֐-׿]", n.value)]
        self.assertTrue(hebrew)
        self.assertEqual([h for h in hebrew if VENDOR.search(h)], [])

    def test_connect_errors(self):
        tree = ast.parse(_src("core", "api_server.py"))
        table = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                     and any(getattr(t, "id", "") == "_TOOL_ERRORS_HE" for t in n.targets))
        errors = ast.literal_eval(table)
        self.assertEqual([v for v in errors.values() if VENDOR.search(v)], [])

    def test_consent_statements_and_chat_lines_of_the_avatar_agent(self):
        source = _src("agents", "avatar_agent.py")
        tree = ast.parse(source)
        consent = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                       and any(getattr(t, "id", "") == "CONSENT_STATEMENTS_HE" for t in n.targets))
        for text in ast.literal_eval(consent).values():
            self.assertIsNone(VENDOR.search(text))
        # The wording changed, so the version every consent row records did too
        self.assertIn('CONSENT_VERSION = "2026-10-30.v2"', source)
        # Every chat line the agent posts to the client
        for call in ast.walk(tree):
            if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "log_communication":
                for arg in call.args:
                    for node in ast.walk(arg):
                        if isinstance(node, ast.Constant) and isinstance(node.value, str):
                            self.assertIsNone(VENDOR.search(node.value), node.value)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "message_he"
                                                   for k in node.keys)):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value == "message_he":
                        for c in ast.walk(v):
                            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                                self.assertIsNone(VENDOR.search(c.value), c.value)

    def test_every_chat_card_text_is_vendor_free(self):
        for name, spec in aa.ACTIONS.items():
            for key in ("title", "what", "done", "refusals"):
                for text in _texts(spec.get(key) or {}):
                    self.assertIsNone(VENDOR.search(text), (name, key, text))
            slow = spec.get("slow") or {}
            for text in _texts(slow.get("eta") or {}):
                self.assertIsNone(VENDOR.search(text), (name, "eta"))
        for fixed in (aa._PROGRESS, aa._STALLED, aa._FAILED, aa._CANCELLED, aa._CREATIVE_REFUSALS):
            for text in _texts(fixed):
                self.assertIsNone(VENDOR.search(text))

    def test_connect_card_names_the_tool_by_capability(self):
        saved = aa._client
        aa._client = lambda cid: CLIENTS.get(cid, {})
        try:
            row = {"id": 1, "client_id": 41, "action": "connect_creative_tool", "status": "proposed",
                   "params": {"tool": "elevenlabs"}, "approver": "client", "origin": "client_chat"}
            card = aa.card(row, "client", "en")
        finally:
            aa._client = saved
        self.assertIn("Your voice", card["what"])
        self.assertIsNone(VENDOR.search(card["what"]))

    def test_feature_catalog(self):
        tree = ast.parse(_src("core", "feature_catalog.py"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value in ("name_he", "what_he", "where_he"):
                        for c in ast.walk(v):
                            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                                self.assertIsNone(VENDOR.search(c.value), c.value)


class ChatReplyScrub(unittest.TestCase):
    def test_vendor_names_become_capabilities(self):
        reply = "חיברתי את HeyGen, ו-ElevenLabs עוד לא. גם Higgsfield וגם Eleven Labs."
        out = vt.scrub_vendor_names(reply, "he")
        self.assertIsNone(VENDOR.search(out))
        self.assertIn("הקול שלך", out)
        self.assertIn("דמות מצולמת שלך", out)
        self.assertIn("דמות AI", out)
        self.assertEqual(vt.scrub_vendor_names("Your HeyGen's key", "en"),
                         "Your " + vt.client_label("heygen", "en") + " key")
        self.assertEqual(vt.scrub_vendor_names("nothing here", "he"), "nothing here")
        self.assertEqual(vt.scrub_vendor_names("", "he"), "")

    def test_wired_into_the_chat_route_except_the_tools_own_help(self):
        source = _src("core", "api_server.py")
        self.assertIn("if req.help_connection not in vendor_tools.TOOLS:", source)
        self.assertIn("vendor_tools.scrub_vendor_names(result[\"reply\"]", source)
        # Scrubbed BEFORE it is stored as the outbound message
        self.assertLess(source.index("vendor_tools.scrub_vendor_names(result[\"reply\"]"),
                        source.index("log_communication(client_id, \"outbound\", channel, result[\"reply\"])"))

    def test_agent_prompt_forbids_vendor_names(self):
        source = _src("agents", "support_agent.py")
        self.assertIn("NEVER write a vendor or product name for them to the client", source)


# ─── 2. Help is the default path ─────────────────────────────────────────────

class GuidedHelp(unittest.TestCase):
    def test_every_tool_has_a_guide_in_every_language(self):
        for tool in TOOLS:
            entry = ch.get(tool)
            allowed = {link["url"] for link in entry["links"] if link["url"]}
            for lang in LANGS:
                view = ch.guide_view(tool, lang)
                self.assertTrue(view["steps"], (tool, lang))
                self.assertTrue(view["key_where"], (tool, lang))
                self.assertIsNone(VENDOR.search(view["title"]), (tool, lang))
                for step in view["steps"]:
                    self.assertTrue(step["do"] and step["see"], (tool, lang))
                    if step["link"]:
                        self.assertIn(step["link"]["url"], allowed)
                        self.assertTrue(step["link"]["url"].startswith("https://"))
                        self.assertTrue(step["link"]["label"])
                # At least one direct link the client can click
                self.assertTrue(any(s["link"] for s in view["steps"]), (tool, lang))

    def test_vendor_named_only_inside_the_guide(self):
        # Opening the account needs the vendor's name — in the steps, never the title
        for tool in TOOLS:
            view = ch.guide_view(tool, "he")
            self.assertIsNone(VENDOR.search(view["title"]))
            self.assertIsNotNone(VENDOR.search(view["steps"][0]["do"]), tool)

    def test_concierge_steps_are_the_guide(self):
        for tool in TOOLS:
            entry = ch.get(tool)
            for lang in LANGS:
                self.assertEqual(entry["steps"][lang], [g["do"] for g in entry["guide"][lang]])

    def test_no_guide_for_oauth_connections(self):
        self.assertEqual(ch.guide_view("google_ads", "he"), {})
        self.assertEqual(ch.guide_view("creatomate", "he"), {})   # inactive

    def test_routes(self):
        source = _src("core", "api_server.py")
        self.assertIn('@app.get("/api/client/connection-help/{connection_id}")', source)
        self.assertIn('@app.post("/api/client/connection-help/{connection_id}/assist")', source)
        assist = source[source.index("def client_connection_assist"):]
        assist = assist[:assist.index("@app.")]
        self.assertIn("_require_session(request)", assist)
        self.assertIn("chat_fallback.alert_once(", assist)       # a staff request, deduped

    def test_card_primary_action_is_the_guide(self):
        html = _src("dashboard", "client", "index.html")
        for tool in TOOLS:
            self.assertIn(f'id="toolOpenBtn-{tool}" onclick="openToolGuide(\'{tool}\')" '
                          f'data-i18n="tool_start_guide"', html)
            self.assertIn(f'id="toolGuide-{tool}"', html)
            # The raw key field is still there, behind "I already have a key"
            self.assertIn(f'id="toolKey-{tool}"', html)
        guide_js = html[html.index("async function openToolGuide"):html.index("async function connectTool")]
        self.assertIn("t('tool_have_key')", guide_js)
        self.assertIn("t('tool_stuck_btn')", guide_js)
        self.assertIn("requestToolAssist(tool, stuck)", guide_js)
        self.assertIn("'/assist'", guide_js)
        self.assertNotIn("innerHTML", guide_js)                  # every value set as text
        i18n = _i18n_values(html)
        for key in ("tool_start_guide", "tool_have_key", "tool_stuck_btn", "tool_stuck_sent", "tool_stuck_failed",
                    "tool_guide_see", "tool_guide_key_where"):
            self.assertTrue(all(i18n[key]), key)


# ─── 3. Waiting is a card, not a spinner ─────────────────────────────────────

class Tasks:
    """Stands in for FastAPI's BackgroundTasks: collects, runs on demand."""

    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))

    def run(self):
        for fn, args, kwargs in self.tasks:
            fn(*args, **kwargs)


def _txt(text):
    return {lang: f"{text}-{lang}" for lang in LANGS}


class ProgressCards(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.posted, self.alerts, self.alert_once = [], [], []
        self.saved = {n: getattr(aa, n) for n in ("_db", "_client", "_post_client_message", "_alert", "_log")}
        aa._db = lambda: self.db
        aa._client = lambda cid: CLIENTS.get(cid, {})
        aa._post_client_message = lambda cid, persona, text: self.posted.append(text)
        aa._alert = lambda issue: self.alerts.append(issue)
        aa._log = lambda *a, **k: None
        fallback = types.ModuleType("core.chat_fallback")
        fallback.alert_once = lambda *a, **k: self.alert_once.append(a) or True
        self.saved_fallback = sys.modules.get("core.chat_fallback")
        sys.modules["core.chat_fallback"] = fallback
        self.outcome = {"raise": None}

        def run(client_id, params, background_tasks):
            if self.outcome["raise"]:
                raise self.outcome["raise"]
            return {"ok": True}

        aa.ACTIONS["slow_test"] = {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "does": "test", "params": {}, "available": lambda s, c: True, "run": run,
            "slow": {"minutes": 5, "eta": _txt("ETA")}, "title": _txt("T"), "what": _txt("W"),
            "done": _txt("DONE"), "refusals": {"explained": _txt("EXPLAINED")}}
        aa.ACTIONS["quick_test"] = {**aa.ACTIONS["slow_test"]}
        aa.ACTIONS["quick_test"].pop("slow")

    def tearDown(self):
        for n, fn in self.saved.items():
            setattr(aa, n, fn)
        aa.ACTIONS.pop("slow_test", None)
        aa.ACTIONS.pop("quick_test", None)
        if self.saved_fallback is None:
            sys.modules.pop("core.chat_fallback", None)
        else:
            sys.modules["core.chat_fallback"] = self.saved_fallback

    def proposal(self, name="slow_test", client_id=40):
        return aa.propose(client_id, "client_chat", "general", {"name": name, "params": {}}, state={})["card"]["id"]

    def row(self, pid):
        return next(r for r in self.db.tables[aa.TABLE] if r["id"] == pid)

    def test_click_answers_at_once_with_a_progress_card(self):
        pid, tasks = self.proposal(), Tasks()
        out = aa.decide(pid, "client", 40, "approve", background_tasks=tasks)
        self.assertEqual(out["status"], "running")
        self.assertIn("ETA-he", out["message"])                  # how long it usually takes
        self.assertIn("ETA-he", out["progress"]["text"])
        self.assertIn("נכתוב כאן בצ׳אט", out["progress"]["text"])  # they'll be told
        self.assertEqual(self.row(pid)["status"], "running")
        self.assertEqual(self.posted, [])                         # nothing ran yet
        self.assertEqual(len(tasks.tasks), 1)
        card = aa.status_card(pid, 40)
        self.assertEqual(card["status"], "running")
        self.assertFalse(card["progress"]["stalled"])
        self.assertEqual(card["result"], "")

    def test_same_card_turns_into_the_result(self):
        pid, tasks = self.proposal(), Tasks()
        aa.decide(pid, "client", 40, "approve", background_tasks=tasks)
        tasks.run()
        self.assertEqual(self.row(pid)["status"], "done")
        self.assertEqual(self.posted, ["DONE-he"])               # told in the chat too
        card = aa.status_card(pid, 40)
        self.assertIsNone(card["progress"])
        self.assertEqual(card["result"], "DONE-he")
        self.assertEqual(self.alerts, [])

    def test_provider_failure_is_a_plain_line_and_an_alert(self):
        pid, tasks = self.proposal(), Tasks()
        self.outcome["raise"] = RuntimeError("heygen 500 Internal Server Error: SECRET-BODY")
        aa.decide(pid, "client", 40, "approve", background_tasks=tasks)
        tasks.run()
        self.assertEqual(self.row(pid)["status"], "failed")
        self.assertEqual(self.posted, [aa._FAILED["he"]])
        self.assertNotIn("SECRET", self.posted[0])
        self.assertIsNone(VENDOR.search(self.posted[0]))
        self.assertEqual(len(self.alerts), 1)                    # the team knows
        card = aa.status_card(pid, 40)
        self.assertEqual(card["result"], aa._FAILED["he"])
        self.assertNotIn("SECRET", repr(card))

    def test_explained_refusal_needs_no_alert(self):
        pid, tasks = self.proposal(), Tasks()
        self.outcome["raise"] = aa.ActionFailed("explained", "detail for the log")
        aa.decide(pid, "client", 40, "approve", background_tasks=tasks)
        tasks.run()
        self.assertEqual(self.posted, ["EXPLAINED-he"])
        self.assertEqual(self.alerts, [])
        self.assertEqual(aa.status_card(pid, 40)["result"], "EXPLAINED-he")

    def test_background_run_never_raises(self):
        pid, tasks = self.proposal(), Tasks()
        aa.decide(pid, "client", 40, "approve", background_tasks=tasks)
        saved = aa._execute
        aa._execute = lambda *a, **k: (_ for _ in ()).throw(KeyError("broken"))
        try:
            tasks.run()                                           # must not raise
        finally:
            aa._execute = saved
        self.assertEqual(self.row(pid)["status"], "failed")
        self.assertEqual(self.posted, [aa._FAILED["he"]])
        self.assertEqual(len(self.alerts), 1)

    def test_quick_actions_and_no_background_run_inline(self):
        pid, tasks = self.proposal("quick_test"), Tasks()
        out = aa.decide(pid, "client", 40, "approve", background_tasks=tasks)
        self.assertEqual(out["status"], "done")
        self.assertEqual(tasks.tasks, [])
        pid = self.proposal()
        out = aa.decide(pid, "client", 40, "approve", background_tasks=None)
        self.assertEqual(out["status"], "done")

    def test_running_card_survives_a_reload(self):
        pid, tasks = self.proposal(), Tasks()
        aa.decide(pid, "client", 40, "approve", background_tasks=tasks)
        cards = aa.open_cards(40, "client", "general")
        self.assertEqual([c["id"] for c in cards], [pid])
        self.assertEqual(cards[0]["status"], "running")
        self.assertIn("ETA-he", cards[0]["progress"]["text"])
        # A non-slow running row is not shown as a progress card
        self.db.tables[aa.TABLE].append({"id": 99, "client_id": 40, "action": "quick_test", "status": "running",
                                         "origin": "client_chat", "persona": "general", "params": {},
                                         "created_at": datetime.now(timezone.utc).isoformat()})
        self.assertEqual([c["id"] for c in aa.open_cards(40, "client", "general")], [pid])

    def test_stalled_job_says_the_team_is_checking_and_alerts_once(self):
        pid, tasks = self.proposal(), Tasks()
        aa.decide(pid, "client", 40, "approve", background_tasks=tasks)
        self.row(pid)["decided_at"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        card = aa.status_card(pid, 40)
        self.assertTrue(card["progress"]["stalled"])
        self.assertEqual(card["progress"]["text"], aa._STALLED["he"])
        self.assertEqual(len(self.alert_once), 1)
        self.assertEqual(self.alert_once[0][2], f"stalled:{pid}")  # deduped per proposal

    def test_another_clients_card_is_not_found(self):
        pid = self.proposal()
        self.assertEqual(aa.status_card(pid, 27), {})
        self.assertEqual(aa.status_card(12345, 40), {})

    def test_real_slow_actions(self):
        for name in ("publish_youtube_from_media", "send_tiktok_to_inbox", "configure_conversion",
                     "clone_voice", "make_presenter_video"):
            slow = aa.ACTIONS[name].get("slow")
            self.assertTrue(slow and slow["minutes"] > 0, name)
            self.assertEqual(set(slow["eta"]), set(LANGS), name)
            for lang in LANGS:
                self.assertNotIn("{eta}", aa.progress_text(aa.ACTIONS[name], lang))


class CreativeActions(unittest.TestCase):
    """The voice clone and the presenter video as client-approved cards —
    internal_only until the client-27 live test passes."""

    def test_internal_only_until_live_tested(self):
        for name in ("clone_voice", "make_presenter_video"):
            self.assertTrue(aa.ACTIONS[name]["internal_only"], name)
            self.assertEqual(aa.ACTIONS[name]["approver"], "client")
            self.assertIs(aa.ACTIONS[name]["refusals"], aa._CREATIVE_REFUSALS)

    def test_availability(self):
        connected = {"status": "connected"}
        state = {"creative_tools": {"tools": {"elevenlabs": connected, "heygen": connected}},
                 "voice": {"count": 2, "voice_clone_consent": True, "voice_clone_exists": False}}
        self.assertTrue(aa._voice_clone_possible(state, {}))
        self.assertTrue(aa._presenter_video_possible(state, {}))
        no_consent = {**state, "voice": {**state["voice"], "voice_clone_consent": False}}
        self.assertFalse(aa._voice_clone_possible(no_consent, {}))
        has_clone = {**state, "voice": {**state["voice"], "voice_clone_exists": True}}
        self.assertFalse(aa._voice_clone_possible(has_clone, {}))
        no_presenter = {**state, "creative_tools": {"tools": {"elevenlabs": connected}}}
        self.assertFalse(aa._presenter_video_possible(no_presenter, {}))

    def test_voice_param_must_be_this_clients_recording(self):
        voice_stub = types.ModuleType("core.voice_recordings")
        voice_stub.get = lambda cid, fid: {"id": fid, "name": "me.mp3"} if (cid, fid) == (27, "rec1") else {}
        saved = sys.modules.get("core.voice_recordings")
        sys.modules["core.voice_recordings"] = voice_stub
        try:
            self.assertEqual(aa._v_presenter_voice("clone", 27), "clone")
            self.assertEqual(aa._v_presenter_voice("recording:rec1", 27), "recording:rec1")
            for bad in ("recording:rec1", "recording:", "tts", ""):
                with self.assertRaises(ValueError):
                    aa._v_presenter_voice(bad, 40 if bad == "recording:rec1" else 27)
        finally:
            if saved is None:
                sys.modules.pop("core.voice_recordings", None)
            else:
                sys.modules["core.voice_recordings"] = saved
        with self.assertRaises(ValueError):
            aa._v_script("hi", 27)
        with self.assertRaises(ValueError):
            aa._v_presenter("sora", 27)

    def _run_video(self, result):
        avatar = types.ModuleType("agents.avatar_agent")
        avatar.generate_avatar_video = lambda *a, **k: result
        saved = sys.modules.get("agents.avatar_agent")
        sys.modules["agents.avatar_agent"] = avatar
        try:
            return aa._run_make_presenter_video(27, {"script": "שלום לכולם", "voice": "clone"}, None)
        finally:
            if saved is None:
                sys.modules.pop("agents.avatar_agent", None)
            else:
                sys.modules["agents.avatar_agent"] = saved

    def test_video_outcomes_map_to_plain_lines(self):
        self.assertEqual(self._run_video({"success": True, "link": "https://drive/x"})["link"], "https://drive/x")
        for code, expected in (("missing_tool", "missing_tool"), ("no_tier", "not_in_plan"),
                               ("minutes_cap", "not_in_plan"), ("no_avatar", "not_ready_yet"),
                               ("provider_error", "provider_error"), ("", "provider_error")):
            with self.assertRaises(aa.ActionFailed) as e:
                self._run_video({"success": False, "code": code, "message_he": "x"})
            self.assertEqual(e.exception.code, expected, code)
        # A provider error has no refusal text: it becomes _FAILED + an alert
        self.assertNotIn("provider_error", aa._CREATIVE_REFUSALS)


class DashboardProgressCards(unittest.TestCase):
    def setUp(self):
        self.html = _src("dashboard", "client", "index.html")

    def test_decide_paints_a_progress_card_and_polls(self):
        decide = self.html[self.html.index("async function decideAction"):]
        decide = decide[:decide.index("\nasync function ")]
        self.assertIn("r.status === 'running' && r.progress", decide)
        self.assertIn("paintActionProgress(el, r.progress)", decide)
        self.assertIn("watchActionCard(el, id)", decide)

    def test_reload_reshows_the_running_card(self):
        append = self.html[self.html.index("function appendActionCard"):self.html.index("function paintActionProgress")]
        self.assertIn("a.status === 'running' && a.progress", append)

    def test_poll_updates_the_same_card_with_backoff_and_no_spinner(self):
        block = self.html[self.html.index("function paintActionProgress"):self.html.index("function actionDetails")]
        self.assertIn("/api/client/actions/${Number(id)}", block)
        self.assertIn("finishActionCard(el, c)", block)
        self.assertIn("Math.min(delay * 1.5, 30000)", block)      # backoff, never a tight loop
        self.assertNotIn("spinner", block.lower())
        self.assertNotIn("alert(", block)
        self.assertIn("linkify(c.result || '')", block)           # escaped, never raw HTML

    def test_status_route(self):
        source = _src("core", "api_server.py")
        self.assertIn('@app.get("/api/client/actions/{proposal_id}")', source)
        self.assertIn("agent_actions.status_card(proposal_id, client_id)", source)

    def test_i18n(self):
        self.assertTrue(all(_i18n_values(self.html)["action_started_ago"]))


if __name__ == "__main__":
    unittest.main()
