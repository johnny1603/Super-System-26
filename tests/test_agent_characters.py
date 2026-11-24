"""The agents as their own characters (2026-11-24, core/agent_characters,
docs/AGENT-CHARACTERS.md).

What must stay true:
- each agent's REFERENCE IMAGE (url + Higgsfield media id + notes) is set,
  replaced and cleared by the admin, STORED in app_settings — no element
  list, no matching, and no code path calls an element-list endpoint;
- the brief carries `characters` + `character_mode` through validation, the
  post item and production; default mode per asset type;
- in scene: the stored images go in the model's documented `image_urls`, in
  order, one per character; the style clause always; the copy never in the
  prompt; a missing reference = "לא מוגדרת תמונת ייחוס עבור <agent>" in the
  manager's chat, never a silent success;
- composite: background removal must yield a transparent PNG (verified);
  the renderer composites the cut-out UNDER the text with position, scale,
  flip and shadow; overflow and text collision are WARNINGS, never a crop;
- the pose library: approved poses saved with tags, offered before anything
  is generated, the last pose never repeated next;
- THE guardrail: an agent never creates or rebuilds an element.

Run from the repo root:  python tests/test_agent_characters.py -v
"""
import copy
import io
import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_post_brief as tpb                  # noqa: E402  (stubs + the pipeline harness)
import test_social_queue as tsq                # noqa: E402  (FakeDB = in-memory app_settings)

from core import agent_characters as ac        # noqa: E402
from core import media_gen_service as gen      # noqa: E402
from core import post_brief as pb              # noqa: E402
from core import post_pipeline as pp           # noqa: E402
from core import slide_renderer as sr          # noqa: E402
from core import slide_spec                    # noqa: E402
from core import social_queue as sq            # noqa: E402

try:
    from PIL import Image
except ImportError:
    Image = None

RAQM = sr.raqm_status()["available"]
NEEDS_PIL = unittest.skipUnless(Image is not None or os.environ.get("CI"), "Pillow not installed here")
NEEDS_RAQM = unittest.skipUnless(RAQM or os.environ.get("CI"), "Pillow + raqm not installed here")

ROLES = {"bg": "#F0F0F0", "fg": "#101010", "accent": "#FF4C1F", "muted": "#666666"}
POSE = "abcdef123456"
REF = {a: f"https://cdn.higgsfield.ai/refs/{a}.png" for a in ac.AGENTS}


def _png_size(w, h, colour=(90, 140, 200)) -> bytes:
    if Image is None:
        return b"\x89PNG fake"
    out = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(out, "PNG")
    return out.getvalue()


def _png(colour=(200, 120, 40)) -> bytes:
    if Image is None:
        return b"\x89PNG fake"
    out = io.BytesIO()
    Image.new("RGB", (24, 32), colour).save(out, "PNG")
    return out.getvalue()


class Store(unittest.TestCase):
    """agent_characters + social_queue on ONE in-memory app_settings."""

    def setUp(self):
        self.db = tsq.FakeDB()
        self.saved = [(ac, "_db", ac._db), (ac, "_alert", ac._alert), (sq, "_db", sq._db),
                      (ac, "image_info", ac.image_info)]
        ac._db = lambda: self.db
        sq._db = lambda: self.db
        self.alerts = []
        ac._alert = lambda lines: self.alerts.extend(lines)
        if Image is None:                                     # no Pillow here: the image check is stubbed
            ac.image_info = lambda content: {"mime": "image/png", "width": 24, "height": 32}

    def tearDown(self):
        for mod, name, value in self.saved:
            setattr(mod, name, value)

    def set_ref(self, agent, url=None, **kw):
        kw.setdefault("_fetch_fn", lambda u: _png())
        kw.setdefault("_keep_fn", lambda a, c, m: f"KEEP_{a}_0123456789")
        return ac.set_reference(agent, url=url or REF[agent], **kw)

    def all_refs(self):
        for a in ac.AGENTS:
            self.set_ref(a, media_id=f"job-{a}")
        return ac.references()


# ─── 1. The reference SET (the admin workspace) ──────────────────────────────

def _row(view, agent):
    return next(r for r in view["agents"] if r["agent"] == agent)


