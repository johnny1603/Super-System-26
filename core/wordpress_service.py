"""uallak's WordPress service — the HTTP layer for agents/website_agent.py.

HTTP only, mirroring core/meta_service.py: REST primitives, error extraction,
and thin wrappers over the WordPress core REST API (wp/v2). Business logic
lives in the agent.

Auth model — no OAuth. WordPress core has Application Passwords (built in
since WP 5.6): the client generates a 24-character per-app password in
wp-admin → Users → Profile → Application Passwords, and every request carries
HTTP Basic auth (username:app_password) over HTTPS. Revocable per-app on the
WordPress side, zero infrastructure on ours. Callers pass site_url + username
+ app_password explicitly — this module never touches the DB.

Costs: the core REST API is free on any self-hosted WordPress. The only paid
thing this file can trigger is nothing — plugin installs pull free plugins
from wordpress.org (paid plugins/themes are a client-billed decision and are
NOT installed through here).
"""
import base64
import re

import httpx

TIMEOUT = 30
# Media uploads fetch the file server-side first (same "public URL" model as
# the Meta content agent) — cap it so a huge video can't blow up memory.
MEDIA_FETCH_TIMEOUT = 60
MAX_MEDIA_BYTES = 10 * 1024 * 1024

# WP REST namespaces that identify an installed SEO plugin (site root index
# lists namespaces without auth — cheapest possible detection).
SEO_PLUGIN_NAMESPACES = {
    "yoast/v1": "yoast",
    "rankmath/v1": "rank_math",
}

# wordpress.org slug → plugin file id the /wp/v2/plugins endpoint returns.
# Yoast is the default install target: biggest ecosystem, free tier is enough
# for meta title/description work.
DEFAULT_SEO_PLUGIN_SLUG = "wordpress-seo"

# Israeli-standard-5568-oriented accessibility plugins, both free on
# wordpress.org, tried in order (standing rule: every site we build/manage
# gets one — same auto-install pattern as Yoast).
ACCESSIBILITY_PLUGIN_SLUGS = ("pojo-accessibility", "wp-accessibility-helper")


class WordPressError(RuntimeError):
    """REST error with the HTTP status and WP error code attached, so callers
    can tell dead credentials (client must reconnect) from a plain bad request."""

    def __init__(self, message, status_code=None, wp_code=None):
        super().__init__(message)
        self.status_code = status_code
        self.wp_code = wp_code


def is_auth_error(exc) -> bool:
    return isinstance(exc, WordPressError) and exc.status_code in (401, 403)


def normalize_site_url(raw: str) -> str:
    """'mysite.co.il/' → 'https://mysite.co.il' — stored as the account_id and
    used as the base for every call, so it must be canonical."""
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise ValueError("empty site url")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


# ─── Anonymous detection (no credentials — runs BEFORE a client connects) ─────
#
# Everything else in this file authenticates as the client. This section is the
# opposite: an ordinary visitor's view of a site we have no access to, used to
# answer one question — "is this WordPress?" — so a client never has to know or
# declare it themselves.
#
# Confidence matters more than the boolean. A wrong "yes" sends a client down a
# migration path that cannot work; a wrong "no" only offers a rebuild they can
# decline. So the caller is told HOW sure we are, and anything short of certain
# must not be treated as a migration green light (see website_agent).

DETECT_TIMEOUT = 12
DETECT_MAX_HTML_BYTES = 200_000  # enough for <head> + nav; a homepage can be huge

_GENERATOR_RE = r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']WordPress\s*([0-9.]*)'
_TITLE_RE = r"<title[^>]*>(.*?)</title>"


