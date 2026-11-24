"""The Media tab's Drive fix + the client-side backup folder (2026-11-24,
docs/DRIVE-FOLDERS.md §Connect from the Media tab, §Backup).

What must stay true:
- the connect flow (drive_folders: paste link → role by name → confirm) is on
  the Media tab, next to the connected-folders list; settings links to it;
- the old pull's add-from-link control (it COPIES) is gone from the Media tab,
  and the admin pull page says plainly that it copies into OUR folder;
- connecting a folder never copies a file;
- a "reviews" folder connects as customer_photos and shows in client_state;
- copies the pull made whose source is still in the client's Drive are
  found by one filter ("pulled duplicates"); nothing is deleted by it;
- the backup folder "uallak" is created only inside a folder the client
  shared that we may add to, with posts / slides / images / video by month;
- each produced, approved file is copied exactly once;
- a client-owned file is never backed up; no writable folder = said plainly.

Stdlib only. Run from the repo root:  python tests/test_drive_backup.py -v
"""
import contextlib
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")
for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}),
                      ("httpx", {"Response": object, "Client": object, "HTTPError": Exception,
                                 "HTTPStatusError": Exception, "TimeoutException": Exception,
                                 "RequestError": Exception, "get": None, "post": None}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from core import (client_state, drive_assets, drive_backup, drive_folders, drive_mirror,  # noqa: E402
                  drive_pull, drive_service, drive_storage, media_inventory, social_queue)

CID = 27
FOLDER = drive_folders.FOLDER_MIME


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


HTML = _read("dashboard", "client", "index.html")


def _view(name):
    start = HTML.index(f'id="view{name}"')
    return HTML[start:HTML.index(f"<!-- /view{name} -->", start)]


class HttpError(Exception):
    def __init__(self, status, text=""):
        super().__init__(f"HTTP {status}")
        self.response = types.SimpleNamespace(status_code=status, text=text)


class FakeDrive:
    """Files and folders by id; every write recorded."""

    def __init__(self):
        self.meta = {}
        self.copies, self.uploads, self.created = [], [], []
        self.props = {}           # (parent, key, value) -> [file]

    def folder(self, fid, name, parent="", can_add=True, drive_id=""):
        self.meta[fid] = {"id": fid, "name": name, "mimeType": FOLDER, "parents": [parent] if parent else [],
                          "trashed": False, "driveId": drive_id, "capabilities": {"canAddChildren": can_add,
                                                                                    "canListChildren": True}}

    def file(self, fid, name, drive_id="OURDRIVE", props=None, trashed=False):
        self.meta[fid] = {"id": fid, "name": name, "mimeType": "image/png", "driveId": drive_id,
                          "appProperties": dict(props or {}), "trashed": trashed}

    def get_meta(self, fid, fields=""):
        if fid not in self.meta:
            raise HttpError(404)
        return dict(self.meta[fid])

    def ensure_folder(self, name, parent):
        for m in self.meta.values():
            if m.get("name") == name and parent in (m.get("parents") or []) and m["mimeType"] == FOLDER:
                return m["id"]
        fid = f"F{len(self.meta)}"
        self.folder(fid, name, parent)
        self.created.append((parent, name, fid))
        return fid

    def copy_file(self, fid, parent, name="", app_properties=None):
        new = {"id": f"C{len(self.copies) + 1}", "name": name}
        self.copies.append((fid, parent, name, dict(app_properties or {})))
        for k, v in (app_properties or {}).items():
            self.props.setdefault((parent, k, v), []).append(new)
        return new

    def upload_bytes(self, folder, name, content, mime, app_properties=None):
        new = {"id": f"U{len(self.uploads) + 1}", "name": name}
        self.uploads.append((folder, name, content, mime))
        for k, v in (app_properties or {}).items():
            self.props.setdefault((folder, k, v), []).append(new)
        return new

    def find_by_app_property(self, parent, key, value):
        return list(self.props.get((parent, key, value), []))

    def list_children(self, fid, page_token="", page_size=40, fields=""):
        return {"files": [m for m in self.meta.values() if fid in (m.get("parents") or [])], "next_page_token": ""}

    def path_of(self, fid):
        names = []
        while fid in self.meta:
            names.append(self.meta[fid]["name"])
            parents = self.meta[fid].get("parents") or []
            fid = parents[0] if parents else ""
        return "/".join(reversed(names))


class Base(unittest.TestCase):
    def patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def setUp(self):
        self.drive = d = FakeDrive()
        for name in ("get_meta", "ensure_folder", "copy_file", "upload_bytes", "find_by_app_property",
                     "list_children"):
            self.patch(drive_service, name, getattr(d, name))
        self.patch(drive_storage, "as_service_account", lambda: contextlib.nullcontext())
        self.patch(drive_storage, "location", lambda cid: {"kind": drive_storage.SHARED})
        self.patch(drive_pull, "_our_drive_id", lambda: "OURDRIVE")
        self.folders = {"connections": []}
        self.patch(drive_folders, "_load", lambda cid: self.folders)
        self.patch(drive_folders, "_save", lambda cid, data: None)
        self.patch(drive_folders, "_all_rows", lambda: [])
        self.patch(drive_folders, "_log", lambda line: None)
        self.state = {}
        self.patch(drive_backup, "_load", self._backup_load)
        self.patch(drive_backup, "_save", lambda cid, s: self.state.update(s))
        self.patch(drive_backup, "_log", lambda line: None)

    def _backup_load(self, cid):
        for key, default in (("root", {}), ("folders", {}), ("done", {}), ("pending", []), ("status", "")):
            self.state.setdefault(key, default)
        return self.state

    def connect(self, fid="SHAREDFOLDER01", name="פרסומים", role="social_output", can_add=True):
        self.drive.folder(fid, name, can_add=can_add, drive_id="0AclientDriveXXXXXXX")
        return drive_folders.confirm(CID, f"https://drive.google.com/drive/folders/{fid}", role)

    def approved(self, fid, source="post", kind="image", **extra):
        return {"id": "q" + fid, "file_id": fid, "kind": kind, "source": source, "status": "approved",
                "approved_at": "2026-11-24T10:00:00+00:00", "title": "טיפים · 1/3", "caption": "כיתוב",
                "channels": ["instagram"], "post_ref": {"post_id": "P1"}, **extra}


# ─── A + B: the Media tab ────────────────────────────────────────────────────

class MediaTab(unittest.TestCase):
    def test_connect_flow_is_on_the_media_tab_next_to_the_folder_list(self):
        media = _view("Media")
        for el in ('id="driveConnect"', 'id="driveFolders"', 'id="driveLink"', 'id="drivePreviewBtn"',
                   'id="driveConfirm"', 'id="driveList"', 'id="driveBackup"'):
            self.assertIn(el, media)
        self.assertLess(media.index('id="mediaFolders"'), media.index('id="driveConnect"'))
        show = HTML[HTML.index("function showView(name)"):][:2500]
        self.assertIn("if (name === 'media') loadDriveFolders();", show)
        self.assertIn("getElementById('viewMedia')", HTML[HTML.index("async function driveStep()"):][:200])
        # after a connect, the connected-folders list above refreshes
        self.assertIn("invLoad({ refresh: true, silent: true })", HTML[HTML.index("t.id === 'driveConnectBtn'"):][:900])

    def test_settings_still_reaches_it(self):
        personal = _view("Personal")
        self.assertNotIn('id="driveLink"', personal)
        self.assertIn("openDriveConnect()", personal)
        self.assertIn("showView('media')", HTML[HTML.index("function openDriveConnect()"):][:200])
        self.assertEqual(HTML.count('id="driveFolders"'), 1)

    def test_the_pull_control_is_gone_from_the_media_tab(self):
        media = _view("Media")
        for gone in ('id="mlLink"', "mlAddLink", "/api/client/media/add-link"):
            self.assertNotIn(gone, media)
            self.assertNotIn(gone, HTML)

    def test_the_admin_pull_page_says_it_copies(self):
        page = _read("dashboard", "admin", "drive", "index.html")
        self.assertIn('id="notConnectBanner"', page)
        self.assertIn("לא מחבר תיקייה של לקוח", page)
        self.assertIn("העתקה לתיקייה שלנו", page[:page.index("</title>")])

    def test_the_pulled_duplicates_chip(self):
        media = _view("Media")
        self.assertIn('data-lib-preset="pulled_dup"', media)
        preset = HTML[HTML.index("function libPreset(kind)"):][:1500]
        self.assertIn("pulled_duplicate: '1'", preset)
        self.assertIn("libSelectAll()", preset)          # selected, not deleted
        self.assertNotIn("media-inventory/delete", preset)


# ─── C: a connected folder is indexed in place ───────────────────────────────

class Connect(Base):
    def test_the_reviews_folder_connects_as_customer_photos_without_a_copy(self):
        self.drive.folder("REVIEWSFOLDER1", "reviews", drive_id="")
        self.drive.file("R1", "review1.jpg", drive_id="")
        self.drive.meta["R1"]["parents"] = ["REVIEWSFOLDER1"]
        preview = drive_folders.preview(CID, "https://drive.google.com/drive/folders/REVIEWSFOLDER1")
        self.assertEqual((preview["inferred_role"], preview["role_source"]), ("customer_photos", "name"))
        record = drive_folders.confirm(CID, "https://drive.google.com/drive/folders/REVIEWSFOLDER1", preview["inferred_role"])
        self.assertEqual((record["folder_id"], record["role"]), ("REVIEWSFOLDER1", "customer_photos"))
        self.assertEqual((self.drive.copies, self.drive.uploads, self.drive.created), ([], [], []))
        # visible to the agents
        self.patch(drive_assets, "_load", lambda cid: {"items": {}, "meter": {"items": 0}, "job": {}})
        self.patch(drive_assets, "allowance", lambda cid, state: 150)
        out = client_state._connected_folders(CID)
        self.assertTrue(out["connected"])
        self.assertEqual([(f["name"], f["role"], f["indexed_for_agents"]) for f in out["folders"]],
                         [("reviews", "customer_photos", True)])

    def test_the_connection_code_never_copies(self):
        for mod in ("drive_folders.py", "drive_assets.py", "drive_index_chat.py"):
            self.assertNotIn("copy_file(", _read("core", mod), mod)


# ─── D: pulled duplicates ────────────────────────────────────────────────────

class PulledDuplicates(Base):
    def setUp(self):
        super().setUp()
        media_inventory._pulled_cache.clear()
        cat = [{"id": "OUR1", "name": "r1.jpg", "kind": "image", "pulled_from": "SRC_INDEXED", "folder": "images"},
               {"id": "OUR2", "name": "r2.jpg", "kind": "image", "pulled_from": "SRC_ALIVE", "folder": "images"},
               {"id": "OUR3", "name": "r3.jpg", "kind": "image", "pulled_from": "SRC_GONE", "folder": "images"},
               {"id": "OUR4", "name": "r4.jpg", "kind": "image", "pulled_from": "SRC_OURS", "folder": "images"},
               {"id": "OUR5", "name": "gen.png", "kind": "image", "folder": "images"}]
        self.drive.file("SRC_ALIVE", "r2.jpg", drive_id="")
        self.drive.file("SRC_OURS", "r4.jpg", drive_id="OURDRIVE")        # a merge between our own folders
        self.patch(drive_mirror, "catalog", lambda cid, refresh=False: {"items": cat})
        self.patch(drive_assets, "_load", lambda cid: {"items": {"SRC_INDEXED": {
            "file_id": "SRC_INDEXED", "name": "r1.jpg", "connection_id": "dcR", "root_id": "dcR",
            "media_type": "image", "state": "indexed"}}})
        self.patch(drive_folders, "load", lambda cid: [{"id": "dcR", "display_name": "reviews", "role": "customer_photos"}])
        self.patch(media_inventory, "_usage_map", lambda cid: {})
        self.patch(media_inventory, "_generated_ids", lambda cid: {})

    def test_the_filter_finds_copies_whose_source_is_still_theirs(self):
        items = media_inventory.inventory(CID)
        f = media_inventory.clean_filter({"pulled_duplicate": "1"})
        self.assertEqual(sorted(i["id"] for i in items if media_inventory.matches(i, f)), ["OUR1", "OUR2"])
        self.assertEqual(media_inventory.summary(items)["pulled_duplicates"], 2)
        # without the filter everything is still listed — nothing removed
        self.assertEqual(len([i for i in items if media_inventory.matches(i, media_inventory.clean_filter({}))]), 6)

    def test_the_marker_comes_from_drive(self):
        self.assertIn("appProperties", drive_service.MIRROR_FILE_FIELDS)
        shaped = drive_mirror._shape({"id": "X", "name": "a.jpg", "mimeType": "image/jpeg",
                                      "appProperties": {"uallak_pulled_from": "SRC"}})
        self.assertEqual(shaped["pulled_from"], "SRC")
        self.assertIn('"pulled_duplicate"', _read("core", "api_server.py"))


# ─── E: the backup ───────────────────────────────────────────────────────────

class Backup(Base):
    def test_folder_structure_under_a_writable_shared_folder(self):
        self.connect()
        self.drive.file("GEN1", "gen1.png")
        self.drive.file("SLIDE1", "set-a1-v1.png")
        self.drive.file("VID1", "clip.mp4")
        drive_backup.enqueue(CID, [self.approved("GEN1"), self.approved("SLIDE1", source="slide"),
                                   self.approved("VID1", kind="video", source="chat")])
        out = drive_backup.run(CID, 60)
        self.assertEqual((out["done"], out["status"]), (3, "ok"))
        root = self.state["root"]["folder_id"]
        self.assertEqual(self.drive.path_of(root), "פרסומים/uallak")
        targets = {fid: self.drive.path_of(parent) for fid, parent, _, _ in self.drive.copies}
        self.assertEqual(targets, {"GEN1": "פרסומים/uallak/images/2026-11",
                                   "SLIDE1": "פרסומים/uallak/slides/2026-11",
                                   "VID1": "פרסומים/uallak/video/2026-11"})
        self.assertEqual([self.drive.path_of(f) for f, *_ in self.drive.uploads], ["פרסומים/uallak/posts/2026-11"])
        self.assertTrue(all(props == {drive_backup.BACKUP_KEY: fid} for fid, _, _, props in self.drive.copies))
        # every created folder is under the folder the client shared
        self.assertTrue(all(self.drive.path_of(fid).startswith("פרסומים/") for _, _, fid in self.drive.created))
        # our backup folder is never indexed as client assets
        rec = next(r for r in self.folders["connections"] if r.get("folder_id") == root)
        self.assertTrue(rec["excluded"] and rec["backup"])

    def test_each_file_exactly_once(self):
        self.connect()
        self.drive.file("GEN1", "gen1.png")
        item = self.approved("GEN1")
        drive_backup.enqueue(CID, [item])
        drive_backup.enqueue(CID, [item])                # queued once
        drive_backup.run(CID, 60)
        drive_backup.enqueue(CID, [item])                # already done: not queued again
        drive_backup.run(CID, 60)
        self.assertEqual(len(self.drive.copies), 1)
        # a lost answer: the ledger forgot, Drive still has the marked copy → no second copy
        self.state["done"].clear()
        drive_backup.enqueue(CID, [item])
        drive_backup.run(CID, 60)
        self.assertEqual(len(self.drive.copies), 1)
        self.assertEqual(len(self.drive.uploads), 1)

    def test_approval_queues_the_backup(self):
        queue = [{"id": "a1b2c3d4e5f6", "file_id": "GEN1", "kind": "image", "source": "post", "status": "waiting",
                  "channels": ["instagram"], "history": []}]
        self.patch(social_queue, "_load", lambda cid: queue)
        self.patch(social_queue, "_save", lambda cid, items: None)
        social_queue.approve(CID, ["a1b2c3d4e5f6"])
        self.assertEqual([p["file_id"] for p in self.state["pending"]], ["GEN1"])

    def test_client_owned_files_are_never_backed_up(self):
        self.connect()
        # a real asset referenced from their folder: not even queued
        self.assertEqual(drive_backup.enqueue(CID, [self.approved("THEIRS", real_asset={"name": "x"})]), 0)
        self.assertEqual(drive_backup.enqueue(CID, [self.approved("PICKED", source="media_library")]), 0)
        # in our folder but a copy of theirs (the old pull / a working copy), or not in our Drive at all
        self.drive.file("PULLED", "r.jpg", props={"uallak_pulled_from": "SRC"})
        self.drive.file("WORK", "w.jpg", props={"uallak_working_copy_of": "SRC"})
        self.drive.file("ELSEWHERE", "e.jpg", drive_id="0AclientDriveXXXXXXX")
        drive_backup.enqueue(CID, [self.approved(f) for f in ("PULLED", "WORK", "ELSEWHERE")])
        out = drive_backup.run(CID, 60)
        self.assertEqual((out["done"], out["skipped"], self.drive.copies), (0, 3, []))

    def test_client_drive_storage_needs_no_backup(self):
        self.connect()
        self.patch(drive_storage, "location", lambda cid: {"kind": drive_storage.CLIENT})
        self.drive.file("GEN1", "gen1.png")
        drive_backup.enqueue(CID, [self.approved("GEN1")])
        self.assertEqual(drive_backup.run(CID, 60)["status"], "client_drive")
        self.assertEqual(self.drive.copies, [])

    def test_no_writable_folder_is_said_plainly_and_nothing_is_created(self):
        view = drive_backup.view(CID, [])
        self.assertEqual(view["status"], "no_writable_folder")
        self.assertIn("שתפו תיקייה", view["text"])
        self.connect(can_add=False)                       # shared, but read-only for us
        self.drive.file("GEN1", "gen1.png")
        drive_backup.enqueue(CID, [self.approved("GEN1")])
        self.assertEqual(drive_backup.run(CID, 60)["status"], "no_writable_folder")
        self.assertEqual((self.drive.created, self.drive.copies), ([], []))
        self.assertEqual(drive_backup.view(CID, drive_folders.load(CID))["status"], "no_writable_folder")
        self.assertEqual(len(self.state["pending"]), 1)   # kept for when they share one

    def test_a_newly_connected_folder_is_tried_at_once(self):
        self.connect("READONLYFOLDER", "קריאה", "archive", can_add=False)
        self.drive.file("GEN1", "gen1.png")
        drive_backup.enqueue(CID, [self.approved("GEN1")])
        drive_backup.run(CID, 60)
        self.connect()                                    # the manager shares a writable one
        self.assertEqual(drive_backup.run(CID, 60)["done"], 1)

    def test_a_disconnected_folder_is_never_written_again(self):
        conn = self.connect()
        self.drive.file("GEN1", "gen1.png")
        drive_backup.enqueue(CID, [self.approved("GEN1")])
        drive_backup.run(CID, 60)
        drive_folders.disconnect(CID, conn["id"])
        self.drive.file("GEN2", "gen2.png")
        drive_backup.enqueue(CID, [self.approved("GEN2")])
        self.assertEqual(drive_backup.run(CID, 60)["status"], "no_writable_folder")
        self.assertEqual(len(self.drive.copies), 1)

    def test_one_mechanism_the_tick_and_the_ui(self):
        api = _read("core", "api_server.py")
        tick = api[api.index("def tiktok_uploads_run"):api.index('@app.get("/api/admin/tiktok-uploads")')]
        self.assertIn("drive_backup.run_due(", tick)
        self.assertIn('"backup": drive_backup.view(client_id, rows)', api)
        self.assertIn("renderDriveBackup(d.backup)", HTML)


if __name__ == "__main__":
    unittest.main()
