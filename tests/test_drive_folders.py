"""Connected Drive folders + the real-asset index (2026-11-23,
core/drive_folders + core/drive_assets, docs/DRIVE-FOLDERS.md).

What must stay true:
- a folder's role is inferred from its own name (Hebrew + English), else ONE
  vision call on ≤ 8 samples — and is NEVER saved without the client's
  confirmation; nothing in the client's Drive is renamed / moved / written;
- subfolders are walked ≥ 5 deep, inherit the parent's role, can be
  overridden or excluded one by one, and carry their own counts;
- a Shared Drive folder that needs membership, a folder not shared with us
  and a malformed link are THREE different Hebrew lines (never "no access");
- every connected folder is read_write whatever its role (the role only ranks
  and filters), and customer photos are used like any other real asset;
- dedupe (one description per near-identical group), video = ONE first
  usable frame, a descriptive name = no vision call;
- the free cap stops, spends nothing, quotes from the MEASURED cost and opens
  ONE billing request; credits resume from where it stopped;
- the daily scan indexes only new / changed files and never re-describes;
- find_assets ranks and filters by media_type; a proof slide with nothing
  real is an objection; a text-only post produces with the image tool off.

Stdlib only; Drive, the model and the database are in memory.
Run from the repo root:  python tests/test_drive_folders.py -v
"""
import contextlib
import json
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

from core import claude_json, drive_assets as da, drive_folders as dfo, drive_service, drive_pull  # noqa: E402

try:
    from PIL import Image  # noqa: F401
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

FOLDER = dfo.FOLDER_MIME
CID = 27
REAL = {name: getattr(da, name) for name in ("dhash", "is_black", "downscale")}


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


# ─── An in-memory Drive (as the service account sees it) ────────────────────

class DriveError(Exception):
    def __init__(self, kind, text=""):
        super().__init__(text or kind)
        self.kind = kind
        self.response = None


class FakeDrive:
    def __init__(self):
        self.files = {}          # id → meta
        self.shared = set()      # ids we can read
        self.calls = []
        self.feed, self.expired = [], set()

    def add(self, fid, name, mime=FOLDER, parent="", shared=True, **meta):
        self.files[fid] = {"id": fid, "name": name, "mimeType": mime, "parents": [parent] if parent else [],
                           "trashed": False, **meta}
        if shared:
            self.shared.add(fid)
        return fid

    def get_meta(self, fid, fields=""):
        self.calls.append(("get_meta", fid))
        if fid not in self.files or fid not in self.shared:
            raise DriveError("no_access", "File not found: " + fid)
        meta = dict(self.files[fid])
        meta.setdefault("capabilities", {"canListChildren": True})
        return meta

    def list_children(self, fid, page_token="", page_size=40, fields=""):
        self.calls.append(("list_children", fid))
        if fid not in self.shared:
            raise DriveError("no_access", "File not found: " + fid)
        kids = [dict(f) for f in self.files.values() if fid in f["parents"]]
        return {"files": kids, "next_page_token": ""}

    # the changes feed (core/drive_assets change detection): a token = a position in `feed`
    feed = None
    expired = None

    def changes_start_token(self, drive_id=""):
        self.calls.append(("start_token", drive_id))
        return str(len(self.feed))

    def list_changes(self, token, drive_id="", fields="", page_size=500):
        self.calls.append(("list_changes", token))
        if token in self.expired:
            err = DriveError("expired", "Invalid page token")
            err.response = types.SimpleNamespace(status_code=410)
            raise err
        return {"changes": [dict(c) for c in self.feed[int(token):]], "next_page_token": "",
                "new_start_page_token": str(len(self.feed))}

    def change(self, fid, removed=False):
        self.feed.append({"fileId": fid, "removed": removed, "file": {} if removed else dict(self.files[fid])})


class Memory:
    def __init__(self):
        self.folders = {}
        self.assets = {}


def media(drive, fid, name, parent, mime="image/jpeg", modified="2026-11-01T10:00:00Z", **extra):
    return drive.add(fid, name, mime=mime, parent=parent, modifiedTime=modified, createdTime=modified,
                     md5Checksum=extra.pop("md5", "md5-" + fid), size=extra.pop("size", 1000),
                     thumbnailLink="https://lh3.example/thumb/" + fid + "=s220", **extra)


