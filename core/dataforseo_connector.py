"""DataForSEO — a READY but DISABLED connector for real backlink data
(docs/SEO-REPORT.md §DataForSEO).

DISABLED BY DECISION. It is a paid service: enabling it is the owner's call.
`ENABLED` is a constant in this file, not an env var and not an admin
setting, so nothing outside a reviewed code change can switch it on. While
it is False:
- no key is read (none exists; no env var is registered for one),
- every function raises `Disabled` BEFORE building a request,
- core/seo_report never calls it (it checks `ENABLED` first).

What it would add to the SEO report (none of which the report claims today):
the client's REAL referring domains and their count, competitors' referring
domains ("who links to them and not to you"), anchor texts, new and lost
links over time. Today the report finds opportunities by web search and
editorial judgment and never states who links to whom.

To enable later (owner's decision, in this order): create the account, add
the login as Secret Manager secrets, register the two env vars in
agents/keys_agent.py, set ENABLED = True here, and wire `referring_domains`
into core/seo_report.build behind the same check.
"""

SERVICE_NAME = "dataforseo"
ENABLED = False
BASE_URL = "https://api.dataforseo.com/v3"
# The endpoints this connector is written for (Backlinks API, "live" mode)
ENDPOINTS = {
    "summary": "/backlinks/summary/live",
    "referring_domains": "/backlinks/referring_domains/live",
    "domain_intersection": "/backlinks/domain_intersection/live",
}
ROW_LIMIT = 100


class Disabled(RuntimeError):
    """Raised by every call while the connector is off."""


def _require_enabled():
    if not ENABLED:
        raise Disabled("the DataForSEO connector is disabled (core/dataforseo_connector.ENABLED)")


def _post(path: str, task: dict, login: str, password: str) -> dict:
    _require_enabled()
    import httpx
    response = httpx.post(BASE_URL + path, json=[task], auth=(login, password), timeout=30)
    response.raise_for_status()
    return response.json()


def _rows(body: dict) -> list:
    tasks = (body or {}).get("tasks") or []
    result = (tasks[0].get("result") or []) if tasks else []
    return (result[0].get("items") or []) if result else []


def summary(domain: str, login: str = "", password: str = "") -> dict:
    """Totals for one domain: backlinks, referring domains."""
    _require_enabled()
    body = _post(ENDPOINTS["summary"], {"target": domain, "include_subdomains": True}, login, password)
    tasks = (body or {}).get("tasks") or []
    result = (tasks[0].get("result") or []) if tasks else []
    first = result[0] if result else {}
    return {"backlinks": first.get("backlinks"), "referring_domains": first.get("referring_domains")}


def referring_domains(domain: str, login: str = "", password: str = "") -> list:
    """The domains that link to `domain`, strongest first."""
    _require_enabled()
    body = _post(ENDPOINTS["referring_domains"],
                 {"target": domain, "limit": ROW_LIMIT, "order_by": ["rank,desc"]}, login, password)
    return [{"domain": r.get("domain"), "rank": r.get("rank"), "backlinks": r.get("backlinks")} for r in _rows(body)]


def link_gap(domain: str, competitors: list, login: str = "", password: str = "") -> list:
    """Domains that link to the competitors and not to `domain`."""
    _require_enabled()
    targets = {str(i + 1): c for i, c in enumerate((competitors or [])[:5])}
    body = _post(ENDPOINTS["domain_intersection"],
                 {"targets": targets, "exclude_targets": [domain], "limit": ROW_LIMIT}, login, password)
    return [{"domain": (r.get("domain_intersection") or {}).get("1", {}).get("target_domain")
             or r.get("domain"), "raw": r} for r in _rows(body)]
