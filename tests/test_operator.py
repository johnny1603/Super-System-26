"""The owner's operator agent (2026-11-01, docs/OPERATOR.md).

A. Tier 3 cannot run without the second gate: no code / wrong code / a code
   for another step / another session / expired / used twice / gate switched
   off → refused BEFORE any GitHub write. A correct code opens a PR on an
   operator/... branch only; merge+deploy needs a NEW code bound to the exact
   PR head, refuses a moved head and failed or pending tests.
B. Protected: workflows, the gate, sessions, the vault, env/secret files and
   SECRETS_ENCRYPTION_KEY / secret-looking text can never be in a change.
C. Rollback works: traffic moves to the newest READY revision before the
   serving one (one PATCH, traffic only), restore returns to latest, both are
   audited BEFORE they run, an unwritable audit means nothing runs, and the
   agent can only OFFER them (owner tap).
D. Flags: an override wins over env; the round-2 Google flags can be switched
   off but never on (refused on write, ignored on read).
E. Admin-only: every operator route requires the admin session; nothing on a
   client surface mentions it; the prompt forbids "passing it to the team".
F. The deploy workflow runs only on request, only for a commit on master,
   tests first, as the dedicated service account via WIF.

Stdlib only. Run from tests/:  python -I -B test_operator.py -v
"""
import json
import os
import re
import sys
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ─── An in-memory Supabase ───────────────────────────────────────────────────

class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload, self._limit = db, table, [], "select", None, None

    def select(self, *_a):
        self.op = "select"; return self

    def insert(self, row):
        self.op, self.payload = "insert", row; return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", row; return self

    def delete(self):
        self.op = "delete"; return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val); return self

    def like(self, col, pattern):
        prefix = pattern.rstrip("%")
        self.filters.append(lambda r: str(r.get(col, "")).startswith(prefix)); return self

    def lt(self, col, val):
        self.filters.append(lambda r: str(r.get(col, "")) < val); return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._limit = n; return self

    def execute(self):
        if self.db.fail.get(self.table):
            raise RuntimeError("db down")
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            row = {"id": len(rows) + 1, "created_at": "2026-11-01T10:00:00", **self.payload}
            rows.append(row)
            return types.SimpleNamespace(data=[row])
        if self.op == "upsert":
            for r in rows:
                if r["key"] == self.payload["key"]:
                    r.update(self.payload)
                    return types.SimpleNamespace(data=[r])
            rows.append(dict(self.payload))
            return types.SimpleNamespace(data=[self.payload])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "delete":
            for r in hit:
                rows.remove(r)
            return types.SimpleNamespace(data=hit)
        return types.SimpleNamespace(data=hit[: self._limit] if self._limit else hit)


class FakeDB:
    def __init__(self):
        self.tables, self.fail = {}, {}

    def table(self, name):
        return _Q(self, name)


DB = FakeDB()
CLIENTS = {27: {"id": 27, "phone": "0500000000"}}
sys.modules["agents.client_agent"] = types.ModuleType("agents.client_agent")
sys.modules["agents.client_agent"].__dict__.update(
    _db=lambda: DB, get_client=lambda cid: CLIENTS.get(cid), log_activity=lambda *a, **k: {})
SENT_CODES = []
sys.modules["core.email_service"] = types.ModuleType("core.email_service")
sys.modules["core.email_service"].send_operator_code = \
    lambda to, code, summary, minutes: SENT_CODES.append((to, code, summary)) or True

from core import cloud_ops, operator_actions, operator_changes, operator_flags, operator_gate  # noqa: E402
from core import operator_github as gh  # noqa: E402

ENV_ON = {"OPERATOR_CODE_ENABLED": "1", "OPERATOR_APPROVAL_EMAIL": "owner@example.com",
          "GMAIL_APP_PASSWORD": "x" * 16, "OPERATOR_GITHUB_TOKEN": "t" * 20}
MASTER = "a" * 40
HEAD = "b" * 40


