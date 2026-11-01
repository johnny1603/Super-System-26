"""Read-only audit of the project's Cloud Scheduler jobs (stdlib only).

Called by `bash scripts/schedule_jobs.sh --audit`, which feeds it ONE line per
job on stdin, tab-separated, with only these fields (gcloud never hands the
job's headers to this process, so the admin key cannot reach the output):

    name  schedule  uri  http_method  state  status_code  last_attempt_time

For every job it prints: name, schedule, target path, HTTP method, last
attempt status, whether scripts/schedule_jobs.sh manages it, and a flag when
the job's HTTP method is not the one core/api_server.py registers for that
path (the lead-volume-scan POST-vs-GET 405), when the route does not exist,
or when a managed job drifted from its definition in the script.

Everything printed goes through mask() first.
"""
import os
import re
import shlex
import sys
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "schedule_jobs.sh")
API = os.path.join(ROOT, "core", "api_server.py")
MASK = "****"

_ROUTE = re.compile(r'@app\.(get|post|put|patch|delete)\(\s*"([^"]+)"')

# google.rpc.Code of the last attempt (Cloud Scheduler maps the HTTP answer
# onto these: 401 -> UNAUTHENTICATED, 405/501 -> UNIMPLEMENTED, ...)
STATUS_NAMES = {
    "0": "OK", "1": "CANCELLED", "2": "UNKNOWN", "3": "INVALID_ARGUMENT",
    "4": "DEADLINE_EXCEEDED", "5": "NOT_FOUND", "6": "ALREADY_EXISTS",
    "7": "PERMISSION_DENIED", "8": "RESOURCE_EXHAUSTED", "9": "FAILED_PRECONDITION",
    "10": "ABORTED", "11": "OUT_OF_RANGE", "12": "UNIMPLEMENTED", "13": "INTERNAL",
    "14": "UNAVAILABLE", "15": "DATA_LOSS", "16": "UNAUTHENTICATED",
}


# ─── masking ─────────────────────────────────────────────────────────────────

_HEADER_VALUE = re.compile(r"(?i)(x-admin-key|authorization|api[-_]?key|token|secret|password)"
                           r"([\"']?\s*[=:]\s*[\"']?(?:bearer\s+)?)([^\s,;&\"'}]+)")
_QUERY_VALUE = re.compile(r"(?i)([?&](?:key|token|secret|sig|signature|password|api_key|apikey)=)[^&#\s]+")


def mask(text: str, secrets=()) -> str:
    """Never let a credential through: known values first, then anything
    shaped like a key/token assignment or a credential in a query string."""
    out = str(text or "")
    for s in secrets:
        if s:
            out = out.replace(s, MASK)
    out = _HEADER_VALUE.sub(lambda m: m.group(1) + m.group(2) + MASK, out)
    out = _QUERY_VALUE.sub(lambda m: m.group(1) + MASK, out)
    return out


# ─── routes registered by core/api_server.py ─────────────────────────────────

def parse_routes(api_src: str) -> list:
    """[(METHOD, path_template)] for every @app.<method>("...") decorator."""
    return [(m.group(1).upper(), m.group(2)) for m in _ROUTE.finditer(api_src)]


def _template_regex(template: str):
    parts = []
    for piece in re.split(r"(\{[^}]+\})", template):
        if piece.startswith("{") and piece.endswith("}"):
            parts.append(".+" if piece.endswith(":path}") else "[^/]+")
        else:
            parts.append(re.escape(piece))
    return re.compile("^" + "".join(parts) + "$")


def route_methods(path: str, routes: list) -> set:
    """The HTTP methods the server registers for this concrete path. A literal
    route wins over a templated one, as in FastAPI's registration order for
    the paths we use."""
    path = path.rstrip("/") or "/"
    exact = {m for m, t in routes if (t.rstrip("/") or "/") == path}
    if exact:
        return exact
    return {m for m, t in routes if "{" in t and _template_regex(t.rstrip("/") or "/").match(path)}


def target_path(uri: str) -> str:
    try:
        return urlsplit(uri or "").path or ""
    except ValueError:
        return ""


def method_check(method: str, path: str, routes: list) -> str:
    """'' when the job's method is registered for the path, else the flag."""
    if not path:
        return "no HTTP target"
    registered = route_methods(path, routes)
    if not registered:
        return "NO ROUTE in core/api_server.py"
    method = (method or "").upper()
    if method not in registered:
        return f"METHOD MISMATCH: job {method or '?'}, route {'/'.join(sorted(registered))}"
    return ""


# ─── jobs managed by scripts/schedule_jobs.sh ────────────────────────────────

