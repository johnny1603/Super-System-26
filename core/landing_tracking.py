"""Landing pages: our own counters, the form's anti-spam checks, and the
per-page numbers the Performance page and client_state read.

Counters (views / calls / WhatsApp / store clicks) are uallak's OWN, counted
server-side when the page is served or a counting redirect is followed, so
they work on every hostname and need nothing from the client. The client's
Tag Manager events (core/landing_pages._gtm_events) are a separate, additional
stream that only exists on their own domain.

Leads are NOT counted here: a lead is a client_leads row (source_detail
'lp:<slug>', created_at) — the one record of a lead.

Anti-spam on the landing form, all invisible to a real visitor:
- a honeypot field ("website") a person never sees or fills,
- a signed render stamp (`lp_stamp`): a submit sooner than MIN_FILL_SECONDS
  after the page was rendered, or with a stamp older than STAMP_MAX_AGE, or a
  forged one, is a bot,
- a per-IP limit per client (MAX_SUBMITS_PER_IP in WINDOW_SECONDS), in memory
  per instance — a cheap first line; the daily per-client cap in
  client_leads stays the hard ceiling.
A dropped submit answers exactly like a stored one, so a bot learns nothing.

Before migrations/2026-11-01-landing-own.sql runs, every counter call is a
silent no-op (one log line), and pages keep serving.
"""
import base64
import hashlib
import hmac
import json
import threading
import time
from datetime import date, timedelta

SERVICE_NAME = "landing_tracking"

FIELDS = ("views", "calls", "whatsapp", "store")
MIN_FILL_SECONDS = 3
STAMP_MAX_AGE = 2 * 24 * 3600
MAX_SUBMITS_PER_IP = 5
WINDOW_SECONDS = 600
HONEYPOT_FIELD = "website"
STAMP_FIELD = "lp_stamp"

_stats_ok = {"value": True}
_ip_hits = {}
_ip_lock = threading.Lock()


def _db():
    from core import landing_pages
    return landing_pages._db()


# ─── Counters ────────────────────────────────────────────────────────────────

def bump(page_id: int, client_id: int, field: str, size_bytes: int = 0):
    """+1 on today's counter (atomic, in the database). Never raises."""
    if field not in FIELDS or not _stats_ok["value"]:
        return
    try:
        _db().rpc("landing_stat_bump", {"p_page": int(page_id), "p_client": int(client_id),
                                        "p_field": field, "p_bytes": int(size_bytes or 0)}).execute()
    except Exception as e:
        text = str(e)
        if "landing_stat_bump" in text or "PGRST202" in text or "42883" in text or "landing_page_stats" in text:
            _stats_ok["value"] = False
            print(f"[{SERVICE_NAME}] counters off until migrations/2026-11-01-landing-own.sql runs")
            return
        print(f"[{SERVICE_NAME}] bump failed for page {page_id}: {type(e).__name__}")


def stats_by_page(client_id: int, days: int = 30) -> dict:
    """{page_id: {views, calls, whatsapp, store, bytes}} over the last `days`.
    Scoped by client_id — one client's numbers never include another's."""
    if not _stats_ok["value"]:
        return {}
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    try:
        rows = (_db().table("landing_page_stats").select("page_id, views, calls, whatsapp, store, bytes")
                .eq("client_id", client_id).gte("day", since).limit(5000).execute().data or [])
    except Exception as e:
        if "landing_page_stats" in str(e) or "42P01" in str(e) or "PGRST205" in str(e):
            _stats_ok["value"] = False
            return {}
        raise
    return _sum(rows)


def _sum(rows: list) -> dict:
    out = {}
    for row in rows:
        acc = out.setdefault(row["page_id"], {k: 0 for k in (*FIELDS, "bytes")})
        for key in acc:
            acc[key] += int(row.get(key) or 0)
    return out


def all_stats(days: int = 30) -> dict:
    """Admin: every page's totals over `days` — {page_id: {...}}."""
    if not _stats_ok["value"]:
        return {}
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    try:
        rows = (_db().table("landing_page_stats").select("page_id, views, calls, whatsapp, store, bytes")
                .gte("day", since).limit(20000).execute().data or [])
    except Exception:
        return {}
    return _sum(rows)


# ─── The client's Tag Manager, on their own domain only ──────────────────────

# Set by our Cloudflare Worker to the hostname the visitor used. A browser
# navigation can never carry a custom header, so a request that has it came
# through the Worker (or from someone fetching the page for themselves) —
# never a visitor on app.uallak.com.
LP_HOST_HEADER = "x-uallak-lp-host"
GTM_TTL = 600
_gtm_cache = {}


