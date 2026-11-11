"""Add by link, attach an existing file from anywhere, merge a client's two
folders, ONE folder resolver, the media diagnosis (2026-11-11,
docs/DRIVE-PULL.md §Add by link / §Attach / §Merge).

A. Links: every Drive / Docs link shape → the id; a link inside chat text.
B. add_by_link: copied INSIDE Drive by type, a repeated link never makes a
   duplicate, the same active source is the same job, a link we can't open is
   ONE calm line with the address, a client can't pull from our own storage.
C. The client card add_media_from_drive_link and the chat hook.
D. find_anywhere + attach_existing_media (admin only): candidates with folder
   path, date, size; the copy never moves or deletes the original.
E. Merge: both folders listed with the connected one marked and file counts;
   everything copied with the same sub-folders, same name+size skipped,
   nothing deleted, a re-run copies nothing.
F. One resolver: a Drive error never reads as "no folder yet"; only the media
   agent creates a client's media folder; the media diagnosis.

Stdlib only; Drive is the in-memory tree of test_drive_pull.
Run from the repo root:  python tests/test_drive_link.py -v
"""
import ast
import copy
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_drive_pull as P                        # noqa: E402  (stubs, FakeDrive, Base)

FOLDER, SA = P.FOLDER, P.SA


class Drive(P.FakeDrive):
    """FakeDrive + the calls this block adds."""

    def search_everywhere(self, words, page_size=50):
        def hit(name, word):
            return any(t.startswith(word.lower()) for t in re.findall(r"\w+", name.lower()))
        out = [copy.deepcopy(f) for fid, f in self.files.items()
               if fid not in self.unshared and not f["trashed"] and f["mimeType"] != FOLDER
               and all(hit(f["name"], w) for w in words or [])]
        return sorted(out, key=lambda f: f["modifiedTime"], reverse=True)[:page_size]

    def find_in_folder_by_name(self, parent, name):
        return [copy.deepcopy(f) for f in self.files.values()
                if parent in f["parents"] and f["name"] == name and not f["trashed"]]


EXTRA = ("search_everywhere", "find_in_folder_by_name")


