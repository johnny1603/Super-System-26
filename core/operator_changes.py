"""Tier 3 of the operator (docs/OPERATOR.md): CODE AND DEPLOY, never automatic.

The lifecycle of one change, each arrow a separate owner approval:

  proposed ──(approve + emailed code)──▶ pr_open ──(approve + NEW code)──▶ deploying ──▶ deployed | deploy_failed

- propose(): the agent's edits are applied IN CODE to the current master
  text (each `old` must occur exactly once), checked against PROTECTED, and
  turned into a unified diff. Nothing is written anywhere but our own row.
- open_pr(): gate code for ("open_pr", change id) → re-applies the edits to
  master as it is NOW (refuses if master moved under them) → ONE commit on a
  new operator/<id> branch → pull request. Never master.
- merge_and_deploy(): gate code for ("merge_deploy", "<id>:<PR head sha>") →
  the PR must still be open, still at that exact head, and its tests not
  failed/pending → squash-merge bound to that SHA → the deploy workflow for
  the merge commit (which re-checks it is on master and re-runs the tests).

What the operator may never touch (PROTECTED): the CI/deploy workflows, the
gate and this lifecycle, sessions, the secrets vault, the keys registry,
env/secret files — and no edit may contain SECRETS_ENCRYPTION_KEY or
anything that looks like a secret value. Those changes are the owner's own,
made the normal way.

State: one app_settings row per change (`operator_change:<id>`). Every step
is audited (core/operator_actions.audit) before it has any outside effect.
"""
import difflib
import re
import secrets
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "operator_changes"
PREFIX = "operator_change:"
MAX_FILES = 8
MAX_FILE_CHARS = 400_000
MAX_EDIT_CHARS = 30_000
DIFF_MAX_CHARS = 60_000

PROTECTED_PREFIXES = (".github/", ".git/", "migrations/")
PROTECTED_FILES = {
    "core/operator_gate.py", "core/operator_changes.py", "core/operator_github.py", "core/operator_actions.py",
    "core/operator_agent.py", "core/operator_flags.py", "core/cloud_ops.py", "core/email_service.py",
    "core/session.py", "core/secrets_vault.py", "agents/keys_agent.py", "config/secrets.py",
    "Dockerfile", ".dockerignore", ".gitignore", "requirements.txt",
    "scripts/setup_operator.sh", "scripts/run_tests.py",
}
_PROTECTED_NAME = re.compile(r"(^|/)\.env|\.(pem|key|p12|pfx)$|secret|credential|service[-_]?account", re.I)
FORBIDDEN_TEXT = ("SECRETS_ENCRYPTION_KEY",)
_PATH = re.compile(r"^[A-Za-z0-9_\-./]{1,200}$")


class ChangeError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── What may be changed ─────────────────────────────────────────────────────

def protected_reason(path: str) -> str:
    """"" when the operator may edit `path`, else why not."""
    if not _PATH.match(path or "") or path.startswith("/") or ".." in path.split("/") or "//" in path:
        return "bad_path"
    if path.startswith(PROTECTED_PREFIXES):
        return "protected_area"
    if path in PROTECTED_FILES:
        return "protected_file"
    if _PROTECTED_NAME.search(path):
        return "secret_like_path"
    return ""


def text_reason(text: str) -> str:
    from core.redaction import looks_like_secret
    for word in FORBIDDEN_TEXT:
        if word in text:
            return "mentions_" + word.lower()
    for line in text.splitlines():
        if looks_like_secret(line):
            return "secret_like_value"
    return ""


def apply_edits(originals: dict, edits: list) -> dict:
    """{path: new text}. originals: {path: current text or None (missing)}.
    An edit {path, old, new}: `old` must occur EXACTLY once; old == "" on a
    missing file creates it."""
    out = dict(originals)
    for i, e in enumerate(edits):
        path, old, new = e["path"], e["old"], e["new"]
        current = out.get(path)
        if current is None:
            if old:
                raise ChangeError("ERR_EDIT_FILE_MISSING", path)
            out[path] = new
            continue
        if not old:
            raise ChangeError("ERR_EDIT_EMPTY_OLD", path)
        count = current.count(old)
        if count != 1:
            raise ChangeError("ERR_EDIT_NOT_UNIQUE" if count else "ERR_EDIT_NOT_FOUND", f"{path} (edit {i + 1})")
        out[path] = current.replace(old, new, 1)
    return out


def validate_edits(edits) -> list:
    if not isinstance(edits, list) or not edits:
        raise ChangeError("ERR_NO_EDITS")
    clean, files = [], set()
    for e in edits:
        if not isinstance(e, dict):
            raise ChangeError("ERR_EDIT_SHAPE")
        path = str(e.get("path") or "").strip()
        old, new = str(e.get("old") or ""), str(e.get("new") or "")
        reason = protected_reason(path)
        if reason:
            raise ChangeError("ERR_PROTECTED_PATH", f"{path}: {reason}")
        if len(old) > MAX_EDIT_CHARS or len(new) > MAX_EDIT_CHARS:
            raise ChangeError("ERR_EDIT_TOO_LARGE", path)
        reason = text_reason(new)
        if reason:
            raise ChangeError("ERR_FORBIDDEN_CONTENT", f"{path}: {reason}")
        files.add(path)
        clean.append({"path": path, "old": old, "new": new})
    if len(files) > MAX_FILES:
        raise ChangeError("ERR_TOO_MANY_FILES")
    return clean


