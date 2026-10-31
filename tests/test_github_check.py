"""The operator's "בדיקת חיבור GitHub" (operator_github.setup_check, action
github_check): reads only, states only — a Variable's value never appears.

Stdlib only: GitHub is a fake. Run from the repo root:
    python tests/test_github_check.py -v
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import operator_github as gh   # noqa: E402

SECRET_LOOKING = "projects/123456/locations/global/workloadIdentityPools/github/providers/github-actions"


class FakeGitHub:
    def __init__(self, missing=(), forbidden=(), reviewers=1, protected=True):
        self.missing, self.forbidden, self.reviewers, self.protected = missing, forbidden, reviewers, protected
        self.calls = []

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path))
        for part in self.forbidden:
            if part in path:
                raise gh.GitHubError("http_403", "Resource not accessible")
        for part in self.missing:
            if part in path:
                raise gh.GitHubError("http_404", "Not Found")
        if "/actions/variables/" in path:
            return {"name": path.rsplit("/", 1)[1], "value": SECRET_LOOKING}
        if path.endswith("/environments/production"):
            rules = [{"type": "required_reviewers", "reviewers": [{"type": "User"}] * self.reviewers}]
            return {"name": "production", "protection_rules": rules if self.reviewers else []}
        if path.endswith("/branches/master"):
            return {"name": "master", "protected": self.protected}
        return {"ok": True}


class SetupCheck(unittest.TestCase):
    def setUp(self):
        self.saved = (gh._call, os.environ.get("OPERATOR_GITHUB_TOKEN"))
        os.environ["OPERATOR_GITHUB_TOKEN"] = "test-token-not-real"

    def tearDown(self):
        gh._call = self.saved[0]
        if self.saved[1] is None:
            os.environ.pop("OPERATOR_GITHUB_TOKEN", None)
        else:
            os.environ["OPERATOR_GITHUB_TOKEN"] = self.saved[1]

    def check(self, fake):
        gh._call = fake
        return gh.setup_check()

    def test_all_ready(self):
        fake = FakeGitHub()
        out = self.check(fake)
        self.assertTrue(out["ok"])
        self.assertEqual(out["items"]["variable_GCP_WIF_PROVIDER"], {"ok": True, "state": "set"})
        self.assertEqual(out["items"]["required_reviewer"]["state"], "1 reviewer(s)")
        self.assertEqual(out["items"]["master_protected"]["state"], "protected")
        self.assertTrue(all(method == "GET" for method, _ in fake.calls))      # read-only

    def test_never_returns_a_value(self):
        out = json.dumps(self.check(FakeGitHub()))
        self.assertNotIn(SECRET_LOOKING, out)
        self.assertNotIn("test-token-not-real", out)

    def test_missing_variable_is_not_ready(self):
        out = self.check(FakeGitHub(missing=("GCP_DEPLOY_SA",)))
        self.assertFalse(out["ok"])
        self.assertEqual(out["items"]["variable_GCP_DEPLOY_SA"], {"ok": False, "state": "missing"})

    def test_missing_environment_is_not_ready(self):
        out = self.check(FakeGitHub(missing=("/environments/",)))
        self.assertFalse(out["ok"])
        self.assertEqual(out["items"]["environment_production"]["state"], "missing")

    def test_recommendations_do_not_block(self):
        out = self.check(FakeGitHub(reviewers=0, protected=False))
        self.assertTrue(out["ok"])
        self.assertFalse(out["items"]["required_reviewer"]["ok"])
        self.assertEqual(out["items"]["master_protected"]["state"], "not_protected")

    def test_token_without_permission_says_so(self):
        out = self.check(FakeGitHub(forbidden=("/actions/variables/",)))
        self.assertEqual(out["items"]["variable_GCP_WIF_PROVIDER"]["state"], "no_permission")
        self.assertFalse(out["ok"])

    def test_no_token(self):
        os.environ.pop("OPERATOR_GITHUB_TOKEN", None)
        out = gh.setup_check()
        self.assertEqual(out, {"ok": False, "items": {"token": {"ok": False, "state": "not_configured"}}})


class Wiring(unittest.TestCase):
    def _src(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_action_agent_and_page(self):
        actions = self._src("core", "operator_actions.py")
        self.assertIn('"github_check": {"run": _github_check, "by_agent": True', actions)
        agent = self._src("core", "operator_agent.py")
        self.assertIn('"name": "github_check"', agent)
        self.assertIn('"clear_cache", "github_check"', agent)
        page = self._src("dashboard", "admin", "operator", "index.html")
        self.assertIn("runAction('github_check'", page)

    def test_variables_script_prints_names_only_from_gcloud(self):
        script = self._src("scripts", "print_github_variables.sh")
        self.assertIn("GCP_WIF_PROVIDER", script)
        self.assertIn("github-deployer@$PROJECT.iam.gserviceaccount.com", script)
        self.assertNotIn("secrets", script.replace("Secrets and variables", ""))


if __name__ == "__main__":
    unittest.main()
