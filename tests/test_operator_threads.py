"""איציק's conversations (core/operator_threads) — 2026-11-16.

What must stay true:
- "new conversation" ARCHIVES the current thread (nothing deleted) and his
  memory (operator_agent.history) starts from the new one;
- only an archived conversation can be deleted, only with the typed phrase
  "מחק", and only its MESSAGE rows go: saved reports and every audit row stay;
- archive and delete are audited BEFORE they happen; no audit = nothing happens;
- the tiers and the emailed-code gates are untouched (this module never imports them);
- the routes are admin-only and the chat asks in the page, never window.confirm.
"""
import os
import sys
import types
import unittest

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

from test_landing_own import FakeDB                      # noqa: E402
from core import operator_actions, operator_agent, operator_threads as ot   # noqa: E402


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Threads(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.saved = [(ot, "_db", ot._db), (operator_actions, "_db", operator_actions._db),
                      (operator_agent, "_db", operator_agent._db)]
        ot._db = operator_actions._db = operator_agent._db = lambda: self.db
        self.db.tables["operator_messages"] = [
            {"id": 1, "created_at": "2099-01-01T10:00:00", "role": "owner", "kind": "message", "content": "האתר לא עולה", "cards": []},
            {"id": 2, "created_at": "2099-01-01T10:01:00", "role": "operator", "kind": "message", "content": "בדקתי", "cards": []},
            {"id": 3, "created_at": "2099-01-01T10:02:00", "role": "operator", "kind": "report", "content": "📋 דוח", "cards": []},
        ]
        ot._now = lambda: "2099-01-02T00:00:00"

    def tearDown(self):
        for module, name, value in self.saved:
            setattr(module, name, value)
        import importlib
        importlib.reload(ot)

    def audit_rows(self):
        return [r for r in self.db.tables.get("alerts", []) if r.get("source") == "operator"]

    def test_archive_keeps_everything_and_memory_starts_fresh(self):
        entry = ot.archive("owner")
        self.assertEqual((entry["title"], entry["count"]), ("האתר לא עולה", 3))
        self.assertEqual(len(self.db.tables["operator_messages"]), 3)            # nothing deleted
        self.assertEqual(ot.current_started_at(), "2099-01-02T00:00:00")
        self.assertEqual(operator_agent.history(), [])                            # the new conversation is empty
        self.assertEqual(len(ot.read(entry["id"])["messages"]), 3)
        self.assertIn("conversation_archived", self.audit_rows()[0]["issues"][0])
        with self.assertRaises(ot.ThreadError) as caught:
            ot.archive("owner")                                                   # nothing new to archive
        self.assertEqual(caught.exception.code, "ERR_NOTHING_TO_ARCHIVE")

    def test_delete_needs_the_phrase_and_keeps_reports_and_audit(self):
        entry = ot.archive("owner")
        for wrong in ("", "delete", "מחקו"):
            with self.assertRaises(ot.ThreadError) as caught:
                ot.delete(entry["id"], wrong, "owner")
            self.assertEqual(caught.exception.code, "ERR_CONFIRM_PHRASE")
        self.assertEqual(len(self.db.tables["operator_messages"]), 3)
        ot.delete(entry["id"], "מחק", "owner")
        self.assertEqual([r["kind"] for r in self.db.tables["operator_messages"]], ["report"])
        self.assertEqual(ot.archived(), [])
        events = [r["issues"][0].split(" ")[0] for r in self.audit_rows()]
        self.assertEqual(events, ["conversation_archived", "conversation_deleted"])  # the log only grows
        with self.assertRaises(ot.ThreadError):
            ot.delete(entry["id"], "מחק", "owner")

    def test_no_audit_no_delete(self):
        entry = ot.archive("owner")

        def fail(*a, **k):
            raise operator_actions.AuditUnavailable("x")
        saved = operator_actions.audit
        operator_actions.audit = fail
        try:
            with self.assertRaises(operator_actions.AuditUnavailable):
                ot.delete(entry["id"], "מחק", "owner")
        finally:
            operator_actions.audit = saved
        self.assertEqual(len(self.db.tables["operator_messages"]), 3)

    def test_the_current_conversation_cannot_be_deleted(self):
        with self.assertRaises(ot.ThreadError) as caught:
            ot.delete("current", "מחק", "owner")
        self.assertEqual(caught.exception.code, "ERR_NO_SUCH_CONVERSATION")


class Wiring(unittest.TestCase):
    def test_routes_admin_only_and_tiers_untouched(self):
        api = _src("core", "api_server.py")
        for route in ('"/api/admin/operator/conversations"', '"/api/admin/operator/conversations/archive"',
                      '"/api/admin/operator/conversations/{thread_id}"',
                      '"/api/admin/operator/conversations/{thread_id}/delete"'):
            body = api[api.index(route):]
            body = body[:body.index("\n@app.")]
            self.assertIn("_operator_admin(request)", body, route)
        mod = _src("core", "operator_threads.py")
        for untouched in ("operator_gate", "operator_changes", "operator_flags", ".delete().eq(\"source\""):
            self.assertNotIn(untouched, mod)

    def test_the_chat_asks_in_the_page(self):
        js = _src("dashboard", "assets", "itzik-chat.js")
        self.assertIn("'/conversations/archive'", js)
        self.assertIn("'/delete', { confirm: inp.value.trim() }", js)
        self.assertNotIn("confirm(", js)                    # no browser dialog: it would block the page


if __name__ == "__main__":
    unittest.main()
