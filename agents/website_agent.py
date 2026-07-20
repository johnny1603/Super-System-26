"""uallak's website agent — controls a client's EXISTING WordPress site.

Phase 1 scope (existing sites only): connect via Application Password, read
site state, publish/edit posts and pages, basic SEO fixes (slug, excerpt,
media alt text), install a free SEO plugin. Building NEW sites for clients
without one is Phase 2 — it needs a real hosting/cost decision first (see
.claude/skills/website/SKILL.md, "Phase 2" section).

Like meta_content_agent, this is a pipe, not a brain: content arrives
ALREADY GENERATED (title/body/media URLs) and goes to the client's site.
No LLM calls here at all. New content defaults to status='draft' — a human
reviews and publishes, same principle as campaigns created PAUSED.

Connection flow (no OAuth): the client fills site URL + WP username +
Application Password in the dashboard card → POST /api/website/connect →
connect_site() validates against the live site and stores ONE row in
client_accounts: platform='wordpress', account_id=site URL,
access_token='username:app_password' (WP usernames cannot contain ':').

Costs: everything this agent does is free (core WP REST API + free plugins
from wordpress.org). Anything with a price tag — hosting, domain, paid
plugin/theme licenses — is deliberately NOT reachable from here.
"""
import os
import re
import time
from datetime import datetime, timedelta, timezone

from supabase import create_client as _supabase_client

from core import wordpress_service as wp
from core.agent_base import agent_alert, log_step, timed_step

AGENT_NAME = "website_agent"
WORDPRESS_PLATFORM = "wordpress"

VALID_CONTENT_TYPES = ("post", "page")
# draft = default (human reviews before it goes live); publish allowed for
# explicit admin-triggered publishing
VALID_STATUSES = ("draft", "publish")
# Whitelist of fields update_content will pass through to WP — everything else
# in a spec is ignored, so a bad payload can't flip site settings.
EDITABLE_FIELDS = ("title", "content", "excerpt", "slug", "status", "featured_media")

OVERVIEW_CACHE_SECONDS = 300  # same 5-min TTL as the ads agents
RECENT_CONTENT_LIMIT = 5
ISSUE_DEDUP_DAYS = 3  # don't re-alert the same site problem more than ~2x/week

# ── Standing quality rules (every site we build OR edit — not one-time) ──────
# Machine-enforced here: accessibility plugin + accessible/SEO-valid HTML on
# every publish, required page structure, plugin-count budget, alt text, WebP
# (conversion lives in wordpress_service.upload_media_from_url). Template-time
# rules that can't be REST-verified (genuinely mobile-first theme, real RTL
# layout + Hebrew fonts — not a mechanical LTR flip) are enforced as the
# master-template checklist in the website skill. Design decisions come from
# sales-chat data + the client's logo when present — NEVER a design
# questionnaire to the client.

# Required page structure per site: matched loosely against page slug/title
# (English or Hebrew). Home is WP's front page and always exists.
REQUIRED_PAGES = {
    "about":    ("about", "אודות", "מי אנחנו", "עלינו"),
    "services": ("services", "שירותים", "מה אנחנו עושים"),
    "contact":  ("contact", "צור קשר", "יצירת קשר", "צרו קשר"),
    "legal":    ("privacy", "terms", "תקנון", "פרטיות", "מדיניות"),
}
# Loading-speed budget: every plugin adds weight; more than this needs a reason
MAX_ACTIVE_PLUGINS = 8
MEDIA_ALT_SAMPLE = 20

# Neutral-by-industry palettes for clients with no logo yet (hex: primary,
# accent, background). Deliberately conservative and professional — the
# palette is a placeholder a future brand identity replaces, not a design
# statement.
NEUTRAL_PALETTES = {
    "food":     ("#7A3E2E", "#D98E4A", "#FAF6F1"),
    "beauty":   ("#8A5A6B", "#D4A5B5", "#FBF7F8"),
    "kids":     ("#2E6E8A", "#F2B84B", "#F9FBFC"),
    "tourism":  ("#1F6E5C", "#D9A44A", "#F7FAF9"),
    "b2b":      ("#1F3A5F", "#4A7AB5", "#F7F9FB"),
    "home":     ("#4A5A3E", "#A5B58A", "#F9FAF7"),
    "default":  ("#2A3B4C", "#5A8AA8", "#F8F9FA"),
}


