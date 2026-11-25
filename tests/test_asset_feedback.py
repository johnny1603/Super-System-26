"""Asset usage + the owner's rejections fed back into selection (2026-11-24,
core/asset_feedback, core/drive_assets.find_assets, agents/media_agent,
core/social_queue.reject, the Social tab's rejection form).

What must stay true:
- every use of a real asset increments its count and its last-used time;
- find_assets ranks a recently / often used asset lower — never excludes
  it, and a clearly stronger match still wins;
- a rejection the owner marks as "the picture" is recorded against that
  asset with the optional short reason (one click or one line);
- a rejected asset ranks lower for that client;
- the accumulated reasons (and what the rejected pictures showed) reach the
  agent's choice step;
- an asset with no history is ranked exactly as before.

Run from the repo root:  python tests/test_asset_feedback.py -v
"""
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_drive_folders as tdf      # noqa: E402  (in-memory Drive / model / index)
import test_social_queue as tsq       # noqa: E402  (in-memory app_settings)

from core import asset_feedback as af, social_queue as sq   # noqa: E402

da = tdf.da
CID = tdf.CID
ROOT = tdf.ROOT


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class WithStore(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.db = tsq.FakeDB()
        old = af._db
        af._db = lambda: self.db
        self.addCleanup(setattr, af, "_db", old)


class Usage(WithStore):
    def test_counts_and_last_used(self):
        self.assertEqual(af.load(CID)["usage"], {})
        af.record_use(CID, "F1")
        first = af.load(CID)["usage"]["F1"]
        self.assertEqual(first["count"], 1)
        self.assertTrue(first["last_used_at"])
        af.record_use(CID, "F1")
        af.record_use(CID, "F2")
        u = af.load(CID)["usage"]
        self.assertEqual((u["F1"]["count"], u["F2"]["count"]), (2, 1))

    def test_penalty_recency_and_frequency_bounded(self):
        now = datetime.now(timezone.utc)
        iso = lambda days: (now - timedelta(days=days)).isoformat()
        data = {"usage": {"new": {"count": 1, "last_used_at": iso(0)}, "old": {"count": 1, "last_used_at": iso(60)},
                          "often": {"count": 9, "last_used_at": iso(0)}}, "rejections": {}, "reasons": []}
        self.assertEqual(af.penalty(data, "never", now), 0.0)
        self.assertGreater(af.penalty(data, "new", now), af.penalty(data, "old", now))
        self.assertGreater(af.penalty(data, "often", now), af.penalty(data, "new", now))
        self.assertLessEqual(af.penalty(data, "often", now), af.USE_PENALTY_MAX)
        # a full match (3.0) used today still beats a weak match (1.0) never used
        self.assertGreater(3.0 - af.penalty(data, "often", now), 1.0)

    def test_media_agent_records_each_use(self):
        src = _read("agents", "media_agent.py")
        body = src[src.index("def _use_real_asset"):src.index("def _craft_brief_prompt")]
        self.assertIn("asset_feedback.record_use(client_id, chosen[\"file_id\"])", body)
        self.assertLess(body.index("verify_usable"), body.index("record_use"))   # only a file actually used


class Ranking(tdf.Base):
    def setUp(self):
        super().setUp()
        self.db = tsq.FakeDB()
        old = af._db
        af._db = lambda: self.db
        self.addCleanup(setattr, af, "_db", old)
        self.drive.add("FOLDER_RANK_0001", "מוצרים")
        for n in range(4):
            tdf.media(self.drive, f"RANK_{n}_abcdef", f"IMG_{n}.jpg", "FOLDER_RANK_0001",
                      modified=f"2026-10-{n + 1:02d}T00:00:00Z")
        self.connect("FOLDER_RANK_0001", "product_catalog")
        self.run_all()

    def ids(self, **kw):
        return [r["file_id"] for r in da.find_assets(CID, ["product_catalog"], "shoe", "image", limit=10, **kw)]

    def test_no_history_ranks_as_before(self):
        self.assertEqual(self.ids(), [f"RANK_{n}_abcdef" for n in (3, 2, 1, 0)])    # newest first, equal match

    def test_recently_used_ranks_lower_but_stays(self):
        af.record_use(CID, "RANK_3_abcdef")
        ids = self.ids()
        self.assertEqual(ids[-1], "RANK_3_abcdef")            # lower …
        self.assertEqual(len(ids), 4)                         # … never excluded
        row = next(r for r in da.find_assets(CID, ["product_catalog"], "shoe", "image") if r["file_id"] == "RANK_3_abcdef")
        self.assertEqual(row["times_used"], 1)

    def test_rejected_ranks_lower(self):
        af.record_rejection(CID, "RANK_2_abcdef", "איכות התמונה נמוכה")
        ids = self.ids()
        self.assertEqual(ids[-1], "RANK_2_abcdef")
        self.assertEqual(len(ids), 4)
        row = next(r for r in da.find_assets(CID, ["product_catalog"], "shoe", "image") if r["file_id"] == "RANK_2_abcdef")
        self.assertEqual((row["times_rejected"], row["rejection_reasons"]), (1, ["איכות התמונה נמוכה"]))


class Rejection(WithStore):
    def setUp(self):
        super().setUp()
        self.qdb = tsq.FakeDB()
        saved = [(sq, "_db", sq._db), (sq, "_modes", sq._modes), (sq, "send_back", sq.send_back)]
        for mod, name, value in saved:
            self.addCleanup(setattr, mod, name, value)
        sq._db = lambda: self.qdb
        sq._modes = lambda cid: {"instagram": "client_publishes", "facebook": "not_connected",
                                 "tiktok": "client_publishes", "youtube": "not_connected"}
        sq.send_back = lambda cid, it, lang="he": {"posted": True, "card": True}

    def add(self, real=True):
        return sq.add(CID, "fileREALREAL0001", "image", ["instagram"],
                      real_asset={"source_file_id": "SRC_FILE_0001", "folder": "מוצרים", "role": "product_catalog",
                                  "description": "נעל אדומה על רקע לבן"} if real else None)

    def test_recorded_with_one_click_reason(self):
        old = da._load
        da._load = lambda cid: {"items": {"SRC_FILE_0001": {"description": "נעל אדומה על רקע לבן"}}}
        self.addCleanup(setattr, da, "_load", old)
        item = self.add()
        out = sq.reject(CID, item["id"], "", asset_rejected=True, asset_reason="כבר השתמשנו בתמונה הזו")
        self.assertEqual(out["reject_reason"], "כבר השתמשנו בתמונה הזו")     # the picture reason is the reason
        data = af.load(CID)
        rej = data["rejections"]["SRC_FILE_0001"]
        self.assertEqual((len(rej), rej[0]["reason"], rej[0]["item_id"]), (1, "כבר השתמשנו בתמונה הזו", item["id"]))
        self.assertEqual(data["reasons"][0]["asset"], "נעל אדומה על רקע לבן")

    def test_reason_optional_and_not_recorded_unless_marked(self):
        item = self.add()
        sq.reject(CID, item["id"], "הכיתוב ארוך מדי")                       # the caption, not the picture
        self.assertEqual(af.load(CID)["rejections"], {})
        item2 = sq.add(CID, "fileREALREAL0002", "image", ["instagram"], real_asset={"source_file_id": "SRC_FILE_0002"})
        sq.reject(CID, item2["id"], "לא זה", asset_rejected=True)             # marked, no picture reason
        self.assertEqual(af.load(CID)["rejections"]["SRC_FILE_0002"][0]["reason"], "")
        with self.assertRaises(sq.QueueError):
            sq.reject(CID, self.add()["id"], "", asset_rejected=True)        # nothing at all → still a reason needed

    def test_form_and_route(self):
        html = _read("dashboard", "client", "index.html")
        self.assertIn("const assetRej = (cur.real_asset || {}).source_file_id ?", html)
        self.assertIn("['soc_asset_r1', 'soc_asset_r2', 'soc_asset_r3', 'soc_asset_r4']", html)
        self.assertIn('<input type="text" data-soc-asset-reason maxlength="160"', html)
        self.assertIn("{ reason, asset_rejected: true, asset_reason: assetReason }", html)
        api = _read("core", "api_server.py")
        self.assertIn("asset_rejected=req.asset_rejected, asset_reason=req.asset_reason", api)


class ReachesTheChoice(WithStore):
    def test_reasons_and_history_in_the_chooser_payload(self):
        from agents import media_agent
        af.record_rejection(CID, "OLD_FILE", "התמונה לא מתאימה לנושא", asset="מגש עוגיות")
        af.record_use(CID, "CAND_1")
        seen = []
        old = media_agent.safe_claude_json_call
        media_agent.safe_claude_json_call = lambda system, user, **kw: seen.append((system, json.loads(user))) or {
            "file_id": "CAND_2", "reason": "fits"}
        self.addCleanup(setattr, media_agent, "safe_claude_json_call", old)
        brief = types.SimpleNamespace(role="product", post_format="single", visual_direction="red shoe",
                                      must_include=[], must_avoid=[], asset_type="real_photo")
        chosen = media_agent.choose_real_asset(CID, brief, [{"file_id": "CAND_1", "score": 1}, {"file_id": "CAND_2", "score": 1}])
        self.assertEqual(chosen["file_id"], "CAND_2")
        system, payload = seen[0]
        self.assertEqual(payload["owner_rejections"][0]["reason"], "התמונה לא מתאימה לנושא")
        self.assertEqual(payload["owner_rejections"][0]["asset"], "מגש עוגיות")
        c1 = next(c for c in payload["candidates"] if c["file_id"] == "CAND_1")
        self.assertEqual(c1["times_used"], 1)
        self.assertIn("owner_rejections", system)
        self.assertIn("prefer one\nnot used recently", system)


class Cleanup(unittest.TestCase):
    def test_cleared_with_the_client(self):
        self.assertIn('f"asset_feedback:{int(i)}"', _read("core", "client_deletion.py"))
        self.assertIn('"asset_feedback:"', _read("core", "deletion_guard.py"))
        self.assertIn('"asset_feedback:"', _read("core", "admin_service.py"))


if __name__ == "__main__":
    unittest.main()
