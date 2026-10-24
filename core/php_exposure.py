"""PHP2Shell exposure — which WordPress sites we know of, and what PHP each one
says it runs, from the outside. See docs/INCIDENT-PHP2SHELL.md.

Our own runtime has no PHP (python:3.12-slim, pip-only requirements). The
exposure is the WordPress sites we build (InstaWP) and connect to (the
client's own host): PHP runs THERE, and the memory-disclosure half of the
advisory can leak request headers — including the Basic-auth header our
Application Password travels in. So this module answers, per site:

  inventory() ..... every WordPress site the system knows: client_accounts
                    'wordpress' rows (any status; has_app_password = an active
                    row with a stored secret — never the secret), InstaWP sites
                    (`website_provisioned` activity), and existing sites a
                    client asked us to assess (`website_existing_assessed`
                    with is_wordpress) or migrate.
  check_site() .... ONE ordinary anonymous HEAD of the homepage (GET when HEAD
                    is refused or shows nothing), exactly what a browser does,
                    reading only the X-Powered-By / Server response headers.
                    No payload, no query string, no body, no credentials, no
                    path but "/". NEVER an exploit or probe request.
  classify() ...... the reported version against core/php_advisory.

Statuses — and what they do NOT mean:
  patched ...... the site REPORTS a fixed release. A header is the host's own
                 claim, not a verification; agents never call a site "safe".
  vulnerable ... the site reports a release older than the fix (or a branch
                 with no official fix) from an upstream build.
  unknown ...... no version visible, unreachable, a hidden patch level, a
                 pre-release, conflicting headers, or a distro/vendor build of
                 an older release (it may carry a backport) — ask the host.
  not_checked .. known WordPress site, never checked yet (client_state only).

Stored per CLIENT in app_settings `php_exposure:<id>` ({"sites": {url: ...}}),
cleared by client_deletion (_settings_keys) and guarded by deletion_guard
like the other per-client rows. ONE admin alert per site per status change
(`alerted_status` in the record is the durable dedup); a first check that
finds `patched` alerts nothing.
"""
import re
from datetime import datetime, timezone

from core import php_advisory as advisory

SERVICE_NAME = "php_exposure"
RECORD_PREFIX = "php_exposure:"
CHECK_TIMEOUT = 12
USER_AGENT = "uallak-site-check/1.0"
SAFE_METHODS = ("HEAD", "GET")

PATCHED, VULNERABLE, UNKNOWN, NOT_CHECKED = "patched", "vulnerable", "unknown", "not_checked"

# Hosting origin of a site
ORIGIN_INSTAWP = "instawp"                         # we provisioned it; InstaWP hosts it
ORIGIN_CLIENT_CONNECTED = "client_host_connected"  # the client's own host, connected to us
ORIGIN_CLIENT_ASSESSED = "client_host_assessed"    # the client's own host, assessed only

# "PHP/8.3.36", "PHP/8.1.2-1ubuntu2.20", "PHP/7.4.33", "Apache/2.4 (Debian) PHP/8.2.7"
_PHP_TOKEN = re.compile(r"\bPHP/(\d{1,2})\.(\d{1,2})(?:\.(\d{1,3}))?([0-9A-Za-z.+~_-]*)", re.I)
_PRERELEASE = re.compile(r"^[-.]?(?:dev|alpha|beta|rc)", re.I)

REASON_TEXT = {
    "reports_fixed_release": "reports a fixed release",
    "below_fixed_release": "older than the fixed release on its branch",
    "no_official_fix": "PHP 8.1 or older — no official fix exists",
    "vendor_build_may_be_backported": "a distro/vendor build of an older release — it may carry a backport",
    "vendor_build_unsupported_branch": "a distro/vendor build of PHP 8.1 or older — only a vendor backport can fix it",
    "prerelease_build": "a pre-release build — not a fixed release",
    "patch_level_hidden": "the patch level is not shown",
    "branch_not_in_advisory": "a PHP branch the advisory does not list",
    "conflicting_headers": "the headers report different PHP versions",
    "not_exposed": "the site does not show its PHP version",
    "unreachable": "the site could not be loaded",
    "invalid_url": "the stored address is not a valid URL",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Seams (patched by tests) ────────────────────────────────────────────────

def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _alert(issue: str):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, [issue])


def _fetch(method: str, url: str):
    """One ordinary browser-style request. Only HEAD/GET, only the given URL,
    no body, no params, no credentials. Returns the response or None."""
    if method not in SAFE_METHODS:
        raise ValueError(f"{method} is not allowed here")
    import httpx
    try:
        return httpx.request(method, url, timeout=CHECK_TIMEOUT, follow_redirects=True,
                             headers={"User-Agent": USER_AGENT})
    except Exception:
        return None


# ─── Version comparison (pure) ───────────────────────────────────────────────

