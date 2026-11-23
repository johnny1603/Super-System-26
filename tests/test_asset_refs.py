"""Real assets are referenced, not copied (2026-11-23, core/asset_refs,
docs/DRIVE-FOLDERS.md §Referenced, not copied).

What must stay true:
- a real asset the agent chooses keeps its own Drive file_id — no copy;
- preview / download / the sends accept that file_id (connected-folder
  index) without a media-folder walk;
- Meta's public link is made on a WORKING COPY in our folder, never on the
  client's original, and the copy is deleted once the publish returns (or by
  the daily sweep when it never returned); working_copy() deletes on exit;
- on publish, the asset is snapshotted into the client's social_output
  folder — setting drive_snapshot_on_publish, default ON, can be switched
  off; no social_output folder = skipped; a generated file is not snapshotted;
- the Drive-folders screen asks for Editor (Content manager in a Shared Drive).

Stdlib only. Run from the repo root:  python tests/test_asset_refs.py -v
"""
import contextlib
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

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

from core import (asset_refs, client_settings, drive_assets, drive_download, drive_folders,  # noqa: E402
                  drive_mirror, drive_service, drive_storage, social_queue)
from agents import media_agent  # noqa: E402

CID = 27


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.copies, self.deleted, self.public = [], [], []
        self.refs = {}
        self.cfg = {}
        self.queue = []
        self.conns = [{"id": "ROOT1", "role": "product_catalog", "folder_id": "PRODFOLDER"},
                      {"id": "OUT", "role": "social_output", "folder_id": "OUTFOLDER"}]
        self.index = {"items": {"CLIENT_FILE": {"file_id": "CLIENT_FILE", "connection_id": "ROOT1",
                                                "state": "indexed"}}}

        def patch(obj, name, value):
            old = getattr(obj, name)
            setattr(obj, name, value)
            self.addCleanup(setattr, obj, name, old)

        def copy_file(fid, parent, name="", app_properties=None):
            self.copies.append((fid, parent, dict(app_properties or {})))
            return {"id": f"COPY{len(self.copies)}"}

        patch(drive_service, "copy_file", copy_file)
        patch(drive_service, "delete_file", lambda fid: self.deleted.append(fid) or "deleted")
        patch(drive_service, "make_file_public", lambda fid: self.public.append(fid) or f"https://pub/{fid}")
        patch(drive_service, "find_by_app_property", lambda parent, key, value: [])
        patch(drive_service, "get_meta", lambda fid, fields="": {"id": fid, "trashed": False, "webViewLink": "v/" + fid,
                                                                  "parents": ["SOMEWHERE"]})
        patch(drive_storage, "scope", lambda cid: contextlib.nullcontext())
        patch(drive_storage, "as_service_account", lambda: contextlib.nullcontext())
        patch(drive_assets, "_load", lambda cid: self.index)
        patch(drive_folders, "load", lambda cid: self.conns)
        patch(asset_refs, "_read", lambda cid: {"copies": dict(self.refs)})
        patch(asset_refs, "_write", lambda cid, value: (self.refs.clear(), self.refs.update(value["copies"])))
        patch(asset_refs, "_log", lambda line: None)
        patch(client_settings, "get", lambda cid: dict(self.cfg))
        patch(media_agent, "_subfolder", lambda cid, *path: "WORKFOLDER")
        patch(media_agent, "_log_activity", lambda *a, **k: None)
        patch(social_queue, "_load", lambda cid: self.queue)
        patch(social_queue, "_save", lambda cid, items: None)

    def approved(self, fid):
        item = {"id": "q1", "file_id": fid, "kind": "image", "channels": ["instagram"], "status": "approved",
                "delivered": {}, "history": [], "created_at": "2026-11-23T00:00:00+00:00"}
        self.queue.append(item)
        return item


class Referenced(Base):
    def test_chosen_real_asset_keeps_its_own_id(self):
        brief = types.SimpleNamespace(set_id="s", asset_id="a1")
        out = media_agent._use_real_asset(CID, brief, {"file_id": "CLIENT_FILE", "media_type": "image"}, "instagram")
        self.assertEqual((out["success"], out["file_id"]), (True, "CLIENT_FILE"))
        self.assertEqual(self.copies, [])

    def test_preview_download_and_sends_accept_the_referenced_id(self):
        drive_mirror._assert_in_client_tree(CID, "MEDIA_ROOT", "CLIENT_FILE")      # agent_actions._media_file path
        drive_download._verify_in_client_tree(CID, "MEDIA_ROOT", "CLIENT_FILE", "SOMEWHERE")
        saved = drive_storage.location
        drive_storage.location = lambda cid: {}
        try:
            with self.assertRaises(drive_mirror.NotInClientFolder):     # a stranger's file still walks and fails
                drive_mirror._assert_in_client_tree(CID, "MEDIA_ROOT", "STRANGER_FILE")
        finally:
            drive_storage.location = saved
        self.assertFalse(asset_refs.is_connected(CID, "STRANGER_FILE"))

    def test_removed_or_excluded_is_not_authorised(self):
        self.index["items"]["CLIENT_FILE"]["state"] = "removed"
        self.assertFalse(asset_refs.is_connected(CID, "CLIENT_FILE"))
        self.index["items"]["CLIENT_FILE"]["state"] = "indexed"
        self.conns[0]["excluded_effective"] = True
        self.assertFalse(asset_refs.is_connected(CID, "CLIENT_FILE"))


