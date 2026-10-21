"""publish_youtube_from_media (core/agent_actions): a client-approved upload
of a video from the client's own Drive media folder to their YouTube channel.

Stdlib only. Drive (parents walk + metadata) and the YouTube agent are stubs;
agent_actions' whitelist, validators and decide() run for real, on the same
in-memory database as tests/test_agent_actions.py.

Run from the repo root:  python tests/test_youtube_upload_action.py -v
"""
import ast
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_agent_actions import CLIENTS, ActionTestCase   # noqa: E402  (FakeDB + seams)
from core import agent_actions as aa                    # noqa: E402

MB = 1048576
OWN_VIDEO, OWN_BIG, OWN_IMAGE, OWN_TRASHED = "1OwnVideoAbcdef", "1OwnBigVideoXyz", "1OwnImageQwerty", "1OwnTrashedVid0"
FOREIGN = "1SomeoneElseVid"
DRIVE = {
    OWN_VIDEO: {"id": OWN_VIDEO, "name": "promo-autumn.mp4", "mimeType": "video/mp4", "size": str(12 * MB)},
    OWN_BIG: {"id": OWN_BIG, "name": "wedding.mov", "mimeType": "video/quicktime", "size": str(600 * MB)},
    OWN_IMAGE: {"id": OWN_IMAGE, "name": "logo.png", "mimeType": "image/png", "size": str(MB)},
    OWN_TRASHED: {"id": OWN_TRASHED, "name": "old.mp4", "mimeType": "video/mp4", "size": str(MB), "trashed": True},
    FOREIGN: {"id": FOREIGN, "name": "not-yours.mp4", "mimeType": "video/mp4", "size": str(MB)},
}


class _NotInClientFolder(Exception):
    pass


def _assert_in_client_tree(client_id, root, item_id):
    if item_id == FOREIGN or item_id not in DRIVE:
        raise _NotInClientFolder(item_id)


class Uploads:
    def __init__(self):
        self.today, self.published = 0, []

    def publish(self, client_id, spec):
        self.published.append((client_id, dict(spec)))
        self.today += 1
        return {"success": True, "video_id": "yt123", "note": "private"}


STATE_OK = {
    "channels": {"by_channel": {"youtube": {"status": "active"}}},
    "connections": {"active_platforms": ["youtube"]},
    "media_folder": {"exists": True, "videos": [{"drive_file_id": OWN_VIDEO, "name": "promo-autumn.mp4",
                                                  "uploadable": True}],
                     "youtube_uploads_today": 0, "youtube_daily_upload_cap": 3},
}


class UploadActionCase(ActionTestCase):
    def setUp(self):
        super().setUp()
        self.uploads = Uploads()
        self.saved_modules = {n: sys.modules.get(n) for n in
                              ("core.drive_mirror", "core.drive_service", "agents.youtube_content_agent")}
        sys.modules["core.drive_mirror"] = types.SimpleNamespace(
            client_root_id=lambda cid: "rootFolder123456", NotInClientFolder=_NotInClientFolder,
            _assert_in_client_tree=_assert_in_client_tree)
        sys.modules["core.drive_service"] = types.SimpleNamespace(get_meta=lambda fid, fields="": dict(DRIVE[fid]))
        sys.modules["agents.youtube_content_agent"] = types.SimpleNamespace(
            DAILY_UPLOADS_PER_CLIENT=3, uploads_today=lambda cid: self.uploads.today,
            publish=self.uploads.publish)
        aa._media_meta.clear()

    def tearDown(self):
        for name, module in self.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        super().tearDown()

    def propose_upload(self, params, state=STATE_OK, client_id=40, origin="client_chat"):
        return aa.propose(client_id, origin, "media",
                          {"name": "publish_youtube_from_media", "params": params}, state=state)

    def stored(self, params, client_id=40):
        row = {"id": len(self.rows()) + 1, "client_id": client_id, "action": "publish_youtube_from_media",
               "params": params, "origin": "client_chat", "persona": "media", "approver": "client",
               "status": "proposed", "created_at": aa._now().isoformat()}
        self.db.tables.setdefault(aa.TABLE, []).append(row)
        return row["id"]


