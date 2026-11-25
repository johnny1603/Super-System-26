"""Approving starts production and the chat shows it (2026-11-25).

What happened: the manager approved a post's copy in the Social tab;
production DID start (post_pipeline.start → the start line + the running
card in the producer's thread), but nothing appeared in the chat — the tab's
approve never touched the chat, an open window never fetched new lines or
cards — so the manager typed "אישרתי", which matched nothing (no card left)
and got "no card was made".

What must stay true:
1. Approving in the Social tab starts production with NO chat message, and
   the response carries what started (the producer's line + running card).
2. The dashboard draws that line + card in the producer's chat at once,
   unprompted — the same for every approval route (tab, card, typed yes).
3. Approving twice never makes two runs; "אישרתי" after the tab's approval
   answers "already running" with the live card.
4. Production that cannot start says so in the chat at once, with the reason.

Run from the repo root:  python tests/test_approval_starts_production.py -v
"""
import os
import sys
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_production_honesty as tph          # noqa: E402  (the pipeline harness)

from core import agent_actions as aa           # noqa: E402
from core import chat_approval as ca           # noqa: E402
from core import post_pipeline as pp           # noqa: E402
from core import production_progress as prog   # noqa: E402
from core import social_queue as sq            # noqa: E402

ROOT = os.path.dirname(HERE)


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(tph.Harness):
    def setUp(self):
        super().setUp()
        self.settings = {}
        self.patch(prog, "_read", lambda key: dict(self.settings.get(key) or {}))
        self.patch(prog, "_write", lambda key, value: self.settings.__setitem__(key, dict(value)))
        self.alerts = []
        self.patch(pp, "_alert", lambda lines: self.alerts.append(lines))
        self.patch(pp, "running_card", lambda cid, iid, lang="he": {})
        self.patch(aa, "_lang", lambda cid: "he")
        self.patch(aa, "_client", lambda cid: {"id": cid})
        self.started_rows = []

        def system_run(client_id, action, params, persona="general"):
            row = {"id": 900 + len(self.started_rows), "client_id": client_id, "action": action, "params": params,
                   "persona": persona, "status": "running", "approver": "client", "origin": "client_chat",
                   "decided_by": "system", "decided_at": datetime.now(timezone.utc).isoformat(),
                   "created_at": datetime.now(timezone.utc).isoformat()}
            self.started_rows.append(row)
            return row                              # stored; its own request runs the pass (not in this test)
        self.patch(aa, "start_system_run", system_run)

    def approve_in_tab(self, item_id):
        report = []
        started = pp.start(27, sq.approve(27, [item_id]), None, report=report)
        return started, report


# ─── 1. The Social tab's approval starts production, no chat message ─────────

class TabApproval(Base):
    def test_approval_in_the_tab_starts_production_without_any_chat_message(self):
        item = self.queue()["queued"]
        started, report = self.approve_in_tab(item["id"])
        self.assertEqual(started, 1)
        self.assertEqual([r["action"] for r in self.started_rows], ["rerun_post_production"])
        self.assertEqual(sq.get(27, item["id"])["production"]["state"], "running")
        entry = report[0]
        self.assertEqual((entry["state"], entry["persona"], entry["item_id"]),
                         ("started", item.get("producer") or "media", item["id"]))
        # the producer's own line, stored in its thread — said without anyone asking
        self.assertIn("התחלתי להפיק את הפוסט", entry["line"])
        self.assertEqual(self.lines, [(f"dashboard_chat:{entry['persona']}", entry["line"])])
        # … and the running card with the real bar, from the very first second
        card = entry["card"]
        self.assertEqual((card["status"], card["action"]), ("running", "rerun_post_production"))
        self.assertTrue(card["progress"]["determinate"])
        self.assertEqual(card["progress"]["fraction"], 0.0)
        self.assertIn("מתכונן", card["progress"]["text"])

    def test_the_tab_route_returns_what_started_and_the_dashboard_shows_it(self):
        api = _src("core", "api_server.py")
        route = api[api.index('@app.post("/api/client/social/approve")'):api.index('@app.post("/api/client/social/delete")')]
        self.assertIn("post_pipeline.start(client_id, done, background_tasks, report=productions)", route)
        self.assertIn('"productions": productions', route)
        html = _src("dashboard", "client", "index.html")
        batch = html[html.index("function socialOpenBatch"):html.index("async function socialPost")]
        self.assertIn("showProductionInChat(data.productions)", batch)


