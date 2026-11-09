"""core/tiktok_uploads: the durable, resumable Drive → TikTok-inbox job, the
per-client lease, progress lines, retries → our-side alert, and the owner's
approval for files above `tiktok_auto_upload_max_mb`.

Stdlib only. app_settings is an in-memory table; the TikTok agent
(begin_upload / delivery_status), the chunk PUT and Drive reads are stubs.
No real TikTok, no real Drive.

Run from the repo root:  python tests/test_tiktok_uploads.py -v
"""
import os
import sys
import time
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import tiktok_uploads as tu   # noqa: E402
from core import video_limits as vl     # noqa: E402

MiB = 1024 * 1024


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ─── app_settings in memory (the query shapes tiktok_uploads uses) ──────────

class _Q:
    def __init__(self, db):
        self.db, self.op, self.filters, self.payload = db, "select", [], None

    def select(self, *_a):
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", row
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        if col == "value->>token":
            self.filters.append(lambda r, v=val: (r.get("value") or {}).get("token") == v)
        else:
            self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def like(self, col, pattern):
        prefix = pattern.rstrip("%")
        self.filters.append(lambda r, c=col, p=prefix: str(r.get(c)).startswith(p))
        return self

    def limit(self, _n):
        return self

    def execute(self):
        rows = self.db.rows
        if self.op == "insert":
            if any(r["key"] == self.payload["key"] for r in rows):
                raise RuntimeError("duplicate key value violates unique constraint")
            rows.append(dict(self.payload))
            return types.SimpleNamespace(data=[dict(self.payload)])
        if self.op == "upsert":
            for r in rows:
                if r["key"] == self.payload["key"]:
                    r.update(self.payload)
                    return types.SimpleNamespace(data=[dict(r)])
            rows.append(dict(self.payload))
            return types.SimpleNamespace(data=[dict(self.payload)])
        matched = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "delete":
            for r in matched:
                rows.remove(r)
            return types.SimpleNamespace(data=matched)
        import copy
        return types.SimpleNamespace(data=copy.deepcopy(matched))


class DB:
    def __init__(self):
        self.rows = []

    def table(self, _name):
        return _Q(self)


class UploadRefused(Exception):
    def __init__(self, code, errors, **extra):
        super().__init__("; ".join(errors))
        self.code, self.errors, self.extra = code, errors, extra


class Agent:
    """Stands in for agents.tiktok_content_agent."""
    UploadRefused = UploadRefused

    def __init__(self):
        self.inits, self.status, self.refuse, self.fail_init = 0, ["SEND_TO_USER_INBOX"], None, 0

    def begin_upload(self, client_id, file_id):
        if self.refuse:
            raise UploadRefused(self.refuse, ["explained"])
        if self.fail_init:
            self.fail_init -= 1
            raise RuntimeError("init 500")
        self.inits += 1
        size = int(tu_test.size)
        chunk = vl.tiktok_chunk_size(size)
        return {"publish_id": f"p{self.inits}", "upload_url": f"https://upload.example/{self.inits}",
                "size": size, "chunk_size": chunk, "chunks": len(vl.tiktok_chunk_plan(size, chunk)),
                "mime": "video/mp4", "video": {}, "warnings": [], "access_token": "never-stored"}

    def delivery_status(self, client_id, publish_id):
        state = self.status.pop(0) if len(self.status) > 1 else self.status[0]
        if state == "SEND_TO_USER_INBOX":
            return "done", state
        if state == "FAILED":
            return "failed", "file_format_check_failed"
        return "processing", state


class tu_test:
    size = 40 * MiB


