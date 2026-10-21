"""First Google sign-in needs the emailed code (core/client_auth +
core/session's pending-link token).

Stdlib only: httpx, keys, the client/login_codes store and the mailer are
in-memory stubs; client_auth and session run for real.

Run from the repo root:  python tests/test_google_link.py -v
"""
import base64
import json
import os
import sys
import time
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ─── In-memory clients + login_codes ─────────────────────────────────────────

class Store:
    def __init__(self):
        self.clients, self.codes, self.activity, self.mail = {}, [], [], []

    def add_client(self, cid, email, sub=None, status="active"):
        self.clients[cid] = {"id": cid, "email": email, "name": "Dana", "google_sub": sub,
                             "status": status, "session_version": 0, "email_verified_at": None}
        return self.clients[cid]


STORE = Store()


def _client_agent_stub():
    m = types.ModuleType("agents.client_agent")

    def create_login_code(client_id, code, expires_at):
        row = {"id": len(STORE.codes) + 1, "client_id": client_id, "code": code,
               "expires_at": expires_at, "used": False, "failed_attempts": 0,
               "created_at": time.time() + len(STORE.codes) * 1e-6}
        STORE.codes.append(row)
        return dict(row)

    def get_active_login_code(client_id):
        rows = [r for r in STORE.codes if r["client_id"] == client_id and not r["used"]]
        return dict(max(rows, key=lambda r: r["created_at"])) if rows else {}

    def increment_login_code_attempts(code_id, current):
        for r in STORE.codes:
            if r["id"] == code_id:
                r["failed_attempts"] = current + 1
        return current + 1

    def mark_login_code_used(code_id):
        for r in STORE.codes:
            if r["id"] == code_id:
                r["used"] = True
        return {}

    m.create_login_code = create_login_code
    m.get_active_login_code = get_active_login_code
    m.increment_login_code_attempts = increment_login_code_attempts
    m.mark_login_code_used = mark_login_code_used
    m.get_client_by_email = lambda email: next(
        (dict(c) for c in STORE.clients.values() if c["email"] == email), {})
    m.log_activity = lambda cid, agent, action, details, result: STORE.activity.append(
        (cid, agent, action, details))
    return m


class _ClientsTable:
    def __init__(self):
        self.op, self.filters, self.values = "select", [], None

    def select(self, *_a):
        return self

    def update(self, values):
        self.op, self.values = "update", values
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def limit(self, _n):
        return self

    def execute(self):
        rows = [c for c in STORE.clients.values() if all(c.get(k) == v for k, v in self.filters)]
        if self.op == "update":
            for c in rows:
                c.update(self.values)
        return types.SimpleNamespace(data=[dict(c) for c in rows])


class _DB:
    def table(self, name):
        assert name == "clients", name
        return _ClientsTable()


def _mail_stub():
    m = types.ModuleType("core.email_service")
    m.send_login_code = lambda email, name, code, client_id, language=None, purpose="login": \
        STORE.mail.append({"to": email, "code": code, "purpose": purpose})
    return m


_STUBS = {
    "httpx": types.ModuleType("httpx"),
    "agents.keys_agent": types.SimpleNamespace(get_key=lambda k: "test-secret-" + k),
    "agents.client_agent": _client_agent_stub(),
    "core.email_service": _mail_stub(),
}
_saved = {name: sys.modules.get(name) for name in _STUBS}
sys.modules.update(_STUBS)
for _name in ("core.client_auth", "core.session"):
    sys.modules.pop(_name, None)
from core import client_auth as ca   # noqa: E402
from core import session as ss       # noqa: E402
ca._db = lambda: _DB()

MAX = 5