class FakeGitHub:
    """Records every write; serves one file on master."""

    def __init__(self):
        self.writes = []
        self.files = {"core/greeting.py": "def hello():\n    return 'hi'\n"}
        self.head = HEAD
        self.check_state = "passed"

    def install(self, test):
        originals = {}
        for name in ("head_sha", "file_at", "create_branch_commit", "open_pr", "pr_state", "checks",
                     "merge_pr", "dispatch_deploy"):
            originals[name] = getattr(gh, name)
            setattr(gh, name, getattr(self, name))
        test.addCleanup(lambda: [setattr(gh, k, v) for k, v in originals.items()])

    def head_sha(self, branch="master"):
        return MASTER

    def file_at(self, path, ref):
        return self.files.get(path)

    def create_branch_commit(self, branch, base, files, message):
        self.writes.append(("branch", branch, dict(files)))
        return HEAD

    def open_pr(self, branch, title, body):
        self.writes.append(("pr", branch))
        return {"number": 7, "url": "https://github.com/x/pull/7", "head_sha": self.head}

    def pr_state(self, number):
        return {"number": 7, "state": "open", "merged": False, "head_sha": self.head,
                "head_ref": "operator/x", "base_ref": "master", "url": ""}

    def checks(self, sha):
        return {"state": self.check_state, "runs": []}

    def merge_pr(self, number, head, title):
        self.writes.append(("merge", number, head))
        return "c" * 40

    def dispatch_deploy(self, sha):
        self.writes.append(("deploy", sha))


def _env(test, values):
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    test.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                             for k, v in saved.items()])


EDIT = [{"path": "core/greeting.py", "old": "return 'hi'", "new": "return 'shalom'"}]


# ─── A. The second gate ──────────────────────────────────────────────────────