class Case(unittest.TestCase):
    def setUp(self):
        self.db = DB()
        self.agent = Agent()
        self.puts, self.fail_puts, self.said, self.alerts, self.finished, self.emails = [], 0, [], [], [], []
        self.saved = {n: getattr(tu, n) for n in ("_db", "_alert", "_log", "_say", "_client_name")}
        tu._db = lambda: self.db
        tu._alert = lambda issue: self.alerts.append(issue)
        tu._log = lambda *a, **k: None
        tu._say = lambda job, text: self.said.append(text)
        tu._client_name = lambda cid: "בית קפה"
        self.mods = {n: sys.modules.get(n) for n in ("agents.tiktok_content_agent", "core.tiktok_service",
                                                       "core.drive_service", "core.agent_actions",
                                                       "core.email_service", "agents.client_agent")}
        import core
        self.core_attrs = {n: getattr(core, n, None) for n in ("tiktok_service", "drive_service", "agent_actions",
                                                                 "email_service")}

        def upload_chunk(url, read, size, index, plan, mime="video/mp4", _sleep=None):
            if self.fail_puts:
                self.fail_puts -= 1
                raise RuntimeError("chunk failed after 3 retries")
            start, length = plan[index]
            data = read(start, length)
            assert len(data) == length
            self.puts.append((url, index, length))
            return 206 if index < len(plan) - 1 else 201

        tiktok = types.SimpleNamespace(upload_chunk=upload_chunk)
        drive = types.SimpleNamespace(read_range=lambda fid, start, length: b"\0" * length)
        actions = types.SimpleNamespace(
            finish_deferred=lambda pid, status, result=None, code="": self.finished.append((pid, status, code)) or True,
            ACTIONS={"send_tiktok_to_inbox": {"done_message": lambda result, lang: f"done {result.get('source_file')}",
                                              "refusals": {"needs_reconnect": {"he": "לחבר מחדש"}}}},
            _pick=lambda texts, lang: texts.get(lang) or texts["he"],
            _post_client_message=lambda *a: None)
        email = types.SimpleNamespace(send_owner_action=lambda s, t, b: self.emails.append((s, t, b)) or True)
        client_agent = types.SimpleNamespace(log_activity=lambda *a, **k: None, get_client=lambda cid: {})
        for name, mod in (("agents.tiktok_content_agent", self.agent), ("core.tiktok_service", tiktok),
                          ("core.drive_service", drive), ("core.agent_actions", actions),
                          ("core.email_service", email), ("agents.client_agent", client_agent)):
            sys.modules[name] = mod
            if name.startswith("core."):
                setattr(core, name.split(".")[1], mod)
        import agents
        self.agents_attrs = {n: getattr(agents, n, None) for n in ("tiktok_content_agent", "client_agent")}
        agents.tiktok_content_agent, agents.client_agent = self.agent, client_agent
        tu_test.size = 40 * MiB

    def tearDown(self):
        for n, fn in self.saved.items():
            setattr(tu, n, fn)
        import core
        import agents
        for name, mod in self.mods.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        for n, v in self.core_attrs.items():
            if v is None:
                if hasattr(core, n):
                    delattr(core, n)
            else:
                setattr(core, n, v)
        for n, v in self.agents_attrs.items():
            if v is None:
                if hasattr(agents, n):
                    delattr(agents, n)
            else:
                setattr(agents, n, v)

    def job(self, size=None, **kw):
        tu_test.size = size or tu_test.size
        return tu.create(27, "1FileIdAbcdef", "promo.mp4", tu_test.size, proposal_id=kw.get("proposal_id", 5))

    def run_all(self, rounds=20):
        for _ in range(rounds):
            tu.step_client(27, time.monotonic() + 1000)
            j = tu.load(27)[-1]
            if j["status"] not in tu.ACTIVE or j.get("retry_at"):
                return j
        return tu.load(27)[-1]