class Base(unittest.TestCase):
    """Wires the fakes into the real modules; restores everything after."""

    def setUp(self):
        self.drive = FakeDrive()
        self.mem = Memory()
        self.vision_calls = []       # (n images, [names])
        self.usd_per_image = 0.0001
        self.team = []
        self.cfg = {"free_items": 150, "batch_items": 60}
        self.previews = {}           # file id → bytes
        self.hashes = {}             # bytes → hex dHash
        mem = self.mem

        def patch(obj, name, value):
            old = getattr(obj, name)
            setattr(obj, name, value)
            self.addCleanup(setattr, obj, name, old)

        patch(drive_service, "get_meta", self.drive.get_meta)
        patch(drive_service, "list_children", self.drive.list_children)
        patch(drive_service, "changes_start_token", self.drive.changes_start_token)
        patch(drive_service, "list_changes", self.drive.list_changes)
        patch(drive_service, "classify_error", lambda e, target="folder": getattr(e, "kind", "other"))
        patch(drive_service, "service_account_email", lambda: dfo.SHARE_WITH)
        patch(drive_pull, "_our_drive_id", lambda: "OUR_SHARED_DRIVE")
        patch(dfo, "_load", lambda cid: json.loads(json.dumps(mem.folders.get(cid) or {"connections": []})))
        patch(dfo, "_save", lambda cid, data: mem.folders.__setitem__(cid, json.loads(json.dumps(data))))
        patch(dfo, "_all_rows", lambda: [(cid, list(v.get("connections") or [])) for cid, v in mem.folders.items()])
        patch(dfo, "_alert", lambda lines: None)
        patch(dfo, "_log", lambda line: None)

        def load_assets(cid):
            value = json.loads(json.dumps(mem.assets.get(cid) or {}))
            value.setdefault("items", {})
            value.setdefault("meter", {"items": 0, "usd": 0.0, "vision_items": 0, "calls": 0, "avg_usd": 0.0})
            value.setdefault("credits", {"extra_items": 0, "history": []})
            value.setdefault("cap", {})
            value.setdefault("job", {"state": "idle"})
            return value

        patch(da, "_load", load_assets)
        patch(da, "_save", lambda cid, state: mem.assets.__setitem__(cid, json.loads(json.dumps(state))))
        patch(da, "_lease_take", lambda cid: "lease")
        patch(da, "_lease_release", lambda cid, token: None)
        patch(da, "_log", lambda line: None)
        patch(da, "_alert", lambda lines: None)
        patch(da, "settings", lambda cid: dict(self.cfg))
        patch(da, "_preview_for", lambda item: (self.previews.get(item["file_id"], b"img-" + item["file_id"].encode()),
                                                {"frame_at_s": 0.0, "frame_source": "first_frame"}
                                                if item["media_type"] == "video" else {}))
        patch(da, "dhash", lambda data: self.hashes.get(data, ""))
        patch(da, "fetch_preview", lambda raw: b"thumb")

        sink_holder = {"sink": None}

        @contextlib.contextmanager
        def collect_usage():
            sink_holder["sink"] = []
            yield sink_holder["sink"]

        def fake_call(system, blocks, **kw):
            images = [b for b in blocks if isinstance(b, dict) and b.get("type") == "image"]
            names = [b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text"
                     and b["text"].startswith("item ")]
            self.vision_calls.append((len(images), names, kw.get("model")))
            if sink_holder["sink"] is not None:
                sink_holder["sink"].append({"ils": self.usd_per_image * len(images) * 3.40})
            if system == dfo.VISION_SYSTEM:
                return {"role": self.vision_role, "reason_he": "נראות תמונות מוצר על רקע לבן."}
            self.describe_systems.append(system)
            if self.vision_rows:
                return {"items": [dict(self.vision_rows(n), n=n) for n in range(1, len(images) + 1)]}
            return {"items": [{"n": n, "description": f"described {n}", "tags": ["shoe", "red"]}
                              for n in range(1, len(images) + 1)]}

        self.vision_rows = None      # n -> a full structured answer (tests of the description spec)
        self.describe_systems = []

        self.vision_role = "product_catalog"
        patch(claude_json, "collect_usage", collect_usage)
        patch(claude_json, "safe_claude_json_call", fake_call)
        from core import agent_actions
        patch(agent_actions, "open_team_request",
              lambda cid, persona, topic, text, origin="": self.team.append((cid, persona, topic, text, origin))
              or {"card": {"id": "tr1"}})

    # helpers
    def connect(self, link, role):
        record = dfo.confirm(CID, link, role)
        da.request(CID, "add", [record["id"]])
        return record

    def run_all(self, rounds=20):
        for _ in range(rounds):
            out = da.step(CID, 60)
            if (self.mem.assets[CID].get("job") or {}).get("state") == "idle":
                return out
        return out

    def items(self):
        return self.mem.assets[CID]["items"]


# ─── 1. Classification by name, the vision fallback, confirmation ───────────

class Classification(Base):
    def test_hebrew_and_english_names(self):
        cases = {"ביקורות לקוחות": "customer_photos", "Testimonials 2025": "customer_photos", "UGC": "customer_photos",
                 "ארכיון 2019": "archive", "old stuff": "archive", "תמונות מוצר": "product_catalog",
                 "Product shots": "product_catalog", "קטלוג קיץ": "product_catalog", "לוגו": "brand_assets",
                 "Brand kit": "brand_assets", "פוסטים לאינסטגרם": "social_output", "Instagram": "social_output",
                 "חומרי גלם": "raw_materials", "RAW": "raw_materials", "inbox": "raw_materials"}
        for name, role in cases.items():
            with self.subTest(name=name):
                self.assertEqual(dfo.classify_name(name)["role"], role)

    def test_no_match_and_whole_word_short_keywords(self):
        for name in ("Golden hour", "drawings", "2024", "מה שיש", "Summer"):
            with self.subTest(name=name):
                self.assertEqual(dfo.classify_name(name), {})

    def test_customer_photos_wins_over_product(self):
        self.assertEqual(dfo.classify_name("מוצרים - ביקורות")["role"], "customer_photos")

    def test_name_match_preview_needs_no_vision_and_saves_nothing(self):
        self.drive.add("FOLDER_PRODUCTS_01", "תמונות מוצר")
        out = dfo.preview(CID, "https://drive.google.com/drive/folders/FOLDER_PRODUCTS_01")
        self.assertEqual((out["inferred_role"], out["role_source"]), ("product_catalog", "name"))
        self.assertIn("תמונות מוצר", out["explanation"])
        self.assertEqual(self.vision_calls, [])
        self.assertEqual(self.mem.folders, {})                          # preview never saves

    def test_vision_fallback_one_call_at_most_8_samples(self):
        self.drive.add("FOLDER_MISC_0001", "Summer 2026")
        for n in range(12):
            media(self.drive, f"IMG_{n:02d}_abcdef", f"IMG_{n:04d}.jpg", "FOLDER_MISC_0001")
        out = dfo.preview(CID, "FOLDER_MISC_0001")
        self.assertEqual((out["inferred_role"], out["role_source"]), ("product_catalog", "vision"))
        self.assertEqual(len(self.vision_calls), 1)
        self.assertEqual(self.vision_calls[0][0], 8)
        self.assertEqual(self.vision_calls[0][2], claude_json.CLASSIFY_MODEL)
        self.assertEqual(self.mem.folders, {})

    def test_role_is_never_saved_without_confirmation(self):
        self.drive.add("FOLDER_PRODUCTS_01", "תמונות מוצר")
        for role in ("", None):
            with self.assertRaises(dfo.FolderError) as e:
                dfo.confirm(CID, "FOLDER_PRODUCTS_01", role)
            self.assertEqual(e.exception.code, "role_required")
        self.assertEqual(self.mem.folders, {})
        record = dfo.confirm(CID, "FOLDER_PRODUCTS_01", "product_catalog")
        self.assertEqual((record["role"], record["access"]), ("product_catalog", "read_write"))

    def test_api_connect_route_goes_through_confirm(self):
        src = _read("core", "api_server.py")
        self.assertIn("drive_folders.confirm(client_id, req.link[:2000], req.role", src)
        self.assertIn("drive_folders.preview(client_id, req.link[:2000])", src)