class References(Store):
    def test_several_images_added_shown_removed_one_by_one(self):
        self.set_ref("lior", media_id="3f1c9a2e-job")
        self.set_ref("lior", url="https://cdn.higgsfield.ai/refs/lior-side.png", media_id="job-2")
        view = self.set_ref("lior", url="https://cdn.higgsfield.ai/refs/lior-back.png")
        row = _row(view, "lior")
        self.assertEqual([i["url"] for i in row["images"]],
                         [REF["lior"], "https://cdn.higgsfield.ai/refs/lior-side.png",
                          "https://cdn.higgsfield.ai/refs/lior-back.png"])
        self.assertEqual((row["set"], row["sent"], row["images"][0]["media_id"], row["images"][0]["durable"]),
                         (True, 3, "3f1c9a2e-job", True))
        self.assertEqual(self.db.tables["app_settings"][0]["key"], "agent_references")
        with self.assertRaises(ValueError):
            self.set_ref("lior")                                          # the same image twice
        # remove ONE
        view = ac.remove_reference("lior", row["images"][1]["ref_id"])
        self.assertEqual([i["url"] for i in _row(view, "lior")["images"]],
                         [REF["lior"], "https://cdn.higgsfield.ai/refs/lior-back.png"])
        with self.assertRaises(ValueError):
            ac.remove_reference("lior", "r0000000000")
        # clear the whole set
        view = ac.clear_reference("lior")
        self.assertFalse(_row(view, "lior")["set"])
        with self.assertRaises(ac.MissingReference):
            ac.reference_for("lior")
        self.assertEqual([h["action"] for h in ac.references()["history"]], ["add", "add", "add", "remove", "clear"])

    def test_at_most_fourteen_per_agent(self):
        for n in range(ac.MAX_REF_IMAGES):
            self.set_ref("maya", url=f"https://cdn.higgsfield.ai/refs/maya-{n}.png")
        with self.assertRaises(ValueError):
            self.set_ref("maya", url="https://cdn.higgsfield.ai/refs/maya-extra.png")

    def test_the_whole_set_is_sent_within_each_models_max(self):
        for n in range(12):
            self.set_ref("maya", url=f"https://cdn.higgsfield.ai/refs/maya-{n}.png")
        self.set_ref("lior")
        self.set_ref("uri")
        grok = ac.reference_urls(["maya"], max_total=10)
        self.assertEqual(grok, [f"https://cdn.higgsfield.ai/refs/maya-{n}.png" for n in range(10)])
        self.assertEqual(len(ac.reference_urls(["maya"], max_total=3)), 3)                  # Qwen
        self.assertEqual(len(ac.reference_urls(["maya"], max_total=14)), 12)                # all of them
        # several characters share the max, in order, at least one each
        shared = ac.reference_plan(["maya", "lior", "uri"], max_total=10)
        self.assertEqual([len(g) for g in shared], [3, 1, 1])
        self.assertEqual([len(g) for g in ac.reference_plan(["maya", "lior", "uri"], max_total=3)], [1, 1, 1])
        # the default max is the configured model's (Grok: 10)
        self.assertEqual(len(ac.reference_urls(["maya"])), gen.max_references())
        # the prompt names who is who by those images
        saved = gen.max_references
        gen.max_references = lambda path="": 10
        try:
            p = ac.in_scene_prompt("a market stall", ["maya", "lior", "uri"])
        finally:
            gen.max_references = saved
        self.assertIn("the character from reference images 1-3", p)
        self.assertIn("the character from reference image 4", p)
        self.assertIn("the character from reference image 5", p)
        self.assertIn("the character shown in the reference images", ac.pose_prompt("maya", "waving"))

    def test_an_old_single_image_record_reads_as_a_set_of_one(self):
        ac._write(ac.REFS_KEY, {"agents": {"yoav": {"media_id": "job-y", "url": REF["yoav"], "notes": "grey hoodie",
                                                    "file_id": "KEEP_y", "url_at": "2026-11-24T10:00:00+00:00"}}})
        entry = ac.reference_for("yoav")
        self.assertEqual(([i["url"] for i in entry["images"]], entry["notes"]), ([REF["yoav"]], "grey hoodie"))
        self.assertTrue(ac.REF_ID_RE.match(entry["images"][0]["ref_id"]))
        self.assertEqual(ac.reference_for("yoav")["images"][0]["ref_id"], entry["images"][0]["ref_id"])   # stable

    def test_upload_goes_through_the_documented_upload(self):
        published = []
        view = ac.add_reference("maya", content=_png(), by="admin",
                                _publish_fn=lambda c, m: published.append(m) or "https://cdn.higgsfield.ai/up/maya.png",
                                _keep_fn=lambda a, c, m: "KEEP_maya_0123456789")
        self.assertEqual(published, ["image/png"])
        img = ac.reference_for("maya")["images"][0]
        self.assertEqual((img["url"], img["source"]), ("https://cdn.higgsfield.ai/up/maya.png", "upload"))
        self.assertTrue(_row(view, "maya")["set"])
        calls = []

        class Resp:
            def __init__(self, code, data=None):
                self.status_code, self._d, self.text = code, data or {}, ""

            def json(self):
                return self._d
        fake = types.SimpleNamespace(
            post=lambda url, headers=None, json=None, timeout=None: calls.append(("POST", url, headers, json)) or Resp(
                200, {"upload_url": "https://s3.example/put?sig=1", "public_url": "https://cdn.higgsfield.ai/up/x.png",
                      "upload_headers": {"Content-Type": "image/png", "x-amz-tagging": "retention=temporary"}}),
            put=lambda url, headers=None, content=None, timeout=None: calls.append(("PUT", url, headers, None)) or Resp(200))
        saved = gen.httpx
        gen.httpx = fake
        try:
            self.assertEqual(gen.upload_file("id:secret", b"PNG", "image/png"), "https://cdn.higgsfield.ai/up/x.png")
        finally:
            gen.httpx = saved
        self.assertEqual((calls[0][0], calls[0][1], calls[0][3]),
                         ("POST", "https://api.higgsfield.ai/files/generate-upload-url", {"content_type": "image/png"}))
        self.assertEqual((calls[1][0], calls[1][1]), ("PUT", "https://s3.example/put?sig=1"))
        self.assertNotIn("Authorization", calls[1][2])
        self.assertEqual(calls[1][2]["x-amz-tagging"], "retention=temporary")

    def test_refusals(self):
        for bad in ("http://cdn.example/x.png", "not a url", "", "javascript:alert(1)"):
            with self.assertRaises(ValueError, msg=bad):
                ac.add_reference("maya", url=bad, _fetch_fn=lambda u: _png(), _keep_fn=lambda *a: "K")
        with self.assertRaises(ValueError):
            self.set_ref("bob", url=REF["maya"])
        with self.assertRaises(ValueError):
            self.set_ref("maya", media_id="bad id with spaces")

        def unreachable(u):
            raise ValueError("the URL answered 404")
        with self.assertRaises(ValueError) as e:
            self.set_ref("maya", _fetch_fn=unreachable)
        self.assertIn("could not be read", str(e.exception))
        self.assertEqual(ac.references()["agents"], {})                # nothing half-saved

    def test_no_durable_copy_still_saves_and_says_so(self):
        def no_drive(a, c, m):
            raise RuntimeError("Missing key: DRIVE_MEDIA_FOLDER_ID")
        view = self.set_ref("yoav", _keep_fn=no_drive)
        self.assertIn("no durable copy", view["warning"])
        self.assertFalse(_row(view, "yoav")["images"][0]["durable"])
        self.assertEqual(ac.reference_for("yoav")["images"][0]["url"], REF["yoav"])

    def test_notes(self):
        self.set_ref("elinor")
        view = ac.set_notes("elinor", "  green   blazer, half body ")
        self.assertEqual(_row(view, "elinor")["notes"], "green blazer, half body")

    def test_an_old_url_is_re_uploaded_from_our_copy(self):
        self.set_ref("uri")
        data = ac.references()
        data["agents"]["uri"]["images"][0]["url_at"] = "2026-01-01T00:00:00+00:00"
        ac._write(ac.REFS_KEY, data)
        refreshed = []
        urls = ac.reference_urls(["uri"], _refresh=lambda i: refreshed.append(i["file_id"]) or "https://cdn.higgsfield.ai/up/uri-2.png")
        self.assertEqual((urls, refreshed), (["https://cdn.higgsfield.ai/up/uri-2.png"], ["KEEP_uri_0123456789"]))
        self.assertEqual(ac.reference_for("uri")["images"][0]["url"], "https://cdn.higgsfield.ai/up/uri-2.png")
        self.assertEqual(ac.reference_urls(["uri"], _refresh=lambda i: self.fail("refreshed a fresh URL")),
                         ["https://cdn.higgsfield.ai/up/uri-2.png"])

    def test_admin_routes_and_screen(self):
        api = tsq._src("core", "api_server.py")
        routes = ('@app.get("/api/admin/characters")', '@app.post("/api/admin/characters/reference")',
                  '@app.post("/api/admin/characters/upload")', '@app.post("/api/admin/characters/reference/remove")',
                  '@app.post("/api/admin/characters/notes")', '@app.post("/api/admin/characters/clear")',
                  '@app.post("/api/admin/characters/generate")', '@app.post("/api/admin/characters/candidate/save")',
                  '@app.post("/api/admin/characters/candidate/discard")',
                  '@app.post("/api/admin/characters/candidate/promote")',
                  '@app.post("/api/admin/characters/pose/remove")', '@app.get("/api/admin/characters/pose/{pose_id}")')
        for route in routes:
            self.assertIn(route, api)
        body = api[api.index('@app.get("/api/admin/characters")'):api.index('@app.get("/api/admin/drive-pull/info")')]
        self.assertEqual(body.count("_require_admin(request)"), len(routes))   # every route: the admin session
        self.assertEqual(body.count('by="admin"'), 10)
        for gone in ("/api/admin/characters/sync", "/api/admin/characters/override"):
            self.assertNotIn(gone, api)
        html = tsq._src("dashboard", "admin", "characters", "index.html")
        for needle in ("/api/admin/characters/reference", "/api/admin/characters/upload",
                       "/api/admin/characters/reference/remove", "/api/admin/characters/generate",
                       "/api/admin/characters/candidate/save", "/api/admin/characters/candidate/discard",
                       "/api/admin/characters/candidate/promote", "/api/admin/characters/pose/remove",
                       "/api/admin/characters/pose/", "שמירה למאגר", "הפיכה לתמונת ייחוס", "class=\"promote-warn\"",
                       "בניית הזהות מתוצר שנוצר", "תמונות ייחוס", "בדיקת תנוחות", "מאגר התנוחות", "multiple",
                       "itzik-chat.js"):
            self.assertIn(needle, html)
        # the order on each agent's card: what defines it, the test, what posts reuse
        self.assertIn("${refsHtml(a, d)}${testHtml(a, d)}${libraryHtml(a)}", html)
        # promote sends confirm only from the warning's own button
        self.assertEqual(html.count("confirm: true"), 1)
        self.assertIn('href="/admin/characters/"', tsq._src("dashboard", "admin", "index.html"))


# ─── The workspace: test poses → save / discard / promote; remove a pose ─────

