"""Client 27's real miss (2026-11-12, docs/DRIVE-PULL.md §Why test3.mp4 was
"not found"), with the real names.

Production facts (scripts/diagnose_media.py 27 "test3"): the connected folder
(under the media root, in our Shared Drive) holds test3.mp4 at its ROOT, put
there by hand, plus images/, self-test/ …; the agents' find_media_file and the
Media tab answered "not found" while the search EVERYWHERE found it "already
in the client folder".

Root cause: drive_mirror.catalog (the ONE list behind find_media_file, the
Media tab and the agents' newest-10) asked Drive ONE multi-parent query —
('root' in parents or 'images' in parents or …) — with no `corpora`. On a
Shared Drive that searches only the service account's own "user" corpus, so a
file the owner dropped in never came back; a single-parent listing (the
diagnose tree, list_children) and corpora=allDrives (find everywhere) did see
it. The fake Drive below answers exactly like that, and the catalog now lists
every folder with list_children.

A. The matcher with the exact names and queries.
B. find_media_file / Media tab / newest on a Drive that answers multi-parent
   queries like production did.
C. The diagnosis calls the SAME function as the agent tool.
D. The second, non-connected folder: nothing automatic.
E. A pasted link to a file already in the folder: said so, shown at once.

Stdlib only; nothing talks to Google. Run:  python tests/test_media_find_real_names.py -v
"""
import copy
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_drive_pull as P                        # noqa: E402
import test_drive_link as L                        # noqa: E402
from core import media_search as ms                # noqa: E402

FOLDER = P.FOLDER


def _in(self, col, values):                        # the fake DB learns .in_() (media_library)
    self.filters.append(lambda r, c=col, v=tuple(values): r.get(c) in v)
    return self


P._Q.in_ = _in


class ProductionLikeDrive(L.Drive):
    """Single-parent listings see everything; a MULTI-parent query without
    corpora sees only what the service account itself created (by_sa) — what
    production answered for client 27."""

    def list_in_folders(self, folder_ids, max_items=2000):
        return [f for f in super().list_in_folders(folder_ids, max_items) if f.get("by_sa")]

    def search_in_folders(self, folder_ids, name_words=None, mime_prefix="", order_by="", page_size=50, fields=""):
        return [f for f in super().search_in_folders(folder_ids, name_words, mime_prefix, order_by, page_size, fields)
                if f.get("by_sa")]


# ─── A. The matcher ─────────────────────────────────────────────────────────

class RealNames(unittest.TestCase):
    def test_test3_every_way_it_is_typed(self):
        for query in ("test3", "test 3", "TEST3", "test3 ", " Test3", "test_3", "the video test3"):
            self.assertEqual(ms.score("test3.mp4", query), 300, query)
            self.assertEqual(ms.score("Test3.jpg", query), 300, query)

    def test_google_2_and_the_hebrew_name(self):
        self.assertGreaterEqual(ms.score("uallak google 2.mp4", "google 2"), 200)
        self.assertEqual(ms.score("uallak google 2.mp4", "test3"), 0)
        self.assertGreaterEqual(ms.score("בדיקה - תמונה גדולה.jpg", "תמונה גדולה"), 200)
        self.assertEqual(ms.score("בדיקה - תמונה גדולה.jpg", "בדיקה - תמונה גדולה"), 300)

    def test_best_only_keeps_the_exact_names_together(self):
        items = [{"id": "a", "name": "test3.mp4", "kind": "video", "modified_time": "2"},
                 {"id": "b", "name": "Test3.jpg", "kind": "image", "modified_time": "1"},
                 {"id": "c", "name": "test30.mp4", "kind": "video", "modified_time": "3"},
                 {"id": "d", "name": "uallak google 2.mp4", "kind": "video", "modified_time": "4"}]
        self.assertEqual([i["id"] for i in ms.search(items, "test3", best_only=True)], ["a", "b"])
        self.assertEqual([i["id"] for i in ms.search(items, "test3", kind="video", best_only=True)], ["a"])


# ─── The client-27 tree ─────────────────────────────────────────────────────

