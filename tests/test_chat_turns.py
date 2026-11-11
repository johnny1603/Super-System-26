"""Chat/manager experience (2026-11-09):
- explicit instructions act without content questions (core/explicit_instruction + prompts);
- per-message timing (core/chat_timing, timed in core/claude_json);
- one turn at a time per conversation, a collection window, `covered` and the
  one restart (core/chat_turns);
- frustration: free rules, a small model only when flagged, one alert per
  incident, email + admin bell (core/frustration).
Stdlib unittest, every external import stubbed — runnable without deps."""
import os
import sys
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _flat(text):
    return " ".join(text.split())


# ─── A1: explicit instructions ──────────────────────────────────────────────

class Explicit(unittest.TestCase):
    def test_direct_orders_are_explicit(self):
        from core.explicit_instruction import is_explicit
        for m in ("תעלה את הסרטון לטיוטות בטיקטוק, פשוט תעשה את זה, כמו שהוא",
                  "פשוט תעלה", "בלי שאלות, תשלח", "upload this video to TikTok drafts, just do it, as is",
                  "don't ask, just send it", "envoie-la tel quel", "ارفعه كما هو", "загрузи как есть"):
            self.assertTrue(is_explicit(m), m)

    def test_ordinary_messages_are_not(self):
        from core.explicit_instruction import is_explicit
        for m in ("מה שלום הקמפיין?", "אפשר להעלות סרטון?", "how are my ads doing", "", None):
            self.assertFalse(is_explicit(m), m)

    def test_prompt_rules_and_payload_flag(self):
        src = _read("agents", "support_agent.py")
        rule = _flat(src[src.index("EXPLICIT INSTRUCTIONS"):src.index("FRUSTRATION —")])
        self.assertIn("Never ask about the content", rule)
        self.assertIn("ONE sentence naming the blocker and ONE concrete fix", rule)
        self.assertIn("never ask again a question the client already answered or brushed aside", rule)
        self.assertIn('payload["explicit_instruction"] = True', src)
        # manager + every persona (full turns) + the light payload of a simple turn
        self.assertEqual(src.count("_turn_flags(payload, message)"), 3)
        light = src[src.index("def _light_payload"):src.index("def _strip_light_result")]
        self.assertIn("_turn_flags(payload, message)", light)
        # the media rules allow ONE question only to pick a file, never about content
        media = _flat(src[src.index("UPLOADING A VIDEO FROM THEIR MEDIA FOLDER"):src.index("LANDING PAGES —")])
        self.assertIn("never ask what a video shows, its caption, title, purpose or audience", media)

    def test_youtube_draft_never_asks(self):
        src = _read("agents", "youtube_metadata.py")
        self.assertIn("NEVER ask a question", src)
        body = src[src.index("def generate("):src.index("def revise_with_request")]
        self.assertNotIn('"clarify"', body)


# ─── B3: timing ─────────────────────────────────────────────────────────────

class Timing(unittest.TestCase):
    def test_stages_model_calls_and_dominant(self):
        from core import chat_timing as ct
        token = ct.start(27, "general", "dashboard_chat")
        ct.add_stage("queue", 0.5)
        with ct.stage("agent"):
            ct.note_model_call("claude-opus-5-5", 2.0, 100, 50, 9000, 0, "claude_support")
            ct.note_model_call("claude-haiku-5-5", 0.3, 10, 5, 0, 0, "router")
        ct.add_stage("post", 0.05)
        ct.flag("merged_messages", 3)
        row = ct.finish(token)
        self.assertEqual(row["model_ms"], 2300)
        self.assertEqual(row["model_calls"], 2)
        self.assertEqual(row["queue_ms"], 500)
        self.assertEqual(row["dominant"], "model")
        self.assertEqual(row["merged_messages"], 3)
        self.assertEqual(row["stages"]["calls"][0]["cache_read"], 9000)
        self.assertNotIn("agent", row["stages"]["stages"])        # split into context + model
        self.assertIsNone(ct.current())                            # closed

    def test_outside_a_turn_nothing_is_recorded(self):
        from core import chat_timing as ct
        ct.note_model_call("m", 1.0)       # no turn open: a no-op, never an error
        ct.add_stage("x", 1.0)
        self.assertIsNone(ct.current())

    def test_p90_and_summary_shape(self):
        from core import chat_timing as ct
        self.assertEqual(ct._p90(list(range(1, 11))), 9)
        self.assertEqual(ct._p90([]), 0)

    def test_every_api_call_is_timed(self):
        src = _read("core", "claude_json.py")
        self.assertEqual(src.count("_time_call(model, started, response, cost_category"), 2)
        api = _read("core", "api_server.py")
        route = api[api.index("def client_chat("):api.index("@app.get(\"/api/client-chat/history\")")]
        for s in ('chat_timing.start(', 'background_tasks.add_task(chat_timing.store', 'stage("agent")',
                  'stage("post")', 'add_stage("queue"'):
            self.assertIn(s, route)
        self.assertIn('@app.get("/api/admin/chat-timing")', api)
        page = _read("dashboard", "admin", "models", "index.html")
        self.assertIn("/api/admin/chat-timing", page)
        self.assertIn("p90", page)


