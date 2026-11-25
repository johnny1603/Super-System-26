"""The Avatars page, part 4 (2026-11-25, docs/AVATARS.md).

A: the client asks for NEW poses of a uallak agent — made on the real pose
path, arriving pending. B: the business's own characters — one generated
face, stored, and used as the reference of every pose made afterwards; on
the client's own key; per client; pending until the client approves.
"""
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import test_agent_characters as tac            # noqa: E402  (stubs + the in-memory app_settings)

from core import agent_characters as ac        # noqa: E402
from core import business_avatars as ba        # noqa: E402

ROOT = os.path.dirname(HERE)
A, B = 27, 31


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _png(alpha=True) -> bytes:
    from PIL import Image
    img = Image.new("RGBA", (300, 400), (0, 0, 0, 0) if alpha else (200, 200, 200, 255))
    for x in range(100, 200):
        for y in range(50, 350):
            img.putpixel((x, y), (180, 60, 40, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class Base(tac.Store):
    def setUp(self):
        super().setUp()
        self.saved_db, self.saved_alert = ba._db, ba._alert
        ba._db = lambda: self.db
        self.alerts = []
        ba._alert = lambda lines: self.alerts.extend(lines)
        ac._alert = lambda lines: self.alerts.extend(lines)
        self.files, self.calls = {}, []
        ba._THUMBS.clear()

    def tearDown(self):
        ba._db, ba._alert = self.saved_db, self.saved_alert
        super().tearDown()

    def store(self, client_id, name, content, mime="image/png"):
        file_id = f"F_{client_id}_{len(self.files):03d}"
        self.files[file_id] = content
        return file_id

    def face(self, key, prompt, aspect):
        self.calls.append(("face", prompt, aspect))
        return {"content": _png(False), "mime": "image/png", "credits": 1.5}

    def make(self, client_id=A, name="נועה"):
        return ba.create(client_id, name, "אמא צעירה שאוהבת אוכל טוב", "young woman, short dark hair, green jacket",
                         _gen=self.face, _store_fn=self.store, _english=lambda t, c=0: t)

    def deps(self, fail=()):
        def gen(key, prompt, refs, aspect):
            self.calls.append(("pose", prompt, list(refs), aspect))
            if any(word in prompt for word in fail):
                raise RuntimeError("vendor said no")
            return {"url": "https://cdn.example/pose.png", "credits": 2, "aspect_ratio": aspect}
        return {"gen": gen, "english": lambda t, c=0: t, "bytes": lambda f: self.files[f],
                "publish": lambda key, content, mime: f"https://cdn.example/ref-{len(content)}.png",
                "remove": lambda key, url: {"content": _png(True), "credits": 0}, "store": self.store}


class Create(Base):
    def test_one_face_is_generated_and_stored(self):
        made = self.make()
        self.assertTrue(ba.ID_RE.match(made["avatar_id"]))
        self.assertEqual(len([c for c in self.calls if c[0] == "face"]), 1)
        self.assertIn("young woman, short dark hair, green jacket", self.calls[0][1])
        self.assertIn(made["face_file_id"], self.files)
        view = ba.view(A)
        self.assertEqual([(a["name"], a["personality"], a["poses"]) for a in view["avatars"]],
                         [("נועה", "אמא צעירה שאוהבת אוכל טוב", [])])

    def test_empty_at_first_and_refusals_are_codes(self):
        self.assertEqual(ba.view(A)["avatars"], [])
        for name, look, code in (("", "a look here", "name_needed"), ("נועה", "", "look_needed")):
            with self.assertRaises(ba.Refused) as ctx:
                ba.create(A, name, "", look, _gen=self.face, _store_fn=self.store)
            self.assertEqual(ctx.exception.code, code)
        saved = ba._key
        ba._key = lambda cid: ""                                   # no creative tool connected: nothing runs
        self.addCleanup(setattr, ba, "_key", saved)
        with self.assertRaises(ba.Refused) as ctx:
            ba.create(A, "נועה", "", "a look here")
        self.assertEqual(ctx.exception.code, "no_tool")
        self.assertEqual(self.calls, [])

    def test_the_limit_and_per_client(self):
        for n in range(ba.MAX_AVATARS):
            self.make(name=f"דמות {n}")
        with self.assertRaises(ba.Refused) as ctx:
            self.make()
        self.assertEqual(ctx.exception.code, "limit")
        self.assertEqual(ba.view(B)["avatars"], [])                # another client sees none of it
        self.make(client_id=B)
        self.assertEqual(len(ba.view(B)["avatars"]), 1)

    def test_a_failed_generation_alerts_and_stores_nothing(self):
        def boom(key, prompt, aspect):
            raise RuntimeError("vendor down")
        with self.assertRaises(ba.Refused) as ctx:
            ba.create(A, "נועה", "", "a look here", _gen=boom, _store_fn=self.store, _english=lambda t, c=0: t)
        self.assertEqual(ctx.exception.code, "failed")
        self.assertTrue(any("client 27" in line for line in self.alerts))
        self.assertEqual(ba.view(A)["avatars"], [])


class Poses(Base):
    def test_every_pose_uses_the_stored_face_as_its_reference(self):
        made = self.make()
        out = ba.add_poses(A, made["avatar_id"], ["waving hello", "holding a coffee cup"], **self.deps())
        self.assertEqual((len(out["made"]), out["failed"]), (2, 0))
        face_ref = f"https://cdn.example/ref-{len(self.files[made['face_file_id']])}.png"
        pose_calls = [c for c in self.calls if c[0] == "pose"]
        self.assertEqual(len(pose_calls), 2)
        for _kind, prompt, refs, _aspect in pose_calls:
            self.assertEqual(refs, [face_ref])                     # the one face, every time
            self.assertIn("reference image", prompt)
        self.assertEqual(len([c for c in self.calls if c[0] == "face"]), 1)       # the face is never regenerated
        poses = ba.view(A)["avatars"][0]["poses"]
        self.assertEqual({p["status"] for p in poses}, {"pending"})

    def test_the_cap_per_request_and_a_partial_failure(self):
        made = self.make()
        out = ba.add_poses(A, made["avatar_id"], ["a", "b BAD", "c", "d", "e", "f"], **self.deps(fail=("BAD",)))
        self.assertEqual((len(out["made"]), out["failed"]), (ba.MAX_REQUEST_POSES - 1, 1))
        self.assertTrue(self.alerts)
        with self.assertRaises(ba.Refused):
            ba.add_poses(A, made["avatar_id"], ["  "], **self.deps())
        with self.assertRaises(ba.Refused) as ctx:
            ba.add_poses(B, made["avatar_id"], ["waving"], **self.deps())          # not theirs
        self.assertEqual(ctx.exception.code, "not_found")

    def test_approve_reject_and_what_a_post_may_use(self):
        made = self.make()
        ids = ba.add_poses(A, made["avatar_id"], ["one", "two", "three"], **self.deps())["made"]
        self.assertEqual(ba.approved_poses(A, made["avatar_id"]), [])
        ba.decide(A, ids[:2], "approved")
        ba.decide(A, ids[2:], "rejected")
        self.assertEqual(ba.decide(B, ids, "approved")["changed"], [])            # another client changes nothing
        self.assertEqual(ba.view(A)["avatars"][0]["counts"], {"approved": 2, "rejected": 1, "pending": 0})
        self.assertEqual({p["pose_id"] for p in ba.approved_poses(A, made["avatar_id"])}, set(ids[:2]))

    def test_images_are_the_clients_own_and_delete_removes(self):
        made = self.make()
        pose = ba.add_poses(A, made["avatar_id"], ["one"], **self.deps())["made"][0]
        read = lambda f: self.files[f]                              # noqa: E731
        self.assertTrue(ba.thumb(A, avatar_id=made["avatar_id"], _bytes_fn=read).startswith(b"\x89PNG"))
        self.assertTrue(ba.thumb(A, pose_id=pose, _bytes_fn=read).startswith(b"\x89PNG"))
        with self.assertRaises(LookupError):
            ba.thumb(B, pose_id=pose, _bytes_fn=read)
        trashed = []
        saved = ba._trash
        ba._trash = lambda ids: trashed.extend(ids)
        self.addCleanup(setattr, ba, "_trash", saved)
        ba.remove(A, made["avatar_id"])
        self.assertEqual(ba.view(A)["avatars"], [])
        self.assertEqual(len(trashed), 2)


class TeamRequest(Base):
    def test_new_poses_of_an_agent_arrive_pending(self):
        made = []

        def make(agent, pose):
            if "BAD" in pose:
                raise ac.MissingReference(agent)
            record = ac.add_pose(agent, f"FILE_{len(made)}", True, 0, description=pose)
            made.append(pose)
            return record
        out = ac.client_request_poses(A, "maya", ["waving", "BAD one", "typing", "x", "y", "z"], _make=make,
                                      _english=lambda t, c=0: t)
        self.assertEqual((len(out["made"]), out["failed"], out["reason"]), (ac.MAX_REQUEST_POSES - 1, 1, "no_reference"))
        counts = ac.client_counts(A)["maya"]
        self.assertEqual((counts["approved"], counts["pending"]), (0, 3))          # the client still approves
        self.assertTrue(any("client 27" in line for line in self.alerts))
        with self.assertRaises(ValueError):
            ac.client_request_poses(A, "nobody", ["waving"], _make=make)


class Wiring(unittest.TestCase):
    html = _src("dashboard", "client", "index.html")
    api = _src("core", "api_server.py")

    def test_the_two_sections(self):
        self.assertIn('data-i18n="av_team_title">הצוות של וואלק</div>', self.html)
        self.assertIn("av_biz_title: { he:'הדמויות של העסק'", self.html)
        self.assertIn("function renderBusinessAvatars()", self.html)
        self.assertIn("esc(t('av_biz_empty'))", self.html)                      # empty at first
        for field in ("data-bz-name", "data-bz-personality", "data-bz-look"):
            self.assertIn(field, self.html)

    def test_writes_are_the_clients_own_session(self):
        for route in ('@app.post("/api/client/avatars/team/{agent}/poses")', '@app.post("/api/client/avatars/business")',
                      '@app.post("/api/client/avatars/business/{avatar_id}/poses")',
                      '@app.post("/api/client/avatars/business/poses/decide")',
                      '@app.post("/api/client/avatars/business/{avatar_id}/delete")'):
            a = self.api.index(route)
            body = self.api[a:self.api.index("\n@app.", a + 10)]
            self.assertIn("client_id = _require_session(request)\n    _avatars_write(request)", body.replace("\r\n", "\n"), route)

    def test_the_clients_rows_leave_with_the_client(self):
        for path in (("core", "client_deletion.py"), ("core", "deletion_guard.py"), ("core", "admin_service.py")):
            src = _src(*path)
            self.assertIn("pose_approvals:", src, path)
            self.assertIn("business_avatars:", src, path)

    def test_the_admin_page_is_untouched_and_no_vendor_name_reaches_the_client(self):
        keys = [line for line in self.html.splitlines() if line.lstrip().startswith(("av_", "avatars_", "nav_avatars"))]
        self.assertGreater(len(keys), 60)
        for line in keys:
            self.assertNotIn("higgsfield", line.lower())
            for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
                self.assertIn(lang, line)


if __name__ == "__main__":
    unittest.main()
