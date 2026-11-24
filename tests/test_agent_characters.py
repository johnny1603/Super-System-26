"""The agents as their own characters (2026-11-24, core/agent_characters,
docs/AGENT-CHARACTERS.md).

What must stay true:
- the agent ↔ element mapping is STORED (app_settings), matched from the
  element list by "uallak-<agent>" (case-insensitive), overridable per agent,
  with conflicts and unmatched elements listed — never hard-coded ids;
- the brief carries `characters` + `character_mode` through validation, the
  post item and production; default mode per asset type;
- in scene: ONE <<<element_id>>> per character, the style clause always, the
  slide copy never in the prompt, and an element id never in a media list;
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

MAYA, LIOR, YOAV = "fb0e4dba-4f11-4e27-9b5c-517406406d53", "25c32188-073e-4946-9801-2a759632ff3d", \
    "d61da04f-ff66-4b5b-9045-34a71c40e0be"
URI_A, URI_B = "a74cc3b0-d681-4231-8901-4239fe149ef0", "67e4d7d4-997f-4266-8012-f9852d81a934"
JOHNNY, ELINOR, NISSIM = "c4ba7167-8ac3-4ff0-9213-440049a2d6d2", "6f5bb752-84a8-4904-b6c0-385904521c1d", \
    "623775a6-f4e3-40b1-8293-0888f8dd50ea"
AGENT1 = "9feee7bb-0000-4000-8000-000000000001"

# The element list as Higgsfield showed it on 2026-11-24 (names verbatim)
ELEMENTS = [
    {"id": NISSIM, "name": "uallak-nissim", "status": "completed"},
    {"id": ELINOR, "name": "uallak-elinor", "status": "completed"},
    {"id": JOHNNY, "name": "uallak-johnny", "status": "completed"},
    {"id": YOAV, "name": "uallak-yoav", "status": "completed"},
    {"id": LIOR, "name": "char_uallak-lior", "status": "completed"},
    {"id": URI_A, "name": "Uallak-uri", "status": "completed"},
    {"id": MAYA, "name": "uallak-maya", "status": "completed"},
    {"id": URI_B, "name": "uallak-uri", "status": "completed"},
    {"id": AGENT1, "name": "uallak-agent-1", "status": "completed"},
    {"id": "1e6b6890-0000-4000-8000-000000000002", "name": "johnny-face", "status": "completed"},
]
ROLES = {"bg": "#F0F0F0", "fg": "#101010", "accent": "#FF4C1F", "muted": "#666666"}
POSE = "abcdef123456"


class Store(unittest.TestCase):
    """agent_characters + social_queue on ONE in-memory app_settings."""

    def setUp(self):
        self.db = tsq.FakeDB()
        self.saved = [(ac, "_db", ac._db), (ac, "_alert", ac._alert), (sq, "_db", sq._db)]
        ac._db = lambda: self.db
        sq._db = lambda: self.db
        self.alerts = []
        ac._alert = lambda lines: self.alerts.extend(lines)

    def tearDown(self):
        for mod, name, value in self.saved:
            setattr(mod, name, value)

    def synced(self):
        ac.sync(_list=lambda: copy.deepcopy(ELEMENTS))
        return ac.mapping()


# ─── 1. The mapping ──────────────────────────────────────────────────────────

class Mapping(Store):
    def test_populated_from_the_element_list(self):
        view = ac.sync(_list=lambda: copy.deepcopy(ELEMENTS))
        rows = {r["agent"]: r for r in view["agents"]}
        for agent, eid in (("maya", MAYA), ("yoav", YOAV), ("johnny", JOHNNY), ("elinor", ELINOR),
                           ("nissim", NISSIM)):
            self.assertEqual((rows[agent]["element_id"], rows[agent]["status"], rows[agent]["match"]),
                             (eid, "ready", "exact"))
        # "char_uallak-lior": a LOOSE match, shown as such, still usable
        self.assertEqual((rows["lior"]["element_id"], rows["lior"]["match"]), (LIOR, "loose"))
        # two uri elements: a conflict — no automatic pick
        self.assertEqual((rows["uri"]["status"], rows["uri"]["element_id"]), ("conflict", ""))
        self.assertEqual({c["id"] for c in rows["uri"]["candidates"]}, {URI_A, URI_B})
        with self.assertRaises(ac.CharacterUnavailable):
            ac.element_for("uri")
        stored = self.db.tables["app_settings"][0]
        self.assertEqual(stored["key"], "agent_elements")              # in the DB, not in code
        self.assertTrue(view["last_sync"])

    def test_unmatched_elements_are_listed(self):
        view = ac.sync(_list=lambda: copy.deepcopy(ELEMENTS))
        self.assertEqual([e["name"] for e in view["unmatched"]], ["uallak-agent-1"])
        self.assertEqual(view["other_elements"], 1)                    # johnny-face: not a uallak element

    def test_override_wins_and_clears(self):
        self.synced()
        view = ac.set_override("uri", URI_A)
        uri = next(r for r in view["agents"] if r["agent"] == "uri")
        self.assertEqual((uri["element_id"], uri["status"], uri["source"]), (URI_A, "ready", "override"))
        self.assertEqual(ac.element_for("uri"), URI_A)
        # survives the next re-read
        ac.sync(_list=lambda: copy.deepcopy(ELEMENTS))
        self.assertEqual(ac.element_for("uri"), URI_A)
        ac.set_override("uri", "")
        self.assertEqual(next(r for r in ac.view()["agents"] if r["agent"] == "uri")["status"], "conflict")
        with self.assertRaises(ValueError):
            ac.set_override("uri", "not-a-uuid")
        with self.assertRaises(ValueError):
            ac.set_override("bob", URI_A)

    def test_only_completed_elements_are_usable(self):
        els = copy.deepcopy(ELEMENTS)
        els[6]["status"] = "processing"                                  # maya
        ac.sync(_list=lambda: els)
        with self.assertRaises(ac.CharacterUnavailable) as e:
            ac.element_for("maya")
        self.assertEqual(e.exception.reason, "not_ready")

    def test_re_read_on_a_schedule_and_on_request(self):
        calls = []
        lister = lambda: calls.append(1) or copy.deepcopy(ELEMENTS)
        self.assertEqual(ac.sync_due(_list=lister).get("synced"), True)
        self.assertEqual(ac.sync_due(_list=lister), {"skipped": "fresh"})   # within SYNC_HOURS
        self.assertEqual(len(calls), 1)
        api = tsq._src("core", "api_server.py")
        self.assertIn('data["agent_elements"] = agent_characters.sync_due()', api)
        self.assertIn('@app.post("/api/admin/characters/sync")', api)
        self.assertIn('@app.post("/api/admin/characters/override")', api)
        body = api[api.index('@app.get("/api/admin/characters")'):api.index('@app.get("/api/admin/drive-pull/info")')]
        self.assertEqual(body.count("_require_admin(request)"), 3)

    def test_a_failed_read_keeps_the_last_match(self):
        self.synced()

        def broken():
            raise RuntimeError("503")
        view = ac.sync(_list=broken)
        self.assertIn("503", view["last_error"])
        self.assertEqual(ac.element_for("maya"), MAYA)

    def test_admin_screen(self):
        html = tsq._src("dashboard", "admin", "characters", "index.html")
        for needle in ("/api/admin/characters", "/api/admin/characters/sync", "/api/admin/characters/override",
                       'id="unmatched"', "candidates", "itzik-chat.js"):
            self.assertIn(needle, html)
        self.assertIn('href="/admin/characters/"', tsq._src("dashboard", "admin", "index.html"))


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

class InScene(Store):
    def setUp(self):
        super().setUp()
        self.synced()
        ac.set_override("uri", URI_A)

    def test_one_placeholder(self):
        p = ac.in_scene_prompt("The character sketching at a sunny workshop table", ["maya"])
        self.assertEqual(ac.placeholders_in(p), [MAYA])
        self.assertEqual(p.count("<<<"), 1)
        self.assertTrue(p.rstrip(".").endswith(ac.STYLE_CLAUSE))

    def test_multiple_placeholders_one_each(self):
        p = ac.in_scene_prompt("Two characters at a market stall", ["maya", "lior", "uri"])
        self.assertEqual(ac.placeholders_in(p), [MAYA, LIOR, URI_A])
        self.assertEqual(p.count(ac.STYLE_CLAUSE), 1)
        with self.assertRaises(ValueError):
            ac.in_scene_prompt("x", ["maya", "maya"])

    def test_the_models_own_placeholders_are_removed(self):
        p = ac.in_scene_prompt("<<<made-up-id>>> at a desk <<< 1234 >>>", ["yoav"])
        self.assertEqual(ac.placeholders_in(p), [YOAV])

    def test_style_clause_always(self):
        for agents in (["maya"], ["lior", "johnny"]):
            self.assertIn(ac.STYLE_CLAUSE, ac.in_scene_prompt("a scene", agents))
        self.assertIn(ac.STYLE_CLAUSE, ac.pose_prompt("elinor", "waving"))
        self.assertIn(ac.STYLE_CLAUSE, ac.with_style("anything"))
        self.assertEqual(ac.with_style("x. " + ac.STYLE_CLAUSE + "."), "x. " + ac.STYLE_CLAUSE + ".")

    def test_copy_never_in_a_character_prompt(self):
        brief = pb.asset(pb.validate_post(_character_post(), 27, "", "he")["assets"][0])
        clean = ac.in_scene_prompt("the character arranging flowers in a bright studio", ["maya"])
        pb.assert_copy_free(clean, brief)
        leaked = ac.in_scene_prompt("the character holding a sign: " + brief.copy_headline, ["maya"])
        with self.assertRaises(pb.CopyLeak):
            pb.assert_copy_free(leaked, brief)
        # the craft call never sees the copy, and the guard runs on the FINAL prompt
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
        self.assertLess(body.index("assert_copy_free(prompt, brief)"), body.index("generate_with_elements("))

    def test_element_ids_never_in_a_media_list(self):
        sent = []

        class Resp:
            status_code = 200

            def __init__(self, data):
                self._d = data

            def json(self):
                return self._d
        fake = types.SimpleNamespace(
            post=lambda url, headers=None, json=None, timeout=None: sent.append(json) or Resp({"request_id": "r1"}),
            get=lambda url, **kw: Resp({"status": "completed", "images": [{"url": "https://cdn.example/x.png"}]}))
        saved = (gen.httpx, gen._download, gen._count, gen.time.sleep)
        gen.httpx, gen._download, gen._count, gen.time.sleep = fake, lambda url: b"PNG", lambda *a: None, lambda s: None
        try:
            prompt = ac.in_scene_prompt("a scene", ["maya", "lior"])
            out = gen.generate_with_elements("id:secret", prompt, "4:5")
        finally:
            gen.httpx, gen._download, gen._count, gen.time.sleep = saved
        payload = sent[0]
        self.assertEqual(set(payload), {"model", "prompt", "aspect_ratio"})
        self.assertIn(f"<<<{MAYA}>>>", payload["prompt"])
        self.assertEqual(out["url"], "https://cdn.example/x.png")
        with self.assertRaises(ValueError):
            gen.generate_with_elements("id:secret", "no placeholder here", "4:5")

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
        self.synced()
        calls = []
        gen_fn = lambda key, prompt, aspect: calls.append(("gen", prompt)) or {"url": "https://cdn/x.png", "credits": 2}
        rem_fn = lambda key, url: calls.append(("remove", url)) or {"content": _figure()}
        up_fn = lambda agent, png: calls.append(("upload", agent)) or {"id": "POSEFILE_0123456789"}
        made = ac.make_pose("maya", "waving hello", _gen=gen_fn, _remove=rem_fn, _upload=up_fn)
        self.assertEqual([c[0] for c in calls], ["gen", "remove", "upload"])
        self.assertIn("plain flat light-grey seamless studio background", calls[0][1])
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
    def test_an_agent_creating_an_element_is_refused(self):
        for fn in (ac.create_element, ac.rebuild_element, ac.update_element):
            with self.assertRaises(ac.ElementCreationRefused):
                fn("maya", images=["generated.png"], by="media")
        self.assertTrue(any("refused" in a for a in self.alerts))

    def test_no_create_path_exists(self):
        src = tsq._src("core", "media_gen_service.py")
        tail = src[src.index("# ─── Reference elements"):]
        for word in ("def create_element", "def update_element", "def train", "soul_id", "custom_reference"):
            self.assertNotIn(word, tail)
        self.assertEqual([n for n in dir(gen) if "element" in n.lower() and callable(getattr(gen, n))],
                         ["generate_with_elements", "list_elements"])
        from core import agent_actions as aa
        for name in aa.ACTIONS:
            self.assertFalse("element" in name or "soul" in name, name)
        out = aa.propose(27, "client_chat", "media", {"name": "create_reference_element", "params": {"agent": "maya"}},
                         client={"id": 27}, dry_run=True)
        self.assertIn("error", out)

    def test_weak_element_is_reported_not_acted_on(self):
        self.synced()
        before = ac.element_for("maya")
        entry = ac.report_element_weak("maya", "the face drifts in profile shots", by="media")
        self.assertEqual(entry["agent"], "maya")
        self.assertEqual(ac.element_for("maya"), before)                 # nothing changed
        self.assertEqual(ac.view()["reports"][-1]["reason"], "the face drifts in profile shots")
        self.assertTrue(any("reported weak" in a for a in self.alerts))


if __name__ == "__main__":
    unittest.main()
