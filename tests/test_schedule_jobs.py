"""scripts/schedule_jobs.sh — the one scheduler entry point.

- create uses --headers, update uses --update-headers (the old failure)
- the admin key is never printed: --dry-run masks it, errors are sanitized
- every job the docs promise is there; hosting costs starts paused
The dry-run test runs the real script when a bash is available.
"""
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "schedule_jobs.sh")


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


class Source(unittest.TestCase):
    src = _src("scripts", "schedule_jobs.sh")

    def test_header_flag_matches_the_action(self):
        self.assertIn('action="update"; header_flag="--update-headers"', self.src)
        self.assertIn('action="create"; header_flag="--headers"', self.src)

    def test_all_jobs(self):
        for job, schedule, path in (("chat-rollover", '"0 8 * * *"', "/api/chat/rollover"),
                                    ("chat-rollover-catchup", '"10-59/15 8-22 * * *"', "/api/chat/rollover"),
                                    ("connection-reminders", '"0 10 * * *"', "/api/connections/reminders"),
                                    ("landing-hosting-costs", '"0 6 2 * *"', "/api/landing/hosting-costs")):
            self.assertIn(f"upsert {job} {schedule} \"{path}\"", self.src)
        self.assertIn("pause_if_new landing-hosting-costs", self.src)
        self.assertIn('PROJECT="super-system-500410"', self.src)
        self.assertIn('LOCATION="${SCHEDULER_LOCATION:-me-west1}"', self.src)

    def test_routes_are_get_as_the_server_defines_them(self):
        api = _src("core", "api_server.py")
        for path in ("/api/chat/rollover", "/api/connections/reminders", "/api/landing/hosting-costs"):
            self.assertIn(f'@app.get("{path}", dependencies=_admin_only)', api)
        self.assertIn("--http-method=GET", self.src)

    def test_key_from_secret_manager_never_echoed(self):
        self.assertIn("gcloud secrets versions access", self.src)
        self.assertNotIn("SECRETS_ENCRYPTION_KEY", self.src)
        self.assertIn("unset AK", self.src)
        for line in self.src.splitlines():
            if line.strip().startswith(("echo", "printf")):
                self.assertNotIn("$AK", line, line)
                self.assertNotIn("${AK}", line, line)
        # errors: only the job name + gcloud's sanitized ERROR line, never the arguments
        self.assertIn('echo "FAILED: $job ($action, exit $rc): $(sanitize', self.src)

    def test_summary_never_reads_headers(self):
        self.assertIn("--format='value(schedule,timeZone,state,scheduleTime)'", self.src)

    def test_old_command_is_a_thin_wrapper(self):
        wrapper = _src("scripts", "schedule_chat_rollover.sh")
        self.assertIn('exec bash "$(dirname "$0")/schedule_jobs.sh" "$@"', wrapper)
        self.assertNotIn("gcloud", wrapper)


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
        self.assertEqual(len(lines), 4, out)
        for line in lines:
            # create ↔ --headers, update ↔ --update-headers (gcloud may or may not exist here)
            flag = "--headers=" if " create http " in line else "--update-headers="
            self.assertIn(flag + "X-Admin-Key=****", line)
            if " create http " in line:
                self.assertNotIn("--update-headers", line)
        if any(" create http landing-hosting-costs " in l for l in lines):
            self.assertIn("[dry-run] gcloud scheduler jobs pause landing-hosting-costs", out)

    def test_error_text_is_sanitized(self):
        # The exact shape of the original failure, fed through the script's own sanitize()
        src = _src("scripts", "schedule_jobs.sh")
        fn = src[src.index("sanitize() {"):src.index("\n}\n", src.index("sanitize() {")) + 3]
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