# ─── 2. Subfolders: recursion, inheritance, override, exclusion ─────────────

class Subfolders(Base):
    def build(self):
        d = self.drive
        d.add("ROOT_FOLDER_0001", "מוצרים")
        parent = "ROOT_FOLDER_0001"
        for depth in range(1, 7):                                       # 6 levels below the root
            fid = f"LEVEL_{depth}_FOLDER_X"
            d.add(fid, f"level {depth}", parent=parent)
            media(d, f"FILE_L{depth}_abcdef", f"IMG_{depth:04d}.jpg", fid)
            parent = fid
        d.add("REVIEWS_FOLDER_01", "ביקורות", parent="ROOT_FOLDER_0001")
        media(d, "FILE_REVIEW_abcdef", "IMG_9000.jpg", "REVIEWS_FOLDER_01")
        d.add("TRASH_FOLDER_0001", "Trash", parent="ROOT_FOLDER_0001")
        media(d, "FILE_TRASH_abcdef", "IMG_9001.jpg", "TRASH_FOLDER_0001")
        media(d, "FILE_ROOT_abcdef0", "IMG_0000.jpg", "ROOT_FOLDER_0001")

    def test_recursion_at_least_5_deep_inherits_and_counts(self):
        self.build()
        root = self.connect("ROOT_FOLDER_0001", "product_catalog")
        self.run_all()
        conns = {c["folder_id"]: c for c in dfo.load(CID)}
        for depth in range(1, 6):
            rec = conns[f"LEVEL_{depth}_FOLDER_X"]
            self.assertEqual((rec["role"], rec["role_source"], rec["parent_connection_id"]),
                             ("product_catalog", "inherited", root["id"]))
        self.assertIn("FILE_L5_abcdef", self.items())                  # 5 levels below: indexed
        self.assertNotIn("TRASH_FOLDER_0001", conns)                    # system folder skipped
        self.assertNotIn("FILE_TRASH_abcdef", self.items())
        tree = dfo.tree(CID)[0]
        self.assertEqual(tree["item_count"], len(self.items()))
        level1 = next(c for c in tree["children"] if c["folder_id"] == "LEVEL_1_FOLDER_X")
        self.assertEqual((level1["item_count"], level1["role_source"]), (1, "inherited"))
        reviews = next(c for c in tree["children"] if c["folder_id"] == "REVIEWS_FOLDER_01")
        self.assertEqual(reviews["suggested_role"], "customer_photos")   # flagged, still inherited
        self.assertEqual(reviews["role"], "product_catalog")

    def test_override_one_subfolder_and_back(self):
        self.build()
        self.connect("ROOT_FOLDER_0001", "product_catalog")
        self.run_all()
        sub = dfo.for_folder(CID, "REVIEWS_FOLDER_01")
        out = dfo.set_role(CID, sub["id"], "customer_photos")
        self.assertEqual((out["role"], out["role_source"], out["access"]), ("customer_photos", "override", "read_write"))
        # its children inherit the override; siblings keep the root's role
        self.assertEqual(dfo.for_folder(CID, "LEVEL_1_FOLDER_X")["role"], "product_catalog")
        back = dfo.set_role(CID, sub["id"], "")
        self.assertEqual((back["role"], back["role_source"]), ("product_catalog", "inherited"))

    def test_exclude_subfolder_drops_its_tree_from_the_index(self):
        self.build()
        self.connect("ROOT_FOLDER_0001", "product_catalog")
        self.run_all()
        level2 = dfo.for_folder(CID, "LEVEL_2_FOLDER_X")
        dfo.set_excluded(CID, level2["id"], True)
        self.assertTrue(dfo.for_folder(CID, "LEVEL_4_FOLDER_X")["excluded_effective"])
        self.assertEqual([f for f in da.find_assets(CID, query="") if f["folder"].startswith("level 3")], [])
        da.request(CID, "manual")
        self.run_all()
        self.assertNotIn("FILE_L3_abcdef", self.items())
        self.assertIn("FILE_L1_abcdef", self.items())
        root = dfo.tree(CID)[0]
        with self.assertRaises(dfo.FolderError):
            dfo.set_excluded(CID, root["id"], True)                      # a root is disconnected, not excluded


# ─── 3. Shared Drives + distinct errors ─────────────────────────────────────