def content_quality_issues(html: str, require_excerpt: bool = False,
                           has_excerpt: bool = False) -> list:
    """The shared accessibility + technical-SEO gate for EVERY publish/update.
    Regex heuristics, not a DOM parser — kept deliberately simple; they catch
    the rules' violations in LLM-generated HTML, which is the only HTML that
    flows through here.

    Enforced: no <h1> in the body (WP renders the title as the page's single
    H1), heading hierarchy starts at h2 with no level jumps, alt text on
    every <img>, a label/aria-label on every form field, and (on create) an
    excerpt — core WP's meta-description surface."""
    issues = []
    body = html or ""

    if re.search(r"<h1[\s>]", body, re.I):
        issues.append("body contains an <h1> — the page title is the single H1; start body headings at <h2>")

    levels = [int(m) for m in re.findall(r"<h([1-6])[\s>]", body, re.I)]
    if levels:
        if levels[0] > 2:
            issues.append(f"first heading is <h{levels[0]}> — hierarchy must start at <h2>")
        for previous, current in zip(levels, levels[1:]):
            if current > previous + 1:
                issues.append(f"heading jump <h{previous}> → <h{current}> — nest headings without skipping levels")
                break

    for tag in re.findall(r"<img\b[^>]*>", body, re.I):
        if not re.search(r"""alt\s*=\s*["'][^"']+["']""", tag, re.I):
            issues.append("every <img> needs non-empty alt text (accessibility + SEO standing rule)")
            break

    for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", body, re.I):
        if re.search(r"""type\s*=\s*["'](?:hidden|submit|button)["']""", tag, re.I):
            continue
        labeled = re.search(r"aria-label(?:ledby)?\s*=", tag, re.I)
        id_match = re.search(r"""id\s*=\s*["']([^"']+)["']""", tag, re.I)
        if not labeled and not (id_match and re.search(
                rf"""<label\b[^>]*for\s*=\s*["']{re.escape(id_match.group(1))}["']""", body, re.I)):
            issues.append("every form field needs a <label for=...> or aria-label (accessibility standing rule)")
            break

    if require_excerpt and not has_excerpt:
        issues.append("excerpt is required on new content — it is the page's meta-description surface")
    return issues

_overview_cache = {}  # client_id -> (fetched_at, overview)

# Created lazily — no DB client at import time (api_server imports every agent at startup)
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        _db_instance = _supabase_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _db_instance


