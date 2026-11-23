"""The brand brief in the database, drafted at onboarding (2026-11-24,
core/brand_brief + core/brand_intake, knowledge/brand-brief.schema.md).

What must stay true:
- the brief is read from the database, per client, versioned;
- client 27's repo brief + palette are migrated byte-for-byte, and what the
  agents get (text, facts, palette roles) is identical to before;
- at onboarding a draft is queued and the intake agent writes it from the
  intake answers, the website and the public social pages, with a
  confidence mark on every field;
- agents work from a draft (no refusal), the Control Room marks it awaiting
  approval;
- the manager's edits persist and survive approval; approval clears the draft;
- the palette holds only colours seen on the site, the rest is a GAP; a client
  with no palette still gets colours (neutral) for its content;
- a missing brief degrades: the agents get the MISSING note, never an error.

Run from the repo root:  python tests/test_brand_brief.py -v
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_social_queue as tsq      # noqa: E402  (stubs + an in-memory app_settings)

from core import brand_brief as bb, brand_intake as bi, post_brief as pb, slide_spec   # noqa: E402

ROOT = os.path.dirname(HERE)
CID = 27


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


FILE_27 = _read("knowledge", "brands", "27.md")
PALETTE_27 = json.loads(_read("knowledge", "brands", "27.palette.json"))


class WithDB(unittest.TestCase):
    def setUp(self):
        self.db = tsq.FakeDB()
        old = bb._db
        bb._db = lambda: self.db
        self.addCleanup(setattr, bb, "_db", old)
        bb._cache.clear()
        self.addCleanup(bb._cache.clear)

    def row(self, cid):
        rows = [r for r in self.db.tables.get("app_settings", []) if r["key"] == f"brand_brief:{cid}"]
        return rows[0]["value"] if rows else None

    def put(self, cid, value):
        self.db.tables.setdefault("app_settings", []).append({"key": f"brand_brief:{cid}", "value": value})
        bb._cache.clear()


class FromTheDatabase(WithDB):
    def test_read_from_the_database(self):
        text = "# Brand Brief — Test\nclient_id: 500\nStatus: DRAFT — awaiting manager approval\n\n## identity\n- business_name: Test [stated]\n"
        self.put(500, {"versions": [{"v": 1, "at": "x", "by": "intake_agent", "kind": "drafted", "status": "draft",
                                     "text": text, "palette": None}]})
        ctx = pb.load_context(500)
        self.assertEqual(ctx["brand"], text.strip())
        self.assertEqual(ctx["brand_status"], "draft")
        self.assertEqual(bb.admin_view(500)["source"], "database")


class Migration27(WithDB):
    def test_migrated_with_identical_content(self):
        self.assertIsNone(self.row(CID))
        rec = bb.record(CID)
        stored = self.row(CID)["versions"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["text"], FILE_27)                      # byte for byte
        self.assertEqual(stored[0]["palette"], PALETTE_27)
        self.assertEqual((stored[0]["kind"], stored[0]["status"]), ("migrated", "draft"))
        self.assertEqual(rec["source"], "database")
        # from now on the database is the source: the file is never read again
        old = bb._legacy
        bb._legacy = lambda cid: (_ for _ in ()).throw(AssertionError("file read after migration"))
        self.addCleanup(setattr, bb, "_legacy", old)
        bb._cache.clear()
        self.assertEqual(bb.text(CID), FILE_27)

    def test_behaviour_unchanged_for_27(self):
        ctx = pb.load_context(CID)
        self.assertEqual(ctx["brand"], FILE_27.strip())                   # what the file path gave
        self.assertEqual(ctx["brand_facts"], pb.parse_brand(FILE_27.strip()))
        self.assertEqual(ctx["brand_facts"]["lead_time_days"], (15, 25))
        self.assertEqual([v["label"] for v in ctx["brand_facts"]["palette_variants"]], ["black + red", "black + gold"])
        for label, roles in PALETTE_27["variants"].items():
            self.assertEqual(slide_spec.palette_roles(CID, label), {k: v.upper() for k, v in roles.items()})
        self.assertEqual(ctx["brand_lacks"], [])
        # database down → the same, read-only from the file
        bb._db = lambda: (_ for _ in ()).throw(RuntimeError("down"))
        bb._cache.clear()
        self.assertEqual(pb.load_context(CID)["brand"], FILE_27.strip())


SITE = """<html><head><title>Bloom Bakery — עוגות בהזמנה</title>
<meta name="description" content="Custom cakes in Haifa"><meta name="theme-color" content="#d94f70">
<style>body{background:#fffaf5;color:#2b2b2b} .btn{background:#d94f70} small{color:#999999}</style></head>
<body><h1>עוגות מעוצבות בהזמנה אישית</h1><a href="https://www.instagram.com/bloom.bakery/">ig</a></body></html>"""


class Onboarding(WithDB):
    def setUp(self):
        super().setUp()
        self.calls = []
        patches = []
        from agents import client_agent
        from core import claude_json, drive_assets

        def fake_fetch(url, max_bytes):
            self.calls.append(url)
            if "instagram.com" in url:
                return '<meta property="og:title" content="Bloom Bakery (@bloom.bakery)"><meta property="og:description" content="1,200 followers - cakes">'
            return SITE

        def fake_model(system, user, **kw):
            payload = json.loads(user)
            self.payload = payload
            return {"business_name": "Bloom Bakery",
                    "brief_markdown": "## identity\n- business_name: Bloom Bakery [stated]\n- website: https://bloom.example.co.il [observed]\n"
                                      "- founded: unknown\n## voice\n- tone_attributes: warm, homey [inferred]\n",
                    "palette": {"bg": "#FFFAF5", "fg": "#2B2B2B", "accent": "#D94F70", "muted": "#123456"},
                    "gaps": ["typefaces: not determinable"]}

        for mod, name, value in ((bi, "fetch", fake_fetch),
                                 (bi, "intake_answers", lambda cid: {"business": "Bloom Bakery",
                                                                     "site": "bloom.example.co.il",
                                                                     "email": "owner@gmail.com"}),
                                 (claude_json, "safe_claude_json_call", fake_model),
                                 (client_agent, "get_client", lambda cid: {"id": cid, "business_name": "Bloom"}),
                                 (client_agent, "get_accounts", lambda cid: []),
                                 (drive_assets, "_load", lambda cid: {"items": {}})):
            patches.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)
        for mod, name, value in patches:
            self.addCleanup(setattr, mod, name, value)

    def test_queued_at_onboarding_then_drafted_by_the_minute(self):
        self.assertTrue(bb.request_draft(600, "onboarding"))
        self.assertFalse(bb.request_draft(600, "onboarding"))            # once
        self.assertEqual(self.row(600)["draft_job"]["state"], "queued")
        out = bb.run_due(200)
        self.assertEqual(out["drafted"][0]["client_id"], 600)
        cur = bb.current(600)
        self.assertEqual((cur["kind"], cur["status"], cur["by"]), ("drafted", "draft", "intake_agent"))
        # its sources: the answers, the website (not the email's domain), the public social page
        self.assertIn("https://bloom.example.co.il", self.calls)
        self.assertFalse(any("gmail.com" in u for u in self.calls))
        self.assertTrue(any("instagram.com/bloom.bakery" in u for u in self.calls))
        self.assertEqual(self.payload["social_pages"][0]["title"], "Bloom Bakery (@bloom.bakery)")
        self.assertIn("Business: Bloom Bakery".lower(), self.payload["intake_answers"].lower())
        self.assertEqual(self.row(600)["draft_job"]["state"], "done")

    def test_confidence_marks_on_every_field(self):
        bb.request_draft(601)
        bb.run_due(200)
        text = bb.text(601)
        self.assertIn(bb.DRAFT_STATUS_LINE, text)
        fields = [f for s in bb.sections(text) for f in s["fields"] if f["key"] not in ("client_id",)]
        self.assertTrue(fields)
        self.assertTrue(all(f["mark"] in ("stated", "observed", "inferred") for f in fields), fields)
        self.assertIn("- founded: unknown [inferred]", text)              # an unmarked line gets [inferred]

    def test_palette_only_from_seen_colours_rest_is_a_gap(self):
        bb.request_draft(602)
        bb.run_due(200)
        cur = bb.current(602)
        roles = cur["palette"]["variants"]["default"]
        self.assertEqual(roles, {"bg": "#FFFAF5", "fg": "#2B2B2B", "accent": "#D94F70"})   # #123456 was never seen
        self.assertIn("palette: muted colour not determinable from the site", cur["gaps"])
        self.assertEqual(cur["palette"]["status"], "partial")
        got = slide_spec.palette_roles(602, "default")
        self.assertEqual(got["muted"], bb.NEUTRAL["muted"])                # the gap → neutral
        self.assertEqual(got["accent"], "#D94F70")

    def test_agents_work_from_the_draft(self):
        bb.request_draft(603)
        bb.run_due(200)
        ctx = pb.load_context(603)
        self.assertEqual(ctx["brand_status"], "draft")
        self.assertIn("Bloom Bakery", ctx["brand"])
        self.assertEqual(pb.try_context(603).get("missing"), None)
        from agents import media_agent
        self.assertIn("Bloom Bakery", media_agent._context(603)["brand"])
        view = bb.admin_view(603)
        self.assertEqual(view["status"], "draft")                         # the Control Room marks it
        self.assertGreater(view["marks"]["stated"], 0)

    def test_a_draft_never_overwrites_an_existing_brief(self):
        self.put(604, {"versions": [{"v": 1, "at": "x", "by": "manager", "kind": "edited", "status": "draft",
                                     "text": "# mine", "palette": None}]})
        self.assertFalse(bb.request_draft(604))
        self.assertEqual(bb.draft_now(604), {"ok": False, "error": "exists"})
        self.assertEqual(bb.text(604), "# mine")


class ManagerReview(WithDB):
    def setUp(self):
        super().setUp()
        bb.record(CID)                                                     # 27, migrated

    def test_edits_persist_and_survive_approval(self):
        secs = bb.sections(bb.text(CID))
        i = next(n for n, s in enumerate(secs) if s["title"] == "voice")
        edited = secs[i]["text"].replace("emoji_policy: sparing; never in a headline [inferred — confirm]",
                                         "emoji_policy: none at all [stated]")
        self.assertNotEqual(edited, secs[i]["text"])
        new_text = "\n".join(edited if n == i else s["text"] for n, s in enumerate(secs))
        v = bb.save_edit(CID, new_text, by="manager:דנה")
        self.assertEqual((v["kind"], v["status"], v["v"]), ("edited", "draft", 2))
        self.assertIn("emoji_policy: none at all [stated]", bb.text(CID))
        a = bb.approve(CID, by="manager:דנה")
        self.assertEqual((a["kind"], a["status"], a["v"]), ("approved", "approved", 3))
        text = bb.text(CID)
        self.assertIn("emoji_policy: none at all [stated]", text)        # the correction survived
        self.assertNotIn("Status: DRAFT", text)                           # the draft state is cleared
        self.assertIn("Status: APPROVED by the manager", text)
        self.assertEqual(pb.load_context(CID)["brand_status"], "approved")
        self.assertEqual([h["kind"] for h in bb.admin_view(CID)["history"]], ["approved", "edited", "migrated"])
        self.assertEqual(bb.current(CID)["palette"], PALETTE_27)          # untouched by text edits

    def test_palette_edit(self):
        pal = json.loads(json.dumps(PALETTE_27))
        pal["variants"]["black + red"]["accent"] = "#ff0000"
        bb.save_edit(CID, None, pal, by="manager")
        self.assertEqual(slide_spec.palette_roles(CID, "black + red")["accent"], "#FF0000")
        self.assertEqual(bb.text(CID), FILE_27)


class NoPaletteAndMissing(WithDB):
    def test_no_palette_still_produces_content(self):
        text = "# Brand Brief — Plain\nStatus: DRAFT — awaiting manager approval\n\n## identity\n- business_name: Plain [stated]\n"
        self.put(700, {"versions": [{"v": 1, "at": "x", "by": "intake_agent", "kind": "drafted", "status": "draft",
                                     "text": text, "palette": None, "gaps": []}]})
        ctx = pb.load_context(700)
        palette = pb.resolve_palette(ctx["brand_facts"], "anyone")            # no variants → nothing to choose
        self.assertEqual(palette, {})
        self.assertEqual(slide_spec.palette_roles(700, slide_spec.variant_for(palette)), bb.NEUTRAL)
        self.assertIn("palette (neutral colours used)", ctx["brand_lacks"])
        self.assertIn("palette: none defined — neutral colours are used", bb.admin_view(700)["gaps"])

    def test_missing_brief_degrades_gracefully(self):
        ctx = pb.load_context(800)
        self.assertEqual((ctx["brand"], ctx["brand_status"]), (bb.MISSING_NOTE, "missing"))
        self.assertIn("say briefly what you lacked", ctx["brand"])
        self.assertEqual(self.row(800)["draft_job"]["state"], "queued")    # and one is on its way
        self.assertEqual(pb.try_context(800).get("missing"), None)         # no refusal
        self.assertEqual(slide_spec.palette_roles(800, "default"), bb.NEUTRAL)
        self.assertEqual(bb.admin_view(800)["status"], "missing")


class Wiring(unittest.TestCase):
    def test_routes_staff_only_and_onboarding_hooks(self):
        api = _read("core", "api_server.py")
        for path in ("/api/admin/brand-brief/{client_id}", "/api/admin/brand-brief/{client_id}/edit",
                     "/api/admin/brand-brief/{client_id}/approve", "/api/admin/brand-brief/{client_id}/draft"):
            a = api.index(f'"{path}")')
            self.assertIn("_require_admin(request)", api[a:a + 400], path)
        self.assertEqual(api.count('brand_brief.request_draft(client_id, "onboarding")'), 1)      # checkout
        self.assertEqual(api.count('brand_brief.request_draft(client["id"], "onboarding")'), 1)   # admin create
        self.assertIn('data["brand_brief"] = brand_brief.run_due(left)', api)
        self.assertNotIn("brand-brief", _read("dashboard", "client", "index.html"))
        self.assertTrue(os.path.exists(os.path.join(ROOT, "knowledge", "brand-brief.schema.md")))
        for path in (("core", "client_deletion.py"), ("core", "deletion_guard.py"), ("core", "admin_service.py")):
            self.assertIn("brand_brief:", _read(*path))


if __name__ == "__main__":
    unittest.main()
