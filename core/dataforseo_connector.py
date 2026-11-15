"""DataForSEO — a READY but OFF connector for real backlink and keyword data
(docs/SEO-REPORT.md §DataForSEO, docs/ORGANIC-PLAN.md §Real data).

OFF BY DEFAULT. It is a paid service: turning it on is the owner's call.
Turning it on is a SETTING plus the owner's own login (2026-11-16):
1. the admin setting `dataforseo_enabled` (admin_service.DEFAULT_SETTINGS, default False), AND
2. the login in the env vars DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD (Secret Manager → Cloud Run;
   not created by us, not registered in keys_agent so startup never nags about them).
Both, or it is off. While off:
- every function raises `Disabled` BEFORE building a request (no HTTP, no cost),
- core/seo_report and core/organic_plan check `enabled()` first and label their work
  "judgment-based".

What it adds when on: the client's REAL referring domains and their count, competitors'
referring domains ("who links to them and not to you"), and real monthly search volumes
and competition for keyword candidates (Israel, Hebrew by default).
"""
import os

SERVICE_NAME = "dataforseo"
SETTING = "dataforseo_enabled"
LOGIN_ENV, PASSWORD_ENV = "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"
BASE_URL = "https://api.dataforseo.com/v3"
ENDPOINTS = {
    "summary": "/backlinks/summary/live",
    "referring_domains": "/backlinks/referring_domains/live",
    "domain_intersection": "/backlinks/domain_intersection/live",
    "search_volume": "/keywords_data/google_ads/search_volume/live",
}
ROW_LIMIT = 100
ISRAEL = 2376          # DataForSEO location_code
MAX_KEYWORDS_PER_CALL = 50


class Disabled(RuntimeError):
    """Raised by every call while the connector is off."""


def _setting_on() -> bool:
    try:
        from core.admin_service import get_setting
        value = get_setting(SETTING, False)
    except Exception:
        return False
    return value is True or str(value).strip().lower() in ("true", "1", "on", "yes")


def _login() -> tuple:
    return os.environ.get(LOGIN_ENV, "").strip(), os.environ.get(PASSWORD_ENV, "").strip()


def enabled() -> bool:
    """The setting is on AND the owner's login is there. Anything else = off."""
    login, password = _login()
    return bool(login and password) and _setting_on()


def status() -> dict:
    """For the admin page and the reports: never a secret, only whether each half is there."""
    login, password = _login()
    return {"setting_on": _setting_on(), "login_present": bool(login and password), "enabled": enabled()}


def _require_enabled():
    if not enabled():
        raise Disabled("the DataForSEO connector is off (admin setting dataforseo_enabled + login)")


def _post(path: str, task: dict) -> dict:
    _require_enabled()
    import httpx
    response = httpx.post(BASE_URL + path, json=[task], auth=_login(), timeout=30)
    response.raise_for_status()
    return response.json()


def _rows(body: dict) -> list:
    tasks = (body or {}).get("tasks") or []
    result = (tasks[0].get("result") or []) if tasks else []
    return (result[0].get("items") or []) if result else []


def summary(domain: str) -> dict:
    """Totals for one domain: backlinks, referring domains."""
    _require_enabled()
    body = _post(ENDPOINTS["summary"], {"target": domain, "include_subdomains": True})
    tasks = (body or {}).get("tasks") or []
    result = (tasks[0].get("result") or []) if tasks else []
    first = result[0] if result else {}
    return {"backlinks": first.get("backlinks"), "referring_domains": first.get("referring_domains")}


def referring_domains(domain: str) -> list:
    """The domains that link to `domain`, strongest first."""
    _require_enabled()
    body = _post(ENDPOINTS["referring_domains"], {"target": domain, "limit": ROW_LIMIT, "order_by": ["rank,desc"]})
    return [{"domain": r.get("domain"), "rank": r.get("rank"), "backlinks": r.get("backlinks")} for r in _rows(body)]


def link_gap(domain: str, competitors: list) -> list:
    """Domains that link to the competitors and not to `domain`."""
    _require_enabled()
    targets = {str(i + 1): c for i, c in enumerate((competitors or [])[:5])}
    body = _post(ENDPOINTS["domain_intersection"], {"targets": targets, "exclude_targets": [domain], "limit": ROW_LIMIT})
    return [{"domain": (r.get("domain_intersection") or {}).get("1", {}).get("target_domain")
             or r.get("domain")} for r in _rows(body)]


def keyword_volumes(keywords: list, language: str = "he") -> dict:
    """keyword → {"volume", "competition"} (real monthly searches in Israel)."""
    _require_enabled()
    words = [str(k) for k in (keywords or []) if str(k).strip()][:MAX_KEYWORDS_PER_CALL]
    if not words:
        return {}
    body = _post(ENDPOINTS["search_volume"], {"keywords": words, "location_code": ISRAEL, "language_code": language})
    tasks = (body or {}).get("tasks") or []
    items = (tasks[0].get("result") or []) if tasks else []
    return {str(i.get("keyword")): {"volume": i.get("search_volume"), "competition": i.get("competition")}
            for i in items if i.get("keyword")}