def classify(major: int, minor: int, patch, suffix: str = "") -> dict:
    """{status, reason, php_version} for one reported version."""
    suffix = suffix or ""
    version = f"{major}.{minor}" + (f".{patch}" if patch is not None else "") + suffix

    def out(status, reason):
        return {"status": status, "reason": reason, "php_version": version}

    if _PRERELEASE.match(suffix):
        return out(UNKNOWN, "prerelease_build")
    vendor = bool(suffix)
    branch = (int(major), int(minor))
    if branch < advisory.OLDEST_FIXED_BRANCH:
        return out(UNKNOWN, "vendor_build_unsupported_branch") if vendor else out(VULNERABLE, "no_official_fix")
    if branch not in advisory.FIXED_RELEASES:
        return out(UNKNOWN, "branch_not_in_advisory")
    if patch is None:
        return out(UNKNOWN, "patch_level_hidden")
    if int(patch) >= advisory.FIXED_RELEASES[branch]:
        return out(PATCHED, "reports_fixed_release")
    return out(UNKNOWN, "vendor_build_may_be_backported") if vendor else out(VULNERABLE, "below_fixed_release")


def php_header_values(headers) -> list:
    """The X-Powered-By and Server values (all of them) from a header mapping."""
    if headers is None:
        return []
    items = headers.multi_items() if hasattr(headers, "multi_items") else list(dict(headers).items())
    return [str(v) for k, v in items if str(k).lower() in ("x-powered-by", "server")]


def assess_headers(values) -> dict:
    """{status, reason, php_version, header} from the header values."""
    found = [m for value in values or [] for m in _PHP_TOKEN.finditer(str(value))]
    header = " | ".join(str(v)[:120] for v in values or [])[:300]
    if not found:
        return {"status": UNKNOWN, "reason": "not_exposed", "php_version": "", "header": header}
    distinct = {(m.group(1), m.group(2), m.group(3) or "", (m.group(4) or "").lower()) for m in found}
    if len(distinct) > 1:
        return {"status": UNKNOWN, "reason": "conflicting_headers",
                "php_version": ", ".join(sorted(m.group(0)[4:] for m in found)), "header": header}
    m = found[0]
    result = classify(int(m.group(1)), int(m.group(2)),
                      int(m.group(3)) if m.group(3) else None, m.group(4) or "")
    return {**result, "header": header}


# ─── The anonymous check ─────────────────────────────────────────────────────

def _homepage(site_url: str) -> str:
    from core.wordpress_service import normalize_site_url
    return normalize_site_url(site_url) + "/"


def check_site(site_url: str) -> dict:
    """Never raises. HEAD the homepage; GET it only when HEAD was refused or
    showed no PHP header. Headers of every hop (redirects included) count."""
    try:
        url = _homepage(site_url)
    except ValueError:
        return {"reachable": False, "status": UNKNOWN, "reason": "invalid_url", "php_version": "",
                "header": "", "checked_at": _now()}
    values, reachable, http_status = [], False, None
    for method in SAFE_METHODS:
        response = _fetch(method, url)
        if response is None:
            continue
        hops = list(getattr(response, "history", None) or []) + [response]
        values = [v for hop in hops for v in php_header_values(getattr(hop, "headers", None))]
        http_status = getattr(response, "status_code", None)
        reachable = http_status is not None and http_status < 500
        if reachable and http_status < 400 and any(_PHP_TOKEN.search(v) for v in values):
            break
    if not reachable:
        result = {"status": UNKNOWN, "reason": "unreachable", "php_version": "", "header": ""}
    else:
        result = assess_headers(values)
    return {**result, "reachable": reachable, "http_status": http_status, "checked_at": _now()}


# ─── Inventory ───────────────────────────────────────────────────────────────

def _norm(url: str) -> str:
    from core.wordpress_service import normalize_site_url
    try:
        return normalize_site_url(url)
    except ValueError:
        return ""


def _activity(action_type: str, client_id: int = None) -> list:
    query = (_db().table("client_activity").select("client_id,details,created_at")
             .eq("agent_name", "website_agent").eq("action_type", action_type))
    if client_id is not None:
        query = query.eq("client_id", int(client_id))
    return query.limit(5000).execute().data or []