def _get_connection(client_id: int) -> dict:
    result = (
        _db().table("client_accounts")
        .select("*")
        .eq("client_id", client_id)
        .eq("platform", WORDPRESS_PLATFORM)
        .eq("status", "active")
        # newest row wins; client_accounts has no created_at column, so order
        # by the auto-incrementing id (same semantics)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def _creds(connection: dict):
    """(site_url, username, app_password) from a client_accounts row."""
    username, _, app_password = (connection.get("access_token") or "").partition(":")
    return connection.get("account_id") or "", username, app_password


def is_connected(client_id: int) -> bool:
    connection = _get_connection(client_id)
    return bool(connection.get("account_id") and ":" in (connection.get("access_token") or ""))


def is_provisioned_by_us(client_id: int) -> bool:
    """True when THIS client's site was created via provision_site (InstaWP,
    billable to us) rather than an existing site they connected — the pull
    point for budget_agent, which needs to know whether the InstaWP hosting
    cost basis (PRICING["website"]["new_site_hosting"]) actually applies."""
    rows = (_db().table("client_activity").select("id")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", "website_provisioned").limit(1).execute().data)
    return bool(rows)


def _log_activity(client_id: int, action_type: str, details: dict, result: dict = None):
    _db().table("client_activity").insert({
        "client_id": client_id,
        "agent_name": AGENT_NAME,
        "action_type": action_type,
        "details": details,
        "result": result or {},
    }).execute()


# ─── Connect ──────────────────────────────────────────────────────────────────

def connect_site(client_id: int, site_url: str, username: str, app_password: str) -> dict:
    """Validate the credentials against the live site, then store the
    connection. Failures return an error dict (bad user input, not an
    incident) — no alert."""
    log_step(AGENT_NAME, "connect_site", f"client {client_id}")
    try:
        site_url = wp.normalize_site_url(site_url)
    except ValueError:
        return {"success": False, "error": "invalid site url"}
    username, app_password = username.strip(), app_password.strip()
    if not username or not app_password:
        return {"success": False, "error": "missing username or application password"}

    try:
        user = wp.get_current_user(site_url, username, app_password)
        info = wp.get_site_info(site_url, username, app_password)
    except wp.WordPressError as e:
        log_step(AGENT_NAME, "connect_site", f"client {client_id}: validation failed — {e}")
        return {"success": False,
                "error": "could not authenticate — check the site URL, username and "
                         "application password (and that the site allows REST API auth)"}
    except Exception as e:
        log_step(AGENT_NAME, "connect_site", f"client {client_id}: site unreachable — {e}")
        return {"success": False, "error": "site unreachable"}

    capabilities = user.get("capabilities") or {}
    from agents.client_agent import upsert_account
    upsert_account(client_id, WORDPRESS_PLATFORM, site_url,
                   f"{username}:{app_password}", "active")
    _overview_cache.pop(client_id, None)
    _log_activity(client_id, "website_connected",
                  {"site_url": site_url, "site_name": info.get("name", ""),
                   "seo_plugin": info.get("seo_plugin"),
                   "can_manage_plugins": bool(capabilities.get("install_plugins"))})
    log_step(AGENT_NAME, "connect_site", f"client {client_id}: connected {site_url}")
    return {"success": True, "site_url": site_url, "site_name": info.get("name", ""),
            "seo_plugin": info.get("seo_plugin"),
            "can_manage_plugins": bool(capabilities.get("install_plugins"))}


# ─── Overview (support chat + admin) ──────────────────────────────────────────

def get_site_overview(client_id: int) -> dict:
    """Site name, SEO plugin, recent posts/pages. Cached 5 minutes so the
    support chat can always include it, same as campaign performance."""
    cached = _overview_cache.get(client_id)
    if cached and time.time() - cached[0] < OVERVIEW_CACHE_SECONDS:
        return cached[1]

    connection = _get_connection(client_id)
    if not connection:
        return {"connected": False}
    site_url, username, app_password = _creds(connection)

    overview = {"connected": True, "site_url": site_url}
    try:
        info = wp.get_site_info(site_url, username, app_password)
        overview["site_name"] = info.get("name", "")
        overview["seo_plugin"] = info.get("seo_plugin")
        for content_type, field in (("post", "recent_posts"), ("page", "pages")):
            items = wp.list_content(site_url, username, app_password,
                                    content_type, limit=RECENT_CONTENT_LIMIT)
            overview[field] = [
                {"id": item["id"],
                 "title": (item.get("title") or {}).get("rendered", ""),
                 "status": item.get("status"), "modified": item.get("modified")}
                for item in items
            ]
    except Exception as e:
        # Same contract as the ads agents: support prompt knows an "error"
        # field means "temporary issue reading the data"
        overview["error"] = str(e)

    _overview_cache[client_id] = (time.time(), overview)
    return overview


# ─── Content (publish / edit) ─────────────────────────────────────────────────

def _validate_publish_spec(spec: dict) -> list:
    errors = []
    if spec.get("kind", "post") not in VALID_CONTENT_TYPES:
        errors.append(f"kind must be one of {VALID_CONTENT_TYPES}")
    if not (spec.get("title") or "").strip():
        errors.append("title is required")
    if not (spec.get("content") or "").strip():
        errors.append("content is required")
    if spec.get("status", "draft") not in VALID_STATUSES:
        errors.append(f"status must be one of {VALID_STATUSES}")
    # Standing quality rules apply to every piece of content we create
    errors.extend(content_quality_issues(spec.get("content", ""),
                                         require_excerpt=True,
                                         has_excerpt=bool((spec.get("excerpt") or "").strip())))
    return errors


def publish_content(client_id: int, spec: dict) -> dict:
    """Create a post or page from already-generated content. Defaults to
    draft — an explicit status='publish' is the human sign-off."""
    errors = _validate_publish_spec(spec)
    if errors:
        return {"success": False, "errors": errors}
    connection = _get_connection(client_id)
    if not connection:
        return {"success": False, "errors": ["website not connected"]}
    site_url, username, app_password = _creds(connection)

    kind = spec.get("kind", "post")
    fields = {"title": spec["title"], "content": spec["content"],
              "status": spec.get("status", "draft")}
    for optional in ("excerpt", "slug"):
        if spec.get(optional):
            fields[optional] = spec[optional]

    try:
        created = timed_step(
            AGENT_NAME, "publish_content",
            lambda: wp.create_content(site_url, username, app_password, kind, fields))
    except wp.WordPressError as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: publish to {site_url} failed: {e}"])
        return {"success": False, "errors": [str(e)]}

    _overview_cache.pop(client_id, None)
    result = {"success": True, "id": created.get("id"), "kind": kind,
              "status": created.get("status"), "link": created.get("link", "")}
    _log_activity(client_id, "website_content_created",
                  {"kind": kind, "title": spec["title"], "status": fields["status"]},
                  {"id": created.get("id"), "link": created.get("link", "")})
    log_step(AGENT_NAME, "publish_content",
             f"client {client_id}: {kind} {created.get('id')} ({fields['status']})")
    return result


def update_content(client_id: int, content_type: str, content_id: int, fields: dict) -> dict:
    """Edit an existing post/page. Only EDITABLE_FIELDS pass through."""
    if content_type not in VALID_CONTENT_TYPES:
        return {"success": False, "errors": [f"content_type must be one of {VALID_CONTENT_TYPES}"]}
    allowed = {k: v for k, v in (fields or {}).items() if k in EDITABLE_FIELDS}
    if not allowed:
        return {"success": False, "errors": [f"no editable fields given (allowed: {EDITABLE_FIELDS})"]}
    if "status" in allowed and allowed["status"] not in VALID_STATUSES:
        return {"success": False, "errors": [f"status must be one of {VALID_STATUSES}"]}
    if "content" in allowed:
        # Edits must meet the same standing quality rules as new content
        quality = content_quality_issues(allowed["content"])
        if quality:
            return {"success": False, "errors": quality}
    connection = _get_connection(client_id)
    if not connection:
        return {"success": False, "errors": ["website not connected"]}
    site_url, username, app_password = _creds(connection)

    try:
        updated = timed_step(
            AGENT_NAME, "update_content",
            lambda: wp.update_content(site_url, username, app_password,
                                      content_type, content_id, allowed))
    except wp.WordPressError as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: update {content_type} "
                                 f"{content_id} on {site_url} failed: {e}"])
        return {"success": False, "errors": [str(e)]}

    _overview_cache.pop(client_id, None)
    _log_activity(client_id, "website_content_updated",
                  {"content_type": content_type, "content_id": content_id,
                   "fields": sorted(allowed)},
                  {"link": updated.get("link", "")})
    return {"success": True, "id": updated.get("id"),
            "status": updated.get("status"), "link": updated.get("link", "")}