class SecondGate(unittest.TestCase):
    def setUp(self):
        DB.tables.clear(); DB.fail.clear(); SENT_CODES.clear()
        _env(self, ENV_ON)
        self.gh = FakeGitHub(); self.gh.install(self)
        self.change = operator_changes.propose("Greeting", "מחליף ברכה", "בדיקה", EDIT)

    def code_for(self, step, fp="fp-1"):
        g = operator_changes.request_code(self.change["id"], step, fp, "admin")
        return g["gate_id"], SENT_CODES[-1][1]

    def test_proposing_writes_nothing_to_github(self):
        self.assertEqual(self.gh.writes, [])
        self.assertEqual(self.change["status"], "proposed")
        self.assertIn("-    return 'hi'", self.change["diff"])
        self.assertIn("+    return 'shalom'", self.change["diff"])

    def test_no_code_no_pr(self):
        for gate_id, code in (("", ""), ("0" * 16, "123456"), ("zz", "abc")):
            with self.assertRaises(operator_changes.ChangeError):
                operator_changes.open_pr(self.change["id"], gate_id, code, "fp-1", "admin")
        self.assertEqual(self.gh.writes, [])

    def test_wrong_code_then_lock(self):
        gate_id, code = self.code_for("open_pr")
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(operator_gate.MAX_ATTEMPTS - 1):
            with self.assertRaises(operator_changes.ChangeError) as e:
                operator_changes.open_pr(self.change["id"], gate_id, wrong, "fp-1", "admin")
            self.assertEqual(e.exception.code, "ERR_GATE_WRONG")
        with self.assertRaises(operator_changes.ChangeError) as e:
            operator_changes.open_pr(self.change["id"], gate_id, wrong, "fp-1", "admin")
        self.assertEqual(e.exception.code, "ERR_GATE_LOCKED")
        with self.assertRaises(operator_changes.ChangeError):        # burnt: even the right code is dead
            operator_changes.open_pr(self.change["id"], gate_id, code, "fp-1", "admin")
        self.assertEqual(self.gh.writes, [])

    def test_code_is_bound_to_the_session(self):
        gate_id, code = self.code_for("open_pr", fp="fp-1")
        with self.assertRaises(operator_changes.ChangeError) as e:
            operator_changes.open_pr(self.change["id"], gate_id, code, "fp-OTHER", "admin")
        self.assertEqual(e.exception.code, "ERR_GATE_MISMATCH")
        self.assertEqual(self.gh.writes, [])

    def test_expired_code(self):
        gate_id, code = self.code_for("open_pr")
        row = DB.tables["app_settings"][-1]
        row["value"]["exp"] = time.time() - 1
        with self.assertRaises(operator_changes.ChangeError) as e:
            operator_changes.open_pr(self.change["id"], gate_id, code, "fp-1", "admin")
        self.assertEqual(e.exception.code, "ERR_GATE_EXPIRED")

    def test_gate_off_refuses_everything(self):
        os.environ.pop("OPERATOR_CODE_ENABLED")
        with self.assertRaises(operator_gate.GateError) as e:
            operator_changes.request_code(self.change["id"], "open_pr", "fp-1", "admin")
        self.assertEqual(e.exception.code, "ERR_GATE_DISABLED")
        self.assertEqual(SENT_CODES, [])
        with self.assertRaises(operator_changes.ChangeError):
            operator_changes.open_pr(self.change["id"], "a" * 16, "123456", "fp-1", "admin")
        self.assertEqual(self.gh.writes, [])

    def test_the_admin_password_alone_cannot_ship(self):
        # A logged-in admin session (fp) with no access to the owner's mailbox:
        # it can ask for a code, but never sees it — every guess fails, nothing is written
        gate_id, _ = self.code_for("open_pr")
        for guess in ("123456", "000001"):
            with self.assertRaises(operator_changes.ChangeError):
                operator_changes.open_pr(self.change["id"], gate_id, guess, "fp-1", "admin")
        self.assertEqual(self.gh.writes, [])
        self.assertTrue(SENT_CODES and SENT_CODES[-1][0] == "owner@example.com")
        self.assertIn("Greeting", SENT_CODES[-1][2])               # the email names what it approves

    def test_full_path_two_codes(self):
        gate_id, code = self.code_for("open_pr")
        opened = operator_changes.open_pr(self.change["id"], gate_id, code, "fp-1", "admin")
        self.assertEqual(opened["status"], "pr_open")
        branch = self.gh.writes[0][1]
        self.assertTrue(branch.startswith("operator/"))
        self.assertEqual(self.gh.writes[0][2]["core/greeting.py"], "def hello():\n    return 'shalom'\n")
        # the open_pr code is single-use and cannot merge
        with self.assertRaises(operator_changes.ChangeError):
            operator_changes.open_pr(self.change["id"], gate_id, code, "fp-1", "admin")
        with self.assertRaises(operator_changes.ChangeError):
            operator_changes.merge_and_deploy(self.change["id"], gate_id, code, "fp-1", "admin")
        self.assertFalse(any(w[0] in ("merge", "deploy") for w in self.gh.writes))
        gate_id2, code2 = self.code_for("merge_deploy")
        done = operator_changes.merge_and_deploy(self.change["id"], gate_id2, code2, "fp-1", "admin")
        self.assertEqual(done["status"], "deploying")
        self.assertIn(("merge", 7, HEAD), self.gh.writes)
        self.assertIn(("deploy", "c" * 40), self.gh.writes)
        audit = " ".join(r["issues"][0] for r in DB.tables["alerts"])
        self.assertIn("tier3 open_pr START", audit)
        self.assertIn("tier3 merge_deploy OK", audit)

    def _to_pr_open(self):
        gate_id, code = self.code_for("open_pr")
        operator_changes.open_pr(self.change["id"], gate_id, code, "fp-1", "admin")

    def test_merge_refuses_a_moved_head(self):
        self._to_pr_open()
        gate_id, code = self.code_for("merge_deploy")
        self.gh.head = "d" * 40                       # someone pushed after the approval was asked
        with self.assertRaises(operator_changes.ChangeError):
            operator_changes.merge_and_deploy(self.change["id"], gate_id, code, "fp-1", "admin")
        self.assertFalse(any(w[0] == "merge" for w in self.gh.writes))

    def test_merge_refuses_failed_or_pending_tests(self):
        self._to_pr_open()
        for state in ("failed", "pending"):
            self.gh.check_state = state
            gate_id, code = self.code_for("merge_deploy")
            with self.assertRaises(operator_changes.ChangeError) as e:
                operator_changes.merge_and_deploy(self.change["id"], gate_id, code, "fp-1", "admin")
            self.assertEqual(e.exception.code, "ERR_TESTS_" + state.upper())
        self.assertFalse(any(w[0] == "merge" for w in self.gh.writes))

    def test_gate_is_checked_in_the_protected_module_not_the_route(self):
        changes = _src("core", "operator_changes.py")
        for fn, purpose in (("def open_pr(", "open_pr"), ("def merge_and_deploy(", "merge_deploy")):
            body = changes.split(fn, 1)[1].split("\ndef ", 1)[0]
            first_lines = "\n".join(body.splitlines()[:4])            # before anything else happens
            self.assertIn(f'_gate(change, "{purpose}", gate_id, code, admin_fp, who)', first_lines, fn)
        self.assertIn("core/operator_changes.py", operator_changes.PROTECTED_FILES)
        api = _src("core", "api_server.py")
        self.assertNotIn("operator_gate.verify", api)

    def test_github_writes_only_to_operator_branches(self):
        saved = gh._call
        gh._call = lambda *a, **k: self.fail("must not call GitHub")
        self.addCleanup(lambda: setattr(gh, "_call", saved))
        for bad in ("master", "main", "operator/../master", "feature/x", "operator/"):
            with self.assertRaises(gh.GitHubError, msg=bad):
                _real_create_branch_commit(bad, MASTER, {"x.py": ""}, "m")


