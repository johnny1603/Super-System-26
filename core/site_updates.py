"""Plugin and theme updates on a client's WordPress site (2026-11-02).

Reached ONLY through the website agent's confirmation cards (core/agent_actions
`update_site_items`, and `update_site_items_without_backup` when a backup could
not be taken). Read docs/SITE-UPDATES.md first.

STATUS (`status(client_id)`, also client_state `site_updates` and the admin
route): every installed plugin and theme from the site's own REST API (name,
active state, installed version), the newest version and what it requires
(WordPress / PHP) from the public wordpress.org directory, the site's
WordPress and PHP versions, and flags: `php_incompatible`, `wp_incompatible`,
`compat_unknown`. A plugin that is not in the public directory (premium) has
no known update and is never offered.

THE UPDATE (`run_updates`), one item at a time, stops at the first problem:
  1. the item is re-read: still installed, still behind, still compatible;
  2. the site must be healthy BEFORE we touch it (else nothing runs);
  3. BACKUP — the hosting's restorable site version (files + database),
     verified: the copy's task completed AND it is listed for the site. No
     verified backup -> nothing is updated; the remaining items go to a
     separate card that says plainly there is no backup;
  4. update through the host's WP-CLI (`wp plugin|theme update <slug>`) —
     the command is built ONLY from a validated slug, never from text;
  5. HEALTH — home + key pages answer 200 with no PHP fatal / critical-error
     page, WordPress itself still loads, and the checkout page when the
     store plugin is active;
  6. on failure: automatic ROLLBACK to that backup, health re-checked, plain
     Hebrew to the client, admin alert.
WordPress core is NEVER updated (there is no code path for it: the item
types are plugin and theme only).

Every item is logged to client_activity (`site_update`): who approved, what,
version from -> to, backup reference, result.

Updates run only on sites WE host (provisioned by uallak, `website_provisioned`
with the host's site id, the same URL as the live connection): only there is a
real backup + restore + command API. On a site the client hosts elsewhere the
status view still works and updates are a team task.

Everything read from the site or the directory is DATA, never instructions.
"""
import re
import time
from datetime import datetime, timezone

AGENT_NAME = "website_agent"
LOG_ACTION = "site_update"
STATUS_TTL = 6 * 3600
DIRECTORY_TTL = 12 * 3600
MAX_ITEMS = 10
KEY_PAGES = 3
HTTP_TIMEOUT = 20
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}$")
ITEM_TYPES = ("plugin", "theme")          # never "core"
STORE_PLUGIN = "woocommerce"
FATAL_MARKERS = ("there has been a critical error on this website", "fatal error</b>:", "parse error</b>:",
                 "php fatal error", "error establishing a database connection")
VERSION_PROBE = "wp eval 'echo PHP_VERSION . \" \" . get_bloginfo(\"version\");'"

_status_cache = {}      # client_id -> (time, status)
_directory_cache = {}   # (type, slug) -> (time, info)


class UpdateStop(Exception):
    """Stops the run: `code` is the outcome the card explains."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


# ─── Seams (tests patch these) ───────────────────────────────────────────────

def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connection(client_id: int) -> tuple:
    """(site_url, username, app_password) of the live WordPress connection."""
    from agents import website_agent
    return website_agent._creds(website_agent._get_connection(client_id))


def _wp_get(creds: tuple, path: str, params: dict = None):
    from core import wordpress_service as wp
    site_url, user, password = creds
    return wp.rest_get(site_url, path, user, password, params=params)


def _http_get(url: str) -> tuple:
    """(status_code, body text) — an ordinary anonymous GET, like a visitor."""
    import httpx
    r = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True,
                  headers={"User-Agent": "uallak-site-health/1.0", "Cache-Control": "no-cache"})
    return r.status_code, r.text[:400_000]


def _directory_fetch(kind: str, slug: str) -> dict:
    """The public wordpress.org directory entry ({} = not listed)."""
    import httpx
    if kind == "plugin":
        url = "https://api.wordpress.org/plugins/info/1.2/"
        params = {"action": "plugin_information", "request[slug]": slug}
    else:
        url = "https://api.wordpress.org/themes/info/1.2/"
        params = {"action": "theme_information", "request[slug]": slug}
    r = httpx.get(url, params=params, timeout=10)
    if r.status_code != 200:
        return {}
    body = r.json()
    if not isinstance(body, dict) or body.get("error") or not body.get("version"):
        return {}
    return {"version": str(body.get("version") or ""), "requires_wp": str(body.get("requires") or ""),
            "requires_php": str(body.get("requires_php") or "")}


def _host_api():
    from core import instawp_service
    return instawp_service


def _provisioned_rows(client_id: int) -> list:
    return (_db().table("client_activity").select("details,created_at")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", "website_provisioned").order("created_at", desc=True)
            .limit(5).execute().data or [])


def _php_reported(client_id: int, site_url: str) -> str:
    """What the site's own header claimed at the last PHP2Shell check."""
    from core import php_exposure
    sites = (php_exposure.record(client_id) or {}).get("sites") or {}
    entry = sites.get(site_url) or sites.get(site_url.rstrip("/")) or {}
    return str(entry.get("php_version") or "").split(",")[0].strip()


