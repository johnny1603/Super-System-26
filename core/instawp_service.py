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


# ─── Site versions + remote commands (core/site_updates, 2026-11-02) ─────────
# Paths and bodies as InstaWP's own open-source CLI calls them
# (github.com/InstaWP/cli, CLAUDE.md + src/commands/versions.ts +
# src/lib/api-exec.ts): a "version" is a restorable point-in-time copy of the
# site's files AND database, restored in place. Never run against a live site
# by us yet — see docs/SITE-UPDATES.md.

TASK_POLL_SECONDS = 3
TASK_MAX_SECONDS = 600            # the CLI's own ceiling for versions create/restore


def wait_for_task(task_id, max_seconds: int = TASK_MAX_SECONDS, sleep=time.sleep) -> dict:
    """Poll tasks/{id}/status until 'completed'. Raises InstaWPError on
    'error'/'failed' or timeout. A failed status READ is retried (the CLI
    does the same) — only the task's own answer decides."""
    waited = 0
    while waited <= max_seconds:
        try:
            status = get_task_status(task_id) or {}
        except InstaWPError:
            status = {}
        state = str(status.get("status", "")).lower()
        if state == "completed":
            return status
        if state in ("error", "failed"):
            raise InstaWPError(f"task {task_id} {state}: {str(status.get('comment') or '')[:200]}")
        sleep(TASK_POLL_SECONDS)
        waited += TASK_POLL_SECONDS
    raise InstaWPError(f"task {task_id} timed out after {max_seconds}s")


def create_site_version(site_id) -> dict:
    """{"id", "task_id"} — the copy is usable only once its task completes."""
    data = _request("POST", "site-versions", {"site_id": site_id}) or {}
    if not data.get("id"):
        raise InstaWPError("site version not created (no id in the answer)")
    return {"id": data["id"], "task_id": data.get("task_id")}


def name_site_version(version_id, name: str):
    """Best effort, like the CLI (a version without a name is still a version)."""
    try:
        _request("PUT", f"site-versions/{version_id}", {"name": str(name)[:25]})
    except InstaWPError:
        pass


def list_site_versions(site_id) -> list:
    data = _request("GET", f"site-versions?site_id={site_id}")
    if isinstance(data, dict):
        data = data.get("data") or data.get("versions") or []
    return data if isinstance(data, list) else []


def restore_site_version(site_id, version_id) -> dict:
    """OVERWRITES the site's current files + database with the version."""
    data = _request("PUT", f"sites/{site_id}/restore-versions/{version_id}") or {}
    return {"task_id": data.get("task_id")}


def run_cmd(site_id, command: str, timeout_seconds: int = 120) -> dict:
    """{"output", "exit_code"} of ONE command on the site (WP-CLI included),
    request/response only. The caller builds the command from validated
    parts only — never pass text that came from a client or a model."""
    response = httpx.post(f"{API_BASE}/sites/{site_id}/run-cmd", headers=_headers(),
                          json={"commands": [command], "timeout_seconds": int(timeout_seconds)},
                          timeout=max(30, int(timeout_seconds) + 20))
    try:
        body = response.json()
    except Exception:
        body = {}
    if response.status_code >= 400 or (isinstance(body, dict) and body.get("status") is False):
        raise InstaWPError(f"{response.status_code}: {(body or {}).get('message') or response.text[:300]}",
                           status_code=response.status_code)
    return normalize_run_cmd(body.get("data") if isinstance(body, dict) else body)


def normalize_run_cmd(data) -> dict:
    """The three answer shapes the CLI accepts (list / string / object), and
    the timestamp echo line run-cmd prepends, removed."""
    if isinstance(data, list):
        data = data[0] if data else ""
    if isinstance(data, dict):
        output, code = data.get("output"), data.get("exit_code", 0)
    else:
        output, code = data, 0
    if output is None:
        output = ""
    if not isinstance(output, str):
        import json
        output = json.dumps(output)
    lines = output.split("\n")
    if lines and len(lines[0]) >= 10 and lines[0][:4].isdigit() and lines[0][4] == "-" and lines[0][7] == "-":
        lines = lines[1:]
    try:
        code = int(code or 0)
    except (TypeError, ValueError):
        code = 1
    return {"output": "\n".join(lines).strip(), "exit_code": code}