def _probe_get(url: str, **kwargs):
    """Anonymous GET that never raises — an unreachable site is a FINDING
    ("we couldn't see it"), not an exception to handle at every call site."""
    try:
        return httpx.get(url, timeout=DETECT_TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": "uallak-site-check/1.0"}, **kwargs)
    except Exception:
        return None


def detect_wordpress(site_url: str) -> dict:
    """Is this public site WordPress? Anonymous, read-only, no credentials.

    Returns {"reachable", "is_wordpress", "confidence", "signals", "wp_version",
    "site_title", "rest_api_open"}. NEVER raises.

    confidence:
      certain  — the WP REST API answered with the wp/v2 namespace. Nothing
                 else produces that.
      likely   — a generator meta tag, the api.w.org link relation, or
                 wp-content/wp-includes asset paths in the HTML.
      unlikely — the page loaded and showed none of those.
      unknown  — we could not load the site at all (DNS, TLS, firewall,
                 bot-blocking). NOT the same as "not WordPress", and callers
                 must not collapse the two.
    """
    result = {"reachable": False, "is_wordpress": False, "confidence": "unknown",
              "signals": [], "wp_version": "", "site_title": "", "rest_api_open": False}
    try:
        site_url = normalize_site_url(site_url)
    except ValueError:
        result["signals"].append("invalid url")
        return result
    result["site_url"] = site_url

    # 1. The REST root. A WordPress site answers with a namespaces array; this
    #    is the only signal that cannot be faked by a theme copying WP markup.
    rest = _probe_get(f"{site_url}/wp-json/")
    if rest is not None and rest.status_code == 200:
        result["reachable"] = True
        try:
            body = rest.json()
        except Exception:
            body = {}
        if isinstance(body, dict) and "wp/v2" in (body.get("namespaces") or []):
            result.update({
                "is_wordpress": True, "confidence": "certain", "rest_api_open": True,
                "site_title": str(body.get("name") or "")[:200],
            })
            result["signals"].append("wp-json exposes the wp/v2 namespace")
            return result

    # 2. The homepage HTML. Several independent tells, any one of which is
    #    strong; a site can disable the REST API and still be WordPress.
    home = _probe_get(site_url)
    if home is None or home.status_code >= 400:
        if not result["reachable"]:
            result["signals"].append("site could not be loaded")
            return result
        result["confidence"] = "unlikely"
        return result

    result["reachable"] = True
    html = (home.text or "")[:DETECT_MAX_HTML_BYTES]
    lowered = html.lower()

    title = re.search(_TITLE_RE, html, re.I | re.S)
    if title:
        result["site_title"] = re.sub(r"\s+", " ", title.group(1)).strip()[:200]

    generator = re.search(_GENERATOR_RE, html, re.I)
    if generator:
        result["signals"].append("generator meta tag says WordPress")
        result["wp_version"] = (generator.group(1) or "").strip()
    if "api.w.org" in lowered or "api.w.org" in (home.headers.get("link") or "").lower():
        result["signals"].append("api.w.org link relation present")
    if "/wp-content/" in lowered or "/wp-includes/" in lowered:
        result["signals"].append("wp-content / wp-includes asset paths")
    if "wp-json" in lowered:
        result["signals"].append("wp-json reference in the page")

    if result["signals"]:
        result["is_wordpress"] = True
        result["confidence"] = "likely"
    else:
        result["confidence"] = "unlikely"
    return result


def public_page_summary(site_url: str) -> dict:
    """What a visitor can see on the homepage, as REFERENCE material for a
    rebuild: title, meta description, and the visible headings.

    Deliberately shallow and public-only — this is "what is this business's
    site about", never an attempt to extract content for a 1:1 copy (which is
    exactly what the rebuild path promises NOT to do). Never raises."""
    summary = {"available": False, "title": "", "description": "", "headings": []}
    try:
        site_url = normalize_site_url(site_url)
    except ValueError:
        return summary
    home = _probe_get(site_url)
    if home is None or home.status_code >= 400:
        return summary

    html = (home.text or "")[:DETECT_MAX_HTML_BYTES]
    summary["available"] = True

    title = re.search(_TITLE_RE, html, re.I | re.S)
    if title:
        summary["title"] = re.sub(r"\s+", " ", title.group(1)).strip()[:200]
    description = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I | re.S)
    if description:
        summary["description"] = re.sub(r"\s+", " ", description.group(1)).strip()[:400]

    headings = re.findall(r"<h[12][^>]*>(.*?)</h[12]>", html, re.I | re.S)
    cleaned = []
    for heading in headings:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", heading)).strip()
        if text and text not in cleaned:
            cleaned.append(text[:120])
    summary["headings"] = cleaned[:15]
    return summary