class WorkingCopy(Base):
    def test_meta_public_link_is_on_a_working_copy_released_after_publish(self):
        out = media_agent.prepare_for_publishing(CID, "CLIENT_FILE")
        self.assertEqual(self.copies[0][:2], ("CLIENT_FILE", "WORKFOLDER"))
        self.assertEqual(self.public, ["COPY1"])                 # never the client's original
        self.assertEqual(self.refs["COPY1"]["url"], out["media_url"])
        from agents import meta_content_agent
        meta_content_agent._release_working_copy(CID, {"media_url": out["media_url"]})
        self.assertEqual(self.deleted, ["COPY1"])
        self.assertEqual(self.refs, {})
        src = _read("agents", "meta_content_agent.py")
        body = src[src.index("def publish("):src.index("# ─── Inbox")]
        self.assertEqual(body.count("_release_working_copy(client_id, spec)"), 2)   # success and failure

    def test_our_own_file_is_made_public_directly(self):
        media_agent.prepare_for_publishing(CID, "OUR_FILE")
        self.assertEqual((self.copies, self.public), ([], ["OUR_FILE"]))

    def test_context_manager_deletes_on_exit(self):
        with asset_refs.working_copy(CID, "CLIENT_FILE", "render") as copy:
            self.assertIn(copy["id"], self.refs)
        self.assertEqual((self.deleted, self.refs), (["COPY1"], {}))

    def test_daily_sweep_deletes_an_unreleased_copy(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        rows = [{"key": f"{asset_refs.PREFIX}{CID}", "value": {"copies": {"OLD": {"at": old}, "NEW": {
            "at": datetime.now(timezone.utc).isoformat()}}}}]
        self.refs.update(rows[0]["value"]["copies"])

        class Q:
            def __getattr__(self, name):
                return lambda *a, **k: self

            def execute(self):
                return types.SimpleNamespace(data=rows)

        db = types.SimpleNamespace(table=lambda name: Q())
        saved = asset_refs._db
        asset_refs._db = lambda: db
        try:
            self.assertEqual(asset_refs.sweep_working_copies(), 1)
        finally:
            asset_refs._db = saved
        self.assertEqual(self.deleted, ["OLD"])


class Snapshot(Base):
    def test_published_asset_is_snapshotted_into_social_output(self):
        self.approved("CLIENT_FILE")
        item = social_queue.mark_published(CID, "q1")
        self.assertEqual(self.copies[0][:2], ("CLIENT_FILE", "OUTFOLDER"))
        self.assertEqual(item["snapshot"]["of"], "CLIENT_FILE")

    def test_channel_publish_snapshots_too(self):
        self.approved("CLIENT_FILE")
        social_queue.mark_delivered(CID, "instagram", "done", item_id="q1")
        self.assertEqual(self.queue[0]["status"], "published")
        self.assertEqual(len(self.copies), 1)

    def test_default_on_and_the_manager_can_switch_it_off(self):
        self.assertTrue(asset_refs.snapshot_enabled(CID))
        self.assertIs(client_settings.FIELDS["drive_snapshot_on_publish"]["paying"], True)
        self.cfg["drive_snapshot_on_publish"] = False
        self.approved("CLIENT_FILE")
        item = social_queue.mark_published(CID, "q1")
        self.assertEqual((self.copies, item["snapshot"]), ([], {"skipped": "setting_off"}))

    def test_no_social_output_folder_is_skipped(self):
        self.conns.pop()
        self.approved("CLIENT_FILE")
        self.assertEqual(social_queue.mark_published(CID, "q1")["snapshot"], {"skipped": "no_social_output_folder"})
        self.assertEqual(self.copies, [])

    def test_a_generated_file_is_not_snapshotted(self):
        self.approved("GENERATED_IN_OUR_FOLDER")
        self.assertNotIn("snapshot", social_queue.mark_published(CID, "q1"))
        self.assertEqual(self.copies, [])


class ShareInstructions(unittest.TestCase):
    def test_editor_not_viewer(self):
        html = _read("dashboard", "client", "index.html")
        section = html[html.index('id="driveFolders"'):html.index('id="driveShareWith"')]
        self.assertIn('"עורך"', section)
        self.assertIn('"מנהל תוכן"', section)
        self.assertNotIn("צופה", section)
        src = _read("core", "drive_folders.py")
        self.assertNotIn("צופה", src)


if __name__ == "__main__":
    unittest.main()
