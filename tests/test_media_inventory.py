"""The media library (2026-11-23, core/media_inventory, docs/MEDIA-LIBRARY.md).

What must stay true:
- "index" removal takes an item out of the index (agents and the Media tab
  stop seeing it) and leaves the file itself in Drive;
- "file" deletion deletes the file from Drive AND removes it from the index,
  in our media folder and in the client's connected folders alike (no role
  restriction);
- bulk selection works by explicit ids and by "everything matching";
- an item used by a waiting/approved post can still be deleted — the preview
  lists the affected posts, nothing is blocked;
- the retention rule is OFF by default and, when on with N days, deletes only
  rejected generated media older than N days;
- every deletion is logged: who, when, what, how many, total size, mode.

Stdlib only; Drive and the database are in memory.
Run from the repo root:  python tests/test_media_inventory.py -v
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

from core import (content_team, drive_assets, drive_folders, drive_mirror, drive_service,  # noqa: E402
                  drive_storage, media_inventory as mi, social_queue)

CID = 27
NOW = datetime.now(timezone.utc)


def ago(days):
    return (NOW - timedelta(days=days)).isoformat()


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.drive = {}        # file id -> {"name", "where": "media"|"connected", ...}
        self.settings = {}     # app_settings key -> value
        self.assets = {"items": {}}
        self.queue = []
        self.generated = {}
        self.deleted_calls = []

        def patch(obj, name, value):
            old = getattr(obj, name)
            setattr(obj, name, value)
            self.addCleanup(setattr, obj, name, old)

        def catalog(client_id, refresh=False):
            items = [{"id": fid, "name": f["name"], "kind": f.get("kind", "image"),
                      "mime_type": f.get("mime", "image/png"), "created_time": f["created"], "size": f["size"],
                      "folder": f.get("folder", "images")}
                     for fid, f in self.drive.items() if f["where"] == "media"]
            return drive_mirror._without_removed(client_id, {"exists": True, "items": items})

        def delete_file(fid):
            self.deleted_calls.append(fid)
            self.drive.pop(fid)
            return "deleted"

        patch(drive_mirror, "catalog", catalog)
        patch(drive_service, "delete_file", delete_file)
        patch(drive_storage, "scope", lambda cid: contextlib.nullcontext())
        patch(drive_storage, "as_service_account", lambda: contextlib.nullcontext())
        patch(drive_assets, "_load", lambda cid: self.assets)
        patch(drive_assets, "_save", lambda cid, state: None)
        patch(drive_folders, "load", lambda cid: [
            {"id": "ROOT1", "display_name": "ביקורות", "role": "customer_photos"},
            {"id": "SUB1", "display_name": "2025", "role": "customer_photos", "parent_connection_id": "ROOT1"}])
        patch(social_queue, "items", lambda cid: self.queue)
        patch(content_team, "latest", lambda cid: {})
        patch(mi, "_read", lambda key: dict(self.settings.get(key) or {}))
        patch(mi, "_write", lambda key, value: self.settings.__setitem__(key, value))
        patch(mi, "_generated_ids", lambda cid: dict(self.generated))
        patch(mi, "_log", lambda line: None)
        patch(mi, "_forget_caches", lambda cid: None)
        mi._removed_cache.clear()
        self.addCleanup(mi._removed_cache.clear)

    def media(self, fid, name, days=1, size=1000, source="generated_image", kind="image"):
        self.drive[fid] = {"name": name, "where": "media", "created": ago(days), "size": size, "kind": kind}
        if source in ("generated_image", "generated_video", "real_copy"):
            self.generated[fid] = source

    def asset(self, fid, name, conn="SUB1", days=1, size=5000):
        self.drive[fid] = {"name": name, "where": "connected", "created": ago(days), "size": size}
        self.assets["items"][fid] = {"file_id": fid, "name": name, "connection_id": conn, "root_id": "ROOT1",
                                     "state": "indexed", "media_type": "image", "path": "ביקורות/2025",
                                     "created": ago(days), "size": size, "description": "red shoe", "tags": ["shoe"]}

    def post(self, fid, status, title="post"):
        self.queue.append({"id": "q" + str(len(self.queue)), "file_id": fid, "status": status, "title": title,
                           "planned_date": "2026-12-01", "source": ""})

    def ids(self):
        return {i["id"] for i in mi.inventory(CID)}

    def log(self):
        return mi.deletion_log(CID)


class Inventory(Base):
    def test_every_source_with_size_usage_and_summary(self):
        self.media("G1", "gen.png", size=2000)
        self.media("V1", "clip.mp4", size=9000, source="generated_video", kind="video")
        self.media("S1", "set-a1-v2.png", source="uploaded")
        self.media("U1", "logo.png", source="uploaded")
        self.asset("C1", "review.jpg")
        self.post("G1", "approved")
        self.post("V1", "rejected")
        items = {i["id"]: i for i in mi.inventory(CID)}
        self.assertEqual({k: v["source"] for k, v in items.items()},
                         {"G1": "generated_image", "V1": "generated_video", "S1": "slide", "U1": "uploaded",
                          "C1": "client_asset"})
        self.assertEqual((items["G1"]["usage"], items["V1"]["usage"], items["U1"]["usage"]), ("used", "rejected", "unused"))
        out = mi.view(CID)
        self.assertEqual((out["summary"]["count"], out["summary"]["size"]), (5, 2000 + 9000 + 1000 + 1000 + 5000))
        self.assertEqual(out["summary"]["by_source"]["client_asset"], {"count": 1, "size": 5000})

    def test_filters_search_sort(self):
        self.media("G1", "old.png", days=40, size=100)
        self.media("G2", "new.png", days=2, size=5 * 1024 * 1024)
        self.asset("C1", "review.jpg")
        view = lambda f, sort="newest": [i["id"] for i in mi.view(CID, f, sort)["items"]]   # noqa: E731
        self.assertEqual(view({"source": "client_asset"}), ["C1"])
        self.assertEqual(view({"q": "shoe"}), ["C1"])
        self.assertEqual(view({"older_than_days": "30"}), ["G1"])
        self.assertEqual(view({"size_min_mb": "1"}), ["G2"])
        self.assertEqual(view({"folder": "conn:ROOT1"}), ["C1"])
        self.assertEqual(view({}, "largest")[0], "G2")
        self.assertEqual(view({}, "oldest")[0], "G1")


class IndexOnly(Base):
    def test_index_removal_leaves_the_file(self):
        self.media("G1", "gen.png")
        self.asset("C1", "review.jpg")
        out = mi.delete(CID, ["G1", "C1"], mode="index", by="client")
        self.assertEqual(out["done"], 2)
        self.assertIn("G1", self.drive)
        self.assertIn("C1", self.drive)
        self.assertEqual(self.deleted_calls, [])
        self.assertEqual(self.ids(), set())
        self.assertEqual(self.assets["items"]["C1"]["state"], "removed")
        self.assertIn("G1", mi.removed_ids(CID))

    def test_agents_stop_seeing_it(self):
        self.asset("C1", "review.jpg")
        mi.delete(CID, ["C1"], mode="index")
        self.assertEqual(drive_assets.find_assets(CID, query="shoe"), [])
        # the daily scan keeps a removed file out of the index
        drive_assets._upsert_item(self.assets, {"id": "C1", "name": "review.jpg", "mimeType": "image/jpeg",
                                                "md5Checksum": "other"}, {"conn_id": "SUB1", "root_id": "ROOT1"}, "run2")
        self.assertEqual(self.assets["items"]["C1"]["state"], "removed")


class FileDeletion(Base):
    def test_file_mode_deletes_from_drive_and_index(self):
        self.media("G1", "gen.png")
        self.asset("C1", "review.jpg")
        out = mi.delete(CID, ["G1", "C1"], mode="file", by="client")
        self.assertEqual(out["done"], 2)
        self.assertEqual(sorted(self.deleted_calls), ["C1", "G1"])
        self.assertNotIn("G1", self.drive)
        self.assertNotIn("C1", self.drive)
        self.assertNotIn("C1", self.assets["items"])
        self.assertEqual(self.ids(), set())

    def _refuse(self, status, refused=("G2",)):
        class Refused(Exception):
            pass

        def refuse(fid):
            if fid in refused:
                e = Refused(f"Drive DELETE {status}: secret-ish detail")
                e.response = types.SimpleNamespace(status_code=status)
                raise e
            self.drive.pop(fid)
            return "trashed"
        drive_service.delete_file = refuse

    def test_a_failed_delete_still_leaves_the_library_and_says_the_file_stayed(self):
        self.media("G1", "gen.png")
        self.media("G2", "gen2.png")
        self.asset("C1", "review.jpg")
        self._refuse(503, refused=("G2", "C1"))
        out = mi.delete(CID, ["G1", "G2", "C1"], mode="file")
        self.assertEqual((out["done"], out["trashed"]), (1, 1))
        self.assertEqual({f["id"]: (f["file_deleted"], f["removed_from_library"]) for f in out["failed"]},
                         {"G2": (False, True), "C1": (False, True)})
        self.assertIn("G2", self.drive)                       # the file itself stays
        self.assertEqual(self.ids(), set())                   # ... but the library no longer lists it
        self.assertEqual(self.assets["items"]["C1"]["state"], "removed")
        self.assertEqual(drive_assets.find_assets(CID, query="shoe"), [])   # agents stop using it

    def test_reasons_are_codes_and_the_detail_stays_in_the_log(self):
        self.asset("C1", "review.jpg")
        self.asset("C2", "other.jpg")
        self.media("G1", "gen.png")
        self.media("G2", "gen2.png")
        self.media("G3", "gen3.png")
        owner = {"C1": {"ownedByMe": False}, "C2": {"ownedByMe": False, "driveId": "SHARED"}}
        old_meta = drive_service.get_meta
        drive_service.get_meta = lambda fid, fields="": owner[fid]
        self.addCleanup(setattr, drive_service, "get_meta", old_meta)
        reasons, outs = {}, []
        for ids, status in ((["C1", "C2"], 403), (["G1"], 404), (["G2"], 502), (["G3"], 400)):
            self._refuse(status, refused=tuple(ids))
            outs.append(mi.delete(CID, ids, mode="file"))
            reasons.update({f["id"]: f["reason"] for f in outs[-1]["failed"]})
        self.assertEqual(reasons, {"C1": "owner_only", "C2": "no_permission", "G1": "not_found",
                                   "G2": "drive_unavailable", "G3": "unknown"})
        self.assertTrue(set(reasons.values()) <= set(mi.REASONS))
        self.assertNotIn("Drive DELETE", str(outs))           # nothing technical reaches the screen
        failures = [f for e in self.log() for f in e.get("failures", [])]
        self.assertTrue(all("Drive DELETE" in f["detail"] for f in failures))
        html = _read("dashboard", "client", "index.html")
        for code in mi.REASONS:
            self.assertIn(f"reason_{code}:", html)


class Bulk(Base):
    def test_explicit_ids_and_everything_matching(self):
        for n in range(4):
            self.media(f"G{n}", f"g{n}.png", days=n * 10)
        self.asset("C1", "review.jpg")
        self.assertEqual(mi.delete(CID, ["G0", "G1"], mode="index")["done"], 2)
        out = mi.delete(CID, raw_filter={"source": "generated_image"}, mode="file")
        self.assertEqual(out["done"], 2)
        self.assertEqual(self.ids(), {"C1"})

    def test_preview_counts_size_and_folders_without_changing_anything(self):
        self.media("G1", "a.png", size=1000)
        self.asset("C1", "review.jpg", size=4000)
        p = mi.preview(CID, ["G1", "C1"], mode="file")
        self.assertEqual((p["count"], p["size"]), (2, 5000))
        self.assertEqual({f["origin"] for f in p["folders"]}, {"media", "connected"})
        self.assertEqual(self.deleted_calls, [])
        self.assertEqual(self.log(), [])


class UsageNotice(Base):
    def test_used_item_is_listed_and_still_deleted(self):
        self.media("G1", "a.png")
        self.post("G1", "waiting", "Black Friday")
        self.post("G1", "approved", "Story")
        p = mi.preview(CID, ["G1"], mode="file")
        self.assertEqual(sorted(a["title"] for a in p["affected_posts"]), ["Black Friday", "Story"])
        out = mi.delete(CID, ["G1"], mode="file")
        self.assertEqual(out["done"], 1)
        self.assertEqual(len(out["affected_posts"]), 2)
        self.assertNotIn("G1", self.drive)

    def test_published_or_rejected_posts_are_not_listed(self):
        self.media("G1", "a.png")
        self.post("G1", "published")
        self.assertEqual(mi.preview(CID, ["G1"])["affected_posts"], [])


class Retention(Base):
    def setUp(self):
        super().setUp()
        self.media("OLD_REJ", "old.png", days=40)
        self.media("NEW_REJ", "new.png", days=3)
        self.media("OLD_UNUSED", "unused.png", days=40)
        self.asset("OLD_CLIENT", "review.jpg", days=40)
        for fid in ("OLD_REJ", "NEW_REJ", "OLD_CLIENT"):
            self.post(fid, "rejected")

    def test_off_by_default(self):
        self.assertEqual(mi.settings(CID), {"retention_enabled": False, "retention_days": None, "unused_days": None})
        self.assertEqual(mi.run_retention_for(CID), {"client_id": CID, "ran": False})
        self.assertEqual(self.deleted_calls, [])

    def test_needs_a_day_count_to_switch_on(self):
        with self.assertRaises(ValueError):
            mi.save_settings(CID, retention_enabled=True)
        self.assertFalse(mi.settings(CID)["retention_enabled"])

    def test_on_deletes_only_rejected_generated_older_than_n(self):
        mi.save_settings(CID, retention_enabled=True, retention_days="30")
        out = mi.run_retention_for(CID)
        self.assertEqual((out["ran"], out["deleted"]), (True, 1))
        self.assertEqual(self.deleted_calls, ["OLD_REJ"])
        entry = self.log()[0]
        self.assertEqual((entry["by"], entry["mode"], entry["count"]), ("retention", "file", 1))

    def test_unused_days_is_the_managers_setting(self):
        mi.save_settings(CID, unused_days="14")
        self.assertEqual(mi.view(CID)["settings"]["unused_days"], 14)


class Log(Base):
    def test_every_deletion_is_logged(self):
        self.media("G1", "a.png", size=1000)
        self.asset("C1", "review.jpg", size=4000)
        mi.delete(CID, ["G1"], mode="index", by="client")
        mi.delete(CID, ["C1"], mode="file", by="admin")
        newest, first = self.log()
        self.assertEqual((first["by"], first["mode"], first["count"], first["size"]), ("client", "index", 1, 1000))
        self.assertEqual((newest["by"], newest["mode"], newest["count"], newest["size"]), ("admin", "file", 1, 4000))
        self.assertEqual(newest["items"][0]["name"], "review.jpg")
        self.assertTrue(first["at"] and newest["folders"])


class Wiring(unittest.TestCase):
    def test_routes_ui_and_admin(self):
        api = _read("core", "api_server.py")
        for route in ('"/api/client/media-inventory"', '"/api/client/media-inventory/preview"',
                      '"/api/client/media-inventory/delete"', '"/api/admin/media-inventory/{client_id}"',
                      "media_inventory.run_retention()"):
            self.assertIn(route, api)
        html = _read("dashboard", "client", "index.html")
        for text in ("הסרה מהמאגר — הקובץ נשאר בדרייב", "מחיקת הקובץ — נמחק מהדרייב ומהמאגר", 'id="viewLibrary"',
                     "'library'"):
            self.assertIn(text, html)
        admin = _read("dashboard", "admin", "index.html")
        self.assertIn("/api/admin/media-inventory/", admin)
        self.assertIn("crMlOn", admin)

    def test_no_role_restriction_on_delete(self):
        src = _read("core", "media_inventory.py")
        for word in ("read_only", "access_for", "can_write", "ROLE"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
