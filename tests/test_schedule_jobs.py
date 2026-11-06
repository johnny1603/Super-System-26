"""scripts/schedule_jobs.sh — the one scheduler entry point — and its --audit.

- the job table is the one source of truth (script, audit and these tests read it)
- every managed job's HTTP method is the one core/api_server.py registers
  (lead-volume-scan was POST against a GET route: 405 every morning)
- create uses --headers, update uses --update-headers (the old failure)
- the admin key is never printed: --dry-run masks it, errors are sanitized,
  --audit never requests headers and masks anything key-shaped
The dry-run tests run the real script when a bash is available.
"""
import importlib.util
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "schedule_jobs.sh")

_spec = importlib.util.spec_from_file_location("scheduler_audit", os.path.join(ROOT, "scripts", "scheduler_audit.py"))
sa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sa)


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _bash():
    found = shutil.which("bash")
    if found and "system32" not in found.lower():   # WSL's launcher is not a usable bash here
        return found
    for candidate in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "GitHubDesktop"),):
        for base, _dirs, files in os.walk(candidate):
            if "sh.exe" in files and base.replace("\\", "/").endswith("usr/bin"):
                return os.path.join(base, "sh.exe")
    return None


SRC = _src("scripts", "schedule_jobs.sh")
API = _src("core", "api_server.py")
MANAGED = sa.parse_managed(SRC)
ROUTES = sa.parse_routes(API)
EXPECTED = {
    "chat-rollover": ("GET", "0 8 * * *", "/api/chat/rollover"),
    "chat-rollover-catchup": ("GET", "10-59/15 8-22 * * *", "/api/chat/rollover"),
    "connection-reminders": ("GET", "0 10 * * *", "/api/connections/reminders"),
    "landing-hosting-costs": ("GET", "0 6 2 * *", "/api/landing/hosting-costs"),
    "crm-retry-syncs": ("GET", "0 */6 * * *", "/api/crm/retry-syncs"),
    "lead-volume-scan": ("GET", "0 6 * * *", "/api/leads/volume-scan"),
    "engagement-weekly": ("GET", "0 9 * * 0", "/api/engagement/weekly"),
    "drive-usage-scan": ("GET", "30 3 * * *", "/api/drive/usage-scan"),
    "engagement-morning": ("GET", "30 8 * * 0-5", "/api/engagement/morning"),
    "action-reaper": ("GET", "*/5 * * * *", "/api/actions/reap"),
}
RETIRED = sa.parse_retired(SRC)


class JobTable(unittest.TestCase):
    def test_expected_jobs_are_managed(self):
        by_name = {j["name"]: j for j in MANAGED}
        for name, (method, schedule, path) in EXPECTED.items():
            self.assertIn(name, by_name)
            j = by_name[name]
            self.assertEqual((j["method"], j["schedule"], j["path"]), (method, schedule, path), name)
        self.assertEqual(by_name["landing-hosting-costs"]["initial"], "paused")

    def test_daily_sales_job_is_retired_not_managed(self):
        self.assertEqual(RETIRED, ["engagement-daily"])
        self.assertNotIn("engagement-daily", [j["name"] for j in MANAGED])

    def test_names_unique(self):
        names = [j["name"] for j in MANAGED]
        self.assertEqual(len(names), len(set(names)))

    def test_every_managed_method_matches_the_registered_route(self):
        for j in MANAGED:
            self.assertEqual(sa.method_check(j["method"], j["path"], ROUTES), "", j["name"])
            self.assertIn(f'@app.{j["method"].lower()}("{j["path"]}", dependencies=_admin_only)', API, j["name"])

    def test_project_location_tz(self):
        self.assertIn('PROJECT="super-system-500410"', SRC)
        self.assertIn('LOCATION="${SCHEDULER_LOCATION:-me-west1}"', SRC)
        self.assertIn('TZ_NAME="Asia/Jerusalem"', SRC)
        self.assertIn('--time-zone="$TZ_NAME"', SRC)
        self.assertIn('--http-method="$method"', SRC)


