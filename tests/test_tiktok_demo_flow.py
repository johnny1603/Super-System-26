"""The TikTok app-review flow, end to end in the product (2026-11-07,
docs/TIKTOK-REVIEW.md): each requested scope is USED where the reviewer can
see it.

- user.info.basic → the TikTok card shows "Connected as <name>" + the avatar,
  read live from /v2/user/info/ with the client's own grant
  (tiktok_content_agent.get_profile, GET /api/client/tiktok/profile).
- video.upload → a "Send to TikTok" button on each video tile opens the SAME
  client-approved send_tiktok_to_inbox card the chat offers
  (POST /api/client/media/send-to-tiktok); only Approve uploads.

Stdlib only. Run from the repo root:  python tests/test_tiktok_demo_flow.py -v
"""
import ast
import os
import re
import sys
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_tiktok_inbox_action import SMALL, BIG, IMAGE, TikTokActionCase, state   # noqa: E402


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


API_SRC = _src("core", "api_server.py")
DASH = _src("dashboard", "client", "index.html")


def _function(name: str) -> str:
    node = next(n for n in ast.walk(ast.parse(API_SRC))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(API_SRC, node)


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class TikTokAPIError(RuntimeError):
    def __init__(self, message, code=None, log_id=None):
        super().__init__(message)
        self.code, self.log_id = code, log_id


# ─── user.info.basic: the connected account on the card ─────────────────────

class Profile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = ("supabase", "core.tiktok_service", "core.agent_base", "core.secrets_vault",
                     "core.drive_storage", "core.tiktok_grant", "agents.tiktok_content_agent")
        cls.saved = {n: sys.modules.get(n) for n in cls.names}
        cls.calls, cls.answers, cls.grant = [], {}, {"state": "ok"}

        def get_user_info(token):
            cls.calls.append(("user_info", token))
            answer = cls.answers.get(token)
            if isinstance(answer, Exception):
                raise answer
            return answer or {}

        tokens = {"access_token_invalid", "access_token_expired"}
        sys.modules["supabase"] = _module("supabase", create_client=lambda *a, **k: None)
        sys.modules["core.tiktok_service"] = _module(
            "core.tiktok_service", get_user_info=get_user_info,
            split_tokens=lambda s: tuple(s.partition("::")[::2]),
            is_token_error=lambda e: getattr(e, "code", None) in tokens,
            is_dead_grant=lambda e: getattr(e, "code", None) in tokens | {"invalid_grant"},
            is_scope_error=lambda e: getattr(e, "code", None) in {"scope_not_authorized"})
        sys.modules["core.agent_base"] = _module("core.agent_base", agent_alert=lambda *a, **k: None,
                                                 log_step=lambda *a, **k: None, timed_step=lambda a, s, f: f())
        sys.modules["core.secrets_vault"] = _module("core.secrets_vault", hydrate=lambda r: r)
        sys.modules["core.drive_storage"] = _module("core.drive_storage", scoped=lambda f: f)
        sys.modules["core.tiktok_grant"] = _module(
            "core.tiktok_grant", state=lambda cid: cls.grant["state"],
            needs_reconnect=lambda cid: cls.grant["state"] in ("dead", "missing_upload"))
        sys.modules.pop("agents.tiktok_content_agent", None)
        from agents import tiktok_content_agent
        cls.agent = tiktok_content_agent

    @classmethod
    def tearDownClass(cls):
        for name, module in cls.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self):
        self.calls.clear()
        self.answers.clear()
        self.grant["state"] = "ok"
        self.agent._profile_cache.clear()
        self.conn = {"account_id": "-open-1", "access_token": "act.live::rft.1"}
        self.agent._get_connection = lambda cid: dict(self.conn)
        self.refreshed = []
        self.agent._live_access_token = lambda cid, conn: (self.refreshed.append(cid), "act.fresh")[1]

    def test_name_and_avatar_come_from_user_info_with_the_clients_own_token(self):
        self.answers["act.live"] = {"open_id": "-open-1", "display_name": "Shin Sekai",
                                    "avatar_url": "https://p16-sign.tiktokcdn.com/a.jpeg"}
        profile = self.agent.get_profile(27)
        self.assertEqual(profile, {"connected": True, "display_name": "Shin Sekai",
                                   "avatar_url": "https://p16-sign.tiktokcdn.com/a.jpeg", "needs_reconnect": False})
        self.assertEqual(self.calls, [("user_info", "act.live")])
        self.assertEqual(self.refreshed, [])                     # the stored token lives 24 h
        self.agent.get_profile(27)
        self.assertEqual(len(self.calls), 1)                     # cached

    def test_an_expired_token_is_refreshed_once(self):
        self.answers["act.live"] = TikTokAPIError("expired", code="access_token_expired")
        self.answers["act.fresh"] = {"display_name": "Shin Sekai", "avatar_url": ""}
        profile = self.agent.get_profile(27)
        self.assertEqual(profile["display_name"], "Shin Sekai")
        self.assertEqual(self.refreshed, [27])

    def test_not_connected_dead_and_failing_are_plain_states(self):
        self.agent._get_connection = lambda cid: {}
        self.assertEqual(self.agent.get_profile(27), {"connected": False})
        self.agent._get_connection = lambda cid: dict(self.conn)
        self.grant["state"] = "dead"
        self.assertTrue(self.agent.get_profile(27)["needs_reconnect"])
        self.assertEqual(self.calls, [])                         # a known dead grant is not called
        self.grant["state"] = "ok"
        self.answers["act.live"] = RuntimeError("network")
        self.assertTrue(self.agent.get_profile(27)["unavailable"])

    def test_only_https_avatars_and_a_bounded_name(self):
        self.answers["act.live"] = {"display_name": "x" * 500, "avatar_url": "javascript:alert(1)"}
        profile = self.agent.get_profile(27)
        self.assertEqual(len(profile["display_name"]), 100)
        self.assertEqual(profile["avatar_url"], "")

    def test_the_route_and_the_card(self):
        route = _function("client_tiktok_profile")
        self.assertIn("_require_session(request)", route)
        self.assertIn("get_profile(client_id)", route)
        self.assertIn('id="tiktokAccount"', DASH)
        fn = DASH[DASH.index("async function loadTiktokProfile"):]
        fn = fn[:fn.index("\n}\n")]
        self.assertIn("/api/client/tiktok/profile", fn)
        self.assertNotIn("innerHTML", fn)                        # textContent + an https img only
        self.assertIn("startsWith('https://')", fn)
        self.assertIn("loadTiktokProfile()", DASH[DASH.index("function markTiktokConnected"):][:400])
        # the callback drops a cached profile so a new account shows at once
        callback = _function("tiktok_oauth_callback")
        self.assertIn("forget_profile(client_id)", callback)


