"""Pose approval is the CLIENT's, per client (2026-11-25, docs/AVATARS.md).

The live blocker: 15 valid poses of Maya were waiting, nothing could approve
them, and the posts came out without her. Now: the client approves one pose
or many on the Avatars page; a post uses ONLY a pose that client approved;
none approved = an alert + a visible state on the card (never a silent
fallback, never an unapproved pose, never a pose generated behind the
client's back); one client's decision never touches another's; and a design
that was waiting for a pose gets it the moment one is approved.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import test_agent_characters as tac            # noqa: E402  (stubs + the in-memory app_settings)

from core import agent_characters as ac        # noqa: E402
from core import slide_design as sd            # noqa: E402
from core import social_queue as sq            # noqa: E402

ROOT = os.path.dirname(HERE)
A, B = 27, 31


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(tac.Store):
    def setUp(self):
        super().setUp()
        self.alerts = []
        ac._alert = lambda lines: self.alerts.extend(lines)
        self.generated = []
        saved = ac.make_pose
        ac.make_pose = lambda *a, **k: self.generated.append(a) or (_ for _ in ()).throw(AssertionError("generated"))
        self.addCleanup(setattr, ac, "make_pose", saved)
        self.poses = [ac.add_pose("maya", f"FILE_MAYA_{n:02d}", True, 0, description=f"pose {n}")["pose_id"]
                      for n in range(15)]


class Decide(Base):
    def test_fifteen_wait_until_the_client_approves(self):
        counts = ac.client_counts(A)["maya"]
        self.assertEqual((counts["approved"], counts["pending"]), (0, 15))
        self.assertEqual(ac.find_poses("maya", "wave", transparent=True, approved_for=A), [])

    def test_one_then_many_then_undo(self):
        out = ac.decide_poses(A, self.poses[:1], "approved")
        self.assertEqual((out["changed"], out["counts"]["maya"]["approved"]), (self.poses[:1], 1))
        out = ac.decide_poses(A, self.poses[1:10], "approved")                # a bulk selection
        self.assertEqual(out["counts"]["maya"], {"approved": 10, "pending": 5, "rejected": 0})
        out = ac.decide_poses(A, self.poses[10:12], "rejected")
        self.assertEqual(out["counts"]["maya"], {"approved": 10, "pending": 3, "rejected": 2})
        out = ac.decide_poses(A, self.poses[:2] + self.poses[10:11], "pending")
        self.assertEqual(out["counts"]["maya"], {"approved": 8, "pending": 6, "rejected": 1})
        with self.assertRaises(ValueError):
            ac.decide_poses(A, self.poses[:1], "maybe")

    def test_one_clients_decision_never_touches_another(self):
        ac.decide_poses(A, self.poses, "approved")
        self.assertEqual(ac.client_counts(B)["maya"], {"approved": 0, "pending": 15, "rejected": 0})
        self.assertEqual(ac.find_poses("maya", "", transparent=True, approved_for=B), [])
        ac.decide_poses(B, self.poses[:3], "rejected")
        self.assertEqual(ac.client_counts(A)["maya"]["approved"], 15)
        keys = {k for k in self.db.rows if k.startswith(ac.APPROVALS_PREFIX)} if hasattr(self.db, "rows") else None
        if keys is not None:
            self.assertEqual(keys, {f"{ac.APPROVALS_PREFIX}{A}", f"{ac.APPROVALS_PREFIX}{B}"})

    def test_a_client_cannot_decide_a_pose_they_cannot_see(self):
        other = ac.add_pose("maya", "FILE_SCENE_B", False, B, description="B's own scene")["pose_id"]
        out = ac.decide_poses(A, [other, "ffffffffffff", self.poses[0]], "approved")
        self.assertEqual(out["changed"], [self.poses[0]])
        self.assertNotIn(other, [r["pose_id"] for r in ac.client_library(A)])
        with self.assertRaises(LookupError):
            ac.client_pose_thumb(A, other)

    def test_the_admins_keep_is_seeding_not_a_clients_approval(self):
        ac.approve_poses(self.poses[:4])                                       # the library's own flag (admin Keep)
        self.assertEqual(ac.client_counts(A)["maya"]["approved"], 0)
        self.assertEqual(len(ac.find_poses("maya", "", transparent=True, limit=9)), 4)     # the admin workspace still sees them

    def test_a_pose_their_own_approved_post_carried_counts_for_them_only(self):
        ac.approve_poses(self.poses[:1], client_id=A, item_id="item1")
        self.assertEqual(ac.client_counts(A)["maya"]["approved"], 1)
        self.assertEqual(ac.client_counts(B)["maya"]["approved"], 0)


class Production(Base):
    def entries(self):
        return [{"asset_id": "a1", "role": "hook", "n": 1, "characters": ["maya"], "character_mode": "composite"},
                {"asset_id": "a2", "role": "point", "n": 2}]

    def test_none_approved_is_loud_and_nothing_is_generated(self):
        entries, missing = sd.attach_characters(self.entries(), "he", stopped=lambda: False, client_id=A)
        self.assertEqual(self.generated, [])                                   # no pose made behind the client's back
        self.assertNotIn("character", entries[0])                              # and no unapproved pose used
        self.assertEqual([m["reason"] for m in missing], ["needs_approval"])
        self.assertIn("אווטרים", missing[0]["message"])
        self.assertEqual(entries[0]["wants_character"]["agent"], "maya")
        self.assertEqual(sd.missing_flags(missing), ["character_needs_approval"])
        self.assertEqual(sd.missing_flags([{"reason": "no_reference"}]), ["character_unavailable"])
        for lang in ("he", "en", "fr", "ar", "ru"):
            self.assertTrue(sq.pick(sq.FLAG_TEXT["character_needs_approval"], lang))
            self.assertTrue(ac.FAILURE_TEXT["needs_approval"][lang])

    def test_an_approved_pose_is_used_even_as_a_repeat(self):
        ac.decide_poses(A, self.poses[:1], "approved")
        for _ in range(2):                                                     # the only approved pose, twice in a row
            entries, missing = sd.attach_characters(self.entries(), "he", stopped=lambda: False, client_id=A)
            self.assertEqual((entries[0]["character"], missing), ({"agent": "maya", "pose_id": self.poses[0]}, []))
        self.assertEqual(self.generated, [])
        # … and client B, who approved nothing, still gets none
        _, missing = sd.attach_characters(self.entries(), "he", stopped=lambda: False, client_id=B)
        self.assertEqual(missing[0]["reason"], "needs_approval")

    def test_the_card_carries_the_state_and_the_alert_is_raised(self):
        src = _src("core", "slide_design.py")
        self.assertIn("entries, missing = attach_characters(text_slides, lang, client_id=client_id)", src)
        self.assertIn("flags += missing_flags(missing)", src)
        self.assertIn('_alert([f"client {client_id}: post {post_item.get(\'id\')} — agent character not drawn on "', src)
        self.assertIn("problems += [f\"• {m}\" for m in drawn_without]", _src("core", "post_pipeline.py"))

    def test_approval_draws_the_pose_into_the_waiting_design(self):
        entries, missing = sd.attach_characters(self.entries(), "he", stopped=lambda: False, client_id=A)
        design = {"post_id": "p1", "template_name": "t", "spec": {}, "rev": 1, "slides": sd._slides_of(entries), "state": "ready"}
        item = sq.add_design(A, design, title="post", flags=sd.missing_flags(missing))
        self.assertEqual(item["flags"], ["character_needs_approval"])
        self.assertEqual(sd.apply_approved_poses(A), [])                       # nothing approved yet: nothing changes
        ac.decide_poses(A, self.poses[:2], "approved")
        self.assertEqual(sd.apply_approved_poses(A), [item["id"]])
        after = sq.get(A, item["id"])
        self.assertEqual(after["design"]["slides"][0]["character"]["agent"], "maya")
        self.assertIn(after["design"]["slides"][0]["character"]["pose_id"], self.poses[:2])
        self.assertNotIn("wants_character", after["design"]["slides"][0])
        self.assertEqual((after["design"]["rev"], after.get("flags")), (2, []))
        self.assertEqual(sd.apply_approved_poses(A), [])                       # once


class Page(unittest.TestCase):
    html = _src("dashboard", "client", "index.html")
    api = _src("core", "api_server.py")

    def test_the_nav_item_and_the_view(self):
        self.assertIn('data-view="avatars" onclick="showView(\'avatars\')"', self.html)
        self.assertIn('<span data-i18n="nav_avatars">אווטרים</span>', self.html)
        self.assertIn("'social', 'avatars', 'personal'", self.html)
        self.assertIn("if (name === 'avatars') loadAvatars();", self.html)
        self.assertIn('<div class="av-h" data-i18n="av_team_title">הצוות של וואלק</div>', self.html)

    def test_single_bulk_and_drag_selection(self):
        h = self.html
        self.assertIn('data-av-one="approved"', h)                             # one pose
        self.assertIn('data-av-all="${esc(a.agent)}"', h)                      # every waiting pose of an agent
        self.assertIn('data-av-bulk="approved"', h)                            # the selection
        self.assertIn("avToggle(cb.getAttribute('data-av-sel'), cb.checked, ev.shiftKey)", h)      # Shift = a range
        for fn in ("function avDragStart(ev)", "function avDragMove(ev)", "function avDragEnd(ev)"):
            self.assertIn(fn, h)
        # the media library's rules: mouse only, Ctrl / Cmd adds, the same threshold, edge scroll and rectangle
        self.assertIn("if (ev.button !== 0 || ADMIN_VIEW || libTouchOnly()) return;", h)
        self.assertIn("add: Boolean(ev.ctrlKey || ev.metaKey)", h)
        self.assertIn("if (Math.hypot(dx, dy) < LIB_DRAG_MIN) return;", h)
        self.assertIn("avDrag.box.className = 'lib-marquee';", h)

    def test_the_routes_are_the_clients_own(self):
        a = self.api.index('@app.post("/api/client/avatars/poses/decide")')
        route = self.api[a:self.api.index("\n@app.", a + 10)]
        self.assertIn("client_id = _require_session(request)", route)
        self.assertIn("if _admin_view_client(request) is not None:", route)    # staff view never decides for a client
        self.assertIn("agent_characters.decide_poses(client_id,", route)
        self.assertIn("slide_design.apply_approved_poses(client_id)", route)
        from core import admin_view, chat_parity
        self.assertIn("client_avatars", admin_view.READ_ENDPOINTS)
        self.assertNotIn("client_avatar_poses_decide", admin_view.READ_ENDPOINTS)
        self.assertIn("POST /api/client/avatars/poses/decide", chat_parity.PARITY)

    def test_the_social_card_leads_to_the_page(self):
        self.assertIn("f.code === 'character_needs_approval')", self.html)
        self.assertIn("onclick=\"showView('avatars')\">${esc(uallakI18n.t('soc_go_avatars'))}", self.html)


if __name__ == "__main__":
    unittest.main()
