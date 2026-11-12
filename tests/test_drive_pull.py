"""Pull from Drive + find by name + why a dropped file hid (2026-11-11,
docs/DRIVE-PULL.md).

A. core/drive_pull: parse a pasted link, browse as the service account (a
   folder we can't open = ONE calm line with the address, never an error),
   start refusals, the durable job (expand folders, copy INSIDE Drive into
   videos/images/documents, idempotent via appProperties, retries, per-file
   result, usage vs the alert after the job), the lease.
B. core/drive_mirror: the WHOLE tree (newest + find by name, Hebrew prefix
   letters, shortcuts left out), the folder resolution fixes (no cached "",
   a stale hint is not trusted), the admin diagnostic.
C. core/agent_actions find_media_file / media_file_not_found: one match →
   the real card, none → ONE card, several → ONE choice card; no tool names
   in what the client reads.
D. Wiring: admin-only routes, deletion clears the keys, media_agent reuses
   the folder the readers resolve.

Stdlib only; Drive is an in-memory tree. Nothing here talks to Google.
Run from the repo root:  python tests/test_drive_pull.py -v
"""
import ast
import copy
import os
import re
import sys
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
SA = "uallak-drive@example-project.iam.gserviceaccount.com"


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class Stubs:
    def __init__(self):
        self.saved = []

    def put(self, name, module):
        self.saved.append((name, sys.modules.get(name)))
        sys.modules[name] = module
        if "." in name:
            parent, attr = name.rsplit(".", 1)
            if parent in sys.modules:
                self.saved.append((f"{parent}:{attr}", getattr(sys.modules[parent], attr, None)))
                setattr(sys.modules[parent], attr, module)

    def undo(self):
        for name, value in reversed(self.saved):
            if ":" in name:
                parent, attr = name.split(":")
                if value is None:
                    if hasattr(sys.modules.get(parent), attr):
                        delattr(sys.modules[parent], attr)
                else:
                    setattr(sys.modules[parent], attr, value)
            elif value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
        self.saved.clear()


# ─── app_settings / client_activity in memory ───────────────────────────────