class Base(P.Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._real_extra = {n: getattr(cls.drv, n) for n in EXTRA}
        for name in ("core.drive_merge",):
            sys.modules.pop(name, None)
        from core import drive_merge
        cls.dmg = drive_merge

    @classmethod
    def tearDownClass(cls):
        for n, fn in cls._real_extra.items():
            setattr(cls.drv, n, fn)
        sys.modules.pop("core.drive_merge", None)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        old = self.fake
        self.fake = d = Drive()
        d.files, d.unshared = old.files, old.unshared
        for n in P.DRIVE_FUNCS + EXTRA:
            setattr(self.drv, n, getattr(d, n))
        # our Shared Drive has an id; the owner's My Drive has none
        for fid, f in d.files.items():
            f["driveId"] = "" if (f["parents"] and f["parents"][0] in ("OWNERDRIVE", "SRCFOLDER01", "SUBFOLDER01")) \
                else "OURDRIVE"
        d.files["MEDIA"]["driveId"] = "OURDRIVE"
        self.dp._media_drive_id.update(at=0.0, id="")
        # A real nested _subfolder on the fake tree (find-or-create by name)
        made = {(): "C7", ("videos",): "V7", ("images",): "I7", ("documents",): "D7", ("videos", "tiktok"): "T7"}

        def subfolder(cid, *path):
            key, parent = (), "C7"
            for name in path:
                key = key + (name,)
                if key not in made:
                    fid = "NEW_" + "_".join(key)
                    d.add(fid, name, FOLDER, parent)
                    made[key] = fid
                parent = made[key]
            return parent
        self.made = made
        self.media._subfolder = subfolder
        self.media.ensure_client_media_root = lambda cid: "C7"

    def add_owner_file(self, fid, name, mime="video/mp4", size=50):
        self.fake.add(fid, name, mime, "SRCFOLDER01", size, driveId="")


# ─── A. Links ───────────────────────────────────────────────────────────────

class Links(Base):
    def test_every_link_shape(self):
        p = self.dp.parse_ref
        cases = {
            "https://drive.google.com/file/d/1AbCdEfGhIjK/view?usp=sharing": "1AbCdEfGhIjK",
            "https://drive.google.com/file/d/1AbCdEfGhIjK/view?usp=drive_link&resourcekey=0-xyz": "1AbCdEfGhIjK",
            "https://drive.google.com/open?id=1AbCdEfGhIjK&authuser=0": "1AbCdEfGhIjK",
            "https://drive.google.com/uc?id=1AbCdEfGhIjK&export=download": "1AbCdEfGhIjK",
            "https://drive.google.com/drive/folders/1FoLdEr_-99?usp=sharing": "1FoLdEr_-99",
            "https://drive.google.com/drive/u/1/folders/1FoLdEr_-99": "1FoLdEr_-99",
            "https://docs.google.com/document/d/1DoC_ument-1/edit?usp=sharing": "1DoC_ument-1",
            "https://docs.google.com/spreadsheets/d/1ShEeT_ab-2/edit#gid=0": "1ShEeT_ab-2",
            "https://docs.google.com/presentation/d/1SlIdEs_ab3/edit": "1SlIdEs_ab3",
            "1AbCdEfGhIjK": "1AbCdEfGhIjK",
            "  1AbCdEfGhIjK\n": "1AbCdEfGhIjK",
            "just words": "",
        }
        for link, want in cases.items():
            self.assertEqual(p(link), want, link)

    def test_a_link_inside_chat_text(self):
        f = self.dp.find_link
        self.assertEqual(f("hi, here it is: https://drive.google.com/file/d/1AbCdEfGhIjK/view?usp=sharing thanks"),
                         "https://drive.google.com/file/d/1AbCdEfGhIjK/view?usp=sharing")
        self.assertTrue(f("תוסיף את זה (https://docs.google.com/document/d/1DoC_ument-1/edit)"))
        self.assertEqual(f("my site https://example.com/file/d/1AbCdEfGhIjK"), "")
        self.assertEqual(f("no link here"), "")


# ─── B. add_by_link ─────────────────────────────────────────────────────────

class AddByLink(Base):
    def test_a_file_is_copied_inside_drive_by_type(self):
        out = self.dp.add_by_link(7, "https://drive.google.com/file/d/OWNERFILE_A/view?usp=sharing")
        self.assertEqual(out["source"]["name"], "a.mp4")
        self.assertEqual(out["job"]["status"], "done")
        self.assertEqual(self.fake.copies, [("OWNERFILE_A", "V7")])
        self.assertEqual(out["job"]["source"]["id"], "OWNERFILE_A")

    def test_a_repeated_link_never_makes_a_duplicate(self):
        link = "https://drive.google.com/open?id=OWNERFILE_B"
        self.dp.add_by_link(7, link)
        again = self.dp.add_by_link(7, link + "&usp=sharing")
        self.assertEqual(self.fake.copies, [("OWNERFILE_B", "I7")])
        self.assertEqual(again["job"]["counts"], {"already": 1})

    def test_a_folder_link_copies_everything_with_a_result_per_file(self):
        out = self.dp.add_by_link(7, "https://drive.google.com/drive/folders/SRCFOLDER01?usp=sharing", budget_s=0)
        self.assertEqual(out["job"]["status"], "queued")
        job = self.run_job()
        self.assertEqual(job["status"], "done")
        self.assertEqual(sorted(self.fake.copies),
                         [("OWNERFILE_A", "V7"), ("OWNERFILE_B", "I7"), ("OWNERFILE_C", "D7")])
        self.assertEqual(len(job["files"]), 3)

    def test_the_same_active_source_is_the_same_job(self):
        a = self.dp.add_by_link(7, "SRCFOLDER01", budget_s=0)
        b = self.dp.add_by_link(7, "https://drive.google.com/drive/folders/SRCFOLDER01", budget_s=0)
        self.assertEqual(a["job"]["id"], b["job"]["id"])
        self.assertEqual(len(self.dp.load(7)), 1)

    def test_not_shared_is_one_calm_line_with_the_address(self):
        with self.assertRaises(self.dp.PullRefused) as ctx:
            self.dp.add_by_link(7, "https://drive.google.com/drive/folders/PRIVATE0001")
        e = ctx.exception
        self.assertEqual(e.code, "not_shared")
        self.assertEqual(e.share_with, SA)
        self.assertIn(SA, e.text)
        for raw in ("404", "403", "Error", "file_not_found"):
            self.assertNotIn(raw, e.text)
        self.assertEqual(self.fake.copies, [])

    def test_a_client_cannot_pull_from_our_own_storage(self):
        # another client's file in OUR Shared Drive: a client link is refused,
        # the owner's attach may copy it
        self.fake.add("C9", "client-9 — Shop", FOLDER, "MEDIA", driveId="OURDRIVE")
        self.fake.add("v_other_file1", "secret.mp4", "video/mp4", "C9", 9, driveId="OURDRIVE")
        with self.assertRaises(self.dp.PullRefused) as ctx:
            self.dp.add_by_link(7, "https://drive.google.com/file/d/v_other_file1/view", for_client=True)
        self.assertEqual(ctx.exception.code, "own_storage")
        self.assertEqual(self.fake.copies, [])
        self.dp.attach_existing(7, "v_other_file1")
        self.assertEqual(self.fake.copies, [("v_other_file1", "V7")])

    def test_bad_link_and_trash(self):
        for ref, code in (("hello", "bad_ref"),):
            with self.assertRaises(self.dp.PullRefused) as ctx:
                self.dp.add_by_link(7, ref)
            self.assertEqual(ctx.exception.code, code)
        self.add_owner_file("OWNERFILE_T", "old.mp4")
        self.fake.files["OWNERFILE_T"]["trashed"] = True
        with self.assertRaises(self.dp.PullRefused) as ctx:
            self.dp.add_by_link(7, "OWNERFILE_T")
        self.assertEqual(ctx.exception.code, "trashed")


# ─── C. The client card + the chat hook ─────────────────────────────────────

class LinkCard(Base):
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

    def prepare(self, link, rows=(), lang="he"):
        params = self.aa.validate_params(self.aa.ACTIONS["add_media_from_drive_link"], {"link": link}, 7)
        return self.aa._prepare_drive_link(7, params, {"language": lang}, list(rows))

    def test_a_link_we_can_open_is_one_card(self):
        out = self.prepare("https://drive.google.com/file/d/OWNERFILE_A/view?usp=sharing")
        self.assertEqual(out["params"], {"link": "OWNERFILE_A", "name": "a.mp4", "kind": "file"})
        self.assertIn("a.mp4", out["notice"])
        stored = self.aa._validate_drive_link_stored(7, out["params"])
        self.assertEqual(stored["link"], "OWNERFILE_A")
        with self.assertRaises(ValueError):
            self.aa._validate_drive_link_stored(7, {**out["params"], "target": "x"})

    def test_the_same_link_shows_the_open_card(self):
        row = {"id": 4, "params": {"link": "OWNERFILE_A"}}
        self.assertEqual(self.prepare("OWNERFILE_A", rows=[row]), {"existing": row})

    def test_not_shared_is_a_calm_line_with_the_address_in_every_language(self):
        for lang in ("he", "en", "fr", "ar", "ru"):
            out = self.prepare("https://drive.google.com/drive/folders/PRIVATE0001", lang=lang)
            self.assertIn(SA, out["refuse"], lang)
            self.assertNotIn("{email}", out["refuse"])
            self.assertNotIn("error", out["refuse"].lower())

    def test_the_click_copies_and_says_so(self):
        spec = self.aa.ACTIONS["add_media_from_drive_link"]
        result = spec["run"](7, {"link": "OWNERFILE_A", "name": "a.mp4", "kind": "file"}, None,
                             row={"origin": "client_chat"})
        self.assertEqual(self.fake.copies, [("OWNERFILE_A", "V7")])
        self.assertIn("a.mp4", spec["done_message"](result, "en"))
        # a second click on a repeated link: no duplicate
        spec["run"](7, {"link": "OWNERFILE_A", "name": "a.mp4", "kind": "file"}, None, row={})
        self.assertEqual(len(self.fake.copies), 1)

    def test_who_may_propose_it(self):
        aa = self.aa
        add, attach = aa.ACTIONS["add_media_from_drive_link"], aa.ACTIONS["attach_existing_media"]
        self.assertTrue(aa._may_propose(add, "client"))
        self.assertTrue(aa._may_propose(add, "admin"))
        # attach searches EVERY client's files: never a client chat
        self.assertFalse(aa._may_propose(attach, "client"))
        self.assertTrue(aa._may_propose(attach, "admin"))
        self.assertEqual(attach["approver"], "admin")

    def test_card_texts_have_every_language_and_no_internal_words(self):
        aa = self.aa
        texts = list(aa._LINK_REFUSALS.values()) + list(aa._LINK_RUN_REFUSALS.values()) + \
            [aa._LINK_DONE, aa._LINK_DONE_SOME, aa._LINK_UNDERWAY, aa._LINK_FOUND]
        spec = aa.ACTIONS["add_media_from_drive_link"]
        texts += [spec["title"], spec["what"], spec["button"]]
        for t in texts:
            self.assertEqual(set(t), set(aa.LANGS))
            for lang, line in t.items():
                for bad in ("add_media_from_drive_link", "drive_pull", "files.copy", "appProperties",
                            "צוות", "the team"):
                    self.assertNotIn(bad, line, f"{lang}: {line}")

    def test_the_chat_hook_proposes_the_card_for_a_pasted_link(self):
        src = P._read("agents", "support_agent.py")
        hook = src[src.index("def _drive_link_turn"):]
        hook = hook[:hook.index("\ndef ")]
        self.assertIn("drive_pull.find_link(message)", hook)
        self.assertIn('"add_media_from_drive_link"', hook)
        # both chats: the concierge and every persona, before anything else routes
        self.assertEqual(src.count("_drive_link_turn(message, result)"), 2)
        for path in ('"general"', "persona_id"):
            i = src.index(f"_drive_link_turn(message, result):\n        return _route_proposal(client_id, {path}")
            self.assertGreater(i, 0)


# ─── D. Attach an existing file from anywhere ───────────────────────────────

class Attach(Base):
    def setUp(self):
        super().setUp()
        d = self.fake
        # another client's folder, and client 7's SECOND folder outside the media root
        d.add("C9", "client-9 — Shop", FOLDER, "MEDIA", modified="2026-02-01T00:00:00Z", driveId="OURDRIVE")
        d.add("t3_c9_file01", "test 3.mp4", "video/mp4", "C9", 1048576 * 3, "2026-11-11T08:00:00Z", driveId="OURDRIVE")
        d.add("OLDFOLDER_7", "client-7 — Cafe (old)", FOLDER, "OWNERDRIVE", modified="2025-05-01T00:00:00Z", driveId="")
        d.add("t3_old_file1", "test_3.mov", "video/quicktime", "OLDFOLDER_7", 1048576, "2026-10-01T08:00:00Z", driveId="")
        d.add("t30", "test 30.mp4", "video/mp4", "OLDFOLDER_7", 5, "2026-10-02T08:00:00Z", driveId="")

    def test_found_everywhere_with_folder_date_size(self):
        out = self.dp.find_anywhere("test 3", 7)
        ids = [c["id"] for c in out["candidates"]]
        self.assertEqual(set(ids), {"t3_c9_file01", "t3_old_file1"})            # best matches only, no "test 30"
        by = {c["id"]: c for c in out["candidates"]}
        self.assertIn("client-9 — Shop", by["t3_c9_file01"]["folder"])
        self.assertIn("client-7 — Cafe (old)", by["t3_old_file1"]["folder"])
        self.assertEqual(by["t3_c9_file01"]["size"], 1048576 * 3)
        self.assertEqual(by["t3_c9_file01"]["modified_time"][:10], "2026-11-11")
        self.assertFalse(by["t3_c9_file01"]["in_client_folder"])

    def test_found_by_link_or_id(self):
        out = self.dp.find_anywhere("https://drive.google.com/file/d/t3_old_long1/view", 7)
        self.assertEqual(out["candidates"], [])
        self.assertTrue(out.get("not_shared"))
        self.fake.add("LONGID_0001", "x.mp4", "video/mp4", "OLDFOLDER_7", 1, driveId="")
        out = self.dp.find_anywhere("LONGID_0001", 7)
        self.assertEqual([c["id"] for c in out["candidates"]], ["LONGID_0001"])

    def test_several_are_one_card_and_the_pick_is_copied_never_moved(self):
        aa = LinkCard.aa if hasattr(LinkCard, "aa") else None
        sys.modules.pop("core.agent_actions", None)
        import core
        if hasattr(core, "agent_actions"):
            delattr(core, "agent_actions")
        from core import agent_actions as aa
        self.addCleanup(lambda: sys.modules.pop("core.agent_actions", None))
        prepared = aa._prepare_attach(7, {"query": "test 3"}, {"language": "he"}, [])
        params = aa._validate_attach_stored(7, prepared["params"])
        self.assertEqual(len(aa._attach_choices(params)), 2)
        label = params["labels"]["t3_c9_file01"]
        for bit in ("test 3.mp4", "client-9 — Shop", "2026-11-11", "3.0 MB"):
            self.assertIn(bit, label)
        before = copy.deepcopy(self.fake.files["t3_c9_file01"])
        out = aa._run_attach_existing(7, {**params, "mode": "t3_c9_file01"}, None, row={"origin": "admin_chat"})
        self.assertEqual(self.fake.copies, [("t3_c9_file01", "V7")])
        self.assertEqual(self.fake.files["t3_c9_file01"], before)         # the original: same place, untouched
        self.assertIn("test 3.mp4", out["file"])
        with self.assertRaises(aa.ActionFailed):
            aa._run_attach_existing(7, {**params, "mode": "nope"}, None)
        one = aa._prepare_attach(7, {"query": "secret thing"}, {"language": "he"}, [])
        self.assertIn("refuse", one)


# ─── E. Merge the second folder into the connected one ──────────────────────

class Merge(Base):
    def setUp(self):
        super().setUp()
        d = self.fake
        d.add("OLDFOLDER_7", "client-7 — Cafe (old)", FOLDER, "OWNERDRIVE", modified="2025-05-01T00:00:00Z", driveId="")
        d.add("OV", "videos", FOLDER, "OLDFOLDER_7", driveId="")
        d.add("OT", "tiktok", FOLDER, "OV", driveId="")
        d.add("OI", "images", FOLDER, "OLDFOLDER_7", driveId="")
        d.add("OR", "reels", FOLDER, "OLDFOLDER_7", driveId="")
        d.add("o_root", "brief.pdf", "application/pdf", "OLDFOLDER_7", 7, driveId="")
        d.add("o_vid", "test 3.mp4", "video/mp4", "OV", 40, driveId="")
        d.add("o_tik", "clip.mp4", "video/mp4", "OT", 41, driveId="")
        d.add("o_same", "logo.png", "image/png", "OI", 99, driveId="")
        d.add("o_diff", "banner.png", "image/png", "OI", 12, driveId="")
        d.add("o_reel", "r1.mp4", "video/mp4", "OR", 3, driveId="")
        # already in the connected folder: same name+size, and same name other size
        d.add("c_same", "logo.png", "image/png", "I7", 99)
        d.add("c_diff", "banner.png", "image/png", "I7", 11)

    def test_plan_lists_both_folders_and_which_is_connected(self):
        plan = self.dmg.plan(7)
        self.assertEqual(plan["connected"], "C7")
        by = {f["id"]: f for f in plan["folders"]}
        self.assertEqual(set(by), {"C7", "OLDFOLDER_7"})
        self.assertTrue(by["C7"]["connected"])
        self.assertFalse(by["OLDFOLDER_7"]["connected"])
        self.assertFalse(by["OLDFOLDER_7"]["under_media_root"])
        self.assertEqual(by["OLDFOLDER_7"]["files"], 6)
        self.assertEqual(by["OLDFOLDER_7"]["subfolders"], 4)
        self.assertEqual(plan["mergeable"], ["OLDFOLDER_7"])
        self.assertEqual(plan["folders"][0]["id"], "C7")            # the connected one first

    def test_everything_copied_with_the_same_structure_nothing_deleted(self):
        before = copy.deepcopy({k: v for k, v in self.fake.files.items() if k.startswith("o") or k == "OLDFOLDER_7"})
        self.dmg.start(7, "OLDFOLDER_7")
        job = self.run_job(rounds=20)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["mode"], "merge")
        dest = dict(self.fake.copies)
        self.assertEqual(dest["o_root"], "C7")                       # root → root
        self.assertEqual(dest["o_vid"], "V7")                        # videos/ → videos/
        self.assertEqual(dest["o_tik"], "T7")                        # videos/tiktok → videos/tiktok
        self.assertEqual(dest["o_diff"], "I7")                       # same name, other size → copied
        self.assertEqual(dest["o_reel"], self.made[("reels",)])      # a sub-folder the connected one lacked
        self.assertNotIn("o_same", dest)                             # same name+size → skipped
        status = {f["name"]: f["status"] for f in job["files"]}
        self.assertEqual(status["logo.png"], "already_same")
        # nothing moved, nothing deleted
        for fid, f in before.items():
            self.assertEqual(self.fake.files[fid], f, fid)

    def test_a_rerun_copies_nothing(self):
        self.dmg.start(7, "OLDFOLDER_7")
        self.run_job(rounds=20)
        n = len(self.fake.copies)
        self.dmg.start(7, "OLDFOLDER_7")
        job = self.run_job(rounds=20)
        self.assertEqual(len(self.fake.copies), n)
        self.assertEqual(set(job["counts"]), {"already", "already_same"})

    def test_refusals(self):
        for source, code in (("C7", "is_connected"), ("SRCFOLDER01", "not_listed")):
            with self.assertRaises(self.dmg.MergeRefused) as ctx:
                self.dmg.start(7, source)
            self.assertEqual(ctx.exception.code, code)