def _log_activity(client_id: int, details: dict):
    from agents.client_agent import log_activity
    log_activity(client_id, AGENT_NAME, LOG_ACTION, details, {"result": details.get("result")})


def _alert(issue: str):
    from core.agent_base import agent_alert
    agent_alert(AGENT_NAME, [issue])


def _sleep(seconds: float):
    time.sleep(seconds)


# ─── Versions ────────────────────────────────────────────────────────────────

def _vtuple(version: str) -> tuple:
    parts = re.findall(r"\d+", str(version or ""))
    return tuple(int(p) for p in parts[:4]) if parts else ()


def newer(available: str, installed: str) -> bool:
    a, b = _vtuple(available), _vtuple(installed)
    return bool(a) and bool(b) and a > b


def _needs(required: str, have: str):
    """True = incompatible, False = fine, None = cannot tell."""
    if not _vtuple(required):
        return False
    if not _vtuple(have):
        return None
    n = len(_vtuple(required))
    return _vtuple(have)[:n] < _vtuple(required)


def slug_of(kind: str, item: dict) -> str:
    if kind == "plugin":
        plugin = str(item.get("plugin") or "")
        return plugin.split("/")[0] if "/" in plugin else plugin.rsplit(".php", 1)[0]
    return str(item.get("stylesheet") or "")


def _name(raw) -> str:
    if isinstance(raw, dict):
        raw = raw.get("raw") or raw.get("rendered") or ""
    return re.sub(r"<[^>]+>", "", str(raw or "")).strip()[:120]


# ─── Hosting (where a real backup + restore + command exists) ────────────────

def _norm(url: str) -> str:
    return re.sub(r"^https?://", "", str(url or "").strip().lower()).rstrip("/")


def host_of(client_id: int, site_url: str) -> dict:
    """{"provider": "instawp", "site_id"} when THIS connected site is one we
    provisioned (same URL), else {}."""
    try:
        rows = _provisioned_rows(client_id)
    except Exception:
        return {}
    for row in rows:
        details = row.get("details") or {}
        if details.get("provider") == "instawp" and details.get("site_id") \
                and _norm(details.get("site_url")) == _norm(site_url):
            return {"provider": "instawp", "site_id": details["site_id"]}
    return {}


def _environment(client_id: int, creds: tuple, host: dict) -> dict:
    """{"php", "wp", "source"} — from the host's WP-CLI when we host the site,
    else the homepage's generator tag + the last reported PHP header."""
    if host:
        try:
            out = _host_api().run_cmd(host["site_id"], VERSION_PROBE, timeout_seconds=60)
            parts = out["output"].split()
            if out["exit_code"] == 0 and len(parts) >= 2:
                return {"php": parts[-2], "wp": parts[-1], "source": "host"}
        except Exception:
            pass
    env = {"php": "", "wp": "", "source": "public"}
    try:
        status, html = _http_get(creds[0].rstrip("/") + "/")
        m = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']WordPress\s+([\d.]+)', html or "", re.I)
        env["wp"] = m.group(1) if m else ""
    except Exception:
        pass
    try:
        env["php"] = _php_reported(client_id, creds[0])
    except Exception:
        pass
    return env


