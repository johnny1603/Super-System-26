"""Gmail SEND plumbing for Google verification ROUND 2 — behind
GMAIL_SEND_ENABLED, OFF by default. docs/GOOGLE-ROUND2.md is the source of
truth for the scope, the justification and the demo.

While the flag is off (the default, and the state while round 1 of Google's
verification is under review): the scope is never requested —
build_consent_url raises GmailSendDisabled, the OAuth routes answer 404, no
action card is offered and the dashboard card stays hidden. Nothing here is
called.

Auth model — its own consent, like YouTube/GTM: same OAuth client
(GOOGLE_OAUTH_CLIENT_ID/SECRET), its own client_accounts row
platform='gmail_send', account_id=the sending address (from the OpenID
userinfo of THIS grant), access_token=refresh token.

SEND ONLY. The one Gmail API method this module calls is
users.messages.send. There is no list/get/history/threads/labels call
anywhere, and gmail.send cannot read the mailbox even if one were added
(tests/test_google_round2.py asserts the module has no read path).

VERIFICATION STATUS: written against the Gmail API v1 reference, never run
with a live grant.
"""
import base64
import os
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from urllib.parse import urlencode

import httpx

from agents.keys_agent import get_key
from core.google_ads_service import OAUTH_CONSENT_URL, _access_token  # noqa: F401
from core.google_ads_service import exchange_code as _google_exchange_code


def enabled() -> bool:
    """GMAIL_SEND_ENABLED, read at call time (core/round2_flags)."""
    from core.round2_flags import gmail_send_enabled
    return gmail_send_enabled()


PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")
REDIRECT_PATH = "/api/oauth/gmail/callback"
# THE Gmail scope list (round 2; docs/GOOGLE-ROUND2.md mirrors it). openid +
# userinfo.email identify the sending address; gmail.send sends — nothing reads.
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email", SEND_SCOPE]
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
TIMEOUT = 30


class GmailSendDisabled(RuntimeError):
    """GMAIL_SEND_ENABLED is off: no consent, no send."""


class GmailSendFailed(RuntimeError):
    def __init__(self, status: int, text: str):
        super().__init__(f"Gmail send failed: {status} {text[:300]}")
        self.status = status


def _require_enabled():
    if not enabled():
        raise GmailSendDisabled("GMAIL_SEND_ENABLED is off")


def redirect_uri() -> str:
    return f"{PUBLIC_APP_URL}{REDIRECT_PATH}"


def build_consent_url(state: str) -> str:
    _require_enabled()
    params = {
        "client_id": get_key("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH_CONSENT_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    _require_enabled()
    return _google_exchange_code(code, redirect_uri())


def sender_address(refresh_token: str) -> str:
    """The verified address of the account that granted gmail.send (OpenID
    userinfo — the openid/userinfo.email part of THIS consent)."""
    response = httpx.get(USERINFO_URL, headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                         timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"userinfo failed: {response.status_code}")
    data = response.json()
    return data.get("email", "") if data.get("email_verified") else ""


def build_message(sender: str, sender_name: str, to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((sender_name, sender)) if sender_name else sender
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=sender.split("@")[-1] if "@" in sender else None)
    message.set_content(body)
    return message


def send_message(refresh_token: str, message: EmailMessage) -> dict:
    """users.messages.send — the only Gmail call. Returns {id, threadId}."""
    _require_enabled()
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    response = httpx.post(SEND_URL, headers={"Authorization": f"Bearer {_access_token(refresh_token)}"},
                          json={"raw": raw}, timeout=TIMEOUT)
    if response.status_code != 200:
        raise GmailSendFailed(response.status_code, response.text)
    data = response.json()
    return {"id": data.get("id", ""), "threadId": data.get("threadId", "")}