def parse_managed(script_src: str) -> list:
    """The `job ...` lines between '# BEGIN JOBS' and '# END JOBS' — the same
    table the script creates/updates from."""
    start = script_src.index("\n# BEGIN JOBS\n")
    end = script_src.index("\n# END JOBS\n", start)
    jobs = []
    for line in script_src[start:end].splitlines():
        line = line.strip()
        if not line.startswith("job "):
            continue
        parts = shlex.split(line)
        if len(parts) != 7:
            raise ValueError(f"bad job line: {line}")
        _, name, method, schedule, path, initial, description = parts
        jobs.append({"name": name, "method": method, "schedule": schedule, "path": path,
                     "initial": initial, "description": description})
    return jobs


def parse_retired(script_src: str) -> list:
    """Jobs the script keeps paused (`retire NAME "why"` lines in the table)."""
    start = script_src.index("\n# BEGIN JOBS\n")
    end = script_src.index("\n# END JOBS\n", start)
    names = []
    for line in script_src[start:end].splitlines():
        line = line.strip()
        if line.startswith("retire "):
            names.append(shlex.split(line)[1])
    return names


# ─── the audit ───────────────────────────────────────────────────────────────

def parse_line(line: str) -> dict:
    cols = (line.lstrip("\ufeff").rstrip("\r\n").split("\t") + [""] * 7)[:7]
    name, schedule, uri, method, state, code, last = (c.strip() for c in cols)
    return {"name": name.rsplit("/", 1)[-1], "schedule": schedule, "uri": uri,
            "method": method.upper(), "state": state, "code": code, "last_attempt": last}


def last_status(job: dict) -> str:
    if not job.get("last_attempt"):
        return "never ran"
    code = job.get("code") or "0"
    return STATUS_NAMES.get(code, f"code {code}")


def audit(jobs: list, managed: list, routes: list, retired=()) -> list:
    by_name = {m["name"]: m for m in managed}
    rows = []
    for job in jobs:
        path = target_path(job["uri"])
        flags = []
        bad_method = method_check(job["method"], path, routes)
        if bad_method:
            flags.append(bad_method)
        want = by_name.get(job["name"])
        if want:
            if want["method"] != job["method"]:
                flags.append(f"drift: method {job['method']} (script: {want['method']})")
            if want["schedule"] != job["schedule"]:
                flags.append(f"drift: schedule '{job['schedule']}' (script: '{want['schedule']}')")
            if want["path"] != path:
                flags.append(f"drift: path {path} (script: {want['path']})")
        if job["name"] in retired and job.get("state") == "ENABLED":
            flags.append("retired job still ENABLED")
        status = last_status(job)
        if status == "UNAUTHENTICATED":
            flags.append("last attempt UNAUTHENTICATED: the job carries a missing or old admin key"
                         + (" - rerun the script to refresh it" if want else ""))
        elif status not in ("OK", "never ran"):
            flags.append(f"last attempt failed ({status})")
        rows.append({"name": job["name"], "schedule": job["schedule"], "path": path,
                     "method": job["method"] or "-", "state": job["state"] or "-",
                     "last": last_status(job),
                     "managed": "yes" if want else ("retired" if job["name"] in retired else "no"),
                     "flags": flags})
    present = {j["name"] for j in jobs}
    for m in managed:
        if m["name"] not in present:
            rows.append({"name": m["name"], "schedule": m["schedule"], "path": m["path"],
                         "method": m["method"], "state": "MISSING", "last": "-", "managed": "yes",
                         "flags": ["managed but not created yet - run the script without --audit"]})
    return rows


def render(rows: list, secrets=()) -> str:
    head = ("NAME", "SCHEDULE", "PATH", "METHOD", "STATE", "LAST ATTEMPT", "MANAGED")
    table = [head] + [(r["name"], r["schedule"], r["path"], r["method"], r["state"], r["last"],
                       r["managed"]) for r in rows]
    widths = [max(len(str(row[i])) for row in table) for i in range(len(head))]
    lines = ["  ".join(str(c).ljust(w) for c, w in zip(row, widths)).rstrip() for row in table]
    flagged = [r for r in rows if r["flags"]]
    lines.append("")
    if flagged:
        lines.append("Flags:")
        for r in flagged:
            for f in r["flags"]:
                lines.append(f"  {r['name']}: {f}")
    else:
        lines.append("No flags: every job's method matches its route and every managed job matches the script.")
    managed = sorted({r["name"] for r in rows if r["managed"] == "yes"})
    lines.append("")
    lines.append(f"Managed by scripts/schedule_jobs.sh ({len(managed)}): {', '.join(managed) or '-'}")
    return mask("\n".join(lines), secrets)


def main(argv=None) -> int:
    sys.stdout.reconfigure(errors="replace")
    with open(SCRIPT, encoding="utf-8") as f:
        script_src = f.read()
    with open(API, encoding="utf-8") as f:
        routes = parse_routes(f.read())
    jobs = [parse_line(l) for l in sys.stdin if l.strip()]
    rows = audit(jobs, parse_managed(script_src), routes, parse_retired(script_src))
    print(render(rows))
    return 1 if any(r["flags"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
