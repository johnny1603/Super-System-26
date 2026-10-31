"""GitHub REST for the operator agent (docs/OPERATOR.md): history and test
results to READ (Tier 1), and — for Tier 3 only, each step behind the owner's
approval + emailed code (core/operator_gate) — its OWN branch, a pull
request, a merge bound to the approved head SHA, and the deploy workflow.

Token: OPERATOR_GITHUB_TOKEN, a fine-grained token for this one repository
(Contents R/W, Pull requests R/W, Actions R/W, Metadata R, Variables R and
Environments R for setup_check) and deliberately
WITHOUT the Workflows permission — so it cannot change .github/workflows even
if everything else failed. The token never appears in a return value, an
error or a log line.

There is no function here that writes to master: commits go to a new
`operator/...` ref only (create_branch_commit refuses any other name), and
master changes only through merge_pr, which GitHub runs against the exact
head SHA the owner approved.
"""
import base64
import os
import re

SERVICE_NAME = "operator_github"
API = "https://api.github.com"
REPO = os.environ.get("OPERATOR_GITHUB_REPO", "johnny1603/Super-System-26")
BASE_BRANCH = "master"
BRANCH_PREFIX = "operator/"
DEPLOY_WORKFLOW = "deploy.yml"
TESTS_WORKFLOW = "tests.yml"
TIMEOUT = 20
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


class GitHubError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _token() -> str:
    return os.environ.get("OPERATOR_GITHUB_TOKEN", "").strip()


def configured() -> bool:
    return bool(_token())