# ─── video.upload: "Send to TikTok" on a video tile ─────────────────────────

class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(status_code)
        self.status_code, self.detail = status_code, detail


class SendButton(TikTokActionCase):
    def setUp(self):
        super().setUp()
        self.logged = []
        self.extra = {n: sys.modules.get(n) for n in
                      ("core.client_state", "agents.support_agent", "agents.client_agent")}
        self.snapshot = state()
        sys.modules["core.client_state"] = types.SimpleNamespace(snapshot=lambda cid: self.snapshot,
                                                                 forget=lambda cid: None)
        sys.modules["agents.support_agent"] = types.SimpleNamespace(persona_channel=lambda p: f"persona:{p}")
        sys.modules["agents.client_agent"] = types.SimpleNamespace(
            log_communication=lambda *a, **k: self.logged.append(a))
        ns = {"re": re, "HTTPException": HTTPException, "Request": object,
              "TikTokSendRequest": object, "_require_session": lambda request: 27,
              "_admin_view_client": lambda request: self.viewing}
        self.viewing = None
        exec(compile(_function("client_media_send_to_tiktok"), "core/api_server.py", "exec"), ns)
        self.route = ns["client_media_send_to_tiktok"]

    def tearDown(self):
        for name, module in self.extra.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        super().tearDown()

    def click(self, file_id):
        return self.route(types.SimpleNamespace(drive_file_id=file_id), object())["data"]

    def test_the_button_opens_the_approval_card_and_sends_nothing(self):
        data = self.click(SMALL)
        self.assertEqual(data["persona"], "media")
        self.assertEqual(data["card"]["action"], "send_tiktok_to_inbox")
        self.assertIn("promo-reel.mp4", data["card"]["what"])
        self.assertEqual(self.tiktok.sent, [])                   # only Approve uploads
        self.assertEqual(self.rows()[0]["params"], {"drive_file_id": SMALL})
        self.assertEqual(self.click(SMALL)["card"]["id"], data["card"]["id"])   # no second card

    def test_approve_on_that_card_uploads_to_the_inbox(self):
        card = self.click(SMALL)["card"]
        from core import agent_actions
        out = agent_actions.decide(card["id"], "client", 27, "approve")
        self.assertEqual(self.tiktok.sent, [(27, {"drive_file_id": SMALL})])
        self.assertTrue(out.get("success"), out)

    def test_wrong_files_are_refused_by_the_same_validator(self):
        for file_id in (BIG, IMAGE):
            data = self.click(file_id)
            self.assertIsNone(data["card"])
            self.assertEqual(data["error"], "invalid_params")
        self.assertEqual(self.rows(), [])

    def test_not_connected_switches_to_the_connect_card_with_its_line(self):
        self.tiktok.grant = "not_connected"
        data = self.click(SMALL)
        self.assertEqual(data["card"]["action"], "connect_tiktok")
        self.assertTrue(data["notice"])
        self.assertTrue(self.logged)                             # the line is in the media thread

    def test_not_in_plan_and_bad_ids_and_read_only_view(self):
        self.snapshot = state(tiktok_status="not_in_plan")
        self.assertEqual(self.click(SMALL)["error"], "not_available")
        with self.assertRaises(HTTPException) as caught:
            self.click("../../etc")
        self.assertEqual(caught.exception.status_code, 400)
        self.viewing = 99
        with self.assertRaises(HTTPException) as caught:
            self.click(SMALL)
        self.assertEqual(caught.exception.status_code, 403)

    def test_the_tile_button_and_english_texts(self):
        render = DASH[DASH.index("function renderDriveMirror"):]
        render = render[:render.index("\n}\n")]
        self.assertIn("data-tiktok=", render)
        self.assertIn("TIKTOK_CONNECTED", render)
        send = DASH[DASH.index("async function sendVideoToTiktok"):]
        send = send[:send.index("\n}\n")]
        self.assertIn("/api/client/media/send-to-tiktok", send)
        self.assertIn("appendActionCard(", send)
        expected = {"tt_send": "Send to TikTok", "tt_connected_as": "Connected as {name}"}
        for key in ("tt_send", "tt_connected_as", "tt_profile_unavailable", "tt_needs_reconnect",
                    "tt_send_opening", "tt_send_failed", "tt_not_in_plan", "tt_too_large"):
            line = re.search(r"^  %s: \{(.*)\},$" % key, DASH, re.M)
            self.assertIsNotNone(line, key)
            for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
                self.assertIn(lang, line.group(1))
            if key in expected:
                self.assertIn("en:'%s'" % expected[key], line.group(1))


