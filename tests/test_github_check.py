"""The operator's "בדיקת חיבור GitHub" (operator_github.connection_check):
Tier 1, read-only. Token valid, repo reachable, may create a branch / open a
PR / dispatch the deploy — each tested WITHOUT doing it — plus the Variables,
the production environment and the recommendations. Plain Hebrew lines; a
Variable's value and the token never appear.

Stdlib only: GitHub is a fake that behaves like the real one (permission
first: 403; then validation: 422). Run from the repo root:
    python tests/test_github_check.py -v
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import operator_github as gh   # noqa: E402

VALUE = "projects/123456/locations/global/workloadIdentityPools/github/providers/github-actions"
TOKEN = "test-token-not-real"


class FakeGitHub:
    def __init__(self, bad_token=False, no_repo=False, denied=(), missing=(), probe_exists=False,
                 reviewers=1, protected=True):
        self.bad_token, self.no_repo, self.denied, self.missing = bad_token, no_repo, denied, missing
        self.probe_exists, self.reviewers, self.protected = probe_exists, reviewers, protected
        self.calls = []

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json")))
        if self.bad_token:
            raise gh.GitHubError("http_401", "Bad credentials")
        if path == "/user":
            return {"login": "owner"}
        if self.no_repo:
            raise gh.GitHubError("http_404", "Not Found")
        if method == "POST":
            for part in self.denied:
                if part in path:
                    raise gh.GitHubError("http_403", "Resource not accessible by personal access token")
            raise gh.GitHubError("http_422", "Validation Failed")     # the impossible input
        if path.endswith(f"/git/ref/heads/{gh.PROBE_BRANCH}"):
            if self.probe_exists:
                return {"ref": "x"}
            raise gh.GitHubError("http_404", "Not Found")
        for part in self.missing:
            if part in path:
                raise gh.GitHubError("http_404", "Not Found")
        if "/actions/variables/" in path:
            return {"name": path.rsplit("/", 1)[1], "value": VALUE}
        if path.endswith("/environments/production"):
            rules = [{"type": "required_reviewers", "reviewers": [{"type": "User"}] * self.reviewers}]
            return {"protection_rules": rules if self.reviewers else []}
        if path.endswith("/branches/master"):
            return {"protected": self.protected}
        return {"ok": True}


class ConnectionCheck(unittest.TestCase):
    def setUp(self):
        self.saved = (gh._call, os.environ.get("OPERATOR_GITHUB_TOKEN"))
        os.environ["OPERATOR_GITHUB_TOKEN"] = TOKEN

    def tearDown(self):
        gh._call = self.saved[0]
        if self.saved[1] is None:
            os.environ.pop("OPERATOR_GITHUB_TOKEN", None)
        else:
            os.environ["OPERATOR_GITHUB_TOKEN"] = self.saved[1]

    def check(self, fake):
        gh._call = fake
        out = gh.connection_check()
        return out, {l["check"]: l for l in out["lines"]}

    def test_all_ready_in_plain_hebrew(self):
        out, lines = self.check(FakeGitHub())
        self.assertTrue(out["ok"])
        self.assertIn("הכול מוכן", out["summary_he"])
        for key in ("token", "repo", "branch", "pr", "dispatch", "workflow", "variable_GCP_WIF_PROVIDER",
                    "variable_GCP_DEPLOY_SA", "environment", "reviewer", "protected"):
            self.assertTrue(lines[key]["ok"], key)
            self.assertRegex(lines[key]["text_he"], r"[א-ת]")
        self.assertEqual(lines["branch"]["text_he"], "מותר ליצור ענף (נבדק בלי ליצור)")

    def test_nothing_is_created(self):
        fake = FakeGitHub()
        self.check(fake)
        posts = [(p, body) for m, p, body in fake.calls if m == "POST"]
        self.assertEqual(len(posts), 3)
        ref, pr, dispatch = posts
        self.assertEqual(ref[1]["sha"], "0" * 40)                     # a commit that cannot exist
        self.assertEqual(pr[1]["head"], gh.PROBE_BRANCH)
        self.assertEqual(dispatch[1]["ref"], gh.PROBE_BRANCH)
        # the probe branch was confirmed absent BEFORE the PR / dispatch probes
        order = [p for _m, p, _b in fake.calls]
        self.assertLess(order.index(f"/repos/{gh.REPO}/git/ref/heads/{gh.PROBE_BRANCH}"),
                        order.index(f"/repos/{gh.REPO}/pulls"))

    def test_existing_probe_branch_skips_pr_and_dispatch(self):
        fake = FakeGitHub(probe_exists=True)
        out, lines = self.check(fake)
        self.assertIsNone(lines["pr"]["ok"])
        self.assertIsNone(lines["dispatch"]["ok"])
        self.assertFalse(any(m == "POST" and ("/pulls" in p or "dispatches" in p) for m, p, _ in fake.calls))
        self.assertFalse(out["ok"])

    def test_missing_permissions_named_in_hebrew(self):
        out, lines = self.check(FakeGitHub(denied=("/git/refs", "/pulls", "/dispatches")))
        self.assertFalse(out["ok"])
        self.assertIn("Contents: Read and write", lines["branch"]["text_he"])
        self.assertIn("Pull requests: Read and write", lines["pr"]["text_he"])
        self.assertIn("Actions: Read and write", lines["dispatch"]["text_he"])
        self.assertIn("3 דברים לתקן", out["summary_he"])

    def test_bad_token(self):
        out, lines = self.check(FakeGitHub(bad_token=True))
        self.assertFalse(out["ok"])
        self.assertIn("פג תוקפו", lines["token"]["text_he"])
        self.assertEqual(len(out["lines"]), 1)

    def test_repo_unreachable(self):
        out, lines = self.check(FakeGitHub(no_repo=True))
        self.assertFalse(lines["repo"]["ok"])
        self.assertFalse(out["ok"])

    def test_missing_variable_and_environment(self):
        out, lines = self.check(FakeGitHub(missing=("GCP_DEPLOY_SA", "/environments/")))
        self.assertFalse(out["ok"])
        self.assertIn("GCP_DEPLOY_SA חסר", lines["variable_GCP_DEPLOY_SA"]["text_he"])
        self.assertIn("עוד לא קיימת", lines["environment"]["text_he"])

    def test_recommendations_do_not_block(self):
        out, lines = self.check(FakeGitHub(reviewers=0, protected=False))
        self.assertTrue(out["ok"])
        self.assertFalse(lines["reviewer"]["ok"])
        self.assertTrue(lines["protected"]["recommended"])

    def test_never_returns_a_value_or_the_token(self):
        out, _ = self.check(FakeGitHub())
        text = json.dumps(out, ensure_ascii=False)
        self.assertNotIn(VALUE, text)
        self.assertNotIn(TOKEN, text)

    def test_no_token(self):
        os.environ.pop("OPERATOR_GITHUB_TOKEN", None)
        out = gh.connection_check()
        self.assertFalse(out["ok"])
        self.assertIn("setup_operator.sh", out["lines"][0]["text_he"])


class Wiring(unittest.TestCase):
    def _src(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_tier1_route_agent_and_page(self):
        api = self._src("core", "api_server.py")
        route = api[api.index('@app.get("/api/admin/operator/github-check")'):][:300]
        self.assertIn("_operator_admin(request)", route)
        self.assertNotIn('"github_check"', self._src("core", "operator_actions.py"))   # not a Tier 2 action
        agent = self._src("core", "operator_agent.py")
        self.assertIn('"name": "github_check", "description": "Tier 1 read', agent)
        self.assertIn("return operator_github.connection_check()", agent)
        self.assertIn("call('/github-check')", self._src("dashboard", "admin", "operator", "index.html"))

    def test_variables_script(self):
        script = self._src("scripts", "print_github_variables.sh")
        self.assertIn("GCP_WIF_PROVIDER", script)
        self.assertIn("github-deployer@$PROJECT.iam.gserviceaccount.com", script)

    def test_workflow_uses_the_production_environment(self):
        self.assertIn("    environment: production\n", self._src(".github", "workflows", "deploy.yml"))


if __name__ == "__main__":
    unittest.main()
