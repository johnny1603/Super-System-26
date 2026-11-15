"""Open PageRank — a free public domain-authority number (0-10) for the SEO
report's backlink opportunities (core/seo_report, docs/SEO-REPORT.md).

OPTIONAL. The key is `OPEN_PAGERANK_API_KEY` (free tier; only the owner can
create it — see docs/SEO-REPORT.md). Without it `enabled()` is False, nothing
is called and the report scores by editorial judgment only, labelled so.

What leaves our server: a list of THIRD-PARTY domains (directories, portals,
blogs). Never the client's name, domain or any client data.

Never raises: any failure is an empty answer, and the report says its scores
are judgment-only.
"""
import os
import re

SERVICE_NAME = "open_pagerank"
KEY_ENV = "OPEN_PAGERANK_API_KEY"
# The service moved under Keywords Everywhere; this is its documented "legacy format" endpoint
# (same request and response as the original openpagerank.com API). Read 2026-11-15.
# KNOWN LIMIT: a FREE key works from ONE fixed IPv4 address set on the account page; a call from
# any other address is refused (403). Cloud Run has no fixed outbound address, so a free key may
# be refused there — then this module answers {} and the report stays judgment-only
# (docs/SEO-REPORT.md §The key). Never run live.
URL = "https://openpagerank.keywordseverywhere.com/api/v1.0/getPageRank"
MAX_DOMAINS = 30          # one request per report
TIMEOUT = 10
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


def enabled() -> bool:
    return bool(os.environ.get(KEY_ENV, "").strip())


def _get(domains: list) -> dict:
    import httpx
    response = httpx.get(URL, params=[("domains[]", d) for d in domains],
                         headers={"API-OPR": os.environ.get(KEY_ENV, "").strip()}, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def scores(domains: list) -> dict:
    """{domain: 0-10 float} for the domains the service knows. {} when the
    key is missing or anything fails."""
    clean = []
    for domain in domains or []:
        domain = str(domain or "").strip().lower()
        if _DOMAIN.match(domain) and domain not in clean:
            clean.append(domain)
    clean = clean[:MAX_DOMAINS]
    if not enabled() or not clean:
        return {}
    try:
        body = _get(clean)
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", "")
        print(f"[{SERVICE_NAME}] lookup failed ({type(e).__name__} {status}) — report falls back to judgment only"
              + (" (403 = the free key is bound to another IP address)" if status == 403 else ""))
        return {}
    out = {}
    for row in (body or {}).get("response") or []:
        try:
            if int(row.get("status_code") or 0) != 200:
                continue
            value = float(row.get("page_rank_decimal"))
        except (TypeError, ValueError):
            continue
        domain = str(row.get("domain") or "").lower()
        if domain in clean and 0 <= value <= 10:
            out[domain] = round(value, 1)
    return out
