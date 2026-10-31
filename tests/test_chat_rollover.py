"""Daily chat rollover (core/chat_rollover, 2026-11-01).

Rollover creates a summary and archives (never deletes) the history; a
client active in the last 30 minutes is deferred; pending approval cards
carry over; a summary failure keeps the old window and alerts; a double run
is idempotent; the new window's context is only the summaries; 08:00 is
Israel time across DST; the flag stops it.

Stdlib only: an in-memory Supabase fake; the summarizer's model call is a fake.
Run from the repo root:  python tests/test_chat_rollover.py -v
"""
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ─── An in-memory Supabase ───────────────────────────────────────────────────

class Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self._order, self._limit, self._insert = db, table, [], None, None, None

    def select(self, *a, **k):
        return self

    def _f(self, fn):
        self.filters.append(fn)
        return self

    def eq(self, c, v):
        return self._f(lambda r: r.get(c) == v)

    def gte(self, c, v):
        return self._f(lambda r: (r.get(c) or "") >= v)

    def lt(self, c, v):
        return self._f(lambda r: (r.get(c) or "") < v)

    def like(self, c, pattern):
        prefix = pattern.rstrip("%")
        return self._f(lambda r: (r.get(c) or "").startswith(prefix))

    def order(self, c, desc=False):
        self._order = (c, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def insert(self, row):
        self._insert = dict(row)
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self._insert is not None:
            self.db.writes.append(self.table)
            if self.table == "chat_summaries":
                key = (self._insert["client_id"], self._insert["channel"], self._insert["window_date"])
                if any((r["client_id"], r["channel"], r["window_date"]) == key for r in rows):
                    raise RuntimeError("duplicate key value violates unique constraint")
            rows.append(self._insert)
            return types.SimpleNamespace(data=[self._insert])
        out = [r for r in rows if all(f(r) for f in self.filters)]
        if self._order:
            out.sort(key=lambda r: r.get(self._order[0]) or "", reverse=self._order[1])
        if self._limit is not None:
            out = out[:self._limit]
        return types.SimpleNamespace(data=[dict(r) for r in out])


class FakeDB:
    def __init__(self):
        self.tables, self.writes = {}, []

    def table(self, name):
        return Query(self, name)


DB = FakeDB()
ALERTS, CARDS, CLIENTS = [], {}, {}


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


sys.modules["agents.client_agent"] = _module("agents.client_agent", _db=lambda: DB,
                                             get_client=lambda cid: CLIENTS.get(cid, {}))
sys.modules["core.agent_base"] = _module("core.agent_base", agent_alert=lambda name, issues: ALERTS.append(issues))
sys.modules["core.agent_actions"] = _module(
    "core.agent_actions", open_cards=lambda client_id, actor, persona=None: CARDS.get((client_id, persona), []))
if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = _module("anthropic", Anthropic=lambda **k: None)
for name in ("core.chat_rollover", "core.operator_flags", "core.claude_json"):
    sys.modules.pop(name, None)
from core import chat_rollover as cr    # noqa: E402
from core import claude_json as cj      # noqa: E402

UTC = timezone.utc
# 2026-11-02 06:30 UTC = 08:30 in Israel (winter time, UTC+2)
NOW = datetime(2026, 11, 2, 6, 30, tzinfo=UTC)
SUMMARY = {"decisions": ["להעלות תקציב גוגל ל-50 ₪ ביום"], "pending_client_approval": ["אישור מודעה חדשה"],
           "client_preferences": ["לא אוהב אימוג'ים"], "actions_completed": ["חובר חשבון גוגל"],
           "open_questions": ["האם לפרסם גם בשבת?"]}


def msg(client_id, channel, direction, minutes_ago, text="שלום"):
    DB.tables.setdefault("client_communications", []).append(
        {"client_id": client_id, "channel": channel, "direction": direction, "content": text,
         "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat()})


class Base(unittest.TestCase):
    def setUp(self):
        DB.tables.clear()
        DB.writes.clear()
        ALERTS.clear()
        CARDS.clear()
        CLIENTS.clear()
        CLIENTS.update({1: {"id": 1}, 2: {"id": 2}})
        os.environ.pop(cr.FLAG, None)
        self.calls = []
        self.saved = cr.summarize
        cr.summarize = self.fake_summarize

    def tearDown(self):
        cr.summarize = self.saved
        os.environ.pop(cr.FLAG, None)

    def fake_summarize(self, client_id, previous, messages, pending):
        self.calls.append({"client_id": client_id, "previous": previous, "messages": messages, "pending": pending})
        return dict(SUMMARY), {"model": "claude-haiku-5-5", "input": 900, "output": 120, "ils": 0.0005}

    def run_job(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return cr.run(now)

    def summaries(self):
        return DB.tables.get("chat_summaries", [])


class Rollover(Base):
    def test_creates_summary_and_archives_history_without_deleting(self):
        for i, (d, t) in enumerate([("inbound", "מה המצב?"), ("outbound", "הכול טוב"), ("inbound", "תודה")]):
            msg(1, "dashboard_chat:google", d, 600 - i, t)
        before = [dict(r) for r in DB.tables["client_communications"]]
        report = self.run_job()
        self.assertEqual(report["rolled"], 1)
        row = self.summaries()[0]
        self.assertEqual((row["status"], row["channel"], row["window_date"]), ("done", "dashboard_chat:google", "2026-11-02"))
        self.assertEqual(row["summary"], SUMMARY)
        self.assertEqual(row["messages_archived"], 3)
        self.assertEqual(row["window_started_at"], NOW.isoformat())
        self.assertEqual((row["input_tokens"], row["output_tokens"], row["cost_ils"]), (900, 120, 0.0005))
        self.assertGreater(row["history_chars"], 0)
        self.assertGreater(row["context_chars"], 0)
        # archived = still there, unchanged; the only write is the summary row
        self.assertEqual(DB.tables["client_communications"], before)
        self.assertEqual(DB.writes, ["chat_summaries"])
        self.assertEqual([m["content"] for m in self.calls[0]["messages"]], ["מה המצב?", "הכול טוב", "תודה"])

    def test_new_window_context_is_only_the_summaries(self):
        msg(1, "dashboard_chat", "inbound", 600, "שאלה ישנה")
        self.run_job()
        w = cr.window(1, "dashboard_chat", None)
        self.assertEqual(w["start"], NOW.isoformat())
        self.assertTrue(w["rolled_over"])
        self.assertEqual(w["summaries"], [{"date": "2026-11-02", **SUMMARY}])
        # nothing from before the window reaches the thread
        thread = [m for m in DB.tables["client_communications"]
                  if m["channel"] == "dashboard_chat" and m["created_at"] >= w["start"]]
        self.assertEqual(thread, [])

    def test_context_is_the_latest_plus_at_most_two_earlier(self):
        for day in range(1, 6):
            DB.tables.setdefault("chat_summaries", []).append(
                {"client_id": 1, "channel": "dashboard_chat", "window_date": f"2026-10-0{day}", "status": "done",
                 "window_started_at": f"2026-10-0{day}T06:00:00+00:00", "summary": {"decisions": [f"d{day}"]}})
        w = cr.window(1, "dashboard_chat", None)
        self.assertEqual([s["date"] for s in w["summaries"]], ["2026-10-03", "2026-10-04", "2026-10-05"])
        self.assertEqual(w["start"], "2026-10-05T06:00:00+00:00")

    def test_a_later_manual_new_chat_wins_but_keeps_the_memory(self):
        msg(1, "dashboard_chat", "inbound", 600)
        self.run_job()
        later = (NOW + timedelta(hours=2)).isoformat()
        w = cr.window(1, "dashboard_chat", later)
        self.assertEqual((w["start"], w["rolled_over"]), (later, False))
        self.assertEqual(len(w["summaries"]), 1)

    def test_active_in_last_30_minutes_is_deferred_then_rolled_when_idle(self):
        msg(1, "dashboard_chat", "inbound", 10)
        msg(2, "dashboard_chat", "inbound", 300)
        report = self.run_job()
        self.assertEqual((report["deferred"], report["rolled"]), (1, 1))
        self.assertEqual([r["client_id"] for r in self.summaries()], [2])
        report = self.run_job(NOW + timedelta(minutes=31))
        self.assertEqual((report["rolled"], report["already"]), (1, 1))
        self.assertEqual(sorted(r["client_id"] for r in self.summaries()), [1, 2])

    def test_pending_approval_cards_carry_over(self):
        CARDS[(1, "meta")] = [{"id": 7, "title": "אישור מודעה", "what": "מודעת סתיו", "status": "pending"}]
        msg(1, "dashboard_chat:meta", "inbound", 600)
        self.run_job()
        self.assertEqual(self.calls[0]["pending"], [{"title": "אישור מודעה", "what": "מודעת סתיו"}])
        # the cards themselves are untouched and are not tied to the window
        self.assertEqual(CARDS[(1, "meta")][0]["status"], "pending")
        self.assertNotIn("agent_action", " ".join(DB.writes))
        with open(os.path.join(ROOT, "core", "agent_actions.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def open_cards"):src.index("def status_card")]
        self.assertNotIn("chat_started_at", body)
        self.assertNotIn("window", body)

    def test_summary_failure_keeps_the_old_window_and_alerts(self):
        tries = []

        def broken(*a):
            tries.append(1)
            raise RuntimeError("model down")
        cr.summarize = broken
        msg(1, "dashboard_chat", "inbound", 600)
        report = self.run_job()
        self.assertEqual(report["failed"], 1)
        self.assertEqual(len(tries), 2)                     # retried once
        self.assertEqual(self.summaries()[0]["status"], "failed")
        self.assertIsNone(self.summaries()[0].get("window_started_at"))
        self.assertEqual(len(ALERTS), 1)
        self.assertIn("NOT rolled over", ALERTS[0][0])
        w = cr.window(1, "dashboard_chat", None)
        self.assertEqual((w["start"], w["rolled_over"], w["summaries"]), (None, False, []))
        # no retry storm and no second alert the same day
        self.assertEqual(self.run_job(NOW + timedelta(hours=1))["already"], 1)
        self.assertEqual((len(tries), len(ALERTS)), (2, 1))

    def test_double_run_is_idempotent(self):
        msg(1, "dashboard_chat", "inbound", 600)
        msg(1, "dashboard_chat:website", "inbound", 600)
        first = self.run_job()
        second = self.run_job(NOW + timedelta(minutes=5))
        self.assertEqual((first["rolled"], second["rolled"], second["already"]), (2, 0, 2))
        self.assertEqual(len(self.summaries()), 2)
        self.assertEqual(len(self.calls), 2)

    def test_next_day_carries_the_previous_summary_forward(self):
        msg(1, "dashboard_chat", "inbound", 600)
        self.run_job()
        DB.tables["client_communications"].append(
            {"client_id": 1, "channel": "dashboard_chat", "direction": "inbound", "content": "עוד שאלה",
             "created_at": (NOW + timedelta(hours=3)).isoformat()})
        report = self.run_job(NOW + timedelta(days=1))
        self.assertEqual(report["rolled"], 1)
        self.assertEqual(self.calls[1]["previous"]["decisions"], SUMMARY["decisions"])
        self.assertEqual([m["content"] for m in self.calls[1]["messages"]], ["עוד שאלה"])

    def test_no_client_message_no_rollover(self):
        msg(1, "dashboard_chat:media", "outbound", 600, "הצעה מהסוכן")
        report = self.run_job()
        self.assertEqual(report["rolled"], 0)
        self.assertEqual(self.summaries(), [])

    def test_other_channels_are_not_chat_windows(self):
        msg(1, "dashboard_chat_upload", "inbound", 600)
        msg(1, "email", "inbound", 600)
        self.assertEqual(self.run_job()["rolled"], 0)


class Timing(Base):
    def test_before_0800_israel_does_nothing(self):
        msg(1, "dashboard_chat", "inbound", 600)
        report = self.run_job(datetime(2026, 11, 2, 5, 30, tzinfo=UTC))      # 07:30 Israel
        self.assertEqual(report["skipped"], "before_0800")
        self.assertEqual(self.summaries(), [])

    def test_dst_summer_and_winter(self):
        self.assertEqual(cr.israel_now(datetime(2026, 7, 1, 5, 0, tzinfo=UTC)).hour, 8)    # IDT +3
        self.assertEqual(cr.israel_now(datetime(2026, 12, 1, 6, 0, tzinfo=UTC)).hour, 8)   # IST +2
        # the fallback rule (no tz database) agrees around both switches of 2026
        self.assertEqual(cr._israel_offset_hours(datetime(2026, 3, 26, 23, 0, tzinfo=UTC)), 2)
        self.assertEqual(cr._israel_offset_hours(datetime(2026, 3, 27, 1, 0, tzinfo=UTC)), 3)
        self.assertEqual(cr._israel_offset_hours(datetime(2026, 10, 24, 22, 0, tzinfo=UTC)), 3)
        self.assertEqual(cr._israel_offset_hours(datetime(2026, 10, 25, 0, 0, tzinfo=UTC)), 2)

    def test_flag_off_stops_it(self):
        os.environ[cr.FLAG] = "0"
        msg(1, "dashboard_chat", "inbound", 600)
        self.assertEqual(self.run_job(), {"skipped": "flag_off"})
        self.assertEqual(self.summaries(), [])
        from core import operator_flags
        self.assertTrue(operator_flags.FLAGS[cr.FLAG]["default"])


class Summarizer(unittest.TestCase):
    def test_uses_the_cheap_model_redacts_and_records_cost(self):
        seen = {}

        def fake(system, user, **k):
            seen.update(user=user, **k)
            cj._collect(k["model"], 800, 100, 0, 0, 0.0004)
            return {**SUMMARY, "decisions": ["key sk-abcdefghijklmnopqrstuvwxyz123"]}
        saved = cj.safe_claude_json_call
        cj.safe_claude_json_call = fake
        try:
            summary, usage = cr.summarize(1, {}, [{"direction": "inbound", "content": "הנה sk-abcdefghijklmnopqrstuvwxyz999",
                                                   "created_at": "2026-11-01T10:00:00+00:00"}], [])
        finally:
            cj.safe_claude_json_call = saved
        self.assertEqual(seen["model"], "claude-haiku-5-5")
        self.assertEqual(seen["cost_category"], "chat_rollover_summary")
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz999", seen["user"])
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123", summary["decisions"][0])
        self.assertEqual(usage, {"model": "claude-haiku-5-5", "input": 800, "output": 100, "ils": 0.0004})
        self.assertEqual(set(summary), set(cr.SUMMARY_KEYS))

    def test_malformed_summary_is_a_failure(self):
        for bad in ("text", ["a"], {"decisions": "not a list"}):
            with self.assertRaises(cr.SummaryError):
                cr._clean(bad)


class Wiring(unittest.TestCase):
    def _src(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_agents_and_history_use_the_window(self):
        s = self._src("agents", "support_agent.py")
        self.assertEqual(s.count("_thread_context("), 3)          # def + concierge + persona
        self.assertIn('payload["previous_days"] = previous_days', s)
        api = self._src("core", "api_server.py")
        self.assertIn("chat_rollover.window(client_id, channel,", api)
        self.assertIn('"rolled_over": window["rolled_over"]', api)
        route = api[api.index('@app.get("/api/chat/rollover"'):][:200]
        self.assertIn("dependencies=_admin_only", route)

    def test_dashboard_shows_one_quiet_line(self):
        page = self._src("dashboard", "client", "index.html")
        self.assertIn("chat_new_day: { he:'יום חדש, הכול שמור'", page)
        self.assertIn("chat_prev: { he:'הצג שיחות קודמות'", page)
        self.assertIn("if (data.rolled_over) {", page)

    def test_scheduler_script(self):
        script = self._src("scripts", "schedule_chat_rollover.sh")
        self.assertIn('"0 8 * * *"', script)
        self.assertIn('TZ_NAME="Asia/Jerusalem"', script)
        self.assertNotIn("SECRETS_ENCRYPTION_KEY", script)
        self.assertIn("unset AK", script)


if __name__ == "__main__":
    unittest.main()
