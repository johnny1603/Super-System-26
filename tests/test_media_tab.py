"""The client Media tab + the shared file-name matcher (2026-11-11,
docs/MEDIA-TAB.md).

A. core/media_search: one matcher for the tab's search box AND the agents'
   find_media_file — every script, case, separators, extension, best_only,
   kind / folder / sort.
B. core/drive_mirror.catalog + core/media_library.view on an in-memory Drive:
   whole tree, "new" since the last visit, "sent", duplicates, playable.
C. core/drive_download player: Range parsing, the 4 MiB piece, the play cap.
D. core/agent_actions send_tiktok_batch: 2-10 videos, one card, one durable
   job per video, connect card when there is no grant.
E. Wiring + the dashboard: routes, admin read-only list, chat parity, the
   media_seen prefix everywhere a client's settings are cleared, dir="auto",
   no DOM move, i18n (English fallback, nothing bypassing the table).

Stdlib only; nothing here talks to Google. Run from the repo root:
    python tests/test_media_tab.py -v
"""
import ast
import os
import re
import sys
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import test_drive_pull as tdp            # noqa: E402  (the in-memory Drive + DB)
from core import media_search as ms      # noqa: E402


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


def _in(self, col, values):              # the fake DB learns .in_()
    self.filters.append(lambda r, c=col, v=tuple(values): r.get(c) in v)
    return self


tdp._Q.in_ = _in


def item(fid, name, kind="video", folder="videos", at="2026-11-01T10:00:00Z", **extra):
    return {"id": fid, "name": name, "kind": kind, "folder": folder, "modified_time": at, "created_time": at, **extra}


# ─── A. The matcher ─────────────────────────────────────────────────────────

class Matcher(unittest.TestCase):
    def test_case_and_separators_do_not_count(self):
        for name in ("test_3.mp4", "Test-3.mov", "TEST3.mp4", "test 3.MP4", "test.3.webm"):
            self.assertEqual(ms.score(name, "test 3"), 300, name)
        self.assertEqual(ms.compact("Test_3 .MP4"), "test3mp4")

    def test_the_exact_name_beats_a_near_miss(self):
        items = [item("a", "test 30.mp4"), item("b", "test_3.mp4"), item("c", "my test 3 final.mp4")]
        self.assertEqual([i["id"] for i in ms.search(items, "test 3", best_only=True)], ["b"])
        self.assertEqual([i["id"] for i in ms.search(items, "test 3")][0], "b")

    def test_every_script(self):
        cases = [("ทดสอบวิดีโอ.mp4", "ทดสอบ"),          # Thai: no spaces between words
                 ("מוצר חדש.mp4", "המוצר"),              # a Hebrew prefix letter
                 ("Café promo.jpg", "cafe"),               # accents
                 ("מבצע_קיץ.mp4", "מבצע קיץ"),
                 ("عرض الصيف.mp4", "الصيف"),
                 ("Летняя акция.mp4", "ЛЕТНЯЯ"),
                 ("夏のセール.mp4", "セール")]
        for name, query in cases:
            self.assertGreater(ms.score(name, query), 0, (name, query))

    def test_thai_vowel_marks_are_part_of_the_word(self):
        self.assertNotEqual(ms.compact("ที่"), ms.compact("ท"))

    def test_request_words_are_dropped_unless_nothing_is_left(self):
        self.assertEqual(ms.query_words("the video called test 3"), ["test", "3"])
        self.assertEqual(ms.query_words("סרטון"), ["סרטון"])

    def test_a_lone_digit_is_not_a_match_by_itself(self):
        self.assertEqual(ms.score("holiday 2026.mp4", "wedding 3"), 0)

    def test_kind_folder_and_sort(self):
        items = [item("v1", "b.mp4", folder="videos", at="2026-11-02T00:00:00Z"),
                 item("v2", "a.mp4", folder="videos/tiktok", at="2026-11-03T00:00:00Z"),
                 item("i1", "c.jpg", kind="image", folder="images", at="2026-11-01T00:00:00Z"),
                 item("r1", "d.pdf", kind="document", folder="", at="2026-10-01T00:00:00Z"),
                 item("s1", "link", kind="shortcut", folder="videos", target_kind="video")]
        ids = lambda out: [i["id"] for i in out]
        self.assertEqual(sorted(ids(ms.search(items, kind="video"))), ["s1", "v1", "v2"])
        self.assertEqual(sorted(ids(ms.search(items, folder="videos"))), ["s1", "v1", "v2"])
        self.assertEqual(ids(ms.search(items, folder="videos/tiktok")), ["v2"])
        self.assertEqual(ids(ms.search(items, folder="")), ["r1"])
        self.assertEqual(ids(ms.search(items, kind="video", sort="name"))[:2], ["v2", "v1"])
        self.assertEqual(ids(ms.search(items, kind="image", sort="oldest")), ["i1"])
        self.assertEqual(ids(ms.search(items, sort="newest"))[:1], ["v2"])

    def test_one_matcher_for_the_tab_and_the_agents(self):
        mirror = _read("core", "drive_mirror.py")
        self.assertIn("media_search.search(", mirror)
        self.assertNotIn("name_words=", mirror)   # Drive's word-prefix match is gone
        self.assertIn("media_search.search(", _read("core", "media_library.py"))