_real_create_branch_commit = gh.create_branch_commit


# ─── B. Protected ────────────────────────────────────────────────────────────

class Protected(unittest.TestCase):
    def test_paths(self):
        for path in (".github/workflows/deploy.yml", "core/operator_gate.py", "core/operator_changes.py",
                     "core/session.py", "core/secrets_vault.py", "agents/keys_agent.py", ".env",
                     "config/secrets.py", "creds/service_account.json", "../etc/passwd", "/abs.py",
                     "migrations/x.sql", "core/operator_agent.py"):
            self.assertTrue(operator_changes.protected_reason(path), path)
        for path in ("core/performance.py", "dashboard/client/index.html", "agents/seo_agent.py"):
            self.assertEqual(operator_changes.protected_reason(path), "", path)

    def test_content(self):
        with self.assertRaises(operator_changes.ChangeError) as e:
            operator_changes.validate_edits([{"path": "core/x.py", "old": "a",
                                              "new": "os.environ['SECRETS_ENCRYPTION_KEY']"}])
        self.assertEqual(e.exception.code, "ERR_FORBIDDEN_CONTENT")
        with self.assertRaises(operator_changes.ChangeError) as e:
            operator_changes.validate_edits([{"path": ".github/workflows/deploy.yml", "old": "a", "new": "b"}])
        self.assertEqual(e.exception.code, "ERR_PROTECTED_PATH")

    def test_edits_must_match_exactly_once(self):
        with self.assertRaises(operator_changes.ChangeError) as e:
            operator_changes.apply_edits({"a.py": "x\nx\n"}, [{"path": "a.py", "old": "x", "new": "y"}])
        self.assertEqual(e.exception.code, "ERR_EDIT_NOT_UNIQUE")
        with self.assertRaises(operator_changes.ChangeError) as e:
            operator_changes.apply_edits({"a.py": "x\n"}, [{"path": "a.py", "old": "z", "new": "y"}])
        self.assertEqual(e.exception.code, "ERR_EDIT_NOT_FOUND")
        self.assertEqual(operator_changes.apply_edits({"n.py": None}, [{"path": "n.py", "old": "", "new": "1"}]),
                         {"n.py": "1"})


# ─── C. Rollback ─────────────────────────────────────────────────────────────