class Errors(Base):
    def test_not_shared_names_the_address(self):
        self.drive.add("NOT_SHARED_FOLDER1", "x", shared=False)
        with self.assertRaises(dfo.FolderError) as e:
            dfo.preview(CID, "https://drive.google.com/drive/folders/NOT_SHARED_FOLDER1")
        self.assertEqual(e.exception.code, "not_shared")
        self.assertIn(dfo.SHARE_WITH, e.exception.text)
        self.assertEqual(e.exception.extra["share_with"], dfo.SHARE_WITH)

    def test_shared_drive_needs_membership(self):
        # visible inside a Shared Drive but not listable = folder sharing isn't enough
        self.drive.add("SHARED_DRIVE_FOLDR", "Team photos", driveId="0AABCDEFGHIJKLMNOPQ",
                       capabilities={"canListChildren": False})
        with self.assertRaises(dfo.FolderError) as e:
            dfo.preview(CID, "SHARED_DRIVE_FOLDR")
        self.assertEqual(e.exception.code, "shared_drive_member")
        self.assertIn("חבר", e.exception.text)
        self.assertIn(dfo.SHARE_WITH, e.exception.text)
        # a pasted Shared Drive root we can't open
        with self.assertRaises(dfo.FolderError) as e:
            dfo.preview(CID, "https://drive.google.com/drive/folders/0AABCDEFGHIJKLMNOPQ")
        self.assertEqual(e.exception.code, "shared_drive_member")

    def test_malformed_and_not_found_and_file(self):
        with self.assertRaises(dfo.FolderError) as e:
            dfo.preview(CID, "hello there")
        self.assertEqual(e.exception.code, "bad_link")
        media(self.drive, "A_FILE_NOT_FOLDER", "IMG_1.jpg", "")
        with self.assertRaises(dfo.FolderError) as e:
            dfo.preview(CID, "A_FILE_NOT_FOLDER")
        self.assertEqual(e.exception.code, "not_folder")

    def test_every_error_line_is_distinct_and_never_generic(self):
        codes = ("bad_link", "not_found", "not_folder", "trashed", "not_shared", "shared_drive_member")
        texts = [dfo._texts(c, dfo.SHARE_WITH) for c in codes]
        self.assertEqual(len(set(texts)), len(texts))
        for t in texts:
            self.assertNotIn("אין גישה", t)

    def test_shared_drive_folder_connects_and_reads_with_all_drives(self):
        self.drive.add("SHARED_DRIVE_OK01", "מוצרים", driveId="0AABCDEFGHIJKLMNOPQ")
        record = dfo.confirm(CID, "SHARED_DRIVE_OK01", "product_catalog")
        self.assertEqual(record["drive_kind"], "shared_drive")
        src = _read("core", "drive_service.py")
        body = src[src.index("def list_children"):src.index("def get_meta")]
        self.assertIn("supportsAllDrives", body)
        self.assertIn("includeItemsFromAllDrives", body)

    def test_own_storage_and_other_client_refused(self):
        self.drive.add("OUR_OWN_FOLDER_01", "media", driveId="OUR_SHARED_DRIVE")
        with self.assertRaises(dfo.FolderError) as e:
            dfo.preview(CID, "OUR_OWN_FOLDER_01")
        self.assertEqual(e.exception.code, "own_storage")


# ─── 4. Access: every connected folder is read_write ───────────────────────────────────────────────

class Access(Base):
    def test_every_role_is_read_write(self):
        for role in dfo.ROLES:
            self.assertEqual(dfo.access_for(role), "read_write")


# ─── 6. Indexing: tier 0, dedupe, video, batching ───────────────────────────