class MethodMatching(unittest.TestCase):
    def test_parse_routes(self):
        routes = sa.parse_routes('@app.get("/a", dependencies=x)\n@app.post("/b/{id}")\n@app.delete("/c")\n')
        self.assertEqual(routes, [("GET", "/a"), ("POST", "/b/{id}"), ("DELETE", "/c")])

    def test_lead_volume_post_is_flagged(self):
        flag = sa.method_check("POST", "/api/leads/volume-scan", ROUTES)
        self.assertEqual(flag, "METHOD MISMATCH: job POST, route GET")
        self.assertEqual(sa.method_check("GET", "/api/leads/volume-scan", ROUTES), "")

    def test_template_and_missing_routes(self):
        routes = [("POST", "/api/clients/{client_id}/accounts"), ("GET", "/api/clients/{client_id}/accounts"),
                  ("GET", "/api/x")]
        self.assertEqual(sa.route_methods("/api/clients/27/accounts", routes), {"GET", "POST"})
        self.assertEqual(sa.method_check("PUT", "/api/clients/27/accounts", routes),
                         "METHOD MISMATCH: job PUT, route GET/POST")
        self.assertEqual(sa.method_check("GET", "/api/nope", routes), "NO ROUTE in core/api_server.py")
        self.assertEqual(sa.method_check("GET", "", routes), "no HTTP target")
        self.assertEqual(sa.method_check("GET", "/api/x/", routes), "")

    def test_audit_rows(self):
        lines = [
            "lead-volume-scan\t0 6 * * *\thttps://app.uallak.com/api/leads/volume-scan\tPOST\tENABLED\t12\t2026-11-02T04:00:00Z",
            "crm-retry-syncs\t0 */6 * * *\thttps://app.uallak.com/api/crm/retry-syncs\tGET\tENABLED\t16\t2026-11-02T06:00:00Z",
            "projects/p/locations/l/jobs/other-job\t0 1 * * *\thttps://app.uallak.com/api/budget/scan\tGET\tPAUSED\t\t",
        ]
        jobs = [sa.parse_line(l) for l in lines]
        rows = {r["name"]: r for r in sa.audit(jobs, MANAGED, ROUTES)}
        lv = rows["lead-volume-scan"]
        self.assertEqual(lv["managed"], "yes")
        self.assertEqual(lv["last"], "UNIMPLEMENTED")
        self.assertIn("METHOD MISMATCH: job POST, route GET", lv["flags"])
        self.assertIn("drift: method POST (script: GET)", lv["flags"])
        self.assertEqual(rows["crm-retry-syncs"]["last"], "UNAUTHENTICATED")
        self.assertTrue(any("UNAUTHENTICATED: the job carries a missing or old admin key" in f
                            for f in rows["crm-retry-syncs"]["flags"]))
        self.assertIn("last attempt failed (UNIMPLEMENTED)", lv["flags"])
        other = rows["other-job"]
        self.assertEqual((other["managed"], other["last"], other["flags"]), ("no", "never ran", []))
        # managed jobs absent from the project are listed as missing
        self.assertEqual(rows["chat-rollover"]["state"], "MISSING")
        text = sa.render(list(rows.values()))
        self.assertIn("lead-volume-scan: METHOD MISMATCH", text)
        self.assertIn(f"Managed by scripts/schedule_jobs.sh ({len(MANAGED)})", text)

    def test_retired_job_still_enabled_is_flagged(self):
        job = sa.parse_line("old\t1 1 * * *\thttps://h/api/engagement/daily\tGET\tENABLED\t\t2026-11-01T05:45:00Z")
        rows = sa.audit([job], [], ROUTES, retired=["old"])
        self.assertEqual(rows[0]["managed"], "retired")
        self.assertIn("retired job still ENABLED", rows[0]["flags"])


class Masking(unittest.TestCase):
    KEY = "Sup3r-Secret_Value.123"

    def test_known_value_and_header_shapes(self):
        for text in (f"X-Admin-Key={self.KEY}", f"x-admin-key: {self.KEY}", f'"X-Admin-Key": "{self.KEY}"',
                     f"Authorization: Bearer{self.KEY}", f"token={self.KEY}"):
            out = sa.mask(text)
            self.assertNotIn(self.KEY, out, text)
            self.assertIn("****", out)
        self.assertEqual(sa.mask(f"oops {self.KEY} here", secrets=[self.KEY]), "oops **** here")

    def test_query_string_credentials(self):
        out = sa.mask("https://h/api/x?key=abc123&page=2")
        self.assertEqual(out, "https://h/api/x?key=****&page=2")

    def test_audit_output_never_carries_a_key(self):
        # even if a key ended up inside a URI, the rendered audit masks it
        job = sa.parse_line(f"j\t* * * * *\thttps://h/api/chat/rollover?token={self.KEY}\tGET\tENABLED\t\t")
        text = sa.render(sa.audit([job], MANAGED, ROUTES))
        self.assertNotIn(self.KEY, text)

    def test_audit_never_requests_headers(self):
        audit_fn = SRC[SRC.index("run_audit() {"):SRC.index("\n}\n", SRC.index("run_audit() {"))]
        self.assertIn("name.basename(),schedule,httpTarget.uri,httpTarget.httpMethod,state,status.code,lastAttemptTime", audit_fn)
        self.assertNotIn("headers", audit_fn.replace("never httpTarget.headers", ""))
        self.assertNotIn("read_admin_key", audit_fn)
        self.assertNotIn("--format=json", audit_fn)
        # the audit runs (and exits) before the key is ever read
        self.assertLess(SRC.index("  run_audit\nfi"), SRC.index("  read_admin_key\nfi"))