def update_alt_text(client_id: int, media_id: int, alt_text: str) -> dict:
    """The classic quick SEO fix — alt text is a core WP media field."""
    connection = _get_connection(client_id)
    if not connection:
        return {"success": False, "errors": ["website not connected"]}
    site_url, username, app_password = _creds(connection)
    try:
        wp.update_media(site_url, username, app_password, media_id,
                        {"alt_text": alt_text})
    except wp.WordPressError as e:
        return {"success": False, "errors": [str(e)]}
    _log_activity(client_id, "website_seo_updated",
                  {"media_id": media_id, "field": "alt_text"})
    return {"success": True, "media_id": media_id}


# ─── SEO plugin install ───────────────────────────────────────────────────────

def install_seo_plugin(client_id: int, slug: str = wp.DEFAULT_SEO_PLUGIN_SLUG) -> dict:
    """Install + activate a FREE SEO plugin from wordpress.org (default:
    Yoast). No-op if one is already active. Needs the WP user to have the
    install_plugins capability (admins do)."""
    connection = _get_connection(client_id)
    if not connection:
        return {"success": False, "errors": ["website not connected"]}
    site_url, username, app_password = _creds(connection)

    try:
        info = wp.get_site_info(site_url, username, app_password)
        if info.get("seo_plugin"):
            return {"success": True, "already_installed": info["seo_plugin"]}
        installed = timed_step(
            AGENT_NAME, "install_seo_plugin",
            lambda: wp.install_plugin(site_url, username, app_password, slug))
    except wp.WordPressError as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: SEO plugin install on "
                                 f"{site_url} failed: {e}"])
        return {"success": False, "errors": [str(e)]}

    _overview_cache.pop(client_id, None)
    _log_activity(client_id, "website_seo_plugin_installed",
                  {"slug": slug}, {"plugin": installed.get("plugin", "")})
    log_step(AGENT_NAME, "install_seo_plugin", f"client {client_id}: {slug} installed")
    return {"success": True, "plugin": installed.get("plugin", ""),
            "status": installed.get("status", "")}


