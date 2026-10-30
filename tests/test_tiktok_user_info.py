"""TikTok connect: the open_id bug found live in the sandbox (2026-10-31).

/v2/user/info/ nests the account in data.user; the old get_user_info
returned data itself, so the callback found no open_id. Now get_user_info
unwraps data.user, the stored account id comes from the TOKEN response's
open_id (account_open_id), and a truly missing open_id gives the client a
plain line and the team one alert with the log_id.

Also pins the response shape every other caller relies on (inbox init,
status fetch, video/list, video/query): those put their fields directly in
"data", so they have no such nesting.

Stdlib only: httpx and keys are stubs. Run from tests/:  python test_tiktok_user_info.py -v
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class Response:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


CALLS = []
ANSWERS = {}


def _answer(method, url, **kwargs):
    CALLS.append((method, url, kwargs))
    for path, response in ANSWERS.items():
        if url.endswith(path):
            return response
    raise AssertionError(f"unexpected {method} {url}")


_NAMES = ("httpx", "agents.keys_agent", "core.tiktok_service")
_saved = {n: sys.modules.get(n) for n in _NAMES}
sys.modules["httpx"] = _module("httpx", Response=Response,
                               get=lambda url, **k: _answer("GET", url, **k),
                               post=lambda url, **k: _answer("POST", url, **k))
sys.modules["agents.keys_agent"] = _module("agents.keys_agent", get_key=lambda k: "x")
sys.modules.pop("core.tiktok_service", None)
from core import tiktok_service as tt   # noqa: E402

OK = {"code": "ok", "message": "", "log_id": "20261031120000ABCDEF"}
# What TikTok's /v2/user/info/ really answers (fields=open_id,display_name,avatar_url)
USER_INFO = {"data": {"user": {"open_id": "-000aBcDeF-user-1", "display_name": "Shin Sekai",
                               "avatar_url": "https://p16-sign.tiktokcdn.com/avatar.jpeg"}},
             "error": OK}
# What /v2/oauth/token/ answers for an authorization code
TOKEN = {"access_token": "act.example", "refresh_token": "rft.example", "open_id": "-000aBcDeF-user-1",
         "scope": "user.info.basic,video.upload", "expires_in": 86400, "refresh_expires_in": 31536000,
         "token_type": "Bearer"}


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


class UserInfo(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        ANSWERS.clear()
        ANSWERS["/user/info/"] = Response(200, USER_INFO)

    def test_the_old_parsing_finds_no_open_id_in_a_real_payload(self):
        # The bug, reproduced: data → {"user": {...}}, open_id one level down
        old = tt.api_get("user/info/", "act", params={"fields": tt.USER_INFO_FIELDS})
        self.assertEqual(old.get("open_id", ""), "")
        self.assertEqual(set(old), {"user"})

    def test_get_user_info_returns_the_user_object_itself(self):
        user = tt.get_user_info("act")
        self.assertEqual(user["open_id"], "-000aBcDeF-user-1")
        self.assertEqual(user["display_name"], "Shin Sekai")
        method, url, kwargs = CALLS[-1]
        self.assertEqual((method, url), ("GET", f"{tt.API_BASE}/user/info/"))
        self.assertEqual(kwargs["params"], {"fields": "open_id,display_name,avatar_url"})

    def test_the_log_id_comes_back_for_alerts(self):
        user, log_id = tt.get_user_info_with_log("act")
        self.assertEqual(log_id, "20261031120000ABCDEF")

    def test_a_missing_user_object_is_empty_not_a_crash(self):
        ANSWERS["/user/info/"] = Response(200, {"data": {}, "error": OK})
        self.assertEqual(tt.get_user_info_with_log("act"), ({}, "20261031120000ABCDEF"))

    def test_an_error_body_raises_with_its_log_id(self):
        ANSWERS["/user/info/"] = Response(401, {"data": {}, "error": {
            "code": "access_token_invalid", "message": "bad", "log_id": "LOG401"}})
        with self.assertRaises(tt.TikTokAPIError) as e:
            tt.get_user_info("act")
        self.assertEqual(e.exception.log_id, "LOG401")


class AccountId(unittest.TestCase):
    def test_the_token_response_is_authoritative(self):
        self.assertEqual(tt.account_open_id(TOKEN, {}), ("-000aBcDeF-user-1", ""))
        # user/info broken or reshaped again: connecting still works
        self.assertEqual(tt.account_open_id(TOKEN, {"user": {"open_id": "x"}})[0], "-000aBcDeF-user-1")

    def test_a_disagreement_is_logged_and_the_token_wins(self):
        open_id, note = tt.account_open_id(TOKEN, {"open_id": "-other"})
        self.assertEqual(open_id, "-000aBcDeF-user-1")
        self.assertIn("differs", note)

    def test_user_info_is_only_the_fallback(self):
        open_id, note = tt.account_open_id({"access_token": "a"}, USER_INFO["data"]["user"])
        self.assertEqual(open_id, "-000aBcDeF-user-1")
        self.assertIn("no open_id", note)
        self.assertEqual(tt.account_open_id({}, {}), ("", ""))
        self.assertEqual(tt.account_open_id(None, None), ("", ""))


class Callback(unittest.TestCase):
    def setUp(self):
        api = _src("core", "api_server.py")
        start = api.index("def tiktok_oauth_callback(")
        self.callback = api[start:api.index("# ─── Meta execution", start)]

    def test_the_account_id_comes_from_account_open_id(self):
        self.assertIn("tiktok_service.account_open_id(tokens, user)", self.callback)
        self.assertNotIn('user.get("open_id"', self.callback)
        self.assertIn("upsert_account(client_id, \"tiktok\", open_id,", self.callback)

    def test_user_info_failing_does_not_stop_the_connection(self):
        guarded = self.callback[self.callback.index("try:\n            user, info_log_id"):
                                self.callback.index("account_open_id")]
        self.assertIn("except Exception as e:", guarded)

    def test_missing_open_id_is_a_plain_line_and_one_alert_with_the_log_id(self):
        missing = self.callback[self.callback.index("if not open_id:"):]
        missing = missing[:missing.index("return RedirectResponse") + 80]
        self.assertIn("alert_once(", missing)
        self.assertIn("log_id", missing)
        self.assertIn("connect_error=tiktok_no_account", missing)
        html = _src("dashboard", "client", "index.html")
        self.assertIn("params.get('connect_error') === 'tiktok_no_account'", html)
        line = re.search(r"^  notice_tiktok_no_account: \{(.*)\},$", html, re.M)
        self.assertIsNotNone(line)
        for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
            self.assertIn(lang, line.group(1))
        # the specific branch must come before the generic connect_error one
        self.assertLess(html.index("=== 'tiktok_no_account'"), html.index("} else if (params.get('connect_error')) {"))


class OtherEndpointsHaveNoNesting(unittest.TestCase):
    """TikTok's documented shapes for the other calls: the fields sit
    directly in "data" — what each caller reads."""

    def setUp(self):
        CALLS.clear()
        ANSWERS.clear()

    def test_inbox_init_and_status(self):
        ANSWERS["/post/publish/inbox/video/init/"] = Response(200, {
            "data": {"publish_id": "v_inbox_file~v2.1", "upload_url": "https://open-upload.tiktokapis.com/x"},
            "error": OK})
        init = tt.init_inbox_upload("act", 10, 10, 1)
        self.assertEqual(init["publish_id"], "v_inbox_file~v2.1")    # tiktok_content_agent reads init["publish_id"]
        ANSWERS["/post/publish/status/fetch/"] = Response(200, {
            "data": {"status": "SEND_TO_USER_INBOX", "uploaded_bytes": 10}, "error": OK})
        self.assertEqual(tt.get_post_status("act", "p")["status"], "SEND_TO_USER_INBOX")

    def test_video_list_and_query(self):
        saved = tt.ENGAGEMENT_READS_ENABLED
        tt.ENGAGEMENT_READS_ENABLED = True
        try:
            ANSWERS["/video/list/"] = Response(200, {
                "data": {"videos": [{"id": "1", "view_count": 5}], "cursor": 0, "has_more": False}, "error": OK})
            self.assertEqual(tt.list_videos("act")["videos"][0]["id"], "1")
            ANSWERS["/video/query/"] = Response(200, {"data": {"videos": [{"id": "1"}]}, "error": OK})
            self.assertEqual(tt.query_videos("act", ["1"])["videos"][0]["id"], "1")
        finally:
            tt.ENGAGEMENT_READS_ENABLED = saved


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
