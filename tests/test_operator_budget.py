"""איציק: routing by request type, load on demand, per-task budget cap, stop
control, measurement (2026-11-10, docs/OPERATOR.md §Routing, budget, stop).

A. Routing: rules only. Thanks → cheapest tier with no tools; a short status
   question → cheapest tier with three reads; diagnose → standard tier;
   code / deploy / any change / an approval / anything unclear → the locked
   top tier. Doubt goes up, never down.
B. Permissions unchanged: every tier gets a SUBSET of the same tools; only
   the top tier sees the tools that change something.
C. Budget: near the cap the loop stops, summarizes and shows ONE continue
   card with the cost so far; nothing is spent past the cap; continue resumes
   the saved task, and only a paused task can be continued.
D. Stop: halts BETWEEN tools / steps, never inside one; an irreversible tool
   that started finishes and the line says so; a stop word with nothing
   running answers without a model.
E. Measurement: kind, model, seconds, USD on every reply row; the metrics
   reader and the operator page.
F. The chat: Stop button, the same stop words, the continue card.

Stdlib only. Run from tests/:  python -I -B test_operator_budget.py -v
"""
import json
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_operator as T                         # noqa: E402  (in-memory DB + client_agent/email stubs)

if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = types.ModuleType("anthropic")
    sys.modules["anthropic"].Anthropic = lambda **k: None
from core import admin_service, claude_json, cost_tracker, model_routing, operator_agent as op  # noqa: E402

JS = os.path.join(ROOT, "dashboard", "assets", "itzik-chat.js")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _usage(inp=2000, out=300):
    return types.SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0,
                                 cache_creation_input_tokens=0)


def text_reply(text="בסדר.", inp=2000, out=300):
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                                 usage=_usage(inp, out))


def tool_reply(*names, inp=2000, out=300):
    blocks = [types.SimpleNamespace(type="tool_use", id=f"tu{i}", name=n, input={}) for i, n in enumerate(names)]
    return types.SimpleNamespace(content=blocks, stop_reason="tool_use", usage=_usage(inp, out))


class FakeClient:
    """messages.create → the next scripted response (a callable gets kwargs)."""

    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        nxt = self.script.pop(0) if self.script else text_reply()
        return nxt(kw) if callable(nxt) else nxt


class Base(unittest.TestCase):
    CAP = 1.5

    def setUp(self):
        T.DB.tables.clear(); T.DB.fail.clear()
        op._STOP["at"] = ""
        op._RUNNING.clear(); op._INFLIGHT.clear()
        self.costs = []
        self._patch(cost_tracker, "record_cost", lambda *a, **k: self.costs.append((a, k)))
        self._patch(op, "task_budget_usd", lambda: self.CAP)

    def _patch(self, obj, name, value):
        saved = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(lambda: setattr(obj, name, saved))

    def client(self, *script):
        fake = FakeClient(script)
        self._patch(claude_json, "_client", lambda api_key=None: fake)
        return fake

    def stored(self, role="operator"):
        return [r for r in T.DB.tables.get(op.TABLE, []) if r["role"] == role]


# ─── A. Routing ──────────────────────────────────────────────────────────────

class Routing(unittest.TestCase):
    def test_table(self):
        cases = {
            "תודה": "simple", "תודה רבה!": "simple", "סבבה": "simple", "thanks": "simple", "👍": "simple",
            "מה המצב?": "status", "הכל תקין?": "status", "יש התראות?": "status", "האתר עובד?": "status",
            "למה האתר לא עולה?": "read", "לקוח 12 אומר שהלידים הפסיקו": "read", "the site is broken": "read",
            "מה קרה בפריסה של הבוקר": "read", "why are there errors": "read",
            "תתקן את זה": "write", "תפרוס": "write", "תחזיר לגרסה הקודמת": "write", "fix it": "write",
            "deploy please": "write", "כן": "write", "yes": "write", "תציע תיקון לקוד": "write",
            "תכבה את המתג": "write", "מה דעתך על הדף החדש": "write",           # unclear → up
        }
        for msg, kind in cases.items():
            self.assertEqual(op.classify(msg)[0], kind, msg)

    def test_doubt_goes_up(self):
        # an "ok" after he offered something may be a yes → the top tier
        self.assertEqual(op.classify("אוקיי", "רוצה שאציע תיקון?")[0], "write")
        self.assertEqual(op.classify("אוקיי", "סיימתי. הכול תקין.")[0], "simple")
        # long / multi-part messages are never cheap
        self.assertEqual(op.classify("בדוק " + "א" * 400)[0], "write")
        self.assertEqual(op.classify("מה המצב? ומה עם הלוגים? ומה עם לקוח 3?")[0], "write")
        # a status question with a number / a why is a real read
        self.assertEqual(op.classify("מה המצב של לקוח 12?")[0], "read")

    def test_models_from_the_central_config(self):
        self.assertEqual(op.model_for("write"), model_routing.locked_model("operator"))
        self.assertEqual(op.model_for("write"), model_routing.TIER_MODELS["heavy"])
        self.assertEqual(op.model_for("read"), model_routing.TIER_MODELS["standard"])
        self.assertEqual(op.model_for("simple"), model_routing.TIER_MODELS["simple"])
        self.assertEqual(op.model_for("status"), model_routing.TIER_MODELS["simple"])
        self.assertNotRegex(_src("core", "operator_agent.py"), r"[\"']claude-(haiku|sonnet|opus)")