# ─── Standing quality rules: site-level checks + brand identity ──────────────

def install_accessibility_plugin(client_id: int) -> dict:
    """Israeli-standard-5568 accessibility plugin on every site — same
    auto-install pattern as the SEO plugin. Tries the free candidates in
    order (a slug occasionally disappears from wordpress.org; the second is
    the fallback, not a choice the client makes)."""
    connection = _get_connection(client_id)
    if not connection:
        return {"success": False, "errors": ["website not connected"]}
    site_url, username, app_password = _creds(connection)

    try:
        installed = {p.get("plugin", "") for p in wp.list_plugins(site_url, username, app_password)}
        for slug in wp.ACCESSIBILITY_PLUGIN_SLUGS:
            if any(slug in plugin for plugin in installed):
                return {"success": True, "already_installed": slug}
    except wp.WordPressError as e:
        return {"success": False, "errors": [f"could not list plugins: {e}"]}

    errors = []
    for slug in wp.ACCESSIBILITY_PLUGIN_SLUGS:
        try:
            result = timed_step(
                AGENT_NAME, "install_accessibility_plugin",
                lambda s=slug: wp.install_plugin(site_url, username, app_password, s))
            _log_activity(client_id, "website_accessibility_plugin_installed",
                          {"slug": slug}, {"plugin": result.get("plugin", "")})
            return {"success": True, "plugin": result.get("plugin", "")}
        except wp.WordPressError as e:
            errors.append(f"{slug}: {e}")
    agent_alert(AGENT_NAME, [f"client {client_id}: accessibility plugin install failed "
                             f"on {site_url}: {'; '.join(errors)}"])
    return {"success": False, "errors": errors}


