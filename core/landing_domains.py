"""The client's OWN subdomain for their landing pages, self-service.

## The preference this implements

Landing pages should live on the CLIENT's domain (`lp.theirbusiness.co.il`),
not on ours. A page on our subdomain is visibly not theirs, earns them no
domain authority, and looks like an agency artifact in an ad. So
`app.uallak.com/lp/...` is a WAITING STATE, never the destination — the
dashboard says so in those words, and every page URL flips automatically the
moment their DNS resolves.

## Why Cloudflare for SaaS and not Cloud Run domain mappings

Checked, not assumed. Cloud Run's domain mapping requires the CLIENT's base
domain to be verified in **Google Search Console under OUR Google account** —
the client would have to add a TXT record AND add us as a verified owner, then
we run a gcloud command per client. That directly contradicts the goal (one
record, forwarded as-is, no understanding required), and mappings are still
unavailable in me-west1 anyway — the reason `proxy/` exists.

Cloudflare for SaaS custom hostnames need exactly ONE thing from the client:
a CNAME pointing at our fallback origin. Cloudflare then issues and renews the
certificate for their hostname itself. 100 custom hostnames are included free
on the Free plan (checked 2026-08-01; `price_monitor_agent` territory if that
ever needs re-checking) and $0.10/hostname/month beyond — i.e. free to 100
clients, then trivially absorbed.

## What is dormant until uallak-side setup happens

Registering the custom hostname needs `CLOUDFLARE_API_TOKEN` +
`CLOUDFLARE_ZONE_ID`, and traffic needs a small Worker that rewrites the Host
header before forwarding to Cloud Run (Cloud Run 404s a request whose Host it
does not recognise; Host-header override is not on Cloudflare's free plan, but
a Worker does it in a few lines — see the landing-pages skill for the runbook).

**Until those exist this module is honest rather than broken**: the client can
still be shown their exact DNS record, `verify_domain` reports
`uallak_side_pending` instead of blaming the client for a record they added
correctly, and pages keep serving on the shared URL. No half-state, no lie.
"""
import os
import re

import httpx

SERVICE_NAME = "landing_domains"
PLATFORM = "landing_domain"

# Where a client's CNAME points. Ours; a Cloudflare-proxied record in the
# uallak.com zone that fronts this app.
DEFAULT_FALLBACK_ORIGIN = "lp.uallak.com"

# Served by this app at the site root on EVERY hostname that reaches it. A
# request for this path answering with this exact body proves the hostname
# genuinely routes here — which is the only claim verification makes, and the
# reason it needs no secret.
VERIFY_PATH = "/_uallak-verify"
VERIFY_BODY = "uallak-landing-ok"

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
TIMEOUT = 15

# Deliberately conservative: a landing subdomain is a normal hostname, and
# anything odd here would end up in a DNS instruction we hand to a stranger.
_HOSTNAME_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


def _db():
    from core import landing_pages
    return landing_pages._db()


def fallback_origin() -> str:
    return os.environ.get("LANDING_FALLBACK_ORIGIN", DEFAULT_FALLBACK_ORIGIN).strip()


def _cloudflare_configured() -> bool:
    return bool(os.environ.get("CLOUDFLARE_API_TOKEN")
                and os.environ.get("CLOUDFLARE_ZONE_ID"))


def normalize_hostname(raw: str) -> str:
    """Accepts what a human might paste (a URL, mixed case, a trailing slash)
    and returns a bare lowercase hostname, or '' if it is not one."""
    text = (raw or "").strip().lower()
    text = re.sub(r"^https?://", "", text).split("/")[0].split("?")[0].strip().strip(".")
    if not text or len(text) > 253 or not _HOSTNAME_RE.match(text):
        return ""
    return text


# ─── Stored state (reuses client_accounts — no new table) ────────────────────

def _row(client_id: int) -> dict:
    rows = (_db().table("client_accounts").select("*")
            .eq("client_id", client_id).eq("platform", PLATFORM)
            .order("id", desc=True).limit(1).execute().data or [])
    return rows[0] if rows else {}


def dns_instructions(hostname: str) -> dict:
    """The record itself, plus the plain-language message meant to be forwarded
    AS-IS to whoever manages the client's domain. The client is not expected to
    understand it — only to pass it on and say when it is done."""
    origin = fallback_origin()
    return {
        "type": "CNAME",
        "name": hostname,
        "value": origin,
        "ttl": "Auto (or 3600)",
        "proxy_note": "אם הדומיין מנוהל ב-Cloudflare — להשאיר על DNS only (ענן אפור).",
        "forward_text": (
            f"שלום, נשמח שתוסיפו רשומת DNS אחת לדומיין שלנו:\n\n"
            f"סוג: CNAME\n"
            f"שם / Host: {hostname}\n"
            f"ערך / Target: {origin}\n"
            f"TTL: Auto\n\n"
            f"אם הדומיין מנוהל ב-Cloudflare — נא להשאיר את הרשומה על DNS only "
            f"(ענן אפור) ולא Proxied.\n"
            f"זה הכל — אין צורך בשינוי נוסף. תודה!"),
    }


