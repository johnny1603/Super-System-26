"""One outreach e-mail, drafted in the client chat and sent from the client's
own Gmail after the client approves THAT draft — Google verification ROUND 2,
behind GMAIL_SEND_ENABLED (off by default; core/gmail_service).

The shape (core/agent_actions action `send_outreach_email`):
1. The chat agent writes a draft for ONE named business: recipient address,
   the business's name, its website, one specific detail (`hook`) about it,
   subject and body. `prepare_draft` reads the business's OWN site (robots.txt
   respected, at most MAX_PAGES pages) and refuses unless:
   - the address appears on that site (never guessed, never bought);
   - the draft names the business and uses a detail we actually read there;
   - it is not a near-copy of an earlier draft (outreach_policy.validate_draft);
   - the address is not suppressed or already contacted.
   The opt-out line is appended here, so every card shows it.
2. The client reads the exact message on the card and presses Approve.
3. `send_approved` runs every rule again (outreach_policy.check_before_send:
   suppression FIRST, opt-out, one message per business, entitlement, the
   daily caps), then the monthly cost-budget guard
   (gmail_agent_plan.require_budget), then sends with users.messages.send,
   and records the send in `outreach_sends`.

Nothing here reads a mailbox: replies and opt-outs arrive in the client's own
inbox, and an opt-out is added to the suppression list by a human
(outreach_policy.suppress).
"""
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

SENDS_TABLE = "outreach_sends"
PLATFORM = "gmail_send"
MAX_PAGES = 3
FETCH_TIMEOUT = 10
USER_AGENT = "uallak-outreach/1.0 (+https://app.uallak.com/privacy/)"
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}$")
_CONTACT_LINK = re.compile(r'href=["\']([^"\'#]+)["\'][^>]*>([^<]{0,80})<', re.IGNORECASE)
_CONTACT_WORDS = ("contact", "about", "צור קשר", "צור-קשר", "אודות", "קשר")