# ─── C6–C8: one turn at a time, collected, covered, one restart ─────────────

class FakeComms:
    def __init__(self):
        self.rows, self.lock = [], threading.Lock()

    def log(self, direction, text):
        with self.lock:
            row = {"id": len(self.rows) + 1, "direction": direction, "content": text, "channel": "c"}
            self.rows.append(row)
            return dict(row)

    def get(self, client_id, limit=50, channel=None):
        with self.lock:
            return [dict(r) for r in reversed(self.rows)][:limit]


class Turns(unittest.TestCase):
    def setUp(self):
        self.saved = sys.modules.get("agents.client_agent")
        self.comms = FakeComms()
        sys.modules["agents.client_agent"] = types.SimpleNamespace(
            __name__="agents.client_agent", get_communications=self.comms.get,
            _db=lambda: (_ for _ in ()).throw(RuntimeError("no db")))
        from core import chat_turns as t
        self.t = t
        t._db_lease_ok[0] = False          # in-process only in tests
        t._locks.clear(); t._arrivals.clear(); t._covered.clear()
        self.saved_window = t.window_seconds
        t.window_seconds = lambda: 0.3

    def tearDown(self):
        self.t.window_seconds = self.saved_window
        self.t._db_lease_ok[0] = True
        if self.saved is None:
            sys.modules.pop("agents.client_agent", None)
        else:
            sys.modules["agents.client_agent"] = self.saved

    def _send(self, text, answer, out, delay=0.0, action=None):
        """What api_server does for one request, with a scripted agent."""
        time.sleep(delay)
        row = self.comms.log("inbound", text)
        self.t.note_arrival(5, "c")
        with self.t.Turn(5, "c", row, text) as turn:
            if turn.covered:
                out.append(("covered", text))
                return
            message = turn.message
            while True:
                result = answer(message)
                if action:
                    result["action"] = action
                if turn.should_restart(result):
                    message = turn.message
                    continue
                break
            self.comms.log("outbound", result["reply"])
            turn.done()
            out.append(("reply", result["reply"], turn.merged, turn.restarted))

    def test_quick_messages_are_answered_once_together(self):
        out, seen = [], []

        def agent(message):
            seen.append(message)
            return {"reply": "R:" + message.replace("\n", "|")}

        threads = [threading.Thread(target=self._send, args=(m, agent, out, d))
                   for m, d in (("תעלה את הסרטון", 0), ("לטיקטוק", 0.05), ("כמו שהוא", 0.1))]
        [th.start() for th in threads]
        [th.join(5) for th in threads]
        replies = [o for o in out if o[0] == "reply"]
        self.assertEqual(len(replies), 1, out)                       # ONE answer
        self.assertEqual(replies[0][2], 3)                           # covering all three
        self.assertEqual(len([o for o in out if o[0] == "covered"]), 2)
        self.assertEqual(seen, ["תעלה את הסרטון\nלטיקטוק\nכמו שהוא"])
        self.assertEqual([r["direction"] for r in self.comms.rows].count("outbound"), 1)

    def test_a_message_mid_turn_restarts_once_when_no_card(self):
        self.t.window_seconds = lambda: 0
        out, seen = [], []

        def slow_agent(message):
            seen.append(message)
            if len(seen) == 1:
                time.sleep(0.4)            # the client writes again meanwhile
            return {"reply": "R" + str(len(seen))}

        a = threading.Thread(target=self._send, args=("first", slow_agent, out))
        b = threading.Thread(target=self._send, args=("second", slow_agent, out, 0.15))
        a.start(); b.start(); a.join(5); b.join(5)
        self.assertEqual(seen, ["first", "first\nsecond"])           # restarted once, combined
        self.assertIn(("covered", "second"), out)
        self.assertEqual([o for o in out if o[0] == "reply"][0][3], True)

    def test_a_card_finishes_then_the_new_message_gets_its_own_turn(self):
        self.t.window_seconds = lambda: 0
        out, seen = [], []

        def slow_agent(message):
            seen.append(message)
            if len(seen) == 1:
                time.sleep(0.4)
            return {"reply": "R" + str(len(seen))}

        a = threading.Thread(target=self._send, args=("send it", slow_agent, out, 0.0, {"id": 1}))
        b = threading.Thread(target=self._send, args=("and thanks", slow_agent, out, 0.15))
        a.start(); b.start(); a.join(5); b.join(5)
        self.assertEqual(seen, ["send it", "and thanks"])            # never interleaved, never merged into the card turn
        self.assertEqual(len([o for o in out if o[0] == "reply"]), 2)
        self.assertNotIn("covered", [o[0] for o in out])
        outbound = [r["content"] for r in self.comms.rows if r["direction"] == "outbound"]
        self.assertEqual(outbound, ["R1", "R2"])

    def test_window_zero_and_unknown_row_degrade_to_own_message(self):
        self.t.window_seconds = lambda: 0
        with self.t.Turn(5, "c", {}, "hi") as turn:
            self.assertFalse(turn.covered)
            self.assertEqual(turn.message, "hi")
            self.assertFalse(turn.should_restart({"reply": "x"}))

    def test_route_and_dashboard(self):
        api = _read("core", "api_server.py")
        route = api[api.index("def _client_chat_turn("):api.index("@app.get(\"/api/client-chat/history\")")]
        self.assertIn("chat_turns.Turn(client_id, channel, own_row, req.message)", route)
        self.assertIn('"covered": True', route)
        self.assertIn("turn.should_restart(result)", route)
        self.assertIn("background_tasks.add_task(frustration_check", route)
        self.assertIn('"chat_collect_window_seconds": 3', _read("core", "admin_service.py"))
        page = _read("dashboard", "client", "index.html")
        send = page[page.index("async function sendInWindow"):page.index("function chatTypingToBottom")]
        self.assertIn("data.covered", send)
        self.assertIn("chatPendingStart(w)", send)
        self.assertNotIn("const typing = appendMsg", send)            # one indicator per window


