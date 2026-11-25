"""Pose library scope (2026-11-26).

The admin's characters page is a SEED library. A pose a client asked for is
that client's: stored in their bank, not on the admin page, not in another
client's pending queue (the earlier choice — pending for every client — is
reversed). The system may reuse a banked pose for another client's post; it
is offered to that client then, never pushed. And the admin page counts, per
pose, the clients who approved it — the old "0 מאושרות" read a flag no
client's approval ever set.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import test_agent_characters as tac            # noqa: E402

from core import agent_characters as ac        # noqa: E402

ROOT = os.path.dirname(HERE)
A, B, C = 27, 31, 44


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(tac.Store):
    def request(self, client_id, agent, poses):
        def make(agent, pose):
            return ac.add_pose(agent, f"FILE_{client_id}_{pose[:12]}", True, 0, description=pose,
                               tags=[w for w in pose.lower().split() if len(w) > 3])
        return ac.client_request_poses(client_id, agent, poses, _make=make, _english=lambda t, c=0: t)["made"]

    def ids(self, client_id, agent="maya"):
        return [r["pose_id"] for r in ac.client_library(client_id, agent)]


class TheBankIsTheClients(Base):
    def test_a_requested_pose_is_owned_and_seen_only_by_its_requester(self):
        seed = ac.add_pose("maya", "FILE_SEED", True, description="standing, arms crossed")["pose_id"]
        mine = self.request(A, "maya", ["holding a custom sneaker proudly"])
        self.assertEqual(ac.get_pose(mine[0])["owner"], A)
        self.assertEqual(sorted(self.ids(A)), sorted([seed] + mine))
        self.assertEqual(self.ids(B), [seed])                                   # not in another client's queue
        self.assertEqual(ac.client_counts(B)["maya"]["pending"], 1)
        row = next(r for r in ac.client_library(A, "maya") if r["pose_id"] == mine[0])
        self.assertTrue(row["mine"])
        self.assertEqual([p["pose_id"] for p in ac.bank(A)], mine)
        self.assertEqual(ac.bank(B), [])

    def test_another_client_cannot_decide_on_see_or_use_it(self):
        mine = self.request(A, "maya", ["holding a custom sneaker proudly"])
        self.assertEqual(ac.decide_poses(B, mine, "approved")["changed"], [])
        with self.assertRaises(LookupError):
            ac.client_pose_image(B, mine[0])
        with self.assertRaises(LookupError):
            ac.client_pose_thumb(B, mine[0])
        ac.decide_poses(A, mine, "approved")
        self.assertEqual(len(ac.find_poses("maya", "sneaker", transparent=True, approved_for=A)), 1)
        self.assertEqual(ac.find_poses("maya", "sneaker", transparent=True, approved_for=B), [])

    def test_it_never_appears_on_the_admin_page(self):
        seed = ac.add_pose("maya", "FILE_SEED", True, description="standing, arms crossed")["pose_id"]
        self.request(A, "maya", ["holding a custom sneaker proudly", "pointing at a price tag"])
        row = tac._row(ac.view(), "maya")
        self.assertEqual([p["pose_id"] for p in row["poses"]], [seed])
        self.assertEqual((row["pose_counts"]["total"], row["client_bank"]), (1, 2))
        self.assertEqual(ac.pose_counts()["maya"]["pending"], 1)

    def test_claiming_never_takes_another_clients_pose(self):
        mine = self.request(A, "maya", ["holding a custom sneaker proudly"])
        self.assertEqual(ac.claim_poses(mine, B), [])
        self.assertEqual(ac.get_pose(mine[0])["owner"], A)
        self.assertIn("make_pose(agent, _english(p, client_id), owner=client_id)", _src("core", "agent_characters.py"))


class ReusedNotPushed(Base):
    def test_the_bank_is_searched_by_what_the_pose_shows(self):
        self.request(A, "maya", ["holding a custom sneaker proudly", "typing on a laptop"])
        found = ac.bank_search("maya", "sneaker in her hands")
        self.assertEqual([p["description"] for p in found], ["holding a custom sneaker proudly"])
        self.assertEqual(ac.bank_search("maya", "riding a bicycle"), [])           # searched, never dumped
        self.assertEqual(ac.bank_search("lior", "sneaker"), [])

    def test_a_post_that_needs_a_pose_gets_the_stored_one_offered_and_waits_for_approval(self):
        mine = self.request(A, "maya", ["holding a custom sneaker proudly"])
        out = ac.poses_for([("maya", "holding a sneaker")], client_id=B, stopped=lambda: False)
        self.assertIsInstance(out[0], ac.NeedsApproval)                             # never used unapproved
        self.assertEqual(out[0].pending, 1)
        self.assertEqual(self.ids(B), mine)                                         # offered to B — and only to B
        self.assertEqual(self.ids(C), [])
        self.assertFalse(next(r for r in ac.client_library(B, "maya"))["mine"])
        ac.decide_poses(B, mine, "approved")
        again = ac.poses_for([("maya", "holding a sneaker")], client_id=B, stopped=lambda: False)
        self.assertEqual((again[0]["pose_id"], again[0]["reused"], again[0]["credits"]), (mine[0], True, 0))
        self.assertEqual(ac.get_pose(mine[0])["owner"], A)                          # still the requester's

    def test_nothing_is_offered_without_a_need_or_a_match(self):
        self.request(A, "maya", ["holding a custom sneaker proudly"])
        self.assertEqual(ac.offer_from_bank(B, "maya", "riding a bicycle"), {})
        self.assertEqual(ac.offer_from_bank(A, "maya", "sneaker"), {})              # their own: nothing to offer
        self.assertEqual(self.ids(B), [])


class AdminCountsAreTrue(Base):
    def test_each_pose_shows_how_many_clients_approved_it(self):
        p1 = ac.add_pose("maya", "FILE_1", True, description="waving")["pose_id"]
        p2 = ac.add_pose("maya", "FILE_2", True, description="thinking")["pose_id"]
        p3 = ac.add_pose("maya", "FILE_3", True, description="pointing")["pose_id"]
        ac.decide_poses(A, [p1, p2], "approved")
        ac.decide_poses(B, [p1], "approved")
        ac.decide_poses(C, [p1], "rejected")
        row = tac._row(ac.view(), "maya")
        by = {p["pose_id"]: (p["approved_clients"], p["approved_client_ids"]) for p in row["poses"]}
        self.assertEqual(by, {p1: (2, [A, B]), p2: (1, [A]), p3: (0, [])})
        self.assertEqual((row["pose_counts"]["approved"], row["pose_counts"]["pending"]), (2, 1))   # was "0 מאושרות"
        self.assertEqual(ac.pose_counts()["maya"]["approved"], 2)

    def test_a_pose_a_clients_approved_post_carried_counts_for_that_client(self):
        p = ac.add_pose("maya", "FILE_1", True, description="waving")["pose_id"]
        ac.approve_poses([p], client_id=A, item_id="item123")
        self.assertEqual(ac.approval_counts()[p], [A])
        ac.decide_poses(A, [p], "pending")                                          # their undo
        self.assertNotIn(p, ac.approval_counts())

    def test_the_page_says_it(self):
        html = _src("dashboard", "admin", "characters", "index.html")
        self.assertIn("אושרה אצל ${esc(p.approved_clients)}", html)
        self.assertIn("אף לקוח עוד לא אישר", html)
        self.assertNotIn("} מאושרות · ", html)
        self.assertIn("a.client_bank", html)


if __name__ == "__main__":
    unittest.main()
