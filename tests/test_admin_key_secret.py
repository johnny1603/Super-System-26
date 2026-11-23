"""scripts/move_admin_key_to_secret.sh (2026-11-24): ADMIN_KEY from a plain
env var on super-system into Secret Manager.

What must stay true:
- the value never reaches the screen, a log or a file: it is piped from the
  service config into `gcloud secrets versions add --data-file=-`;
- the switch is ONE `gcloud run services update` that removes the plain var
  and adds the secret reference together (no revision without ADMIN_KEY);
- running it again when it is already a secret does nothing;
- the usual deploy command (`gcloud run deploy --source .` with no env /
  secret flags) is untouched by the repo, so it keeps the reference;
- scripts/schedule_jobs.sh reads the key from either form.

Run from the repo root:  python tests/test_admin_key_secret.py -v
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


SCRIPT = _read("scripts", "move_admin_key_to_secret.sh")


class MoveAdminKey(unittest.TestCase):
    def test_value_only_travels_through_a_pipe(self):
        self.assertIn("sys.stdout.write(v)' | gcloud secrets versions add \"$SECRET\"", SCRIPT)
        self.assertIn("--data-file=-", SCRIPT)
        code = "\n".join(ln for ln in SCRIPT.splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotRegex(code, r"echo[^\n]*\$(AK|VALUE|KEY)\b")
        self.assertNotIn("set -x", code)
        self.assertNotRegex(code, r">\s*[\w/.$-]+\.(txt|env|key)")

    def test_one_update_swaps_plain_for_secret(self):
        m = re.search(r"gcloud run services update[^\n]*\\\n([^\n]*\\\n)*[^\n]*", SCRIPT)
        self.assertTrue(m)
        cmd = m.group(0)
        self.assertIn("--remove-env-vars ADMIN_KEY", cmd)
        self.assertIn('--update-secrets "ADMIN_KEY=$SECRET:$VERSION"', cmd)
        code = "\n".join(ln for ln in SCRIPT.splitlines() if not ln.lstrip().startswith("#"))
        self.assertEqual(code.count("gcloud run services update"), 1)

    def test_idempotent_and_checked(self):
        self.assertIn('secret) echo "Already a Secret Manager reference - nothing to do."; exit 0', SCRIPT)
        self.assertIn("roles/secretmanager.secretAccessor", SCRIPT)
        self.assertIn('if [ "$K" = "secret" ]; then', SCRIPT)

    def test_scheduler_script_reads_either_form(self):
        sched = _read("scripts", "schedule_jobs.sh")
        self.assertIn('elif "valueFrom" in entry:', sched)
        self.assertIn("gcloud secrets versions access", sched)

    def test_nothing_in_the_repo_rewrites_the_service_env_on_deploy(self):
        for root, _dirs, files in os.walk(os.path.join(ROOT, ".github")):
            for name in files:
                text = _read(os.path.relpath(os.path.join(root, name), ROOT))
                for flag in ("--set-env-vars", "--set-secrets", "--clear-env-vars", "--clear-secrets",
                             "--env-vars-file"):
                    self.assertNotIn(flag, text, name)


if __name__ == "__main__":
    unittest.main()