class Indexing(Base):
    def test_descriptive_name_skips_vision(self):
        self.assertTrue(da.descriptive_name("red leather sneaker side view.jpg"))
        self.assertTrue(da.descriptive_name("נעל_עור_אדומה_מהצד.png"))
        for name in ("IMG_4032.jpg", "DSC00012.JPG", "PXL_20250101_101010.jpg", "WhatsApp Image 2025-01-01.jpeg",
                     "photo.jpg", "a1b2c3d4e5f6.jpg", "final final.png"):
            with self.subTest(name=name):
                self.assertEqual(da.descriptive_name(name), {})
        self.drive.add("PRODUCTS_FOLDER_3", "מוצרים")
        media(self.drive, "NAMED_FILE_abcde", "red leather sneaker side view.jpg", "PRODUCTS_FOLDER_3")
        self.connect("PRODUCTS_FOLDER_3", "product_catalog")
        self.run_all()
        item = self.items()["NAMED_FILE_abcde"]
        self.assertEqual((item["state"], item["tier"], item["cost_usd"]), ("indexed", "name", 0.0))
        self.assertEqual(self.vision_calls, [])

    def test_tier0_metadata(self):
        raw = {"id": "F1", "name": "IMG_1.HEIC", "mimeType": "image/heic", "size": "2048",
               "imageMediaMetadata": {"width": 4000, "height": 3000, "time": "2025:06:01 10:00:00",
                                      "cameraMake": "Apple", "cameraModel": "iPhone 15",
                                      "location": {"latitude": 32.1, "longitude": 34.8}}}
        out = da.tier0(raw, {"path": "מוצרים / קיץ", "conn_id": "dc1", "root_id": "dc1"})
        self.assertEqual((out["width"], out["height"], out["format"], out["media_type"]), (4000, 3000, "heic", "image"))
        self.assertEqual(out["exif"], {"taken": "2025:06:01 10:00:00", "camera": "Apple iPhone 15", "has_location": True})
        self.assertNotIn("32.1", json.dumps(out))                         # never a coordinate

    def test_dedupe_one_description_per_group(self):
        self.drive.add("PRODUCTS_FOLDER_4", "מוצרים")
        for n in range(4):
            media(self.drive, f"ANGLE_{n}_abcdef", f"IMG_{n:04d}.jpg", "PRODUCTS_FOLDER_4")
            self.previews[f"ANGLE_{n}_abcdef"] = f"angle{n}".encode()
        media(self.drive, "OTHER_abcdef0000", "IMG_0099.jpg", "PRODUCTS_FOLDER_4")
        self.previews["OTHER_abcdef0000"] = b"other"
        self.hashes.update({b"angle0": "ffffffff00000000", b"angle1": "ffffffff00000001",
                            b"angle2": "ffffffff00000003", b"angle3": "fffffffe00000000",
                            b"other": "00000000ffffffff"})
        self.connect("PRODUCTS_FOLDER_4", "product_catalog")
        self.run_all()
        images_described = sum(n for n, _names, _m in self.vision_calls)
        self.assertEqual(images_described, 2)                            # one per group
        tiers = sorted(i["tier"] for i in self.items().values())
        self.assertEqual(tiers, ["group", "group", "group", "vision", "vision"])
        followers = [i for i in self.items().values() if i["tier"] == "group"]
        leader = self.items()[followers[0]["group_of"]]
        self.assertEqual({f["description"] for f in followers}, {leader["description"]})
        self.assertTrue(all("angle" in f["angle_note"] and f["cost_usd"] == 0 for f in followers))

    def test_dhash_hamming_tolerates_another_angle(self):
        self.assertEqual(da.hamming("ffffffff00000000", "ffffffff00000003"), 2)
        self.assertLessEqual(da.hamming("ffffffff00000000", "fffffffe00000000"), da.GROUP_DISTANCE)
        self.assertGreater(da.hamming("ffffffff00000000", "00000000ffffffff"), da.GROUP_DISTANCE)

    def test_video_first_frame_costs_one_image(self):
        self.drive.add("VIDEOS_FOLDER_01", "מוצרים")
        media(self.drive, "VIDEO_abcdef0000", "VID_0001.mp4", "VIDEOS_FOLDER_01", mime="video/mp4",
              videoMediaMetadata={"width": 1080, "height": 1920, "durationMillis": 14500})
        self.connect("VIDEOS_FOLDER_01", "product_catalog")
        self.run_all()
        item = self.items()["VIDEO_abcdef0000"]
        self.assertEqual((item["media_type"], item["duration_s"], item["width"], item["height"], item["format"]),
                         ("video", 14.5, 1080, 1920, "mp4"))
        self.assertEqual(self.vision_calls[0][0], 1)                     # ONE frame, never several
        self.assertIn("first usable frame of a video", self.vision_calls[0][1][0])
        self.assertEqual(item["frame"]["frame_source"], "first_frame")

    def test_video_frame_skips_black_frames(self):
        frames = {0.0: b"BLACK", 0.5: b"BLACK", 1.0: b"\xff\xd8real"}
        asked = []
        saved = (da._ffmpeg_frame, da.is_black)
        da._ffmpeg_frame = lambda fid, at: asked.append(at) or frames.get(at, b"")
        da.is_black = lambda data: data == b"BLACK"
        try:
            frame, info = da.video_frame({"file_id": "V", "duration_s": 10})
        finally:
            da._ffmpeg_frame, da.is_black = saved
        self.assertEqual((frame, info["frame_at_s"]), (b"\xff\xd8real", 1.0))
        self.assertEqual(asked, [0.0, 0.5, 1.0])                         # stops at the first usable one

    def test_video_frame_falls_back_to_drive_thumbnail(self):
        saved = (da._ffmpeg_frame, da.is_black, da._fresh_thumb_link)
        da._ffmpeg_frame = lambda fid, at: b""
        da.is_black = lambda data: False
        da._fresh_thumb_link = lambda item: "https://lh3.example/t=s220"
        try:
            frame, info = da.video_frame({"file_id": "V", "duration_s": 10})
        finally:
            da._ffmpeg_frame, da.is_black, da._fresh_thumb_link = saved
        self.assertEqual((frame, info["frame_source"]), (b"thumb", "drive_thumbnail"))

    def test_batched_cheapest_model_and_512px_link(self):
        self.drive.add("PRODUCTS_FOLDER_5", "מוצרים")
        for n in range(11):
            media(self.drive, f"BATCH_{n:02d}_abcdef", f"IMG_{n:04d}.jpg", "PRODUCTS_FOLDER_5")
        self.connect("PRODUCTS_FOLDER_5", "product_catalog")
        self.run_all()
        self.assertEqual([n for n, _names, _m in self.vision_calls], [8, 3])
        self.assertTrue(all(m == claude_json.CLASSIFY_MODEL for _n, _names, m in self.vision_calls))
        self.assertEqual(da._sized_link("https://lh3.example/abc=s220"), "https://lh3.example/abc=s512")
        self.assertEqual(da.PREVIEW_PX, 512)

    def test_newest_first(self):
        self.cfg["batch_items"] = 5
        self.drive.add("PRODUCTS_FOLDER_6", "מוצרים")
        for n in range(9):
            media(self.drive, f"AGE_{n}_abcdef00", f"IMG_{n:04d}.jpg", "PRODUCTS_FOLDER_6",
                  modified=f"2026-0{n + 1}-01T00:00:00Z")
        self.connect("PRODUCTS_FOLDER_6", "product_catalog")
        self.run_all()
        indexed = sorted(k for k, i in self.items().items() if i["state"] == "indexed")
        self.assertEqual(indexed, [f"AGE_{n}_abcdef00" for n in range(4, 9)])
        self.assertEqual(da.view_state(CID)["state"], "older")

@unittest.skipUnless(HAVE_PIL or os.environ.get("CI"), "Pillow not installed here (CI and prod have it)")
class RealPillow(unittest.TestCase):
    def png(self, color, size=(800, 600), stripes=False):
        import io
        from PIL import Image as PILImage
        img = PILImage.new("RGB", size, color)
        if stripes:
            for x in range(0, size[0], 40):
                for y in range(size[1]):
                    img.putpixel((x, y), (255, 255, 255))
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()

    def test_black_frame_downscale_and_dhash(self):
        import io
        from PIL import Image as PILImage
        self.assertTrue(REAL["is_black"](self.png((0, 0, 0))))
        self.assertFalse(REAL["is_black"](self.png((200, 120, 40))))
        small = REAL["downscale"](self.png((10, 200, 10), (2000, 1000)))
        self.assertEqual(PILImage.open(io.BytesIO(small)).size, (512, 256))
        a = REAL["dhash"](self.png((0, 0, 0), stripes=True))
        b = REAL["dhash"](self.png((0, 0, 0), stripes=True))
        self.assertEqual((len(a), da.hamming(a, b)), (16, 0))

# ─── 7. The free cap, the quote, credits ────────────────────────────────────