# ─── B. Permissions unchanged ────────────────────────────────────────────────

class Permissions(unittest.TestCase):
    def test_every_tier_is_a_subset(self):
        names = [t["name"] for t in op.TOOLS]
        for kind in op.KINDS:
            sub = [t["name"] for t in op.tools_for(kind)]
            self.assertTrue(set(sub) <= set(names), kind)
        self.assertEqual(op.tools_for("simple"), [])
        self.assertEqual({t["name"] for t in op.tools_for("status")}, set(op.STATUS_TOOLS))
        self.assertIs(op.tools_for("write"), op.TOOLS)

    def test_only_the_top_tier_changes_things(self):
        for kind in ("simple", "status", "read"):
            got = {t["name"] for t in op.tools_for(kind)}
            for name in op.CHANGE_TOOLS:
                self.assertNotIn(name, got, kind)

    def test_tier2_offers_still_owner_tap_only(self):
        # the read tier may OFFER a button; rollback is still not a tool anywhere
        self.assertIn("offer_action", {t["name"] for t in op.tools_for("read")})
        self.assertNotIn("rollback", {t["name"] for t in op.TOOLS})

    def test_no_cloud_run_setting_changes(self):
        for parts in (("core", "operator_agent.py"), ("dashboard", "assets", "itzik-chat.js")):
            src = _src(*parts)
            self.assertNotIn("--no-cpu-throttling", src)
            self.assertNotIn("min-instances", src)


# ─── Load on demand + the loop ───────────────────────────────────────────────

class Loop(Base):
    def test_thanks_is_cheap_and_has_no_tools(self):
        fake = self.client(text_reply("בכיף."))
        out = op.ask("תודה", "admin")
        self.assertEqual(out["reply"], "בכיף.")
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["model"], model_routing.TIER_MODELS["simple"])
        self.assertNotIn("tools", call)
        self.assertEqual(call["system"][0]["text"], op.QUICK_SYSTEM)
        self.assertNotIn("TIER 3", json.dumps(call["system"]).upper())
        self.assertEqual(out["metrics"]["kind"], "simple")

    def test_short_history_for_a_short_message(self):
        for i in range(12):
            T.DB.tables.setdefault(op.TABLE, []).append(
                {"id": i + 1, "created_at": "2026-11-10T10:00:00", "role": "owner" if i % 2 == 0 else "operator",
                 "kind": "message", "content": f"m{i}", "cards": []})
        fake = self.client(text_reply("בכיף."))
        op.ask("תודה", "admin")
        self.assertLessEqual(len(fake.calls[0]["messages"]), 5)      # ≤4 history rows + the new message

    def test_diagnose_runs_on_the_standard_tier_without_change_tools(self):
        fake = self.client(tool_reply("recent_alerts"), text_reply("מצאתי."))
        out = op.ask("למה יש שגיאות בלוגים?", "admin")
        self.assertEqual(out["reply"], "מצאתי.")
        self.assertEqual({c["model"] for c in fake.calls}, {model_routing.TIER_MODELS["standard"]})
        offered = {t["name"] for t in fake.calls[0]["tools"]}
        self.assertNotIn("propose_code_change", offered)
        self.assertNotIn("run_reaper", offered)

    def test_a_tool_outside_the_tier_is_refused(self):
        ran = []
        self._patch(op, "run_tool", lambda name, args, turn: ran.append(name) or {})
        self.client(tool_reply("propose_code_change"), text_reply("ok"))
        op.ask("למה יש שגיאות?", "admin")
        self.assertEqual(ran, [])

    def test_code_goes_to_the_top_tier_with_every_tool(self):
        fake = self.client(text_reply("הצעתי."))
        op.ask("תתקן את הבאג", "admin")
        self.assertEqual(fake.calls[0]["model"], model_routing.TIER_MODELS["heavy"])
        self.assertEqual(len(fake.calls[0]["tools"]), len(op.TOOLS))

    def test_empty_cheap_answer_goes_up_once(self):
        empty = types.SimpleNamespace(content=[types.SimpleNamespace(type="thinking", thinking="", signature="s")],
                                      stop_reason="end_turn", usage=_usage())
        fake = self.client(empty, text_reply("הכול תקין."))
        out = op.ask("מה המצב?", "admin")
        self.assertEqual(out["reply"], "הכול תקין.")
        self.assertEqual(fake.calls[1]["model"], model_routing.TIER_MODELS["standard"])

    def test_cost_rows_carry_the_task(self):
        self.client(text_reply("בכיף."))
        out = op.ask("תודה", "admin")
        details = self.costs[0][1]["details"]
        self.assertEqual(details["operator_task"], out["metrics"]["task_id"])
        self.assertEqual(details["operator_kind"], "simple")