def run_standards_check(client_id: int, auto_install_plugins: bool = True) -> dict:
    """The standing site-level checklist, run after provisioning and on
    demand: accessibility + SEO plugins present (auto-installed by default),
    required page structure, plugin-count speed budget, alt-text sample.
    Report-only for what it can't fix — missing pages are surfaced, never
    auto-created empty."""
    connection = _get_connection(client_id)
    if not connection:
        return {"connected": False}
    site_url, username, app_password = _creds(connection)
    report = {"connected": True, "site_url": site_url, "issues": [], "fixed": []}

    # 1+2. Plugins: accessibility + SEO present; count within the speed budget
    try:
        if auto_install_plugins:
            a11y = install_accessibility_plugin(client_id)
            if a11y.get("plugin"):
                report["fixed"].append(f"installed accessibility plugin {a11y['plugin']}")
            elif not a11y.get("success"):
                report["issues"].append("accessibility plugin missing and install failed")
            seo = install_seo_plugin(client_id)
            if seo.get("plugin"):
                report["fixed"].append(f"installed SEO plugin {seo['plugin']}")
            elif not seo.get("success"):
                report["issues"].append("SEO plugin missing and install failed")
        active = [p for p in wp.list_plugins(site_url, username, app_password)
                  if p.get("status") == "active"]
        if len(active) > MAX_ACTIVE_PLUGINS:
            report["issues"].append(
                f"{len(active)} active plugins (speed budget: {MAX_ACTIVE_PLUGINS}) — "
                "deactivate what isn't earning its weight")
    except wp.WordPressError as e:
        report["issues"].append(f"plugin checks failed: {e}")

    # 3. Required page structure (home = WP front page, always exists)
    try:
        pages = wp.list_content(site_url, username, app_password, "page", limit=50)
        haystacks = [f"{(p.get('title') or {}).get('rendered', '')} {p.get('slug', '')}".lower()
                     for p in pages]
        for page_key, keywords in REQUIRED_PAGES.items():
            if not any(k.lower() in haystack for k in keywords for haystack in haystacks):
                report["issues"].append(f"required page missing: {page_key} "
                                        f"(expected one of: {', '.join(keywords[:2])}...)")
    except wp.WordPressError as e:
        report["issues"].append(f"page-structure check failed: {e}")

    # 4. Alt text on media (sample of recent uploads)
    try:
        media = wp.list_media(site_url, username, app_password, limit=MEDIA_ALT_SAMPLE)
        missing = [m["id"] for m in media
                   if str(m.get("mime_type", "")).startswith("image/") and not m.get("alt_text")]
        if missing:
            report["issues"].append(f"{len(missing)} of last {len(media)} media items "
                                    f"missing alt text (ids: {missing[:5]}...) — fix via update_alt_text")
    except wp.WordPressError as e:
        report["issues"].append(f"media alt-text check failed: {e}")

    _log_activity(client_id, "website_standards_check",
                  {"issues": report["issues"], "fixed": report["fixed"]})
    if report["issues"]:
        agent_alert(AGENT_NAME, [f"client {client_id}: site standards issues on "
                                 f"{site_url}: {'; '.join(report['issues'])}"])
    log_step(AGENT_NAME, "standards_check",
             f"client {client_id}: {len(report['issues'])} issues, {len(report['fixed'])} fixed")
    return report


