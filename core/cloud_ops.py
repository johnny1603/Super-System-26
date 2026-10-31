"""Cloud Run + Cloud Logging, read by the operator agent (core/operator_agent,
docs/OPERATOR.md) — and the ONE place that can move traffic between revisions.

Auth is the service's own runtime service account, through the metadata
server (no key file, no new dependency). What that account may do is set once
by scripts/setup_operator.sh: logging.viewer, and a custom role with revision
reads + services.update. Locally (no metadata server) every call raises
CloudOpsUnavailable("no_metadata") — the operator says so instead of guessing.

Safety:
- A revision or service object is NEVER returned as-is: it carries the
  container env (secret values set as plain env would be in it). Only the
  fields picked in _revision_view / serving() leave this module.
- Log text passes through core/redaction before anyone sees it.
- Traffic moves only to a READY revision of this service (rollback), or back
  to "latest" (restore). Traffic-only updates never create a revision, never
  touch the template, never touch env or secrets.
"""
import os
import re
import time
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "cloud_ops"
PROJECT = os.environ.get("GCP_PROJECT", "super-system-500410")
REGION = os.environ.get("CLOUD_RUN_REGION", "me-west1")
SERVICE = os.environ.get("K_SERVICE") or "super-system"

METADATA_TOKEN_URL = ("http://metadata.google.internal/computeMetadata/v1/"
                      "instance/service-accounts/default/token")
RUN_API = "https://run.googleapis.com/v2"
LOGGING_API = "https://logging.googleapis.com/v2/entries:list"
TIMEOUT = 20

# After a deploy, how long the operator keeps watching it (and offering the
# one-tap rollback banner when it looks wrong)
WATCH_HOURS = 3
ERROR_THRESHOLD = 5           # ERROR+ log entries on the new revision = "looks wrong"
LOG_TEXT_MAX = 600
LOG_LIMIT_MAX = 100
SEVERITIES = ("DEFAULT", "INFO", "WARNING", "ERROR", "CRITICAL")
# Free text for a log filter: letters (Hebrew too), digits and a few
# punctuation marks — never a quote, so it can't break out of the filter
_SAFE_TEXT = re.compile(r"^[\w\s\-.:/@#א-ת]{1,80}$")

BUILD_COMMIT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "BUILD_COMMIT")

_token = {"value": "", "exp": 0.0}


class CloudOpsUnavailable(Exception):
    """No metadata server / no permission / API error. `code` is stable."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _requests():
    import requests
    return requests


def _now():
    return datetime.now(timezone.utc)


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _access_token() -> str:
    if _token["value"] and _token["exp"] > time.time() + 60:
        return _token["value"]
    try:
        r = _requests().get(METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"}, timeout=3)
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        raise CloudOpsUnavailable("no_metadata", type(e).__name__)
    _token.update(value=body.get("access_token", ""), exp=time.time() + int(body.get("expires_in", 0)))
    return _token["value"]


def _call(method: str, url: str, **kwargs) -> dict:
    headers = {"Authorization": f"Bearer {_access_token()}"}
    try:
        r = _requests().request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
    except Exception as e:
        raise CloudOpsUnavailable("network", type(e).__name__)
    if r.status_code == 403:
        raise CloudOpsUnavailable("permission_denied",
                                  "the runtime service account lacks this permission — scripts/setup_operator.sh")
    if r.status_code >= 400:
        try:
            message = str((r.json().get("error") or {}).get("message", ""))[:200]
        except Exception:
            message = ""
        raise CloudOpsUnavailable(f"http_{r.status_code}", message)
    return r.json() if r.content else {}


def _service_path() -> str:
    return f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}"


def _short(name: str) -> str:
    return str(name or "").rsplit("/", 1)[-1]


def running_commit() -> str:
    """The commit THIS instance was built from. Written by the deploy
    workflow only; a manual Cloud Shell deploy has no file → "" (unknown)."""
    try:
        with open(BUILD_COMMIT_FILE, encoding="utf-8") as f:
            value = f.read().strip()
        return value if re.fullmatch(r"[0-9a-f]{40}", value) else ""
    except OSError:
        return ""


def _revision_view(rev: dict) -> dict:
    """The only fields of a revision that ever leave this module."""
    conditions = rev.get("conditions") or []
    ready = any(c.get("type") == "Ready" and c.get("state") == "CONDITION_SUCCEEDED" for c in conditions)
    failed = [str(c.get("message", ""))[:200] for c in conditions if c.get("state") == "CONDITION_FAILED"]
    return {"name": _short(rev.get("name")), "created_at": rev.get("createTime"), "ready": ready,
            "failure": failed[0] if failed else ""}


def _service() -> dict:
    return _call("GET", f"{RUN_API}/{_service_path()}")


def serving(service: dict = None) -> dict:
    """Which revision gets the traffic now. pinned = traffic is routed to a
    named revision (after a rollback), not to "latest"."""
    service = service if service is not None else _service()
    statuses = service.get("trafficStatuses") or []
    latest = _short(service.get("latestReadyRevision"))
    main = max(statuses, key=lambda s: s.get("percent", 0), default={})
    pinned = main.get("type") == "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
    return {"revision": _short(main.get("revision")) if pinned else latest,
            "percent": main.get("percent", 100 if not statuses else 0),
            "pinned": pinned, "latest_ready": latest,
            "url": service.get("uri", "")}


def revisions(limit: int = 10) -> list:
    """Newest first, with deploy time and readiness."""
    body = _call("GET", f"{RUN_API}/{_service_path()}/revisions", params={"pageSize": max(1, min(int(limit), 50))})
    rows = [_revision_view(r) for r in body.get("revisions") or []]
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows[:limit]


def previous_revision(rows: list = None, current: str = None) -> dict:
    """The newest READY revision created before the one serving now — the
    rollback target. {} when there is none."""
    rows = rows if rows is not None else revisions(20)
    current = current or serving().get("revision")
    names = [r["name"] for r in rows]
    if current not in names:
        return {}
    for r in rows[names.index(current) + 1:]:
        if r.get("ready"):
            return r
    return {}


def status() -> dict:
    """Everything the operator page's status card shows, in one read."""
    service = _service()
    now_serving = serving(service)
    rows = revisions(10)
    current = next((r for r in rows if r["name"] == now_serving["revision"]), {})
    return {"service": SERVICE, "region": REGION, "serving": now_serving,
            "serving_since": current.get("created_at"),
            "running_commit": running_commit(),
            "previous": previous_revision(rows, now_serving["revision"]),
            "revisions": rows}