# ─── B. Catalog + library view ──────────────────────────────────────────────

class Library(tdp.Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.uploads = tdp._module("core.tiktok_uploads", load=lambda cid: cls.jobs)
        cls.stubs.put("core.tiktok_uploads", cls.uploads)
        sys.modules.pop("core.media_library", None)
        import core
        if hasattr(core, "media_library"):
            delattr(core, "media_library")   # bound to the drive_mirror Base just reloaded
        from core import drive_download, media_library
        cls.ml, cls.dl = media_library, drive_download

    def setUp(self):
        super().setUp()
        type(self).jobs = []
        self.ml._db = lambda: self.db
        self.ml.forget(7)
        self.dl._play_settings.update({"at": time.time() + 3600, "max_mb": 100})
        self.dl._settings.update({"at": time.time() + 3600, "max_mb": 20})
        d = self.fake
        d.add("vid_test3_a", "test 3.mp4", "video/mp4", "V7", 40 * 1024 * 1024, "2026-11-11T08:00:00Z")
        d.add("vid_test3_b", "Test-3.mp4", "video/mp4", "T7", 300 * 1024 * 1024, "2026-11-09T08:00:00Z")
        d.add("img_logo_01", "logo.png", "image/png", "I7", 1000, "2026-11-08T08:00:00Z")
        d.add("doc_price_01", "price list.pdf", "application/pdf", "D7", 1000, "2026-11-07T08:00:00Z")

    def view(self, **kw):
        return self.ml.view(7, **kw)

    def test_the_whole_tree_in_one_list(self):
        out = self.view()
        names = {i["name"] for i in out["items"]}
        for want in ("launch final.mp4", "intro.mp4", "מוצר חדש.mp4", "test 3.mp4", "logo.png", "price list.pdf"):
            self.assertIn(want, names)
        self.assertIn("videos/tiktok", out["folders"])
        self.assertEqual(out["counts"]["image"], 1)
        self.assertEqual(out["counts"]["document"], 1)

    def test_search_kind_folder_and_paging(self):
        out = self.view(q="test_3")
        self.assertEqual({i["id"] for i in out["items"]}, {"vid_test3_a", "vid_test3_b"})
        self.assertEqual([i["id"] for i in self.view(kind="image")["items"]], ["img_logo_01"])
        self.assertTrue(all(i["folder"].startswith("videos/tiktok") for i in self.view(folder="videos/tiktok")["items"]))
        page = self.view(limit=2, offset=1)
        self.assertEqual(len(page["items"]), 2)
        self.assertGreater(page["total"], 2)
        self.assertNotIn("error", self.view(folder="gone/away"))     # an unknown folder = everywhere

    def test_duplicates_say_so_and_carry_folder_and_date(self):
        by = {i["id"]: i for i in self.view(q="test 3")["items"]}
        self.assertTrue(by["vid_test3_a"]["duplicate"] and by["vid_test3_b"]["duplicate"])
        self.assertEqual(by["vid_test3_b"]["folder"], "videos/tiktok")
        self.assertTrue(by["vid_test3_b"]["created_time"])
        self.assertFalse({i["id"]: i for i in self.view()["items"]}["img_logo_01"]["duplicate"])

    def test_playable_only_up_to_the_admin_setting(self):
        by = {i["id"]: i for i in self.view()["items"]}
        self.assertTrue(by["vid_test3_a"]["playable"])          # 40 MB
        self.assertFalse(by["vid_test3_b"]["playable"])        # 300 MB: open in Drive
        self.assertFalse(by["img_logo_01"]["playable"])
        self.assertEqual(self.view()["play_max_mb"], 100)

    def test_new_since_the_previous_visit(self):
        self.assertEqual(self.ml.mark_seen(7), "")                       # first visit: nothing is new
        self.db.tables["app_settings"][0]["value"]["at"] = "2026-11-10T12:00:00+00:00"
        previous = self.ml.mark_seen(7)
        self.assertEqual(previous, "2026-11-10T12:00:00+00:00")
        out = self.view(since=previous)
        new = {i["id"] for i in out["items"] if i["new"]}
        self.assertEqual(new, {"vid_test3_a"})
        self.assertEqual(out["new_count"], 1)
        self.assertFalse(any(i["new"] for i in self.view()["items"]))   # no stamp, no badge

    def test_sent_comes_from_what_really_happened(self):
        self.db.tables["agent_actions"] = [
            {"client_id": 7, "action": "publish_youtube_from_media", "status": "done", "params": {"drive_file_id": "vid_test3_a"}},
            {"client_id": 7, "action": "send_tiktok_batch", "status": "done", "params": {"drive_file_ids": ["vid_test3_a", "v_root"]}},
            {"client_id": 7, "action": "send_tiktok_to_inbox", "status": "proposed", "params": {"drive_file_id": "img_logo_01"}}]
        type(self).jobs = [{"file_id": "v_deep", "status": "done"}, {"file_id": "v_prod", "status": "running"}]
        by = {i["id"]: i for i in self.view()["items"]}
        self.assertEqual(sorted(by["vid_test3_a"]["sent"]), ["tiktok", "youtube"])
        self.assertEqual(by["v_root"]["sent"], ["tiktok"])
        self.assertEqual(by["v_deep"]["sent"], ["tiktok"])
        self.assertEqual(by["v_prod"]["sent"], [])
        self.assertEqual(by["img_logo_01"]["sent"], [])

    def test_drive_trouble_is_one_calm_code(self):
        def boom(*a, **k):
            raise tdp.DriveError("unavailable")
        self.drv.list_in_folders = boom
        self.dm.forget(7)
        self.assertEqual(self.view(), {"exists": True, "error": "unavailable"})

    def test_find_reads_the_same_catalog(self):
        out = self.dm.find(7, "test 3", kind="video")
        self.assertEqual({m["id"] for m in out["matches"]}, {"vid_test3_a", "vid_test3_b"})
        self.assertEqual(out["shortcuts"], 0)
        self.assertEqual(self.dm.find(7, "launch", kind="video")["shortcuts"], 1)


# ─── C. Player ──────────────────────────────────────────────────────────────

class Player(Library):
    def test_range_pieces_stay_small(self):
        mib = 1024 * 1024
        self.assertEqual(self.dl.parse_range("", 100 * mib), (0, 4 * mib - 1))
        self.assertEqual(self.dl.parse_range("bytes=0-", 100 * mib), (0, 4 * mib - 1))
        self.assertEqual(self.dl.parse_range("bytes=10-19", 100), (10, 19))
        self.assertEqual(self.dl.parse_range("bytes=-10", 100), (90, 99))
        self.assertEqual(self.dl.parse_range("bytes=95-500", 100), (95, 99))
        for bad in ("bytes=200-", "bytes=-", "items=0-1", "bytes=5-2"):
            with self.assertRaises(ValueError, msg=bad):
                self.dl.parse_range(bad, 100)

    def test_plan_play_caps_and_owns(self):
        p = self.dl.plan_play(7, "vid_test3_a")
        self.assertEqual(p["send_mime"], "video/mp4")
        h = self.dl.play_headers(p, 0, 9)
        self.assertEqual(h["Content-Length"], "10")
        self.assertTrue(h["Content-Range"].startswith("bytes 0-9/"))
        with self.assertRaises(self.dl.OpenInDrive):
            self.dl.plan_play(7, "vid_test3_b")                 # over the play cap
        with self.assertRaises(self.dl.OpenInDrive):
            self.dl.plan_play(7, "img_logo_01")                   # not a video
        with self.assertRaises(self.dl.DownloadRefused):
            self.dl.plan_play(7, "OWNERFILE_A")           # not this client's folder

    def test_downloads_of_videos_still_never_go_through_us(self):
        with self.assertRaises(self.dl.OpenInDrive):
            self.dl.plan(7, "vid_test3_a")


# ─── D. Several videos → one TikTok card ────────────────────────────────────

class TikTokBatch(tdp.Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.created, cls.owner = [], []
        cls.grant = "ok"
        cls.stubs.put("agents.tiktok_content_agent",
                      tdp._module("agents.tiktok_content_agent", check_grant=lambda cid: cls.grant))
        cls.stubs.put("core.video_probe", tdp._module(
            "core.video_probe", drive_meta_problems=lambda meta: ["container"] if meta.get("bad") else []))
        cls.stubs.put("core.video_limits", tdp._module(
            "core.video_limits", tiktok_needs_owner_approval=lambda size: size > 1000))
        cls.stubs.put("core.tiktok_uploads", tdp._module(
            "core.tiktok_uploads", ON_IT={"he": "אני מטפל בזה", "en": "I'm on it"},
            create=lambda *a, **k: cls.created.append((a, k)) or {"id": "j"},
            request_owner_approval=lambda *a, **k: cls.owner.append(a)))
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
        type(self).grant = "ok"
        self.created.clear()
        self.owner.clear()
        aa = self.aa
        self.meta = {"a1": {"size": 10}, "a2": {"size": 20}, "big": {"size": 5000}, "bad": {"size": 5, "bad": True}}
        aa._media_meta.clear()
        for fid, meta in self.meta.items():
            aa._media_meta[(7, fid)] = (time.time(), meta)
        self.saved = (aa._v_tiktok_video, aa._media_name)
        aa._v_tiktok_video = lambda v, cid: v
        aa._media_name = lambda cid, fid: f"{fid}.mp4"

    def tearDown(self):
        self.aa._v_tiktok_video, self.aa._media_name = self.saved
        super().tearDown()

    def test_two_to_ten_videos(self):
        v = self.aa._v_tiktok_videos
        self.assertEqual(v(["a1", "a2", "a1"], 7), ["a1", "a2"])
        for bad in (["a1"], [f"x{i}" for i in range(11)], []):
            with self.assertRaises(ValueError):
                v(bad, 7)

    def test_one_card_for_the_batch(self):
        out = self.aa._prepare_tiktok_batch(7, {"drive_file_ids": ["a1", "a2"]}, {"language": "en"}, [])
        self.assertEqual(out["params"], {"drive_file_ids": ["a1", "a2"]})
        spec = self.aa.ACTIONS["send_tiktok_batch"]
        self.assertTrue(spec["code_only"])
        self.assertEqual(spec["approver"], "client")

    def test_big_goes_to_the_owner_bad_is_named_and_one_left_is_the_single_card(self):
        out = self.aa._prepare_tiktok_batch(7, {"drive_file_ids": ["a1", "big", "bad"]}, {"language": "en"}, [])
        self.assertEqual(out["switch_to"], "send_tiktok_to_inbox")
        self.assertEqual(out["switch_params"], {"drive_file_id": "a1"})
        self.assertIn("bad.mp4", out["notice"])
        self.assertIn("I'm on it", out["notice"])
        self.assertEqual(len(self.owner), 1)

    def test_no_grant_is_the_connect_card(self):
        type(self).grant = "not_connected"
        out = self.aa._prepare_tiktok_batch(7, {"drive_file_ids": ["a1", "a2"]}, {"language": "en"}, [])
        self.assertEqual(out["switch_to"], "connect_tiktok")

    def test_the_same_open_batch_is_reused(self):
        row = {"id": 3, "params": {"drive_file_ids": ["a2", "a1"]}}
        self.assertEqual(self.aa._prepare_tiktok_batch(7, {"drive_file_ids": ["a1", "a2"]}, {}, [row]),
                         {"existing": row})

    def test_the_click_makes_one_durable_job_per_video(self):
        out = self.aa._run_send_tiktok_batch(7, {"drive_file_ids": ["a1", "a2"]}, None,
                                             row={"persona": "media", "language": "en"})
        self.assertEqual(out["count"], 2)
        self.assertEqual([c[0][1] for c in self.created], ["a1", "a2"])
        self.assertTrue(all("proposal_id" not in c[1] for c in self.created))

    def test_stored_params_are_checked_again(self):
        with self.assertRaises(ValueError):
            self.aa._validate_tiktok_batch_stored(7, {"drive_file_ids": ["a1", "a2"], "x": 1})
        self.assertEqual(self.aa._validate_tiktok_batch_stored(7, {"drive_file_ids": ["a1", "a2"]}),
                         {"drive_file_ids": ["a1", "a2"]})


# ─── E. Wiring + dashboard ──────────────────────────────────────────────────

class Wiring(unittest.TestCase):
    def setUp(self):
        self.api = _read("core", "api_server.py")
        self.page = _read("dashboard", "client", "index.html")

    def _route_fn(self, path):
        for node in ast.parse(self.api).body:
            if isinstance(node, ast.FunctionDef) and any(path.replace('"', "'") in ast.unparse(d) for d in node.decorator_list):
                return node.name, ast.unparse(node)
        self.fail(path)

    def test_routes_are_client_session_and_send_is_not_read_only(self):
        for path in ('"/api/client/media-library"', '"/api/client/media-library/seen"',
                     '"/api/client/media-drive/play/{file_id}"', '"/api/client/media/send"'):
            _name, body = self._route_fn(path)
            self.assertIn("_require_session(request)", body, path)
        _n, send = self._route_fn('"/api/client/media/send"')
        self.assertIn("ERR_READ_ONLY", send)

    def test_admin_view_reads_but_never_stamps_or_sends(self):
        from core import admin_view
        self.assertIn("client_media_library", admin_view.READ_ENDPOINTS)
        self.assertIn("client_media_play", admin_view.READ_ENDPOINTS)
        self.assertNotIn("client_media_library_seen", admin_view.READ_ENDPOINTS)
        self.assertNotIn("client_media_send", admin_view.READ_ENDPOINTS)

    def test_chat_parity_knows_the_routes(self):
        src = _read("core", "chat_parity.py")
        for route in ("/api/client/media-library", "/api/client/media-library/seen",
                      "/api/client/media-drive/play/", "/api/client/media/send"):
            self.assertIn(route, src)

    def test_media_seen_is_cleared_with_the_client(self):
        self.assertIn("media_seen:", _read("core", "client_deletion.py"))
        self.assertIn('"media_seen:"', _read("core", "deletion_guard.py"))
        self.assertIn('"media_seen:"', _read("core", "admin_service.py"))
        self.assertIn('"media_video_play_max_mb": 100', _read("core", "admin_service.py"))
        self.assertIn("media_video_play_max_mb", _read("dashboard", "admin", "index.html"))

    def test_names_are_bidi_safe_and_documents_get_an_icon(self):
        self.assertIn('<div class="media-item-title"><bdi dir="auto">${esc(label)}</bdi></div>', self.page)
        self.assertIn('<div class="media-item-title"><bdi dir="auto">${esc(asset.title)}</bdi></div>', self.page)
        self.assertIn("doc-thumb", self.page)
        self.assertIn("(shown === 'image' || shown === 'video') && f.thumbnail_link", self.page)

    def test_no_nothing_yet_under_a_full_list(self):
        fn = self.page[self.page.index("function renderMediaGrid()"):]
        fn = fn[:fn.index("\n}\n")]
        self.assertIn("section.hidden", fn)
        self.assertIn("listHasFiles", fn)

    def test_tab_open_syncs_silently_and_keeps_the_button(self):
        self.assertIn("if (name === 'media') openMediaLibrary();", self.page)
        self.assertIn("loadLibrary({ refresh: true, silent: true })", self.page)
        self.assertIn('id="driveMirrorRefresh" onclick="loadLibrary({ refresh: true })"', self.page)
        self.assertIn('data-i18n="ml_where_hint"', self.page)

    def test_player_and_multiselect_use_the_existing_paths(self):
        self.assertIn("'/api/client/media-drive/play/' + encodeURIComponent(f.id)", self.page)
        self.assertIn("fetch('/api/client/media/send'", self.page)
        self.assertIn("appendActionCard(persona, d.card)", self.page)
        self.assertIn("const ML_TIKTOK_MAX = 10;", self.page)
        from core import agent_actions
        self.assertEqual(agent_actions.TIKTOK_BATCH_MAX, 10)

    def test_no_dom_move_in_the_media_code(self):
        block = self.page[self.page.index("// ─── Media library: the client's WHOLE Drive tree"):
                          self.page.index("function renderMediaGrid()")]
        for bad in ("insertBefore(", ".prepend(", "appendChild(document.getElementById"):
            self.assertNotIn(bad, block)

    def test_mobile_width(self):
        css = self.page[self.page.index("/* Media library: search"):]
        css = css[:css.index("</style>")] if "</style>" in css[:6000] else css[:6000]
        self.assertIn("@media (max-width: 640px)", css)
        self.assertIn("min-height: 44px", css)
        self.assertIn("flex-wrap: wrap", css)


class Translations(unittest.TestCase):
    def test_english_is_the_fallback_before_hebrew(self):
        src = _read("dashboard", "assets", "i18n.js")
        self.assertIn("(lang !== 'he' && entry.en != null ? entry.en : entry.he)", src)
        self.assertIn("data-i18n-aria-label", src)

    def test_client_pages_have_no_hebrew_outside_the_tables(self):
        import i18n_report
        data = i18n_report.report()
        self.assertEqual(data["languages"], ["he", "en", "fr", "ar", "ru"])
        for page, info in data["pages"].items():
            self.assertEqual(info["bypassing"], [], page)
            self.assertEqual(info["wrong_script"], {}, page)
            self.assertEqual(info["missing"], {}, page)

    def test_internal_package_badge_is_translated(self):
        page = _read("dashboard", "client", "index.html")
        self.assertIn("pkg_internal:", page)
        self.assertIn("const pkg = pkgLabel(", page)

    def test_no_language_setting_is_changed(self):
        # The report and the fallback are read-side only: nothing writes a client's language
        for f in ("scripts/i18n_report.py", "core/media_library.py"):
            src = _read(*f.split("/"))
            self.assertNotIn("preferred_language", src)
            self.assertNotIn(".update(", src)


if __name__ == "__main__":
    unittest.main()