class Source(unittest.TestCase):
    def test_header_flag_matches_the_action(self):
        self.assertIn('action="update"; header_flag="--update-headers"', SRC)
        self.assertIn('action="create"; header_flag="--headers"', SRC)

    def test_key_from_secret_manager_never_echoed(self):
        self.assertIn("gcloud secrets versions access", SRC)
        self.assertNotIn("SECRETS_ENCRYPTION_KEY", SRC)
        self.assertIn("unset AK", SRC)
        for line in SRC.splitlines():
            if line.strip().startswith(("echo", "printf")):
                self.assertNotIn("$AK", line, line)
                self.assertNotIn("${AK}", line, line)
        self.assertIn('echo "FAILED: $job ($action, exit $rc): $(sanitize', SRC)

    def test_summary_never_reads_headers(self):
        self.assertIn("--format='value(httpTarget.httpMethod,schedule,timeZone,state,scheduleTime)'", SRC)

    def test_old_commands_are_thin_wrappers(self):
        for name in ("schedule_chat_rollover.sh", "schedule_drive_usage_scan.sh"):
            wrapper = _src("scripts", name)
            self.assertIn('exec bash "$(dirname "$0")/schedule_jobs.sh" "$@"', wrapper)
            self.assertNotIn("gcloud", wrapper)

    def test_lf_endings(self):
        self.assertNotIn("\r\n", SRC)
        self.assertNotIn("\r\n", _src("scripts", "scheduler_audit.py"))


@unittest.skipUnless(_bash(), "no bash on this machine")
class DryRun(unittest.TestCase):
    KEY = "Sup3r-Secret_Value.123"

    def _run(self, *args):
        env = {**os.environ, "DRY_RUN_KEY": self.KEY,
               "PATH": os.path.dirname(_bash()) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"}
        env.pop("CLOUDSDK_CONFIG", None)
        return subprocess.run([_bash(), SCRIPT.replace("\\", "/"), *args], capture_output=True, text=True,
                              encoding="utf-8", env=env, timeout=60)

    def test_syntax(self):
        result = subprocess.run([_bash(), "-n", SCRIPT.replace("\\", "/")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dry_run_masks_the_key(self):
        result = self._run("--dry-run")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertNotIn(self.KEY, out)
        lines = [l for l in out.splitlines() if l.startswith("[dry-run] gcloud scheduler jobs") and " http " in l]
        self.assertEqual(len(lines), len(MANAGED), out)
        for job in MANAGED:
            line = next(l for l in lines if f" http {job['name']} " in l)
            self.assertIn(f"--http-method={job['method']}", line)
            self.assertIn(f"--uri=https://app.uallak.com{job['path']}", line)
            self.assertIn("--time-zone=Asia/Jerusalem", line)
        for line in lines:
            flag = "--headers=" if " create http " in line else "--update-headers="
            self.assertIn(flag + "X-Admin-Key=****", line)
            if " create http " in line:
                self.assertNotIn("--update-headers", line)
        if any(" create http landing-hosting-costs " in l for l in lines):
            self.assertIn("[dry-run] gcloud scheduler jobs pause landing-hosting-costs", out)
        self.assertIn(f"Managed by this script ({len(MANAGED)}):", out)
        self.assertIn("[dry-run] gcloud scheduler jobs pause engagement-daily", out)
        self.assertIn("Retired (kept paused): engagement-daily", out)

    def test_error_text_is_sanitized(self):
        fn = SRC[SRC.index("sanitize() {"):SRC.index("\n}\n", SRC.index("sanitize() {")) + 3]
        err = ("ERROR: (gcloud.scheduler.jobs.create.http) unrecognized arguments: "
               f"--update-headers=X-Admin-Key={self.KEY} (did you mean '--headers'?)")
        program = f'MASK="****"\nAK=""\n{fn}\nsanitize "$1"\n'
        env = {**os.environ, "PATH": os.path.dirname(_bash()) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"}
        result = subprocess.run([_bash(), "-c", program, "x", err], capture_output=True, text=True,
                                encoding="utf-8", env=env)
        self.assertNotIn(self.KEY, result.stdout)
        self.assertIn("X-Admin-Key=****", result.stdout)

    def test_unknown_argument_refused(self):
        self.assertEqual(self._run("--nope").returncode, 2)


if __name__ == "__main__":
    unittest.main()