def gtm_container(client_id: int, lp_host: str) -> str:
    """'GTM-XXXX' to load on this request, or ''. Only when the request came
    through the client's own VERIFIED hostname and their Tag Manager is
    connected. Read from what we recorded at connect time (no Google call per
    visit), cached for 10 minutes."""
    lp_host = (lp_host or "").strip().lower()
    if not lp_host:
        return ""
    hit = _gtm_cache.get(client_id)
    if hit and hit[0] > time.time() - GTM_TTL:
        hostname, container = hit[1]
    else:
        hostname, container = "", ""
        try:
            from core import gtm_connection, landing_domains
            state = landing_domains.get_state(client_id)
            if state.get("mode") == "active":
                hostname = state["hostname"]
                conn = gtm_connection._connection(client_id)
                if conn.get("access_token") and conn.get("account_id"):
                    container = gtm_connection._recorded_container(client_id, conn["account_id"]).get("public_id", "")
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: tag manager lookup failed ({type(e).__name__})")
        _gtm_cache[client_id] = (time.time(), (hostname, container))
    if not hostname or lp_host != hostname.lower():
        return ""
    from core.landing_pages import GTM_ID
    return container if GTM_ID.match(container or "") else ""


# ─── The form's anti-spam stamp ──────────────────────────────────────────────

def _key() -> bytes:
    from agents.keys_agent import get_key
    # Derived, so a form stamp can never verify as any other signed token
    return hashlib.sha256(get_key("SESSION_SECRET_KEY").encode() + b":lp-form").digest()


def make_stamp(client_id: int, now: float = None) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"c": int(client_id), "t": int(now if now is not None else time.time())}).encode())
    return payload.decode() + "." + hmac.new(_key(), payload, hashlib.sha256).hexdigest()[:32]


def _stamp_time(stamp: str, client_id: int):
    try:
        payload, signature = str(stamp or "").split(".", 1)
        expected = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return data["t"] if int(data.get("c", -1)) == int(client_id) else None
    except Exception:
        return None


def is_landing_submit(payload: dict) -> bool:
    return str(payload.get("source_detail") or "").startswith("lp:") or STAMP_FIELD in payload


def spam_reason(client_id: int, payload: dict, ip: str, now: float = None) -> str:
    """'' for a real person, else why the submit is dropped. Only applied to
    our own landing forms (is_landing_submit) — a client's site form keeps
    its existing behaviour."""
    now = now if now is not None else time.time()
    if str(payload.get(HONEYPOT_FIELD) or "").strip():
        return "honeypot"
    stamped = _stamp_time(payload.get(STAMP_FIELD), client_id)
    if stamped is None:
        return "stamp"
    if now - stamped < MIN_FILL_SECONDS:
        return "too_fast"
    if now - stamped > STAMP_MAX_AGE:
        return "stale"
    if not _ip_allowed(client_id, ip, now):
        return "rate"
    return ""


def _ip_allowed(client_id: int, ip: str, now: float) -> bool:
    key = (int(client_id), hashlib.sha256((ip or "?").encode()).hexdigest()[:16])
    with _ip_lock:
        hits = [t for t in _ip_hits.get(key, []) if now - t < WINDOW_SECONDS]
        if len(hits) >= MAX_SUBMITS_PER_IP:
            _ip_hits[key] = hits
            return False
        hits.append(now)
        _ip_hits[key] = hits
        if len(_ip_hits) > 20000:   # bound memory on an instance under a flood
            for stale in [k for k, v in _ip_hits.items() if not v or now - v[-1] > WINDOW_SECONDS]:
                _ip_hits.pop(stale, None)
    return True


INTEREST_FIELD = "interest"
MAX_INTEREST_CHARS = 120


def clean_payload(payload: dict) -> dict:
    """The fields that are ours, not the visitor's, never reach the lead row.
    A designed page's `interest` (the option the visitor picked — "נעליים",
    "סט שלם") has no column of its own, so it leads the lead's message,
    where the client reads it in the leads list."""
    out = {k: v for k, v in payload.items() if k not in (HONEYPOT_FIELD, STAMP_FIELD, INTEREST_FIELD)}
    interest = " ".join(str(payload.get(INTEREST_FIELD) or "").split())[:MAX_INTEREST_CHARS]
    if interest:
        message = str(out.get("message") or "").strip()
        out["message"] = f"מתעניין/ת ב: {interest}" + (f"\n{message}" if message else "")
    return out


def redirect_allowed(client_id: int, target: str) -> bool:
    """A landing form's thank-you redirect, judged WITHOUT the Referer: a
    sandboxed page has an opaque origin, so browsers may send no usable
    Referer at all. Allowed only back to a landing page of THIS client — on
    our app host under /lp/<…-client_id>/, or on the client's own verified
    hostname. Anything else is ignored (never an open redirect)."""
    import os
    import re
    from urllib.parse import urlparse
    parts = urlparse(str(target or "").strip())
    if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc or parts.port not in (None, 443):
        return False
    host = parts.hostname.lower()
    app_host = (urlparse(os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")).hostname or "").lower()
    if host == app_host:
        match = re.match(r"^/lp/[a-z0-9-]*?(\d+)/[a-z0-9-]+/?$", parts.path or "")
        return bool(match) and int(match.group(1)) == int(client_id)
    try:
        from core import landing_domains
        state = landing_domains.get_state(client_id)
    except Exception:
        return False
    return state.get("mode") == "active" and host == str(state.get("hostname") or "").lower() \
        and bool(re.match(r"^/[a-z0-9-]+/?$", parts.path or ""))
