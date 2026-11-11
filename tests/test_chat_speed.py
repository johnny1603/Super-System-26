"""Client chat speed (2026-11-11, docs/CHAT-SPEED.md).

A. core/chat_route: rules only, doubt goes UP — simple (thanks / hi with no
   pending offer) / read (a short question) / work (everything that does,
   changes, publishes, spends, a "yes", a frustrated client, long, unclear).
B. A simple turn reads NO account context, sends only the last few
   messages, runs on the cheapest tier, and nothing it "proposes" is acted on;
   a full turn reads everything — at the same time (core/parallel).
C. core/parallel: gather runs together and keeps the contextvars;
   single_flight shares ONE live call between concurrent asks for a client.
D. client_state.snapshot reads its sections in parallel, same keys / order,
   failures still listed.
E. The route chosen and the message length are on the timing row and in the
   admin view; the dashboard's long-wait line is text only.

Stdlib only; no model, no Supabase, no provider. Run from the repo root:
python -I -B tests/test_chat_speed.py -v
"""
import contextvars
import os
import sys
import threading
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


STUBS = {
    "httpx": _module("httpx"),
    "anthropic": _module("anthropic", Anthropic=lambda *a, **k: None),
    "supabase": _module("supabase", create_client=lambda *a, **k: None),
    "agents.client_agent": _module(
        "agents.client_agent",
        get_client=lambda cid: {"id": cid, "name": "Cafe", "language": "he", "billing_exempt": False},
        get_activity=lambda *a, **k: [], get_communications=lambda *a, **k: [],
        log_activity=lambda *a, **k: None, _db=lambda: None, get_accounts=lambda cid: [],
        log_communication=lambda *a, **k: None),
    "agents.onboarding_agent": _module("agents.onboarding_agent", LANGUAGE_RULE="\n\nLANGUAGE.", PRICING={}),
    "core.automation_service": _module("core.automation_service", VENDORS={},
                                       POLICY_NOTE="Automation is quoted by hand.", SIGNUP_RULE_HE=""),
    "core.agent_base": _module("core.agent_base", agent_alert=lambda *a, **k: None,
                               log_step=lambda *a, **k: None, timed_step=lambda a, s, fn: fn()),
}
_SAVED = {name: sys.modules.get(name) for name in list(STUBS) + ["agents.support_agent"]}
sys.modules.update(STUBS)
sys.modules.pop("agents.support_agent", None)

from agents import support_agent as sa      # noqa: E402
from core import chat_route as cr           # noqa: E402
from core import chat_timing as ct          # noqa: E402
from core import client_state as cs         # noqa: E402
from core import model_routing as mr        # noqa: E402
from core import parallel                   # noqa: E402

HAIKU, SONNET, OPUS = mr.TIER_MODELS["simple"], mr.TIER_MODELS["standard"], mr.TIER_MODELS["heavy"]


def tearDownModule():
    for name, mod in _SAVED.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


# ─── A. the rules ───────────────────────────────────────────────────────────