def _wait_for(revision_or_latest: str, seconds: int = 25) -> dict:
    deadline = time.time() + seconds
    now_serving = serving()
    while time.time() < deadline:
        if (revision_or_latest == "latest" and not now_serving["pinned"]) or \
                (revision_or_latest != "latest" and now_serving["revision"] == revision_or_latest):
            return {"done": True, "serving": now_serving}
        time.sleep(2)
        now_serving = serving()
    return {"done": False, "serving": now_serving}


def route_to(revision: str) -> dict:
    """ROLLBACK: 100% of traffic to a named, READY revision of this service."""
    rows = revisions(30)
    target = next((r for r in rows if r["name"] == revision), None)
    if not target:
        raise CloudOpsUnavailable("unknown_revision", revision)
    if not target["ready"]:
        raise CloudOpsUnavailable("revision_not_ready", revision)
    before = serving()
    _call("PATCH", f"{RUN_API}/{_service_path()}", params={"updateMask": "traffic"},
          json={"traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                             "revision": revision, "percent": 100}]})
    return {"from": before["revision"], "to": revision, **_wait_for(revision)}


def route_to_latest() -> dict:
    """RESTORE (the undo of a rollback): traffic follows the latest revision again."""
    before = serving()
    _call("PATCH", f"{RUN_API}/{_service_path()}", params={"updateMask": "traffic"},
          json={"traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}]})
    return {"from": before["revision"], "to": "latest", **_wait_for("latest")}


def log_filter(minutes: int = 60, severity: str = "WARNING", text: str = "", revision: str = "") -> str:
    minutes = max(1, min(int(minutes or 60), 7 * 24 * 60))
    since = (_now() - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    severity = severity if severity in SEVERITIES else "WARNING"
    parts = ['resource.type="cloud_run_revision"', f'resource.labels.service_name="{SERVICE}"',
             f'timestamp>="{since}"']
    if severity != "DEFAULT":
        # 5xx request logs are ERROR already; print() lines are DEFAULT and only
        # appear when severity DEFAULT is asked for
        parts.append(f"severity>={severity}")
    if revision:
        if not re.fullmatch(r"[a-z0-9-]{1,80}", revision):
            raise CloudOpsUnavailable("bad_revision", "")
        parts.append(f'resource.labels.revision_name="{revision}"')
    if text:
        if not _SAFE_TEXT.match(text):
            raise CloudOpsUnavailable("bad_text", "letters, digits and - . : / @ # only")
        parts.append(f'"{text}"')
    return " AND ".join(parts)


def _entry_text(e: dict) -> str:
    from core.redaction import redact
    if e.get("textPayload"):
        text = e["textPayload"]
    elif isinstance(e.get("jsonPayload"), dict):
        payload = e["jsonPayload"]
        text = str(payload.get("message") or payload.get("msg") or payload)
    elif isinstance(e.get("httpRequest"), dict):
        h = e["httpRequest"]
        # Path only — a query string can carry codes or tokens
        url = str(h.get("requestUrl", "")).split("?", 1)[0]
        text = f'{h.get("requestMethod", "")} {url} → {h.get("status", "")} ({h.get("latency", "")})'
    else:
        text = ""
    return redact(text)[:LOG_TEXT_MAX]


def logs(minutes: int = 60, severity: str = "WARNING", text: str = "", revision: str = "",
         limit: int = 40) -> list:
    body = _call("POST", LOGGING_API, json={
        "resourceNames": [f"projects/{PROJECT}"],
        "filter": log_filter(minutes, severity, text, revision),
        "orderBy": "timestamp desc",
        "pageSize": max(1, min(int(limit or 40), LOG_LIMIT_MAX)),
    })
    return [{"at": e.get("timestamp"), "severity": e.get("severity", "DEFAULT"),
             "revision": (e.get("resource") or {}).get("labels", {}).get("revision_name", ""),
             "text": _entry_text(e)} for e in body.get("entries") or []]


def deploy_watch(state: dict = None) -> dict:
    """Is the revision serving now a recent deploy, and does it look wrong?
    suspicious → the page shows the rollback banner and the agent offers it."""
    state = state or status()
    since = _parse(state.get("serving_since"))
    recent = bool(since and _now() - since < timedelta(hours=WATCH_HOURS))
    out = {"recent_deploy": recent, "revision": state["serving"]["revision"],
           "deployed_at": state.get("serving_since"), "errors": None, "suspicious": False,
           "rollback_to": (state.get("previous") or {}).get("name", "")}
    if not recent:
        return out
    minutes = int((_now() - since).total_seconds() // 60) + 1
    errors = logs(minutes=minutes, severity="ERROR", revision=out["revision"], limit=LOG_LIMIT_MAX)
    out["errors"] = len(errors)
    out["suspicious"] = len(errors) >= ERROR_THRESHOLD and bool(out["rollback_to"])
    out["sample"] = errors[:3]
    return out