def get_state(client_id: int) -> dict:
    """Everything the dashboard needs to render the domain card.

    mode: 'shared'  — no custom domain requested yet (pages on our URL)
          'pending' — record generated, not yet verified live
          'active'  — verified; pages serve from the client's own hostname
    """
    row = _row(client_id)
    hostname = (row.get("account_id") or "").strip()
    status = (row.get("status") or "").strip()
    if not hostname:
        return {"mode": "shared", "hostname": "", "dns": None,
                "uallak_side_ready": _cloudflare_configured()}
    return {
        "mode": "active" if status == "active" else "pending",
        "hostname": hostname,
        "dns": dns_instructions(hostname),
        # Surfaced so a "still pending" state can say WHOSE side is pending —
        # never blame the client for a record they added correctly.
        "uallak_side_ready": _cloudflare_configured(),
    }


# Building a page's public URL deliberately lives in ONE place —
# landing_page_agent.page_url() — rather than half here and half there, so the
# dashboard, the chat messages and the rendered page can never disagree about
# where a page actually is. This module owns the STATE; that function owns the
# URL built from it.


# ─── Request + verify ─────────────────────────────────────────────────────────

def request_domain(client_id: int, raw_hostname: str) -> dict:
    """Record the client's chosen landing hostname and generate their DNS
    instruction. Registers the custom hostname with Cloudflare when that is
    configured; when it is not, this still succeeds — the client can add their
    record immediately, and our side catches up later without them redoing
    anything."""
    hostname = normalize_hostname(raw_hostname)
    if not hostname:
        return {"success": False, "code": "ERR_LANDING_BAD_HOSTNAME",
                "errors": ["that does not look like a valid subdomain "
                           "(expected something like lp.yourbusiness.co.il)"]}
    if hostname.endswith("uallak.com"):
        return {"success": False, "code": "ERR_LANDING_OWN_DOMAIN",
                "errors": ["that is one of our own domains — this flow is for the "
                           "client's own domain"]}

    from agents.client_agent import upsert_account
    upsert_account(client_id, PLATFORM, hostname, "", "pending")

    registered, register_error = False, ""
    if _cloudflare_configured():
        try:
            registered = _register_custom_hostname(hostname)
        except Exception as e:
            register_error = str(e)

    return {"success": True, "hostname": hostname,
            "dns": dns_instructions(hostname),
            "registered_with_cloudflare": registered,
            "register_error": register_error,
            "uallak_side_ready": _cloudflare_configured()}


def _register_custom_hostname(hostname: str) -> bool:
    """Cloudflare for SaaS: tell our zone to accept this hostname and issue a
    certificate for it. HTTP DCV means the client's CNAME alone completes
    validation — no extra record for them to add."""
    response = httpx.post(
        f"{CLOUDFLARE_API}/zones/{os.environ['CLOUDFLARE_ZONE_ID']}/custom_hostnames",
        headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}",
                 "Content-Type": "application/json"},
        json={"hostname": hostname, "ssl": {"method": "http", "type": "dv"}},
        timeout=TIMEOUT)
    if response.status_code in (200, 201):
        return True
    # 409/"already exists" is a success for our purposes — re-requesting the
    # same hostname must be harmless (a client may click twice).
    body = response.text[:300]
    if response.status_code == 409 or "already exists" in body.lower():
        return True
    raise RuntimeError(f"cloudflare custom_hostnames {response.status_code}: {body}")


def verify_domain(client_id: int) -> dict:
    """Is the client's hostname actually serving their pages right now?

    Verifies END TO END by fetching our marker over HTTPS on their hostname,
    rather than by reading a DNS record: a resolving CNAME with no certificate
    yet still shows a browser error, and telling a client they are live when a
    visitor sees a warning would be worse than saying 'not yet'. On success the
    stored row flips to active and every page URL switches automatically."""
    state = get_state(client_id)
    if state["mode"] == "shared":
        return {"success": False, "code": "ERR_LANDING_NO_DOMAIN",
                "errors": ["no landing domain has been requested for this client yet"]}

    hostname = state["hostname"]
    try:
        response = httpx.get(f"https://{hostname}{VERIFY_PATH}", timeout=TIMEOUT,
                             follow_redirects=True,
                             headers={"User-Agent": "uallak-landing-verify"})
        live = response.status_code == 200 and VERIFY_BODY in response.text
        detail = f"HTTP {response.status_code}"
    except Exception as e:
        live, detail = False, str(e)[:200]

    if live:
        from agents.client_agent import upsert_account
        upsert_account(client_id, PLATFORM, hostname, "", "active")
        return {"success": True, "verified": True, "hostname": hostname,
                "public_base": f"https://{hostname}"}

    # Not live. Say WHOSE side is incomplete — this is the difference between a
    # useful status and a client re-checking a record that was always correct.
    if not state["uallak_side_ready"]:
        reason = ("uallak_side_pending: our Cloudflare setup for custom landing domains "
                  "isn't finished yet. The client's DNS record can stay as it is — "
                  "nothing for them to redo.")
    else:
        reason = (f"not reachable yet ({detail}). DNS changes can take up to a few hours "
                  "to spread; if it has been longer, re-check the record was added exactly "
                  "as written and left on 'DNS only'.")
    return {"success": True, "verified": False, "hostname": hostname,
            "reason": reason, "uallak_side_ready": state["uallak_side_ready"]}