def _headers(username: str, app_password: str) -> dict:
    token = base64.b64encode(f"{username}:{app_password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _raise_rest_error(response: httpx.Response):
    """WP REST errors carry {code, message} — surface both, keep the status."""
    try:
        body = response.json()
        raise WordPressError(
            f"{response.status_code} {body.get('code', '')}: {body.get('message', '')}",
            status_code=response.status_code,
            wp_code=body.get("code"),
        )
    except WordPressError:
        raise
    except Exception:
        raise WordPressError(f"{response.status_code}: {response.text[:300]}",
                             status_code=response.status_code)


def rest_get(site_url: str, path: str, username: str, app_password: str,
             params: dict = None):
    """GET {site_url}/wp-json/{path} with Basic auth."""
    response = httpx.get(f"{site_url}/wp-json/{path.lstrip('/')}",
                         headers=_headers(username, app_password),
                         params=params or {}, timeout=TIMEOUT,
                         follow_redirects=True)
    if response.status_code >= 400:
        _raise_rest_error(response)
    return response.json()


def rest_post(site_url: str, path: str, username: str, app_password: str,
              data: dict = None):
    """POST JSON to {site_url}/wp-json/{path}. WP returns 200 or 201 on success."""
    response = httpx.post(f"{site_url}/wp-json/{path.lstrip('/')}",
                          headers=_headers(username, app_password),
                          json=data or {}, timeout=TIMEOUT,
                          follow_redirects=True)
    if response.status_code >= 400:
        _raise_rest_error(response)
    return response.json()


def rest_delete(site_url: str, path: str, username: str, app_password: str,
                params: dict = None):
    response = httpx.delete(f"{site_url}/wp-json/{path.lstrip('/')}",
                            headers=_headers(username, app_password),
                            params=params or {}, timeout=TIMEOUT,
                            follow_redirects=True)
    if response.status_code >= 400:
        _raise_rest_error(response)
    return response.json()


# ─── Site + user ──────────────────────────────────────────────────────────────

def get_site_info(site_url: str, username: str, app_password: str) -> dict:
    """Root index: site name/description + REST namespaces (SEO plugin tell)."""
    info = rest_get(site_url, "", username, app_password,
                    params={"_fields": "name,description,url,namespaces"})
    namespaces = info.get("namespaces") or []
    info["seo_plugin"] = next(
        (label for ns, label in SEO_PLUGIN_NAMESPACES.items() if ns in namespaces), None)
    return info


def get_current_user(site_url: str, username: str, app_password: str) -> dict:
    """Validates the credentials AND reveals what we're allowed to do —
    context=edit includes the capabilities map (edit_pages, install_plugins...)."""
    return rest_get(site_url, "wp/v2/users/me", username, app_password,
                    params={"context": "edit",
                            "_fields": "id,name,capabilities"})


# ─── Application Passwords (used by provisioning's credential rotation) ──────

def create_application_password(site_url: str, username: str, app_password: str,
                                name: str) -> dict:
    """Mint a NEW Application Password for the authenticated user. The
    response's 'password' field is the plaintext — WP shows it exactly once,
    so the caller must store it immediately."""
    return rest_post(site_url, "wp/v2/users/me/application-passwords",
                     username, app_password, data={"name": name})


def list_application_passwords(site_url: str, username: str, app_password: str) -> list:
    return rest_get(site_url, "wp/v2/users/me/application-passwords",
                    username, app_password)


def delete_application_password(site_url: str, username: str, app_password: str,
                                uuid: str) -> dict:
    return rest_delete(site_url, f"wp/v2/users/me/application-passwords/{uuid}",
                       username, app_password)


# ─── Posts + pages (content_type: 'post' → wp/v2/posts, 'page' → wp/v2/pages) ─

def _collection(content_type: str) -> str:
    if content_type not in ("post", "page"):
        raise ValueError(f"content_type must be 'post' or 'page', got '{content_type}'")
    return f"wp/v2/{content_type}s"


def list_content(site_url: str, username: str, app_password: str,
                 content_type: str = "post", limit: int = 10) -> list:
    return rest_get(
        site_url, _collection(content_type), username, app_password,
        params={"per_page": limit, "status": "publish,draft,pending",
                "_fields": "id,title,status,link,slug,modified"})


def list_content_for_audit(site_url: str, username: str, app_password: str,
                           content_type: str = "post", limit: int = 30) -> list:
    """Like list_content but with the body + excerpt included — the SEO
    agent's content audit needs word counts and excerpt presence, which the
    slim _fields list deliberately omits for the overview path."""
    return rest_get(
        site_url, _collection(content_type), username, app_password,
        params={"per_page": limit, "status": "publish,draft,pending",
                "_fields": "id,title,status,link,slug,date,modified,excerpt,content"})


def get_content(site_url: str, username: str, app_password: str,
                content_type: str, content_id: int) -> dict:
    return rest_get(site_url, f"{_collection(content_type)}/{content_id}",
                    username, app_password, params={"context": "edit"})


def create_content(site_url: str, username: str, app_password: str,
                   content_type: str, fields: dict) -> dict:
    return rest_post(site_url, _collection(content_type),
                     username, app_password, data=fields)


def update_content(site_url: str, username: str, app_password: str,
                   content_type: str, content_id: int, fields: dict) -> dict:
    return rest_post(site_url, f"{_collection(content_type)}/{content_id}",
                     username, app_password, data=fields)


# ─── Media ────────────────────────────────────────────────────────────────────

def update_media(site_url: str, username: str, app_password: str,
                 media_id: int, fields: dict) -> dict:
    """alt_text lives here (core field) — the cheapest real SEO fix WP offers."""
    return rest_post(site_url, f"wp/v2/media/{media_id}",
                     username, app_password, data=fields)


def list_media(site_url: str, username: str, app_password: str,
               limit: int = 20) -> list:
    """Recent media with alt_text — feeds the standards check's
    missing-alt-text sample."""
    return rest_get(site_url, "wp/v2/media", username, app_password,
                    params={"per_page": limit,
                            "_fields": "id,alt_text,source_url,mime_type"})


def fetch_bytes(url: str) -> bytes:
    """Generic public-URL fetch with the media size cap — used for media
    uploads and for logo analysis (apply_brand_identity)."""
    fetched = httpx.get(url, timeout=MEDIA_FETCH_TIMEOUT, follow_redirects=True)
    if fetched.status_code != 200:
        raise WordPressError(f"fetch failed: {fetched.status_code} for {url}")
    if len(fetched.content) > MAX_MEDIA_BYTES:
        raise WordPressError(f"file too large ({len(fetched.content)} bytes, cap {MAX_MEDIA_BYTES})")
    return fetched.content


def fetch_public_html(url: str, max_bytes: int = 800_000) -> str:
    """Any page on the site as an ANONYMOUS visitor receives it — no auth
    header, so what comes back is what the public actually gets. This is the
    only honest way to verify an injection: a 2xx on the REST write proves the
    row was saved, not that the markup survived wp_kses or that the theme
    renders it. Capped because we only ever scan for a marker string."""
    response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (uallak site audit)"})
    if response.status_code != 200:
        raise WordPressError(f"page fetch failed: {response.status_code} for {url}")
    return response.text[:max_bytes]


def fetch_homepage_html(site_url: str, max_bytes: int = 800_000) -> str:
    """The site's rendered homepage HTML, unauthenticated — what a real
    visitor (and every tracking tag) actually gets. Used by the tracking-tag
    audit; capped because we only need the <head>/inline scripts, not a
    media-heavy body."""
    return fetch_public_html(site_url, max_bytes)


# ─── Widgets (WP 5.8+ core REST — the tracking-snippet injection path) ───────

def list_sidebars(site_url: str, username: str, app_password: str) -> list:
    """Registered widget areas (wp/v2/sidebars). Block (FSE) themes may
    register none — callers must treat an empty list as 'no injection path
    on this theme', not an error."""
    result = rest_get(site_url, "wp/v2/sidebars", username, app_password)
    return result if isinstance(result, list) else []


def add_custom_html_widget(site_url: str, username: str, app_password: str,
                           sidebar_id: str, content: str, title: str = "") -> dict:
    """Create a Custom HTML widget carrying `content` in the given sidebar
    (wp/v2/widgets, core since 5.8). IMPORTANT: <script> tags survive only
    when the authenticated user has the `unfiltered_html` capability —
    single-site ADMINS have it, Editors don't (WP strips script/iframe
    silently). Callers must VERIFY the tag actually renders afterwards
    (re-fetch the homepage) instead of trusting this call's 2xx."""
    return rest_post(site_url, "wp/v2/widgets", username, app_password, data={
        "id_base": "custom_html",
        "sidebar": sidebar_id,
        "instance": {"raw": {"title": title, "content": content}},
    })


# Standing rule: images on sites we build/manage are served as WebP (lightest
# widely-supported format — page weight is an SEO ranking factor). Only these
# static raster types get converted; GIFs (animation), SVGs, and video pass
# through untouched.
WEBP_CONVERTIBLE_TYPES = ("image/jpeg", "image/png", "image/bmp", "image/tiff")
WEBP_QUALITY = 82


def _to_webp(content: bytes, content_type: str, name: str):
    """(bytes, content_type, filename) with WebP conversion applied when the
    input is a convertible raster image. Falls back to the original on ANY
    failure (missing Pillow, corrupt image) — a slightly heavier image beats
    a failed publish."""
    if content_type.split(";")[0].strip().lower() not in WEBP_CONVERTIBLE_TYPES:
        return content, content_type, name
    try:
        import io
        from PIL import Image
        image = Image.open(io.BytesIO(content))
        if image.mode not in ("RGB", "RGBA"):  # palette/CMYK/etc. -> safe modes
            image = image.convert("RGBA" if "A" in image.mode else "RGB")
        out = io.BytesIO()
        image.save(out, format="WEBP", quality=WEBP_QUALITY)
        stem = name.rsplit(".", 1)[0] or "upload"
        return out.getvalue(), "image/webp", f"{stem}.webp"
    except Exception as e:
        print(f"[wordpress_service] WebP conversion failed, uploading original: {e}")
        return content, content_type, name


def upload_media_from_url(site_url: str, username: str, app_password: str,
                          media_url: str, filename: str = "") -> dict:
    """Fetch a PUBLIC media URL and upload the bytes to the WP media library
    (WP has no fetch-by-URL endpoint, unlike Meta — we do the fetch).
    JPEG/PNG images are converted to WebP on the way (standing site rule)."""
    fetched = httpx.get(media_url, timeout=MEDIA_FETCH_TIMEOUT, follow_redirects=True)
    if fetched.status_code != 200:
        raise WordPressError(f"media fetch failed: {fetched.status_code} for {media_url}")
    if len(fetched.content) > MAX_MEDIA_BYTES:
        raise WordPressError(
            f"media too large ({len(fetched.content)} bytes, cap {MAX_MEDIA_BYTES})")

    name = filename or media_url.split("?")[0].rstrip("/").split("/")[-1] or "upload"
    content, content_type, name = _to_webp(
        fetched.content, fetched.headers.get("content-type", "application/octet-stream"), name)

    headers = _headers(username, app_password)
    headers["Content-Disposition"] = f'attachment; filename="{name}"'
    headers["Content-Type"] = content_type
    response = httpx.post(f"{site_url}/wp-json/wp/v2/media", headers=headers,
                          content=content, timeout=MEDIA_FETCH_TIMEOUT,
                          follow_redirects=True)
    if response.status_code >= 400:
        _raise_rest_error(response)
    return response.json()


# ─── Plugins (core endpoint since WP 5.5; needs install_plugins capability) ───

def list_plugins(site_url: str, username: str, app_password: str) -> list:
    return rest_get(site_url, "wp/v2/plugins", username, app_password,
                    params={"_fields": "plugin,status,name"})


def install_plugin(site_url: str, username: str, app_password: str,
                   slug: str, activate: bool = True) -> dict:
    """Install a FREE plugin from the wordpress.org repo by slug (and activate
    in the same call). Paid plugins can't be installed this way — by design:
    a paid license is a client-billed decision, never an automatic install."""
    data = {"slug": slug}
    if activate:
        data["status"] = "active"
    return rest_post(site_url, "wp/v2/plugins", username, app_password, data=data)


def set_plugin_status(site_url: str, username: str, app_password: str,
                      plugin: str, status: str) -> dict:
    """plugin is the id from list_plugins (e.g. 'wordpress-seo/wp-seo');
    status: 'active' | 'inactive'."""
    if status not in ("active", "inactive"):
        raise ValueError(f"Invalid plugin status: {status}")
    return rest_post(site_url, f"wp/v2/plugins/{plugin}",
                     username, app_password, data={"status": status})