class Rules(unittest.TestCase):
    def kind(self, message, last="", **k):
        return cr.classify(message, last, **k)["kind"]

    def test_simple_is_only_a_greeting_or_thanks(self):
        for m in ("תודה", "תודה רבה!", "היי", "סבבה 👍", "מעולה", "thanks", "ok", "בוקר טוב", "спасибо", ""):
            self.assertEqual(self.kind(m), "simple", m)

    def test_an_ok_to_something_we_offered_goes_up(self):
        for last in ("רוצה שאכין לך כרטיס?", "Want me to send it?", "אפשר להמשיך?"):
            self.assertEqual(self.kind("אוקיי", last), "work", last)
        self.assertEqual(self.kind("אוקיי", "סיימתי, הכול מוכן."), "simple")

    def test_yes_is_work(self):
        for m in ("כן", "כן בבקשה", "יאללה", "תעשה את זה", "yes", "go ahead"):
            self.assertEqual(self.kind(m), "work", m)

    def test_short_questions_are_read(self):
        for m in ("כמה לידים היו השבוע?", "מה המצב?", "איך הקמפיין בגוגל?", "מתי הדוח הבא?",
                  "how are my ads doing?"):
            self.assertEqual(self.kind(m), "read", m)

    def test_anything_that_does_something_is_work(self):
        for m in ("תעלה את הסרטון לטיקטוק", "תבנה לי דף נחיתה", "תשנה את התקציב", "אפשר לפרסם פוסט?",
                  "תחבר את החנות", "publish the video", "send it to tiktok", "תכתוב מאמר על קפה",
                  "upgrade my package"):
            self.assertEqual(self.kind(m), "work", m)

    def test_frustration_explicit_long_and_multi_step_are_work(self):
        self.assertEqual(self.kind("מה המצב?", frustrated=True), "work")
        self.assertEqual(self.kind("מה המצב?", explicit=True), "work")
        self.assertEqual(self.kind("שוב לא עובד!!"), "work")
        self.assertEqual(self.kind("מה " * 200 + "?"), "work")
        self.assertEqual(self.kind("1. בדוק\n2. תקן\n3. דווח"), "work")
        self.assertEqual(cr.classify("מה? למה? איך?")["reason"], "multi-step request")

    def test_doubt_goes_up_never_down(self):
        for m in ("הממ", "לגבי מה שדיברנו", "the thing from yesterday", "ראיתי משהו מעניין אצל מתחרה"):
            self.assertEqual(self.kind(m), "work", m)

    def test_tiers_and_the_kill_switch(self):
        self.assertEqual(cr.TIER_FOR, {"simple": "simple", "read": "standard", "work": "heavy"})
        saved = os.environ.get(mr.FLAG)
        try:
            os.environ[mr.FLAG] = "0"
            d = mr.chat_decision(cr.classify("תודה"))
            self.assertEqual((d["model"], d["kind"], d["source"]), (OPUS, "work", "flag_off"))
            os.environ[mr.FLAG] = "1"
            d = mr.chat_decision(cr.classify("תודה"))
            self.assertEqual((d["model"], d["kind"], d["source"]), (HAIKU, "simple", "rule"))
        finally:
            if saved is None:
                os.environ.pop(mr.FLAG, None)
            else:
                os.environ[mr.FLAG] = saved

    def test_no_model_is_named_in_the_rules(self):
        self.assertNotRegex(_src("core", "chat_route.py"), r"claude-(haiku|sonnet|opus)")


# ─── B. the chat turn ───────────────────────────────────────────────────────