class Propose(UploadActionCase):
    def test_own_video_gets_a_client_card_naming_the_file_from_drive(self):
        result = self.propose_upload({"drive_file_id": OWN_VIDEO, "title": "Autumn promo"})
        card = result["card"]
        self.assertEqual(result["status"], "proposed")
        self.assertTrue(card["can_decide"])
        self.assertEqual(card["approver"], "client")
        self.assertIn("promo-autumn.mp4", card["what"])       # Drive's name, not the model's
        self.assertNotIn(OWN_VIDEO, card["what"])             # the id is never shown
        self.assertEqual(card["params"], {})

    def test_foreign_drive_file_refused(self):
        self.assertEqual(self.propose_upload({"drive_file_id": FOREIGN})["error"], "invalid_params")
        self.assertEqual(self.rows(), [])

    def test_unknown_or_malformed_id_refused(self):
        for bad in ("1NoSuchFileHere00", "../etc/passwd", "", "x"):
            self.assertEqual(self.propose_upload({"drive_file_id": bad})["error"], "invalid_params", bad)

    def test_not_a_video_too_big_or_trashed_refused(self):
        for fid in (OWN_IMAGE, OWN_BIG, OWN_TRASHED):
            self.assertEqual(self.propose_upload({"drive_file_id": fid})["error"], "invalid_params", fid)

    def test_privacy_cannot_be_set(self):
        for key in ("privacy", "privacy_status", "privacyStatus"):
            result = self.propose_upload({"drive_file_id": OWN_VIDEO, key: "public"})
            self.assertEqual(result["error"], "invalid_params", key)

    def test_title_is_sanitised_and_capped(self):
        title = aa._v_title("  Hello‮<script>  world\n" + "x" * 300, 40)
        self.assertNotIn("<", title)
        self.assertNotIn("‮", title)
        self.assertNotIn("\n", title)
        self.assertLessEqual(len(title), aa.YOUTUBE_TITLE_MAX)
        with self.assertRaises(ValueError):
            aa._v_title(" <> ", 40)

    def test_no_youtube_connection_means_no_card(self):
        not_connected = {**STATE_OK, "channels": {"by_channel": {"youtube": {"status": "not_connected"}}},
                         "connections": {"active_platforms": []}}
        dead_grant = {**STATE_OK, "channels": {"by_channel": {"youtube": {"status": "needs_attention",
                                                                         "reason": "reconnect"}}}}
        for state in (not_connected, dead_grant):
            self.assertEqual(self.propose_upload({"drive_file_id": OWN_VIDEO}, state=state)["error"],
                             "not_available")

    def test_no_uploadable_video_means_no_card(self):
        state = {**STATE_OK, "media_folder": {"exists": True, "videos": [
            {"drive_file_id": OWN_BIG, "name": "wedding.mov", "uploadable": False}]}}
        self.assertEqual(self.propose_upload({"drive_file_id": OWN_VIDEO}, state=state)["error"], "not_available")

    def test_cap_reached_means_no_card(self):
        state = {**STATE_OK, "media_folder": {**STATE_OK["media_folder"], "youtube_uploads_today": 3}}
        self.assertEqual(self.propose_upload({"drive_file_id": OWN_VIDEO}, state=state)["error"], "not_available")

    def test_offered_to_every_client_persona_but_not_the_admin_chat(self):
        names = {a["name"] for a in aa.available(40, "client_chat", STATE_OK, client=CLIENTS[40])}
        self.assertIn("publish_youtube_from_media", names)                    # not internal_only
        self.assertEqual(self.propose_upload({"drive_file_id": OWN_VIDEO}, origin="admin_chat")["error"],
                         "not_allowed_here")


class Decide(UploadActionCase):
    def test_click_uploads_privately_and_returns_the_studio_link(self):
        pid = self.stored({"drive_file_id": OWN_VIDEO, "title": "Autumn promo"})
        result = aa.decide(pid, "client", 40, "approve")
        self.assertEqual(result["status"], "done")
        (cid, spec), = self.uploads.published
        self.assertEqual(spec, {"drive_file_id": OWN_VIDEO, "title": "Autumn promo", "description": ""})
        self.assertNotIn("privacy", " ".join(spec))                          # nothing to set it with
        self.assertIn("https://studio.youtube.com/video/yt123/edit", result["message"])
        self.assertIn("פרטי", result["message"])
        self.assertIn("studio.youtube.com", self.posted_client[-1][2])       # also into the chat thread

    def test_title_defaults_to_the_file_name(self):
        pid = self.stored({"drive_file_id": OWN_VIDEO})
        aa.decide(pid, "client", 40, "approve")
        self.assertEqual(self.uploads.published[0][1]["title"], "promo-autumn")

    def test_double_click_uploads_once(self):
        pid = self.stored({"drive_file_id": OWN_VIDEO})
        aa.decide(pid, "client", 40, "approve")
        self.assertTrue(aa.decide(pid, "client", 40, "approve")["already"])
        self.assertEqual(len(self.uploads.published), 1)

    def test_daily_cap_refuses_with_its_own_line_and_no_alert(self):
        self.uploads.today = 3
        pid = self.stored({"drive_file_id": OWN_VIDEO})
        result = aa.decide(pid, "client", 40, "approve")
        self.assertEqual(result["code"], "daily_cap")
        self.assertIn("3", result["message"])
        self.assertEqual(self.uploads.published, [])
        self.assertEqual(self.alerts, [])
        self.assertEqual(self.rows()[0]["outcome"], "daily_cap")

    def test_file_moved_out_of_the_folder_after_proposing_is_refused(self):
        pid = self.stored({"drive_file_id": FOREIGN})
        self.assertEqual(aa.decide(pid, "client", 40, "approve")["code"], "invalid_params")
        self.assertEqual(self.uploads.published, [])

    def test_privacy_smuggled_into_the_stored_row_is_refused(self):
        pid = self.stored({"drive_file_id": OWN_VIDEO, "privacy_status": "public"})
        self.assertEqual(aa.decide(pid, "client", 40, "approve")["code"], "invalid_params")
        self.assertEqual(self.uploads.published, [])

    def test_another_clients_card_is_not_found(self):
        pid = self.stored({"drive_file_id": OWN_VIDEO}, client_id=40)
        self.assertEqual(aa.decide(pid, "client", 27, "approve")["code"], "not_found")

    def test_admin_cannot_click_it_for_the_client(self):
        pid = self.stored({"drive_file_id": OWN_VIDEO})
        self.assertEqual(aa.decide(pid, "admin", 40, "approve")["code"], "not_your_decision")


class CapTextMatchesTheConstant(unittest.TestCase):
    def test_refusal_and_description_name_the_real_cap(self):
        with open(os.path.join(ROOT, "agents", "youtube_content_agent.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        cap = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                   and getattr(n.targets[0], "id", "") == "DAILY_UPLOADS_PER_CLIENT")
        spec = aa.ACTIONS["publish_youtube_from_media"]
        self.assertIn(f"Max {cap} uploads", spec["does"])
        for text in spec["refusals"]["daily_cap"].values():
            self.assertIn(str(cap), text)


if __name__ == "__main__":
    unittest.main()
