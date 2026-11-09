"""send_tiktok_to_inbox + connect_tiktok (core/agent_actions): a client-
approved send of a video from the client's own Drive media folder to THEIR
TikTok inbox as a draft, and the reconnect card when the connection can't
carry it.

Owner's size policy (2026-11-10): no size cap of ours. Up to
`tiktok_auto_upload_max_mb` (1536) the client's card is all it takes; above
it no card and no question — ONE owner approval request, and a calm line.
The click starts a durable job (core/tiktok_uploads, stubbed here — its own
tests are tests/test_tiktok_uploads.py) and the card stays running until
the job ends it with agent_actions.finish_deferred.

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
from core import tiktok_uploads                 # noqa: E402
from core import video_limits                   # noqa: E402

MB = 1048576
SMALL, MID, HUGE, IMAGE = "1SmallClipAbcdef", "1MidClipXyz12345", "1HugeClipQwe9876", "1LogoImageQwerty"
FOREIGN, EMPTY = "1SomeoneElseVid0", "1EmptyVideo00000"
DRIVE = {
    SMALL: {"id": SMALL, "name": "promo-reel.mp4", "mimeType": "video/mp4", "size": str(20 * MB)},
    # 600 MB: over the OLD 512 MB cap — now an ordinary automatic send
    MID: {"id": MID, "name": "full-event.mov", "mimeType": "video/quicktime", "size": str(600 * MB)},
    # 2 GB: above the owner's 1.5 GB threshold — his approval, never a refusal
    HUGE: {"id": HUGE, "name": "wedding-4k.mp4", "mimeType": "video/mp4", "size": str(2048 * MB)},
    IMAGE: {"id": IMAGE, "name": "logo.png", "mimeType": "image/png", "size": str(MB)},
    FOREIGN: {"id": FOREIGN, "name": "not-yours.mp4", "mimeType": "video/mp4", "size": str(MB)},
    EMPTY: {"id": EMPTY, "name": "broken.mp4", "mimeType": "video/mp4", "size": "0"},
}


class _NotInClientFolder(Exception):
    pass


def _assert_in_client_tree(client_id, root, item_id):
    if item_id == FOREIGN or item_id not in DRIVE:
        raise _NotInClientFolder(item_id)


class TikTokAgent:
    """Stands in for agents.tiktok_content_agent: check_grant."""

    def __init__(self):
        self.grant = "ok"

    def check_grant(self, client_id):
        return self.grant


def state(tiktok_status="connected_no_data", videos=None):
    return {
        "channels": {"by_channel": {"tiktok": {"status": tiktok_status}}},
        "connections": {"active_platforms": ["tiktok"]},
        "media_folder": {"exists": True, "videos": videos if videos is not None else [
            {"drive_file_id": f, "name": DRIVE[f]["name"], "uploadable": True, "tiktok_uploadable": True}
            for f in (SMALL, MID, HUGE)]},
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
        sys.modules["agents.tiktok_content_agent"] = types.SimpleNamespace(check_grant=self.tiktok.check_grant)
        aa._media_meta.clear()
        self.saved_forget = aa._forget_state
        aa._forget_state = lambda cid: None
        # The durable job and the owner request (their own tests elsewhere)
        self.jobs, self.owner_requests = [], []
        self.saved_jobs = (tiktok_uploads.create, tiktok_uploads.request_owner_approval, tiktok_uploads.nudge,
                           video_limits.tiktok_auto_upload_max_mb)
        tiktok_uploads.create = lambda *a, **k: self.jobs.append((a, k)) or {"id": f"job{len(self.jobs)}"}
        tiktok_uploads.request_owner_approval = lambda *a, **k: self.owner_requests.append((a, k)) or {"id": "o1"}
        tiktok_uploads.nudge = lambda *a, **k: 0
        video_limits.tiktok_auto_upload_max_mb = lambda: 1536

    def tearDown(self):
        (tiktok_uploads.create, tiktok_uploads.request_owner_approval, tiktok_uploads.nudge,
         video_limits.tiktok_auto_upload_max_mb) = self.saved_jobs
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
        self.assertEqual(self.jobs, [])

    def test_stored_params_are_only_the_file(self):
        self.send()
        self.assertEqual(self.rows()[0]["params"], {"drive_file_id": SMALL})

    def test_internal_test_client_27_is_a_normal_client_click(self):
        result = self.send(client_id=27)
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(self.rows()[0]["approver"], "client")
        self.assertEqual(self.alerts, [])

    def test_same_video_twice_shows_the_open_card(self):
        first, second = self.send(), self.send()
        self.assertEqual(first["card"]["id"], second["card"]["id"])
        self.assertEqual(len(self.rows()), 1)

    def test_over_the_old_512_cap_is_an_ordinary_card(self):
        card = self.send(file_id=MID)["card"]
        self.assertEqual(card["action"], "send_tiktok_to_inbox")
        self.assertEqual(self.owner_requests, [])

    def test_above_the_owner_threshold_asks_him_never_the_client(self):
        result = self.send(file_id=HUGE)
        self.assertNotIn("card", result)                           # no card, no question
        self.assertEqual(result["refuse"], tiktok_uploads.ON_IT["he"])
        self.assertIn("אני מטפל בזה", result["refuse"])
        self.assertNotRegex(result["refuse"], r"(?i)tiktok|MB|GB|שגיאה|error")
        self.assertEqual(len(self.owner_requests), 1)
        args, kwargs = self.owner_requests[0]
        self.assertEqual(args[:4], (27, HUGE, "wedding-4k.mp4", 2048 * MB))
        self.assertEqual(kwargs["persona"], "media")
        self.assertEqual(self.rows(), [])

    def test_threshold_is_the_admin_setting(self):
        video_limits.tiktok_auto_upload_max_mb = lambda: 4096
        self.assertEqual(self.send(file_id=HUGE)["card"]["action"], "send_tiktok_to_inbox")
        video_limits.tiktok_auto_upload_max_mb = lambda: 10
        self.assertIn("refuse", self.send(file_id=SMALL))

    def test_not_a_video_not_theirs_or_empty_is_refused(self):
        self.assertEqual(self.send(file_id=IMAGE)["error"], "invalid_params")
        self.assertEqual(self.send(file_id=FOREIGN)["error"], "invalid_params")
        self.assertEqual(self.send(file_id=EMPTY)["error"], "invalid_params")
        self.assertEqual(self.rows(), [])

    def test_resolution_or_length_never_blocks_the_card(self):
        DRIVE[SMALL]["videoMediaMetadata"] = {"width": 100, "height": 100, "durationMillis": str(3600 * 1000)}
        try:
            self.assertEqual(self.send()["card"]["action"], "send_tiktok_to_inbox")
        finally:
            DRIVE[SMALL].pop("videoMediaMetadata")

    def test_not_offered_without_tiktok_in_the_plan(self):
        self.assertEqual(self.send(st=state(tiktok_status="not_in_plan"))["error"], "not_available")

    def test_not_offered_without_a_sendable_video(self):
        only_empty = [{"drive_file_id": EMPTY, "name": "broken.mp4", "tiktok_uploadable": False}]
        self.assertEqual(self.send(st=state(videos=only_empty))["error"], "not_available")

    def test_listed_for_the_chat_model(self):
        names = {a["name"] for a in aa.available(27, "client_chat", state(), client={"billing_exempt": True})}
        self.assertIn("send_tiktok_to_inbox", names)


class ProposeWithoutAWorkingConnection(TikTokActionCase):
    """No connection / dead grant / no video.upload → the reconnect card and a
    plain Hebrew line, never a send card the click would fail on."""

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
        self.check("dead", state(tiktok_status="connected_no_data"))

    def test_not_connected_at_all(self):
        result = self.check("not_connected", state(tiktok_status="not_connected"))
        self.assertIn("לחבר", result["notice"])

    def test_chat_reply_is_the_notice(self):
        with open(os.path.join(ROOT, "agents", "support_agent.py"), encoding="utf-8") as f:
            self.assertIn('"reply": proposal.get("notice") or reply', f.read())

    def test_connect_click_opens_tiktok_consent(self):
        self.tiktok.grant = "missing_upload"
        card = self.send()["card"]
        out = aa.decide(card["id"], "client", 27, "approve")
        self.assertEqual(out["redirect"], "/api/oauth/tiktok/start")


# ─── Decide: the click starts the durable job ───────────────────────────────

class Approve(TikTokActionCase):
    def approve(self, client_id=27, file_id=SMALL):
        card = self.send(client_id=client_id, file_id=file_id)["card"]
        return card, aa.decide(card["id"], "client", client_id, "approve")

    def test_click_creates_one_job_and_the_card_keeps_running(self):
        card, out = self.approve()
        self.assertEqual(out["status"], "running")
        self.assertIn("progress", out)
        self.assertEqual(len(self.jobs), 1)
        args, kwargs = self.jobs[0]
        self.assertEqual(args[:4], (27, SMALL, "promo-reel.mp4", 20 * MB))
        self.assertEqual(kwargs["proposal_id"], card["id"])
        self.assertEqual(kwargs["persona"], "media")
        self.assertEqual(self.rows()[0]["status"], "running")
        self.assertEqual(self.posted_client, [])                  # nothing said until it's done

    def test_click_starts_inside_the_request_even_with_background_tasks(self):
        tasks = types.SimpleNamespace(added=[], add_task=lambda *a, **k: tasks.added.append(a))
        card = self.send()["card"]
        out = aa.decide(card["id"], "client", 27, "approve", background_tasks=tasks)
        self.assertEqual(out["status"], "running")
        self.assertEqual(len(self.jobs), 1)                       # created now, not after the response
        self.assertEqual(tasks.added, [])                         # no background work at all

    def test_double_click_starts_once(self):
        card, _ = self.approve()
        again = aa.decide(card["id"], "client", 27, "approve")
        self.assertTrue(again["already"])
        self.assertEqual(len(self.jobs), 1)

    def test_job_done_posts_the_hebrew_done_line_once(self):
        card, _ = self.approve()
        self.assertTrue(aa.finish_deferred(card["id"], "done", result={"source_file": "promo-reel.mp4"}))
        self.assertEqual(self.rows()[0]["status"], "done")
        text = self.posted_client[-1][2]
        self.assertEqual(self.posted_client[-1][1], "media")
        self.assertIn("promo-reel.mp4", text)
        self.assertIn("פתחו את אפליקציית טיקטוק", text)
        self.assertFalse(aa.finish_deferred(card["id"], "done", result={}))   # never twice
        self.assertEqual(len(self.posted_client), 1)

    def test_grant_lost_during_the_job_offers_reconnect(self):
        card, _ = self.approve()
        aa.finish_deferred(card["id"], "failed", code="needs_reconnect")
        self.assertIn("לחבר מחדש", self.posted_client[-1][2])
        self.assertIn("connect_tiktok", [r["action"] for r in self.rows()])
        self.assertEqual(self.alerts, [])

    def test_too_many_pending_drafts_is_explained(self):
        card, _ = self.approve()
        aa.finish_deferred(card["id"], "failed", code="pending_limit")
        self.assertIn("תיבת הנכנסים", self.posted_client[-1][2])

    def test_our_failure_is_a_calm_line_never_the_error(self):
        card, _ = self.approve()
        aa.finish_deferred(card["id"], "failed", code="in_hand")
        text = self.posted_client[-1][2]
        self.assertIn("ממשיך לטפל", text)
        self.assertNotRegex(text, r"(?i)error|שגיאה|תקלה|500")

    def test_cancel_starts_nothing(self):
        card = self.send()["card"]
        out = aa.decide(card["id"], "client", 27, "cancel")
        self.assertEqual(out["status"], "cancelled")
        self.assertEqual(self.jobs, [])

    def test_another_client_cannot_click_it(self):
        card = self.send(client_id=27)["card"]
        self.assertEqual(aa.decide(card["id"], "client", 40, "approve")["code"], "not_found")

    def test_file_no_longer_a_video_before_the_click(self):
        card = self.send()["card"]
        backup = dict(DRIVE[SMALL])
        try:
            DRIVE[SMALL]["mimeType"] = "image/png"
            aa._media_meta.clear()
            self.assertEqual(aa.decide(card["id"], "client", 27, "approve")["code"], "invalid_params")
        finally:
            DRIVE[SMALL] = backup
        self.assertEqual(self.jobs, [])

    def test_reaper_window_outlives_the_card_job(self):
        spec = aa.ACTIONS["send_tiktok_to_inbox"]
        self.assertGreater(aa.reap_after(spec).total_seconds(), tiktok_uploads.CARD_JOB_MAX_S)


class Registry(unittest.TestCase):
    def test_every_text_in_five_languages(self):
        for name in ("send_tiktok_to_inbox", "connect_tiktok"):
            spec = aa.ACTIONS[name]
            for key in ("title", "what", "done"):
                self.assertEqual(set(spec[key]), set(aa.LANGS), f"{name}.{key}")
        for text in list(aa.ACTIONS["send_tiktok_to_inbox"]["refusals"].values()) + [
                aa._TIKTOK_CONNECT_FIRST, aa._TIKTOK_RECONNECT_FIRST, aa._TIKTOK_BAD_FORMAT,
                tiktok_uploads.ON_IT, tiktok_uploads.PROGRESS, tiktok_uploads.DECLINED,
                tiktok_uploads.STILL_ON_IT]:
            self.assertEqual(set(text), set(aa.LANGS))

    def test_no_size_line_anywhere_for_the_client(self):
        refusals = aa.ACTIONS["send_tiktok_to_inbox"]["refusals"]
        self.assertNotIn("too_large", refusals)
        for text in list(refusals.values()) + [aa._TIKTOK_BAD_FORMAT]:
            self.assertNotRegex(text["he"], r"512|MB|פיקסל|10 דקות")

    def test_never_direct_post(self):
        with open(os.path.join(ROOT, "core", "agent_actions.py"), encoding="utf-8") as f:
            self.assertNotIn("init_direct_post", f.read())

    def test_client_state_marks_every_real_video_sendable(self):
        from core.video_limits import tiktok_sendable
        self.assertTrue(tiktok_sendable(5 * 1024 * MB))
        self.assertFalse(tiktok_sendable(0))
        self.assertFalse(tiktok_sendable(None))
        with open(os.path.join(ROOT, "core", "client_state.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"tiktok_uploadable": tiktok_sendable(', src)
        self.assertNotIn("tiktok_upload_max_mb", src)
        self.assertFalse(hasattr(video_limits, "TIKTOK_INBOX_MAX_MB"))


if __name__ == "__main__":
    unittest.main()
