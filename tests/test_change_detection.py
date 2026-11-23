"""Change detection instead of blind daily walks, the chat / first-use
triggers, use-time verification, the description spec and the thumbnail
proxy (2026-11-23, core/drive_assets, core/asset_describe,
core/drive_index_chat, docs/DRIVE-FOLDERS.md).

What must stay true:
- a folder's Drive change token is stored when it is walked and REUSED by the
  scheduled check; a check with no changes lists no folder and describes
  nothing; an expired / invalid token falls back to ONE walk that stores a
  fresh token;
- the scheduled job still runs retention and the stuck-working-copy cleanup;
- "I uploaded the photos" in a chat indexes the connection now (one short
  question when several folders could be meant); "not yet" never does;
- an agent selecting from a never-indexed connection indexes it at that moment;
- a file deleted since it was indexed is skipped at use time, marked missing,
  and the next candidate is used — the post never fails for it;
- every description carries the spec's fields and the prompt version, tags
  come from a controlled vocabulary, and only outdated ones are re-described;
- the media library's thumbnails come from our endpoint, fetched server-side
  once and cached — never a public file.

Stdlib only; Drive, the model and the database are in memory.
Run from the tests folder:  python test_change_detection.py -v
"""
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from test_drive_folders import CID, Base, _read, media  # noqa: E402  (its fakes and stubs)

from core import asset_describe, drive_assets as da, drive_folders as dfo, drive_index_chat  # noqa: E402


class Changes(Base):
    def setUp(self):
        super().setUp()
        self.drive.add("CHANGE_FOLDER_01", "מוצרים")
        media(self.drive, "CH_OLD_abcdef000", "IMG_0001.jpg", "CHANGE_FOLDER_01")
        self.root = self.connect("CHANGE_FOLDER_01", "product_catalog")
        self.run_all()

    def root_record(self):
        return dfo._load(CID)["connections"][0]

    def test_token_is_stored_at_the_walk_and_reused(self):
        token = self.root_record()["changes_token"]
        self.assertEqual(token, "0")
        media(self.drive, "CH_NEW_abcdef000", "IMG_0002.jpg", "CHANGE_FOLDER_01")
        self.drive.change("CH_NEW_abcdef000")
        self.drive.calls.clear()
        da.daily_scan(60)
        self.run_all()
        self.assertIn(("list_changes", "0"), self.drive.calls)              # the stored token, reused
        self.assertNotIn("list_children", [c[0] for c in self.drive.calls])   # no walk
        self.assertEqual(self.items()["CH_NEW_abcdef000"]["state"], "indexed")
        self.assertEqual(self.root_record()["changes_token"], "1")          # moved forward

    def test_no_change_means_no_listing_and_nothing_described(self):
        self.drive.calls.clear()
        self.vision_calls.clear()
        out = da.check_changes(CID)
        self.assertEqual((out["unchanged"], out["queued"]), ([self.root["id"]], ""))
        self.assertEqual([c[0] for c in self.drive.calls], ["list_changes"])
        self.assertEqual(self.mem.assets[CID]["job"]["state"], "idle")
        self.run_all()
        self.assertEqual(self.vision_calls, [])

    def test_removed_and_moved_out_leave_the_index(self):
        self.drive.change("CH_OLD_abcdef000", removed=True)
        da.check_changes(CID)
        self.assertNotIn("CH_OLD_abcdef000", self.items())

    def test_expired_token_walks_once_and_stores_a_fresh_one(self):
        self.drive.expired.add("0")
        media(self.drive, "CH_MISSED_abcdef0", "IMG_0003.jpg", "CHANGE_FOLDER_01")   # never in the feed
        out = da.check_changes(CID)
        self.assertEqual((out["walk"], out["queued"]), ([self.root["id"]], "walk"))
        self.drive.calls.clear()
        self.run_all()
        self.assertIn(("list_children", "CHANGE_FOLDER_01"), self.drive.calls)
        self.assertIn("start_token", [c[0] for c in self.drive.calls])
        self.assertEqual(self.root_record()["changes_token"], str(len(self.drive.feed)))
        self.assertEqual(self.items()["CH_MISSED_abcdef0"]["state"], "indexed")
        self.drive.expired.clear()
        self.drive.calls.clear()
        da.check_changes(CID)
        self.assertNotIn("list_children", [c[0] for c in self.drive.calls])   # back to change checks

    def test_a_new_subfolder_walks_its_root(self):
        self.drive.add("NEW_SUB_FOLDER01", "קיץ", parent="CHANGE_FOLDER_01")
        media(self.drive, "CH_SUB_abcdef000", "IMG_0004.jpg", "NEW_SUB_FOLDER01")
        self.drive.change("NEW_SUB_FOLDER01")
        self.assertEqual(da.check_changes(CID)["walk"], [self.root["id"]])
        self.run_all()
        self.assertEqual(self.items()["CH_SUB_abcdef000"]["state"], "indexed")

    def test_the_scheduled_job_still_runs_retention_and_the_cleanup(self):
        src = _read("core", "api_server.py")
        body = src[src.index("def drive_assets_scan"):src.index("@app.post(\"/api/admin/drive-assets/{client_id}/credits")]
        order = [body.index("media_inventory.run_retention()"), body.index("asset_refs.sweep_working_copies()"),
                 body.index("drive_assets.daily_scan(")]
        self.assertEqual(order, sorted(order))
        self.assertIn("check_changes", _read("core", "drive_assets.py")[
            _read("core", "drive_assets.py").index("def daily_scan"):])


