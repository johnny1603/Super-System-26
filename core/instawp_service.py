"""uallak's InstaWP service — HTTP layer for provisioning NEW WordPress sites
(website agent Phase 2). Editing/publishing on any WordPress site — provisioned
or client-owned — stays in core/wordpress_service.py; this file only talks to
the InstaWP control-plane API.

Auth: one workspace-level API token (INSTAWP_API_KEY in keys_agent KEYS),
Bearer header. Per-site WP credentials come back from provisioning and are
immediately rotated by the agent — nothing InstaWP-specific is stored per
client.

Provisioning model: sites are cloned from the uallak MASTER TEMPLATE (a
one-time manually prepared site: Hebrew/RTL theme, base pages, the uallak
admin user with a known Application Password). `is_reserved=True` makes the
clone a permanent billable site — this is the moment real money starts
(per-site plan, ~$5/mo Starter), so provision_site is admin-triggered only,
never reachable from a client-facing flow.

Creation is async when the site isn't served from InstaWP's warm pool: the
create response carries task_id and wait_until_ready polls
/tasks/{id}/status until it completes.
"""
import time

import httpx

from agents.keys_agent import get_key

API_BASE = "https://app.instawp.io/api/v2"
TIMEOUT = 60
PROVISION_POLL_SECONDS = 5
PROVISION_POLL_MAX_TRIES = 60  # ~5 minutes — template clones are usually much faster


class InstaWPError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def _headers() -> dict:
    return {
        "authorization": f"Bearer {get_key('INSTAWP_API_KEY')}",
        "accept": "application/json",
        "content-type": "application/json",
    }


def _request(method: str, path: str, json_body: dict = None):
    """InstaWP wraps responses in {status, message, data} — unwrap data,
    surface message on errors."""
    response = httpx.request(method, f"{API_BASE}/{path.lstrip('/')}",
                             headers=_headers(), json=json_body, timeout=TIMEOUT)
    try:
        body = response.json()
    except Exception:
        body = {}
    if response.status_code >= 400 or body.get("status") is False:
        raise InstaWPError(
            f"{response.status_code}: {body.get('message') or response.text[:300]}",
            status_code=response.status_code,
        )
    return body.get("data") if isinstance(body, dict) and "data" in body else body


def create_site_from_template(template_slug: str, site_name: str = "") -> dict:
    """Clone the master template into a PERMANENT (reserved = billable) site.
    Response data carries wp_url, wp_username, wp_password, s_hash, and —
    when not pool-served — task_id to poll."""
    payload = {"template_slug": template_slug, "is_reserved": True}
    if site_name:
        payload["site_name"] = site_name
    return _request("POST", "sites/template", payload)


def get_task_status(task_id: str) -> dict:
    return _request("GET", f"tasks/{task_id}/status")


def wait_until_ready(task_id: str) -> dict:
    """Poll the provisioning task until it leaves 'progress'. Raises on
    timeout or failure — the caller cleans up."""
    for _ in range(PROVISION_POLL_MAX_TRIES):
        status = get_task_status(task_id)
        state = str(status.get("status", "")).lower()
        if state in ("completed", "complete", "success", "done"):
            return status
        if state in ("failed", "error"):
            raise InstaWPError(f"provisioning task {task_id} failed: {status}")
        time.sleep(PROVISION_POLL_SECONDS)
    raise InstaWPError(f"provisioning task {task_id} timed out "
                       f"(~{PROVISION_POLL_SECONDS * PROVISION_POLL_MAX_TRIES}s)")


def delete_site(site_id) -> dict:
    """Cleanup path for failed provisioning ONLY — a reserved site bills until
    deleted. Never wired to any client-facing flow."""
    return _request("DELETE", f"sites/{site_id}")
