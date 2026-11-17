"""Approval cards in the client chat (2026-11-17, core/chat_approval).

Replays client 27's production sequence: test3.mp4 → an upload card → "ok upload
it" / "i approve" / "תעלה" … which did NOTHING (only a click ran a card) while
the agent said "I've put a card right below" (the card sat higher up, or did
not exist). What must be true now:
- a typed yes runs the card exactly like the button (the same decide(): every
  check, compare-and-set, a double yes runs once) — the MODEL reads the yes; a
  keyword is only the fast path for the obvious;
- several open cards → ONE question listing them; one card → it runs;
- heavy / paid actions: one line first (what, cost, undo), never a block;
- stop inside the window cancels completely; after it the run continues and
  the client reads where it stands; undo where genuinely possible, as ONE card;
- the progress bar: real fraction, else the measured time (>= 5 runs), else
  "working" without a number; stalled / ended never stays mid-way;
- never a card that isn't there; never the same "press the card" a 3rd time;
- the dashboard: a known card comes back at the BOTTOM, never duplicated.
"""
import os
import re
import shutil
import subprocess
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")

for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}), ("httpx", {}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from test_agent_actions import CLIENTS, FakeDB, _Query, install_stubs   # noqa: E402
from core import agent_actions as aa                                      # noqa: E402
from core import chat_approval as ca                                      # noqa: E402
from core import task_eta                                                 # noqa: E402
from core import vendor_names as vn                                       # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
YT = "publish_youtube_from_media"


def _gte(self, col, val):
    self.filters.append(lambda r, c=col, v=val: str(r.get(c) or "") >= str(v))
    return self


_Query.gte = _gte


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Tasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        install_stubs()
        self.posted, self.runs = [], []
        self.saved = {n: getattr(aa, n) for n in ("_db", "_client", "_post_client_message", "_post_admin_message",
                                                  "_alert", "_log", "_sleep", "propose")}
        self.saved_spec = aa.ACTIONS[YT]
        self.saved_eta = (task_eta.action_seconds, task_eta.usually)
        aa._db = lambda: self.db
        aa._client = lambda cid: CLIENTS.get(cid, {})
        aa._post_client_message = lambda cid, persona, text: self.posted.append((cid, persona, text))
        aa._post_admin_message = lambda *a: None
        aa._alert = lambda issue: None
        aa._log = lambda *a, **k: None
        aa._sleep = lambda s: None
        task_eta.action_seconds = lambda action: None          # no history unless a test says so
        task_eta.usually = lambda action, lang: ""
        # The real upload card; only the YouTube call and the Drive reads are replaced
        aa.ACTIONS[YT] = {**self.saved_spec,
                          "validate_stored": lambda cid, params: dict(params or {}),
                          "details": lambda cid, params: {},
                          "display": lambda cid, params: {"file": "test3.mp4", "title": params.get("title", "")},
                          "run": self._run}

    def _run(self, client_id, params, background_tasks, **kw):
        self.runs.append(params.get("drive_file_id"))
        return {"video_id": "v1", "studio_url": "https://studio.youtube.com/video/v1/edit"}

    def tearDown(self):
        for n, fn in self.saved.items():
            setattr(aa, n, fn)
        aa.ACTIONS[YT] = self.saved_spec
        task_eta.action_seconds, task_eta.usually = self.saved_eta

    def card_row(self, action=YT, params=None, persona="media", client_id=27, minutes_ago=1, **extra):
        rows = self.db.tables.setdefault(aa.TABLE, [])
        row = {"id": len(rows) + 1, "client_id": client_id, "origin": "client_chat", "persona": persona,
               "action": action, "approver": "client", "status": "proposed",
               "params": params if params is not None else {"drive_file_id": "f-test3", "title": "test3"},
               "created_at": (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(), **extra}
        rows.append(row)
        return row

    def row(self, rid):
        return aa._get(rid)

    def model_says(self, verdict, card_id, persona="media", tasks=None):
        """The chat model read the client's message as `verdict` for card_id."""
        return ca.from_result(27, persona, {"reply": "…", "card_decision": {"verdict": verdict, "card_id": card_id}},
                              tasks if tasks is not None else Tasks(), "he")


# ─── 1. The production sequence ──────────────────────────────────────────────

class ProductionReplay(Base):
    def test_ok_upload_it_runs_the_upload_card(self):
        card = self.card_row()
        tasks = Tasks()
        out = self.model_says("approve", card["id"], tasks=tasks)   # "ok upload it"
        self.assertEqual(self.row(card["id"])["status"], "running")
        self.assertEqual(self.row(card["id"])["decided_by"], "client")
        self.assertEqual(len(tasks.tasks), 1)
        self.assertIs(tasks.tasks[0][0], aa._execute_after_window)
        self.assertEqual(out["action"]["id"], card["id"])            # the card comes back, at the bottom
        self.assertIn("YouTube Studio", out["reply"])                # undo said before it runs
        self.assertIn("עצור", out["reply"])                          # the stop window
        # the window passes, the job runs once and the card ends done
        fn, args, _ = tasks.tasks[0]
        fn(*args)
        self.assertEqual(self.runs, ["f-test3"])
        self.assertEqual(self.row(card["id"])["status"], "done")

    def test_fast_path_explicit_approve_in_every_language(self):
        for text in ("i approve", "approve", "מאשר", "אשר", "j'approuve", "одобряю", "أوافق", "approved!"):
            self.db.tables[aa.TABLE] = []
            card = self.card_row()
            out = ca.fast_turn(27, "media", text, Tasks(), "he")
            self.assertTrue(out, text)
            self.assertEqual(self.row(card["id"])["status"], "running", text)

    def test_free_language_is_the_models_reading_never_the_fast_path(self):
        self.card_row()
        for text in ("ok upload it", "תעלה", "כן תעלה", "יאללה", "סבבה", "קדימה", "למה לא", "אני סומך עליך",
                     "go for it", "vas-y", "давай", "يلا", "yes", "כן"):
            self.assertEqual(ca.fast_turn(27, "media", text, Tasks(), "he"), {}, text)
        # … and the model's verdict runs it: "תעלה"
        card = self.db.tables[aa.TABLE][0]
        self.model_says("approve", card["id"])
        self.assertEqual(self.row(card["id"])["status"], "running")

    def test_a_double_yes_runs_once(self):
        card = self.card_row()
        tasks = Tasks()
        self.model_says("approve", card["id"], tasks=tasks)
        second = self.model_says("approve", card["id"], tasks=tasks)
        self.assertEqual(len(tasks.tasks), 1)
        self.assertEqual(second, {})            # nothing open any more: the normal reply stands

    def test_no_card_open_fast_path_does_nothing(self):
        self.assertEqual(ca.fast_turn(27, "media", "i approve", Tasks(), "he"), {})

    def test_another_clients_card_never_runs(self):
        other = self.card_row(client_id=40)
        self.model_says("approve", other["id"])
        self.assertEqual(self.row(other["id"])["status"], "proposed")

    def test_paid_and_irreversible_are_no_exception(self):
        self.assertTrue(set(ca.IRREVERSIBLE) & set(aa.ACTIONS))
        self.assertIn("Costly or irreversible actions are no exception", ca.SUPPORT_RULE)


class Disambiguation(Base):
    def test_several_cards_ask_once_listing_them(self):
        a = self.card_row(minutes_ago=3)
        b = self.card_row(params={"drive_file_id": "f-2", "title": "two"}, minutes_ago=2)
        out = self.model_says("approve", 0)
        self.assertIn("1.", out["reply"])
        self.assertIn("2.", out["reply"])
        self.assertNotIn("action", out)
        self.assertEqual({self.row(a["id"])["status"], self.row(b["id"])["status"]}, {"proposed"})
        # the answer names one → it runs, the other stays
        self.model_says("approve", b["id"])
        self.assertEqual(self.row(b["id"])["status"], "running")
        self.assertEqual(self.row(a["id"])["status"], "proposed")

    def test_one_card_runs_without_asking(self):
        card = self.card_row()
        self.model_says("approve", 0)
        self.assertEqual(self.row(card["id"])["status"], "running")

    def test_this_windows_cards_first(self):
        other = self.card_row(persona="general")
        mine = self.card_row(persona="media")
        self.model_says("approve", 0, persona="media")
        self.assertEqual(self.row(mine["id"])["status"], "running")
        self.assertEqual(self.row(other["id"])["status"], "proposed")

    def test_cancel_in_words(self):
        card = self.card_row()
        out = self.model_says("cancel", card["id"])
        self.assertEqual(self.row(card["id"])["status"], "cancelled")
        self.assertTrue(out["reply"])


class HeadsUp(Base):
    def test_heavy_action_gets_one_line(self):
        for lang in LANGS:
            line = ca.heads_up({"action": YT}, lang)
            self.assertTrue(line, lang)
            self.assertEqual(line.count("\n"), 0)
        self.assertEqual(ca.heads_up({"action": "book_automation_call"}, "he"), "")      # light: nothing

    def test_cost_and_irreversible_said_before(self):
        self.assertIn(ca.COST["clone_voice"]["en"], ca.heads_up({"action": "clone_voice"}, "en"))
        self.assertIn(ca.CANNOT_UNDO["en"], ca.heads_up({"action": "clone_voice"}, "en"))
        self.assertEqual(ca.undo_note("configure_conversion", aa.ACTIONS["configure_conversion"], "en"),
                         ca.CAN_UNDO["en"])
        self.assertEqual(ca.undo_note("send_outreach_email", aa.ACTIONS.get("send_outreach_email", {}), "he"),
                         ca.CANNOT_UNDO["he"])

    def test_the_card_carries_the_undo_note_before_it_runs(self):
        card = aa.card(self.card_row(), "client", "en")
        self.assertEqual(card["undo_note"], ca.UNDO_BY_HAND[YT]["en"])


# ─── 2. Stop / undo ──────────────────────────────────────────────────────────

class StopWindow(Base):
    def test_stop_inside_the_window_cancels_completely(self):
        card = self.card_row()
        tasks = Tasks()
        self.model_says("approve", card["id"], tasks=tasks)
        out = ca.fast_turn(27, "media", "עצור", Tasks(), "he")
        self.assertEqual(out["reply"], ca.STOPPED["he"])
        row = self.row(card["id"])
        self.assertEqual((row["status"], row["outcome"]), ("cancelled", "stopped"))
        fn, args, _ = tasks.tasks[0]
        fn(*args)                                   # the window's end re-reads the row
        self.assertEqual(self.runs, [])             # nothing ran
        self.assertEqual(aa.card(row, "client", "en")["result"], ca.STOPPED["en"])

    def test_stop_words_in_five_languages(self):
        for word in ("stop", "wait", "עצור", "רגע", "חכה", "arrête", "стоп", "توقف"):
            self.db.tables[aa.TABLE] = []
            card = self.card_row()
            self.model_says("approve", card["id"])
            self.assertTrue(ca.fast_turn(27, "media", word, Tasks(), "he"), word)
            self.assertEqual(self.row(card["id"])["status"], "cancelled", word)

    def test_after_the_window_it_continues_and_says_so(self):
        card = self.card_row()
        self.model_says("approve", card["id"])
        past = (datetime.now(timezone.utc) - timedelta(seconds=aa.STOP_WINDOW_SECONDS + 5)).isoformat()
        self.db.tables[aa.TABLE][0]["decided_at"] = past
        out = self.model_says("stop", 0)
        self.assertEqual(self.row(card["id"])["status"], "running")      # not interrupted mid-upload
        self.assertIn("ממשיך עד הסוף", out["reply"])

    def test_stop_with_nothing_running_drops_the_open_card(self):
        card = self.card_row()
        self.model_says("stop", 0)
        self.assertEqual(self.row(card["id"])["status"], "cancelled")

    def test_window_constants_reuse_the_operator_pattern(self):
        self.assertTrue(0 < aa.STOP_WINDOW_SECONDS <= 15)
        from core import operator_agent
        for word in ("stop", "wait", "עצור", "רגע"):
            self.assertTrue(operator_agent.STOP_RX.match(word))
            self.assertTrue(ca.FAST_STOP.match(word))


class Undo(Base):
    def test_undo_where_possible_is_one_card(self):
        proposed = []
        aa.propose = lambda cid, origin, persona, raw, **kw: proposed.append(raw) or {
            "card": {"id": 99, "action": raw["name"]}}
        now = datetime.now(timezone.utc).isoformat()
        self.card_row(action="configure_conversion", params={}, status="done", finished_at=now)
        out = ca.undo(27, "media", "he")
        self.assertEqual(proposed, [{"name": "revert_conversion", "params": {}}])
        self.assertEqual(out["action"]["action"], "revert_conversion")
        self.assertEqual(ca.UNDO["publish_landing_page"]({"params": {"page_id": 4}}),
                         {"name": "unpublish_landing_page", "params": {"page_id": 4}})
        for name in ("revert_conversion", "unpublish_landing_page"):
            self.assertIn(name, aa.ACTIONS)

    def test_no_automatic_undo_says_how(self):
        now = datetime.now(timezone.utc).isoformat()
        self.card_row(status="done", finished_at=now)
        out = ca.undo(27, "media", "en")
        self.assertIn("YouTube Studio", out["reply"])
        self.assertNotIn("action", out)

    def test_nothing_to_undo(self):
        self.assertEqual(ca.undo(27, "media", "ru")["reply"], ca.UNDO_NOTHING["ru"])

    def test_a_finished_run_offers_its_undo_card(self):
        src = _src("core", "agent_actions.py")
        self.assertIn("undo = chat_approval.undo_card(client_id, row, lang)", src)
        self.assertIn("out[\"next_card\"] = card(open_undo[0], \"client\", _lang(client_id))", src)


# ─── 3. Progress ─────────────────────────────────────────────────────────────

class Progress(Base):
    def running_row(self, seconds_ago=60, action=YT):
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
        return self.card_row(action=action, status="running", decided_at=stamp)

    def test_without_history_no_number(self):
        p = aa.card(self.running_row(), "client", "he")["progress"]
        self.assertIsNone(p["fraction"])
        self.assertIsNone(p["expected_seconds"])
        self.assertIsNone(p["stop_until"])

    def test_with_history_the_measured_time(self):
        task_eta.action_seconds = lambda action: 240.0
        p = aa.card(self.running_row(), "client", "he")["progress"]
        self.assertEqual(p["expected_seconds"], 240)

    def test_history_needs_five_runs(self):
        self.assertEqual(task_eta.MIN_SAMPLES, 5)

    def test_real_fraction_from_the_chunked_upload(self):
        row = self.running_row(action="send_tiktok_to_inbox")
        fake = types.ModuleType("core.tiktok_uploads")
        fake.load = lambda cid: [{"proposal_id": row["id"], "next": 3, "chunks": 4}]
        saved = sys.modules.get("core.tiktok_uploads")
        import core
        saved_attr = getattr(core, "tiktok_uploads", None)
        sys.modules["core.tiktok_uploads"] = fake
        core.tiktok_uploads = fake
        try:
            self.assertEqual(ca.real_fraction(row), 0.75)
        finally:
            if saved is not None:
                sys.modules["core.tiktok_uploads"] = saved
            if saved_attr is not None:
                core.tiktok_uploads = saved_attr

    def test_stop_until_only_inside_the_window(self):
        p = aa.card(self.running_row(seconds_ago=1), "client", "he")["progress"]
        self.assertTrue(p["stop_until"])

    def test_stalled_shows_clearly(self):
        spec = aa.ACTIONS[YT]
        row = self.running_row(seconds_ago=60 * (2 * spec["slow"]["minutes"] + 1))
        p = aa.card(row, "client", "he")["progress"]
        self.assertTrue(p["stalled"])
        self.assertEqual(p["text"], aa._pick(aa._STALLED, "he"))

    def test_decide_returns_the_bar_data(self):
        card = self.card_row()
        out = aa.decide(card["id"], "client", 27, "approve", background_tasks=Tasks())
        for key in ("fraction", "expected_seconds", "stop_until", "started_at", "stalled"):
            self.assertIn(key, out["progress"])

    def test_the_dashboard_bar(self):
        html = _src("dashboard", "client", "index.html")
        fn = html[html.index("function actionBarState("):]
        fn = fn[:fn.index("\nfunction paintActionBar(")]
        self.assertIn("Math.min(95", fn)                       # an estimate never claims done
        node = shutil.which("node")
        if not node:
            return
        script = fn + """
const now = Date.parse('2026-11-17T10:00:00Z');
const s = new Date(now - 60000).toISOString();
const out = [actionBarState({started_at: s, fraction: 0.5}, now),
             actionBarState({started_at: s, expected_seconds: 240}, now),
             actionBarState({started_at: s, expected_seconds: 30}, now),
             actionBarState({started_at: s}, now),
             actionBarState({started_at: s, stop_until: new Date(now + 3000).toISOString()}, now)];
console.log(JSON.stringify(out));"""
        res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        import json
        real, measured, over, none, window = json.loads(res.stdout)
        self.assertEqual(real["pct"], 50)
        self.assertEqual(measured["pct"], 25)
        self.assertEqual(over["pct"], 95)
        self.assertIsNone(none["pct"])                      # no history: no made-up number
        self.assertTrue(window["stopHint"])


# ─── 4. Never a card that isn't there, never a loop ──────────────────────────

class ClaimGuard(Base):
    def test_failed_card_creation_is_said_plainly(self):
        reply = "מעולה, אני מעלה את זה. שמתי לכם כרטיס כאן למטה לאישור."
        out = ca.claim_guard(27, "media", reply, None, "he")
        self.assertNotIn("כרטיס כאן למטה", out["reply"])
        self.assertIn(ca.NO_CARD_MADE["he"], out["reply"])
        self.assertNotIn("action", out)

    def test_a_claim_with_an_open_card_resurfaces_it(self):
        card = self.card_row()
        out = ca.claim_guard(27, "media", "I've put a card right below.", None, "en")
        self.assertEqual(out["action"]["id"], card["id"])

    def test_no_claim_no_change(self):
        self.assertEqual(ca.claim_guard(27, "media", "The video is great.", None, "en"), {})
        self.assertEqual(ca.claim_guard(27, "media", "the card below", {"id": 1}, "en"), {})
        # the home page's connection cards are not a claim
        self.assertFalse(ca.mentions_card("Open the Google Ads card on your home page"))

    def test_claims_in_five_languages(self):
        for text in ("הכרטיס למטה", "the card below", "la carte ci-dessous", "البطاقة أدناه", "карточка ниже"):
            self.assertTrue(ca.mentions_card(text), text)

    def test_route_proposal_failure_flags_no_card(self):
        src = _src("agents", "support_agent.py")
        self.assertIn('"needs_human_followup": False, "no_card": True}', src)
        api = _src("core", "api_server.py")
        self.assertIn('{} if result.get("no_card") else', api)


class LoopGuard(Base):
    def test_third_time_states_what_blocks(self):
        card = {"id": 3, "title": "Upload", "can_decide": True, "blocked": ""}
        line = "The card is waiting for you below — press approve."
        self.assertEqual(ca.loop_guard(27, line, card, [line], "en"), line)
        third = ca.loop_guard(27, line, card, [line, line], "en")
        self.assertEqual(third, ca.WAITING_ONLY_FOR_YOU["en"].replace("{title}", "Upload"))
        blocked = ca.loop_guard(27, line, {**card, "blocked": "connect YouTube first"}, [line, line], "en")
        self.assertIn("connect YouTube first", blocked)

    def test_wired_after_the_reply(self):
        api = _src("core", "api_server.py")
        self.assertIn("chat_approval.loop_guard(client_id, result[\"reply\"], result[\"action\"], previous, lang)", api)


# ─── 5. Wiring, prompt, texts, dashboard ─────────────────────────────────────

class Wiring(unittest.TestCase):
    def test_both_schemas_carry_the_decision(self):
        from agents import support_agent as sa
        self.assertIn('"card_decision": {"verdict": "none", "card_id": 0}', sa.SYSTEM)
        self.assertIn(ca.SUPPORT_RULE, sa.SYSTEM)
        persona = sa.PERSONA_SYSTEM_TEMPLATE.format(name="x", domain="y", extra_fields="", **{
            k: "" for k in re.findall(r"\{(\w+)\}", sa.PERSONA_SYSTEM_TEMPLATE) if k not in
            ("name", "domain", "extra_fields")})
        self.assertIn('"card_decision": {"verdict": "none", "card_id": 0}', persona)
        self.assertIn("TYPING AGREEMENT IS THE SAME AS PRESSING THE", persona)
        self.assertNotIn("press the approve button on the\n  card below", sa.SYSTEM)

    def test_light_turns_keep_the_decision(self):
        from agents import support_agent as sa
        result = {"card_decision": {"verdict": "approve", "card_id": 1}, "proposed_action": {"name": "x"}}
        sa._strip_light_result(result)
        self.assertIn("card_decision", result)
        self.assertNotIn("proposed_action", result)

    def test_the_concierge_gets_background_tasks(self):
        api = _src("core", "api_server.py")
        self.assertIn("help_connection=req.help_connection,\n                                                 "
                      "background_tasks=background_tasks)", api)
        self.assertIn("chat_approval.fast_turn(client_id, persona, message, background_tasks", api)
        self.assertIn('"next_card": result.get("next_card")}', api)

    def test_every_text_in_five_languages_without_vendor_tools(self):
        for key, table in ca.TEXT.items():
            self.assertEqual(set(table), set(LANGS), key)
            for lang, text in table.items():
                self.assertTrue(text.strip(), f"{key}/{lang}")
                self.assertEqual([n for n in vn.find(text) if n.lower() not in ("youtube", "tiktok")], [],
                                 f"{key}/{lang}")
        for table in list(ca.UNDO_BY_HAND.values()) + list(ca.COST.values()):
            self.assertEqual(set(table), set(LANGS))

    def test_no_new_settings_key_or_env(self):
        src = _src("core", "chat_approval.py")
        self.assertNotIn("os.environ", src)
        self.assertNotIn("app_settings", src)


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.html = _src("dashboard", "client", "index.html")

    def test_a_known_card_comes_back_at_the_bottom(self):
        fn = self.html[self.html.index("function appendActionCard("):]
        fn = fn[:fn.index("\n}\n")]
        self.assertNotIn("data-action-id=\"${Number(a.id)}\"]`)) return;", fn)
        self.assertIn("if (old) old.remove();", fn)
        self.assertIn("w.msgsEl.appendChild(el);", fn)
        self.assertEqual(fn.count("w.msgsEl.appendChild(el)"), 1)    # one copy, never two

    def test_the_reply_waits_for_the_history(self):
        fn = self.html[self.html.index("async function sendInWindow("):]
        fn = fn[:fn.index("\n}\n")]
        wait = fn.index("await w.historyLoaded")
        self.assertLess(wait, fn.index("appendActionCard(personaId, data.action)"))
        self.assertIn("if (data.next_card) appendActionCard(personaId, data.next_card);", fn)

    def test_bar_i18n_and_rtl(self):
        for key in ("action_left_min", "action_left_sec", "action_running_now", "action_stop_hint"):
            line = next(l for l in self.html.splitlines() if l.strip().startswith(key + ":"))
            for lang in LANGS:
                self.assertIn(f"{lang}:", line, key)
        css = self.html[self.html.index(".chat-action .pbar {"):self.html.index("@keyframes pbar-run")]
        self.assertIn("inset-inline-start", css)                 # RTL-safe
        self.assertNotIn(" left:", css)
        self.assertIn("prefers-reduced-motion", self.html[self.html.index("@keyframes pbar-run"):][:400])

    def test_finished_card_hands_over_the_undo_card(self):
        self.assertIn("if (c.next_card) appendActionCard(", self.html)


if __name__ == "__main__":
    unittest.main()