# ─── Status ──────────────────────────────────────────────────────────────────

def _directory(kind: str, slug: str) -> dict:
    hit = _directory_cache.get((kind, slug))
    if hit and hit[0] > time.time() - DIRECTORY_TTL:
        return hit[1]
    try:
        info = _directory_fetch(kind, slug)
    except Exception:
        return {"unreadable": True}
    _directory_cache[(kind, slug)] = (time.time(), info)
    return info


def _entry(kind: str, raw: dict, env: dict) -> dict:
    slug = slug_of(kind, raw)
    installed = str(raw.get("version") or "")
    entry = {"type": kind, "slug": slug, "name": _name(raw.get("name")) or slug,
             "active": raw.get("status") in ("active", "network-active"),
             "installed": installed, "available": "", "update": False, "flags": []}
    if not SLUG_RE.match(slug or ""):
        entry["flags"].append("not_updatable_here")
        return entry
    info = _directory(kind, slug)
    if info.get("unreadable"):
        entry["flags"].append("directory_unreadable")
        return entry
    if not info:
        entry["flags"].append("not_in_public_directory")
        return entry
    entry["available"] = info["version"]
    entry["requires_php"], entry["requires_wp"] = info.get("requires_php", ""), info.get("requires_wp", "")
    if not newer(info["version"], installed):
        return entry
    entry["update"] = True
    php, wordpress = _needs(info.get("requires_php"), env.get("php")), _needs(info.get("requires_wp"), env.get("wp"))
    if php:
        entry["flags"].append("php_incompatible")
    if wordpress:
        entry["flags"].append("wp_incompatible")
    if php is None or wordpress is None:
        entry["flags"].append("compat_unknown")
    return entry


def offerable(entry: dict) -> bool:
    return entry.get("update") and not ({"php_incompatible", "wp_incompatible"} & set(entry.get("flags") or []))


def status(client_id: int, refresh: bool = False) -> dict:
    """The status view. Raises when the site's own list cannot be read (the
    caller treats that as a failed read, never as "no updates")."""
    hit = _status_cache.get(client_id)
    if hit and not refresh and hit[0] > time.time() - STATUS_TTL:
        return hit[1]
    creds = _connection(client_id)
    if not creds[0] or not creds[1] or not creds[2]:
        return {"connected": False}
    host = host_of(client_id, creds[0])
    env = _environment(client_id, creds, host)
    plugins = _wp_get(creds, "wp/v2/plugins", {"_fields": "plugin,status,name,version,requires_php,requires_wp"})
    themes = _wp_get(creds, "wp/v2/themes", {"_fields": "stylesheet,status,name,version,requires_php,requires_wp"})
    items = [_entry("plugin", p, env) for p in plugins or []] + [_entry("theme", t, env) for t in themes or []]
    offered = [i for i in items if offerable(i)]
    out = {"connected": True, "site_url": creds[0], "we_can_update": bool(host),
           "wordpress_version": env.get("wp") or None, "php_version": env.get("php") or None,
           "versions_source": env.get("source"),
           "core_updates": "never automatic — WordPress itself is updated by the team only",
           "items": items,
           "updates_available": [f"{i['type']}:{i['slug']}" for i in offered],
           "blocked_updates": [{"item": f"{i['type']}:{i['slug']}", "name": i["name"],
                                "flags": [f for f in i["flags"] if f.endswith("_incompatible")]}
                               for i in items if i.get("update") and not offerable(i)],
           "checked_at": _now()}
    out["offer_line_he"] = offer_line(offered) if host else ""
    _status_cache[client_id] = (time.time(), out)
    return out


def forget(client_id: int):
    _status_cache.pop(client_id, None)


def offer_line(offered: list) -> str:
    """The plain sentence the website agent uses ("יש 3 עדכונים לתוספים...")."""
    plugins = sum(1 for i in offered if i["type"] == "plugin")
    themes = len(offered) - plugins
    if not offered:
        return ""
    parts = []
    if plugins:
        parts.append("עדכון אחד לתוסף" if plugins == 1 else f"{plugins} עדכונים לתוספים")
    if themes:
        parts.append("עדכון אחד לתבנית" if themes == 1 else f"{themes} עדכונים לתבניות")
    return f"יש {' ו'.join(parts)}, רוצה שאעדכן? אני אגבה לפני ואבדוק אחרי."