class Turn(unittest.TestCase):
    def setUp(self):
        self.calls, self.loaded = [], []
        self.saved = {n: getattr(sa, n) for n in ("get_client", "_thread_context", "_load_context",
                                                  "safe_claude_json_call", "_guard_invented")}
        self.saved_db, self.saved_flag = mr._db, os.environ.get(mr.FLAG)
        os.environ[mr.FLAG] = "1"
        mr._db = lambda: types.SimpleNamespace(table=lambda n: types.SimpleNamespace(
            insert=lambda row: types.SimpleNamespace(execute=lambda: None)))
        history = [{"from": "client" if i % 2 == 0 else "uallak", "text": f"m{i}"} for i in range(12)]
        sa.get_client = lambda cid: {"id": cid, "name": "Cafe", "chat_started_at": ""}
        sa._thread_context = lambda cid, started, channel="dashboard_chat": (list(history), [])
        sa._guard_invented = lambda cid, p, m, reply, state, result: reply

        def load(*a, **k):
            self.loaded.append(a)
            return {"lead": {}, "client_knowledge": {}, "recent_activity": [], "suggestions": None,
                    "reads": {}, "store": {}, "account": {"account_state": {"x": 1}, "available_actions": []},
                    "state": {}, "failed": set()}
        sa._load_context = load
        self.answer = {"reply": "בכיף!", "proposed_action": {"name": "x"}, "web_search_query": "q",
                       "upgrade_request": "more"}

        def call(system, user, **k):
            import json
            self.calls.append((k["model"], json.loads(user)))
            return dict(self.answer)
        sa.safe_claude_json_call = call
        self.timing = ct.start(7, "general")

    def tearDown(self):
        ct.finish(self.timing)
        for n, fn in self.saved.items():
            setattr(sa, n, fn)
        mr._db = self.saved_db
        if self.saved_flag is None:
            os.environ.pop(mr.FLAG, None)
        else:
            os.environ[mr.FLAG] = self.saved_flag

    def test_a_thanks_reads_nothing_and_runs_on_the_cheap_tier(self):
        out = sa.answer_support_question(7, "תודה!")
        self.assertEqual(out["reply"], "בכיף!")
        self.assertEqual(self.loaded, [])                         # no account reads at all
        model, payload = self.calls[0]
        self.assertEqual(model, HAIKU)
        self.assertNotIn("account_state", payload)
        self.assertNotIn("available_actions", payload)
        self.assertEqual(len(payload["conversation_history"]), cr.SIMPLE_HISTORY_TURNS)
        self.assertIn("turn_note", payload)
        self.assertEqual(len(self.calls), 1)                      # no search, no router
        flags = ct.current()["flags"]
        self.assertEqual((flags["route"], flags["route_model"], flags["message_chars"]), ("simple", HAIKU, len("תודה!")))

    def test_a_simple_turn_never_acts_on_what_it_proposed(self):
        result = dict(self.answer)
        sa._strip_light_result(result)
        self.assertEqual(result, {"reply": "בכיף!"})

    def test_a_question_reads_everything_on_the_middle_tier(self):
        self.answer = {"reply": "יש 3 לידים"}
        sa.answer_support_question(7, "כמה לידים היו השבוע?")
        self.assertEqual(len(self.loaded), 1)
        model, payload = self.calls[0]
        self.assertEqual(model, SONNET)
        self.assertIn("account_state", payload)
        self.assertEqual(len(payload["conversation_history"]), 12)
        self.assertEqual(ct.current()["flags"]["route"], "read")

    def test_work_and_doubt_run_on_the_strongest_tier(self):
        self.answer = {"reply": "מכין"}
        for message in ("תעלה את הסרטון לטיקטוק", "כן", "משהו לא ברור"):
            self.calls.clear()
            sa.answer_support_question(7, message)
            self.assertEqual(self.calls[0][0], OPUS, message)

    def test_connection_help_is_always_a_full_turn(self):
        self.answer = {"reply": "הנה"}
        sa.answer_support_question(7, "תודה", help_connection="unknown_connection")
        self.assertEqual(self.calls[0][0], OPUS)
        self.assertEqual(len(self.loaded), 1)


