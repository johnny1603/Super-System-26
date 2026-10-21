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

# agents/youtube_metadata runs FOR REAL here; only what it imports at module
# level that needs the platform (core.agent_base → master_agent) is stubbed,
# and its LLM + page sources are seams set per test (install_metadata_seams).
_SAVED_AGENT_BASE = sys.modules.get("core.agent_base")
sys.modules["core.agent_base"] = types.SimpleNamespace(
    log_step=lambda *a, **k: None, timed_step=lambda agent, step, fn: fn(), agent_alert=lambda *a, **k: None)
sys.modules.pop("agents.youtube_metadata", None)
from agents import youtube_metadata as ym               # noqa: E402

SITE = "https://www.shop.co.il"
PAGES = [{"title": "Shoes", "url": f"{SITE}/shoes"}, {"title": "Contact", "url": f"{SITE}/contact"}]
DRAFT = {"needs_clarification": False,
         "title": "נעליים מעוצבות בעבודת יד — קולקציית סתיו",
         "description_body": "נעליים מעוצבות שנעשות ביד, אחת אחת.\nכך נראית קולקציית הסתיו החדשה.",
         "cta": "בואו לראות את הקולקציה באתר",
         "links": [{"url": f"{SITE}/shoes", "label": "הקולקציה"}],
         "hashtags": ["#נעליים", "#עיצוב", "#עבודתיד"],
         "tags": ["נעליים מעוצבות", "נעליים בעבודת יד", "נעלי עור"],
         "category_id": "26"}
KNOWLEDGE = {"business": {"told_us": {"organic_target_phrases": "נעליים מעוצבות, נעליים בעבודת יד"},
                          "competitors": ["BigShoeCo"]}}


def install_metadata_seams(case, draft=None, revised=None):
    """The real pipeline with a scripted model. `case.llm_steps` records every
    LLM step; `draft`/`revised` are what the draft and revise steps answer."""
    case.llm_steps, case.llm_kwargs = [], []
    case.draft, case.revised = dict(draft or DRAFT), revised

    def llm(system, payload, step, client_id, max_tokens):
        case.llm_steps.append(step)
        case.llm_kwargs.append(payload)
        if step == "draft":
            return dict(case.draft)
        if step == "critique":
            return {"issues": []}
        return dict(case.revised if case.revised is not None else case.draft)
    case._saved_ym = {n: getattr(ym, n) for n in ("_llm", "_knowledge", "_research", "_site_pages",
                                                  "_landing_pages", "_search_videos")}
    ym._llm = llm
    ym._knowledge = lambda cid: dict(KNOWLEDGE)
    ym._research = lambda cid: {}
    ym._site_pages = lambda cid: (SITE, [dict(p) for p in PAGES])
    ym._landing_pages = lambda cid: []
    ym._search_videos = lambda query, language: []
    ym._inventory_cache.clear()
    ym._search_cache.clear()


def restore_metadata_seams(case):
    for name, fn in case._saved_ym.items():
        setattr(ym, name, fn)

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
        install_metadata_seams(self)

    def tearDown(self):
        restore_metadata_seams(self)
        for name, module in self.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        super().tearDown()

    def propose_upload(self, params, state=STATE_OK, client_id=40, origin="client_chat",
                       message="תעלה את הסרטון של קולקציית הסתיו ליוטיוב"):
        return aa.propose(client_id, origin, "media",
                          {"name": "publish_youtube_from_media", "params": params}, state=state,
                          context={"message": message, "language": "he"})

    def valid_params(self, file_id=OWN_VIDEO, client_id=40, **overrides):
        """A REAL stored proposal: the pipeline's own output, validated."""
        prepared = ym.prepare_publish(client_id, {"drive_file_id": file_id},
                                      {"message": "upload the autumn collection video", "language": "he",
                                       "file_name": "promo-autumn.mp4"}, [])
        return {**prepared["params"], **overrides}

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
    def test_click_uploads_exactly_the_approved_metadata_privately(self):
        params = self.valid_params()
        pid = self.stored(params)
        result = aa.decide(pid, "client", 40, "approve")
        self.assertEqual(result["status"], "done")
        (cid, spec), = self.uploads.published
        self.assertEqual(spec, {"drive_file_id": OWN_VIDEO, "title": params["title"],
                                "description": params["description"], "tags": params["tags"],
                                "category_id": params["category_id"],
                                "default_language": params["default_language"]})
        self.assertNotIn("privacy", " ".join(spec))                          # nothing to set it with
        self.assertIn("https://studio.youtube.com/video/yt123/edit", result["message"])
        self.assertIn("פרטי", result["message"])
        self.assertIn("studio.youtube.com", self.posted_client[-1][2])       # also into the chat thread

    def test_double_click_uploads_once(self):
        pid = self.stored(self.valid_params())
        aa.decide(pid, "client", 40, "approve")
        self.assertTrue(aa.decide(pid, "client", 40, "approve")["already"])
        self.assertEqual(len(self.uploads.published), 1)

    def test_daily_cap_refuses_with_its_own_line_and_no_alert(self):
        self.uploads.today = 3
        pid = self.stored(self.valid_params())
        result = aa.decide(pid, "client", 40, "approve")
        self.assertEqual(result["code"], "daily_cap")
        self.assertIn("3", result["message"])
        self.assertEqual(self.uploads.published, [])
        self.assertEqual(self.alerts, [])
        self.assertEqual(self.rows()[0]["outcome"], "daily_cap")

    def test_file_moved_out_of_the_folder_after_proposing_is_refused(self):
        pid = self.stored(self.valid_params(drive_file_id=FOREIGN))
        self.assertEqual(aa.decide(pid, "client", 40, "approve")["code"], "invalid_params")
        self.assertEqual(self.uploads.published, [])

    def test_privacy_smuggled_into_the_stored_row_is_refused(self):
        pid = self.stored(self.valid_params(privacy_status="public"))
        self.assertEqual(aa.decide(pid, "client", 40, "approve")["code"], "invalid_params")
        self.assertEqual(self.uploads.published, [])

    def test_the_old_minimal_shape_is_no_longer_uploadable(self):
        pid = self.stored({"drive_file_id": OWN_VIDEO, "title": "Autumn promo"})
        self.assertEqual(aa.decide(pid, "client", 40, "approve")["code"], "invalid_params")
        self.assertEqual(self.uploads.published, [])

    def test_another_clients_card_is_not_found(self):
        pid = self.stored(self.valid_params(), client_id=40)
        self.assertEqual(aa.decide(pid, "client", 27, "approve")["code"], "not_found")

    def test_admin_cannot_click_it_for_the_client(self):
        pid = self.stored(self.valid_params())
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


def tearDownModule():
    if _SAVED_AGENT_BASE is None:
        sys.modules.pop("core.agent_base", None)
    else:
        sys.modules["core.agent_base"] = _SAVED_AGENT_BASE


if __name__ == "__main__":
    unittest.main()