class ChatTrigger(Base):
    def setUp(self):
        super().setUp()
        self.drive.add("CHAT_PRODUCTS_01", "מוצרים")
        self.drive.add("CHAT_AVATAR_0001", "אווטאר")
        self.products = self.connect("CHAT_PRODUCTS_01", "product_catalog")
        self.avatar = self.connect("CHAT_AVATAR_0001", "raw_materials")
        self.run_all()

    def test_detection(self):
        for text in ("העליתי את התמונות לתיקייה", "סיימתי להעלות את הקבצים", "I uploaded the photos to the folder",
                     "j'ai ajouté les photos", "загрузила фото в папку"):
            with self.subTest(text=text):
                self.assertTrue(drive_index_chat.says_uploaded(text))
        for text in ("עוד לא העליתי את התמונות", "איך מעלים תמונות?", "I haven't uploaded the photos yet",
                     "תודה רבה", "upload"):
            with self.subTest(text=text):
                self.assertFalse(drive_index_chat.says_uploaded(text))

    def test_ambiguous_asks_one_short_question_then_indexes_the_named_folder(self):
        media(self.drive, "CHAT_NEW_abcdef0", "IMG_0010.jpg", "CHAT_AVATAR_0001")
        out = drive_index_chat.turn(CID, "העליתי את התמונות", "he")
        self.assertEqual(out["ask"], "לאיזו תיקייה העליתם — מוצרים או אווטאר?")
        self.assertEqual(drive_index_chat.turn(CID, "בסדר", "he"), {})         # not an answer: nothing runs
        out = drive_index_chat.turn(CID, "לאווטאר", "he")
        self.assertIn("אווטאר", out["line"])
        job = self.mem.assets[CID]["job"]
        self.assertEqual((job["mode"], job["roots"]), ("chat", [self.avatar["id"]]))
        self.run_all()
        self.assertEqual(self.items()["CHAT_NEW_abcdef0"]["state"], "indexed")
        self.assertEqual(drive_index_chat.turn(CID, "לאווטאר", "he"), {})      # the question is answered

    def test_a_named_folder_indexes_at_once(self):
        out = drive_index_chat.turn(CID, "I uploaded the photos to מוצרים", "en")
        self.assertEqual(out["roots"], [self.products["id"]])
        self.assertIn("scanning", out["line"])

    def test_one_folder_needs_no_question_and_the_chat_calls_it(self):
        dfo._save(CID, {"connections": [c for c in dfo._load(CID)["connections"] if c["id"] == self.products["id"]]})
        tasks = []
        bg = types.SimpleNamespace(add_task=lambda fn, *a: tasks.append((fn, a)))
        out = drive_index_chat.turn(CID, "העליתי תמונות", "he", bg)
        self.assertEqual(out["roots"], [self.products["id"]])
        self.assertEqual(tasks[0][0], da.step)                               # indexing starts now
        src = _read("agents", "support_agent.py")
        self.assertEqual(src.count("_uploaded_files_turn(client_id, message, reply, background_tasks)"), 2)


class FirstUse(Base):
    def test_an_agent_selecting_from_a_never_indexed_connection_indexes_it(self):
        self.drive.add("NEVER_INDEXED_01", "מוצרים")
        media(self.drive, "FIRST_USE_abcdef", "red leather sneaker.jpg", "NEVER_INDEXED_01")
        dfo.confirm(CID, "NEVER_INDEXED_01", "product_catalog")             # connected, never run
        found = da.find_assets(CID, ["product_catalog"], "sneaker")
        self.assertEqual([f["file_id"] for f in found], ["FIRST_USE_abcdef"])
        self.assertEqual(self.items()["FIRST_USE_abcdef"]["state"], "indexed")