class FirstGoogleLink(unittest.TestCase):
    def setUp(self):
        STORE.__init__()
        self.client = STORE.add_client(27, "dana@example.com")
        self.identity = {"sub": "google-sub-111", "email": "dana@example.com", "email_verified": True}

    def fresh(self):
        return dict(STORE.clients[27])

    def start(self):
        client = ca.find_client_for_google(self.identity)
        self.assertEqual(ca.google_callback_outcome(client, self.identity), "link_code")
        return ca.start_google_link(client, self.identity)

    # 1. first login needs the code
    def test_first_login_requires_code_and_links_nothing_yet(self):
        pending = self.start()
        self.assertIsNone(STORE.clients[27]["google_sub"])           # not linked
        self.assertEqual(len(STORE.mail), 1)
        self.assertEqual(STORE.mail[0]["purpose"], "google_link")
        self.assertEqual(pending["code_id"], STORE.codes[0]["id"])

    def test_correct_code_links_and_verifies_email(self):
        pending = self.start()
        result = ca.complete_google_link(self.fresh(), pending["sub"], pending["code_id"],
                                         STORE.mail[0]["code"], MAX)
        self.assertTrue(result["success"])
        self.assertEqual(STORE.clients[27]["google_sub"], "google-sub-111")
        self.assertIsNotNone(STORE.clients[27]["email_verified_at"])
        actions = [a[2] for a in STORE.activity]
        self.assertIn("google_link_code_sent", actions)
        self.assertIn("google_link_verified", actions)

    def test_code_never_lands_in_activity(self):
        pending = self.start()
        ca.complete_google_link(self.fresh(), pending["sub"], pending["code_id"], STORE.mail[0]["code"], MAX)
        self.assertNotIn(STORE.mail[0]["code"], json.dumps(STORE.activity))

    # 2. second login with the linked sub goes straight in
    def test_linked_sub_logs_in_without_code(self):
        STORE.clients[27]["google_sub"] = "google-sub-111"
        client = ca.find_client_for_google(self.identity)
        self.assertEqual(ca.google_callback_outcome(client, self.identity), "login")
        self.assertEqual(STORE.mail, [])

    # 3. wrong code
    def test_wrong_code_keeps_the_step_open_until_the_attempt_limit(self):
        pending = self.start()
        for _ in range(MAX - 1):
            r = ca.complete_google_link(self.fresh(), pending["sub"], pending["code_id"], "000000", MAX)
            self.assertEqual(r["code"], "ERR_INVALID_CODE")
        last = ca.complete_google_link(self.fresh(), pending["sub"], pending["code_id"], "000000", MAX)
        self.assertEqual(last["code"], "ERR_LINK_EXPIRED")             # burnt on the limit
        right = ca.complete_google_link(self.fresh(), pending["sub"], pending["code_id"],
                                        STORE.mail[0]["code"], MAX)
        self.assertFalse(right["success"])                             # even the right code now
        self.assertIsNone(STORE.clients[27]["google_sub"])

    def test_expired_code_refused(self):
        pending = self.start()
        STORE.codes[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        r = ca.complete_google_link(self.fresh(), pending["sub"], pending["code_id"], STORE.mail[0]["code"], MAX)
        self.assertEqual(r["code"], "ERR_LINK_EXPIRED")

    # 4. the pending token is single-use
    def test_reused_pending_token_cannot_link_again(self):
        pending = self.start()
        code = STORE.mail[0]["code"]
        self.assertTrue(ca.complete_google_link(self.fresh(), pending["sub"], pending["code_id"], code, MAX)["success"])
        STORE.clients[27]["google_sub"] = None          # even if the link were undone
        again = ca.complete_google_link(self.fresh(), pending["sub"], pending["code_id"], code, MAX)
        self.assertEqual(again["code"], "ERR_LINK_EXPIRED")
        self.assertIsNone(STORE.clients[27]["google_sub"])

    def test_superseded_by_a_newer_code(self):
        pending = self.start()
        _client_agent = sys.modules["agents.client_agent"]
        _client_agent.create_login_code(27, "123456", (datetime.now(timezone.utc)
                                                       + timedelta(minutes=10)).isoformat())
        r = ca.complete_google_link(self.fresh(), pending["sub"], pending["code_id"], "123456", MAX)
        self.assertEqual(r["code"], "ERR_LINK_EXPIRED")                # a code from another flow
        self.assertIsNone(STORE.clients[27]["google_sub"])

    # 5. a different sub is refused
    def test_account_linked_to_a_different_sub_is_refused(self):
        STORE.clients[27]["google_sub"] = "google-sub-OTHER"
        client = ca.find_client_for_google(self.identity)
        self.assertEqual(client, {})
        self.assertEqual(ca.google_callback_outcome(client, self.identity), "refuse")
        self.assertEqual(STORE.mail, [])

    def test_different_sub_refused_at_the_code_step_too(self):
        pending = self.start()
        STORE.clients[27]["google_sub"] = "google-sub-OTHER"   # linked meanwhile by someone else
        r = ca.complete_google_link(self.fresh(), pending["sub"], pending["code_id"], STORE.mail[0]["code"], MAX)
        self.assertEqual(r["code"], "ERR_GOOGLE_LINKED_ELSEWHERE")
        self.assertEqual(STORE.clients[27]["google_sub"], "google-sub-OTHER")

    def test_unverified_google_email_matches_nothing(self):
        identity = {**self.identity, "email_verified": False}
        self.assertEqual(ca.find_client_for_google(identity), {})


class PendingToken(unittest.TestCase):
    def test_round_trip(self):
        token = ss.create_google_link_token(27, "sub-1", 9, "nonce-A")
        self.assertEqual(ss.verify_google_link_token(token, "nonce-A"),
                         {"client_id": 27, "sub": "sub-1", "code_id": 9})

    def test_bound_to_the_nonce_cookie(self):
        token = ss.create_google_link_token(27, "sub-1", 9, "nonce-A")
        self.assertIsNone(ss.verify_google_link_token(token, "nonce-B"))
        self.assertIsNone(ss.verify_google_link_token(token, ""))

    def test_tampered_payload_refused(self):
        token = ss.create_google_link_token(27, "sub-1", 9, "nonce-A")
        payload_b64, sig = token.split(".", 1)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        payload["client_id"] = 28
        forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode() + "." + sig
        self.assertIsNone(ss.verify_google_link_token(forged, "nonce-A"))

    def test_expires_after_ten_minutes(self):
        token = ss.create_google_link_token(27, "sub-1", 9, "nonce-A")
        real = time.time
        try:
            ss.time.time = lambda: real() + ss.GOOGLE_LINK_MAX_AGE_SECONDS + 1
            self.assertIsNone(ss.verify_google_link_token(token, "nonce-A"))
        finally:
            ss.time.time = real

    def test_other_tokens_never_verify_as_a_link_token(self):
        state = ss.create_google_login_state("nonce-A")
        session = ss.create_session_token(27)
        self.assertIsNone(ss.verify_google_link_token(state, "nonce-A"))
        self.assertIsNone(ss.verify_google_link_token(session, "nonce-A"))
        link = ss.create_google_link_token(27, "sub-1", 9, "nonce-A")
        self.assertIsNone(ss.verify_session_token(link))


class NoAccountDiagnosis(unittest.TestCase):
    LOCKED = staticmethod(lambda c: c.get("status") in ("closed", "transferred", "deleting"))

    def setUp(self):
        STORE.__init__()
        ca._no_account_alerted.clear()

    def diagnose(self, email, verified=True, sub="sub-new"):
        return ca.google_no_account_diagnosis(
            {"sub": sub, "email": email, "email_verified": verified}, self.LOCKED)

    def assert_safe(self, text, email, sub="sub-new"):
        self.assertNotIn(email, text)
        self.assertNotIn(email.split("@")[0], text)
        self.assertNotIn(sub, text)

    def test_unknown_email(self):
        d = self.diagnose("stranger@example.com")
        self.assertEqual(d["reason"], "no_client_with_this_email")
        self.assertFalse(d["matched_by_email"] or d["matched_by_sub"])
        text = ca.no_account_alert_text(d)
        self.assertIn("google_email=s***@example.com", text)
        self.assertIn("no client", text)
        self.assert_safe(text, "stranger@example.com")

    def test_unverified_google_email(self):
        STORE.add_client(27, "dana@example.com")
        d = self.diagnose("dana@example.com", verified=False)
        self.assertEqual(d["reason"], "google_email_not_verified")
        self.assertTrue(d["matched_by_email"])
        text = ca.no_account_alert_text(d)
        self.assertIn("client 27", text)
        self.assertIn("email_verified=False", text)
        self.assertIn("client_status=active", text)
        self.assert_safe(text, "dana@example.com")

    def test_linked_to_another_google_account(self):
        STORE.add_client(27, "dana@example.com", sub="sub-OTHER")
        d = self.diagnose("dana@example.com")
        self.assertEqual(d["reason"], "account_linked_to_another_google_account")
        self.assertNotIn("sub-OTHER", ca.no_account_alert_text(d))

    def test_closed_account_matched_by_sub(self):
        STORE.add_client(27, "dana@example.com", sub="sub-new", status="closed")
        d = self.diagnose("dana@example.com")
        self.assertEqual(d["reason"], "account_closed_or_being_deleted")
        self.assertTrue(d["matched_by_sub"])
        self.assertIn("client_status=closed", ca.no_account_alert_text(d))

    def test_retries_are_one_alert(self):
        d = self.diagnose("stranger@example.com")
        self.assertIsNotNone(ca.no_account_alert_text(d))
        self.assertIsNone(ca.no_account_alert_text(self.diagnose("stranger@example.com")))
        self.assertIsNone(ca.no_account_alert_text(self.diagnose("STRANGER@example.com")))
        self.assertIsNotNone(ca.no_account_alert_text(self.diagnose("other@example.com")))

    def test_mask(self):
        self.assertEqual(ca.mask_email("dana@example.com"), "d***@example.com")
        self.assertEqual(ca.mask_email(""), "(none)")
        self.assertEqual(ca.mask_email("weird"), "w***")


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