def _extract_logo_palette(logo_bytes: bytes) -> list:
    """Dominant brand colors from a logo image: quantize small, drop
    near-white/near-black (backgrounds and outlines), return up to 3 hex
    colors by pixel share."""
    import io
    from PIL import Image
    image = Image.open(io.BytesIO(logo_bytes)).convert("RGB").resize((64, 64))
    counts = {}
    for count, rgb in image.getcolors(64 * 64):
        r, g, b = rgb
        if max(r, g, b) > 245 or max(r, g, b) < 25:  # near-white / near-black
            continue
        counts[rgb] = counts.get(rgb, 0) + count
    # Quantize similar shades together (32-step buckets) so anti-aliasing
    # doesn't fragment one brand color into dozens of entries. Each bucket
    # keeps its heaviest exact shade as the representative color.
    buckets = {}  # bucket key -> [representative rgb, rep count, bucket total]
    for (r, g, b), count in counts.items():
        key = (r // 32, g // 32, b // 32)
        bucket = buckets.setdefault(key, [(r, g, b), count, 0])
        if count > bucket[1]:
            bucket[0], bucket[1] = (r, g, b), count
        bucket[2] += count
    top = sorted(buckets.values(), key=lambda bucket: -bucket[2])[:3]
    return ["#{:02X}{:02X}{:02X}".format(*rgb) for rgb, _, _ in top]


def apply_brand_identity(client_id: int, logo_url: str = "", industry_hint: str = "") -> dict:
    """THE brand-identity step for every site build/edit — and the extension
    point future logo/media-generation agents plug into (they call this same
    function with their generated logo URL; nothing else changes).

    Logo present → analyze it (dominant colors) and that palette drives the
    site. No logo → NEVER block and NEVER ask the client design questions
    (standing rule: zero design questionnaire — industry/tone already live in
    the sales-chat data): fall back to the neutral-by-industry palette.

    v1 records the decision (activity log + return value) for content/site
    work to consume; automated theme re-skinning from the palette is the
    deferred half — see the website skill."""
    palette, source = None, ""
    if logo_url:
        try:
            palette = _extract_logo_palette(wp.fetch_bytes(logo_url))
            source = "logo"
        except Exception as e:
            log_step(AGENT_NAME, "apply_brand_identity",
                     f"client {client_id}: logo analysis failed ({e}) — using neutral palette")
    if not palette:
        key = (industry_hint or "").strip().lower()
        palette = list(NEUTRAL_PALETTES.get(key, NEUTRAL_PALETTES["default"]))
        source = f"neutral_{key or 'default'}"

    _log_activity(client_id, "website_brand_identity",
                  {"source": source, "palette": palette, "logo_url": logo_url})
    log_step(AGENT_NAME, "apply_brand_identity", f"client {client_id}: {source} {palette}")
    return {"success": True, "source": source, "palette": palette}


# ─── Phase 2: provision a NEW site (InstaWP, admin-triggered only) ────────────

def provision_site(client_id: int, site_name: str = "",
                   logo_url: str = "", industry_hint: str = "") -> dict:
    """Spin up a real WordPress site for a client who has none: clone the
    uallak master template on InstaWP (reserved = billable — the hosting cost
    passthrough in PRICING exists because of this call), then rotate the
    template's baked-in Application Password for a per-site one and store the
    connection exactly like a Phase-1 connect. From that point every Phase-1
    tool (publish/update/SEO) works on the new site unchanged."""
    from core import instawp_service as iwp

    template_slug = os.environ.get("WEBSITE_TEMPLATE_SLUG", "")
    template_user = os.environ.get("WEBSITE_TEMPLATE_WP_USERNAME", "uallak")
    template_app_password = os.environ.get("WEBSITE_TEMPLATE_APP_PASSWORD", "")
    if not template_slug or not template_app_password:
        return {"success": False,
                "errors": ["WEBSITE_TEMPLATE_SLUG / WEBSITE_TEMPLATE_APP_PASSWORD not configured "
                           "— create the master template on InstaWP first (see website skill)"]}
    if is_connected(client_id):
        # Provisioning would orphan a paid site or clobber a live connection —
        # disconnect deliberately first if a rebuild is really wanted
        return {"success": False, "errors": ["client already has a connected website"]}

    log_step(AGENT_NAME, "provision_site", f"client {client_id} ({site_name or 'auto-named'})")
    site = {}
    try:
        site = timed_step(
            AGENT_NAME, "provision_create",
            lambda: iwp.create_site_from_template(template_slug, site_name))
        if site.get("task_id") and not site.get("is_pool"):
            timed_step(AGENT_NAME, "provision_wait",
                       lambda: iwp.wait_until_ready(site["task_id"]))
        site_url = wp.normalize_site_url(site["wp_url"])

        # Rotate: every clone inherits the template's shared Application
        # Password — mint a per-site one, store it, then revoke everything else
        minted = wp.create_application_password(
            site_url, template_user, template_app_password,
            name=f"uallak-client-{client_id}")
        per_site_password = minted["password"]
        for existing in wp.list_application_passwords(site_url, template_user,
                                                      per_site_password):
            if existing.get("uuid") != minted.get("uuid"):
                wp.delete_application_password(site_url, template_user,
                                               per_site_password, existing["uuid"])
    except Exception as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: provisioning failed: {e}"])
        # A reserved site bills until deleted — don't leave a half-provisioned orphan
        if site.get("id"):
            try:
                iwp.delete_site(site["id"])
            except Exception as cleanup_error:
                agent_alert(AGENT_NAME,
                            [f"client {client_id}: cleanup of InstaWP site "
                             f"{site['id']} ALSO failed ({cleanup_error}) — delete it "
                             "manually in the InstaWP dashboard or it keeps billing"])
        return {"success": False, "errors": [str(e)]}

    from agents.client_agent import upsert_account
    upsert_account(client_id, WORDPRESS_PLATFORM, site_url,
                   f"{template_user}:{per_site_password}", "active")
    _overview_cache.pop(client_id, None)
    _log_activity(client_id, "website_provisioned",
                  {"site_url": site_url, "provider": "instawp",
                   "site_id": site.get("id")})
    log_step(AGENT_NAME, "provision_site", f"client {client_id}: live at {site_url}")

    # Standing rules kick in immediately on every new site: plugins + page
    # structure (verifies the template kept its shape) and brand identity
    # (logo analysis or the neutral-by-industry fallback). Both are
    # best-effort here — the site IS provisioned; problems alert, not abort.
    result = {"success": True, "site_url": site_url, "site_id": site.get("id")}
    try:
        result["standards"] = run_standards_check(client_id)
        result["brand"] = apply_brand_identity(client_id, logo_url, industry_hint)
    except Exception as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: post-provision standards/brand "
                                 f"step failed on {site_url}: {e}"])
    return result