class Job(Case):
    def test_small_file_one_chunk_then_done_through_the_card(self):
        self.job(size=3 * MiB)
        j = self.run_all()
        self.assertEqual(j["status"], "done")
        self.assertEqual([(i, n) for _, i, n in self.puts], [(0, 3 * MiB)])
        self.assertEqual(self.finished, [(5, "done", "")])
        self.assertIsNone(j.get("upload_url"))                     # dropped once sent

    def test_chunks_follow_tiktoks_plan_and_resume_from_the_saved_chunk(self):
        self.job(size=100 * MiB)                                   # 6 chunks of 16 MB, last 20 MB
        # first step: budget for exactly two chunks, then the request "ends"
        calls = {"n": 0}
        real = time.monotonic
        start = real()

        def clock():
            calls["n"] += 1
            return start if self.puts.__len__() < 2 else start + 10 ** 6
        tu.time.monotonic = clock
        try:
            tu.step_client(27, start + tu.CHUNK_MARGIN_S + 100)
        finally:
            tu.time.monotonic = real
        j = tu.load(27)[-1]
        self.assertEqual((j["status"], j["next"]), ("uploading", 2))
        # the next request resumes at chunk 2 on the SAME session (no new init)
        j = self.run_all()
        self.assertEqual(j["status"], "done")
        self.assertEqual([i for _, i, _ in self.puts], [0, 1, 2, 3, 4, 5])
        self.assertEqual(self.agent.inits, 1)
        self.assertEqual(sum(n for _, _, n in self.puts), 100 * MiB)

    def test_memory_is_one_chunk_whatever_the_size(self):
        tu_test.size = 3 * 1024 * MiB
        plan = vl.tiktok_chunk_plan(tu_test.size)
        self.assertLessEqual(max(n for _, n in plan), 2 * vl.TIKTOK_CHUNK_BYTES)
        src = _read(os.path.join(ROOT, "core", "tiktok_uploads.py"))
        self.assertNotIn("download_file", src)

    def test_progress_lines_for_a_big_file_no_tool_names(self):
        self.job(size=400 * MiB)
        self.run_all(rounds=40)
        progress = [s for s in self.said if "%" in s]
        self.assertEqual(len(progress), 3)                          # 25 / 50 / 75, once each
        for line in progress:
            self.assertNotRegex(line, r"(?i)tiktok|drive|api|chunk|error")
        self.assertEqual(tu.load(27)[-1]["status"], "done")

    def test_no_progress_lines_for_a_small_file(self):
        self.job(size=40 * MiB)
        self.run_all()
        self.assertEqual([s for s in self.said if "%" in s], [])

    def test_a_failing_chunk_is_retried_later_then_restarted_then_ours(self):
        self.job(size=40 * MiB)
        self.fail_puts = 100
        for _ in range(30):
            j = tu.load(27)[-1]
            if j["status"] not in tu.ACTIVE:
                break
            tu._update(27, j["id"], retry_at=None)                 # skip the waiting in the test
            tu.step_client(27, time.monotonic() + 1000)
        j = tu.load(27)[-1]
        self.assertEqual(j["status"], "failed")
        self.assertEqual(len(self.alerts), 1)                      # ours: ONE alert
        self.assertIn("client 27", self.alerts[0])
        self.assertEqual(self.finished, [(5, "failed", "in_hand")])  # the card's calm line
        self.assertGreaterEqual(self.agent.inits, 2)               # a fresh session was tried

    def test_a_transient_failure_recovers(self):
        self.job(size=40 * MiB)
        self.fail_puts = 1
        tu.step_client(27, time.monotonic() + 1000)
        j = tu.load(27)[-1]
        self.assertEqual((j["status"], j["attempts"]), ("uploading", 1))
        self.assertTrue(j.get("retry_at"))
        self.assertFalse(tu._due(j))                               # waits for its backoff
        tu._update(27, j["id"], retry_at=None)
        self.assertEqual(self.run_all()["status"], "done")
        self.assertEqual(self.alerts, [])

    def test_explained_refusal_goes_to_the_card(self):
        self.agent.refuse = "needs_reconnect"
        self.job()
        j = self.run_all()
        self.assertEqual(j["status"], "refused")
        self.assertEqual(self.finished, [(5, "failed", "needs_reconnect")])
        self.assertEqual(self.alerts, [])

    def test_tiktok_processing_failure_is_ours_not_the_clients(self):
        self.agent.status = ["FAILED"]
        self.job(size=3 * MiB)
        j = self.run_all()
        self.assertEqual(j["status"], "failed")
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.finished, [(5, "failed", "in_hand")])

    def test_an_old_upload_session_starts_over(self):
        self.job(size=40 * MiB)
        j = tu.load(27)[-1]
        old = (datetime.now(timezone.utc) - timedelta(minutes=55)).isoformat()
        tu._update(27, j["id"], status="uploading", publish_id="p0", upload_url="https://upload.example/0",
                   init_at=old, chunk_size=16 * MiB, chunks=2, next=1, mime="video/mp4")
        self.assertEqual(self.run_all()["status"], "done")
        self.assertEqual(self.agent.inits, 1)                      # re-init, then a full upload
        self.assertNotIn("https://upload.example/0", [u for u, _, _ in self.puts])

    def test_lease_one_worker_per_client(self):
        token = tu._lease_take(27)
        self.assertTrue(token)
        self.assertEqual(tu._lease_take(27), "")
        self.job()
        self.assertEqual(tu.step_client(27, time.monotonic() + 1000), 0)   # held: nothing runs
        tu._lease_release(27, token)
        self.assertTrue(tu._lease_take(27))

    def test_an_expired_lease_is_taken_over(self):
        self.db.rows.append({"key": "tiktok_upload_lock:27", "value": {
            "token": "dead", "until": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}})
        self.assertTrue(tu._lease_take(27))

    def test_scheduler_tick_runs_due_jobs_only(self):
        self.assertEqual(tu.run_due(5)["steps"], 0)
        self.job(size=3 * MiB)
        self.assertGreaterEqual(tu.run_due(1000)["steps"], 1)
        self.assertEqual(tu.load(27)[-1]["status"], "done")

    def test_admin_view_never_shows_the_upload_url(self):
        self.job()
        j = tu.load(27)[-1]
        tu._update(27, j["id"], upload_url="https://secret.example/u")
        self.assertNotIn("upload_url", tu.admin_view("all")[0])


