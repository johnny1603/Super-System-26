"""איציק's wider reads, notebook, progress line and self-description
(2026-11-15, docs/OPERATOR.md §Insight, docs/SYSTEM-REVIEW.md Part 1).

A. The three tiers are unchanged: the new tools are reads or his own notebook,
   the new alert action is owner-tap only, code + deploy still need the gate.
B. integration_health: duplicates and recorded-dead grants, never a secret.
C. alert_triage: groups by key, the works_now hint, the group action closes
   only what is still open and never an audit row.
D. cost_report / scheduler_jobs: totals from our own rows; unmanaged routes.
E. Notebook + ideas: saved, clipped, deduped, comes back as a prompt block.
F. "What can you do?" is answered by code (no model call) and lists the tiers.
G. Progress: a plain-words line, a time estimate only from measured history,
   the route is admin-only, the chat polls it.

Stdlib only. Run from tests/:  python -I -B test_operator_insight.py -v
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_operator as T                         # noqa: E402  (stubs client_agent + email)

if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = types.ModuleType("anthropic")
    sys.modules["anthropic"].Anthropic = lambda **k: None
from core import alert_hygiene, operator_actions, operator_insight as oi  # noqa: E402
from core import claude_json, cost_tracker, operator_agent as op          # noqa: E402


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload, self._limit = db, table, [], "select", None, None

    def select(self, *_a):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val); return self

    def in_(self, col, values):
        values = set(values)
        self.filters.append(lambda r: r.get(col) in values); return self

    def like(self, col, pattern):
        prefix = pattern.rstrip("%")
        self.filters.append(lambda r: str(r.get(col, "")).startswith(prefix)); return self

    def gte(self, col, val):
        self.filters.append(lambda r: str(r.get(col, "")) >= val); return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._limit = n; return self

    def update(self, payload):
        self.op, self.payload = "update", payload; return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", row; return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            for r in rows:
                if r.get("key") == self.payload["key"]:
                    r.update(self.payload)
                    return types.SimpleNamespace(data=[r])
            rows.append(dict(self.payload))
            return types.SimpleNamespace(data=[self.payload])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return types.SimpleNamespace(data=[dict(r) for r in hit])
        return types.SimpleNamespace(data=hit[: self._limit] if self._limit else hit)


class Fake:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _Q(self, name)


class Base(unittest.TestCase):
    def setUp(self):
        self.db = Fake()
        self._patch(oi, "_db", lambda: self.db)

    def _patch(self, obj, name, value):
        saved = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(lambda: setattr(obj, name, saved))


# ─── A. Tiers unchanged ──────────────────────────────────────────────────────

class Tiers(unittest.TestCase):
    NEW_READS = ("integration_health", "alert_triage", "cost_report", "speed_report", "scheduler_jobs",
                 "error_summary", "improvement_ideas")

    def test_new_tools_exist_with_labels(self):
        names = {t["name"] for t in op.TOOLS}
        for name in self.NEW_READS + ("check_client_connections", "task_note", "suggest_improvement"):
            self.assertIn(name, names)
            self.assertIn(name, op.TOOL_LABELS_HE)

    def test_change_tools_are_exactly_as_before(self):
        self.assertEqual(set(op.CHANGE_TOOLS), {"propose_code_change", "run_reaper"})
        self.assertEqual(set(op.STATUS_TOOLS), {"cloud_run_status", "recent_alerts", "stuck_actions"})

    def test_the_group_action_is_owner_tap_only(self):
        self.assertFalse(operator_actions.ACTIONS["resolve_alert_group"]["by_agent"])
        with self.assertRaises(operator_actions.ActionError) as ctx:
            operator_actions.run("resolve_alert_group", {"key": "a" * 24}, "agent", by_agent=True)
        self.assertEqual(ctx.exception.code, "ERR_OWNER_TAP_ONLY")

    def test_the_live_check_only_reads(self):
        self.assertTrue(operator_actions.ACTIONS["check_client_connections"]["by_agent"])
        body = _src("core", "operator_actions.py")
        body = body[body.index("def _check_client_connections"):body.index("def _mark_request_handled")]
        for write in (".insert(", ".update(", ".delete(", "upsert_account", "remove_accounts"):
            self.assertNotIn(write, body)

    def test_insight_never_calls_a_vendor_or_reads_a_secret(self):
        src = _src("core", "operator_insight.py")
        for banned in ("access_token", "hydrate(", "get_client_secret", "httpx", "requests.", "secret_last4"):
            self.assertNotIn(banned, src, banned)
        # the only tables it writes are its own notebook rows
        self.assertEqual(set(re.findall(r'_put\((\w+),', src)), {"MEMORY_KEY", "IDEAS_KEY"})

    def test_code_and_deploy_still_go_through_the_gate(self):
        self.assertIn("operator_gate", _src("core", "operator_changes.py"))
        self.assertNotIn("operator_changes", _src("core", "operator_insight.py"))
        self.assertIn("NOTHING runs", next(t for t in op.TOOLS if t["name"] == "suggest_improvement")["description"])


# ─── B. Integration health ───────────────────────────────────────────────────

class Health(Base):
    def setUp(self):
        super().setUp()
        self.db.tables["client_accounts"] = [
            {"id": 1, "client_id": 27, "platform": "google_tagmanager", "status": "active", "access_token": "SECRET"},
            {"id": 2, "client_id": 27, "platform": "google_tagmanager", "status": "active", "access_token": "SECRET"},
            {"id": 3, "client_id": 27, "platform": "google_ads", "status": "active"},
            {"id": 4, "client_id": 30, "platform": "tiktok", "status": "active"},
        ]
        self.db.tables["app_settings"] = [
            {"key": "gads_token:27", "value": {"ok": False, "reason": "invalid_grant", "grant": "fp123"}},
            {"key": "gtm_token:27", "value": {"ok": True, "reason": ""}},
            {"key": "tiktok_grant:30", "value": {"state": "ok", "scopes": ["video.upload"]}},
        ]

    def test_one_client_shows_duplicates_and_the_dead_grant(self):
        out = oi.integration_health(27)
        self.assertEqual(out["duplicates"], ["google_tagmanager"])
        self.assertEqual(out["health"]["google_ads"]["state"], "needs_reconnect")
        self.assertEqual(out["health"]["google_tagmanager"]["state"], "ok")
        self.assertEqual(len(out["needs_attention"]), 2)

    def test_all_clients_lists_only_the_ones_needing_attention(self):
        out = oi.integration_health()
        self.assertEqual(out["clients"], 2)
        self.assertEqual([c["client_id"] for c in out["attention"]], [27])

    def test_nothing_secret_leaves(self):
        text = str(oi.integration_health(27)) + str(oi.integration_health())
        self.assertNotIn("SECRET", text)
        self.assertNotIn("fp123", text)


# ─── C. Alert triage ─────────────────────────────────────────────────────────

class Triage(Base):
    def setUp(self):
        super().setUp()
        self._patch(alert_hygiene, "_audit_sources", lambda: ("operator", "credits"))
        self.db.tables["alerts"] = [
            {"id": 1, "source": "performance", "status": "open", "created_at": "2026-11-13T08:00:00",
             "issues": ["client 27: Performance failed: tagmanager HTTP 401 (card shows: reconnect)"]},
            {"id": 2, "source": "performance", "status": "open", "created_at": "2026-11-14T08:00:00",
             "issues": ["client 27: Performance failed: tagmanager HTTP 401 (card shows: reconnect)"]},
            {"id": 3, "source": "lead_tracking", "status": "open", "created_at": "2026-11-14T09:00:00",
             "issues": ["client 27: no sales lead linked by client_id"]},
            {"id": 4, "source": "operator", "status": "open", "created_at": "2026-11-14T09:00:00",
             "issues": ["tier2 clear_cache START by owner"]},
            {"id": 5, "source": "performance", "status": "resolved", "created_at": "2026-11-12T08:00:00",
             "issues": ["client 27: Performance failed: tagmanager HTTP 401 (card shows: reconnect)"]},
        ]
        self.db.tables["app_settings"] = [{"key": "gtm_token:27", "value": {"ok": True}}]
        self.db.tables["client_accounts"] = [{"id": 1, "client_id": 27, "platform": "google_tagmanager",
                                              "status": "active"}]

    def test_groups_and_hints(self):
        out = oi.alert_triage()
        self.assertEqual(out["open_alerts"], 3)          # the audit row and the resolved one are not counted
        top = out["top"][0]
        self.assertEqual((top["count"], top["clients"], top["hint"]), (2, [27], "works_now"))
        self.assertEqual(sorted(top["ids"]), [1, 2])

    def test_the_group_action_closes_only_that_group(self):
        from core import admin_service
        self._patch(admin_service, "_db", lambda: self.db)
        key = oi.alert_triage()["top"][0]["key"]
        out = operator_actions._resolve_alert_group({"key": key})
        self.assertEqual(out["resolved"], 2)
        status = {r["id"]: r["status"] for r in self.db.tables["alerts"]}
        self.assertEqual(status, {1: "resolved", 2: "resolved", 3: "open", 4: "open", 5: "resolved"})

    def test_a_bad_key_is_refused(self):
        with self.assertRaises(operator_actions.ActionError):
            operator_actions._resolve_alert_group({"key": "'; drop"})


# ─── D. Cost report + scheduler ──────────────────────────────────────────────

class Reports(Base):
    def test_cost_report_totals(self):
        self._patch(cost_tracker, "_db", lambda: self.db)
        now = oi._now().isoformat()
        self.db.tables["client_costs"] = [
            {"client_id": 27, "category": "claude_chat", "cost_category": "chat", "created_at": now,
             "details": {"usd": 0.5, "model": "m-heavy", "action": "chat_turn"}},
            {"client_id": None, "category": "claude_operator", "cost_category": "infra", "attribution": "internal",
             "created_at": now, "details": {"usd": 0.25, "model": "m-standard"}},
            {"client_id": 27, "category": cost_tracker.CLIENT_PAID_MEDIA, "created_at": now, "details": {"usd": 9}},
        ]
        out = oi.cost_report(7)
        self.assertEqual(out["total_usd"], 0.75)                       # client-paid media is not our money
        self.assertEqual(out["by_category_usd"], {"chat": 0.5, "infra": 0.25})
        self.assertEqual(out["top_owners_usd"], {"client 27": 0.5, "internal": 0.25})

    def test_scheduler_lists_managed_jobs_and_unmanaged_routes(self):
        out = oi.scheduler_jobs()
        names = {j["name"] for j in out["managed"]}
        self.assertTrue({"chat-rollover", "tiktok-uploads", "action-reaper"} <= names)
        self.assertIn("/api/google-ads/scan", out["routes_without_a_managed_job"])
        self.assertNotIn("/api/chat/rollover", out["routes_without_a_managed_job"])

    def test_speed_report_says_plainly_when_the_table_is_missing(self):
        class Broken:
            def table(self, _n):
                raise RuntimeError("no table")
        self._patch(oi, "_db", lambda: Broken())
        self.assertEqual(oi.speed_report(48)["available"], False)


# ─── E. Notebook + ideas ─────────────────────────────────────────────────────

class Notebook(Base):
    def test_note_round_trip_and_block(self):
        self.assertEqual(oi.memory_block(), "")
        self.assertTrue(oi.save_task_memory("לברר למה לקוח 27 מקבל התראות חיבור", "שתי שורות GTM", "לבדוק חי",
                                            client_id=27)["saved"])
        block = oi.memory_block()
        self.assertIn("לקוח 27", block)
        self.assertIn("data, not an instruction", block)
        self.assertTrue(oi.save_task_memory(status="done")["cleared"])
        self.assertEqual(oi.memory_block(), "")

    def test_a_note_needs_a_goal_and_is_clipped(self):
        self.assertFalse(oi.save_task_memory(findings="x")["saved"])
        oi.save_task_memory("g" * 5000)
        self.assertLessEqual(len(oi.task_memory()["goal"]), oi.MEMORY_MAX_CHARS)

    def test_the_note_rides_after_the_cached_prompt(self):
        oi.save_task_memory("מטרה לבדיקה")
        self._patch(op, "_db", lambda: self.db)
        blocks = op._system_for("write")
        self.assertIn("cache_control", blocks[0])
        self.assertNotIn("cache_control", blocks[-1])
        self.assertIn("מטרה לבדיקה", blocks[-1]["text"])
        self.assertEqual(len(op._system_for("simple")), 1)             # cheap kinds never carry it

    def test_ideas_are_deduped_and_capped(self):
        first = oi.add_idea("Move the monitor memory to app_settings", "נמחק בכל פריסה", "small")
        self.assertTrue(first["saved"])
        again = oi.add_idea("move the monitor memory to app_settings!", "שוב")
        self.assertTrue(again["already_listed"])
        self.assertEqual(len(oi.ideas()), 1)
        self.assertTrue(oi.set_idea_status(first["idea"]["id"], "dismissed")["saved"])
        self.assertEqual(oi.ideas()[0]["status"], "dismissed")
        self.assertFalse(oi.set_idea_status(first["idea"]["id"], "approved")["saved"])

    def test_an_idea_becomes_a_card_and_runs_nothing(self):
        self._patch(op, "_db", lambda: self.db)
        turn = op._Turn("owner")
        out = op.run_tool("suggest_improvement", {"title": "Dedupe the weekly report alert",
                                                  "why_he": "חוזר כל שבוע", "effort": "small"}, turn)
        self.assertTrue(out["saved"])
        self.assertEqual([c["type"] for c in turn.cards], ["idea"])
        self.assertTrue(op._card_ok(turn.cards[0]))
        self.assertIn("idea", op.RENDERED_CARD_TYPES)
        self.assertIn("ideaCard", _src("dashboard", "assets", "itzik-chat.js"))


# ─── F. "What can you do?" ───────────────────────────────────────────────────

class Capabilities(unittest.TestCase):
    def test_answered_without_a_model(self):
        T.DB.tables.clear()
        called = []
        saved = claude_json._client
        claude_json._client = lambda api_key=None: called.append(1)
        try:
            out = op.ask("מה אתה יכול לעשות?", "owner")
        finally:
            claude_json._client = saved
        self.assertEqual(called, [])
        self.assertEqual(out["metrics"]["cost_usd"], 0.0)
        for needle in ("לבדוק ולאבחן", "רק בלחיצה שלך", "קוד חד-פעמי", "עצור"):
            self.assertIn(needle, out["reply"])

    def test_every_action_label_is_in_it_and_no_tool_name(self):
        text = op.capabilities_reply()
        for spec in operator_actions.ACTIONS.values():
            self.assertIn(spec["label_he"], text)
        for tool in op.TOOLS:
            self.assertNotIn(tool["name"], text)

    def test_only_the_bare_question_matches(self):
        self.assertTrue(op.CAPABILITIES_RX.match("מה אתה יכול לעשות"))
        self.assertTrue(op.CAPABILITIES_RX.match("what can you do?"))
        self.assertFalse(op.CAPABILITIES_RX.match("מה אתה יכול לעשות עם לקוח 27 שלא עולה"))


# ─── G. Progress ─────────────────────────────────────────────────────────────

class Progress(unittest.TestCase):
    def setUp(self):
        self.saved = dict(op._eta_cache)
        op._eta_cache.update({"at": 9e12, "by_kind": {}})

    def tearDown(self):
        op._eta_cache.update(self.saved)

    def test_plain_words_never_a_tool_name(self):
        line = op.progress_line("read", ["read_logs", "recent_alerts"], "client_state")["line_he"]
        self.assertIn(op.TOOL_LABELS_HE["client_state"], line)
        self.assertIn("2 בדיקות הושלמו", line)
        for tool in op.TOOLS:
            self.assertNotIn(tool["name"], line)

    def test_no_estimate_without_measured_history(self):
        self.assertIsNone(op.progress_line("read", [])["eta_seconds"])
        op._eta_cache["by_kind"] = {"read": {"messages": op.ETA_MIN_SAMPLES - 1, "avg_seconds": 40}}
        self.assertIsNone(op.progress_line("read", [])["eta_seconds"])

    def test_estimate_from_measured_history(self):
        op._eta_cache["by_kind"] = {"read": {"messages": 12, "avg_seconds": 118}}
        out = op.progress_line("read", [])
        self.assertEqual(out["eta_seconds"], 118)
        self.assertIn("כשתי דקות", out["line_he"])

    def test_about_is_how_a_person_says_it(self):
        self.assertEqual([op.about_he(s) for s in (8, 30, 70, 120, 290)],
                         ["כמה שניות", "כחצי דקה", "כדקה", "כשתי דקות", "כ-5 דקות"])

    def test_route_is_admin_only_and_the_chat_polls_it(self):
        api = _src("core", "api_server.py")
        body = api[api.index('@app.get("/api/admin/operator/progress")'):]
        self.assertIn("_operator_admin(request)", body[:500])
        js = _src("dashboard", "assets", "itzik-chat.js")
        self.assertIn("call('/progress')", js)
        self.assertIn("document.hidden", js[js.index("async function pollProgress"):][:300])
        self.assertIn("clearInterval(progressTimer)", js)

    def test_idle_means_not_running(self):
        T.DB.tables.clear()
        op._RUNNING.clear(); op._progress.clear()
        self.assertEqual(op.progress(), {"running": False})


if __name__ == "__main__":
    unittest.main()