# ─── C. Budget ───────────────────────────────────────────────────────────────

class Budget(Base):
    CAP = 0.5
    BIG = {"inp": 20000, "out": 2000}          # opus: $0.12 per call

    def script(self):
        def respond(kw):
            if kw.get("tool_choice") == {"type": "none"}:
                return text_reply("בדקתי את ההתראות. נשאר לבדוק את הלוגים.", **self.BIG)
            return tool_reply("recent_alerts", **self.BIG)
        return [respond] * 20

    def test_pauses_before_the_cap_with_one_card(self):
        fake = self.client(*self.script())
        out = op.ask("תתקן את הבאג בהתראות", "admin")
        self.assertEqual(out["metrics"]["outcome"], "paused")
        spent = out["metrics"]["cost_usd"]
        self.assertLessEqual(spent, self.CAP)                       # never past the cap
        self.assertEqual(fake.calls[-1].get("tool_choice"), {"type": "none"})   # the summary
        cards = [c for c in out["cards"] if c["type"] == "budget"]
        self.assertEqual(len(cards), 1)
        self.assertIn(f"${spent:.2f}", cards[0]["label_he"])
        self.assertIn("להמשיך?", cards[0]["label_he"])
        self.assertEqual(out["reply"], "בדקתי את ההתראות. נשאר לבדוק את הלוגים.")
        state = op._load_task(cards[0]["task_id"])
        self.assertEqual(state["status"], "paused")
        self.assertTrue(state["msgs"])

    def test_card_text_names_no_tool_and_no_error(self):
        card = op.budget_card("abc123", 0.42, 0.5)
        for name in [t["name"] for t in op.TOOLS] + ["error", "ERR_"]:
            self.assertNotIn(name, card["label_he"] + card["button_he"])
        self.assertNotIn("recent_alerts", op.budget_summary(["recent_alerts", "read_logs"]))
        self.assertIn(op.TOOL_LABELS_HE["read_logs"], op.budget_summary(["read_logs"]))

    def test_summary_by_code_when_even_it_would_pass_the_cap(self):
        self.CAP = 0.01
        fake = self.client(*self.script())
        out = op.ask("תתקן את הבאג", "admin")
        self.assertEqual(fake.calls, [])                            # not one call fits
        self.assertEqual(out["metrics"]["outcome"], "paused")
        self.assertTrue(out["reply"].startswith("עצרתי כאן כדי לא לעבור את התקרה"))

    def test_continue_only_on_the_owners_tap_and_only_once(self):
        self.client(*self.script())
        first = op.ask("תתקן את הבאג בהתראות", "admin")
        task_id = first["cards"][0]["task_id"]
        fake = self.client(text_reply("סיימתי."))
        out = op.ask("", "admin", continue_task=task_id)
        self.assertEqual(out["reply"], "סיימתי.")
        self.assertIn(op.CONTINUE_NOTE, json.dumps(fake.calls[0]["messages"], ensure_ascii=False))
        self.assertEqual(op._load_task(task_id)["status"], "done")
        again = op.ask("", "admin", continue_task=task_id)
        self.assertEqual(again["reply"], op.NO_PAUSED)
        self.assertEqual(op.ask("", "admin", continue_task="../x")["reply"], op.NO_PAUSED)

    def test_setting_default_and_clamp(self):
        self.assertEqual(admin_service.DEFAULT_SETTINGS["operator_task_budget_usd"], 1.5)
        saved = admin_service.get_setting
        try:
            for raw, want in ((0, op.BUDGET_MIN_USD), (999, op.BUDGET_MAX_USD), ("2.5", 2.5), ("x", 1.5)):
                admin_service.get_setting = lambda key, default=None, ttl=60.0, raw=raw: raw
                # the real reader (Base patched the module attribute; call the original)
                self.assertEqual(Budget._real_budget(), want, raw)
        finally:
            admin_service.get_setting = saved

    def test_task_and_stop_rows_are_not_global_settings(self):
        T.DB.tables["app_settings"] = [{"key": "operator_task:abc", "value": {}}, {"key": op.STOP_KEY, "value": {}}]
        saved = admin_service._db
        admin_service._db = lambda: T.DB
        try:
            got = admin_service.get_settings()
        finally:
            admin_service._db = saved
        self.assertNotIn("operator_task:abc", got)
        self.assertNotIn(op.STOP_KEY, got)