class Workspace(Store):
    def setUp(self):
        super().setUp()
        self.set_ref("maya")
        self.old_key = os.environ.get(ac.KEY_ENV)
        os.environ[ac.KEY_ENV] = "kid:ksecret"

    def tearDown(self):
        if self.old_key is None:
            os.environ.pop(ac.KEY_ENV, None)
        else:
            os.environ[ac.KEY_ENV] = self.old_key
        super().tearDown()

    def run_test_poses(self, instruction="", count=3):
        sent = []

        def fake_gen(key, prompt, refs, aspect):
            sent.append((prompt, refs, aspect))
            return {"url": f"https://cdn.example/test-{len(sent)}.png", "credits": 2.5, "model": "xai/grok-imagine-image-2.0",
                    "aspect_ratio": aspect, "request_id": f"req-{len(sent)}"}
        poses = ac.start_test("maya", instruction, count)["poses"]
        job = ac.run_test("maya", poses, _gen=fake_gen)
        return poses, job, sent

    def test_three_varied_default_poses_on_the_real_post_path(self):
        poses, job, sent = self.run_test_poses()
        self.assertEqual(poses, list(ac.DEFAULT_TEST_POSES))
        self.assertEqual((job["state"], job["made"], job["credits"]), ("done", 3, 7.5))
        for (prompt, refs, aspect), pose in zip(sent, poses):
            self.assertEqual(prompt, ac.pose_prompt("maya", pose, framing="full body"))   # the composite path
            self.assertIn(ac.STYLE_CLAUSE, prompt)
            self.assertEqual(refs, ac.reference_urls(["maya"]))
            self.assertEqual(aspect, ac.POSE_ASPECT)
        row = _row(ac.view(), "maya")
        self.assertEqual(len(row["candidates"]), 3)
        self.assertEqual(row["job"]["credits"], 7.5)
        self.assertEqual(row["pose_counts"]["total"], 0)                 # nothing saved by itself
        self.assertEqual(ac.references()["agents"]["maya"]["images"][0]["url"], REF["maya"])   # nor promoted

    def test_an_instruction_and_a_count(self):
        poses, job, sent = self.run_test_poses("sitting at a desk, typing", 2)
        self.assertEqual(poses, ["sitting at a desk, typing"] * 2)
        self.assertIn("sitting at a desk, typing", sent[0][0])

    def test_refusals(self):
        with self.assertRaises(ac.MissingReference):
            ac.start_test("lior")
        os.environ.pop(ac.KEY_ENV, None)
        with self.assertRaises(ac.CharacterUnavailable):
            ac.start_test("maya")
        os.environ[ac.KEY_ENV] = "kid:ksecret"
        ac.start_test("maya")
        with self.assertRaises(ValueError):
            ac.start_test("maya")                                         # one test at a time

    def test_save_to_library_tagged_and_reused_like_any_pose(self):
        self.run_test_poses()
        cand = _row(ac.view(), "maya")["candidates"][0]
        removed = []
        view = ac.save_candidate("maya", cand["cand_id"], pose="waving hello", outfit="denim apron",
                                 framing="half body",
                                 _remove=lambda key, url: removed.append(url) or {"content": _figure()},
                                 _upload=lambda agent, png: {"id": "POSEFILE_0123456789"})
        self.assertEqual(removed, [cand["url"]])                         # background removed at save
        pose = ac.get_pose(view["saved"])
        self.assertEqual((pose["approved"], pose["transparent"], pose["outfit"], pose["framing"], pose["description"]),
                         (True, True, "denim apron", "half body", "waving hello"))
        self.assertEqual(len(_row(view, "maya")["candidates"]), 2)
        found = ac.find_poses("maya", "waving hello", transparent=True)
        self.assertEqual(found[0]["pose_id"], pose["pose_id"])           # offered to posts like any library pose
        # a background that was not removed: refused, the result stays
        with self.assertRaises(ac.BackgroundNotRemoved):
            ac.save_candidate("maya", _row(ac.view(), "maya")["candidates"][0]["cand_id"],
                              _remove=lambda key, url: {"content": _figure(alpha=False)},
                              _upload=lambda agent, png: self.fail("uploaded an opaque pose"))
        self.assertEqual(len(_row(ac.view(), "maya")["candidates"]), 2)

    def test_discard(self):
        self.run_test_poses(count=1)
        cand = _row(ac.view(), "maya")["candidates"][0]
        view = ac.discard_candidate("maya", cand["cand_id"])
        self.assertEqual(_row(view, "maya")["candidates"], [])
        self.assertEqual(_row(view, "maya")["pose_counts"]["total"], 0)
        with self.assertRaises(ValueError):
            ac.discard_candidate("maya", cand["cand_id"])

    def test_promote_only_deliberately(self):
        self.run_test_poses(count=1)
        cand = _row(ac.view(), "maya")["candidates"][0]
        with self.assertRaises(ValueError):
            ac.promote_candidate("maya", cand["cand_id"])                 # no confirm, no promotion
        self.assertEqual(len(ac.reference_for("maya")["images"]), 1)
        view = ac.promote_candidate("maya", cand["cand_id"], confirm=True,
                                    _fetch_fn=lambda u: _png(), _keep_fn=lambda *a: "KEEP_promoted_01")
        images = ac.reference_for("maya")["images"]
        self.assertEqual((len(images), images[-1]["url"], images[-1]["source"]), (2, cand["url"], "promoted"))
        self.assertTrue(_row(view, "maya")["candidates"][0]["promoted"])
        self.assertTrue(any("GENERATED image was promoted" in a for a in self.alerts))

    def test_remove_a_pose_from_the_library(self):
        p = ac.add_pose("maya", "FILE_0123456789", True)
        ac.approve_poses([p["pose_id"]])
        ac.record_pose_use(p["pose_id"])
        view = ac.remove_pose(p["pose_id"])
        self.assertEqual(_row(view, "maya")["poses"], [])
        self.assertEqual(ac.get_pose(p["pose_id"]), {})
        self.assertNotIn("maya", ac._library()["last"])
        with self.assertRaises(ValueError):
            ac.remove_pose(p["pose_id"])

    def test_the_screen_shows_both(self):
        p = ac.add_pose("maya", "FILE_0123456789", True, description="waving", outfit="apron", framing="full body")
        ac.approve_poses([p["pose_id"]])
        row = _row(ac.view(), "maya")
        self.assertEqual(len(row["images"]), 1)
        self.assertEqual(row["pose_counts"], {"approved": 1, "pending": 0, "transparent": 1, "total": 1})
        self.assertEqual((row["poses"][0]["description"], row["poses"][0]["outfit"]), ("waving", "apron"))

# ─── No undocumented element endpoint, anywhere ──────────────────────────────