def unified_diff(originals: dict, updated: dict) -> str:
    chunks = []
    for path in sorted(updated):
        before = (originals.get(path) or "").splitlines(keepends=True)
        after = (updated[path] or "").splitlines(keepends=True)
        chunks.extend(difflib.unified_diff(before, after, f"a/{path}", f"b/{path}"))
    text = "".join(chunks)
    return text if len(text) <= DIFF_MAX_CHARS else text[:DIFF_MAX_CHARS] + "\n… (diff truncated — full diff in the PR)\n"


def _fetch(paths, ref: str) -> dict:
    from core import operator_github as gh
    originals = {}
    for path in sorted(set(paths)):
        text = gh.file_at(path, ref)
        if text is not None and len(text) > MAX_FILE_CHARS:
            raise ChangeError("ERR_FILE_TOO_LARGE", path)
        originals[path] = text
    return originals


# ─── Storage ─────────────────────────────────────────────────────────────────

def _save(change: dict):
    change["updated_at"] = _now()
    _db().table("app_settings").upsert({"key": PREFIX + change["id"], "value": change,
                                        "updated_at": change["updated_at"]}, on_conflict="key").execute()


def get(change_id: str) -> dict:
    if not re.fullmatch(r"[a-z0-9]{8,16}", str(change_id or "")):
        raise ChangeError("ERR_CHANGE_UNKNOWN")
    rows = (_db().table("app_settings").select("value").eq("key", PREFIX + change_id).limit(1)
            .execute().data or [])
    value = rows[0].get("value") if rows else None
    if not isinstance(value, dict):
        raise ChangeError("ERR_CHANGE_UNKNOWN")
    return value


def public(change: dict) -> dict:
    """What the page shows (the edits themselves stay server-side; the diff is the view)."""
    keys = ("id", "title", "what_he", "why_he", "status", "files", "diff", "base_sha", "branch",
            "pr_number", "pr_url", "head_sha", "merge_sha", "deploy_run", "created_at", "updated_at", "error")
    return {k: change.get(k) for k in keys}


# ─── The three steps ─────────────────────────────────────────────────────────

def propose(title: str, what_he: str, why_he: str, edits) -> dict:
    from core import operator_github as gh
    if not gh.configured():
        raise ChangeError("ERR_GITHUB_NOT_CONFIGURED")
    title = re.sub(r"[\r\n\"]", " ", str(title or "")).strip()[:100]
    if not title:
        raise ChangeError("ERR_NO_TITLE")
    edits = validate_edits(edits)
    base = gh.head_sha()
    originals = _fetch((e["path"] for e in edits), base)
    updated = apply_edits(originals, edits)
    change = {"id": secrets.token_hex(5), "title": title, "what_he": str(what_he or "")[:3000],
              "why_he": str(why_he or "")[:3000], "edits": edits, "files": sorted(updated),
              "base_sha": base, "diff": unified_diff(originals, updated), "status": "proposed",
              "created_at": _now()}
    _save(change)
    return public(change)


def code_summary(change: dict, purpose: str) -> str:
    """The sentence the approval email names (what this code approves)."""
    if purpose == "open_pr":
        return (f"פתיחת ענף ו-PR לשינוי #{change['id']} — \"{change['title']}\" "
                f"({len(change['files'])} קבצים: {', '.join(change['files'])[:300]})")
    return (f"מיזוג PR #{change.get('pr_number')} ופריסה לייצור — שינוי #{change['id']} "
            f"\"{change['title']}\" (גרסה {str(change.get('head_sha', ''))[:7]})")


def gate_subject(change: dict, purpose: str) -> str:
    if purpose == "open_pr":
        return change["id"]
    return f"{change['id']}:{change.get('head_sha', '')}"


def expected_status(purpose: str) -> str:
    return "proposed" if purpose == "open_pr" else "pr_open"


def request_code(change_id: str, purpose: str, admin_fp: str, who: str) -> dict:
    """Email the one-time code for the NEXT step of this change."""
    from core import operator_actions, operator_gate
    change = get(change_id)
    if purpose not in operator_gate.PURPOSES or change["status"] != expected_status(purpose):
        raise ChangeError("ERR_WRONG_STATE", change["status"])
    operator_actions.audit(f"tier3 code requested for {purpose} change {change['id']}", who)
    return operator_gate.request_code(purpose, gate_subject(change, purpose), admin_fp,
                                      code_summary(change, purpose))