Budget._real_budget = staticmethod(op.task_budget_usd)


# ─── D. Stop ─────────────────────────────────────────────────────────────────

class Stop(Base):
    def test_between_tools_never_inside_one(self):
        ran = []

        def fake_tool(name, args, turn):
            ran.append(name)
            if name == "run_reaper":
                op.request_stop("admin")             # arrives WHILE it runs: it finishes anyway
            return {"ok": True}
        self._patch(op, "run_tool", fake_tool)
        fake = self.client(tool_reply("run_reaper", "recent_alerts"))
        out = op.ask("תנקה את הכרטיסים התקועים", "admin")
        self.assertEqual(ran, ["run_reaper"])        # finished; the next one never started
        self.assertEqual(len(fake.calls), 1)         # no further model step
        line = out["reply"]
        self.assertTrue(line.startswith("עצרתי."))
        self.assertIn("בוצע: " + op.TOOL_LABELS_HE["run_reaper"], line)
        self.assertIn("הושלם עד הסוף, לא נעצר באמצע", line)
        self.assertIn("לא בוצע: " + op.TOOL_LABELS_HE["recent_alerts"], line)
        self.assertNotIn("\n", line)
        for name in (t["name"] for t in op.TOOLS):
            self.assertNotIn(name, line)
        self.assertEqual(out["metrics"]["outcome"], "stopped")
        self.assertEqual(op._load_task(out["metrics"]["task_id"])["status"], "stopped")

    def test_between_steps(self):
        def fake_tool(name, args, turn):
            op.request_stop("admin")
            return {}
        self._patch(op, "run_tool", fake_tool)
        fake = self.client(tool_reply("recent_alerts"), text_reply("never"))
        out = op.ask("למה יש שגיאות?", "admin")
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("לא בוצע: הסיכום והמשך הבדיקה.", out["reply"])

    def test_signal_from_another_instance(self):
        # only the shared row says stop (this instance's memory doesn't)
        def fake_tool(name, args, turn):
            from datetime import datetime, timedelta, timezone
            later = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()   # another instance's clock
            T.DB.tables.setdefault("app_settings", []).append({"key": op.STOP_KEY, "value": {"at": later}})
            return {}
        self._patch(op, "run_tool", fake_tool)
        self._patch(op, "STOP_POLL_SECONDS", 0)
        fake = self.client(tool_reply("recent_alerts"), text_reply("never"))
        out = op.ask("למה יש שגיאות?", "admin")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(out["metrics"]["outcome"], "stopped")

    def test_an_older_stop_does_not_touch_a_new_task(self):
        op.request_stop("admin")
        self.client(text_reply("בכיף."))
        self.assertEqual(op.ask("תודה", "admin")["reply"], "בכיף.")

    def test_stop_word_with_nothing_running(self):
        fake = self.client()
        out = op.ask("עצור", "admin")
        self.assertEqual(out["reply"], op.STOP_IDLE)
        self.assertEqual(fake.calls, [])
        op._RUNNING.add("abc123")
        self.assertEqual(op.ask("stop", "admin")["reply"], op.STOP_ACK)

    def test_stop_words_are_whole_messages_only(self):
        for msg in ("עצור", "חכה!", "רגע", "stop", "Wait.", "תפסיק"):
            self.assertTrue(op.STOP_RX.match(msg), msg)
        for msg in ("למה השרת עצר?", "רגע, תבדוק את הלוגים", "stop the deploy"):
            self.assertFalse(op.STOP_RX.match(msg), msg)

    def test_owner_approved_irreversible_steps_finish(self):
        with op.irreversible_step("המיזוג והפריסה לייצור"):
            self.assertEqual(op.request_stop("admin")["in_progress_he"], ["המיזוג והפריסה לייצור"])
            self.assertIn("המיזוג והפריסה לייצור — כבר התחיל ומסתיים עד הסוף.", op.stop_line([], []))
        self.assertEqual(op.inflight_labels(), [])
        api = _src("core", "api_server.py")
        for label in ("שליחת קוד האישור במייל", "פתיחת ה-PR ב-GitHub", "המיזוג והפריסה לייצור"):
            self.assertIn(f'with operator_agent.irreversible_step("{label}"):', api)

    def test_every_tool_has_a_plain_label(self):
        self.assertEqual(set(op.TOOL_LABELS_HE), {t["name"] for t in op.TOOLS})