# ─── 2. One path: every approval route shows it in the chat, unprompted ──────

class OnePath(Base):
    def test_the_chat_card_approval_returns_the_same_productions(self):
        item = self.queue()["queued"]
        out = aa.ACTIONS["approve_social_items"]["run"](27, {"item_ids": [item["id"]]}, None)
        self.assertEqual(out["approved"], 1)
        self.assertEqual(out["productions"][0]["state"], "started")
        self.assertEqual(out["productions"][0]["card"]["action"], "rerun_post_production")
        src = _src("core", "agent_actions.py")
        self.assertIn('out["productions"] = result["productions"]', src)        # → the decide response

    def test_a_typed_yes_carries_them_too(self):
        src = _src("core", "chat_approval.py")
        approve = src[src.index("def approve(client_id"):src.index("def already_running")]
        self.assertIn('out["productions"] = result["productions"]', approve)
        api = _src("core", "api_server.py")
        self.assertIn('"productions": fast.get("productions") or []', api)
        self.assertIn('"productions": result.get("productions") or []', api)
        agent = _src("agents", "support_agent.py")
        self.assertIn('reply["productions"] = out["productions"]', agent)

    def test_the_dashboard_draws_the_line_and_card_in_the_producers_window(self):
        html = _src("dashboard", "client", "index.html")
        show = html[html.index("async function showProductionInChat"):html.index("function paintActionWhen")]
        self.assertIn("openAgentWindow(persona, { quiet: true })", show)     # not open: opens, history draws both
        self.assertIn("appendMsg(w.msgsEl, p.line, 'bot')", show)            # open: the line now …
        self.assertIn("appendActionCard(persona, p.card)", show)             # … and the running card (watched)
        for call in ("showProductionInChat(data.productions)",                # the Social tab
                     "if (r.productions) showProductionInChat(r.productions)",  # the approval card
                     "showProductionInChat(data.productions, { fromWindow: personaId })"):  # a typed yes
            self.assertIn(call, html)


# ─── 3. Approving twice never makes two runs ─────────────────────────────────