class NoElementList(unittest.TestCase):
    def test_no_code_path_calls_an_element_list_endpoint(self):
        root = os.path.dirname(HERE)
        hits = []
        for folder in ("core", "agents", "scripts", "dashboard"):
            for dirpath, _dirs, files in os.walk(os.path.join(root, folder)):
                for name in files:
                    if not name.endswith((".py", ".sh", ".html", ".js")):
                        continue
                    with open(os.path.join(dirpath, name), encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                    for needle in ("reference-elements", "list_elements", "ELEMENTS_LIST_PATH", "sync_due",
                                   "generate_with_elements", "/api/admin/characters/sync"):
                        if needle in text:
                            hits.append(f"{folder}/{name}: {needle}")
        self.assertEqual(hits, [])
        self.assertFalse(hasattr(gen, "list_elements"))
        self.assertFalse(hasattr(ac, "sync"))
        api = tsq._src("core", "api_server.py")
        self.assertNotIn("agent_characters.sync", api)


# ─── 2. The brief ────────────────────────────────────────────────────────────

class Brief(unittest.TestCase):
    def test_default_mode_per_asset_type(self):
        self.assertEqual(pb.default_mode("text_slide"), "composite")
        for t in ("generated_image", "generated_video"):
            self.assertEqual(pb.default_mode(t), "in_scene")
        self.assertEqual(pb.character_fields("a", "text_slide", ["maya"], ""), (["maya"], "composite"))
        self.assertEqual(pb.character_fields("a", "generated_image", ["Lior", "maya"], None),
                         (["lior", "maya"], "in_scene"))
        self.assertEqual(pb.character_fields("a", "real_photo", [], ""), ([], ""))

    def test_refusals(self):
        for args in (("real_photo", ["maya"], ""), ("text_slide", ["maya", "lior"], "composite"),
                     ("generated_image", ["maya"], "composite"), ("text_slide", ["maya"], "in_scene"),
                     ("generated_image", ["maya", "lior", "yoav", "uri"], "in_scene"),
                     ("generated_image", ["bob"], ""), ("generated_image", ["maya"], "collage")):
            with self.assertRaises(ValueError, msg=str(args)):
                pb.character_fields("a", *args)

    def test_both_fields_carried_through(self):
        post = tpb._post()
        post["assets"][0]["characters"] = ["maya"]                       # text slide → composite by default
        post["assets"][1]["characters"] = ["lior", "maya"]
        post["assets"][1]["character_mode"] = "in_scene"
        out = pb.validate_post(post, 27, "", "he")
        a0, a1, a2 = out["assets"]
        self.assertEqual((a0["characters"], a0["character_mode"]), (["maya"], "composite"))
        self.assertEqual((a1["characters"], a1["character_mode"]), (["lior", "maya"], "in_scene"))
        self.assertEqual((a2["characters"], a2["character_mode"]), ([], ""))
        brief = pb.asset(a1)
        self.assertEqual((brief.characters, brief.character_mode), (["lior", "maya"], "in_scene"))
        old = {k: v for k, v in a2.items() if k not in ("characters", "character_mode")}
        self.assertEqual(pb.asset(old).characters, [])                   # entries stored before this

    def test_the_social_agent_is_told(self):
        hint = tsq._src("core", "agent_actions.py")
        self.assertIn('\\"characters\\"?: [agents who appear AS THEMSELVES', hint)
        rule = tsq._src("agents", "support_agent.py")
        self.assertIn("AGENTS AS CHARACTERS (2026-11-24)", rule)
        self.assertIn("NEVER create, rebuild", rule)
        craft = tsq._src("knowledge", "social-craft.md")
        section = craft[craft.index("### 2.2"):craft.index("### 2.3")]
        self.assertIn("character_mode            composite | in_scene", section)


class CarriedThroughProduction(unittest.TestCase):
    def setUp(self):
        tpb.Pipeline.setUp(self)

    def test_card_flags_and_production_brief(self):
        from agents import media_agent
        from core import slide_design
        post = tpb._post()
        post["assets"][0]["characters"] = ["maya"]
        post["assets"][1]["characters"] = ["lior"]
        saved_flags = ac.approval_flags
        ac.approval_flags = lambda cid, has, _brief_text=None: ["ai_label"] if has else []
        self.addCleanup(setattr, ac, "approval_flags", saved_flags)
        item = pp.submit(27, post, "", "he")["queued"]
        s1, s2, s3 = item["post"]["slides"]
        self.assertEqual((s1["characters"], s1["character_mode"]), (["maya"], "composite"))
        self.assertEqual((s2["characters"], s2["character_mode"]), (["lior"], "in_scene"))
        self.assertIn("ai_label", s1["flags"])
        self.assertNotIn("ai_label", s3["flags"])
        seen, designed = [], []

        def fake(client_id, brief, palette, platform="instagram"):
            seen.append((brief.characters, brief.character_mode))
            return {"success": True, "kind": "image", "file_id": "FILE_s2_0123456789", "link": "", "credits": 3,
                    "characters": [{"agent": "lior", "pose_id": POSE}]}
        saved = (media_agent.generate_from_brief, slide_design.open_for_post)
        media_agent.generate_from_brief = fake
        slide_design.open_for_post = lambda cid, it, slides, lang="he": designed.append(slides) or {
            "item": {"id": "design0001"}, "problems": []}
        try:
            with _approve_hook_off():
                pp.start(27, sq.approve(27, [item["id"]]))
        finally:
            media_agent.generate_from_brief, slide_design.open_for_post = saved
        self.assertEqual(seen, [(["lior"], "in_scene")])
        self.assertEqual(designed[0][0]["characters"], ["maya"])          # the composite slide → the design
        made = next(i for i in self.store.items if i.get("post_ref"))
        self.assertEqual(made["characters"], [{"agent": "lior", "pose_id": POSE}])
        self.assertIn("ai_label", made["flags"])


class _approve_hook_off:
    def __enter__(self):
        self.old = ac.on_items_approved
        ac.on_items_approved = lambda *a, **k: []

    def __exit__(self, *exc):
        ac.on_items_approved = self.old


# ─── 3. In scene ─────────────────────────────────────────────────────────────

class _FakeHiggsfield:
    """httpx stand-in: records every POST payload, answers completed."""

    def __init__(self):
        self.sent, self.urls = [], []

    def __enter__(self):
        test = self

        class Resp:
            status_code = 200
            text = ""

            def __init__(self, data):
                self._d = data

            def json(self):
                return self._d
        self.fake = types.SimpleNamespace(
            post=lambda url, headers=None, json=None, timeout=None: test.urls.append(url) or test.sent.append(json)
            or Resp({"request_id": "req-0001"}),
            get=lambda url, **kw: Resp({"status": "completed", "images": [{"url": "https://cdn.example/x.png"}]}))
        self.saved = (gen.httpx, gen._download, gen._count, gen.time.sleep)
        gen.httpx, gen._download, gen._count, gen.time.sleep = self.fake, lambda url: b"PNG", lambda *a: None, lambda s: None
        return self

    def __exit__(self, *exc):
        gen.httpx, gen._download, gen._count, gen.time.sleep = self.saved


class InScene(Store):
    def setUp(self):
        super().setUp()
        self.all_refs()

    def test_one_character(self):
        p = ac.in_scene_prompt("The character sketching at a sunny workshop table", ["maya"])
        self.assertIn("the character shown in the reference image", p)
        self.assertNotIn("<<<", p)
        self.assertTrue(p.rstrip(".").endswith(ac.STYLE_CLAUSE))

    def test_several_characters_one_reference_each_in_order(self):
        p = ac.in_scene_prompt("Two characters at a market stall", ["maya", "lior", "uri"])
        for n in (1, 2, 3):
            self.assertIn(f"the character from reference image {n}", p)
        self.assertEqual(p.count(ac.STYLE_CLAUSE), 1)
        self.assertEqual(ac.reference_urls(["maya", "lior", "uri"]), [REF["maya"], REF["lior"], REF["uri"]])
        with self.assertRaises(ValueError):
            ac.in_scene_prompt("x", ["maya", "maya"])
        self.assertGreaterEqual(gen.max_references("xai/grok-imagine-image-2.0"), ac.MAX_CHARACTERS)
        self.assertGreaterEqual(gen.max_references("alibaba/qwen-image-3/edit"), ac.MAX_CHARACTERS)

    def test_the_models_own_placeholders_are_removed(self):
        p = ac.in_scene_prompt("<<<made-up-id>>> at a desk <<< 1234 >>>", ["yoav"])
        self.assertNotIn("<<<", p)
        self.assertIn("at a desk", p)

    def test_notes_carried(self):
        ac.set_notes("elinor", "green blazer, half body")
        self.assertIn("green blazer, half body", ac.in_scene_prompt("a scene", ["elinor"]))
        self.assertIn("green blazer, half body", ac.pose_prompt("elinor", "waving"))
        self.assertNotIn("green blazer", ac.pose_prompt("elinor", "waving", outfit="red apron"))

    def test_style_clause_always(self):
        for agents in (["maya"], ["lior", "johnny"]):
            self.assertIn(ac.STYLE_CLAUSE, ac.in_scene_prompt("a scene", agents))
        self.assertIn(ac.STYLE_CLAUSE, ac.pose_prompt("elinor", "waving"))
        self.assertIn(ac.STYLE_CLAUSE, ac.with_style("anything"))
        self.assertEqual(ac.with_style("x. " + ac.STYLE_CLAUSE + "."), "x. " + ac.STYLE_CLAUSE + ".")
        self.assertEqual(ac.STYLE_CLAUSE, "same stylized 3D character look and same face as the reference figure")

    def test_copy_never_in_a_character_prompt(self):
        brief = pb.asset(pb.validate_post(_character_post(), 27, "", "he")["assets"][0])
        clean = ac.in_scene_prompt("the character arranging flowers in a bright studio", ["maya"])
        pb.assert_copy_free(clean, brief)
        leaked = ac.in_scene_prompt("the character holding a sign: " + brief.copy_headline, ["maya"])
        with self.assertRaises(pb.CopyLeak):
            pb.assert_copy_free(leaked, brief)
        from agents import media_agent
        seen = []
        old = media_agent.safe_claude_json_call
        media_agent.safe_claude_json_call = lambda system, user, **kw: seen.append(json.loads(user)) or {
            "generation_prompt": "x"}
        self.addCleanup(setattr, media_agent, "safe_claude_json_call", old)
        media_agent._craft_character_prompt(27, brief, {}, {"craft": "c", "brand": "b"})
        text = json.dumps(seen[0], ensure_ascii=False)
        self.assertNotIn(brief.copy_headline, text)
        self.assertNotIn("copy_headline", text)
        src = tsq._src("agents", "media_agent.py")
        body = src[src.index("def _generate_character_scene("):src.index("# ─── Text-slide DESIGN")]
        self.assertLess(body.index("in_scene_prompt("), body.index("assert_copy_free(prompt, brief)"))
        self.assertLess(body.index("assert_copy_free(prompt, brief)"), body.index("generate_with_references("))

    def test_generation_sends_the_stored_images_per_the_model_schema(self):
        with _FakeHiggsfield() as hf:
            prompt = ac.in_scene_prompt("a scene", ["maya", "lior"])
            out = gen.generate_with_references("id:secret", prompt, ac.reference_urls(["maya", "lior"]), "4:5")
        payload = hf.sent[0]
        self.assertEqual(hf.urls[0], "https://api.higgsfield.ai/xai/grok-imagine-image-2.0")
        self.assertEqual(set(payload), {"prompt", "image_urls", "aspect_ratio"})   # Grok Image 2.0's documented fields
        self.assertEqual(payload["image_urls"], [REF["maya"], REF["lior"]])
        self.assertEqual(payload["aspect_ratio"], "3:4")                 # 4:5 is not in its enum
        self.assertEqual((out["url"], out["request_id"]), ("https://cdn.example/x.png", "req-0001"))
        with self.assertRaises(ValueError):
            gen.generate_with_references("id:secret", prompt, [], "4:5")
        with self.assertRaises(ValueError):
            gen.generate_with_references("id:secret", prompt, ["https://x/y.png"] * 11, "4:5")
        with self.assertRaises(gen.CharacterVideoUnsupported):
            gen.generate_with_references("id:secret", prompt, [REF["maya"]], "9:16", kind="video")

    def test_the_media_agent_sends_the_stored_image(self):
        from agents import media_agent
        brief = pb.asset(pb.validate_post(_character_post(), 27, "", "he")["assets"][0])
        sent = []
        saved = (media_agent._library_scene, media_agent._context, media_agent.safe_claude_json_call,
                 gen.generate_with_references, media_agent._subfolder, media_agent.drive.upload_bytes,
                 media_agent._log_activity, os.environ.get(ac.KEY_ENV))
        media_agent._library_scene = lambda cid, b: {}
        media_agent._context = lambda cid: {"craft": "c", "brand": "b"}
        media_agent.safe_claude_json_call = lambda *a, **k: {"generation_prompt": "the character at a sunny table"}
        three_four = _png_size(300, 400)                                 # Grok has no 4:5: it sends 3:4
        gen.generate_with_references = lambda key, prompt, refs, ar, kind="image": sent.append((refs, prompt)) or {
            "url": "https://cdn.example/x.png", "content": three_four, "mime": "image/png", "credits": 2,
            "model": "m", "aspect_ratio": "3:4"}
        media_agent._subfolder = lambda *a: "FOLDER"
        stored = []
        media_agent.drive.upload_bytes = lambda *a: stored.append(a[2]) or {"id": "GENFILE_0123456789"}
        media_agent._log_activity = lambda *a, **k: None
        os.environ[ac.KEY_ENV] = "kid:ksecret"
        try:
            out = media_agent.generate_from_brief(27, brief, {})
        finally:
            (media_agent._library_scene, media_agent._context, media_agent.safe_claude_json_call,
             gen.generate_with_references, media_agent._subfolder, media_agent.drive.upload_bytes,
             media_agent._log_activity) = saved[:7]
            if saved[7] is None:
                os.environ.pop(ac.KEY_ENV, None)
            else:
                os.environ[ac.KEY_ENV] = saved[7]
        self.assertTrue(out["success"], out)
        self.assertEqual(sent[0][0], [REF["maya"]])
        self.assertIn(ac.CROP_MARGIN, sent[0][1])                       # it will be cropped: room asked for
        if Image is not None:
            self.assertEqual(Image.open(io.BytesIO(stored[0])).size, (300, 375))   # a true 4:5 reached the post

    def test_runs_on_uallaks_key_and_refuses_without_it(self):
        from agents import media_agent
        brief = pb.asset(pb.validate_post(_character_post(), 27, "", "he")["assets"][0])
        old = (os.environ.pop(ac.KEY_ENV, None), media_agent.agent_alert, media_agent._library_scene)
        media_agent.agent_alert = lambda *a, **k: None
        media_agent._library_scene = lambda cid, b: {}
        try:
            out = media_agent.generate_from_brief(27, brief, {})
        finally:
            if old[0] is not None:
                os.environ[ac.KEY_ENV] = old[0]
            media_agent.agent_alert, media_agent._library_scene = old[1], old[2]
        self.assertEqual(out["status"], "characters_not_configured")
        self.assertIn('"HIGGSFIELD_AGENTS_KEY": os.environ.get("HIGGSFIELD_AGENTS_KEY", "")',
                      tsq._src("agents", "keys_agent.py"))


# ─── Missing reference → the explicit Hebrew error, to the manager ───────────

class MissingReferenceError(Store):
    def test_the_message(self):
        self.assertEqual(ac.missing_text("lior"), "לא מוגדרת תמונת ייחוס עבור ליאור (lior)")
        with self.assertRaises(ac.MissingReference) as e:
            ac.in_scene_prompt("a scene", ["lior"])
        self.assertEqual(e.exception.message, "לא מוגדרת תמונת ייחוס עבור ליאור (lior)")
        with self.assertRaises(ac.MissingReference):
            ac.pose_prompt("lior", "waving")
        with self.assertRaises(ac.MissingReference):
            ac.make_pose("lior", "waving", _gen=lambda *a: self.fail("generated without a reference"))

    def test_in_scene_failure_reaches_the_reply(self):
        from agents import media_agent
        brief = pb.asset(pb.validate_post(_character_post(), 27, "", "he")["assets"][0])
        old = (media_agent.agent_alert, media_agent._library_scene)
        media_agent.agent_alert = lambda *a, **k: None
        media_agent._library_scene = lambda cid, b: {}
        try:
            out = media_agent.generate_from_brief(27, brief, {})
        finally:
            media_agent.agent_alert, media_agent._library_scene = old
        self.assertFalse(out["success"])
        self.assertEqual(out["status"], "character_missing_reference")
        self.assertEqual(out["message"], "לא מוגדרת תמונת ייחוס עבור מאיה (maya)")

    def test_composite_failure_carries_the_message(self):
        from core import slide_design
        entries, failures = slide_design.attach_characters(
            [{"asset_id": "s1", "role": "hook", "characters": ["nissim"], "character_mode": "composite"}])
        self.assertEqual(failures[0]["message"], "לא מוגדרת תמונת ייחוס עבור ניסים (nissim)")
        self.assertNotIn("character", entries[0])
        src = tsq._src("core", "slide_design.py")
        self.assertIn('"character_errors": list(dict.fromkeys(m["message"] for m in missing if m.get("message")))', src)


REAL_TELL = pp._tell                           # the harness stubs it; this test needs the real line


class MissingReferenceInChat(unittest.TestCase):
    def setUp(self):
        tpb.Pipeline.setUp(self)
        pp._tell = REAL_TELL

    def test_production_line_says_it_never_success(self):
        from agents import media_agent
        from agents import client_agent
        from core import slide_design
        post = tpb._post()
        post["assets"][1]["characters"] = ["lior"]
        saved_flags = ac.approval_flags
        ac.approval_flags = lambda cid, has, _brief_text=None: ["ai_label"] if has else []
        self.addCleanup(setattr, ac, "approval_flags", saved_flags)
        item = pp.submit(27, post, "", "he")["queued"]
        msg = ac.missing_text("lior")
        lines = []
        saved = (media_agent.generate_from_brief, slide_design.open_for_post, client_agent.log_communication)
        media_agent.generate_from_brief = lambda cid, brief, palette, platform="instagram": (
            {"success": False, "status": "character_missing_reference", "errors": [msg], "message": msg}
            if brief.characters else {"success": True, "kind": "image", "file_id": "FILE_ok_0123456789", "credits": 0})
        slide_design.open_for_post = lambda cid, it, slides, lang="he": {
            "item": {"id": "design0001"}, "problems": [], "character_errors": []}
        client_agent.log_communication = lambda cid, direction, channel, text: lines.append(text)
        try:
            with _approve_hook_off():
                pp.start(27, sq.approve(27, [item["id"]]))
        finally:
            media_agent.generate_from_brief, slide_design.open_for_post, client_agent.log_communication = saved
        got = sq.get(27, item["id"])["production"]
        self.assertEqual(got["character_errors"], [msg])
        self.assertGreaterEqual(got["failed"], 1)
        self.assertNotEqual(got["state"], "done")
        self.assertTrue(lines and msg in lines[-1], lines)


def _character_post():
    return {"post_format": "single_image", "format_reason": "one strong scene carries the message",
            "objective": "awareness", "audience": "gift buyers (private customers)", "set_id": "maya-scene-1",
            "caption": "כיתוב", "assets": [
                {"asset_id": "s1", "role": "hook", "copy_headline": tpb.HEAD, "copy_body": tpb.BODY,
                 "asset_type": "generated_image", "characters": ["maya"],
                 "visual_direction": "the character arranging a gift box on a sunny studio table"}]}


# ─── 4. Background removal → a transparent PNG ───────────────────────────────

def _figure(w=100, h=200, left=(220, 30, 30, 255), right=None, alpha=True) -> bytes:
    img = Image.new("RGBA" if alpha else "RGB", (w, h), (0, 0, 0, 0) if alpha else (255, 255, 255))
    for x in range(20, 80):
        for y in range(10, 190):
            colour = left if (right is None or x < 50) else right
            img.putpixel((x, y), colour if alpha else colour[:3])
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


@NEEDS_PIL
class BackgroundRemoval(Store):
    def test_transparent_png_accepted_opaque_refused(self):
        png = ac.ensure_transparent(_figure())
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.mode, "RGBA")
        self.assertEqual(img.getchannel("A").getextrema(), (0, 255))
        with self.assertRaises(ac.BackgroundNotRemoved):
            ac.ensure_transparent(_figure(alpha=False))                  # the remover gave the original back
        full = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        out = io.BytesIO()
        full.save(out, "PNG")
        with self.assertRaises(ac.BackgroundNotRemoved):
            ac.ensure_transparent(out.getvalue())                        # alpha channel, nothing transparent

    def test_make_pose_generates_removes_verifies_stores(self):
        self.set_ref("maya")
        calls = []
        gen_fn = lambda key, prompt, refs, aspect: calls.append(("gen", prompt, refs)) or {
            "url": "https://cdn/x.png", "credits": 2}
        rem_fn = lambda key, url: calls.append(("remove", url)) or {"content": _figure()}
        up_fn = lambda agent, png: calls.append(("upload", agent)) or {"id": "POSEFILE_0123456789"}
        made = ac.make_pose("maya", "waving hello", _gen=gen_fn, _remove=rem_fn, _upload=up_fn)
        self.assertEqual([c[0] for c in calls], ["gen", "remove", "upload"])
        self.assertIn("plain flat light-grey seamless studio background", calls[0][1])
        self.assertEqual(calls[0][2], [REF["maya"]])                    # the stored reference image
        self.assertEqual(calls[1][1], "https://cdn/x.png")
        pose = ac.get_pose(made["pose_id"])
        self.assertEqual((pose["agent"], pose["transparent"], pose["approved"], pose["client_id"]),
                         ("maya", True, False, 0))
        bad = lambda key, url: {"content": _figure(alpha=False)}
        with self.assertRaises(ac.BackgroundNotRemoved):
            ac.make_pose("maya", "waving", _gen=gen_fn, _remove=bad, _upload=up_fn)