class FakeRun:
    """Cloud Run v2: a service and its revisions."""

    def __init__(self):
        self.revisions = [
            {"name": "x/revisions/super-system-00012-new", "createTime": "2026-11-01T09:00:00Z",
             "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}],
             "containers": [{"env": [{"name": "SECRET", "value": "never-leaves"}]}]},
            {"name": "x/revisions/super-system-00011-bad", "createTime": "2026-10-31T09:00:00Z",
             "conditions": [{"type": "Ready", "state": "CONDITION_FAILED", "message": "crash"}]},
            {"name": "x/revisions/super-system-00010-old", "createTime": "2026-10-30T09:00:00Z",
             "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]},
        ]
        self.traffic = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}]
        self.patches = []

    def __call__(self, method, url, **kwargs):
        if method == "GET" and url.endswith("/revisions"):
            return {"revisions": self.revisions}
        if method == "GET":
            return {"latestReadyRevision": "x/revisions/super-system-00012-new", "uri": "https://s",
                    "trafficStatuses": [dict(t, revision=t.get("revision", "")) for t in self.traffic]}
        if method == "PATCH":
            self.assertion(kwargs)
            self.patches.append(kwargs["json"])
            self.traffic = kwargs["json"]["traffic"]
            return {"name": "operations/1"}
        raise AssertionError(method)

    @staticmethod
    def assertion(kwargs):
        assert kwargs["params"] == {"updateMask": "traffic"}, "traffic-only update"
        assert set(kwargs["json"]) == {"traffic"}, "never the template / env"


class Rollback(unittest.TestCase):
    def setUp(self):
        DB.tables.clear(); DB.fail.clear()
        self.run = FakeRun()
        saved = cloud_ops._call
        cloud_ops._call = self.run
        self.addCleanup(lambda: setattr(cloud_ops, "_call", saved))

    def test_previous_is_the_newest_ready_before_serving(self):
        self.assertEqual(cloud_ops.previous_revision()["name"], "super-system-00010-old")   # skips the failed one

    def test_revision_objects_never_leak_env(self):
        self.assertNotIn("never-leaves", json.dumps(cloud_ops.status()))

    def test_one_tap_rollback_then_restore(self):
        out = operator_actions.run("rollback", {}, "admin session fp from 1.2.3.4")
        self.assertEqual(out["result"]["to"], "super-system-00010-old")
        self.assertTrue(out["result"]["done"])
        self.assertEqual(self.run.patches[-1]["traffic"], [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                                                            "revision": "super-system-00010-old", "percent": 100}])
        self.assertTrue(cloud_ops.serving()["pinned"])
        back = operator_actions.run("restore_latest", {}, "admin")
        self.assertTrue(back["result"]["done"])
        self.assertEqual(self.run.patches[-1]["traffic"], [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST",
                                                            "percent": 100}])
        audit = [r["issues"][0] for r in DB.tables["alerts"]]
        self.assertTrue(audit[0].startswith("tier2 rollback START") and "1.2.3.4" in audit[0])
        self.assertTrue(any(a.startswith("tier2 rollback OK") for a in audit))
        self.assertTrue(all(r["source"] == "operator" for r in DB.tables["alerts"]))

    def test_never_to_a_failed_revision(self):
        with self.assertRaises(operator_actions.ActionError):
            operator_actions.run("rollback", {"revision": "super-system-00011-bad"}, "admin")
        self.assertEqual(self.run.patches, [])

    def test_no_audit_no_rollback(self):
        DB.fail["alerts"] = True
        with self.assertRaises(operator_actions.AuditUnavailable):
            operator_actions.run("rollback", {}, "admin")
        self.assertEqual(self.run.patches, [])

    def test_the_agent_can_only_offer_it(self):
        with self.assertRaises(operator_actions.ActionError) as e:
            operator_actions.run("rollback", {}, "agent", by_agent=True)
        self.assertEqual(e.exception.code, "ERR_OWNER_TAP_ONLY")
        self.assertEqual(self.run.patches, [])
        from core import operator_agent
        turn = operator_agent._Turn("admin")
        operator_agent.run_tool("offer_action", {"action": "rollback", "reason_he": "שגיאות אחרי פריסה"}, turn)
        self.assertEqual(self.run.patches, [])
        self.assertEqual(turn.cards[0]["type"], "action")
        refused = operator_agent.run_tool("offer_action", {"action": "clear_cache", "reason_he": "x"}, turn)
        self.assertIn("error", refused)
        self.assertNotIn("rollback", {t["name"] for t in operator_agent.TOOLS})

    def test_deploy_watch_flags_a_bad_new_revision(self):
        state = {"serving": {"revision": "super-system-00012-new"}, "previous": {"name": "super-system-00010-old"},
                 "serving_since": cloud_ops._now().isoformat()}
        saved = cloud_ops.logs
        cloud_ops.logs = lambda **k: [{"text": "boom"}] * cloud_ops.ERROR_THRESHOLD
        try:
            watch = cloud_ops.deploy_watch(state)
        finally:
            cloud_ops.logs = saved
        self.assertTrue(watch["suspicious"])
        self.assertEqual(watch["rollback_to"], "super-system-00010-old")

    def test_log_filter_cannot_be_broken_out_of(self):
        with self.assertRaises(cloud_ops.CloudOpsUnavailable):
            cloud_ops.log_filter(text='x" OR severity>=DEFAULT OR "')
        self.assertIn('"timeout"', cloud_ops.log_filter(text="timeout"))