class Twice(Base):
    def test_a_second_start_of_a_running_post_starts_nothing(self):
        item = self.queue()["queued"]
        self.approve_in_tab(item["id"])
        live = {"id": 900, "status": "running", "action": "rerun_post_production",
                "progress": {"step": "מייצר תמונה 1 מתוך 1", "fraction": 0.42}}
        self.patch(pp, "running_card", lambda cid, iid, lang="he": live)
        report = []
        self.assertEqual(pp.start(27, [sq.get(27, item["id"])], None, report=report), 0)   # two approvals at once
        self.assertEqual(len(self.started_rows), 1)
        self.assertEqual(report[0]["state"], "already_running")
        self.assertIn("כבר רצה", report[0]["line"])
        self.assertIn("מייצר תמונה 1 מתוך 1 (42%)", report[0]["line"])
        # the tab's approve clicked again: nothing is waiting, the live card comes back
        self.assertEqual(sq.approve(27, [item["id"]]), [])
        again = pp.already_running(27, [item["id"]])
        self.assertEqual((again[0]["state"], again[0]["card"]), ("already_running", live))

    def test_typed_ishárti_after_the_tab_says_already_running_and_runs_nothing(self):
        item = self.queue()["queued"]
        self.approve_in_tab(item["id"])
        live = {"id": 900, "status": "running", "action": "rerun_post_production",
                "progress": {"step": "מרנדר שקופית 3", "fraction": 0.8}}
        self.patch(pp, "running_card", lambda cid, iid, lang="he": live)
        self.patch(ca, "pending", lambda cid, persona=None: [])
        self.patch(ca, "running", lambda cid, persona=None: [])
        self.patch(aa, "decide", lambda *a, **k: self.fail("a second run was asked for"))
        for typed in ("אישרתי", "אישרתי!", "approved it"):
            out = ca.fast_turn(27, "media", typed, None, "he")
            self.assertIn("כבר רצה", out["reply"])
            self.assertIn("מרנדר שקופית 3", out["reply"])
            self.assertEqual(out["action"], live)
            self.assertEqual(out["productions"][0]["state"], "already_running")
        self.assertEqual(len(self.started_rows), 1)
        # the model's reading of it ("approve" with no pending card) answers the same
        self.assertIn("כבר רצה", ca.decide_from_chat(27, "media", "approve", 0, None, "he")["reply"])

    def test_a_reproposed_production_while_running_is_not_no_card(self):
        agent = _src("agents", "support_agent.py")
        route = agent[agent.index("def _route_proposal"):agent.index("def _dry_result")]
        self.assertIn('("rerun_post_production", "approve_social_items")', route)
        self.assertIn("chat_approval.already_running(client_id, persona_id, lang)", route)
        self.assertLess(route.index("already_running"), route.index("NO_CARD"))


# ─── 4. A production that cannot start says so at once ───────────────────────

class CannotStart(Base):
    def test_storage_down_at_the_start_is_said_with_the_reason(self):
        item = self.queue()["queued"]

        def down(*a, **k):
            raise RuntimeError("app_settings unavailable")
        self.patch(sq, "set_production", down)
        started, report = self.approve_in_tab(item["id"])
        self.assertEqual(started, 0)
        self.assertEqual(report[0]["state"], "failed_to_start")
        self.assertEqual(report[0]["reason"], "storage")
        said = [t for _, t in self.lines]
        self.assertTrue(any("לא הצלחתי להתחיל את ההפקה" in t and "תקלה זמנית באחסון" in t for t in said), said)
        self.assertTrue(self.alerts)                                            # the team was alerted
        self.assertEqual(self.started_rows, [])

    def test_no_card_and_the_run_itself_failing_to_start_is_said_too(self):
        item = self.queue()["queued"]
        self.patch(aa, "start_system_run", lambda *a, **k: False)              # no card could be stored

        def broken(*a, **k):
            raise RuntimeError("worker gone")
        self.patch(pp, "produce", broken)
        _, report = self.approve_in_tab(item["id"])
        self.assertEqual(report[0]["state"], "failed_to_start")
        self.assertIn("משהו נכשל ברגע ההתחלה", report[0]["line"])
        self.assertIn(report[0]["line"], [t for _, t in self.lines])


# ─── The rule the chat model reads ───────────────────────────────────────────

class Rule(unittest.TestCase):
    def test_the_model_is_told_approval_starts_production(self):
        self.assertIn("STARTS ITS PRODUCTION BY ITSELF", ca.SUPPORT_RULE)
        self.assertIn("Never start it a second time", ca.SUPPORT_RULE)
        for typed in ("אישרתי", "אישרנו", "approved it", "approve"):
            self.assertTrue(ca.FAST_APPROVE.match(typed), typed)
        self.assertFalse(ca.FAST_APPROVE.match("אישרתי אבל תשנה את הכותרת"))

    def test_every_new_line_in_five_languages(self):
        for key in ("already_running", "now_at", "start_failed"):
            self.assertEqual(set(pp.TEXT[key]), {"he", "en", "fr", "ar", "ru"}, key)
        for key in ("storage", "not_started"):
            self.assertEqual(set(pp.REASONS[key]), {"he", "en", "fr", "ar", "ru"}, key)


if __name__ == "__main__":
    unittest.main()