class UseTime(Base):
    def test_a_deleted_file_is_skipped_marked_missing_and_the_post_goes_on(self):
        from agents import media_agent
        from core import post_brief as pb
        self.drive.add("USE_FOLDER_00001", "מוצרים")
        media(self.drive, "GONE_SHOE_abcdef", "red leather sneaker one.jpg", "USE_FOLDER_00001")
        media(self.drive, "LIVE_SHOE_abcdef", "red leather sneaker two.jpg", "USE_FOLDER_00001")
        self.connect("USE_FOLDER_00001", "product_catalog")
        self.run_all()
        self.drive.files.pop("GONE_SHOE_abcdef")                             # deleted since it was indexed
        picks = iter(["GONE_SHOE_abcdef", "LIVE_SHOE_abcdef"])
        saved = (media_agent.safe_claude_json_call, media_agent._log_activity, media_agent._generation_key)
        media_agent.safe_claude_json_call = lambda system, user, **k: {"file_id": next(picks), "reason": "x"}
        media_agent._log_activity = lambda *a, **k: None
        media_agent._generation_key = lambda cid: self.fail("fell back to generating")
        try:
            brief = pb.AssetBrief(asset_id="p1", post_format="product_carousel", objective="consideration",
                                  client_id=CID, role="point", copy_headline="", copy_body="",
                                  asset_type="real_photo", visual_direction="a red leather sneaker", set_id="s1")
            out = media_agent.generate_from_brief(CID, brief, {})
        finally:
            media_agent.safe_claude_json_call, media_agent._log_activity, media_agent._generation_key = saved
        self.assertEqual((out["success"], out["file_id"]), (True, "LIVE_SHOE_abcdef"))
        self.assertEqual(self.items()["GONE_SHOE_abcdef"]["state"], "missing")
        self.assertNotIn("GONE_SHOE_abcdef", [f["file_id"] for f in da.find_assets(CID, query="sneaker")])

    def test_moved_out_is_missing_too(self):
        self.drive.add("USE_FOLDER_00002", "מוצרים")
        self.drive.add("ELSEWHERE_FOLDER", "other")
        media(self.drive, "MOVED_abcdef0000", "IMG_1.jpg", "USE_FOLDER_00002")
        self.connect("USE_FOLDER_00002", "product_catalog")
        self.run_all()
        self.assertEqual(da.verify_usable(CID, "MOVED_abcdef0000"), "ok")
        self.drive.files["MOVED_abcdef0000"]["parents"] = ["ELSEWHERE_FOLDER"]
        self.assertEqual(da.verify_usable(CID, "MOVED_abcdef0000"), "moved")


FULL = {"subject": "custom hand-painted sneaker", "description": "A white leather sneaker hand-painted with red "
        "flames, side profile on a plain black background. Studio light, sharp, empty space above.",
        "colors": {"dominant": ["white", "black"], "accent": ["red"]}, "framing": "full item", "angle": "side profile",
        "background": "plain", "person": {"present": False, "visible": "none"}, "lighting": "studio softbox",
        "mood": "bold", "visible_text": "", "branding": "SHIN logo on the heel", "sharp": True, "well_exposed": True,
        "text_space": "top", "tags": ["sneakers", "close up", "Hand Painted", "studio", "flame-motif"]}