class DraftRefused(Exception):
    """A draft that can't become a card; `code` picks the plain line."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


# ─── Seams (patched by tests) ────────────────────────────────────────────────

def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _client(client_id: int) -> dict:
    from agents.client_agent import get_client
    return get_client(client_id) or {}


def _fetch(url: str) -> str:
    """One polite GET; "" on anything but a 200 HTML page."""
    import httpx
    try:
        response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT,
                             follow_redirects=True)
    except Exception:
        return ""
    if response.status_code != 200 or "html" not in response.headers.get("content-type", "html"):
        return ""
    return response.text[:500_000]


def _robots_allows(url: str) -> bool:
    from urllib import robotparser
    parsed = urlparse(url)
    parser = robotparser.RobotFileParser()
    parser.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    try:
        parser.parse(_fetch(f"{parsed.scheme}://{parsed.netloc}/robots.txt").splitlines())
    except Exception:
        return True
    return parser.can_fetch(USER_AGENT, url)


def _connection(client_id: int) -> dict:
    from core.secrets_vault import hydrate
    rows = (_db().table("client_accounts").select("*").eq("client_id", int(client_id))
            .eq("platform", PLATFORM).eq("status", "active").order("id", desc=True)
            .limit(1).execute().data or [])
    return hydrate(rows[0]) if rows else {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Reading the business's own site ─────────────────────────────────────────

def _text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    return " ".join(re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ").split())


def read_business_site(url: str) -> dict:
    """{"text", "emails", "pages"} from the homepage and up to two contact /
    about pages linked from it, robots.txt respected."""
    if not url.startswith(("http://", "https://")):
        raise DraftRefused("bad_site", "business_url must be http(s)")
    host = urlparse(url).netloc.lower()
    pages, texts, html_seen = [], [], ""
    queue = [url]
    while queue and len(pages) < MAX_PAGES:
        page = queue.pop(0)
        if urlparse(page).netloc.lower() != host or page in pages or not _robots_allows(page):
            continue
        html = _fetch(page)
        if not html:
            continue
        pages.append(page)
        texts.append(_text(html))
        html_seen += " " + html
        if len(pages) == 1:
            for href, label in _CONTACT_LINK.findall(html):
                if any(w in (href + " " + label).lower() for w in _CONTACT_WORDS):
                    queue.append(urljoin(page, href))
            queue = queue[:MAX_PAGES]
    emails = {m.lower() for m in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}", html_seen)}
    return {"text": " ".join(texts), "emails": emails, "pages": pages}


# ─── The draft (proposal time) ───────────────────────────────────────────────

def _previous_bodies(client_id: int, open_rows: list) -> list:
    bodies = [(r.get("params") or {}).get("body", "") for r in open_rows or []]
    try:
        sent = (_db().table(SENDS_TABLE).select("body").eq("client_id", int(client_id))
                .order("sent_at", desc=True).limit(50).execute().data or [])
        bodies += [r.get("body", "") for r in sent]
    except Exception:
        pass
    return [b for b in bodies if b]


def prepare_draft(client_id: int, params: dict, language: str, open_rows: list) -> dict:
    """The model's draft → the exact stored params the client approves.
    Raises DraftRefused(code)."""
    from core import outreach_policy as policy
    to_email = str(params.get("to_email") or "").strip().lower()
    if not _EMAIL_RE.match(to_email):
        raise DraftRefused("bad_address")
    if policy.is_suppressed(email=to_email):
        raise DraftRefused("suppressed")
    if policy.already_contacted(client_id, "", to_email):
        raise DraftRefused("already_contacted")
    site = read_business_site(str(params.get("business_url") or "").strip())
    if not site["pages"]:
        raise DraftRefused("site_unreadable")
    if to_email not in site["emails"]:
        raise DraftRefused("address_not_on_site")
    language = language if language in policy.OPT_OUT else "he"
    business_name = " ".join(str(params.get("business_name") or "").split())[:120]
    subject = " ".join(str(params.get("subject") or "").split())[:policy.SUBJECT_MAX]
    body = policy.with_opt_out(str(params.get("body") or "").strip()[:policy.BODY_MAX], language)
    problems = policy.validate_draft(subject, body, str(params.get("hook") or ""), business_name,
                                     site["text"], language, _previous_bodies(client_id, open_rows))
    if problems:
        raise DraftRefused("not_personal", "; ".join(problems))
    return {"to_email": to_email, "business_name": business_name, "subject": subject, "body": body,
            "language": language, "source_url": site["pages"][0]}


STORED_KEYS = {"to_email", "business_name", "subject", "body", "language", "source_url"}


def revalidate_stored(params: dict) -> dict:
    """The stored card, re-checked at the click (nothing the model or the
    browser can have changed since): exact keys, a real address, the opt-out
    line, the business named."""
    from core import outreach_policy as policy
    params = dict(params or {})
    if set(params) != STORED_KEYS:
        raise ValueError("stored outreach draft has unexpected params")
    if not _EMAIL_RE.match(params["to_email"]):
        raise ValueError("bad address")
    if params["language"] not in policy.OPT_OUT or policy.opt_out_line(params["language"]) not in params["body"]:
        raise ValueError("opt-out line missing")
    if not params["subject"] or len(params["subject"]) > policy.SUBJECT_MAX:
        raise ValueError("bad subject")
    if policy._norm(params["business_name"]) not in policy._norm(params["body"]):
        raise ValueError("business not named")
    return params


# ─── The send (click time) ───────────────────────────────────────────────────

def sent_today(client_id: int) -> int:
    """Recorded sends since 00:00 UTC. Unreadable → a number at the cap."""
    from core import gmail_agent_plan as plan
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    try:
        rows = (_db().table(SENDS_TABLE).select("id").eq("client_id", int(client_id))
                .gte("sent_at", start).limit(plan.MESSAGES_PER_CLIENT_PER_DAY + 1).execute().data or [])
    except Exception:
        return plan.MESSAGES_PER_CLIENT_PER_DAY
    return len(rows)


class SendFailed(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def send_approved(client_id: int, params: dict, proposal_id=None) -> dict:
    """Every rule again, then users.messages.send, then the record."""
    from core import gmail_agent_plan as plan
    from core import outreach_policy as policy
    from core.round2_flags import gmail_send_enabled
    if not gmail_send_enabled():
        raise SendFailed("send_disabled")
    from core import gmail_service
    # The HARD daily cap, from the send record itself (fails closed) — the
    # api_call_counters brake in check_before_send fails open by design
    if sent_today(client_id) >= plan.MESSAGES_PER_CLIENT_PER_DAY:
        raise SendFailed("daily_cap", "messages_client")
    try:
        policy.check_before_send(client_id, {"to_email": params["to_email"], "place_id": "",
                                             "body": params["body"]}, params["language"])
        plan.require_budget(client_id, 0.0)
    except policy.SendRefused as e:
        raise SendFailed(e.code, str(e))
    except plan.BudgetExhausted as e:
        raise SendFailed("budget", str(e))
    conn = _connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        raise SendFailed("needs_reconnect", "no gmail_send connection")
    message = gmail_service.build_message(conn["account_id"], _client(client_id).get("name", ""),
                                          params["to_email"], params["subject"], params["body"])
    try:
        sent = gmail_service.send_message(conn["access_token"], message)
    except gmail_service.GmailSendFailed as e:
        raise SendFailed("needs_reconnect" if e.status in (401, 403) else "send_error", str(e))
    except Exception as e:
        raise SendFailed("needs_reconnect" if "invalid_grant" in str(e) else "send_error", str(e))
    record = {"client_id": int(client_id), "to_email": params["to_email"],
              "business_name": params["business_name"], "subject": params["subject"],
              "body": params["body"], "source_url": params["source_url"],
              "from_email": conn["account_id"], "gmail_message_id": sent.get("id", ""),
              "proposal_id": proposal_id, "sent_at": _now()}
    try:
        _db().table(SENDS_TABLE).insert(record).execute()
    except Exception as e:
        # Sent but not recorded: the one-message-per-business rule would not
        # see it — loud, so a human records it by hand
        from core.agent_base import agent_alert
        agent_alert("gmail_outreach", [f"client {client_id}: e-mail SENT to {params['to_email']} but not "
                                       f"recorded in {SENDS_TABLE}: {type(e).__name__}"])
    return {"to_email": params["to_email"], "business_name": params["business_name"],
            "from_email": conn["account_id"], "message_id": sent.get("id", "")}
