"""Measured time estimates and the chat prewarm (2026-11-15,
docs/CHAT-SPEED.md §Time estimates, §Prewarm; docs/SYSTEM-REVIEW.md Part 2).

A. `about()` says a duration the way a person does, in all five languages.
B. An estimate exists only with enough measured history; failures = no number.
C. A running card's progress text gains the measured sentence, else is unchanged.
D. Prewarm: the same readers as a turn, no model, no write, rate-limited,
   never two at once, a kill switch, session-gated routes.
E. The dashboard: prewarm on focus / typing, the measured long-wait line with
   the fixed line as fallback, text only.
F. The fixed technical line no longer says "passed to the team".

Stdlib only. Run from tests/:  python -I -B test_task_eta.py -v
"""
import os
import sys
import threading
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import chat_prewarm, task_eta   # noqa: E402  (stdlib-only at import)


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class About(unittest.TestCase):
    def test_plain_words_in_hebrew(self):
        self.assertEqual([task_eta.about(s, "he") for s in (5, 30, 75, 120, 290, 800, 4000)],
                         ["כמה שניות", "כחצי דקה", "כדקה", "כשתי דקות", "כ-5 דקות", "כ-15 דקות", "כשעה"])

    def test_every_language_has_every_phrase(self):
        for table in list(task_eta._ABOUT.values()) + [task_eta.USUALLY, task_eta.CHAT_LONG]:
            self.assertEqual(set(table), set(task_eta.LANGS))
        for lang in task_eta.LANGS:
            for s in (5, 30, 75, 120, 290, 4000):
                self.assertTrue(task_eta.about(s, lang))
                self.assertNotIn("{", task_eta.about(s, lang))

    def test_unknown_language_reads_english_and_bad_input_is_empty(self):
        self.assertEqual(task_eta.about(120, "xx"), "about 2 minutes")
        self.assertEqual([task_eta.about(v) for v in (None, 0, -3, "abc")], ["", "", "", ""])


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        return types.SimpleNamespace(data=self.rows)


class _DB:
    def __init__(self, rows=None, broken=False):
        self.rows, self.broken, self.reads = rows or [], broken, 0

    def table(self, _name):
        self.reads += 1
        if self.broken:
            raise RuntimeError("down")
        return _Q(self.rows)


def _runs(seconds_list):
    return [{"decided_at": "2026-11-10T10:00:00+00:00",
             "finished_at": f"2026-11-10T10:{s // 60:02d}:{s % 60:02d}+00:00"} for s in seconds_list]


class Measured(unittest.TestCase):
    def setUp(self):
        self.saved = task_eta._db
        task_eta._cache.clear()

    def tearDown(self):
        task_eta._db = self.saved
        task_eta._cache.clear()

    def test_median_of_real_runs(self):
        task_eta._db = lambda: _DB(_runs([100, 110, 120, 130, 900]))
        self.assertEqual(task_eta.action_seconds("create_landing_page"), 120)
        self.assertEqual(task_eta.usually("create_landing_page", "en"), "It usually takes about 2 minutes.")

    def test_too_little_history_gives_no_number(self):
        task_eta._db = lambda: _DB(_runs([100, 110, 120, 130]))
        self.assertIsNone(task_eta.action_seconds("x"))
        self.assertEqual(task_eta.usually("x", "he"), "")

    def test_stuck_and_broken_rows_are_not_durations(self):
        rows = _runs([60]) + [{"decided_at": "2026-11-10T10:00:00+00:00", "finished_at": "2026-11-12T10:00:00+00:00"},
                              {"decided_at": None, "finished_at": "2026-11-10T10:00:00+00:00"},
                              {"decided_at": "2026-11-10T10:05:00+00:00", "finished_at": "2026-11-10T10:00:00+00:00"}]
        self.assertEqual(task_eta.durations(rows), [60.0])

    def test_a_failed_read_is_no_estimate_and_is_cached(self):
        db = _DB(broken=True)
        task_eta._db = lambda: db
        self.assertIsNone(task_eta.action_seconds("x"))
        self.assertIsNone(task_eta.action_seconds("x"))
        self.assertEqual(db.reads, 1)

    def test_chat_pace(self):
        task_eta._db = lambda: _DB([{"total_ms": ms, "outcome": "answered"} for ms in (40000, 60000, 80000, 70000, 50000)])
        pace = task_eta.chat_pace("he")
        self.assertEqual((pace["measured"], pace["usual_seconds"]), (True, 60))
        self.assertIn("כדקה", pace["long_wait_text"])
        task_eta._cache.clear()
        task_eta._db = lambda: _DB([])
        self.assertEqual(task_eta.chat_pace("he"), {"measured": False})


class CardText(unittest.TestCase):
    def test_progress_text_appends_only_a_measured_sentence(self):
        src = _src("core", "agent_actions.py")
        body = src[src.index("def progress_text"):src.index("def card_choices")]
        self.assertIn("task_eta.usually(name, lang)", body)
        self.assertIn("if usual else text", body)          # no history = the text exactly as before