class Descriptions(Base):
    def test_every_field_the_version_and_controlled_tags(self):
        self.vision_rows = lambda n: dict(FULL)
        self.drive.add("DESC_FOLDER_0001", "מוצרים")
        media(self.drive, "DESC_abcdef00000", "IMG_0001.jpg", "DESC_FOLDER_0001")
        self.connect("DESC_FOLDER_0001", "product_catalog")
        self.run_all()
        item = self.items()["DESC_abcdef00000"]
        self.assertEqual(item["prompt_version"], asset_describe.PROMPT_VERSION)
        a = item["attributes"]
        for key in ("subject", "colors", "framing", "angle", "background", "person", "lighting", "mood",
                    "visible_text", "branding", "sharp", "well_exposed", "text_space"):
            self.assertIn(key, a)
        self.assertEqual((a["framing"], a["background"], a["text_space"], a["colors"]["accent"]),
                         ("full_item", "plain", "top", ["red"]))
        self.assertEqual(item["tags"], ["sneaker", "close-up", "hand-painted", "studio", "flame-motif"])
        category = item["category"]
        self.assertIn("flame-motif", self.mem.assets[CID]["vocab"][category])     # the vocabulary grew
        self.assertIn("flame-motif", asset_describe.vocabulary(category, self.mem.assets[CID]["vocab"][category]))
        self.assertIn("text space", asset_describe.search_text(a))
        self.assertTrue(da.find_assets(CID, query="flames"))                     # searchable by the description

    def test_the_prompt_asks_for_every_field_and_is_category_aware(self):
        prompt = asset_describe.system_prompt("fashion_footwear", asset_describe.vocabulary("fashion_footwear"))
        for word in ("subject", "colors", "framing", "background", "person", "lighting", "mood", "visible_text",
                     "branding", "sharp", "well_exposed", "text_space", "VOCABULARY", "sneaker"):
            self.assertIn(word, prompt)
        self.assertEqual(asset_describe.category_from_text(_read("knowledge", "brands", "27.md")), "fashion_footwear")
        self.assertEqual(asset_describe.category_from_text("- category: plumbing"), "default")
        self.assertEqual(asset_describe.category_from_text(""), "default")
        food = asset_describe.system_prompt("food", asset_describe.vocabulary("food"))
        self.assertIn("plating", food)                                       # its own emphasis
        self.assertNotIn("colourway", food)                                  # not footwear's
        self.assertIn("plating", asset_describe.vocabulary("food"))
        self.assertIn("distinguishing details", asset_describe.system_prompt("default", []))

    def test_near_duplicate_tags_map_to_the_existing_one(self):
        vocab = asset_describe.vocabulary("fashion_footwear")
        cases = {"Close Up": "close-up", "closeup": "close-up", "sneakers": "sneaker", "flatlay": "flat-lay",
                 "white_background": "white-background", "leathr": "leather", "colourful": "multicolor"}
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(asset_describe.normalize_tag(raw, vocab), want)
        self.assertEqual(asset_describe.normalize_tag("glitter-toe", vocab), "glitter-toe")   # new, allowed

    def test_only_outdated_descriptions_are_redescribed(self):
        self.drive.add("OLD_DESC_FOLDER1", "מוצרים")
        for n in range(3):
            media(self.drive, f"OLD_{n}_abcdef000", f"IMG_{n:04d}.jpg", "OLD_DESC_FOLDER1")
        self.connect("OLD_DESC_FOLDER1", "product_catalog")
        self.run_all()
        state = self.mem.assets[CID]
        state["items"]["OLD_0_abcdef000"].pop("prompt_version")              # described by v1
        state["items"]["OLD_1_abcdef000"]["prompt_version"] = "asset-desc-v1"
        self.assertEqual(sorted(da.outdated(CID)), ["OLD_0_abcdef000", "OLD_1_abcdef000"])
        self.vision_calls.clear()
        self.assertEqual(da.queue_redescribe(CID)["queued"], 2)
        self.run_all()
        self.assertEqual(sum(n for n, _names, _m in self.vision_calls), 2)   # only those two
        self.assertEqual(da.outdated(CID), [])
        self.assertIn('"/api/admin/drive-assets/{client_id}/redescribe"', _read("core", "api_server.py"))


class Thumbnails(Base):
    def test_served_by_our_endpoint_fetched_once_never_public(self):
        from core import drive_service, media_inventory
        self.drive.add("THUMB_FOLDER_001", "מוצרים")
        media(self.drive, "THUMB_abcdef0000", "IMG_1.jpg", "THUMB_FOLDER_001")
        self.connect("THUMB_FOLDER_001", "product_catalog")
        self.run_all()
        fetched = []
        saved = drive_service.fetch_thumbnail
        drive_service.fetch_thumbnail = lambda link, max_bytes: fetched.append(link) or (b"\xff\xd8jpeg", "image/jpeg")
        media_inventory._thumbs.clear()
        try:
            first = media_inventory.thumbnail(CID, "THUMB_abcdef0000")
            second = media_inventory.thumbnail(CID, "THUMB_abcdef0000")
        finally:
            drive_service.fetch_thumbnail = saved
        self.assertEqual(first, (b"\xff\xd8jpeg", "image/jpeg"))
        self.assertEqual(second, first)
        self.assertEqual(len(fetched), 1)                                     # fetched from Drive once
        self.assertTrue(fetched[0].endswith("=s320"))
        html = _read("dashboard", "client", "index.html")
        self.assertIn('src="/api/client/media-inventory/thumb/${encodeURIComponent(i.id)}"', html)
        src = _read("core", "media_inventory.py")
        body = src[src.index("def thumbnail"):src.index("def thumbnail") + 2500]
        self.assertNotIn("make_file_public", body)


if __name__ == "__main__":
    unittest.main()