# ─── 5. Compositing ──────────────────────────────────────────────────────────

def _spec(block_y=12, **character):
    spec = {"version": 1, "canvas": {"width": 1080, "height": 1080}, "palette_variant": "test", "margins": 64,
            "background": {"type": "solid", "color_role": "bg"},
            "text_blocks": [{"role": "headline", "anchor": "top", "align": "right", "x_pct": 0, "y_pct": block_y,
                             "max_width_pct": 80, "font_family": "Rubik", "size": 90, "weight": 700,
                             "color_role": "fg"}]}
    if character:
        spec["character"] = character
    return spec


@NEEDS_RAQM
class Compositing(unittest.TestCase):
    def render(self, spec, fig=None, headline="שלום עולם", **ch):
        warnings = []
        png = sr.render_slide(spec, ROLES, headline, "", 1, 1, "", lambda key: fig or _figure(),
                              character={"agent": "maya", "pose_id": POSE, **ch}, warnings=warnings)
        return Image.open(io.BytesIO(png)).convert("RGB"), warnings

    def test_position_and_scale(self):
        img, w = self.render(_spec(), x_pct=75, y_pct=100, scale_pct=55, shadow=False)
        self.assertEqual(w, [])
        # x_pct from the RIGHT: centre at 1080 - 810 = 270; feet at the bottom, height 594
        self.assertEqual(img.getpixel((270, 1080 - 297)), (220, 30, 30))
        self.assertEqual(img.getpixel((270, 1080 - 594 - 20)), (240, 240, 240))   # above its head: background
        small, _ = self.render(_spec(), x_pct=75, scale_pct=30, shadow=False)
        self.assertEqual(small.getpixel((270, 1080 - 450)), (240, 240, 240))        # shorter figure
        self.assertEqual(small.getpixel((270, 1080 - 100)), (220, 30, 30))

    def test_flip(self):
        fig = _figure(left=(220, 30, 30, 255), right=(30, 30, 220, 255))
        img, _ = self.render(_spec(), fig=fig, x_pct=50, shadow=False)
        mirrored, _ = self.render(_spec(), fig=fig, x_pct=50, shadow=False, flip=True)
        y = 1080 - 300
        self.assertEqual(img.getpixel((500, y)), (220, 30, 30))          # left of centre: the red half
        self.assertEqual(mirrored.getpixel((500, y)), (30, 30, 220))     # mirrored: the blue half

    def test_shadow_soft_and_configurable(self):
        spec = _spec()
        off, _ = self.render(spec, x_pct=50, y_pct=80, scale_pct=50, shadow=False)
        on, _ = self.render(spec, x_pct=50, y_pct=80, scale_pct=50, shadow=True)
        strong, _ = self.render(spec, x_pct=50, y_pct=80, scale_pct=50, shadow=0.9)
        feet = round(1080 * 0.8)
        probe = (540 + 50, feet + 4)                                      # just below the feet, under the ellipse
        self.assertEqual(off.getpixel(probe), (240, 240, 240))
        self.assertLess(on.getpixel(probe)[0], 240)
        self.assertLess(strong.getpixel(probe)[0], on.getpixel(probe)[0])
        self.assertEqual(sr.shadow_intensity(False), 0.0)
        self.assertEqual(sr.shadow_intensity(True), sr.DEFAULT_SHADOW)

    def test_text_drawn_on_top_and_collision_warned(self):
        img, w = self.render(_spec(block_y=60), x_pct=75, y_pct=100, scale_pct=60, shadow=False,
                             headline="שלום עולם גדול מאוד")
        self.assertIn("character_text_overlap", [x["code"] for x in w])
        # inside the figure's box, some pixels are the text colour: the text is ON TOP of the figure
        box = img.crop((171, 650, 369, 900))
        self.assertTrue(any(px == (16, 16, 16) for px in box.getdata()))
        _, clear = self.render(_spec(block_y=8), x_pct=75, scale_pct=40, shadow=False)
        self.assertEqual(clear, [])

    def test_overflow_warns_never_fails(self):
        img, w = self.render(_spec(), x_pct=100, scale_pct=50, shadow=False)
        self.assertEqual([x["code"] for x in w], ["character_overflow"])
        self.assertEqual(img.getpixel((10, 1080 - 200)), (220, 30, 30))   # the part on the canvas is drawn

    def test_an_opaque_pose_fails(self):
        with self.assertRaises(sr.RenderError) as e:
            self.render(_spec(), fig=_figure(alpha=False))
        self.assertEqual(e.exception.problems[0]["code"], "character_not_transparent")

    def test_set_warnings_carry_the_slide(self):
        spec = _spec(block_y=60)
        got = []
        sr.render_set(spec, ROLES, [{"copy_headline": "שלום עולם גדול מאוד", "role": "",
                                     "character": {"agent": "maya", "pose_id": POSE, "scale_pct": 60, "x_pct": 75}},
                                    {"copy_headline": "בלי דמות", "role": ""}],
                      lambda key: _figure(), encode=False, warnings=got)
        self.assertEqual({w["slide"] for w in got}, {1})