class Cap(Base):
    def setUp(self):
        super().setUp()
        self.cfg["free_items"] = 10
        self.drive.add("BIG_FOLDER_00001", "מוצרים")
        for n in range(25):
            media(self.drive, f"CAP_{n:02d}_abcdef", f"IMG_{n:04d}.jpg", "BIG_FOLDER_00001",
                  modified=f"2026-10-{n + 1:02d}T00:00:00Z")
        self.root = self.connect("BIG_FOLDER_00001", "product_catalog")

    def test_stops_at_cap_quotes_measured_cost_and_opens_one_request(self):
        self.run_all()
        state = self.mem.assets[CID]
        self.assertEqual(sum(1 for i in state["items"].values() if i["state"] == "indexed"), 10)
        meter = state["meter"]
        self.assertEqual(meter["items"], 10)
        self.assertAlmostEqual(meter["avg_usd"], self.usd_per_image, places=6)   # MEASURED, from usage
        cap = state["cap"]
        self.assertTrue(cap["active"])
        self.assertEqual(cap["remaining"], 15)
        self.assertAlmostEqual(cap["quote_usd"], 15 * self.usd_per_image, places=6)
        self.assertEqual(len(self.team), 1)
        cid, persona, topic, text, origin = self.team[0]
        self.assertEqual((cid, topic, origin), (CID, "billing", "drive_assets"))
        for part in ("Remaining items: 15", "Measured cost per item", "Quote", "מוצרים", str(CID)):
            self.assertIn(part, text)
        self.assertLessEqual(len(text), 500)
        calls = len(self.vision_calls)
        da.request(CID, "manual")
        self.run_all()
        self.assertEqual(len(self.vision_calls), calls)                  # nothing more spent
        self.assertEqual(len(self.team), 1)                              # ONE request per episode
        view = da.view_state(CID)
        self.assertEqual(view["state"], "cap")
        self.assertIn("10", view["message"])
        self.assertIn("נציג uallak", view["message"])
        self.assertEqual(dfo.by_id(CID, self.root["id"])["status"], "cap_reached")

    def test_resume_after_credits_from_where_it_stopped(self):
        self.run_all()
        before = {k for k, i in self.items().items() if i["state"] == "indexed"}
        da.load_credits(CID, 5, usd=0.01, note="paid")
        self.run_all()
        after = {k for k, i in self.items().items() if i["state"] == "indexed"}
        self.assertEqual(len(after), 15)
        self.assertTrue(before < after)                                  # nothing re-described
        newly = sorted(after - before)
        self.assertEqual(newly, [f"CAP_{n:02d}_abcdef" for n in range(10, 15)])   # the next newest
        self.assertFalse(self.mem.assets[CID]["cap"].get("active"))
        self.assertEqual(self.mem.assets[CID]["credits"]["extra_items"], 5)


# ─── 8. Daily incremental scan ──────────────────────────────────────────────

class DailyScan(Base):
    def test_new_and_changed_only_never_re_describe(self):
        self.drive.add("DAILY_FOLDER_001", "מוצרים")
        for n in range(3):
            media(self.drive, f"DAY_{n}_abcdef00", f"IMG_{n:04d}.jpg", "DAILY_FOLDER_001")
        self.connect("DAILY_FOLDER_001", "product_catalog")
        self.run_all()
        first = {k: dict(v) for k, v in self.items().items()}
        self.vision_calls.clear()
        media(self.drive, "DAY_NEW_abcdef00", "IMG_0100.jpg", "DAILY_FOLDER_001")
        self.drive.files["DAY_1_abcdef00"]["name"] = "IMG_0001 renamed.jpg"       # rename only
        self.drive.files["DAY_2_abcdef00"]["md5Checksum"] = "changed-content"     # new content
        da.request(CID, "daily")
        self.run_all()
        self.assertEqual(sum(n for n, _names, _m in self.vision_calls), 2)        # the new + the changed
        items = self.items()
        self.assertEqual(items["DAY_0_abcdef00"]["indexed_at"], first["DAY_0_abcdef00"]["indexed_at"])
        self.assertEqual(items["DAY_1_abcdef00"]["indexed_at"], first["DAY_1_abcdef00"]["indexed_at"])
        self.assertEqual(items["DAY_1_abcdef00"]["name"], "IMG_0001 renamed.jpg")
        self.assertEqual(items["DAY_NEW_abcdef00"]["state"], "indexed")
        self.assertTrue(dfo.tree(CID)[0]["last_auto_scan_at"])
        self.vision_calls.clear()
        da.request(CID, "daily")
        self.run_all()
        self.assertEqual(self.vision_calls, [])                                    # nothing new

    def test_daily_scan_respects_the_cap(self):
        self.cfg["free_items"] = 3
        self.drive.add("DAILY_FOLDER_002", "מוצרים")
        for n in range(3):
            media(self.drive, f"DCAP_{n}_abcdef0", f"IMG_{n:04d}.jpg", "DAILY_FOLDER_002")
        self.connect("DAILY_FOLDER_002", "product_catalog")
        self.run_all()
        media(self.drive, "DCAP_NEW_abcdef0", "IMG_0200.jpg", "DAILY_FOLDER_002")
        self.drive.change("DCAP_NEW_abcdef0")           # Drive reports it in the changes feed
        self.vision_calls.clear()
        da.daily_scan(60)
        self.run_all()
        self.assertEqual(self.vision_calls, [])
        self.assertEqual(self.items()["DCAP_NEW_abcdef0"]["state"], "pending")

    def test_scheduler_job_and_routes(self):
        jobs = _read("scripts", "schedule_jobs.sh")
        self.assertIn('job drive-assets-scan GET "15 4 * * *" "/api/drive/assets/scan"', jobs)
        src = _read("core", "api_server.py")
        self.assertIn('@app.get("/api/drive/assets/scan", dependencies=_admin_only)', src)
        self.assertIn('@app.post("/api/client/drive-folders/reindex")', src)


# ─── 9. find_assets ─────────────────────────────────────────────────────────

