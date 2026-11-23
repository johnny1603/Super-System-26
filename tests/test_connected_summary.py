"""Connected folders in the chat + social_output kept out of the index
(2026-11-23, docs/DRIVE-FOLDERS.md).

What must stay true:
- client_state carries a `connected_folders` SUMMARY (folders, role, status,
  item counts, images / videos) — never the file list — so the agents can
  answer "what do you have of mine?" in a chat;
- removed items and excluded folders are not counted;
- the walk never indexes a social_output folder (root or subfolder), where the
  publish snapshots are written, and drops items indexed there earlier.

Stdlib only. Run from the repo root:  python tests/test_connected_summary.py -v
"""
import contextlib
import os
import sys
import time
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

from core import chat_parity, client_state, drive_assets, drive_folders, drive_service, drive_storage  # noqa: E402

CID = 27
FOLDER = drive_folders.FOLDER_MIME


def _item(fid, conn, root, media_type="image", state="indexed"):
    return {"file_id": fid, "name": fid, "connection_id": conn, "root_id": root, "media_type": media_type,
            "state": state}


class Base(unittest.TestCase):
    def patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)


class Summary(Base):
    def setUp(self):
        self.conns = [
            {"id": "P", "display_name": "מוצרים", "role": "product_catalog", "status": "ok"},
            {"id": "P1", "display_name": "ישנים", "role": "archive", "role_source": "override",
             "parent_connection_id": "P"},
            {"id": "P2", "display_name": "פרטי", "role": "product_catalog", "excluded": True,
             "excluded_effective": True, "parent_connection_id": "P"},
            {"id": "O", "display_name": "פרסומים", "role": "social_output", "status": "ok"}]
        self.state = {"items": {
            "a": _item("a", "P", "P"), "b": _item("b", "P", "P", "video"), "c": _item("c", "P1", "P"),
            "d": _item("d", "P2", "P"), "e": _item("e", "P", "P", state="removed"),
            "f": _item("f", "P", "P", state="tier0")},
            "meter": {"items": 40}, "job": {"state": "running"}}
        self.patch(drive_folders, "load", lambda cid: self.conns)
        self.patch(drive_assets, "_load", lambda cid: self.state)
        self.patch(drive_assets, "allowance", lambda cid, state: 150)

    def test_a_summary_with_roles_and_counts_by_type(self):
        out = client_state._connected_folders(CID)
        self.assertTrue(out["connected"])
        products, output = out["folders"]
        self.assertEqual((products["name"], products["role"], products["status"]), ("מוצרים", "product_catalog", "ok"))
        # a, b, c, f — not the removed one, not the excluded folder's file
        self.assertEqual({k: products[k] for k in ("items", "indexed", "images", "videos")},
                         {"items": 4, "indexed": 3, "images": 3, "videos": 1})
        self.assertEqual(products["subfolders_with_another_role"],
                         [{"name": "ישנים", "role": "archive", "items": 1, "indexed": 1, "images": 1, "videos": 0}])
        self.assertEqual(products["excluded_subfolders"], 1)
        self.assertFalse(output["indexed_for_agents"])
        self.assertEqual((out["total"]["items"], out["index_running"], out["free_items_left"]), (4, True, 110))

    def test_never_the_file_list(self):
        text = str(client_state._connected_folders(CID))
        for fid in ("'a'", "'b'", "file_id"):
            self.assertNotIn(fid, text)

    def test_nothing_connected(self):
        self.conns.clear()
        self.assertEqual(client_state._connected_folders(CID), {"connected": False, "folders": []})

    def test_it_is_a_snapshot_section_and_the_route_reads_it(self):
        self.assertIn("connected_folders", client_state.SECTIONS)
        self.assertEqual(client_state.SECTION_TOPIC["connected_folders"], "account")
        self.assertEqual(chat_parity.PARITY["GET /api/client/drive-folders"], (chat_parity.READ, "connected_folders"))


class SocialOutputNotIndexed(Base):
    def setUp(self):
        self.tree = {
            "PRODFOLDER": [{"id": "IMG1", "name": "shoe.jpg", "mimeType": "image/jpeg"},
                           {"id": "SUBOUT", "name": "פורסם", "mimeType": FOLDER},
                           {"id": "SUBOK", "name": "new", "mimeType": FOLDER}],
            "SUBOUT": [{"id": "SNAP2", "name": "snap.jpg", "mimeType": "image/jpeg"}],
            "SUBOK": [{"id": "IMG2", "name": "boot.jpg", "mimeType": "image/jpeg"}],
            "OUTFOLDER": [{"id": "SNAP1", "name": "snap.jpg", "mimeType": "image/jpeg"}]}
        self.data = {"connections": [
            {"id": "P", "folder_id": "PRODFOLDER", "display_name": "מוצרים", "role": "product_catalog"},
            {"id": "SO", "folder_id": "SUBOUT", "display_name": "פורסם", "role_override": "social_output",
             "parent_connection_id": "P", "root_connection_id": "P"},
            {"id": "O", "folder_id": "OUTFOLDER", "display_name": "פרסומים", "role": "social_output"}]}
        self.listed = []

        def children(folder_id, page_token="", page_size=200, fields=""):
            self.listed.append(folder_id)
            return {"files": self.tree.get(folder_id, []), "next_page_token": ""}

        def upsert(data, root, folder_id, name, parent, depth, path):
            rec = next((c for c in data["connections"] if c["folder_id"] == folder_id), None)
            if not rec:
                rec = {"id": "N" + folder_id, "folder_id": folder_id, "display_name": name, "role_override": "",
                       "parent_connection_id": root["id"]}
                data["connections"].append(rec)
            return rec

        self.patch(drive_service, "list_children", children)
        self.patch(drive_service, "get_meta", lambda fid, fields="": {"id": fid, "trashed": False})
        self.patch(drive_storage, "as_service_account", lambda: contextlib.nullcontext())
        self.patch(drive_folders, "_load", lambda cid: self.data)
        self.patch(drive_folders, "_save", lambda cid, data: None)
        self.patch(drive_folders, "upsert_subfolder", upsert)

    def test_snapshots_are_never_indexed(self):
        state = {"items": {"OLD": _item("OLD", "O", "O")},
                 "job": {"run_id": "r1", "roots": ["P", "O"]}}
        drive_assets._walk(CID, state, time.monotonic() + 30)
        self.assertEqual(sorted(state["items"]), ["IMG1", "IMG2"])
        self.assertNotIn("OUTFOLDER", self.listed)
        self.assertNotIn("SUBOUT", self.listed)
        self.assertEqual(next(c for c in self.data["connections"] if c["id"] == "O")["status"], "ok")


if __name__ == "__main__":
    unittest.main()