class Schema(unittest.TestCase):
    def test_character_in_the_spec_schema(self):
        spec = _spec(x_pct=20, y_pct=95, scale_pct=50, flip=True, shadow=0.5)
        self.assertEqual(slide_spec.validate(spec)["character"]["shadow"], 0.5)
        for bad in ({"pose_id": "NOPE"}, {"x_pct": 500}, {"shadow": 2}, {"agent": "bob"}, {"size": 3}):
            with self.assertRaises(slide_spec.SpecInvalid, msg=str(bad)):
                slide_spec.validate(_spec(**bad))
        self.assertIn("character", slide_spec.schema()["$defs"]["override"]["properties"])

    def test_designer_sets_placement_only(self):
        from core import slide_design
        spec = _spec(agent="maya", pose_id=POSE, x_pct=20)
        spec["role_overrides"] = {"hook": {"character": {"pose_id": POSE, "scale_pct": 40}}}
        out = slide_design.placement_only(spec)
        self.assertEqual(out["character"], {"x_pct": 20})
        self.assertEqual(out["role_overrides"]["hook"]["character"], {"scale_pct": 40})

    def test_under_the_text_in_the_draw_order(self):
        src = tsq._src("core", "slide_renderer.py")
        body = src[src.index("def _render("):src.index("def _counter_layout(")]
        self.assertLess(body.index("_character(img"), body.index("_logo(img"))
        self.assertLess(body.index("_logo(img"), body.index("_draw_text(draw, lay)"))