class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.filters, self.payload = db, table, "select", [], None

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

    def order(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            if any(r.get("key") == self.payload.get("key") for r in rows):
                raise RuntimeError("duplicate key value violates unique constraint")
            rows.append(dict(self.payload))
            return types.SimpleNamespace(data=[dict(self.payload)])
        if self.op == "upsert":
            for r in rows:
                if r["key"] == self.payload["key"]:
                    r.update(copy.deepcopy(self.payload))
                    return types.SimpleNamespace(data=[dict(r)])
            rows.append(copy.deepcopy(self.payload))
            return types.SimpleNamespace(data=[dict(self.payload)])
        matched = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "delete":
            for r in matched:
                rows.remove(r)
            return types.SimpleNamespace(data=matched)
        return types.SimpleNamespace(data=copy.deepcopy(matched))


class DB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _Q(self, name)


# ─── Drive in memory ────────────────────────────────────────────────────────

class DriveError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class FakeDrive:
    """The drive_service calls the code under test makes, on a dict of files."""

    def __init__(self):
        self.files, self.unshared, self.copies, self.fail_copy, self.n = {}, set(), [], [], 0

    def add(self, fid, name, mime, parent, size=None, modified="2026-11-01T10:00:00Z", **extra):
        self.files[fid] = {"id": fid, "name": name, "mimeType": mime, "parents": [parent] if parent else [],
                           "size": str(size) if size is not None else None, "modifiedTime": modified,
                           "createdTime": modified, "trashed": False, **extra}

    def _visible(self, fid):
        f = self.files.get(fid)
        if not f or fid in self.unshared:
            raise DriveError("file_not_found")
        return f

    def classify_error(self, e, target="folder"):
        return getattr(e, "code", "other")

    def service_account_email(self):
        return SA

    def get_meta(self, fid, fields=""):
        f = copy.deepcopy(self._visible(fid))
        f["capabilities"] = {"canCopy": f.pop("can_copy", True)}
        return f

    def get_link(self, fid):
        return f"https://drive.google.com/drive/folders/{fid}"

    def list_children(self, folder_id, page_token="", page_size=40, fields=""):
        self._visible(folder_id)
        kids = [copy.deepcopy(f) for f in self.files.values()
                if folder_id in f["parents"] and not f["trashed"]]
        return {"files": kids, "next_page_token": ""}

    def search_in_folders(self, folder_ids, name_words=None, mime_prefix="", order_by="", page_size=50, fields=""):
        def word_hit(name, word):     # Drive: `name contains` = a word of the name starts with it
            return any(t.startswith(word) for t in re.findall(r"\w+", name.lower()))
        out = [copy.deepcopy(f) for f in self.files.values()
               if set(f["parents"]) & set(folder_ids) and not f["trashed"] and f["mimeType"] != FOLDER
               and (not mime_prefix or mime_prefix in f["mimeType"])
               and all(word_hit(f["name"], w) for w in (name_words or []))]
        return sorted(out, key=lambda f: f["modifiedTime"], reverse=True)[:page_size]

    def list_in_folders(self, folder_ids, max_items=2000):
        out = [copy.deepcopy(f) for f in self.files.values()
               if set(f["parents"]) & set(folder_ids) and not f["trashed"] and f["mimeType"] != FOLDER]
        return sorted(out, key=lambda f: f["modifiedTime"], reverse=True)[:max_items]

    def find_child_folders_by_prefix(self, parent, prefix):
        return sorted((copy.deepcopy(f) for f in self.files.values()
                       if parent in f["parents"] and f["mimeType"] == FOLDER and f["name"].startswith(prefix)),
                      key=lambda f: f["createdTime"])

    def find_folders_named(self, prefix, page_size=20):
        return [dict(copy.deepcopy(f), webViewLink=self.get_link(f["id"])) for f in self.files.values()
                if f["mimeType"] == FOLDER and f["name"].startswith(prefix)]

    def find_by_app_property(self, parent, key, value):
        return [copy.deepcopy(f) for f in self.files.values()
                if parent in f["parents"] and (f.get("appProperties") or {}).get(key) == value]

    def copy_file(self, fid, parent, name="", app_properties=None):
        if self.fail_copy:
            raise DriveError(self.fail_copy.pop(0))
        src = self._visible(fid)
        self.n += 1
        new = f"copy{self.n}"
        self.add(new, name or src["name"], src["mimeType"], parent, size=src["size"],
                 appProperties=dict(app_properties or {}))
        self.copies.append((fid, parent))
        return {"id": new, "name": name, "parents": [parent]}


DRIVE_FUNCS = ("classify_error", "service_account_email", "get_meta", "get_link", "list_children",
               "search_in_folders", "list_in_folders", "find_child_folders_by_prefix", "find_folders_named",
               "find_by_app_property", "copy_file")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stubs = Stubs()
        cls.stubs.put("httpx", _module("httpx", get=None, post=None, put=None, request=None, Response=object,
                                       HTTPStatusError=Exception, TransportError=Exception,
                                       Timeout=lambda *a, **k: None, Client=object))
        cls.stubs.put("agents.keys_agent", _module("agents.keys_agent", get_key=lambda k: ""))
        cls.stubs.put("core.secrets_vault", _module("core.secrets_vault", get_client_secret=lambda *a, **k: ""))
        cls.stubs.put("core.google_ads_service", _module("core.google_ads_service", _access_token=lambda rt: ""))
        import agents  # noqa: F401
        cls.media = _module("agents.media_agent", _folder_cache={}, AGENT_NAME="media_agent")
        cls.stubs.put("agents.media_agent", cls.media)
        cls.client_agent = _module("agents.client_agent")
        cls.stubs.put("agents.client_agent", cls.client_agent)
        cls.stubs.put("core.deletion_guard", _module("core.deletion_guard", is_deleting=lambda cid: False))
        cls.state = _module("core.client_state", forget=lambda cid: None,
                            _media_folder=lambda cid, fresh=False: {"videos": [], "images": []})
        cls.stubs.put("core.client_state", cls.state)
        for name in ("core.drive_service", "core.drive_storage", "core.drive_mirror", "core.drive_download",
                     "core.drive_pull"):
            sys.modules.pop(name, None)
        import core
        for attr in ("drive_service", "drive_storage", "drive_mirror", "drive_download", "drive_pull"):
            if hasattr(core, attr):
                delattr(core, attr)
        from core import drive_mirror, drive_pull, drive_service, drive_storage
        cls.dm, cls.dp, cls.ds, cls.drv = drive_mirror, drive_pull, drive_storage, drive_service
        cls._real = {n: getattr(drive_service, n) for n in DRIVE_FUNCS}
        cls._real_scan = drive_storage.shared_usage_scan

    @classmethod
    def tearDownClass(cls):
        for n, fn in cls._real.items():
            setattr(cls.drv, n, fn)
        cls.ds.shared_usage_scan = cls._real_scan
        cls.stubs.undo()
        for name in ("core.drive_service", "core.drive_storage", "core.drive_mirror", "core.drive_download",
                     "core.drive_pull"):
            sys.modules.pop(name, None)
        import core
        for attr in ("drive_service", "drive_storage", "drive_mirror", "drive_download", "drive_pull"):
            if hasattr(core, attr):
                delattr(core, attr)

    def setUp(self):
        os.environ["DRIVE_MEDIA_FOLDER_ID"] = "MEDIA"
        os.environ.pop("DRIVE_FILE_ENABLED", None)
        self.ds._location_cache.clear()
        self.db = DB()
        self.fake = d = FakeDrive()
        for n in DRIVE_FUNCS:
            setattr(self.drv, n, getattr(d, n))
        # our Shared Drive: client 7's folder, its subfolders, a deep tiktok one
        d.add("MEDIA", "uallak media", FOLDER, "")
        d.add("C7", "client-7 — Cafe", FOLDER, "MEDIA", modified="2026-01-01T00:00:00Z")
        for fid, name, parent in (("V7", "videos", "C7"), ("I7", "images", "C7"), ("D7", "documents", "C7"),
                                  ("T7", "tiktok", "V7")):
            d.add(fid, name, FOLDER, parent)
        d.add("v_root", "intro.mp4", "video/mp4", "C7", 10, "2026-11-01T09:00:00Z")
        d.add("v_deep", "launch final.mp4", "video/mp4", "T7", 20, "2026-11-10T09:00:00Z")
        d.add("v_prod", "מוצר חדש.mp4", "video/mp4", "V7", 30, "2026-11-05T09:00:00Z")
        d.add("sc", "launch copy", SHORTCUT, "V7", shortcutDetails={"targetId": "OWNERFILE_A", "targetMimeType": "video/mp4"})
        # the owner's own folder, shared with the service account
        d.add("SRCFOLDER01", "for cafe", FOLDER, "OWNERDRIVE")
        d.add("OWNERFILE_A", "a.mp4", "video/mp4", "SRCFOLDER01", 100)
        d.add("OWNERFILE_B", "b.jpg", "image/jpeg", "SRCFOLDER01", 5)
        d.add("SUBFOLDER01", "docs", FOLDER, "SRCFOLDER01")
        d.add("OWNERFILE_C", "c.pdf", "application/pdf", "SUBFOLDER01", 1)
        d.add("PRIVATE0001", "not shared", FOLDER, "OWNERDRIVE")
        d.unshared.add("PRIVATE0001")
        self.media._folder_cache.clear()
        self.media._db = lambda: self.db
        self.media._subfolder = lambda cid, name: {"videos": "V7", "images": "I7", "documents": "D7"}[name]
        self.client_agent._db = lambda: self.db
        self.client_agent.get_client = lambda cid: {"id": cid, "name": "Cafe"} if cid == 7 else {}
        self.dm.forget(7)
        self.dm._root_cache.clear()
        self.scans = []
        self.ds.shared_usage_scan = lambda ids: self.scans.append(ids) or [
            {"client_id": ids[0], "bytes": 3 * 1024 ** 3, "complete": True, "over_threshold": False}]
        self.saved_retry = self.dp.RETRY_AFTER_S
        self.dp.RETRY_AFTER_S = (0, 0, 0)
        self.dp._log = lambda *a, **k: None

    def tearDown(self):
        self.dp.RETRY_AFTER_S = self.saved_retry
        os.environ.pop("DRIVE_MEDIA_FOLDER_ID", None)

    def run_job(self, client_id=7, rounds=10):
        out = {}
        for _ in range(rounds):
            out = self.dp.step(client_id, budget_s=60)
            if out.get("status") not in ("queued", "running"):
                break
        return out


# ─── A. drive_pull ──────────────────────────────────────────────────────────

class Source(Base):
    def test_parse_ref_takes_every_link_shape(self):
        p = self.dp.parse_ref
        self.assertEqual(p("https://drive.google.com/drive/folders/1AbCdEfGhIjK?usp=sharing"), "1AbCdEfGhIjK")
        self.assertEqual(p("https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjK"), "1AbCdEfGhIjK")
        self.assertEqual(p("https://drive.google.com/file/d/1XyZ_-123456/view"), "1XyZ_-123456")
        self.assertEqual(p("https://drive.google.com/open?id=1XyZ_-123456"), "1XyZ_-123456")
        self.assertEqual(p("  1XyZ_-123456  "), "1XyZ_-123456")
        self.assertEqual(p("hello there"), "")
        self.assertEqual(p(""), "")

    def test_not_shared_is_one_calm_line_with_the_address(self):
        out = self.dp.browse("https://drive.google.com/drive/folders/PRIVATE0001")
        self.assertTrue(out["not_shared"])
        self.assertEqual(out["share_with"], SA)
        self.assertIn(SA, out["text"])
        self.assertIn("לא את כל ה-Drive", out["text"])
        for raw in ("404", "403", "error", "Error", "file_not_found"):
            self.assertNotIn(raw, out["text"])

    def test_bad_link_and_drive_trouble_are_lines_not_exceptions(self):
        self.assertEqual(self.dp.browse("not a link")["error"], "bad_ref")
        self.drv.get_meta = lambda *a, **k: (_ for _ in ()).throw(DriveError("other"))
        out = self.dp.browse("SRCFOLDER01")
        self.assertIn("text", out)
        self.assertNotIn("not_shared", out)

    def test_browse_lists_the_folder(self):
        out = self.dp.browse("https://drive.google.com/drive/folders/SRCFOLDER01")
        self.assertEqual(out["source"]["kind"], "folder")
        self.assertEqual({i["id"]: i["kind"] for i in out["items"]},
                         {"OWNERFILE_A": "video", "OWNERFILE_B": "image", "SUBFOLDER01": "folder"})

    def test_a_single_file_link_is_its_own_list(self):
        out = self.dp.browse("https://drive.google.com/file/d/OWNERFILE_A/view")
        self.assertEqual([i["id"] for i in out["items"]], ["OWNERFILE_A"])


class Start(Base):
    def test_refusals_are_calm_lines(self):
        cases = [(99, [{"id": "OWNERFILE_A", "kind": "file"}], "auto", "no_client"),
                 (7, [], "auto", "nothing"),
                 (7, [{"id": "x", "kind": "file"}], "auto", "nothing"),
                 (7, [{"id": "OWNERFILE_A", "kind": "file"}], "somewhere", "bad_target")]
        for cid, items, target, code in cases:
            with self.assertRaises(self.dp.PullRefused) as ctx:
                self.dp.start(cid, items, target)
            self.assertEqual(ctx.exception.code, code)
            self.assertTrue(ctx.exception.text)

    def test_one_active_job_per_client(self):
        self.dp.start(7, [{"id": "OWNERFILE_A", "kind": "file"}])
        with self.assertRaises(self.dp.PullRefused) as ctx:
            self.dp.start(7, [{"id": "OWNERFILE_B", "kind": "file"}])
        self.assertEqual(ctx.exception.code, "busy")

    def test_only_for_clients_in_our_shared_drive(self):
        saved = self.ds.location
        self.ds.location = lambda cid: {"kind": self.ds.CLIENT, "root": "R", "parent": "P"}
        try:
            with self.assertRaises(self.dp.PullRefused) as ctx:
                self.dp.start(7, [{"id": "OWNERFILE_A", "kind": "file"}])
            self.assertEqual(ctx.exception.code, "client_drive")
        finally:
            self.ds.location = saved


class Job(Base):
    def test_whole_folder_copied_inside_drive_by_type(self):
        self.dp.start(7, [{"id": "SRCFOLDER01", "kind": "folder"}])
        out = self.run_job()
        self.assertEqual(out["status"], "done")
        self.assertEqual(sorted(self.fake.copies), [("OWNERFILE_A", "V7"), ("OWNERFILE_B", "I7"), ("OWNERFILE_C", "D7")])
        self.assertEqual(out["counts"], {"copied": 3})
        self.assertTrue(all(f["label"] == "הועתק ✓" for f in out["files"]))
        # usage after the copies, against the existing alert
        self.assertEqual(self.scans, [[7]])
        self.assertEqual(out["usage"]["gb"], 3.0)
        self.assertIn("alert_gb", out["usage"])

    def test_chosen_target_wins_over_the_type(self):
        self.dp.start(7, [{"id": "OWNERFILE_B", "kind": "file"}], target="videos")
        self.run_job()
        self.assertEqual(self.fake.copies, [("OWNERFILE_B", "V7")])

    def test_rerun_never_copies_twice(self):
        self.dp.start(7, [{"id": "OWNERFILE_A", "kind": "file"}])
        self.run_job()
        self.dp.start(7, [{"id": "OWNERFILE_A", "kind": "file"}])
        out = self.run_job()
        self.assertEqual(len(self.fake.copies), 1)
        self.assertEqual(out["files"][0]["status"], "already")

    def test_transient_errors_retry_then_succeed_or_fail(self):
        self.fake.fail_copy = ["other", "other"]
        self.dp.start(7, [{"id": "OWNERFILE_A", "kind": "file"}])
        out = self.run_job()
        self.assertEqual(out["files"][0]["status"], "copied")
        self.assertEqual(out["files"][0]["attempts"], 2)
        self.fake.fail_copy = ["other"] * 3
        self.dp.start(7, [{"id": "OWNERFILE_B", "kind": "file"}])
        out = self.run_job()
        self.assertEqual(out["status"], "done_with_errors")
        self.assertEqual(out["files"][0]["status"], "failed")
        self.assertEqual(out["files"][0]["label"], "לא הועתק אחרי 3 ניסיונות")

    def test_a_file_we_cannot_open_is_named_not_retried(self):
        self.fake.unshared.add("OWNERFILE_B")
        self.dp.start(7, [{"id": "OWNERFILE_B", "kind": "file"}, {"id": "OWNERFILE_A", "kind": "file"}])
        out = self.run_job()
        status = {f["name"]: f["status"] for f in out["files"]}
        self.assertEqual(status["OWNERFILE_B"], "no_access")
        self.assertEqual(status["a.mp4"], "copied")
        self.assertEqual(out["status"], "done_with_errors")

    def test_copy_blocked_and_trashed_are_skipped(self):
        self.fake.files["OWNERFILE_A"]["can_copy"] = False
        self.fake.files["OWNERFILE_B"]["trashed"] = True
        self.dp.start(7, [{"id": "OWNERFILE_A", "kind": "file"}, {"id": "OWNERFILE_B", "kind": "file"}])
        out = self.run_job()
        self.assertEqual(sorted(f["status"] for f in out["files"]), ["copy_blocked", "skipped_trashed"])
        self.assertEqual(self.fake.copies, [])

    def test_a_shortcut_copies_its_target(self):
        self.fake.add("SHORTCUT_SRC", "pointer", SHORTCUT, "SRCFOLDER01",
                      shortcutDetails={"targetId": "OWNERFILE_C", "targetMimeType": "application/pdf"})
        self.dp.start(7, [{"id": "SHORTCUT_SRC", "kind": "file"}])
        self.run_job()
        self.assertEqual(self.fake.copies, [("OWNERFILE_C", "D7")])

    def test_progress_is_saved_and_the_lease_holds_one_worker(self):
        self.dp.start(7, [{"id": "OWNERFILE_A", "kind": "file"}])
        token = self.dp._lease_take(7)
        self.assertTrue(token)
        self.assertEqual(self.dp.step_client(7, time.monotonic() + 60), 0)     # someone else works on it
        self.dp._lease_release(7, token)
        self.assertEqual(self.dp.step_client(7, time.monotonic() + 60), 1)
        stored = self.dp.load(7)[-1]
        self.assertEqual(stored["files"][0]["status"], "copied")

    def test_run_due_moves_every_active_client(self):
        self.dp.start(7, [{"id": "OWNERFILE_A", "kind": "file"}])
        out = self.dp.run_due(60)
        self.assertEqual(out["clients_with_pulls"], 1)
        self.assertEqual(self.dp.latest(7)["status"], "done")

    def test_labels_never_carry_drives_raw_words(self):
        for label in self.dp.FILE_LABELS.values():
            for raw in ("404", "403", "Error", "error", "HTTP", "_"):
                self.assertNotIn(raw, label)


# ─── B. drive_mirror ────────────────────────────────────────────────────────

class Tree(Base):
    def test_newest_reaches_deep_subfolders(self):
        vids = self.dm.newest(7, "video")
        self.assertEqual([v["id"] for v in vids], ["v_deep", "v_prod", "v_root"])
        self.assertEqual(vids[0]["folder"], "videos/tiktok")

    def test_find_by_words_and_hebrew_prefix(self):
        self.assertEqual([m["id"] for m in self.dm.find(7, "the launch video", kind="video")["matches"]],
                         ["v_deep"])
        self.assertEqual([m["id"] for m in self.dm.find(7, "תשלח את הסרטון של המוצר", kind="video")["matches"]],
                         ["v_prod"])
        self.assertEqual(self.dm.find(7, "nothing like it")["matches"], [])

    def test_find_leaves_shortcuts_out(self):
        out = self.dm.find(7, "launch")
        self.assertNotIn("sc", [m["id"] for m in out["matches"]])
        self.assertEqual(out["shortcuts"], 1)

    def test_search_words_drop_request_words(self):
        self.assertEqual(self.dm.search_words("please send the video of the new product"), ["product", "new"])

    def test_no_folder_yet_is_not_cached(self):
        del self.fake.files["C7"]
        self.assertEqual(self.dm.shared_client_root(7), "")
        self.fake.add("C7", "client-7 — Cafe", FOLDER, "MEDIA")
        self.assertEqual(self.dm.shared_client_root(7), "C7")

    def test_a_stale_recorded_folder_is_not_trusted(self):
        self.fake.add("OLD7", "client-7 — Cafe", FOLDER, "MYDRIVE")
        self.db.tables["client_activity"] = [{"client_id": 7, "agent_name": "media_agent",
                                              "action_type": "media_folder_created",
                                              "details": {"folder_id": "OLD7"}}]
        self.assertEqual(self.dm.shared_client_root(7), "C7")

    def test_a_renamed_client_keeps_its_folder(self):
        # the writer used to look up the EXACT current name and create a second folder
        self.client_agent.get_client = lambda cid: {"id": 7, "name": "Cafe Renamed"}
        self.assertEqual(self.dm.shared_client_root(7), "C7")

    def test_diagnose_shows_raw_drive_next_to_what_we_show(self):
        self.fake.add("OLD7", "client-7 — Cafe", FOLDER, "MYDRIVE")
        out = self.dm.diagnose(7)
        self.assertEqual(out["resolved_folder"], "C7")
        self.assertEqual(out["service_account"], SA)
        where = {f["id"]: f["under_media_root"] for f in out["folders_named_like_client"]}
        self.assertEqual(where, {"C7": True, "OLD7": False})
        deep = next(f for f in out["files"] if f["id"] == "v_deep")
        self.assertEqual((deep["parent_id"], deep["folder"]), ("T7", "videos/tiktok"))
        self.assertIn("in_agent_list", deep)
        self.assertIn("in_media_tab", deep)
        self.assertEqual(out["raw_counts"]["video"], 3)
        text = " ".join(out["findings"])
        self.assertIn("מחוץ לתיקיית המדיה", text)
        self.assertIn("קיצורי דרך", text)
        self.assertNotIn("token", str(out).lower())


# ─── C. find_media_file in agent_actions ────────────────────────────────────

class FindCard(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.modules.pop("core.agent_actions", None)
        import core
        if hasattr(core, "agent_actions"):
            delattr(core, "agent_actions")
        from core import agent_actions
        cls.aa = agent_actions

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("core.agent_actions", None)
        import core
        if hasattr(core, "agent_actions"):
            delattr(core, "agent_actions")
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        aa = self.aa
        self.saved = (aa._client, aa._is_internal, dict(aa.ACTIONS["send_tiktok_to_inbox"]))
        aa._client = lambda cid: {"id": cid}
        aa._is_internal = lambda c: False
        aa.ACTIONS["send_tiktok_to_inbox"]["available"] = lambda s, c: True
        self.state.snapshot = lambda cid, **k: {}

    def tearDown(self):
        aa = self.aa
        aa._client, aa._is_internal = self.saved[0], self.saved[1]
        aa.ACTIONS["send_tiktok_to_inbox"] = self.saved[2]
        super().tearDown()

    def prepare(self, name):
        return self.aa._prepare_find_media(7, {"name": name, "for": "tiktok"}, {"language": "he"}, [])

    def test_one_match_goes_straight_to_the_real_card(self):
        out = self.prepare("launch final")
        self.assertEqual(out["switch_to"], "send_tiktok_to_inbox")
        self.assertEqual(out["switch_params"], {"drive_file_id": "v_deep"})
        self.assertIn("launch final.mp4", out["notice"])

    def test_none_is_one_card_with_the_folder(self):
        out = self.prepare("wedding")
        self.assertEqual(out["switch_to"], "media_file_not_found")
        spec = self.aa.ACTIONS["media_file_not_found"]
        self.assertTrue(spec["code_only"])
        self.assertFalse(spec["available"]({}, {}))

    def test_several_is_one_choice_card_never_a_guess(self):
        self.fake.add("v_l2", "launch teaser.mp4", "video/mp4", "V7", 5, "2026-11-09T09:00:00Z")
        out = self.prepare("launch")
        self.assertNotIn("switch_to", out)
        self.assertEqual(sorted(out["params"]["candidates"]), ["v_deep", "v_l2"])
        choices = self.aa._find_choices(out["params"])
        self.assertEqual(len(choices), 2)

    def test_the_click_opens_the_card_for_the_picked_file(self):
        seen = []
        saved = self.aa.propose
        self.aa.propose = lambda *a, **k: seen.append((a, k)) or {"card": {"action": a[3]["name"], "id": 9}}
        try:
            out = self.aa._run_find_media_file(7, {"for": "tiktok", "candidates": ["v_deep", "v_l2"],
                                                   "mode": "v_l2"}, None, row={"origin": "client_chat"})
            self.assertEqual(out["next_card"]["action"], "send_tiktok_to_inbox")
            self.assertEqual(seen[0][0][3]["params"], {"drive_file_id": "v_l2"})
            self.assertTrue(seen[0][1]["by_code"])
            with self.assertRaises(self.aa.ActionFailed):
                self.aa._run_find_media_file(7, {"for": "tiktok", "candidates": ["v_deep"], "mode": "zzz"}, None)
        finally:
            self.aa.propose = saved

    # ── The client-27 refusal (2026-11-11): "do you see a video in my Drive called test 3"
    def _replay(self, params):
        """The manager chat's proposal exactly as support_agent routes it
        (origin client_chat, persona general), on client 7's tree with the
        files client 27 had: a lookup has no `for`."""
        self.fake.add("v_t3", "test 3.mp4", "video/mp4", "V7", 40, "2026-11-11T08:00:00Z")
        self.fake.add("v_t30", "test 30.mp4", "video/mp4", "V7", 41, "2026-11-11T07:00:00Z")
        self.fake.add("v_t1", "test_1.mov", "video/quicktime", "T7", 42, "2026-11-10T07:00:00Z")
        aa = self.aa
        saved = (aa._lang, aa._open_rows)
        aa._lang, aa._open_rows = (lambda cid: "en"), (lambda cid: [])
        try:
            return aa.propose(7, "client_chat", "general", {"name": "find_media_file", "params": params},
                              state={"media_folder": {"exists": True}}, client={"id": 7},
                              context={"message": "do you see a video in my Drive called test 3", "language": "en"})
        finally:
            aa._lang, aa._open_rows = saved

    def test_the_test_3_question_is_answered_not_refused(self):
        for params in ({"name": "test 3"}, {"name": "test 3", "for": ""}, {"name": "test 3", "for": "check"},
                       {"name": "test 3", "kind": "video"}, {"name": "test_3", "for": "see", "kind": "videos"}):
            out = self._replay(params)
            self.assertNotIn("error", out, params)
            self.assertIn("test 3.mp4", out["clarify"], params)
            self.assertIn("videos", out["clarify"], params)          # where it is
            self.assertNotIn("test 30", out["clarify"], params)      # the exact name wins, no near miss

    def test_a_lookup_never_stores_a_card_and_lists_duplicates_with_folder_and_date(self):
        self.fake.add("v_t3b", "Test-3.mp4", "video/mp4", "T7", 43, "2026-11-09T08:00:00Z")
        out = self._replay({"name": "test 3"})
        self.assertNotIn("card", out)
        self.assertIn("Test-3.mp4 (videos/tiktok · 09/11/2026)", out["clarify"])
        self.assertIn("test 3.mp4 (videos · 11/11/2026)", out["clarify"])
        with self.assertRaises(ValueError):
            self.aa._validate_find_stored(7, {"name": "x", "for": "look", "candidates": ["v_t3", "v_t3b"]})

    def test_a_lookup_that_finds_nothing_is_the_one_folder_card(self):
        stored = []
        saved = self.aa._db
        self.aa._db = lambda: types.SimpleNamespace(table=lambda n: types.SimpleNamespace(
            insert=lambda row: types.SimpleNamespace(execute=lambda: stored.append(row) or
                                                     types.SimpleNamespace(data=[{**row, "id": 5}]))))
        try:
            out = self._replay({"name": "wedding"})
        finally:
            self.aa._db = saved
        self.assertEqual([r["action"] for r in stored], ["media_file_not_found"])
        self.assertIn("card", out)

    def test_unknown_param_keys_are_still_refused(self):
        # the validator's rule stays: an unknown key is a tampered request
        self.assertEqual(self._replay({"name": "test 3", "query": "x"}), {"error": "invalid_params"})

    def test_the_old_shape_with_a_purpose_still_opens_the_real_card(self):
        out = self.prepare("launch final")
        self.assertEqual(out["switch_to"], "send_tiktok_to_inbox")

    def test_owner_gets_the_pull_page_client_gets_their_folder(self):
        out = self.aa._run_media_file_not_found(7, {"name": "x"}, None, row={"origin": "admin_chat"})
        self.assertEqual(out["link"], "/admin/drive/?client_id=7")

    def test_no_tool_names_or_dead_ends_in_client_text(self):
        aa = self.aa
        texts = [aa._FOUND_ONE, aa._FOUND_SEVERAL, aa._FOUND_NONE, aa._FIND_TROUBLE]
        for name in ("find_media_file", "media_file_not_found"):
            for key in ("title", "what", "done", "button"):
                if key in aa.ACTIONS[name]:
                    texts.append(aa.ACTIONS[name][key])
        for t in texts:
            for lang, line in t.items():
                for bad in ("find_media_file", "media_file_not_found", "drive_mirror", "send_tiktok",
                            "צוות", "the team", "can't see", "לא רואה"):
                    self.assertNotIn(bad, line, f"{lang}: {line}")


# ─── D. Wiring ──────────────────────────────────────────────────────────────

class Wiring(unittest.TestCase):
    def test_pull_routes_are_admin_only(self):
        src = _read("core", "api_server.py")
        tree = ast.parse(src)
        hits = 0
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            routes = [ast.unparse(d) for d in node.decorator_list]
            if any("/api/admin/drive-pull" in r or "/api/admin/drive/diagnose" in r for r in routes):
                hits += 1
                self.assertIn("_require_admin(request)", ast.get_source_segment(src, node), node.name)
        self.assertEqual(hits, 7)          # + add-link (2026-11-11, tests/test_drive_link.py)

    def test_scheduler_minute_moves_pulls_too(self):
        src = _read("core", "api_server.py")
        self.assertIn("drive_pull.run_due(", src)

    def test_deletion_clears_both_keys(self):
        src = _read("core", "client_deletion.py")
        self.assertIn("drive_pull:", src)
        self.assertIn("drive_pull_lock:", src)

    def test_writer_reuses_the_folder_the_readers_resolve(self):
        src = _read("agents", "media_agent.py")
        body = src[src.index("def ensure_client_media_root"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("drive_mirror.shared_client_root(client_id, strict=True)", body)
        self.assertIn("drive_mirror.remember_root(client_id, root)", body)
        self.assertLess(body.index("shared_client_root"), body.index("drive.ensure_folder("))

    def test_agents_list_comes_from_the_whole_tree(self):
        self.assertIn("drive_mirror.newest(", _read("core", "client_state.py"))

    def test_no_new_scope_and_no_picker(self):
        for path in (("core", "drive_pull.py"), ("dashboard", "admin", "drive", "index.html")):
            src = _read(*path)
            self.assertNotIn("googleapis.com/auth/", src)
            self.assertNotIn("google.picker", src)

    def test_the_page_warns_against_sharing_the_whole_drive(self):
        page = _read("dashboard", "admin", "drive", "index.html")
        self.assertIn("לא את כל ה-Drive", page)
        self.assertIn("/api/admin/drive-pull/info", page)
        self.assertIn("/api/admin/drive/diagnose/", page)


if __name__ == "__main__":
    unittest.main()