class Find(Base):
    def setUp(self):
        super().setUp()
        self.drive.add("PRODUCTS_FIND_001", "מוצרים")
        self.drive.add("RAW_FIND_FOLDER01", "חומרי גלם")
        media(self.drive, "RED_SHOE_abcdef0", "red leather sneaker.jpg", "PRODUCTS_FIND_001")
        media(self.drive, "BLUE_BAG_abcdef0", "blue canvas tote bag.jpg", "PRODUCTS_FIND_001")
        media(self.drive, "SHOE_CLIP_abcdef", "red sneaker unboxing clip.mp4", "PRODUCTS_FIND_001",
              mime="video/mp4", videoMediaMetadata={"width": 1080, "height": 1920, "durationMillis": 8000})
        media(self.drive, "RAW_SHOE_abcdef0", "red sneaker sole leather scraps.jpg", "RAW_FIND_FOLDER01")
        self.connect("PRODUCTS_FIND_001", "product_catalog")
        self.connect("RAW_FIND_FOLDER01", "raw_materials")
        self.run_all()

    def test_ranking_role_order_and_fields(self):
        found = da.find_assets(CID, ["product_catalog", "raw_materials"], "red leather sneaker", "image")
        self.assertEqual([f["file_id"] for f in found][:2], ["RED_SHOE_abcdef0", "RAW_SHOE_abcdef0"])
        top = found[0]
        for key in ("file_id", "description", "tags", "role", "media_type"):
            self.assertIn(key, top)
        self.assertEqual((top["role"], top["media_type"], top["folder"]), ("product_catalog", "image", "מוצרים"))
        self.assertNotIn("BLUE_BAG_abcdef0", [f["file_id"] for f in found])

    def test_media_type_filter_and_role_filter(self):
        videos = da.find_assets(CID, [], "sneaker", "video")
        self.assertEqual([f["file_id"] for f in videos], ["SHOE_CLIP_abcdef"])
        self.assertEqual(videos[0]["duration_s"], 8.0)
        raw_only = da.find_assets(CID, ["raw_materials"], "sneaker")
        self.assertEqual({f["role"] for f in raw_only}, {"raw_materials"})

    def test_on_demand_describes_a_pending_match(self):
        root = dfo.for_folder(CID, "PRODUCTS_FIND_001")
        raw = {"id": "LATE_IMG_abcdef0", "name": "IMG_7777.jpg", "mimeType": "image/jpeg",
               "modifiedTime": "2026-11-20T00:00:00Z", "md5Checksum": "late"}
        state = da._load(CID)
        state["items"]["LATE_IMG_abcdef0"] = {**da.tier0(raw, {"path": "מוצרים", "conn_id": root["id"],
                                                               "root_id": root["id"]}),
                                              "state": "pending", "attempts": 0, "first_seen_run": "old"}
        da._save(CID, state)
        calls = len(self.vision_calls)
        found = da.find_assets(CID, ["product_catalog"], "", "image", limit=20, on_demand=True)
        self.assertEqual(self.items()["LATE_IMG_abcdef0"]["state"], "indexed")
        self.assertEqual(len(self.vision_calls), calls + 1)
        self.assertIn("LATE_IMG_abcdef0", [f["file_id"] for f in found])

# ─── 10. The media agent: choose real first, object when nothing real ──────

class MediaAgent(Base):
    def brief(self, **over):
        from core import post_brief as pb
        base = dict(asset_id="p1", post_format="text_carousel", objective="consideration", client_id=CID,
                    role="proof", copy_headline="", copy_body="", asset_type="real_photo",
                    visual_direction="a finished red leather sneaker on a pedestal", set_id="set-1")
        base.update(over)
        return pb.AssetBrief(**base)

    def test_proof_slide_without_a_real_asset_is_an_objection(self):
        from agents import media_agent
        out = media_agent.review_brief(self.brief())
        self.assertIn("No real asset", out["objection"])
        self.assertIn("Never generate", out["objection"])
        for fmt in ("product_carousel", "before_after", "customer_story"):
            with self.subTest(fmt=fmt):
                self.assertTrue(media_agent.review_brief(self.brief(role="point", post_format=fmt))["objection"])

    def test_customer_photo_is_used_like_any_real_asset(self):
        from agents import media_agent
        self.drive.add("CUSTOMERS_MA_0001", "ביקורות")
        media(self.drive, "CUST_RED_abcdef0", "red leather sneaker on customer.jpg", "CUSTOMERS_MA_0001")
        self.connect("CUSTOMERS_MA_0001", "customer_photos")
        self.run_all()
        saved = media_agent.safe_claude_json_call
        media_agent.safe_claude_json_call = lambda system, user, **k: {"file_id": "CUST_RED_abcdef0", "reason": "x"}
        try:
            out = media_agent.review_brief(self.brief())
        finally:
            media_agent.safe_claude_json_call = saved
        self.assertEqual((out["objection"], out["real_asset"]["source_file_id"]), ("", "CUST_RED_abcdef0"))

    def test_agent_chooses_a_real_asset_itself(self):
        from agents import media_agent
        self.drive.add("PRODUCTS_MA_00001", "מוצרים")
        media(self.drive, "MA_SHOE_abcdef00", "red leather sneaker pedestal.jpg", "PRODUCTS_MA_00001")
        self.connect("PRODUCTS_MA_00001", "product_catalog")
        self.run_all()
        saved = media_agent.safe_claude_json_call
        media_agent.safe_claude_json_call = lambda system, user, **k: {"file_id": "MA_SHOE_abcdef00",
                                                                       "reason": "the real product"}
        try:
            out = media_agent.review_brief(self.brief())
        finally:
            media_agent.safe_claude_json_call = saved
        self.assertEqual(out["objection"], "")
        self.assertEqual((out["real_asset"]["source_file_id"], out["real_asset"]["folder"],
                          out["real_asset"]["role"]), ("MA_SHOE_abcdef00", "מוצרים", "product_catalog"))

    def test_generate_from_brief_uses_the_real_asset_before_any_key(self):
        from agents import media_agent
        self.drive.add("PRODUCTS_MA_00002", "מוצרים")
        media(self.drive, "MA_SHOE2_abcdef0", "red leather sneaker pedestal.jpg", "PRODUCTS_MA_00002")
        self.connect("PRODUCTS_MA_00002", "product_catalog")
        self.run_all()
        used = []
        saved = (media_agent.safe_claude_json_call, media_agent._use_real_asset, media_agent._generation_key)
        media_agent.safe_claude_json_call = lambda system, user, **k: {"file_id": "MA_SHOE2_abcdef0", "reason": "x"}
        media_agent._use_real_asset = lambda cid, brief, chosen, platform: used.append(chosen["file_id"]) or {
            "success": True, "kind": "image", "file_id": "COPY_0123456789", "credits": 0,
            "real_asset": media_agent._real_summary(chosen)}
        media_agent._generation_key = lambda cid: self.fail("asked for the image tool's key")
        try:
            out = media_agent.generate_from_brief(CID, self.brief(role="point", asset_type="generated_image"), {})
        finally:
            media_agent.safe_claude_json_call, media_agent._use_real_asset, media_agent._generation_key = saved
        self.assertEqual((out["success"], out["credits"], used), (True, 0, ["MA_SHOE2_abcdef0"]))
        self.assertEqual(out["real_asset"]["folder"], "מוצרים")

    def test_real_asset_is_referenced_never_copied(self):
        # core/asset_refs: the chosen file keeps its own id; tests/test_asset_refs.py covers the rest
        src = _read("agents", "media_agent.py")
        body = src[src.index("def _use_real_asset"):src.index("def generate_from_brief")]
        self.assertNotIn("copy_file(", body)
        self.assertIn('copy = {"id": chosen["file_id"]', body)