def populate_site(client_id: int, items: list) -> dict:
    """Fill a (freshly provisioned) site with the setup package's initial
    content — pages and the initial article batch. Items are ALREADY-GENERATED
    publish specs (same shape publish_content takes); this just pipes them
    through Phase 1, so drafts-by-default and per-item alerting apply as-is."""
    log_step(AGENT_NAME, "populate_site", f"client {client_id}: {len(items)} items")
    summary = {"success": True, "created": [], "failed": 0, "errors": []}
    for item in items:
        result = publish_content(client_id, item)
        if result.get("success"):
            summary["created"].append({"id": result["id"], "kind": result["kind"],
                                       "status": result["status"]})
        else:
            summary["failed"] += 1
            summary["errors"].append({"title": (item or {}).get("title", ""),
                                      "errors": result.get("errors", [])})
    if summary["failed"]:
        summary["success"] = False
    _log_activity(client_id, "website_populated",
                  {"requested": len(items), "created": len(summary["created"]),
                   "failed": summary["failed"]})
    log_step(AGENT_NAME, "populate_site",
             f"client {client_id}: {len(summary['created'])} created, "
             f"{summary['failed']} failed")
    return summary


# ─── Daily health scan ────────────────────────────────────────────────────────

def _issue_already_alerted(client_id: int, issue_key: str) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ISSUE_DEDUP_DAYS)).isoformat()
    result = (
        _db().table("client_activity")
        .select("id")
        .eq("client_id", client_id)
        .eq("agent_name", AGENT_NAME)
        .eq("action_type", "website_issue_detected")
        .eq("details->>issue_key", issue_key)
        .gte("created_at", cutoff)
        .limit(1)
        .execute()
    )
    return bool(result.data)


def run_health_scan() -> dict:
    """Daily: verify every connected site is reachable and the stored
    Application Password still works (they're revocable in wp-admin). Alerts
    with the same dedup idea as the ads scans."""
    log_step(AGENT_NAME, "health_scan", "starting")
    rows = (
        _db().table("client_accounts").select("*")
        .eq("platform", WORDPRESS_PLATFORM).eq("status", "active")
        .execute().data or []
    )
    summary = {"sites_scanned": 0, "issues": 0}
    for connection in rows:
        client_id = connection["client_id"]
        site_url, username, app_password = _creds(connection)
        summary["sites_scanned"] += 1
        issue = ""
        try:
            wp.get_current_user(site_url, username, app_password)
        except wp.WordPressError as e:
            issue = (f"credentials rejected — client must reconnect ({e})"
                     if wp.is_auth_error(e) else f"REST error: {e}")
        except Exception as e:
            issue = f"site unreachable: {e}"
        if not issue:
            continue
        summary["issues"] += 1
        issue_key = "website_auth" if "reconnect" in issue else "website_down"
        if _issue_already_alerted(client_id, issue_key):
            continue
        _log_activity(client_id, "website_issue_detected",
                      {"issue_key": issue_key, "site_url": site_url, "issue": issue})
        agent_alert(AGENT_NAME, [f"client {client_id}: {site_url}: {issue}"])
    log_step(AGENT_NAME, "health_scan",
             f"done — {summary['sites_scanned']} sites, {summary['issues']} issues")
    return summary