def _call(method: str, path: str, **kwargs):
    if not configured():
        raise GitHubError("not_configured", "OPERATOR_GITHUB_TOKEN is not set")
    import requests
    headers = {"Authorization": f"Bearer {_token()}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    try:
        r = requests.request(method, f"{API}{path}", headers=headers, timeout=TIMEOUT, **kwargs)
    except Exception as e:
        raise GitHubError("network", type(e).__name__)
    if r.status_code >= 400:
        try:
            message = str(r.json().get("message", ""))[:200]
        except Exception:
            message = ""
        raise GitHubError(f"http_{r.status_code}", message)
    return r.json() if r.content else {}


def _repo(path: str = "") -> str:
    return f"/repos/{REPO}{path}"


def _check_sha(sha: str) -> str:
    sha = str(sha or "").strip().lower()
    if not _SHA.match(sha):
        raise GitHubError("bad_sha", "")
    return sha


# ─── Tier 1: reads ───────────────────────────────────────────────────────────

def recent_commits(n: int = 15, branch: str = BASE_BRANCH) -> list:
    rows = _call("GET", _repo("/commits"), params={"sha": branch, "per_page": max(1, min(int(n), 50))})
    return [{"sha": c["sha"][:12], "message": str((c.get("commit") or {}).get("message", "")).split("\n", 1)[0][:200],
             "author": ((c.get("commit") or {}).get("author") or {}).get("name", ""),
             "date": ((c.get("commit") or {}).get("author") or {}).get("date", "")} for c in rows]


def commit_details(sha: str) -> dict:
    c = _call("GET", _repo(f"/commits/{_check_sha(sha)}"))
    return {"sha": c.get("sha", "")[:12],
            "message": str((c.get("commit") or {}).get("message", ""))[:1000],
            "date": ((c.get("commit") or {}).get("author") or {}).get("date", ""),
            "files": [{"path": f.get("filename"), "status": f.get("status"),
                       "additions": f.get("additions"), "deletions": f.get("deletions"),
                       "patch": str(f.get("patch") or "")[:3000]} for f in (c.get("files") or [])[:30]]}


def _run_view(r: dict) -> dict:
    return {"id": r.get("id"), "workflow": r.get("name"), "branch": r.get("head_branch"),
            "sha": str(r.get("head_sha", ""))[:12], "event": r.get("event"),
            "status": r.get("status"), "conclusion": r.get("conclusion"),
            "created_at": r.get("created_at"), "url": r.get("html_url")}


def workflow_runs(n: int = 10, workflow: str = None) -> list:
    path = _repo(f"/actions/workflows/{workflow}/runs") if workflow else _repo("/actions/runs")
    body = _call("GET", path, params={"per_page": max(1, min(int(n), 30))})
    return [_run_view(r) for r in body.get("workflow_runs") or []]


def checks(sha: str) -> dict:
    """Test results for one commit: passed / failed / pending / none."""
    body = _call("GET", _repo(f"/commits/{_check_sha(sha)}/check-runs"), params={"per_page": 50})
    runs = body.get("check_runs") or []
    if not runs:
        return {"state": "none", "runs": []}
    views = [{"name": r.get("name"), "status": r.get("status"), "conclusion": r.get("conclusion"),
              "url": r.get("html_url")} for r in runs]
    if any(r["status"] != "completed" for r in views):
        state = "pending"
    elif all(r["conclusion"] in ("success", "skipped", "neutral") for r in views):
        state = "passed"
    else:
        state = "failed"
    return {"state": state, "runs": views}


def head_sha(branch: str = BASE_BRANCH) -> str:
    return _call("GET", _repo(f"/git/ref/heads/{branch}"))["object"]["sha"]


def file_at(path: str, ref: str) -> str:
    """The text of one file at a ref, or None when it doesn't exist there."""
    try:
        body = _call("GET", _repo(f"/contents/{path}"), params={"ref": ref})
    except GitHubError as e:
        if e.code == "http_404":
            return None
        raise
    if isinstance(body, list) or body.get("type") != "file":
        raise GitHubError("not_a_file", path)
    return base64.b64decode(body.get("content", "")).decode("utf-8")


# ─── Setup check (Tier 2 action "github_check", read-only) ───────────────────
# What Tier 3 needs from GitHub, one line each. States only — never a value:
# the two Variables are reported as set / missing, not echoed.
REQUIRED_VARIABLES = ("GCP_WIF_PROVIDER", "GCP_DEPLOY_SA")
DEPLOY_ENVIRONMENT = "production"


def _probe(path: str):
    """(body, state): state 'ok' | 'missing' | 'no_permission' | an error code."""
    try:
        return _call("GET", path), "ok"
    except GitHubError as e:
        if e.code == "http_404":
            return None, "missing"
        if e.code in ("http_401", "http_403"):
            return None, "no_permission"
        return None, e.code


def setup_check() -> dict:
    items = {}
    if not configured():
        return {"ok": False, "items": {"token": {"ok": False, "state": "not_configured"}}}
    repo, state = _probe(_repo())
    items["repo_access"] = {"ok": state == "ok", "state": state}
    if state != "ok":
        return {"ok": False, "items": items}
    for name in REQUIRED_VARIABLES:
        _, state = _probe(_repo(f"/actions/variables/{name}"))
        items[f"variable_{name}"] = {"ok": state == "ok", "state": "set" if state == "ok" else state}
    _, state = _probe(_repo(f"/actions/workflows/{DEPLOY_WORKFLOW}"))
    items["deploy_workflow"] = {"ok": state == "ok", "state": state}
    env, state = _probe(_repo(f"/environments/{DEPLOY_ENVIRONMENT}"))
    reviewers = 0
    for rule in (env or {}).get("protection_rules") or []:
        if rule.get("type") == "required_reviewers":
            reviewers += len(rule.get("reviewers") or [])
    items["environment_production"] = {"ok": state == "ok", "state": state}
    items["required_reviewer"] = {"ok": reviewers > 0 if state == "ok" else None,
                                  "state": f"{reviewers} reviewer(s)" if state == "ok" else state,
                                  "recommended": True}
    branch, state = _probe(_repo(f"/branches/{BASE_BRANCH}"))
    protected = bool((branch or {}).get("protected"))
    items["master_protected"] = {"ok": protected if state == "ok" else None,
                                 "state": ("protected" if protected else "not_protected") if state == "ok" else state,
                                 "recommended": True}
    required = [k for k, v in items.items() if not v.get("recommended")]
    return {"ok": all(items[k]["ok"] for k in required), "items": items}


# ─── Tier 3: writes (each call site is behind operator_gate) ─────────────────

def create_branch_commit(branch: str, base_sha: str, files: dict, message: str) -> str:
    """ONE commit with `files` ({path: new text}) on a NEW branch from base_sha.
    Only an operator/... branch, never an existing one (the ref create fails
    if it exists), never master."""
    if not branch.startswith(BRANCH_PREFIX) or not re.fullmatch(r"operator/[a-z0-9-]{3,60}", branch):
        raise GitHubError("bad_branch", branch)
    base_sha = _check_sha(base_sha)
    base_tree = _call("GET", _repo(f"/git/commits/{base_sha}"))["tree"]["sha"]
    tree = _call("POST", _repo("/git/trees"), json={
        "base_tree": base_tree,
        "tree": [{"path": p, "mode": "100644", "type": "blob", "content": text} for p, text in files.items()]})
    commit = _call("POST", _repo("/git/commits"), json={"message": message, "tree": tree["sha"],
                                                        "parents": [base_sha]})
    _call("POST", _repo("/git/refs"), json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    return commit["sha"]


def open_pr(branch: str, title: str, body: str) -> dict:
    pr = _call("POST", _repo("/pulls"), json={"title": title, "head": branch, "base": BASE_BRANCH,
                                              "body": body, "maintainer_can_modify": False})
    return {"number": pr["number"], "url": pr.get("html_url"), "head_sha": pr["head"]["sha"]}


def pr_state(number: int) -> dict:
    pr = _call("GET", _repo(f"/pulls/{int(number)}"))
    return {"number": pr["number"], "state": pr.get("state"), "merged": bool(pr.get("merged")),
            "head_sha": pr["head"]["sha"], "head_ref": pr["head"]["ref"],
            "base_ref": pr["base"]["ref"], "mergeable": pr.get("mergeable"),
            "merge_sha": pr.get("merge_commit_sha") if pr.get("merged") else None,
            "url": pr.get("html_url")}


def merge_pr(number: int, head: str, title: str) -> str:
    """Squash-merge — ONLY if the PR head is still exactly `head` (GitHub
    refuses with 409 otherwise), so what merges is what was approved."""
    body = _call("PUT", _repo(f"/pulls/{int(number)}/merge"),
                 json={"sha": _check_sha(head), "merge_method": "squash", "commit_title": title[:120]})
    if not body.get("merged"):
        raise GitHubError("not_merged", str(body.get("message", ""))[:200])
    return body["sha"]


def dispatch_deploy(sha: str):
    """Start the deploy workflow for one master commit. The workflow itself
    checks the commit is on master and runs the tests first."""
    _call("POST", _repo(f"/actions/workflows/{DEPLOY_WORKFLOW}/dispatches"),
          json={"ref": BASE_BRANCH, "inputs": {"sha": _check_sha(sha)}})


def deploy_run_for(sha: str, after_iso: str = "") -> dict:
    """The deploy run started for this commit (by its display title input),
    newest first; {} until GitHub has created it."""
    sha = _check_sha(sha)
    for r in _call("GET", _repo(f"/actions/workflows/{DEPLOY_WORKFLOW}/runs"),
                   params={"per_page": 10, "event": "workflow_dispatch"}).get("workflow_runs") or []:
        if after_iso and str(r.get("created_at", "")) < after_iso[:19]:
            continue
        if sha[:12] in str(r.get("display_title", "")) or str(r.get("head_sha", "")).startswith(sha):
            return _run_view(r)
    return {}