def for_agent(client_id: int) -> dict:
    """client_state's section: what an agent may say, never a key or a URL
    beyond the site itself."""
    s = status(client_id)
    if not s.get("connected"):
        return {"connected": False}
    return {"we_can_update": s["we_can_update"],
            "updates": [{"item": f"{i['type']}:{i['slug']}", "name": i["name"], "from": i["installed"],
                         "to": i["available"], "active": i["active"]}
                        for i in s["items"] if offerable(i)],
            "blocked_updates": s["blocked_updates"],
            "wordpress_version": s["wordpress_version"], "php_version": s["php_version"],
            "offer_line_he": s["offer_line_he"],
            "note": ("" if s["we_can_update"] else
                     "this site is hosted by the client, not by us: we can see the updates but a team member "
                     "runs them by hand — offer a team request, never an update card"),
            "core": "WordPress itself is never updated automatically"}


# ─── Choosing what a card covers ─────────────────────────────────────────────

def parse_request(value) -> list:
    """The model's `items` param -> ["all"] or ["plugin:slug", ...]."""
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\s]+", value) if v]
    if not isinstance(value, list) or not value or len(value) > MAX_ITEMS:
        raise ValueError(f"items must be 'all' or 1-{MAX_ITEMS} entries like plugin:slug")
    out = []
    for v in value:
        v = str(v).strip().lower()
        if v == "all":
            return ["all"]
        kind, _, slug = v.partition(":")
        if kind not in ITEM_TYPES or not SLUG_RE.match(slug):
            raise ValueError("each item must be plugin:<slug> or theme:<slug>")
        out.append(f"{kind}:{slug}")
    return sorted(set(out))


def plan(client_id: int, requested: list) -> dict:
    """{"items": [...]} for the card, or {"refuse": code, ...}."""
    s = status(client_id, refresh=True)
    if not s.get("connected"):
        return {"refuse": "no_site"}
    if not s["we_can_update"]:
        return {"refuse": "not_hosted", "updates": len(s["updates_available"])}
    wanted = set(requested)
    chosen = [i for i in s["items"] if offerable(i)
              and ("all" in wanted or f"{i['type']}:{i['slug']}" in wanted)]
    blocked = [b for b in s["blocked_updates"] if "all" in wanted or b["item"] in wanted]
    if not chosen:
        return {"refuse": "incompatible" if blocked else "nothing_to_update"}
    return {"items": [{"type": i["type"], "slug": i["slug"], "name": i["name"], "from": i["installed"],
                       "to": i["available"]} for i in chosen[:MAX_ITEMS]],
            "blocked": blocked}


def validate_items(items) -> list:
    """The STORED card params, re-checked at approval."""
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise ValueError("items missing")
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("type") not in ITEM_TYPES or not SLUG_RE.match(str(it.get("slug") or "")):
            raise ValueError("bad item")
        out.append({"type": it["type"], "slug": it["slug"], "name": _name(it.get("name")) or it["slug"],
                    "from": str(it.get("from") or "")[:40], "to": str(it.get("to") or "")[:40]})
    return out


def describe(items: list) -> str:
    return "\n".join(f"• {i['name']}: {i['from']} → {i['to']}" for i in items or [])


# ─── Backup / health / rollback ──────────────────────────────────────────────

def take_backup(host: dict, label: str) -> str:
    """The version id of a VERIFIED restorable copy, or raises."""
    api = _host_api()
    created = api.create_site_version(host["site_id"])
    if created.get("task_id"):
        api.wait_for_task(created["task_id"], sleep=_sleep)
    listed = {str(v.get("id")) for v in api.list_site_versions(host["site_id"]) if isinstance(v, dict)}
    if str(created["id"]) not in listed:
        raise RuntimeError("backup not listed for the site after its task completed")
    api.name_site_version(created["id"], label)
    return str(created["id"])


def restore_backup(host: dict, ref: str):
    api = _host_api()
    task = api.restore_site_version(host["site_id"], ref)
    if task.get("task_id"):
        api.wait_for_task(task["task_id"], sleep=_sleep)


