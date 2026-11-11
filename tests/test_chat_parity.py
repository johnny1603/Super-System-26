"""Chat parity guard (2026-11-02, docs/CHAT_PARITY.md, core/chat_parity.py).

FAILS when:
- a session-gated client route is added to core/api_server.py without an
  entry in chat_parity.PARITY (a chat action, an account_state read, a chat
  field, an explicit not_chat reason, or a prioritised gap);
- the client dashboard calls an /api/ path that no PARITY entry covers;
- an entry names an action that doesn't exist, is code-only or can't be
  proposed from the client chat; or an account_state section that isn't read;
- a not_chat entry has no real reason, or a gap has no P1/P2/P3 priority;
- docs/CHAT_PARITY.md misses a gap or an action.

Stdlib only. Run:  python -I -B tests/test_chat_parity.py -v
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

for _name in ("httpx", "anthropic", "supabase"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["anthropic"].__dict__.setdefault("Anthropic", lambda *a, **k: None)
sys.modules["supabase"].__dict__.setdefault("create_client", lambda *a, **k: None)

from core import agent_actions as aa   # noqa: E402
from core import chat_parity as cp     # noqa: E402

# Dashboard calls that are not client capabilities at all
DASHBOARD_ONLY = {"/api/logout": "ends the session", "/api/admin/view-as/exit": "staff leaving the client view",
                  "/api/oauth/": "a prefix the page builds OAuth start links from"}


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def session_routes() -> list:
    """Every route in api_server whose handler calls _require_session."""
    out = []
    for block in re.split(r"\n(?=@app\.)", _src("core", "api_server.py")):
        m = re.match(r'@app\.(get|post|delete|put|patch)\("([^"]+)"', block)
        if not m:
            continue
        lines, body, started = block.split("\n"), [], False
        for i, line in enumerate(lines):
            if i and started and line and not line[0].isspace() and not line.startswith(("@", ")")):
                break
            if line.startswith(("def ", "async def ")):
                started = True
            body.append(line)
        if "_require_session(" in "\n".join(body):
            out.append(f"{m.group(1).upper()} {m.group(2)}")
    return out


def state_sections() -> set:
    # client_state.SECTIONS: every section the snapshot reads (in parallel since 2026-11-11)
    src = _src("core", "client_state.py")
    block = src[src.index("\nSECTIONS = ("):]
    return set(re.findall(r'"(\w+)"', block[:block.index(")")]))


class Parity(unittest.TestCase):
    def test_the_route_scan_sees_the_known_routes(self):
        routes = session_routes()
        self.assertGreater(len(routes), 90)
        self.assertIn("POST /api/client/landing-pages", routes)
        self.assertIn("DELETE /api/client/landing-pages/{page_id}", routes)

    def test_every_client_route_has_a_chat_path_or_an_allowlisted_reason(self):
        missing = sorted(set(session_routes()) - set(cp.PARITY))
        self.assertEqual(missing, [], "add each new dashboard route to core/chat_parity.PARITY — its chat "
                                      "action, or a not_chat reason, or a prioritised gap")

    def test_no_stale_entries(self):
        stale = sorted(set(cp.PARITY) - set(session_routes()))
        self.assertEqual(stale, [], "these routes no longer exist — remove them from chat_parity.PARITY")

    def test_every_dashboard_call_is_covered(self):
        paths = set(re.findall(r"['`\"](/api/[A-Za-z0-9_\-/]+)", _src("dashboard", "client", "index.html")))
        templates = [route.split(" ", 1)[1] for route in cp.PARITY]

        def covered(path):
            if path in DASHBOARD_ONLY:
                return True
            if path.endswith("/"):
                return any(t.startswith(path) for t in templates)
            return any(t == path or re.fullmatch(re.sub(r"\\\{\w+\\\}", r"[^/]+", re.escape(t)), path)
                       for t in templates)
        self.assertEqual(sorted(p for p in paths if not covered(p)), [])

    def test_entries_point_at_real_things(self):
        sections = state_sections()
        for route, (kind, value) in cp.PARITY.items():
            with self.subTest(route=route):
                self.assertIn(kind, (cp.CHAT, cp.FIELD, cp.READ, cp.NOT_CHAT, cp.GAP))
                if kind == cp.CHAT:
                    for name in value.split(","):
                        spec = aa.ACTIONS.get(name)
                        self.assertIsNotNone(spec, f"{name} is not an agent_actions action")
                        self.assertFalse(spec.get("code_only"), f"{name} can't be proposed by the chat")
                        self.assertTrue(aa._may_propose(spec, "client"), f"{name} is not client-proposable")
                        self.assertFalse(spec["internal_only"], f"{name} is internal-only — not real parity")
                elif kind == cp.READ:
                    for section in value.split(","):
                        self.assertIn(section, sections, f"account_state has no '{section}' section")
                elif kind == cp.FIELD:
                    self.assertIn(value, cp.FIELDS)
                elif kind == cp.NOT_CHAT:
                    self.assertGreaterEqual(len(value), 30, "a not_chat entry needs its real reason")
                else:
                    self.assertRegex(value, r"^P[123]: \S")

    def test_the_landing_page_lifecycle_is_fully_in_the_chat(self):
        landing = {r: e for r, e in cp.PARITY.items() if "/landing-" in r}
        self.assertEqual([r for r, (kind, _) in landing.items() if kind == cp.GAP], [])
        for action in ("create_landing_page", "edit_landing_page", "revert_landing_page", "unpublish_landing_page",
                       "delete_landing_page", "publish_landing_page", "connect_landing_domain", "check_landing_domain"):
            self.assertIn((cp.CHAT, action), landing.values(), action)

    def test_the_doc_lists_every_gap_and_every_chat_action(self):
        doc = _src("docs", "CHAT_PARITY.md")
        for route, priority, _what in cp.gaps():
            self.assertIn(route.split(" ", 1)[1], doc, route)
        for name, spec in aa.ACTIONS.items():
            self.assertIn(f"`{name}`", doc, name)


if __name__ == "__main__":
    unittest.main()