class Base(L.Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stubs.put("core.tiktok_uploads", P._module("core.tiktok_uploads", load=lambda cid: []))
        for name in ("core.agent_actions", "core.media_library"):
            sys.modules.pop(name, None)
        import core
        for attr in ("agent_actions", "media_library"):
            if hasattr(core, attr):
                delattr(core, attr)
        from core import agent_actions, media_library
        cls.aa, cls.ml = agent_actions, media_library

    @classmethod
    def tearDownClass(cls):
        import core
        for name in ("core.agent_actions", "core.media_library"):
            sys.modules.pop(name, None)
        for attr in ("agent_actions", "media_library"):
            if hasattr(core, attr):
                delattr(core, attr)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        old = self.fake
        self.fake = d = ProductionLikeDrive()
        d.files, d.unshared = old.files, old.unshared
        for n in P.DRIVE_FUNCS + L.EXTRA:
            setattr(self.drv, n, getattr(d, n))
        self.ml._db = lambda: self.db
        self.ml.forget(7)
        # everything the system itself put there was created by the service account
        for f in d.files.values():
            f["by_sa"] = True
        # what the owner dropped in BY HAND (not created by the service account)
        d.add("ST7", "self-test", FOLDER, "C7", driveId="OURDRIVE")
        d.add("12CyXWPl7pMzw4FzIppouvHO5coe97d6j", "test3.mp4", "video/mp4", "C7", 5 * 1048576,
              "2026-11-12T07:00:00Z", driveId="OURDRIVE")
        d.add("google2_file", "uallak google 2.mp4", "video/mp4", "V7", 2048, "2026-11-11T07:00:00Z", driveId="OURDRIVE")
        d.add("hebrew_image", "בדיקה - תמונה גדולה.jpg", "image/jpeg", "I7", 4096, "2026-11-10T07:00:00Z",
              driveId="OURDRIVE")
        d.add("probe_file01", "uallak-selftest-probe.txt", "text/plain", "ST7", 3, driveId="OURDRIVE")
        # the SECOND, non-connected folder: outside the media root
        d.add("1DexUvPz7EQCOE7JvxoOnxTzCbXL0QdSM", "client-7 — Cafe", FOLDER, "1qEynQyvT0zn95zZAHdI6EseqYq2UqqIF",
              modified="2025-01-01T00:00:00Z", driveId="")
        d.add("old_test3_jpg", "Test3.jpg", "image/jpeg", "1DexUvPz7EQCOE7JvxoOnxTzCbXL0QdSM", 77, driveId="")
        d.add("old_test3_mp4", "test3.mp4", "video/mp4", "1DexUvPz7EQCOE7JvxoOnxTzCbXL0QdSM", 99,
              "2025-06-01T00:00:00Z", driveId="")
        self.T3 = "12CyXWPl7pMzw4FzIppouvHO5coe97d6j"


# ─── B. find_media_file, the Media tab, newest ──────────────────────────────

class FindTheRealFile(Base):
    def test_the_old_multi_parent_query_is_what_missed_it(self):
        ids = [f["id"] for f in self.drv.list_in_folders(["C7", "V7", "I7", "D7", "T7", "ST7"])]
        self.assertNotIn(self.T3, ids)            # how production answered: the root cause

    def test_the_mirror_never_uses_a_multi_parent_query(self):
        src = P._read("core", "drive_mirror.py")
        self.assertNotIn("drive.list_in_folders(", src)
        self.assertNotIn("drive.search_in_folders(", src)
        for fn in ("search_in_folders", "list_in_folders"):
            body = P._read("core", "drive_service.py")
            body = body[body.index(f"def {fn}("):]
            body = body[:body.index("\ndef ")]
            self.assertIn('"corpora": "allDrives"', body, fn)

    def test_find_media_file_finds_test3_at_the_root_every_way(self):
        for query in ("test3", "test 3", "TEST3", "test3 "):
            out = self.aa.find_media_lookup(7, query)
            self.assertEqual([m["id"] for m in out["matches"]], [self.T3], query)
            self.assertEqual(out["matches"][0]["folder"], "")              # the folder root
            self.assertEqual(out["matches"][0]["name"], "test3.mp4")

    def test_the_other_real_names(self):
        self.assertEqual([m["id"] for m in self.aa.find_media_lookup(7, "google 2")["matches"]], ["google2_file"])
        hit = self.aa.find_media_lookup(7, "תמונה גדולה")["matches"]
        self.assertEqual([(m["id"], m["folder"]) for m in hit], [("hebrew_image", "images")])

    def test_the_agent_tool_answers_with_the_file(self):
        params = self.aa.validate_params(self.aa.ACTIONS["find_media_file"], {"name": "test3"}, 7)
        out = self.aa._prepare_find_media(7, params, {"language": "he"}, [])
        self.assertIn("test3.mp4", out.get("clarify", ""))
        self.assertNotIn("switch_to", out)                                 # never "not found"

    def test_the_media_tab_shows_and_finds_it(self):
        everything = {i["id"] for i in self.ml.view(7, limit=200)["items"]}
        self.assertIn(self.T3, everything)
        self.assertIn(self.T3, {i["id"] for i in self.ml.view(7, folder="")["items"]})   # "main folder only"
        for query in ("test3", "TEST3"):
            self.assertEqual([i["id"] for i in self.ml.view(7, q=query)["items"]], [self.T3], query)
        # "test 3": the exact file first; the tab also lists weaker partial matches below it
        self.assertEqual(self.ml.view(7, q="test 3")["items"][0]["id"], self.T3)
        self.assertEqual([i["id"] for i in self.ml.view(7, q="google 2")["items"]], ["google2_file"])

    def test_the_agents_newest_ten_include_it(self):
        vids = self.dm.newest(7, "video", 10)
        self.assertEqual(vids[0]["id"], self.T3)
        self.assertEqual(vids[0]["folder"], "")
        self.assertIn("hebrew_image", [i["id"] for i in self.dm.newest(7, "image", 10)])
        self.assertEqual(self.dm.newest(7, "document", 10), [])

    def test_a_cached_listing_is_dropped_by_a_refresh(self):
        self.dm.catalog(7)                                          # cached without the new file
        self.fake.add("late_file_01", "late.mp4", "video/mp4", "C7", 1, "2026-11-12T09:00:00Z", driveId="OURDRIVE")
        self.assertNotIn("late_file_01", {i["id"] for i in self.dm.catalog(7)["items"]})
        self.assertIn("late_file_01", [m["id"] for m in self.aa.find_media_lookup(7, "late")["matches"]])  # find: fresh


# ─── C. The diagnosis = the agent's own function ────────────────────────────

class Diagnosis(Base):
    def test_diagnosis_calls_the_agent_tools_function(self):
        src = P._read("core", "drive_merge.py")
        body = src[src.index("def media_diagnosis"):]
        self.assertIn("agent_actions.find_media_lookup(client_id, find)", body)
        self.assertIn("media_library.view(client_id, q=find", body)
        self.assertNotIn("drive_mirror.find(", body)
        aa = P._read("core", "agent_actions.py")
        prep = aa[aa.index("def _prepare_find_media"):]
        prep = prep[:prep.index("\ndef ")]
        self.assertIn("find_media_lookup(client_id, params[\"name\"]", prep)
        self.assertNotIn("drive_mirror.find(", prep)

    def test_diagnosis_finds_test3_and_reports_no_catalog_gap(self):
        out = self.dmg.media_diagnosis(7, "test3")
        self.assertEqual([m["id"] for m in out["find_in_client_folder"]], [self.T3])
        self.assertEqual([m["id"] for m in out["find_in_media_tab"]], [self.T3])
        self.assertEqual(out["catalog_missing"], [])
        anywhere = {c["id"]: c for c in out["find_anywhere"]["candidates"]}
        self.assertTrue(anywhere[self.T3]["in_client_folder"])
        self.assertFalse(anywhere["old_test3_mp4"]["in_client_folder"])

    def test_a_catalog_gap_would_be_named(self):
        real = self.dm.catalog
        self.dm.catalog = lambda cid, refresh=False: {**real(cid, refresh=True),
                                                      "items": [i for i in real(cid, refresh=True)["items"]
                                                                if i["id"] != self.T3]}
        try:
            out = self.dmg.media_diagnosis(7, "")
        finally:
            self.dm.catalog = real
        self.assertEqual([m["name"] for m in out["catalog_missing"]], ["test3.mp4"])

    def test_the_script_prints_both_product_paths_and_the_gap(self):
        src = P._read("scripts", "diagnose_media.py")
        for needle in ("find_in_client_folder", "find_in_media_tab", "catalog_missing", "find_media_lookup"):
            self.assertIn(needle, src)
        compile(src, "diagnose_media.py", "exec")


# ─── D. The second folder ───────────────────────────────────────────────────

class SecondFolder(Base):
    def test_nothing_in_it_is_copied_or_found_as_the_clients(self):
        hit = self.aa.find_media_lookup(7, "test3")["matches"]
        self.assertEqual([m["id"] for m in hit], [self.T3])        # not Test3.jpg / the older test3.mp4
        self.dmg.media_diagnosis(7, "test3")
        self.assertEqual(self.fake.copies, [])                     # a diagnosis copies nothing

    def test_merge_stays_copy_only_and_skips_same_name_and_size(self):
        self.fake.add("same_in_c7", "Test3.jpg", "image/jpeg", "C7", 77, driveId="OURDRIVE")
        before = copy.deepcopy({k: v for k, v in self.fake.files.items() if k.startswith("old_")})
        self.dmg.start(7, "1DexUvPz7EQCOE7JvxoOnxTzCbXL0QdSM")
        job = self.run_job(rounds=20)
        status = {f["name"]: f["status"] for f in job["files"]}
        self.assertEqual(status["Test3.jpg"], "already_same")
        self.assertEqual(status["test3.mp4"], "copied")            # same name, other size → copied
        self.assertEqual(self.fake.copies, [("old_test3_mp4", "C7")])
        for fid, f in before.items():
            self.assertEqual(self.fake.files[fid], f)              # nothing moved or deleted


# ─── E. A pasted link ───────────────────────────────────────────────────────

LINK = "https://drive.google.com/file/d/12CyXWPl7pMzw4FzIppouvHO5coe97d6j/view?usp=drive_link"


class PastedLink(Base):
    def test_media_tab_link_to_a_file_already_there(self):
        self.dm.catalog(7)
        self.assertIn(7, self.dm._catalog_cache)
        out = self.dp.add_by_link(7, LINK, who="client_media_tab", for_client=True)
        self.assertTrue(out["already_in_folder"])
        self.assertEqual((out["source"]["name"], out["folder"]), ("test3.mp4", ""))
        self.assertEqual(self.fake.copies, [])
        self.assertNotIn(7, self.dm._catalog_cache)                # the tab reads Drive afresh
        self.assertEqual(self.dp.load(7), [])                      # no job

    def test_a_file_in_a_subfolder_names_the_subfolder(self):
        out = self.dp.add_by_link(7, "https://drive.google.com/file/d/hebrew_image/view", for_client=True)
        self.assertEqual(out["folder"], "images")

    def test_chat_says_it_is_already_there_never_just_recognized(self):
        for lang in ("he", "en", "fr", "ar", "ru"):
            params = self.aa.validate_params(self.aa.ACTIONS["add_media_from_drive_link"], {"link": LINK}, 7)
            out = self.aa._prepare_drive_link(7, params, {"language": lang}, [])
            self.assertNotIn("params", out, lang)                  # no card to approve
            self.assertIn("test3.mp4", out["clarify"], lang)
            self.assertNotIn("{", out["clarify"], lang)
        out_he = self.aa._prepare_drive_link(7, {"link": self.T3}, {"language": "he"}, [])["clarify"]
        self.assertIn("כבר נמצא בתיקיית המדיה", out_he)
        self.assertIn("התיקייה הראשית", out_he)
        self.assertEqual(self.fake.copies, [])

    def test_another_clients_file_is_still_refused(self):
        self.fake.add("C9", "client-9 — Shop", FOLDER, "MEDIA", driveId="OURDRIVE")
        self.fake.add("v_other_file1", "secret.mp4", "video/mp4", "C9", 9, driveId="OURDRIVE")
        with self.assertRaises(self.dp.PullRefused) as ctx:
            self.dp.add_by_link(7, "https://drive.google.com/file/d/v_other_file1/view", for_client=True)
        self.assertEqual(ctx.exception.code, "own_storage")

    def test_an_owner_link_is_copied_and_shows_at_once(self):
        self.dm.catalog(7)                                          # the tab has a cached list
        self.add_owner_file("OWNERFILE_9", "from owner.mp4")
        spec = self.aa.ACTIONS["add_media_from_drive_link"]
        params = self.aa._prepare_drive_link(7, {"link": "OWNERFILE_9"}, {"language": "en"}, [])["params"]
        result = spec["run"](7, params, None, row={"origin": "client_chat"})
        self.assertEqual(self.fake.copies, [("OWNERFILE_9", "V7")])
        self.assertIn("from owner.mp4", spec["done_message"](result, "en"))
        names = {i["name"] for i in self.dm.catalog(7)["items"]}  # NOT refresh: the cache was dropped
        self.assertIn("from owner.mp4", names)

    def test_the_click_on_a_file_that_got_there_meanwhile(self):
        spec = self.aa.ACTIONS["add_media_from_drive_link"]
        result = spec["run"](7, {"link": self.T3, "name": "test3.mp4", "kind": "file"}, None, row={})
        self.assertEqual(self.fake.copies, [])
        line = spec["done_message"](result, "en")
        self.assertIn("already in your media folder", line)
        self.assertIn("main folder", line)

    def test_the_tab_and_admin_page_handle_the_answer(self):
        api = P._read("core", "api_server.py")
        body = api[api.index("def _client_link_answer"):api.index("def _client_job")]
        self.assertIn('"already_in_folder": True', body)
        page = P._read("dashboard", "client", "index.html")
        self.assertIn("d.already_in_folder", page)
        self.assertRegex(page, r"ml_link_already_in: \{ he:'[^']+', en:'[^']+', fr:'[^']+', ar:'[^']+', ru:'[^']+' \}")
        self.assertIn("r.data.already_in_folder", P._read("dashboard", "admin", "drive", "index.html"))


if __name__ == "__main__":
    unittest.main()