def key_pages(creds: tuple, store_active: bool) -> list:
    """Home + up to KEY_PAGES published pages + the checkout page when the
    store plugin is active — chosen BEFORE the update."""
    site = creds[0].rstrip("/")
    urls = [site + "/"]
    try:
        for p in _wp_get(creds, "wp/v2/pages", {"per_page": KEY_PAGES, "status": "publish",
                                                "orderby": "menu_order", "order": "asc", "_fields": "link"}) or []:
            if str(p.get("link") or "").startswith(site) and p["link"] not in urls:
                urls.append(p["link"])
    except Exception:
        pass
    if store_active:
        checkout = ""
        try:
            hits = _wp_get(creds, "wp/v2/pages", {"slug": "checkout", "_fields": "link"}) or []
            checkout = hits[0].get("link", "") if hits else ""
        except Exception:
            pass
        urls.append(checkout if checkout.startswith(site) else site + "/checkout/")
    return urls


def health(host: dict, urls: list) -> dict:
    """{"ok", "problems"}: every page 200 without a fatal / critical-error
    page, and WordPress itself loads (the host's WP-CLI boots it)."""
    problems = []
    stamp = int(time.time())
    for url in urls:
        try:
            code, body = _http_get(f"{url}{'&' if '?' in url else '?'}uallak_health={stamp}")
        except Exception as e:
            problems.append(f"{url}: unreachable ({type(e).__name__})")
            continue
        if code != 200:
            problems.append(f"{url}: HTTP {code}")
        elif any(m in (body or "").lower() for m in FATAL_MARKERS):
            problems.append(f"{url}: PHP fatal / critical error page")
    try:
        out = _host_api().run_cmd(host["site_id"], "wp eval 'echo \"ok\";'", timeout_seconds=60)
        if out["exit_code"] != 0 or not out["output"].endswith("ok"):
            problems.append(f"WordPress does not load cleanly (exit {out['exit_code']})")
    except Exception as e:
        problems.append(f"WordPress load check failed ({type(e).__name__})")
    return {"ok": not problems, "problems": problems}


def _installed(creds: tuple, item: dict) -> dict:
    """The live plugin/theme row for the item ({} = not installed)."""
    if item["type"] == "plugin":
        rows = _wp_get(creds, "wp/v2/plugins", {"_fields": "plugin,status,name,version"}) or []
    else:
        rows = _wp_get(creds, "wp/v2/themes", {"_fields": "stylesheet,status,name,version"}) or []
    return next((r for r in rows if slug_of(item["type"], r) == item["slug"]), {})


def _store_active(creds: tuple) -> bool:
    try:
        rows = _wp_get(creds, "wp/v2/plugins", {"_fields": "plugin,status"}) or []
    except Exception:
        return False
    return any(slug_of("plugin", r) == STORE_PLUGIN and r.get("status") in ("active", "network-active") for r in rows)


def update_command(item: dict) -> str:
    """The ONLY command this module sends that changes the site."""
    if item.get("type") not in ITEM_TYPES or not SLUG_RE.match(str(item.get("slug") or "")):
        raise ValueError("refusing to build an update command for this item")
    return f"wp {item['type']} update {item['slug']} --no-color"


# ─── The run ─────────────────────────────────────────────────────────────────