class LoadsTogether(unittest.TestCase):
    def test_every_read_of_a_full_turn_runs_at_the_same_time(self):
        names = ("_latest_lead", "_client_knowledge", "_recent_activity", "_account_context",
                 "_add_store_reads", "_suggestions_block")
        saved = {n: getattr(sa, n) for n in names}
        nap = 0.25

        def slow(value):
            def fn(*a, **k):
                time.sleep(nap)
                return value
            return fn
        try:
            sa._latest_lead = slow({"proposal": {}})
            sa._client_knowledge = slow({"k": 1})
            sa._recent_activity = slow([])
            sa._suggestions_block = slow([{"title": "t"}])

            def account(cid, client, part, message):
                time.sleep(nap)
                part.update(account_state={"a": 1}, available_actions=[])
                return {"_failed": []}
            sa._account_context = account

            def store(cid, message, part):
                time.sleep(nap)
                part["store"] = {"orders": 2}
                return set()
            sa._add_store_reads = store
            started = time.perf_counter()
            ctx = sa._load_context(7, {}, "כמה מכרנו?", "general", slow({"google_ads_performance": {"x": 1}}),
                                   store=True, suggestions=True)
            took = time.perf_counter() - started
        finally:
            for n, fn in saved.items():
                setattr(sa, n, fn)
        self.assertLess(took, nap * 3)                 # 7 reads of 0.25 s: one wait, not seven
        self.assertEqual(ctx["store"], {"store": {"orders": 2}})
        self.assertEqual(ctx["reads"], {"google_ads_performance": {"x": 1}})
        self.assertEqual(ctx["account"]["account_state"], {"a": 1})
        self.assertEqual(ctx["suggestions"], [{"title": "t"}])

    def test_a_read_that_throws_is_a_failed_topic_not_an_error(self):
        saved = sa._account_context
        try:
            sa._account_context = lambda cid, client, part, message: {"_failed": []}

            def boom(cid):
                raise RuntimeError("provider down")
            ctx = sa._load_context(7, {}, "x", "media", boom, store=False, suggestions=False)
        finally:
            sa._account_context = saved
        if "media" in sa.PERSONA_TOPICS:
            self.assertIn(sa.PERSONA_TOPICS["media"], ctx["failed"])
        self.assertEqual(ctx["reads"], {})


# ─── C. core/parallel ───────────────────────────────────────────────────────