def inventory(client_id: int = None) -> list:
    """Every WordPress site known to the system, one row per (client, site).
    Never carries a secret — only whether we hold one."""
    from core.secrets_vault import public_account
    clients = {}
    try:
        query = _db().table("clients").select("id,name,business_name,status")
        if client_id is not None:
            query = query.eq("id", int(client_id))
        clients = {c["id"]: c for c in (query.execute().data or [])}
    except Exception as e:
        print(f"[{SERVICE_NAME}] client names unavailable: {type(e).__name__}")

    sites = {}

    def add(cid, url, origin):
        url = _norm(url)
        if not cid or not url or (client_id is not None and int(cid) != int(client_id)):
            return None
        key = (int(cid), url)
        if key not in sites:
            client = clients.get(int(cid)) or {}
            sites[key] = {"client_id": int(cid), "client_name": client.get("business_name") or client.get("name") or "",
                          "client_status": client.get("status", ""), "site_url": url, "origin": origin,
                          "connected": False, "has_app_password": False}
        elif origin == ORIGIN_INSTAWP:
            sites[key]["origin"] = ORIGIN_INSTAWP     # provisioning beats any later label
        return sites[key]

    for row in _activity("website_provisioned", client_id):
        add(row.get("client_id"), (row.get("details") or {}).get("site_url", ""), ORIGIN_INSTAWP)

    query = _db().table("client_accounts").select("*").eq("platform", "wordpress")
    if client_id is not None:
        query = query.eq("client_id", int(client_id))
    for raw in query.execute().data or []:
        account = public_account(raw)     # secrets stripped here, before anything else reads it
        entry = add(account.get("client_id"), account.get("account_id", ""), ORIGIN_CLIENT_CONNECTED)
        if entry is not None and account.get("status") == "active":
            entry["connected"] = True
            entry["has_app_password"] = entry["has_app_password"] or bool(account.get("has_secret"))

    for action in ("website_existing_assessed", "website_migration_requested"):
        for row in _activity(action, client_id):
            details = row.get("details") or {}
            if action == "website_existing_assessed" and not details.get("is_wordpress"):
                continue
            add(row.get("client_id"), details.get("site_url", ""), ORIGIN_CLIENT_ASSESSED)

    return sorted(sites.values(), key=lambda s: (s["client_id"], s["site_url"]))


# ─── Per-client record (app_settings) ────────────────────────────────────────

def _key(client_id: int) -> str:
    return f"{RECORD_PREFIX}{int(client_id)}"


def record(client_id: int) -> dict:
    """{"sites": {url: {...}}} — {} when never checked or unreadable."""
    try:
        rows = (_db().table("app_settings").select("value").eq("key", _key(client_id))
                .limit(1).execute().data or [])
    except Exception:
        return {}
    value = rows[0].get("value") if rows else {}
    return value if isinstance(value, dict) else {}


def _save(client_id: int, value: dict) -> bool:
    try:
        _db().table("app_settings").upsert({"key": _key(client_id), "value": value, "updated_at": _now()},
                                           on_conflict="key").execute()
        return True
    except Exception as e:
        # A client being deleted refuses this write (deletion_guard) — fine
        print(f"[{SERVICE_NAME}] record not stored for client {client_id}: {type(e).__name__}")
        return False


def _alert_text(site: dict, entry: dict) -> str:
    who = (f"client {site['client_id']}" + (f" ({site['client_name']})" if site.get("client_name") else ""))
    head = f"{who}: {site['site_url']} — {advisory.NAME} ({' + '.join(advisory.CVES)})"
    fixed = advisory.fixed_versions_text()
    host = ("We host it on InstaWP: change its PHP version in the InstaWP dashboard (runbook in "
            "docs/INCIDENT-PHP2SHELL.md)." if site.get("origin") == ORIGIN_INSTAWP
            else "The client's hosting provider has to do it — updating WordPress or plugins does not fix this.")
    rotate = (" Afterwards rotate the stored WordPress password (Control Room → PHP2Shell)."
              if site.get("has_app_password") else "")
    if entry["status"] == VULNERABLE:
        return (f"{head}: the site reports PHP {entry['php_version']}, which is "
                f"{REASON_TEXT.get(entry['reason'], entry['reason'])}. PHP must be updated to a fixed "
                f"release ({fixed}). {host}{rotate}")
    if entry["status"] == PATCHED:
        return (f"{head}: the site now reports PHP {entry['php_version']}, a fixed release. That is the "
                f"host's own header, not a verification.{rotate}")
    return (f"{head}: we can't tell which PHP it runs ({REASON_TEXT.get(entry['reason'], entry['reason'])}). "
            f"Ask the host which PHP version it runs and whether it includes the fix ({fixed}). {host}{rotate}")


def update_site(site: dict, result: dict, current: dict = None) -> tuple:
    """Merge one check into the client's record. Returns (record, alerted).
    Alerts once per status change per site; a first check that finds
    `patched` is quiet. `current` = an already-read record (one read per client)."""
    current = current if current is not None else record(site["client_id"])
    sites = dict((current or {}).get("sites") or {})
    previous = sites.get(site["site_url"]) or {}
    now = result.get("checked_at") or _now()
    entry = {
        "status": result["status"], "reason": result["reason"], "php_version": result.get("php_version", ""),
        "header": result.get("header", ""), "reachable": bool(result.get("reachable")),
        "checked_at": now, "origin": site.get("origin", ""), "has_app_password": bool(site.get("has_app_password")),
        "status_since": previous.get("status_since") if previous.get("status") == result["status"] else now,
        "alerted_status": previous.get("alerted_status"),
        "password_rotated_at": previous.get("password_rotated_at"),
    }
    alerted = False
    if entry["status"] != entry["alerted_status"]:
        if previous or entry["status"] != PATCHED:
            _alert(_alert_text(site, entry))
            alerted = True
        entry["alerted_status"] = entry["status"]
    sites[site["site_url"]] = entry
    value = {**(current or {}), "sites": sites, "advisory": advisory.NAME}
    _save(site["client_id"], value)
    return value, alerted