class EnglishConnectChat(unittest.TestCase):
    def test_the_connect_steps_speak_english_to_an_english_client(self):
        from core import connection_catalog as cc
        saved = cc.entry
        cc.entry = lambda key: {"key": key, "tier": "free_account", "enabled": True, "signup_url": "",
                                "pricing_url": "", "recommended_plan_note": "", "help_notes": ""}
        try:
            start = cc.flow(27, "tiktok", "start", lang="en")
            self.assertEqual(start["messages"], ["Let's connect TikTok. Do you already have an account there?"])
            self.assertEqual([c["label"] for c in start["chips"]], ["I have an account", "Not yet"])
            has = cc.flow(27, "tiktok", "has", lang="en")
            self.assertEqual(has["connect"], "tiktok")
            self.assertTrue(has["messages"][0].startswith("Great. Opening the TikTok connection"))
            self.assertIn("נחבר את TikTok", cc.flow(27, "tiktok", "start")["messages"][0])   # Hebrew unchanged
        finally:
            cc.entry = saved
        route = _function("client_connect_chat")
        self.assertIn("flow(client_id, key, req.step, lang=lang)", route)
        self.assertIn("chip_label(req.step, lang)", route)


class Scopes(unittest.TestCase):
    def test_both_requested_scopes_are_used_in_the_product(self):
        service = _src("core", "tiktok_service.py")
        self.assertIn('OAUTH_SCOPES = ["user.info.basic", UPLOAD_SCOPE]', service)
        agent = _src("agents", "tiktok_content_agent.py")
        self.assertIn("tiktok.get_user_info(", agent)            # user.info.basic → the card
        self.assertIn("tiktok.init_inbox_upload(", agent)        # video.upload → the inbox draft
        self.assertNotIn("init_direct_post(", agent)


if __name__ == "__main__":
    unittest.main()