def run_updates(client_id: int, items: list, who: str, proposal_id=None, without_backup: bool = False) -> dict:
    """One item at a time. Returns {"updated", "skipped", "stopped", "remaining",
    "problems"}; `stopped` is "" when everything went through, else the code
    the card explains (backup_failed / rolled_back / rollback_failed /
    failed_no_backup / site_unhealthy / not_hosted / no_site)."""
    items = validate_items(items)
    creds = _connection(client_id)
    out = {"updated": [], "skipped": [], "stopped": "", "remaining": [], "problems": []}
    if not creds[0] or not creds[1] or not creds[2]:
        out.update(stopped="no_site", remaining=items)
        return out
    host = host_of(client_id, creds[0])
    if not host:
        out.update(stopped="not_hosted", remaining=items)
        return out
    env = _environment(client_id, creds, host)
    urls = key_pages(creds, _store_active(creds))

    for index, item in enumerate(items):
        record = {"who": who, "proposal_id": proposal_id, "type": item["type"], "slug": item["slug"],
                  "name": item["name"], "from": "", "to": "", "backup_ref": "", "result": "",
                  "with_backup": not without_backup}
        try:
            live = _installed(creds, item)
            if not live:
                record["result"] = "not_installed"
                out["skipped"].append(record)
                continue
            record["from"] = str(live.get("version") or "")
            entry = _entry(item["type"], live, env)
            if not entry["update"]:
                record["result"] = "already_current"
                out["skipped"].append(record)
                continue
            if not offerable(entry):
                record["result"] = "incompatible:" + ",".join(f for f in entry["flags"] if f.endswith("_incompatible"))
                out["skipped"].append(record)
                continue

            before = health(host, urls)
            if not before["ok"]:
                # Not ours to fix by updating: a broken site stays untouched
                record["result"] = "site_unhealthy_before"
                out["problems"] += before["problems"]
                raise UpdateStop("site_unhealthy")

            if not without_backup:
                try:
                    record["backup_ref"] = take_backup(host, f"before {item['slug']}"[:25])
                except Exception as e:
                    record["result"] = "backup_failed"
                    out["problems"].append(f"backup: {type(e).__name__}: {str(e)[:200]}")
                    raise UpdateStop("backup_failed")

            try:
                result = _host_api().run_cmd(host["site_id"], update_command(item), timeout_seconds=300)
                command_ok = result["exit_code"] == 0
            except Exception as e:
                command_ok = False
                out["problems"].append(f"update command: {type(e).__name__}")
            after = health(host, urls)
            now_live = {}
            try:
                now_live = _installed(creds, item)
            except Exception:
                pass
            record["to"] = str(now_live.get("version") or "")

            if after["ok"] and command_ok and newer(record["to"], record["from"]):
                record["result"] = "updated"
                out["updated"].append(record)
                continue
            if after["ok"]:
                # Nothing broke, nothing changed: report it, no restore needed
                record["result"] = "not_updated"
                out["problems"].append(f"{item['slug']}: update did not apply")
                out["skipped"].append(record)
                continue

            out["problems"] += after["problems"]
            if without_backup:
                record["result"] = "failed_no_backup"
                raise UpdateStop("failed_no_backup")
            try:
                restore_backup(host, record["backup_ref"])
                restored = health(host, urls)
            except Exception as e:
                restored = {"ok": False, "problems": [f"restore: {type(e).__name__}: {str(e)[:200]}"]}
            if restored["ok"]:
                record["result"] = "rolled_back"
                raise UpdateStop("rolled_back")
            out["problems"] += restored["problems"]
            record["result"] = "rollback_failed"
            raise UpdateStop("rollback_failed")
        except UpdateStop as stop:
            out["stopped"] = stop.code
            out["remaining"] = items[index:] if stop.code in ("backup_failed", "site_unhealthy") else items[index + 1:]
            break
        finally:
            if record["result"]:
                try:
                    _log_activity(client_id, {**record, "at": _now()})
                except Exception as e:
                    out["problems"].append(f"log not written: {type(e).__name__}")

    forget(client_id)
    _alert_if_needed(client_id, out, creds[0])
    return out


_ALERTS = {
    "rolled_back": "an update broke the site and it was ROLLED BACK automatically to the backup taken just before — "
                   "the site is healthy again; look at why before offering that update again",
    "rollback_failed": "URGENT: an update broke the site and the automatic ROLLBACK did not bring it back — restore it "
                       "by hand from the host's site versions now",
    "failed_no_backup": "URGENT: an update approved WITHOUT a backup broke the site — fix it by hand now",
    "backup_failed": "a verified backup could not be taken, so nothing was updated (the client got a separate "
                     "no-backup card) — check the host's site versions",
    "site_unhealthy": "the site was already failing its health check before any update — nothing was updated",
}


def _alert_if_needed(client_id: int, out: dict, site_url: str):
    if out["stopped"] in _ALERTS:
        _alert(f"client {client_id}: site updates on {site_url}: {_ALERTS[out['stopped']]}. "
               f"Details: {'; '.join(out['problems'])[:600]}")