# ─── F. One resolver + the diagnosis ────────────────────────────────────────

class Resolver(Base):
    def test_a_drive_error_never_reads_as_no_folder(self):
        def broken(*a, **k):
            raise P.DriveError("other")
        self.drv.find_child_folders_by_prefix = broken
        self.dm._root_cache.clear()
        self.assertEqual(self.dm.shared_client_root(7), "")          # readers: calm, nothing cached
        with self.assertRaises(self.dm.RootUnknown):
            self.dm.shared_client_root(7, strict=True)               # the writer: never creates on a guess

    def test_the_writer_uses_the_strict_resolver_and_converges(self):
        src = P._read("agents", "media_agent.py")
        body = src[src.index("def ensure_client_media_root"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("drive_mirror.shared_client_root(client_id, strict=True)", body)
        create = body.index("drive.ensure_folder(")
        self.assertLess(body.index("shared_client_root"), create)
        # after a create, the same rule (oldest by prefix) decides
        self.assertGreater(body.index("find_child_folders_by_prefix(media_root"), create)

    def test_only_the_media_agent_creates_a_client_media_folder(self):
        creators = []
        for folder in ("core", "agents"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if not name.endswith(".py"):
                    continue
                src = P._read(folder, name)
                for m in re.finditer(r"ensure_folder\(([^\n]*)", src):
                    window = src[max(0, m.start() - 300):m.start()] + m.group(1)
                    if "client-{" in window:
                        creators.append((f"{folder}/{name}", m.group(1).strip()))
        files = sorted({c[0] for c in creators})
        self.assertEqual(files, ["agents/media_agent.py", "core/api_server.py"])
        # the api_server one is the offboarding ARCHIVE root, not the media root
        self.assertTrue(all("archive_root_folder_id" in c[1] for c in creators if c[0] == "core/api_server.py"))

    def test_media_diagnosis_shows_both_trees_and_the_find_results(self):
        d = self.fake
        self.db.tables.setdefault("client_activity", []).append(
            {"client_id": 7, "agent_name": "media_agent", "action_type": "media_folder_created",
             "details": {"folder_id": "C7"}})
        d.add("OLDFOLDER_7", "client-7 — Cafe (old)", FOLDER, "OWNERDRIVE", driveId="")
        d.add("t3_old_file1", "test 3.mp4", "video/mp4", "OLDFOLDER_7", 9, driveId="")
        out = self.dmg.media_diagnosis(7, "test 3")
        self.assertEqual(out["stored_folder_id"], "C7")
        self.assertEqual(out["resolved_folder_id"], "C7")
        names = {f["id"]: f for f in out["folders_named_like_client"]}
        self.assertTrue(names["C7"]["connected"])
        self.assertEqual([t["name"] for t in names["OLDFOLDER_7"]["tree"]], ["test 3.mp4"])
        self.assertEqual(out["find_in_client_folder"], [])            # not in the connected folder…
        self.assertEqual([c["id"] for c in out["find_anywhere"]["candidates"]], ["t3_old_file1"])   # …but findable
        self.assertNotIn("token", str(out).lower())


class Wiring(unittest.TestCase):
    def test_every_admin_drive_route_is_guarded(self):
        src = P._read("core", "api_server.py")
        tree = ast.parse(src)
        seen = 0
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            routes = [ast.unparse(d) for d in node.decorator_list]
            if any("/api/admin/drive" in r for r in routes):
                seen += 1
                body = ast.get_source_segment(src, node)
                self.assertTrue("_require_admin(request)" in body or any("_admin_only" in r for r in routes),
                                node.name)
        self.assertGreaterEqual(seen, 12)

    def test_client_routes_are_session_bound_and_never_from_our_storage(self):
        src = P._read("core", "api_server.py")
        body = src[src.index("def client_media_add_link"):]
        body = body[:body.index("\n@app.")]
        self.assertIn("_require_session(request)", body)
        self.assertIn("ERR_READ_ONLY", body)
        self.assertIn("for_client=True", body)
        self.assertNotIn("find_anywhere", src[src.index("def client_media_add_link"):src.index("def admin_media_drive(")])

    def test_both_pages_have_the_link_field(self):
        tab = P._read("dashboard", "client", "index.html")
        self.assertIn('id="mlLink"', tab)
        self.assertIn("/api/client/media/add-link", tab)
        for key in ("ml_link_ph", "ml_link_add", "ml_link_not_shared", "ml_link_copy_email"):
            self.assertRegex(tab, rf"{key}: \{{ he:'[^']+', en:'[^']+', fr:'[^']+', ar:'[^']+', ru:'[^']+' \}}")
        page = P._read("dashboard", "admin", "drive", "index.html")
        for needle in ("/api/admin/drive-pull/add-link", "/api/admin/drive/find-anywhere", "/api/admin/drive/attach",
                       "/api/admin/drive/merge/", "לא מוחק כלום"):
            self.assertIn(needle, page)

    def test_the_cloud_shell_script(self):
        src = P._read("scripts", "diagnose_media.py")
        self.assertIn("/api/admin/drive/media-diagnose", src)
        self.assertIn("X-Admin-Key", src)
        self.assertNotRegex(src, r"print\([^)]*\bkey\b")
        self.assertIn('"test 3"', src)
        compile(src, "diagnose_media.py", "exec")

    def test_no_new_scope_no_env_var(self):
        for path in (("core", "drive_pull.py"), ("core", "drive_merge.py"), ("scripts", "diagnose_media.py")):
            src = P._read(*path)
            self.assertNotIn("googleapis.com/auth/", src)
            self.assertNotIn("os.environ", src)


if __name__ == "__main__":
    unittest.main()