# ─── E. Measurement ──────────────────────────────────────────────────────────

class Measurement(Base):
    def test_every_reply_row_carries_metrics(self):
        self.client(text_reply("בכיף."))
        op.ask("תודה", "admin")
        op.ask("עצור", "admin")
        rows = self.stored()
        self.assertEqual(len(rows), 2)
        m = rows[0]["meta"]["metrics"]
        for key in ("kind", "model", "seconds", "cost_usd", "calls", "outcome", "task_id"):
            self.assertIn(key, m)
        self.assertEqual(m["model"], model_routing.TIER_MODELS["simple"])
        self.assertGreater(m["cost_usd"], 0)
        self.assertEqual(rows[1]["meta"]["metrics"]["outcome"], "quick")

    def test_metrics_reader(self):
        self.client(text_reply("בכיף."), text_reply("בכיף."))
        op.ask("תודה", "admin"); op.ask("תודה", "admin")
        out = op.metrics()
        self.assertTrue(out["table_ready"])
        self.assertEqual(out["by_kind"]["simple"]["messages"], 2)
        self.assertEqual(out["cap_usd"], self.CAP)
        self.assertEqual(len(out["recent"]), 2)

    def test_admin_surfaces(self):
        page = _src("dashboard", "admin", "operator", "index.html")
        self.assertIn('id="metricsCard"', page)
        self.assertIn("call('/metrics')", page)
        admin = _src("dashboard", "admin", "index.html")
        self.assertIn('id="set-operator-budget"', admin)
        self.assertIn("operator_task_budget_usd:", admin)
        api = _src("core", "api_server.py")
        self.assertIn('@app.post("/api/admin/operator/stop")', api)
        self.assertIn('@app.get("/api/admin/operator/metrics")', api)


# ─── F. The chat ─────────────────────────────────────────────────────────────

class Chat(unittest.TestCase):
    def test_same_stop_words_in_the_browser(self):
        js = _src("dashboard", "assets", "itzik-chat.js")
        js_rx = re.search(r"const STOP_RX = /(.+)/i;", js).group(1)
        self.assertEqual(js_rx, op.STOP_RX.pattern)

    def test_stop_button_and_continue_card(self):
        js = _src("dashboard", "assets", "itzik-chat.js")
        self.assertIn("el('button', 'itz-b stop', '⏹ עצור')", js)
        self.assertIn("post('/stop', {})", js)
        self.assertIn("{ message: 'המשך', continue_task: taskId }", js)
        self.assertIn("if (c.type === 'budget') return budgetCard(c);", js)
        # while he works, the composer stays open for a stop word
        self.assertIn("if (isStopWord(text))", js)


if __name__ == "__main__":
    unittest.main()