class Parallel(unittest.TestCase):
    def test_gather_runs_together_and_keeps_the_context(self):
        var = contextvars.ContextVar("v", default="none")
        var.set("turn-7")

        def task():
            time.sleep(0.2)
            return var.get()

        def boom():
            raise ValueError("x")
        started = time.perf_counter()
        out = parallel.gather({"a": task, "b": task, "c": task, "d": boom})
        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertEqual([out[k] for k in "abc"], [(True, "turn-7")] * 3)
        self.assertFalse(out["d"][0])
        self.assertIsInstance(out["d"][1], ValueError)

    def test_single_flight_shares_one_live_call(self):
        calls = []

        @parallel.single_flight
        def read(client_id, force_refresh=False):
            calls.append(client_id)
            time.sleep(0.2)
            return {"client": client_id}
        results = []
        threads = [threading.Thread(target=lambda: results.append(read(7))) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(calls, [7])
        self.assertEqual(results, [{"client": 7}] * 4)
        read(7)
        read(7, force_refresh=True)                 # not shared: an explicit refresh
        self.assertEqual(calls, [7, 7, 7])

    def test_single_flight_passes_the_error_to_every_waiter(self):
        @parallel.single_flight
        def read(client_id):
            time.sleep(0.1)
            raise RuntimeError("down")
        errors = []

        def ask():
            try:
                read(1)
            except RuntimeError as e:
                errors.append(str(e))
        threads = [threading.Thread(target=ask) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, ["down"] * 3)

    def test_the_platform_getters_are_shared(self):
        for path, fn in ((("agents", "google_ads_agent.py"), "def get_campaign_performance("),
                         (("agents", "meta_ads_agent.py"), "def get_campaign_performance("),
                         (("agents", "website_agent.py"), "def get_site_overview(")):
            src = _src(*path)
            before = src[:src.index(fn)].rstrip().splitlines()[-1]
            self.assertTrue(before.startswith("@single_flight"), path)


# ─── D. client_state in parallel ────────────────────────────────────────────

class Snapshot(unittest.TestCase):
    def test_sections_run_together_with_the_same_keys_and_failures(self):
        names = ["_channels", "_connections", "_tracking", "_google_campaigns", "_meta_campaigns", "_budget",
                 "_leads", "_approvals", "_website", "_landing_pages", "_media", "_media_folder", "_package",
                 "_site_security", "_site_updates", "_outreach", "_creative_tools", "_voice", "_team_requests",
                 "_store"]
        saved = {n: getattr(cs, n) for n in names}
        seen_channels = []
        try:
            for n in names:
                setattr(cs, n, (lambda name: lambda *a, **k: time.sleep(0.15) or {"s": name})(n))
            cs._tracking = lambda cid, channels: seen_channels.append(channels) or time.sleep(0.15) or {"t": 1}

            def bad(cid):
                raise RuntimeError("provider down")
            cs._voice = bad
            cs._cache.clear()
            started = time.perf_counter()
            state = cs.snapshot(9)
            took = time.perf_counter() - started
        finally:
            for n, fn in saved.items():
                setattr(cs, n, fn)
            cs._cache.clear()
        self.assertLess(took, 0.15 * 5)            # ~20 reads: channels, then the rest at once
        self.assertEqual(state["_failed"], ["voice"])
        self.assertEqual(seen_channels, [{"s": "_channels"}])
        keys = [k for k in state if not k.startswith("_") and k != "as_of"]
        self.assertEqual(keys, [s for s in cs.SECTIONS if s in keys])
        self.assertEqual(set(keys) | {"voice"}, set(cs.SECTIONS) - {"website_traffic"})


# ─── E. measurement + UI ────────────────────────────────────────────────────

class Measure(unittest.TestCase):
    def row(self, total, route, chars, persona="general", model=OPUS):
        return {"total_ms": total, "queue_ms": 10, "context_ms": total // 2, "model_ms": total // 3, "post_ms": 5,
                "model_calls": 1, "dominant": "context", "persona": persona,
                "stages": {"calls": [{"model": model}], "flags": {"route": route, "route_model": model,
                                                                   "message_chars": chars}}}

    def test_groups_by_route_persona_and_length(self):
        rows = [self.row(90000, "simple", 5, model=HAIKU), self.row(3000, "simple", 6, model=HAIKU),
                self.row(20000, "work", 80, "media"), {"total_ms": 70000, "stages": {}, "persona": "general"}]
        g = ct.grouped(rows)
        self.assertEqual(g["short_turns"], 2)
        self.assertEqual(g["short_over_60s_pct"], 50.0)
        simple = g["by_route"][f"simple:{HAIKU}"]
        self.assertEqual((simple["turns"], simple["median_ms"], simple["models"]), (2, 46500, {HAIKU: 2}))
        self.assertIn("—:—", g["by_route"])                 # rows from before the routing
        self.assertEqual(set(g["by_persona"]), {"general", "media"})
        self.assertEqual(set(g["by_length"]), {"short", "long", "unknown"})

    def test_route_rides_in_the_existing_jsonb_no_new_column(self):
        token = ct.start(3, "general")
        ct.flag("route", "simple")
        ct.flag("message_chars", 4)
        row = ct.finish(token)
        self.assertEqual(row["stages"]["flags"]["route"], "simple")
        self.assertNotIn("route", row)                     # a new top-level key would break the insert
        page = _src("dashboard", "admin", "models", "index.html")
        self.assertIn("groupsHtml(t.groups)", page)
        self.assertIn('<option value="2">48 שעות</option>', page)

    def test_the_long_wait_line_is_text_only(self):
        page = _src("dashboard", "client", "index.html")
        self.assertIn("chat_typing_long:", page)
        fn = page[page.index("const CHAT_LONG_WAIT_MS"):page.index("function chatTypingToBottom")]
        self.assertIn("w.typingEl.textContent = chatTypingText(w)", fn)
        for bad in ("appendChild", "insertBefore", "spinner"):
            self.assertNotIn(bad, fn)
        line = page[page.index("chat_typing_long:"):].split("\n", 1)[0]
        self.assertNotRegex(line, r"[A-Za-z]''[A-Za-z]")   # a doubled quote kills the whole page's script
        for vendor in ("Claude", "Anthropic", "Haiku", "Sonnet", "Opus", "צוות", "team"):
            self.assertNotIn(vendor, line)


if __name__ == "__main__":
    unittest.main()
