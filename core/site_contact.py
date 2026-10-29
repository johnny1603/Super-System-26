"""E-mail addresses from a business's OWN public website — the only source
(docs/PROSPECTING.md §5). Never guessed (no info@ patterns), never bought,
never from directories or social networks.

Polite by construction:
- robots.txt is read first (stdlib robotparser) and obeyed for our agent;
- an honest User-Agent naming uallak with a contact URL;
- at most one request per domain every DOMAIN_INTERVAL_S, at most
  MAX_PAGES pages per domain (the homepage + up to 2 contact/about pages);
- callers record "checked, nothing found" so a domain is not re-crawled
  for RECHECK_DAYS.

Every address comes back with the exact page URL it was found on (stored as
its source, decision 2026-10-22) and whether it looks like a role address.
"""
import html as html_lib
import re
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from core.domains import registered_domain

USER_AGENT = "uallak-research/1.0 (+https://app.uallak.com/privacy/)"
DOMAIN_INTERVAL_S = 10
MAX_PAGES = 3
MAX_PAGE_BYTES = 600_000
RECHECK_DAYS = 30
TIMEOUT = 15

ROLE_LOCALPARTS = {"info", "office", "contact", "hello", "sales", "support", "admin", "shop", "orders",
                   "service", "mail", "team", "studio", "booking", "events", "marketing", "pr"}
_CONTACT_HINT = re.compile(r"(contact|צור[\s-]*קשר|יצירת[\s-]*קשר|about|אודות|קשר)", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}")
_HREF = re.compile(r"<a\b[^>]*href\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")

_last_fetch = {}   # domain -> time of the last request (per process)


# ─── Seams (patched by tests) ────────────────────────────────────────────────

def _http_get(url: str) -> tuple:
    """(status, text) — streamed and cut at MAX_PAGE_BYTES."""
    import httpx
    with httpx.stream("GET", url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
                      follow_redirects=True) as response:
        body = bytearray()
        for piece in response.iter_bytes():
            body += piece
            if len(body) >= MAX_PAGE_BYTES:
                break
        return response.status_code, body[:MAX_PAGE_BYTES].decode(response.encoding or "utf-8", "replace")


_sleep = time.sleep
_clock = time.monotonic


def _polite_get(url: str) -> tuple:
    domain = registered_domain(urlsplit(url).hostname or "")
    wait = DOMAIN_INTERVAL_S - (_clock() - _last_fetch.get(domain, -DOMAIN_INTERVAL_S * 10))
    if wait > 0:
        _sleep(wait)
    _last_fetch[domain] = _clock()
    return _http_get(url)


def _robots(site: str) -> RobotFileParser:
    parser = RobotFileParser()
    try:
        status, text = _polite_get(urljoin(site, "/robots.txt"))
        # No robots.txt (404) = allowed; a server error = treat as disallowed
        parser.parse(text.splitlines() if status == 200 else ([] if status in (404, 410) else
                                                             ["User-agent: *", "Disallow: /"]))
    except Exception:
        parser.parse(["User-agent: *", "Disallow: /"])
    return parser


def _text(page_html: str) -> str:
    no_tags = re.sub(r"<(script|style)\b.*?</\1>", " ", page_html or "", flags=re.I | re.S)
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", no_tags)).split())


def _emails(page_html: str) -> list:
    found = re.findall(r"mailto:([^\"'?\s>]+)", page_html or "", re.I) + _EMAIL.findall(_text(page_html))
    out = []
    for raw in found:
        email = html_lib.unescape(raw).strip().strip(".").lower()
        if _EMAIL.fullmatch(email) and not email.endswith(_SKIP_EXT) and email not in out:
            out.append(email)
    return out


def _contact_links(site: str, page_html: str) -> list:
    host = registered_domain(urlsplit(site).hostname or "")
    links = []
    for href, label in _HREF.findall(page_html or ""):
        url = urljoin(site, href.strip())
        if (urlsplit(url).scheme in ("http", "https")
                and registered_domain(urlsplit(url).hostname or "") == host
                and (_CONTACT_HINT.search(href) or _CONTACT_HINT.search(_text(label)))
                and url not in links):
            links.append(url)
    return links[:MAX_PAGES - 1]


def find_emails(website: str) -> dict:
    """{"emails": [{"email", "source_url", "role"}], "pages_read", "text"
    (the homepage's visible text, cut — for personalising a draft, not
    stored), "blocked_by_robots"}. Never raises for a site's own trouble."""
    out = {"emails": [], "pages_read": 0, "text": "", "blocked_by_robots": False}
    parts = urlsplit(website or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return out
    site = f"{parts.scheme}://{parts.hostname}/"
    robots = _robots(site)
    queue, seen = [website], set()
    while queue and out["pages_read"] < MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if not robots.can_fetch(USER_AGENT, url):
            out["blocked_by_robots"] = True
            continue
        try:
            status, page = _polite_get(url)
        except Exception:
            continue
        out["pages_read"] += 1
        if status != 200:
            continue
        if not out["text"]:
            out["text"] = _text(page)[:2500]
            queue += [link for link in _contact_links(site, page) if link not in seen]
        for email in _emails(page):
            if email not in {e["email"] for e in out["emails"]}:
                out["emails"].append({"email": email, "source_url": url,
                                      "role": email.split("@")[0] in ROLE_LOCALPARTS})
    return out
