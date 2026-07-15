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


def upload_media_from_url(site_url: str, username: str, app_password: str,
                          media_url: str, filename: str = "") -> dict:
    """Fetch a PUBLIC media URL and upload the bytes to the WP media library
    (WP has no fetch-by-URL endpoint, unlike Meta — we do the fetch)."""
    fetched = httpx.get(media_url, timeout=MEDIA_FETCH_TIMEOUT, follow_redirects=True)
    if fetched.status_code != 200:
        raise WordPressError(f"media fetch failed: {fetched.status_code} for {media_url}")
    if len(fetched.content) > MAX_MEDIA_BYTES:
        raise WordPressError(
            f"media too large ({len(fetched.content)} bytes, cap {MAX_MEDIA_BYTES})")

    name = filename or media_url.split("?")[0].rstrip("/").split("/")[-1] or "upload"
    headers = _headers(username, app_password)
    headers["Content-Disposition"] = f'attachment; filename="{name}"'
    headers["Content-Type"] = fetched.headers.get("content-type", "application/octet-stream")
    response = httpx.post(f"{site_url}/wp-json/wp/v2/media", headers=headers,
                          content=fetched.content, timeout=MEDIA_FETCH_TIMEOUT,
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