def note_rotated(client_id: int, site_url: str, old_revoked: bool):
    """Called after an approved rotate_wordpress_password ran."""
    url = _norm(site_url)
    current = record(client_id)
    sites = dict(current.get("sites") or {})
    entry = dict(sites.get(url) or {"status": NOT_CHECKED})
    entry["password_rotated_at"] = _now()
    entry["old_password_revoked"] = bool(old_revoked)
    sites[url] = entry
    _save(client_id, {**current, "sites": sites, "advisory": advisory.NAME})


# ─── Scan + views ────────────────────────────────────────────────────────────

def run_scan(client_id: int = None) -> dict:
    """Check every known site (or one client's). Admin-triggered."""
    sites = inventory(client_id)
    by_url, records = {}, {}
    summary = {"sites": len(sites), "alerts": 0, PATCHED: 0, VULNERABLE: 0, UNKNOWN: 0}
    for site in sites:
        url = site["site_url"]
        if url not in by_url:
            by_url[url] = check_site(url)       # one request set per URL, however many clients
        cid = site["client_id"]
        if cid not in records:
            records[cid] = record(cid)
        records[cid], alerted = update_site(site, by_url[url], records[cid])
        summary[by_url[url]["status"]] += 1
        summary["alerts"] += int(alerted)
    print(f"[{SERVICE_NAME}] scan done: {summary}")
    return summary


def _advisory_view() -> dict:
    return {"name": advisory.NAME, "cves": list(advisory.CVES), "fixed": advisory.fixed_versions_text(),
            "oldest_fixed_branch": ".".join(map(str, advisory.OLDEST_FIXED_BRANCH)),
            "public_since": advisory.PUBLIC_SINCE, "recorded_on": advisory.RECORDED_ON}


def _joined(sites: list) -> list:
    records, out = {}, []
    for site in sites:
        cid = site["client_id"]
        if cid not in records:
            records[cid] = (record(cid).get("sites") or {})
        entry = records[cid].get(site["site_url"]) or {}
        out.append({**site, "status": entry.get("status") if entry.get("checked_at") else NOT_CHECKED,
                    "reason": entry.get("reason", ""),
                    "reason_text": REASON_TEXT.get(entry.get("reason", ""), ""),
                    "php_version": entry.get("php_version", ""), "header": entry.get("header", ""),
                    "checked_at": entry.get("checked_at"), "status_since": entry.get("status_since"),
                    "password_rotated_at": entry.get("password_rotated_at"),
                    "old_password_revoked": entry.get("old_password_revoked")})
    return out


def overview() -> dict:
    """The Control Room summary: every site, joined with its last check."""
    sites = _joined(inventory())
    counts = {s: sum(1 for x in sites if x["status"] == s) for s in (VULNERABLE, UNKNOWN, NOT_CHECKED, PATCHED)}
    order = {VULNERABLE: 0, UNKNOWN: 1, NOT_CHECKED: 2, PATCHED: 3}
    sites.sort(key=lambda s: (order.get(s["status"], 9), s["client_id"]))
    return {"advisory": _advisory_view(), "counts": counts, "sites": sites}


def client_view(client_id: int) -> dict:
    return {"advisory": _advisory_view(), "sites": _joined(inventory(client_id))}


_HOSTED_BY = {ORIGIN_INSTAWP: "uallak (InstaWP)", ORIGIN_CLIENT_CONNECTED: "the client's hosting provider",
              ORIGIN_CLIENT_ASSESSED: "the client's hosting provider"}


def site_security_fact(client_id: int) -> dict:
    """client_state's `site_security` section: stored facts only, no request
    to any site. `verified_safe` is always False — a header is not proof."""
    sites = client_view(client_id)["sites"]
    if not sites:
        return {"has_wordpress_site": False}
    return {"has_wordpress_site": True, "advisory": f"{advisory.NAME} ({advisory.PUBLIC_SINCE})",
            "verified_safe": False,
            "sites": [{"site_url": s["site_url"], "php_status": s["status"],
                       "php_version_reported": s["php_version"] or None,
                       "last_check": s["checked_at"], "hosted_by": _HOSTED_BY.get(s["origin"], "")}
                      for s in sites]}