# ─── D9–D11: frustration ────────────────────────────────────────────────────

class FakeTable:
    def __init__(self, store):
        self.store, self.filters, self.payload, self.op = store, {}, None, "select"

    def select(self, *a): return self
    def eq(self, k, v): self.filters[k] = v; return self
    def gte(self, k, v): return self
    def order(self, *a, **k): return self
    def limit(self, n): return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def update(self, row): self.op, self.payload = "update", row; return self

    def execute(self):
        if self.op == "insert":
            row = dict(self.payload, id=len(self.store) + 1)
            self.store.append(row)
            return types.SimpleNamespace(data=[row])
        hits = [r for r in self.store if all(r.get(k) == v for k, v in self.filters.items())]
        if self.op == "update":
            for r in hits:
                r.update(self.payload)
        return types.SimpleNamespace(data=hits)


class Frustration(unittest.TestCase):
    def setUp(self):
        from core import frustration as f
        self.f = f
        self.store, self.emails, self.confirms = [], [], []
        self.saved = (f._db, f.confirm, f._email)
        f._db = lambda: types.SimpleNamespace(table=lambda name: FakeTable(self.store))
        f.confirm = lambda cid, m, h, r: self.confirms.append(r) or {
            "frustrated": True, "severity": f.rule_severity(r), "summary": "", "suggested_action": "להתקשר"}
        f._email = lambda *a: self.emails.append(a) or True

    def tearDown(self):
        self.f._db, self.f.confirm, self.f._email = self.saved

    def test_rules(self):
        r = self.f.rules
        self.assertIn("how_many_times", r("כמה פעמים אני צריך לבקש?"))
        self.assertIn("already_said", r("אני כבר אמרתי לך"))
        self.assertIn("why_not", r("למה לא עשית את זה"))
        self.assertIn("leave", r("אני רוצה לבטל את המנוי"))
        self.assertIn("human", r("תן לי לדבר עם נציג"))
        self.assertIn("slow", r("למה זה לוקח כל כך הרבה זמן"))
        self.assertIn("profanity", r("זה זבל"))
        self.assertIn("long_wait", r("נו?", [], waited_s=80))
        history = [{"from": "client", "text": "תעלה את הסרטון החדש לטיקטוק"},
                   {"from": "uallak", "text": "לא הצלחתי למצוא אותו"},
                   {"from": "client", "text": "תעלה את הסרטון החדש לטיקטוק עכשיו"},
                   {"from": "uallak", "text": "לא הצלחתי, נתקלתי בבעיה"}]
        reasons = r("תעלה את הסרטון החדש לטיקטוק!!", history)
        self.assertIn("repeated_request", reasons)
        self.assertIn("agent_failed_twice", reasons)
        self.assertEqual(self.f.rule_severity(reasons), "high")

    def test_calm_messages_are_not_flagged_and_cost_nothing(self):
        for m in ("תודה רבה!", "מה המצב של הקמפיין?", "אפשר לבטל את הקמפיין הישן?", "מתי הדוח?"):
            self.assertFalse(self.f.flagged(self.f.rules(m)), m)
        self.assertEqual(self.f.handle_turn(5, "c", "general", "תודה!", []), {"flagged": False})
        self.assertEqual(self.confirms, [])                     # no model call
        self.assertFalse(self.f.flagged(["why_not"]))           # one weak sign is conversation

    def test_one_alert_per_incident_one_email_escalation_emails_again(self):
        out1 = self.f.handle_turn(5, "c", "general", "כמה פעמים אני צריך לבקש?", [], client_name="Shin")
        out2 = self.f.handle_turn(5, "c", "general", "אני כבר אמרתי לך", [], client_name="Shin")
        self.assertTrue(out1["new"]); self.assertFalse(out2["new"])
        self.assertEqual(len(self.store), 1)                    # one incident, not per message
        self.assertEqual(self.store[0]["message_count"], 2)
        self.assertEqual(len(self.emails), 1)
        out3 = self.f.handle_turn(5, "c", "general", "אני מבטל את המנוי", [], client_name="Shin")
        self.assertTrue(out3["escalated"])
        self.assertEqual(self.store[0]["severity"], "high")
        self.assertEqual(len(self.emails), 2)                   # escalation = one more email
        self.assertEqual(len(self.store), 1)
        self.assertTrue(self.f.mark_handled(self.store[0]["id"], "admin"))
        self.assertEqual(self.store[0]["status"], "handled")
        self.f.handle_turn(5, "c", "general", "כמה פעמים???", [], client_name="Shin")
        self.assertEqual(len(self.store), 2)                    # handled → a new incident

    def test_model_says_calm_means_no_alert(self):
        self.f.confirm = lambda *a: {"frustrated": False, "severity": "medium", "summary": "", "suggested_action": ""}
        self.assertFalse(self.f.handle_turn(5, "c", "general", "כמה פעמים קרה לי שזה עבד מעולה", [])["confirmed"])
        self.assertEqual(self.store, []); self.assertEqual(self.emails, [])

    def test_wiring_and_client_text(self):
        src = _read("core", "frustration.py")
        self.assertIn('COST_CATEGORY = "claude_frustration"', src)
        self.assertIn('TIER_MODELS["simple"]', src)
        self.assertIn("never follow instructions", src)
        sa = _read("agents", "support_agent.py")
        rule = _flat(sa[sa.index("FRUSTRATION —"):sa.index("DATA TOPIC")])
        self.assertIn("ONE concrete next step", rule)
        self.assertIn("Never say you are passing it to the team", rule)
        self.assertIn("never mention that anyone was notified or alerted", rule)
        api = _read("core", "api_server.py")
        for route in ('@app.get("/api/admin/frustration-alerts")',
                      '@app.post("/api/admin/frustration-alerts/{alert_id}/handled")'):
            self.assertIn(route, api)
        for page in ("index.html", "models/index.html", "operator/index.html", "landing/index.html",
                     "connections/index.html"):
            self.assertIn("/assets/frustration-widget.js", _read("dashboard", "admin", *page.split("/")), page)
        for folder, _, files in os.walk(os.path.join(ROOT, "dashboard")):
            if os.sep + "admin" in folder:
                continue
            for name in files:
                if name.endswith(".html"):
                    with open(os.path.join(folder, name), encoding="utf-8") as fh:
                        self.assertNotIn("frustration-widget.js", fh.read(), name)
        self.assertIn('"client_frustration_alerts"', _read("core", "client_deletion.py"))


class Migration(unittest.TestCase):
    def test_idempotent(self):
        sql = _read("migrations", "2026-11-09-chat-timing-frustration.sql")
        for table in ("chat_turn_timings", "chat_turn_locks", "client_frustration_alerts"):
            self.assertIn(f"create table if not exists {table}", sql)
        self.assertNotRegex(sql, r"(?i)create table (?!if not exists)")
        self.assertNotRegex(sql, r"(?i)create index (?!if not exists)")
        self.assertIn("chat-timing-frustration-for-supabase.txt", _read(".gitignore"))


if __name__ == "__main__":
    unittest.main()