# ─── 11. Text-only post with the image tool disconnected; the card ─────────

class Production(Base):
    def test_text_only_post_produces_without_the_image_tool(self):
        from core import agent_actions, client_state, post_pipeline
        submitted = []
        saved = (agent_actions._media_tool_status, client_state.cached_package, post_pipeline.submit)
        agent_actions._media_tool_status = lambda cid: "not_connected"
        client_state.cached_package = lambda cid: None
        post_pipeline.submit = lambda cid, raw, msg, lang, persona="media": submitted.append(raw) or {
            "line": "queued", "queued": {"id": "x"}}
        try:
            text_only = {"post": {"assets": [{"asset_type": "text_slide"}, {"asset_type": "text_slide"}]}}
            out = agent_actions._prepare_produce_post(CID, text_only, {"language": "he"}, [])
            self.assertNotIn("switch_to", out)
            self.assertEqual(len(submitted), 1)
            mixed = {"post": {"assets": [{"asset_type": "text_slide"}, {"asset_type": "real_photo"}]}}
            self.assertNotIn("switch_to", agent_actions._prepare_produce_post(CID, mixed, {"language": "he"}, []))
            generated = {"post": {"assets": [{"asset_type": "text_slide"}, {"asset_type": "generated_image"}]}}
            out = agent_actions._prepare_produce_post(CID, generated, {"language": "he"}, [])
            self.assertEqual(out.get("switch_to"), "connect_creative_tool")
        finally:
            agent_actions._media_tool_status, client_state.cached_package, post_pipeline.submit = saved

    def test_card_shows_source_folder_and_role_keeps_concept_for_generated(self):
        from core import social_queue as sq
        line = sq.source_text({"folder": "מוצרים", "role": "product_catalog", "role_text": "קטלוג מוצרים"}, "he")
        self.assertIn("מוצרים", line)
        self.assertIn("קטלוג מוצרים", line)
        self.assertEqual(sq.source_text({}, "he"), "")
        src = _read("core", "post_pipeline.py")
        self.assertIn('flags = ["concept"] if "concept" in (slide.get("flags") or []) and not out.get("real_asset") else []', src)
        html = _read("dashboard", "client", "index.html")
        self.assertIn("s.source_text", html)
        self.assertIn("g.source_text", html)
        for lang in ("he", "en", "fr", "ar", "ru"):
            self.assertTrue(sq.pick(sq.SOURCE_TEXT, lang))


# ─── 12. Wiring ─────────────────────────────────────────────────────────────

class Wiring(unittest.TestCase):
    def test_no_oauth_scope_added(self):
        from core import google_scopes
        src = _read("core", "google_scopes.py")
        self.assertNotIn("auth/drive.readonly", src)
        self.assertNotIn("drive_folders", src)
        self.assertTrue(google_scopes)

    def test_deletion_registries(self):
        for path in (("core", "admin_service.py"), ("core", "deletion_guard.py")):
            src = _read(*path)
            for key in ("drive_folders:", "drive_assets:", "drive_assets_lock:"):
                self.assertIn(f'"{key}"', src, path)
        src = _read("core", "client_deletion.py")
        for key in ("drive_folders", "drive_assets", "drive_assets_lock"):
            self.assertIn(f'f"{key}:{{int(i)}}"', src)

    def test_settings_and_admin_view(self):
        from core import admin_view, client_settings
        self.assertEqual(client_settings.FIELDS["drive_assets_free_items"]["paying"], 150)
        self.assertIn("client_drive_folders", admin_view.READ_ENDPOINTS)
        self.assertNotIn("client_drive_folders_step", admin_view.READ_ENDPOINTS)

    def test_ui_section(self):
        html = _read("dashboard", "client", "index.html")
        self.assertIn("תיקיות דרייב מחוברות", html)
        self.assertIn('id="driveCopyBtn"', html)
        self.assertIn("data-drive-exclude", html)
        self.assertIn("/api/client/drive-folders/preview", html)
        self.assertIn("/api/client/drive-folders/connect", html)

    def test_dockerfile_has_ffmpeg(self):
        self.assertIn("ffmpeg", _read("Dockerfile"))


if __name__ == "__main__":
    unittest.main()
