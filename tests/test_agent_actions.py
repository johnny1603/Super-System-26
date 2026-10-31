"""Whitelist and guard tests for core/agent_actions (+ verification_demo's
internal-client refusal, client_state's failure caching, and the prompt
templates' brace safety).

Stdlib only — no Supabase, no FastAPI, no network: every external module the
code imports lazily is replaced by a stub in sys.modules, and the database by
an in-memory fake that supports exactly the query shapes agent_actions uses.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import ast
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ─── In-memory stand-in for the Supabase query builder ───────────────────────

class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.op = db, table, "select"
        self.filters, self.payload, self.limit_n = [], None, None

    def select(self, *_a):
        self.op = "select"
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, changes):
        self.op, self.payload = "update", changes
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r, c=col, v=tuple(vals): r.get(c) in v)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.db.fail:
            raise RuntimeError("PGRST205 Could not find the table")
        if self.op == "insert":
            row = {"id": len(rows) + 1, "created_at": datetime.now(timezone.utc).isoformat(),
                   **self.payload}
            rows.append(row)
            return types.SimpleNamespace(data=[dict(row)])
        matched = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in matched:
                r.update(self.payload)
            return types.SimpleNamespace(data=[dict(r) for r in matched])
        return types.SimpleNamespace(data=[dict(r) for r in matched[:self.limit_n or None]])


class FakeDB:
    def __init__(self):
        self.tables, self.fail = {}, False

    def table(self, name):
        return _Query(self, name)


# ─── Stubs for the modules agent_actions reaches lazily ──────────────────────

class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"success": True, "live_version": "v7", "event_name": "generate_lead"}


def install_stubs(pending_suggestions=(11, 12)):
    engagement = types.ModuleType("agents.engagement_agent")
    engagement.get_suggestions = lambda client_id, status="pending", limit=20: [
        {"id": sid, "title": f"idea {sid}", "kind": "homework"} for sid in pending_suggestions]
    engagement.decide_suggestion = Recorder()
    website = types.ModuleType("agents.website_agent")
    website.configure_lead_conversion = Recorder()
    sys.modules["agents.engagement_agent"] = engagement
    sys.modules["agents.website_agent"] = website
    return engagement, website


from core import agent_actions as aa          # noqa: E402  (after sys.path)
from core import verification_demo as vd      # noqa: E402

CLIENTS = {
    27: {"id": 27, "billing_exempt": True, "language": "he"},     # internal test client
    40: {"id": 40, "billing_exempt": False, "language": "he"},    # paying client
    41: {"id": 41, "billing_exempt": "true", "language": "en"},   # NOT a real boolean
}

STATE_NEEDS_RECONNECT = {
    "channels": {"by_channel": {"google_ads": {"status": "active"}, "youtube": {"status": "active"},
                                "website_tracking": {"status": "needs_attention", "reason": "reconnect"}}},
    "connections": {"active_platforms": ["google_ads", "youtube", "google_tagmanager"]},
    "tracking": {"connected": True, "needs_reconnect": True, "lead_event_live": None},
    "approvals": {"waiting_for_client": [{"suggestion_id": 11, "title": "idea 11"}]},
}
STATE_READY = {**STATE_NEEDS_RECONNECT,
               "tracking": {"connected": True, "needs_reconnect": False, "lead_event_live": False}}


class ActionTestCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.engagement, self.website = install_stubs()
        self.posted_client, self.posted_admin, self.alerts = [], [], []
        self.saved = {name: getattr(aa, name) for name in
                      ("_db", "_client", "_post_client_message", "_post_admin_message", "_alert", "_log")}
        aa._db = lambda: self.db
        aa._client = lambda cid: CLIENTS.get(cid, {})
        aa._post_client_message = lambda cid, persona, text: self.posted_client.append((cid, persona, text))
        aa._post_admin_message = lambda cid, persona, text: self.posted_admin.append((cid, persona, text))
        aa._alert = lambda issue: self.alerts.append(issue)
        aa._log = lambda *a, **k: None
        self.demo_calls = []
        self.saved_demo = (vd.create_test_campaign, vd.upload_test_video)
        vd.create_test_campaign = lambda cid: self.demo_calls.append(("campaign", cid)) or {
            "campaign_id": "123", "status": "PAUSED"}
        vd.upload_test_video = lambda cid: self.demo_calls.append(("video", cid)) or {
            "video_id": "abc", "privacy_status": "private"}

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(aa, name, fn)
        vd.create_test_campaign, vd.upload_test_video = self.saved_demo

    def rows(self):
        return self.db.tables.get(aa.TABLE, [])

    def propose(self, client_id, origin, name, params=None, state=STATE_READY, **kw):
        return aa.propose(client_id, origin, "general", {"name": name, "params": params or {}},
                          state=state, **kw)


# ─── propose(): the whitelist ────────────────────────────────────────────────

class ProposeGuards(ActionTestCase):
    def test_unknown_action_refused(self):
        self.assertEqual(self.propose(27, "admin_chat", "delete_everything")["error"], "unknown_action")
        self.assertEqual(self.rows(), [])

    def test_empty_name_is_no_proposal(self):
        self.assertEqual(aa.propose(27, "admin_chat", "general", {"name": ""}), {})

    def test_malformed_payload_refused(self):
        self.assertEqual(aa.propose(27, "admin_chat", "general", "demo_google_ads_campaign")["error"],
                         "malformed")

    def test_client_chat_cannot_propose_demo_actions(self):
        for name in ("demo_google_ads_campaign", "demo_youtube_upload"):
            self.assertEqual(self.propose(27, "client_chat", name)["error"], "not_allowed_here")
        self.assertEqual(self.rows(), [])

    def test_admin_chat_cannot_propose_client_actions(self):
        result = self.propose(27, "admin_chat", "connect_tag_manager", state=STATE_NEEDS_RECONNECT)
        self.assertEqual(result["error"], "not_allowed_here")

    def test_internal_only_refused_for_paying_client(self):
        self.assertEqual(self.propose(40, "admin_chat", "demo_google_ads_campaign")["error"], "internal_only")

    def test_internal_only_needs_a_real_boolean(self):
        self.assertEqual(self.propose(41, "admin_chat", "demo_youtube_upload")["error"], "internal_only")

    def test_unknown_param_is_tampering(self):
        result = self.propose(27, "admin_chat", "configure_conversion", {"container_path": "accounts/1"})
        self.assertEqual(result["error"], "invalid_params")

    def test_bad_ga4_id_refused(self):
        result = self.propose(27, "admin_chat", "configure_conversion",
                              {"ga4_measurement_id": "UA-1234; drop table"})
        self.assertEqual(result["error"], "invalid_params")

    def test_params_on_a_no_param_action_refused(self):
        result = self.propose(27, "admin_chat", "demo_google_ads_campaign", {"status": "ENABLED"})
        self.assertEqual(result["error"], "invalid_params")

    def test_suggestion_must_be_this_clients_pending_one(self):
        result = self.propose(40, "client_chat", "approve_suggestion", {"suggestion_id": 999})
        self.assertEqual(result["error"], "invalid_params")

    def test_not_available_when_state_says_no(self):
        # Tracking fine and nothing to reconnect: no reconnect card
        result = self.propose(40, "client_chat", "connect_tag_manager", state=STATE_READY)
        self.assertEqual(result["error"], "not_available")

    def test_admin_proposal_is_stored_as_proposed(self):
        result = self.propose(27, "admin_chat", "demo_google_ads_campaign")
        self.assertEqual(result["status"], "proposed")
        self.assertTrue(result["card"]["can_decide"])
        self.assertEqual(self.rows()[0]["approver"], "admin")

    def test_client_request_for_staff_action_awaits_staff_and_alerts(self):
        result = self.propose(40, "client_chat", "rotate_wordpress_password")
        self.assertEqual(result["error"], "not_allowed_here")       # not client_requestable
        saved = aa.ACTIONS["rotate_wordpress_password"]["client_requestable"]
        aa.ACTIONS["rotate_wordpress_password"]["client_requestable"] = True
        try:
            state = {**STATE_READY, "connections": {"active_platforms": ["wordpress"]}}
            result = self.propose(40, "client_chat", "rotate_wordpress_password", state=state)
        finally:
            aa.ACTIONS["rotate_wordpress_password"]["client_requestable"] = saved
        self.assertEqual(result["status"], "awaiting_staff")
        self.assertFalse(result["card"]["can_decide"])     # no client button
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("client 40", self.alerts[0])

    def test_lead_tracking_is_the_clients_own_click(self):
        result = self.propose(40, "client_chat", "configure_conversion")
        self.assertEqual(result["status"], "proposed")
        self.assertTrue(result["card"]["can_decide"])
        self.assertEqual(self.rows()[0]["approver"], "client")
        self.assertEqual(self.alerts, [])

    def test_lead_tracking_from_the_control_room_is_approved_there(self):
        result = self.propose(27, "admin_chat", "configure_conversion")
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(self.rows()[0]["approver"], "admin")

    def test_same_open_proposal_is_not_duplicated(self):
        first = self.propose(27, "admin_chat", "demo_google_ads_campaign")
        second = self.propose(27, "admin_chat", "demo_google_ads_campaign")
        self.assertEqual(first["card"]["id"], second["card"]["id"])
        self.assertEqual(len(self.rows()), 1)

    def test_dry_run_stores_nothing(self):
        result = self.propose(27, "admin_chat", "demo_google_ads_campaign", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(self.rows(), [])

    def test_storage_missing_fails_closed(self):
        self.db.fail = True
        self.assertEqual(self.propose(27, "admin_chat", "demo_google_ads_campaign")["error"],
                         "storage_unavailable")

    def test_client_card_never_shows_params(self):
        result = self.propose(40, "client_chat", "approve_suggestion", {"suggestion_id": 11},
                              state=STATE_NEEDS_RECONNECT)
        self.assertEqual(result["card"]["params"], {})


# ─── decide(): the human click ───────────────────────────────────────────────

class DecideGuards(ActionTestCase):
    def stored(self, client_id=27, action="demo_google_ads_campaign", origin="admin_chat",
               approver="admin", status="proposed", params=None, created_at=None):
        row = {"id": len(self.rows()) + 1, "client_id": client_id, "action": action,
               "params": params or {}, "origin": origin, "persona": "general", "approver": approver,
               "status": status, "created_at": created_at or datetime.now(timezone.utc).isoformat()}
        self.db.tables.setdefault(aa.TABLE, []).append(row)
        return row["id"]

    def test_admin_approval_runs_the_demo_once(self):
        pid = self.stored()
        result = aa.decide(pid, "admin", 27, "approve")
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.demo_calls, [("campaign", 27)])
        self.assertEqual(self.rows()[0]["status"], "done")

    def test_double_click_runs_once(self):
        pid = self.stored()
        aa.decide(pid, "admin", 27, "approve")
        second = aa.decide(pid, "admin", 27, "approve")
        self.assertTrue(second["already"])
        self.assertEqual(len(self.demo_calls), 1)

    def test_lost_claim_runs_nothing(self):
        # Another request claimed the row between our read and our claim
        pid = self.stored()
        real_claim = aa._claim

        def racing_claim(proposal_id, from_statuses, changes):
            if changes.get("status") == "running":
                self.rows()[0]["status"] = "running"      # the other request won
                return real_claim(proposal_id, from_statuses, changes)
            return real_claim(proposal_id, from_statuses, changes)
        aa._claim = racing_claim
        try:
            result = aa.decide(pid, "admin", 27, "approve")
        finally:
            aa._claim = real_claim
        self.assertTrue(result.get("already"))
        self.assertEqual(self.demo_calls, [])

    def test_client_cannot_approve_an_admin_action(self):
        pid = self.stored()
        self.assertEqual(aa.decide(pid, "client", 27, "approve")["code"], "not_your_decision")
        self.assertEqual(self.demo_calls, [])

    def test_admin_cannot_approve_a_client_action(self):
        pid = self.stored(action="connect_tag_manager", origin="client_chat", approver="client")
        self.assertEqual(aa.decide(pid, "admin", 27, "approve")["code"], "not_your_decision")

    def test_other_clients_proposal_is_not_found(self):
        pid = self.stored(client_id=40, action="approve_suggestion", origin="client_chat",
                          approver="client", params={"suggestion_id": 11})
        self.assertEqual(aa.decide(pid, "client", 27, "approve")["code"], "not_found")
        self.assertEqual(self.engagement.decide_suggestion.calls, [])

    def test_internal_only_rechecked_at_approval(self):
        # A demo row for a paying client (e.g. inserted by hand, or the flag
        # was turned off after proposing): refused, nothing runs
        pid = self.stored(client_id=40)
        result = aa.decide(pid, "admin", 40, "approve")
        self.assertEqual(result["code"], "internal_only")
        self.assertEqual(self.demo_calls, [])
        self.assertEqual(self.rows()[0]["status"], "failed")

    def test_tampered_stored_params_refused_at_approval(self):
        pid = self.stored(action="configure_conversion", params={"ga4_measurement_id": "evil"})
        self.assertEqual(aa.decide(pid, "admin", 27, "approve")["code"], "invalid_params")
        self.assertEqual(self.website.configure_lead_conversion.calls, [])

    def test_extra_stored_param_refused_at_approval(self):
        pid = self.stored(params={"status": "ENABLED"})
        self.assertEqual(aa.decide(pid, "admin", 27, "approve")["code"], "invalid_params")
        self.assertEqual(self.demo_calls, [])

    def test_expired_proposal_refused(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=aa.PROPOSAL_TTL_HOURS + 1)).isoformat()
        pid = self.stored(created_at=old)
        self.assertEqual(aa.decide(pid, "admin", 27, "approve")["code"], "expired")
        self.assertEqual(self.demo_calls, [])

    def test_cancel_then_approve_runs_nothing(self):
        pid = self.stored()
        self.assertEqual(aa.decide(pid, "admin", 27, "cancel")["status"], "cancelled")
        self.assertTrue(aa.decide(pid, "admin", 27, "approve")["already"])
        self.assertEqual(self.demo_calls, [])

    def test_bad_decision_and_actor_refused(self):
        pid = self.stored()
        self.assertEqual(aa.decide(pid, "admin", 27, "run_now")["code"], "bad_decision")
        self.assertEqual(aa.decide(pid, "model", 27, "approve")["code"], "bad_actor")
        self.assertEqual(self.demo_calls, [])

    def test_link_action_redirects_without_running_anything(self):
        pid = self.stored(action="connect_tag_manager", origin="client_chat", approver="client")
        result = aa.decide(pid, "client", 27, "approve")
        self.assertEqual(result["redirect"], "/api/oauth/gtm/start")

    def test_google_ads_connect_opens_the_consent_card_not_oauth(self):
        pid = self.stored(action="connect_google_ads", origin="client_chat", approver="client")
        result = aa.decide(pid, "client", 27, "approve")
        self.assertEqual(result["focus"], "gadsBtn")
        self.assertNotIn("redirect", result)

    def test_staff_request_approved_by_admin_runs_and_tells_the_client(self):
        pid = self.stored(client_id=40, action="configure_conversion", origin="client_chat",
                          approver="admin", status="awaiting_staff")
        result = aa.decide(pid, "admin", 40, "approve")
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(self.website.configure_lead_conversion.calls), 1)
        self.assertEqual(len(self.posted_client), 1)          # the client hears the outcome

    def test_failure_is_recorded_and_alerted(self):
        def refuse(cid):
            raise vd.DemoRefused(400, "container mismatch")
        vd.create_test_campaign = refuse
        pid = self.stored()
        result = aa.decide(pid, "admin", 27, "approve")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.rows()[0]["outcome"], "refused_by_action")

    def test_suggestion_approval_uses_the_same_function_as_the_button(self):
        pid = self.stored(client_id=40, action="approve_suggestion", origin="client_chat",
                          approver="client", params={"suggestion_id": 11})
        aa.decide(pid, "client", 40, "approve")
        (args, _kw), = self.engagement.decide_suggestion.calls
        self.assertEqual(args[:3], (40, 11, "approved"))


# ─── available(): what a chat is shown ───────────────────────────────────────

class Availability(ActionTestCase):
    def names(self, client_id, origin, state):
        return {a["name"] for a in aa.available(client_id, origin, state, client=CLIENTS[client_id])}

    def test_admin_sees_demo_actions_only_for_internal_clients(self):
        self.assertIn("demo_google_ads_campaign", self.names(27, "admin_chat", STATE_READY))
        self.assertNotIn("demo_google_ads_campaign", self.names(40, "admin_chat", STATE_READY))

    def test_client_sees_reconnect_only_when_needed(self):
        self.assertIn("connect_tag_manager", self.names(40, "client_chat", STATE_NEEDS_RECONNECT))
        self.assertNotIn("connect_tag_manager", self.names(40, "client_chat", STATE_READY))

    def test_client_never_sees_demo_actions(self):
        names = self.names(27, "client_chat", STATE_READY)
        self.assertFalse({"demo_google_ads_campaign", "demo_youtube_upload"} & names)

    def test_client_approved_actions_are_labelled_client_for_the_model(self):
        offered = {a["name"]: a for a in aa.available(40, "client_chat", STATE_READY, client=CLIENTS[40])}
        self.assertEqual(offered["configure_conversion"]["approver"], "client")

    def test_test_campaign_card_only_for_internal_clients_in_their_own_chat(self):
        self.assertIn("google_ads_test_campaign", self.names(27, "client_chat", STATE_READY))
        self.assertNotIn("google_ads_test_campaign", self.names(40, "client_chat", STATE_READY))
        self.assertNotIn("google_ads_test_campaign", self.names(27, "admin_chat", STATE_READY))


# ─── verification_demo: the internal-client refusal itself ───────────────────

class VerificationDemoGuard(unittest.TestCase):
    def setUp(self):
        client_agent = types.ModuleType("agents.client_agent")
        client_agent.get_client = lambda cid: CLIENTS.get(cid, {})
        sys.modules["agents.client_agent"] = client_agent

    def test_paying_client_refused(self):
        with self.assertRaises(vd.DemoRefused) as ctx:
            vd.require_internal_test_client(40)
        self.assertEqual(ctx.exception.status, 403)

    def test_string_true_is_not_internal(self):
        with self.assertRaises(vd.DemoRefused):
            vd.require_internal_test_client(41)

    def test_missing_client_404(self):
        with self.assertRaises(vd.DemoRefused) as ctx:
            vd.require_internal_test_client(999)
        self.assertEqual(ctx.exception.status, 404)

    def test_internal_client_allowed(self):
        self.assertEqual(vd.require_internal_test_client(27)["id"], 27)


# ─── request_connection_help: a stuck client gets a person, never a website ──

class ConnectionHelpCard(ActionTestCase):
    def setUp(self):
        super().setUp()
        from core import connection_help
        self.ch = connection_help
        self.saved_assist = connection_help.request_assist
        self.assists = []
        connection_help.request_assist = lambda cid, conn: self.assists.append((cid, conn)) or {
            "requested": True, "alerted": True}

    def tearDown(self):
        self.ch.request_assist = self.saved_assist
        super().tearDown()

    def test_offered_to_every_client_chat_but_not_internal_only(self):
        names = [a["name"] for a in aa.available(40, "client_chat", {}, client=CLIENTS[40])]
        self.assertIn("request_connection_help", names)
        self.assertFalse(aa.ACTIONS["request_connection_help"]["internal_only"])

    def test_card_names_the_capability_never_the_vendor(self):
        card = self.propose(40, "client_chat", "request_connection_help", {"connection": "higgsfield"})["card"]
        self.assertIn("הדמות שבסרטונים", card["what"])
        self.assertNotIn("Higgsfield", card["what"])
        # HeyGen is retired (2026-10-31): no help entry, so no help card either
        self.assertIn("error", self.propose(40, "client_chat", "request_connection_help", {"connection": "heygen"}))
        self.assertTrue(card["can_decide"])
        self.assertIn("TikTok", self.propose(40, "client_chat", "request_connection_help",
                                             {"connection": "tiktok"})["card"]["what"])

    def test_unknown_or_inactive_connection_refused(self):
        for bad in ("everything", "merchant_center", ""):
            result = self.propose(40, "client_chat", "request_connection_help", {"connection": bad})
            self.assertIn("error", result, bad)
        self.assertEqual(self.rows(), [])

    def test_click_sends_the_same_staff_request_as_the_guide_button(self):
        card = self.propose(40, "client_chat", "request_connection_help", {"connection": "elevenlabs"})["card"]
        self.assertEqual(self.assists, [])                      # proposing runs nothing
        done = aa.decide(card["id"], "client", 40, "approve")
        self.assertEqual(done["status"], "done")
        self.assertEqual(self.assists, [(40, "elevenlabs")])


class RequestAssist(unittest.TestCase):
    """connection_help.request_assist — the ONE staff request behind both doors."""

    def test_one_deduped_alert_per_client_and_connection_a_day(self):
        import core
        from core import connection_help
        calls = []
        stub = types.ModuleType("core.chat_fallback")
        stub.alert_once = lambda source, cid, key, text, days=7: calls.append((source, cid, key, days)) or True
        saved = (sys.modules.get("core.chat_fallback"), getattr(core, "chat_fallback", None))
        sys.modules["core.chat_fallback"], core.chat_fallback = stub, stub
        try:
            self.assertEqual(connection_help.request_assist(40, "tiktok"), {"requested": True, "alerted": True})
            with self.assertRaises(ValueError):
                connection_help.request_assist(40, "nope")
        finally:
            if saved[0] is None:
                sys.modules.pop("core.chat_fallback", None)
            else:
                sys.modules["core.chat_fallback"] = saved[0]
            if saved[1] is None:
                delattr(core, "chat_fallback")
            else:
                core.chat_fallback = saved[1]
        self.assertEqual(calls, [("connection_help", 40, "assist:tiktok", 1)])


class TikTokDraftsHistory(ActionTestCase):
    def add(self, status, outcome="", file_id="1AbCdEfGhIjKlMnOp", client_id=40, action="send_tiktok_to_inbox"):
        self.db.tables.setdefault(aa.TABLE, []).append({
            "id": len(self.rows()) + 1, "client_id": client_id, "action": action, "status": status,
            "outcome": outcome, "params": {"drive_file_id": file_id},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat()})

    def test_sent_failed_running_and_waiting_from_this_clients_rows_only(self):
        aa._media_meta[(40, "1AbCdEfGhIjKlMnOp")] = (aa.time.time(), {"name": "promo.mp4"})
        self.add("done")
        self.add("failed", "pending_limit")
        self.add("running")
        self.add("proposed")
        self.add("done", client_id=41)                              # another client
        self.add("done", action="publish_youtube_from_media")       # another action
        view = aa.tiktok_drafts_sent(40)
        self.assertEqual(view["sent_to_inbox"][0]["file"], "promo.mp4")
        self.assertEqual(len(view["sent_to_inbox"]), 1)
        self.assertEqual(view["not_sent"][0]["reason"], "pending_limit")
        self.assertEqual((view["sending_now"], view["waiting_for_approval"]), (1, 1))
        self.assertIn("פרסם", view["client_must_do"])              # they publish it themselves

    def test_nothing_sent_means_nothing_to_do(self):
        self.assertEqual(aa.tiktok_drafts_sent(40)["client_must_do"], "")

    def test_unreadable_table_raises_so_the_field_is_left_out(self):
        self.db.fail = True
        with self.assertRaises(RuntimeError):
            aa.tiktok_drafts_sent(40)


# ─── client_state: failures are never cached and never reach the model ──────

class ClientStateFailures(unittest.TestCase):
    def setUp(self):
        from core import client_state as cs
        self.cs = cs
        cs._cache.clear()
        self.saved = {n: getattr(cs, n) for n in dir(cs) if n.startswith("_") and callable(getattr(cs, n))
                      and n in ("_channels", "_connections", "_tracking", "_google_campaigns",
                                "_meta_campaigns", "_budget", "_leads", "_approvals", "_website",
                                "_landing_pages", "_media", "_media_folder", "_package",
                                "_site_security", "_outreach", "_creative_tools", "_voice", "_warn")}
        for name in self.saved:
            setattr(cs, name, lambda *a, **k: {"ok": True})
        cs._warn = lambda *a, **k: None
        self.calls = 0

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(self.cs, name, fn)
        self.cs._cache.clear()

    def test_failed_section_left_out_named_and_retried(self):
        def broken(client_id):
            self.calls += 1
            raise RuntimeError("token=SECRET123 expired")
        self.cs._tracking = lambda client_id, channels: broken(client_id)
        state = self.cs.snapshot(5)
        self.assertNotIn("tracking", state)
        self.assertIn("tracking", self.cs.failed_sections(state))
        self.assertEqual(self.cs.failed_topics(state), {"tracking"})
        self.assertNotIn("_failed", self.cs.for_model(state))
        self.cs.snapshot(5)
        self.assertEqual(self.calls, 2)          # a failure is never served from cache

    def test_success_is_cached(self):
        def counted(client_id):
            self.calls += 1
            return {"connected": False}
        self.cs._website = counted
        self.cs.snapshot(6)
        self.cs.snapshot(6)
        self.assertEqual(self.calls, 1)

    def test_channels_failure_takes_its_dependants(self):
        def broken(client_id):
            raise RuntimeError("boom")
        self.cs._channels = broken
        state = self.cs.snapshot(7)
        for name in ("channels", "budget", "leads"):
            self.assertIn(name, self.cs.failed_sections(state))


# ─── Prompt templates: .format() must survive the shared rules ──────────────

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _string_constants(path: str) -> dict:
    """Module-level NAME = "..." (and "..." + NAME + "...") assignments,
    evaluated without importing the module (it needs Supabase)."""
    tree = ast.parse(_read(path))
    values = {}

    def evaluate(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = evaluate(node.left), evaluate(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = evaluate(node.value)
            if value is not None:
                values[node.targets[0].id] = value
    return values


class PromptTemplates(unittest.TestCase):
    def test_persona_template_formats(self):
        support = _string_constants(os.path.join(ROOT, "agents", "support_agent.py"))
        language = _string_constants(os.path.join(ROOT, "agents", "onboarding_agent.py")).get("LANGUAGE_RULE", "")
        for name in ("HANDOFF_RULE", "KNOWLEDGE_RULE", "ACCOUNT_STATE_RULE", "NO_SECRETS_RULE"):
            self.assertNotIn("{", support[name], name)
            self.assertNotIn("}", support[name], name)
        # Rebuild the template the way the module does (LANGUAGE_RULE lives elsewhere)
        src = _read(os.path.join(ROOT, "agents", "support_agent.py"))
        head = src.split('PERSONA_SYSTEM_TEMPLATE = """', 1)[1].split('"""', 1)[0]
        template = (head + support["HANDOFF_RULE"] + support["KNOWLEDGE_RULE"]
                    + support["ACCOUNT_STATE_RULE"] + language + support["NO_SECRETS_RULE"])
        rendered = template.format(name="n", domain="d", voice="v", data_notes="x",
                                   action_exception="", action_rule="", extra_fields="")
        self.assertIn('"proposed_action": {"name": "", "params": {}, "summary": ""}', rendered)
        self.assertIn("FORBIDDEN ANSWERS", rendered)

    def test_concierge_system_carries_the_rules(self):
        support = _string_constants(os.path.join(ROOT, "agents", "support_agent.py"))
        self.assertIn("proposed_action", support["ACCOUNT_STATE_RULE"])

    def test_admin_template_formats(self):
        control = _string_constants(os.path.join(ROOT, "core", "control_room.py"))
        rendered = control["ADMIN_AGENT_SYSTEM"].format(name="n", domain="d", voice="v")
        self.assertIn('"proposed_action": {"name": "", "params": {}, "summary": ""}', rendered)


if __name__ == "__main__":
    unittest.main()