class OwnerApproval(Case):
    def test_one_request_per_file_email_with_buttons(self):
        first = tu.request_owner_approval(27, "1HugeFileAbcd", "wedding.mp4", 2048 * MiB)
        second = tu.request_owner_approval(27, "1HugeFileAbcd", "wedding.mp4", 2048 * MiB)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.emails), 1)
        subject, text, buttons = self.emails[0]
        self.assertIn("2048MB", subject)
        self.assertIn("wedding.mp4", text)
        self.assertIn("בית קפה", text)
        self.assertEqual([label for label, _ in buttons], ["אישור", "דחייה"])
        self.assertTrue(all("/admin/?tiktok_upload=27:" in url for _, url in buttons))
        self.assertEqual(len(tu.pending_approvals()), 1)
        self.assertEqual(tu.pending_approvals()[0]["client_name"], "בית קפה")

    def test_awaiting_is_never_run(self):
        tu.request_owner_approval(27, "1HugeFileAbcd", "wedding.mp4", 2048 * MiB)
        self.assertEqual(tu.run_due(1000)["steps"], 0)
        self.assertEqual(self.agent.inits, 0)

    def test_approve_is_final_uploads_with_no_cap_and_tells_the_client_once(self):
        tu_test.size = 2048 * MiB
        job = tu.request_owner_approval(27, "1HugeFileAbcd", "wedding.mp4", tu_test.size)
        self.assertEqual(tu.decide(27, job["id"], "approve", "owner")["job"]["status"], "queued")
        self.assertEqual(tu.decide(27, job["id"], "approve", "owner")["error"], "already_decided")
        j = self.run_all(rounds=60)
        self.assertEqual(j["status"], "done")
        self.assertEqual(sum(n for _, _, n in self.puts), 2048 * MiB)
        self.assertEqual(self.finished, [])                       # no card: posted directly
        self.assertEqual([s for s in self.said if s.startswith("done")], ["done wedding.mp4"])
        self.assertEqual(j["approved_by"], "owner")

    def test_decline_tells_the_client_plainly_with_the_next_step(self):
        job = tu.request_owner_approval(27, "1HugeFileAbcd", "wedding.mp4", 2048 * MiB)
        self.assertEqual(tu.decide(27, job["id"], "decline", "owner")["job"]["status"], "declined")
        self.assertEqual(len(self.said), 1)
        line = self.said[0]
        self.assertIn("wedding.mp4", line)
        self.assertIn("החלטנו", line)
        self.assertIn("גרסה קצרה או דחוסה", line)
        self.assertNotRegex(line, r"(?i)MB|GB|error|שגיאה|tiktok")
        self.assertEqual(tu.run_due(1000)["steps"], 0)

    def test_bad_decision_and_unknown_job(self):
        self.assertEqual(tu.decide(27, "nope", "approve", "o")["error"], "not_found")
        job = tu.request_owner_approval(27, "1HugeFileAbcd", "x.mp4", 2048 * MiB)
        self.assertEqual(tu.decide(27, job["id"], "maybe", "o")["error"], "bad_decision")


class Policy(unittest.TestCase):
    def test_threshold_is_one_admin_setting_default_1536(self):
        from core import admin_service
        self.assertEqual(admin_service.DEFAULT_SETTINGS["tiktok_auto_upload_max_mb"], 1536)
        self.assertEqual(vl.TIKTOK_AUTO_UPLOAD_MAX_MB_DEFAULT, 1536)
        saved = admin_service.get_setting
        try:
            admin_service.get_setting = lambda key, default=None, ttl=60: 1536
            self.assertFalse(vl.tiktok_needs_owner_approval(1536 * MiB))
            self.assertTrue(vl.tiktok_needs_owner_approval(1536 * MiB + 1))
            admin_service.get_setting = lambda key, default=None, ttl=60: "bad"
            self.assertEqual(vl.tiktok_auto_upload_max_mb(), 1536)
        finally:
            admin_service.get_setting = saved

    def test_no_always_on_and_scheduler_job_registered(self):
        script = _read(os.path.join(ROOT, "scripts", "schedule_jobs.sh"))
        self.assertIn('job tiktok-uploads GET "* * * * *" "/api/tiktok/uploads/run" active', script)
        api = _read(os.path.join(ROOT, "core", "api_server.py"))
        self.assertIn('@app.get("/api/tiktok/uploads/run", dependencies=_admin_only)', api)
        for path in ("scripts/schedule_jobs.sh", "core/tiktok_uploads.py", "CLAUDE.md"):
            text = _read(os.path.join(ROOT, path))
            # no deploy command anywhere turns on always-on CPU or min instances
            self.assertNotRegex(text, r"gcloud run [^\n]*(--no-cpu-throttling|--min-instances)")

    def test_tick_budget_fits_the_request_timeout(self):
        self.assertLess(tu.TICK_BUDGET_S + tu.CHUNK_MARGIN_S, 290)

    def test_per_client_keys_are_cleaned_and_hidden(self):
        deletion = _read(os.path.join(ROOT, "core", "client_deletion.py"))
        self.assertIn("tiktok_upload:", deletion)
        self.assertIn("tiktok_upload_lock:", deletion)
        admin = _read(os.path.join(ROOT, "core", "admin_service.py"))
        self.assertIn('"tiktok_upload:", "tiktok_upload_lock:"', admin)


if __name__ == "__main__":
    unittest.main()
