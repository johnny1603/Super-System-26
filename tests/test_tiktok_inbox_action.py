"""send_tiktok_to_inbox + connect_tiktok (core/agent_actions): a client-
approved send of a video from the client's own Drive media folder to THEIR
TikTok inbox as a draft, and the reconnect card when the connection can't
carry it.

Stdlib only. Drive (parents walk + metadata) and the TikTok agent are stubs;
agent_actions' whitelist, validators, propose() and decide() run for real, on
the same in-memory database as tests/test_agent_actions.py.

Run from the repo root:  python tests/test_tiktok_inbox_action.py -v
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_agent_actions import ActionTestCase   # noqa: E402  (FakeDB + seams)
from core import agent_actions as aa            # noqa: E402

MB = 1048576
SMALL, BIG, IMAGE = "1SmallClipAbcdef", "1BigClipXyz12345", "1LogoImageQwerty"
FOREIGN = "1SomeoneElseVid0"
DRIVE = {
    SMALL: {"id": SMALL, "name": "promo-reel.mp4", "mimeType": "video/mp4", "size": str(20 * MB)},
    BIG: {"id": BIG, "name": "full-event.mov", "mimeType": "video/quicktime", "size": str(200 * MB)},
    IMAGE: {"id": IMAGE, "name": "logo.png", "mimeType": "image/png", "size": str(MB)},
    FOREIGN: {"id": FOREIGN, "name": "not-yours.mp4", "mimeType": "video/mp4", "size": str(MB)},
}


class _NotInClientFolder(Exception):
    pass


def _assert_in_client_tree(client_id, root, item_id):
    if item_id == FOREIGN or item_id not in DRIVE:
        raise _NotInClientFolder(item_id)


class TikTokAgent:
    """Stands in for agents.tiktok_content_agent: check_grant + publish."""

    def __init__(self):
        self.grant, self.sent, self.result = "ok", [], None

    def check_grant(self, client_id):
        return self.grant

    def publish(self, client_id, spec):
        self.sent.append((client_id, dict(spec)))
        return self.result or {"success": True, "publish_id": "v_inbox_1", "status": "SEND_TO_USER_INBOX"}


def state(tiktok_status="connected_no_data", videos=None):
    return {
        "channels": {"by_channel": {"tiktok": {"status": tiktok_status}}},
        "connections": {"active_platforms": ["tiktok"]},
        "media_folder": {"exists": True, "videos": videos if videos is not None else [
            {"drive_file_id": SMALL, "name": "promo-reel.mp4", "uploadable": True, "tiktok_uploadable": True},
            {"drive_file_id": BIG, "name": "full-event.mov", "uploadable": True, "tiktok_uploadable": False}]},
    }


class TikTokActionCase(ActionTestCase):
    def setUp(self):
        super().setUp()
        self.tiktok = TikTokAgent()
        self.saved_modules = {n: sys.modules.get(n) for n in
                              ("core.drive_mirror", "core.drive_service", "agents.tiktok_content_agent")}
        sys.modules["core.drive_mirror"] = types.SimpleNamespace(
            client_root_id=lambda cid: "rootFolder123456", NotInClientFolder=_NotInClientFolder,
            _assert_in_client_tree=_assert_in_client_tree)
        sys.modules["core.drive_service"] = types.SimpleNamespace(get_meta=lambda fid, fields="": dict(DRIVE[fid]))
        sys.modules["agents.tiktok_content_agent"] = types.SimpleNamespace(
            check_grant=self.tiktok.check_grant, publish=self.tiktok.publish)
        aa._media_meta.clear()
        self.saved_forget = aa._forget_state
        aa._forget_state = lambda cid: None

    def tearDown(self):
        aa._forget_state = self.saved_forget
        for name, module in self.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        super().tearDown()

    def send(self, client_id=27, file_id=SMALL, st=None, persona="media"):
        return aa.propose(client_id, "client_chat", persona,
                          {"name": "send_tiktok_to_inbox", "params": {"drive_file_id": file_id}},
                          state=st if st is not None else state(),
                          context={"message": "שלח לטיקטוק", "language": "he"})


# ─── Propose: the card ───────────────────────────────────────────────────────

class Propose(TikTokActionCase):
    def test_card_names_the_video_from_drive_and_runs_nothing(self):
        result = self.send()
        card = result["card"]
        self.assertEqual(card["action"], "send_tiktok_to_inbox")
        self.assertTrue(card["can_decide"])                        # the client's own click
        self.assertIn("promo-reel.mp4", card["what"])              # Drive's name, never the model's
        self.assertIn("טיוטה", card["what"])
        self.assertEqual(card["details"], {"file": "promo-reel.mp4"})
        self.assertEqual(card["params"], {})                       # the id is never shown
        self.assertEqual(self.tiktok.sent, [])

    def test_stored_params_are_only_the_file(self):
        self.send()
        self.assertEqual(self.rows()[0]["params"], {"drive_file_id": SMALL})

    def test_internal_test_client_27_is_a_normal_client_click(self):
        # billing_exempt: the same card and one-click approval, nothing billed on this path
        result = self.send(client_id=27)
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(self.rows()[0]["approver"], "client")
        self.assertEqual(self.alerts, [])

    def test_same_video_twice_shows_the_open_card(self):
        first, second = self.send(), self.send()
        self.assertEqual(first["card"]["id"], second["card"]["id"])
        self.assertEqual(len(self.rows()), 1)

    def test_over_the_tiktok_cap_is_refused(self):
        self.assertEqual(self.send(file_id=BIG)["error"], "invalid_params")

    def test_not_a_video_or_not_theirs_is_refused(self):
        self.assertEqual(self.send(file_id=IMAGE)["error"], "invalid_params")
        self.assertEqual(self.send(file_id=FOREIGN)["error"], "invalid_params")
        self.assertEqual(self.rows(), [])

    def test_not_offered_without_tiktok_in_the_plan(self):
        self.assertEqual(self.send(st=state(tiktok_status="not_in_plan"))["error"], "not_available")

    def test_not_offered_without_a_fitting_video(self):
        only_big = [{"drive_file_id": BIG, "name": "full-event.mov", "tiktok_uploadable": False}]
        self.assertEqual(self.send(st=state(videos=only_big))["error"], "not_available")

    def test_listed_for_the_chat_model(self):
        names = {a["name"] for a in aa.available(27, "client_chat", state(), client={"billing_exempt": True})}
        self.assertIn("send_tiktok_to_inbox", names)


class ProposeWithoutAWorkingConnection(TikTokActionCase):
    """(c): no connection / dead grant / no video.upload → the reconnect card
    and a plain Hebrew line, never a send card the click would fail on."""

    def check(self, grant, st):
        self.tiktok.grant = grant
        result = self.send(st=st)
        self.assertEqual(result["card"]["action"], "connect_tiktok")
        self.assertEqual(result["card"]["kind"], "link")
        self.assertTrue(result["card"]["can_decide"])
        self.assertIn("promo-reel.mp4", result["notice"])
        self.assertTrue(re.search("[֐-׿]", result["notice"]))   # Hebrew
        self.assertEqual([r["action"] for r in self.rows()], ["connect_tiktok"])
        return result

    def test_missing_video_upload_scope(self):
        result = self.check("missing_upload", state(tiktok_status="needs_attention"))
        self.assertIn("חידוש", result["notice"])

    def test_dead_grant(self):
        self.check("dead", state(tiktok_status="connected_no_data"))   # the snapshot didn't know yet

    def test_not_connected_at_all(self):
        result = self.check("not_connected", state(tiktok_status="not_connected"))
        self.assertIn("לחבר", result["notice"])

    def test_chat_reply_is_the_notice(self):
        # support_agent imports the platform (supabase, Claude) at module level,
        # so its routing line is checked in source: a switched card's notice
        # replaces the model's reply (which promised the send card)
        with open(os.path.join(ROOT, "agents", "support_agent.py"), encoding="utf-8") as f:
            self.assertIn('"reply": proposal.get("notice") or reply', f.read())

    def test_connect_click_opens_tiktok_consent(self):
        self.tiktok.grant = "missing_upload"
        card = self.send()["card"]
        out = aa.decide(card["id"], "client", 27, "approve")
        self.assertEqual(out["redirect"], "/api/oauth/tiktok/start")


# ─── Decide: the click ───────────────────────────────────────────────────────

class Approve(TikTokActionCase):
    def approve(self, client_id=27):
        card = self.send(client_id=client_id)["card"]
        return aa.decide(card["id"], "client", client_id, "approve")

    def test_sends_to_the_inbox_and_says_so_in_hebrew(self):
        out = self.approve()
        self.assertEqual(out["status"], "done")
        self.assertEqual(self.tiktok.sent, [(27, {"drive_file_id": SMALL})])   # no caption, no privacy
        self.assertIn("promo-reel.mp4", out["message"])
        self.assertIn("טיוטות", out["message"])
        self.assertIn("פתחו את אפליקציית טיקטוק", out["message"])
        self.assertEqual(self.posted_client[-1][1], "media")                   # into the persona's thread
        self.assertEqual(self.rows()[0]["status"], "done")

    def test_double_click_sends_once(self):
        card = self.send()["card"]
        aa.decide(card["id"], "client", 27, "approve")
        again = aa.decide(card["id"], "client", 27, "approve")
        self.assertTrue(again["already"])
        self.assertEqual(len(self.tiktok.sent), 1)

    def test_cancel_sends_nothing(self):
        card = self.send()["card"]
        out = aa.decide(card["id"], "client", 27, "cancel")
        self.assertEqual(out["status"], "cancelled")
        self.assertEqual(self.tiktok.sent, [])

    def test_another_client_cannot_click_it(self):
        card = self.send(client_id=27)["card"]
        self.assertEqual(aa.decide(card["id"], "client", 40, "approve")["code"], "not_found")

    def test_file_moved_out_of_the_folder_before_the_click(self):
        card = self.send()["card"]
        DRIVE_BACKUP = dict(DRIVE[SMALL])
        try:
            DRIVE[SMALL]["size"] = str(100 * MB)      # replaced by a bigger file meanwhile
            aa._media_meta.clear()
            self.assertEqual(aa.decide(card["id"], "client", 27, "approve")["code"], "invalid_params")
        finally:
            DRIVE[SMALL] = DRIVE_BACKUP
        self.assertEqual(self.tiktok.sent, [])

    def test_grant_lost_at_click_time_offers_reconnect(self):
        self.tiktok.result = {"success": False, "needs_reconnect": True, "errors": ["video.upload not granted"]}
        out = self.approve()
        self.assertEqual(out["code"], "needs_reconnect")
        self.assertIn("לחבר מחדש", out["message"])
        self.assertEqual(out["next_card"]["action"], "connect_tiktok")
        self.assertEqual(self.alerts, [])          # an explained refusal, not a failure alert

    def test_too_many_pending_drafts_is_explained(self):
        self.tiktok.result = {"success": False, "pending_limit": True, "errors": ["spam_risk_too_many_pending_share"]}
        out = self.approve()
        self.assertEqual(out["code"], "pending_limit")
        self.assertIn("תיבת הנכנסים", out["message"])
        self.assertNotIn("next_card", out)

    def test_other_failure_is_the_fixed_line_and_alerts(self):
        self.tiktok.result = {"success": False, "errors": ["TikTok 500"]}
        out = self.approve()
        self.assertEqual(out["message"], aa._pick(aa._FAILED, "he"))
        self.assertEqual(len(self.alerts), 1)


class Registry(unittest.TestCase):
    def test_every_text_in_five_languages(self):
        for name in ("send_tiktok_to_inbox", "connect_tiktok"):
            spec = aa.ACTIONS[name]
            for key in ("title", "what", "done"):
                self.assertEqual(set(spec[key]), set(aa.LANGS), f"{name}.{key}")
        for text in list(aa.ACTIONS["send_tiktok_to_inbox"]["refusals"].values()) + [
                aa._TIKTOK_CONNECT_FIRST, aa._TIKTOK_RECONNECT_FIRST]:
            self.assertEqual(set(text), set(aa.LANGS))

    def test_never_direct_post(self):
        with open(os.path.join(ROOT, "core", "agent_actions.py"), encoding="utf-8") as f:
            self.assertNotIn("init_direct_post", f.read())

    def test_client_state_marks_tiktok_sized_videos(self):
        from core.video_limits import TIKTOK_INBOX_MAX_MB, fits_tiktok
        self.assertEqual(TIKTOK_INBOX_MAX_MB, 64)
        self.assertTrue(fits_tiktok(64 * MB))
        self.assertFalse(fits_tiktok(64 * MB + 1))
        with open(os.path.join(ROOT, "core", "client_state.py"), encoding="utf-8") as f:
            self.assertIn('"tiktok_uploadable": fits_tiktok(', f.read())


if __name__ == "__main__":
    unittest.main()