# ─── 6. The pose library ─────────────────────────────────────────────────────

class Library(Store):
    def pose(self, agent="maya", approved=True, **kw):
        p = ac.add_pose(agent, kw.pop("file_id", "FILE_" + os.urandom(4).hex()), kw.pop("transparent", True),
                        **kw)
        if approved:
            ac.approve_poses([p["pose_id"]])
        return ac.get_pose(p["pose_id"])

    def test_approved_post_saves_its_poses_with_tags(self):
        p = self.pose(approved=False, outfit="", setting="", framing="")
        item = sq.add(27, "fileLIBRARY000001", "image", ["instagram"],
                      characters=[{"agent": "maya", "pose_id": p["pose_id"]}])
        self.assertFalse(ac.get_pose(p["pose_id"])["approved"])
        sq.approve(27, [item["id"]])
        got = ac.get_pose(p["pose_id"])
        self.assertTrue(got["approved"])
        self.assertTrue(got["approved_item"].endswith(item["id"]))
        ac.approve_poses([p["pose_id"]], tags={p["pose_id"]: {"outfit": "denim apron", "setting": "workshop",
                                                              "framing": "half body"}})
        got = ac.get_pose(p["pose_id"])
        self.assertEqual((got["outfit"], got["setting"], got["framing"], got["transparent"]),
                         ("denim apron", "workshop", "half body", True))

    def test_an_approved_design_saves_its_poses(self):
        p = self.pose(approved=False)
        design = {"post_id": "post00000001", "template_name": "t", "spec": {},
                  "slides": [{"asset_id": "s1", "character": {"agent": "maya", "pose_id": p["pose_id"]}}]}
        item = sq.add_design(27, design, title="d")
        sq.approve(27, [item["id"]])
        self.assertTrue(ac.get_pose(p["pose_id"])["approved"])

    def test_library_poses_offered_before_generating(self):
        p = self.pose(description="waving hello", tags=["waving", "hello"])
        never = lambda *a, **k: self.fail("generated although a stored pose fits")
        got = ac.pose_for("maya", "waving hello", _gen=never, _remove=never, _upload=never)
        self.assertEqual((got["pose_id"], got["reused"], got["credits"]), (p["pose_id"], True, 0))
        # pending (unapproved) poses are never offered
        ac.add_pose("lior", "FILE_PENDING_0001", True, description="waving")
        self.assertEqual(ac.find_poses("lior", "waving"), [])
        # in scene: the library goes to the agent's own choice before generation
        from agents import media_agent
        scene = self.pose(agent="maya", transparent=False, client_id=27, setting="studio",
                          description="the character at a sunny studio table")
        brief = pb.asset(pb.validate_post(_character_post(), 27, "", "he")["assets"][0])
        old = media_agent.choose_real_asset
        media_agent.choose_real_asset = lambda cid, b, cands: next(c for c in cands if c["pose_id"] == scene["pose_id"])
        self.addCleanup(setattr, media_agent, "choose_real_asset", old)
        out = media_agent._character_scene(27, brief, {}, "instagram")
        self.assertEqual((out["file_id"], out["credits"], out["reused_pose"]), (scene["file_id"], 0, True))
        # another client's scenes are never offered
        self.assertEqual(ac.find_poses("maya", "studio", transparent=False, client_id=99), [])

    def test_repetition_ranking(self):
        a = self.pose(description="waving hello", tags=["waving"])
        b = self.pose(description="waving hello", tags=["waving"])
        first = ac.find_poses("maya", "waving")[0]["pose_id"]
        ac.record_pose_use(first)
        nxt = ac.find_poses("maya", "waving")
        other = ({a["pose_id"], b["pose_id"]} - {first}).pop()
        self.assertEqual(nxt[0]["pose_id"], other)                       # not the same pose twice in a row
        self.assertTrue(nxt[-1]["repeat"])
        # the last-used pose ranks last even when it matches better
        strong = self.pose(description="thumbs up proud", tags=["thumbs", "proud"])
        ac.record_pose_use(strong["pose_id"])
        ranked = ac.find_poses("maya", "thumbs proud", limit=5)
        self.assertEqual(ranked[-1]["pose_id"], strong["pose_id"])
        # usage penalty: the often-used pose ranks below an unused one of the same fit
        self.assertLess(next(p for p in ranked if p["pose_id"] == first)["score"],
                        next(p for p in ranked if p["pose_id"] == other)["score"] + 1e-9)


# ─── 7. Approval-card notices ────────────────────────────────────────────────

class Notices(unittest.TestCase):
    def test_flags(self):
        self.assertEqual(ac.approval_flags(27, False, _brief_text="anything"), [])
        self.assertEqual(ac.approval_flags(27, True, _brief_text="Warm, friendly, real workshop photos."),
                         ["ai_label"])
        for text in ("Never use AI-generated people in our posts.", "No AI people.",
                     "Avoid generated faces at all costs", "ללא אנשים שנוצרו ב-AI"):
            self.assertEqual(ac.approval_flags(27, True, _brief_text=text), ["ai_person_forbidden", "ai_label"], text)
        for code in ("ai_person_forbidden", "ai_label", "character_overflow", "character_text_overlap",
                     "character_unavailable"):
            for lang in tsq.LANGS:
                self.assertTrue(sq.FLAG_TEXT[code][lang], f"{code} {lang}")

    def test_shown_never_blocking(self):
        item = {"id": "x"}
        src = tsq._src("core", "post_pipeline.py")
        self.assertIn("flags[brief.asset_id] += agent_characters.approval_flags(client_id, True)", src)
        objections = src[src.index("if not review[\"objection\"]"):src.index("draft[\"updated_at\"] = _now()")]
        self.assertNotIn("ai_", objections)                               # a notice is never an objection
        self.assertIn("(item.flags || []).map(f => `<div class=\"soc-flag\">${esc(f.text)}</div>`).join('')",
                      tsq._src("dashboard", "client", "index.html"))
        del item


# ─── 8. THE guardrail ────────────────────────────────────────────────────────

class Guardrail(Store):
    def test_an_agent_creating_a_character_is_refused(self):
        for fn in (ac.create_element, ac.rebuild_element, ac.update_element):
            with self.assertRaises(ac.ElementCreationRefused):
                fn("maya", images=["generated.png"], by="media")
        self.assertTrue(any("refused" in a for a in self.alerts))
        # only the admin sets or clears a reference — never an agent
        with self.assertRaises(ac.ElementCreationRefused):
            self.set_ref("maya", by="media")
        with self.assertRaises(ac.ElementCreationRefused):
            ac.clear_reference("maya", by="lior")
        self.assertEqual(ac.references()["agents"], {})
        # nor adds, removes, promotes, tests or removes poses — every write on the screen is the manager's
        self.set_ref("maya")
        ref_id = ac.reference_for("maya")["images"][0]["ref_id"]
        p = ac.add_pose("maya", "FILE_0123456789", True)
        for call in (lambda: ac.add_reference("maya", url=REF["lior"], by="media"),
                     lambda: ac.remove_reference("maya", ref_id, by="media"),
                     lambda: ac.set_notes("maya", "x", by="media"),
                     lambda: ac.start_test("maya", by="maya"),
                     lambda: ac.save_candidate("maya", "c0123456789", by="media"),
                     lambda: ac.discard_candidate("maya", "c0123456789", by="media"),
                     lambda: ac.promote_candidate("maya", "c0123456789", confirm=True, by="media"),
                     lambda: ac.remove_pose(p["pose_id"], by="lior")):
            with self.assertRaises(ac.ElementCreationRefused):
                call()
        self.assertEqual(len(ac.reference_for("maya")["images"]), 1)
        self.assertTrue(ac.get_pose(p["pose_id"]))

    def test_no_create_path_exists(self):
        src = tsq._src("core", "media_gen_service.py")
        tail = src[src.index("# ─── Character reference images"):]
        for word in ("def create_element", "def update_element", "def train", "soul_id", "custom_reference"):
            self.assertNotIn(word, tail)
        from core import agent_actions as aa
        for name in aa.ACTIONS:
            self.assertFalse("element" in name or "soul" in name or "reference" in name, name)
        out = aa.propose(27, "client_chat", "media", {"name": "create_reference_element", "params": {"agent": "maya"}},
                         client={"id": 27}, dry_run=True)
        self.assertIn("error", out)

    def test_weak_character_is_reported_not_acted_on(self):
        self.set_ref("maya")
        before = ac.reference_for("maya")["images"]
        entry = ac.report_element_weak("maya", "the face drifts in profile shots", by="media")
        self.assertEqual(entry["agent"], "maya")
        self.assertEqual(ac.reference_for("maya")["images"], before)     # nothing changed
        self.assertEqual(ac.view()["reports"][-1]["reason"], "the face drifts in profile shots")
        self.assertTrue(any("reported weak" in a for a in self.alerts))