class Prewarm(unittest.TestCase):
    def setUp(self):
        self.saved = (chat_prewarm._reads, chat_prewarm.enabled)
        chat_prewarm._last.clear(); chat_prewarm._running.clear()
        chat_prewarm.enabled = lambda: True
        self.calls = []
        chat_prewarm._reads = lambda cid: self.calls.append(cid) or {"snapshot": (True, {})}

    def tearDown(self):
        chat_prewarm._reads, chat_prewarm.enabled = self.saved
        chat_prewarm._last.clear(); chat_prewarm._running.clear()

    def test_runs_once_per_interval_per_client(self):
        self.assertTrue(chat_prewarm.run(27)["ran"])
        self.assertEqual(chat_prewarm.run(27), {"ran": False, "why": "recent"})
        self.assertTrue(chat_prewarm.run(28)["ran"])
        self.assertEqual(self.calls, [27, 28])

    def test_never_two_at_once_for_one_client(self):
        gate, started = threading.Event(), threading.Event()

        def slow(cid):
            started.set()
            gate.wait(2)
            return {}
        chat_prewarm._reads = slow
        t = threading.Thread(target=chat_prewarm.run, args=(27,))
        t.start()
        started.wait(2)
        chat_prewarm._last.clear()                           # even past the interval
        self.assertEqual(chat_prewarm.run(27), {"ran": False, "why": "already_running"})
        gate.set(); t.join(2)

    def test_kill_switch_and_never_raises(self):
        chat_prewarm.enabled = lambda: False
        self.assertEqual(chat_prewarm.run(27), {"ran": False, "why": "off"})
        chat_prewarm.enabled = lambda: True

        def boom(cid):
            raise RuntimeError("vendor down")
        chat_prewarm._reads = boom
        out = chat_prewarm.run(29)
        self.assertTrue(out["ran"])
        self.assertNotIn(29, chat_prewarm._running)

    def test_same_readers_as_a_turn_no_model_no_write(self):
        src = _src("core", "chat_prewarm.py")
        self.assertIn("support_agent._manager_platform_reads(client_id)", src)
        self.assertIn("client_state.snapshot(client_id)", src)
        code = src.split('"""', 2)[2]
        for banned in ("claude", "anthropic", ".insert(", ".update(", ".upsert(", ".delete(", "log_communication",
                       "log_activity"):
            self.assertNotIn(banned, code, banned)

    def test_routes_are_session_gated_and_in_the_parity_table(self):
        api = _src("core", "api_server.py")
        for route in ('@app.post("/api/client-chat/prewarm")', '@app.get("/api/client-chat/pace")'):
            body = api[api.index(route):][:700]
            self.assertIn("_require_session(request)", body)
        parity = _src("core", "chat_parity.py")
        self.assertIn('"POST /api/client-chat/prewarm"', parity)
        self.assertIn('"GET /api/client-chat/pace"', parity)
        # staff "view as client" may read the pace line but never trigger reads for the client
        from core import admin_view
        self.assertIn("client_chat_pace", admin_view.READ_ENDPOINTS)
        self.assertNotIn("client_chat_prewarm", admin_view.READ_ENDPOINTS)

    def test_no_cloud_run_setting_is_touched(self):
        for path in (("core", "chat_prewarm.py"), ("core", "task_eta.py")):
            code = _src(*path).split('"""', 2)[2]
            for banned in ("min-instances", "min_instances", "cpu-throttling", "gcloud"):
                self.assertNotIn(banned, code)


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.page = _src("dashboard", "client", "index.html")

    def test_prewarm_on_focus_and_typing_rate_limited(self):
        wire = self.page[self.page.index("function wireChatInput"):self.page.index("function chatPaceLoad")]
        self.assertIn("el.addEventListener('focus', chatPrewarm)", wire)
        self.assertIn("chatPrewarm(); });", wire)
        self.assertIn("CHAT_PREWARM_EVERY_MS = 90000", wire)
        self.assertIn("document.hidden", wire)
        self.assertIn(".catch(() => {})", wire)             # never an error in front of the client

    def test_long_wait_line_measured_with_fixed_fallback_text_only(self):
        fn = self.page[self.page.index("function chatTypingText"):self.page.index("function chatPendingStart")]
        self.assertIn("chatPace.text", fn)
        self.assertIn("'chat_typing_long'", fn)
        self.assertNotIn("innerHTML", fn)
        self.assertNotIn("appendChild", fn)


class TechnicalLine(unittest.TestCase):
    def test_no_passing_to_the_team(self):
        from core import chat_fallback
        self.assertEqual(set(chat_fallback._TECHNICAL_OPENING), set(chat_fallback.SUPPORTED_LANGUAGES))
        for lang, line in chat_fallback._TECHNICAL_OPENING.items():
            self.assertFalse(chat_fallback.has_team_routing(line), lang)
            for word in ("צוות", "team", "équipe", "الفريق", "команд"):
                self.assertNotIn(word, line, lang)


if __name__ == "__main__":
    unittest.main()