# ─── D. Flags ────────────────────────────────────────────────────────────────

class Flags(unittest.TestCase):
    def setUp(self):
        DB.tables.clear(); DB.fail.clear(); operator_flags._cache.clear()

    def test_round2_never_on_from_here(self):
        with self.assertRaises(operator_flags.FlagError) as e:
            operator_flags.set_flag("GMAIL_SEND_ENABLED", "on")
        self.assertEqual(e.exception.code, "ERR_FLAG_OWNER_ONLY")
        # even a hand-written ON row is ignored
        DB.tables["app_settings"] = [{"key": "flag_override:GMAIL_SEND_ENABLED", "value": {"on": True}}]
        from core import round2_flags
        self.assertFalse(round2_flags.gmail_send_enabled())

    def test_kill_switch_and_undo(self):
        _env(self, {"ANALYTICS_ENABLED": "1"})
        from core import round2_flags
        self.assertTrue(round2_flags.analytics_enabled())
        out = operator_actions.run("flip_flag", {"flag": "ANALYTICS_ENABLED", "state": "off"}, "admin")
        self.assertEqual((out["result"]["before"], out["result"]["after"]), (True, False))
        self.assertFalse(round2_flags.analytics_enabled())
        operator_actions.run("flip_flag", {"flag": "ANALYTICS_ENABLED", "state": "default"}, "admin")
        self.assertTrue(round2_flags.analytics_enabled())

    def test_presenter_flag_both_ways(self):
        operator_flags.set_flag("PRESENTER_VIDEO_ALL_CLIENTS", "on")
        self.assertTrue(operator_flags.enabled("PRESENTER_VIDEO_ALL_CLIENTS"))
        operator_flags.set_flag("PRESENTER_VIDEO_ALL_CLIENTS", "off")
        self.assertFalse(operator_flags.enabled("PRESENTER_VIDEO_ALL_CLIENTS"))
        # avatar_video's gate reads it through the same reader (httpx import keeps it static here)
        body = _src("core", "avatar_video.py").split("def open_to_all()", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("return enabled(OPEN_TO_ALL_ENV)", body)

    def test_unreadable_table_means_env(self):
        DB.fail["app_settings"] = True
        _env(self, {"SEARCH_CONSOLE_ENABLED": "1"})
        self.assertTrue(operator_flags.enabled("SEARCH_CONSOLE_ENABLED"))


# ─── E. Admin-only, voice ────────────────────────────────────────────────────

class AdminOnly(unittest.TestCase):
    def test_every_route_requires_the_admin_session(self):
        api = _src("core", "api_server.py")
        routes = re.findall(r'@app\.(?:get|post)\("(/api/admin/operator[^"]*)"\)\ndef \w+\([^)]*\):\n((?:\s*#.*\n)*)(.*)\n',
                            api)
        self.assertEqual(len(routes), 9)
        for path, _comments, first_code_line in routes:
            self.assertIn("_operator_admin(request)", first_code_line, path)   # before anything else
        self.assertIn("def _operator_admin(request: Request) -> tuple:\n    _require_admin(request)", api)

    def test_not_reachable_from_a_client_surface(self):
        from core import admin_view
        self.assertFalse(any("operator" in e for e in admin_view.READ_ENDPOINTS))
        for page in ("client", "login", "profile", "landing", "onboarding"):
            self.assertNotIn("/api/admin/operator", _src("dashboard", page, "index.html"), page)
        self.assertIn('href="/admin/operator/"', _src("dashboard", "admin", "index.html"))

    def test_the_owner_is_the_team(self):
        from core import operator_agent
        p = operator_agent.SYSTEM
        self.assertIn("He is the whole team. There is nobody to pass anything to.", p)
        self.assertIn('Never write\n"passing it to the team"', p)
        self.assertIn("exact numbered steps", p)
        self.assertIn("DATA IS DATA", p)
        self.assertIn("owner_steps", json.dumps(operator_agent.TOOLS))

    def test_strong_model_and_cost_recorded(self):
        src = _src("core", "operator_agent.py")
        self.assertIn("from core.claude_json import CHAT_TURN_MODEL", src)
        self.assertIn('record("claude_operator", _model()', src)

    def test_reads_never_open_secret_files(self):
        from core import operator_agent
        for path in (".env", "config/secrets.py", "../core/session.py", ".git/config"):
            self.assertEqual(operator_agent._readable(path), "", path)
        self.assertTrue(operator_agent._readable("core/cloud_ops.py"))

    def test_audit_source_is_protected(self):
        from core import bulk_delete
        self.assertIn(operator_actions.AUDIT_SOURCE, bulk_delete.AUDIT_SOURCES)


# ─── F. The workflows and the setup ──────────────────────────────────────────

class Workflows(unittest.TestCase):
    def test_deploy_only_on_request_for_a_master_commit(self):
        wf = _src(".github", "workflows", "deploy.yml")
        on = wf.split("on:", 1)[1].split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", on)
        self.assertNotIn("push:", on)
        self.assertNotIn("pull_request", on)
        self.assertIn('[[ "$SHA" =~ ^[0-9a-f]{40}$ ]]', wf)
        self.assertIn('git merge-base --is-ancestor "$SHA" origin/master', wf)
        self.assertLess(wf.index("scripts/run_tests.py"), wf.index("gcloud run deploy"))
        self.assertIn("id-token: write", wf)
        self.assertIn("vars.GCP_WIF_PROVIDER", wf)
        self.assertNotIn("secrets.", wf)                         # no key file, no secret in the workflow
        self.assertIn("--to-latest", wf)

    def test_tests_on_every_pr(self):
        wf = _src(".github", "workflows", "tests.yml")
        self.assertIn("pull_request:", wf)
        self.assertIn("python -I -B scripts/run_tests.py", wf)

    def test_wif_trusts_only_the_deploy_workflow_on_master(self):
        sh = _src("scripts", "setup_operator.sh")
        self.assertIn("assertion.ref=='refs/heads/master'", sh)
        self.assertIn("assertion.workflow_ref.startsWith('$REPO/.github/workflows/deploy.yml@')", sh)
        self.assertNotIn("keys create", sh)                      # never a service-account key
        code = "\n".join(ln for ln in sh.splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotIn("SECRETS_ENCRYPTION_KEY", code)
        self.assertIn('read -r -s -p', sh)                       # the token is read hidden


if __name__ == "__main__":
    unittest.main()