# ─── 9. A true 4:5: nearest supported ratio, then a centre crop ──────────────

def _cutout(w, h, top, bottom, left=None, right=None) -> bytes:
    """Transparent canvas with an opaque figure between rows top..bottom-1."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for x in range(left if left is not None else w // 3, right if right is not None else 2 * w // 3):
        for y in range(top, bottom):
            img.putpixel((x, y), (200, 40, 40, 255))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


@NEEDS_PIL
class AspectCrop(unittest.TestCase):
    def test_three_four_becomes_a_true_four_five(self):
        got = ac.fit_aspect(_png_size(600, 800), "4:5", "3:4")
        self.assertTrue(got["cropped"])
        w, h = Image.open(io.BytesIO(got["content"])).size
        self.assertEqual((w, h), (600, 750))                              # 6.25% off the height
        self.assertAlmostEqual(w / h, 0.8)

    def test_native_ratio_is_never_cropped(self):
        src = _png_size(800, 1000)
        got = ac.fit_aspect(src, "4:5", "4:5")
        self.assertEqual((got["content"], got["cropped"]), (src, False))
        self.assertFalse(ac.needs_crop("4:5", "4:5"))
        self.assertTrue(ac.needs_crop("4:5", "3:4"))
        # a model whose enum HAS 4:5 is sent 4:5 (no crop); one without gets its nearest
        saved = dict(gen.CHARACTER_MODELS)
        gen.CHARACTER_MODELS["test/native-45"] = {"field": "image_references", "max": 14,
                                                  "aspects": ("1:1", "4:5", "3:4")}
        try:
            self.assertEqual(gen.character_aspect("4:5", "test/native-45"), "4:5")
        finally:
            gen.CHARACTER_MODELS.clear()
            gen.CHARACTER_MODELS.update(saved)
        self.assertEqual(gen.character_aspect("4:5", "xai/grok-imagine-image-2.0"), "3:4")
        self.assertEqual(gen.character_aspect("4:5", "alibaba/qwen-image-3/edit"), "7:9")   # nearer than 3:4
        self.assertEqual(gen.character_aspect("9:16", "xai/grok-imagine-image-2.0"), "9:16")

    def test_wide_images_lose_the_sides(self):
        got = ac.fit_aspect(_png_size(1000, 1000), "4:5", "1:1")
        self.assertEqual(Image.open(io.BytesIO(got["content"])).size, (800, 1000))

    def test_a_cutout_is_never_clipped(self):
        # the figure touches the top: a centre crop would cut its head — the window moves instead
        got = ac.fit_aspect(_cutout(600, 800, top=0, bottom=700), "4:5", "3:4")
        self.assertTrue(got["cropped"])
        img = Image.open(io.BytesIO(got["content"]))
        self.assertEqual(img.size, (600, 750))
        opaque = sum(1 for a in img.getchannel("A").getdata() if a > 16)
        self.assertEqual(opaque, 200 * 700)                               # every visible pixel kept
        # feet at the very bottom: same
        got = ac.fit_aspect(_cutout(600, 800, top=120, bottom=800), "4:5", "3:4")
        img = Image.open(io.BytesIO(got["content"]))
        self.assertEqual(sum(1 for a in img.getchannel("A").getdata() if a > 16), 200 * 680)

    def test_warn_rather_than_clip(self):
        src = _cutout(600, 800, top=5, bottom=795)                       # taller than any 4:5 window
        got = ac.fit_aspect(src, "4:5", "3:4")
        self.assertEqual((got["content"], got["cropped"]), (src, False))
        self.assertIn("character_crop_skipped", got["warning"])

    def test_make_pose_crops_without_clipping(self):
        db = tsq.FakeDB()
        saved = (ac._db, ac._alert)
        ac._db = lambda: db
        alerts = []
        ac._alert = lambda lines: alerts.extend(lines)
        try:
            ac.set_reference("maya", url=REF["maya"], _fetch_fn=lambda u: _png(), _keep_fn=lambda *a: "KEEP_0123456789")
            gen_fn = lambda key, prompt, refs, aspect: {"url": "https://cdn/x.png", "credits": 1, "aspect_ratio": "3:4"}
            tall = lambda key, url: {"content": _cutout(600, 800, top=2, bottom=798)}
            made = ac.make_pose("maya", "waving", aspect_ratio="4:5", _gen=gen_fn, _remove=tall,
                                _upload=lambda a, png: {"id": "POSEFILE_0123456789"})
            self.assertFalse(made["cropped"])
            self.assertIn("character_crop_skipped", made["warning"])
            self.assertTrue(any("character_crop_skipped" in a for a in alerts))
            fits = lambda key, url: {"content": _cutout(600, 800, top=100, bottom=700)}
            made = ac.make_pose("maya", "waving", aspect_ratio="4:5", _gen=gen_fn, _remove=fits,
                                _upload=lambda a, png: {"id": "POSEFILE_0123456789"})
            self.assertTrue(made["cropped"])
            self.assertEqual(Image.open(io.BytesIO(made["png"])).size, (600, 750))
        finally:
            ac._db, ac._alert = saved


# ─── 10. Only verified model paths, each with its own reference field ────────

class ModelList(unittest.TestCase):
    def test_only_verified_paths_and_each_models_own_field(self):
        self.assertEqual(set(gen.CHARACTER_MODELS), {"xai/grok-imagine-image-2.0", "alibaba/qwen-image-3/edit"})
        self.assertEqual(gen.CHARACTER_IMAGE_PATH, os.environ.get("HIGGSFIELD_CHARACTER_IMAGE_PATH",
                                                                  "xai/grok-imagine-image-2.0"))
        for path, m in gen.CHARACTER_MODELS.items():
            self.assertTrue(m["field"] and m["max"] >= ac.MAX_CHARACTERS and m["aspects"], path)
        # an unverified path is refused before anything is sent
        saved = gen.CHARACTER_IMAGE_PATH
        gen.CHARACTER_IMAGE_PATH = "google/nano-banana-pro"
        try:
            with _FakeHiggsfield() as hf:
                with self.assertRaises(gen.UnverifiedModelPath) as e:
                    gen.generate_with_references("id:secret", "p", [REF["maya"]], "4:5")
            self.assertEqual(hf.sent, [])
            self.assertIn("not a verified model path", str(e.exception))
            self.assertEqual(gen.max_references(), 0)
        finally:
            gen.CHARACTER_IMAGE_PATH = saved
        # a model's OWN field name and native 4:5 are what is sent
        saved_models = dict(gen.CHARACTER_MODELS)
        gen.CHARACTER_MODELS["test/native-45"] = {"field": "image_references", "max": 14, "aspects": ("1:1", "4:5")}
        gen.CHARACTER_IMAGE_PATH = "test/native-45"
        try:
            with _FakeHiggsfield() as hf:
                out = gen.generate_with_references("id:secret", "p", [REF["maya"], REF["lior"]], "4:5")
            self.assertEqual(set(hf.sent[0]), {"prompt", "image_references", "aspect_ratio"})
            self.assertEqual((hf.sent[0]["aspect_ratio"], out["aspect_ratio"]), ("4:5", "4:5"))
        finally:
            gen.CHARACTER_IMAGE_PATH = saved
            gen.CHARACTER_MODELS.clear()
            gen.CHARACTER_MODELS.update(saved_models)

    def test_the_probe_is_safe(self):
        sh = tsq._src("scripts", "higgsfield_probe_paths.sh")
        self.assertIn('SAFE_BODY = {"aspect_ratio": "0:0"}', sh)          # no prompt, invalid enum
        self.assertNotIn('"prompt"', sh)
        self.assertIn('("xai/grok-imagine-image-2.0", "422")', sh)          # the controls
        self.assertIn('"uallak-probe/definitely-not-a-model", "404"', sh)
        self.assertIn('requests/{rid}/cancel', sh)                        # anything accepted is cancelled
        self.assertIn('text.replace(KEY, "***")', sh)                    # the key is never printed
        self.assertIn('KEY = os.environ.pop("HF_KEY")', sh)


if __name__ == "__main__":
    unittest.main()