def _gate(change: dict, purpose: str, gate_id: str, code: str, admin_fp: str, who: str):
    """The second gate, checked HERE (a protected file) — not in the route,
    so no change to api_server can ever skip it."""
    from core import operator_actions, operator_gate
    if change["status"] != expected_status(purpose):
        raise ChangeError("ERR_WRONG_STATE", change["status"])
    try:
        operator_gate.verify(gate_id, code, purpose, gate_subject(change, purpose), admin_fp)
    except operator_gate.GateError as e:
        try:
            operator_actions.audit(f"tier3 {purpose} DENIED {e.code} change {change['id']}", who)
        except operator_actions.AuditUnavailable:
            pass
        raise ChangeError(e.code)


def open_pr(change_id: str, gate_id: str, code: str, admin_fp: str, who: str) -> dict:
    from core import operator_actions, operator_github as gh
    change = get(change_id)
    _gate(change, "open_pr", gate_id, code, admin_fp, who)
    base = gh.head_sha()
    originals = _fetch(change["files"], base)
    try:
        updated = apply_edits(originals, change["edits"])
    except ChangeError as e:
        raise ChangeError("ERR_MASTER_MOVED", e.detail)
    branch = f"{gh.BRANCH_PREFIX}{change['id']}"
    operator_actions.audit(f"tier3 open_pr START change {change['id']} branch {branch}", who)
    head = gh.create_branch_commit(branch, base, updated, f"Operator: {change['title']}")
    body = (f"**מה השתנה:** {change['what_he']}\n\n**למה:** {change['why_he']}\n\n"
            f"נוצר על ידי הסוכן התפעולי (שינוי #{change['id']}). מיזוג ופריסה — רק אחרי אישור נפרד "
            f"וקוד חד-פעמי במייל.")
    pr = gh.open_pr(branch, f"Operator: {change['title']}", body)
    change.update(status="pr_open", branch=branch, base_sha=base, pr_number=pr["number"], pr_url=pr["url"],
                  head_sha=pr["head_sha"] or head, diff=unified_diff(originals, updated))
    _save(change)
    operator_actions.audit(f"tier3 open_pr OK change {change['id']} PR #{pr['number']} head {change['head_sha'][:12]}", who)
    return public(change)


def merge_and_deploy(change_id: str, gate_id: str, code: str, admin_fp: str, who: str) -> dict:
    from core import operator_actions, operator_github as gh
    change = get(change_id)
    _gate(change, "merge_deploy", gate_id, code, admin_fp, who)
    pr = gh.pr_state(change["pr_number"])
    if pr["state"] != "open" or pr["merged"]:
        raise ChangeError("ERR_PR_NOT_OPEN")
    if pr["head_sha"] != change["head_sha"]:
        raise ChangeError("ERR_PR_CHANGED")       # someone pushed to the branch after approval was asked
    if pr["base_ref"] != gh.BASE_BRANCH or not pr["head_ref"].startswith(gh.BRANCH_PREFIX):
        raise ChangeError("ERR_PR_SHAPE")
    tests = gh.checks(change["head_sha"])
    if tests["state"] in ("failed", "pending"):
        raise ChangeError("ERR_TESTS_" + tests["state"].upper())
    operator_actions.audit(f"tier3 merge_deploy START change {change['id']} PR #{change['pr_number']} "
                           f"head {change['head_sha'][:12]} tests {tests['state']}", who)
    merge_sha = gh.merge_pr(change["pr_number"], change["head_sha"], f"Operator: {change['title']}")
    change.update(status="merged", merge_sha=merge_sha)
    _save(change)
    dispatched_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    try:
        gh.dispatch_deploy(merge_sha)
    except gh.GitHubError as e:
        change.update(status="deploy_failed", error=f"dispatch: {e.code}")
        _save(change)
        operator_actions.audit(f"tier3 merge_deploy MERGED but deploy not started ({e.code}) change {change['id']}", who)
        raise ChangeError("ERR_DEPLOY_NOT_STARTED", e.code)
    change.update(status="deploying", dispatched_at=dispatched_at)
    _save(change)
    operator_actions.audit(f"tier3 merge_deploy OK change {change['id']} merged {merge_sha[:12]}, deploy started", who)
    return public(change)


def refresh_deploy(change_id: str) -> dict:
    """Polls the deploy run for a deploying change and settles its status."""
    from core import operator_github as gh
    change = get(change_id)
    if change["status"] != "deploying":
        return public(change)
    run = gh.deploy_run_for(change["merge_sha"], change.get("dispatched_at", ""))
    if run:
        change["deploy_run"] = run
        if run.get("status") == "completed":
            change["status"] = "deployed" if run.get("conclusion") == "success" else "deploy_failed"
        _save(change)
    return public(change)


def recent(limit: int = 10) -> list:
    rows = (_db().table("app_settings").select("value").like("key", PREFIX + "%")
            .order("updated_at", desc=True).limit(int(limit)).execute().data or [])
    return [public(r["value"]) for r in rows if isinstance(r.get("value"), dict)]
