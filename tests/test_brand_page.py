"""The client's branding page "מיתוג" (2026-11-26, core/brand_page,
knowledge/client-governance.md §7).

It is NOT a store: what the client sets is written into the brand brief and
its palette, the objects the agents and the slide renderer already read.
The client's own branding is accepted as it is; one friendly note at most,
dismissed for good; a business with nothing = the system leads.
"""
import copy
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import types                                                                    # noqa: E402

try:
    from core import drive_service                                              # noqa: E402
except ImportError:                       # no HTTP client on a bare test machine: Drive is faked below anyway
    import core                                                                 # noqa: E402
    drive_service = types.ModuleType("core.drive_service")
    for _name in ("list_files", "upload_bytes", "delete_file", "download_file"):
        setattr(drive_service, _name, None)
    sys.modules["core.drive_service"] = core.drive_service = drive_service

from core import brand_brief, brand_page as bp, post_brief                    # noqa: E402

PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _png(width=1200, height=600, alpha=True) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGBA" if alpha else "RGB", (width, height), (200, 30, 40, 0) if alpha else (200, 30, 40)).save(out, "PNG")
    return out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.rows, self.drive, self.n = {}, {}, 0
        saved = {(m, n): getattr(m, n) for m, n in (
            (brand_brief, "_read_row"), (brand_brief, "_write_row"), (bp, "_folder"), (bp, "_alert"),
            (drive_service, "list_files"), (drive_service, "upload_bytes"), (drive_service, "delete_file"),
            (drive_service, "download_file"))}
        self.addCleanup(lambda: [setattr(m, n, v) for (m, n), v in saved.items()])
        brand_brief._cache.clear()
        brand_brief._read_row = lambda cid: copy.deepcopy(self.rows.get(int(cid), {}))
        brand_brief._write_row = self._write
        bp._folder = lambda client_id: "brand-folder"
        self.alerts = []
        bp._alert = lambda lines: self.alerts.extend(lines)
        drive_service.list_files = lambda folder, fields="": [dict(f) for f in self.drive.values()]
        drive_service.upload_bytes = self._upload
        drive_service.delete_file = lambda file_id: self.drive.pop(file_id) and "deleted"
        drive_service.download_file = lambda file_id: self.drive[file_id]["content"]

    def _write(self, cid, value):
        self.rows[int(cid)] = copy.deepcopy(value)
        brand_brief._cache.pop(int(cid), None)

    def _upload(self, folder, name, content, mime, app_properties=None):
        self.n += 1
        fid = f"file{self.n}"
        self.drive[fid] = {"id": fid, "name": name, "mimeType": mime, "size": str(len(content)),
                           "appProperties": dict(app_properties or {}, uploaded_at=f"2026-11-26T10:00:{self.n:02d}"),
                           "content": content}
        return {"id": fid, "name": name}


class WhatAGapCosts(Base):
    """Every empty part of the page says what it costs in practice — never "missing"."""

    def test_an_empty_logo_slot_states_its_consequence(self):
        bp.upload(27, "full", "logo.png", _png())
        gaps = bp.view(27, "he")["gaps"]
        self.assertNotIn("logo_full", gaps)                              # what is there has no line
        self.assertIn("סטורי בהיר", gaps["logo_on_light"])
        self.assertIn("תמונת מוצר", gaps["logo_on_light"])
        for key in ("logo_mark", "logo_on_dark", "fonts", "assets"):
            self.assertIn(key, gaps)

    def test_every_line_is_a_consequence_in_five_languages(self):
        bare = ("חסר", "לא הועלה", "ריק")                                 # absence words alone are not a consequence
        for key, table in bp.GAPS.items():
            for lang in ("he", "en", "fr", "ar", "ru"):
                self.assertGreater(len(table[lang]), 60, key)
            self.assertFalse(any(table["he"].strip() == w for w in bare))
        gaps = bp.gaps({}, [], False, False, {}, {}, [], True, "en")
        self.assertEqual(set(gaps), {"logo_full", "logo_mark", "logo_on_light", "logo_on_dark", "assets", "colors",
                                     "fonts", "tone_says", "tone_not"})
        self.assertEqual(bp.gaps({}, [], False, False, {}, {}, [], False, "en").get("logo_full"), None)   # unknown is not missing
        full = bp.gaps({v: {} for v in bp.LOGO_VERSIONS}, [{}], True, True, {"headline": "Rubik"},
                       {"says": ["a"], "not_says": ["b"]}, [{}], True, "he")
        self.assertEqual(full, {})

    def test_the_page_draws_the_lines(self):
        html = _src("dashboard", "client", "index.html")
        for use in ("brGap('logo_' + v)", "brGap('colors')", "brGap('fonts')", "brGap('assets')", "'tone_says' : 'tone_not'"):
            self.assertIn(use, html)


class TheBrandOnRealWork(Base):
    def setUp(self):
        super().setUp()
        from core import social_queue
        self.queue = []
        saved = social_queue._load
        social_queue._load = lambda client_id: copy.deepcopy(self.queue)
        self.addCleanup(setattr, social_queue, "_load", saved)

    def item(self, n, post="p1", status="approved", of=3, at="2026-11-26T10:00:00", **more):
        roles = {"bg": "#0B0B0B", "fg": "#FFFFFF", "accent": "#E3262F", "muted": "#A6A6A6"}
        return {"id": f"{post}-{n}", "file_id": f"drive-file-{post}-{n}", "kind": "image", "status": status,
                "title": f"Launch · {n}/{of}", "created_at": at,
                "post_ref": {"post_id": post, "set_id": "s", "asset_id": f"s{n}", "n": n, "of": of},
                "manifest": {"layers": [], "palette": {"roles": roles, "variant": "black + red",
                                                       "background": {"kind": "solid"}},
                             "fonts": {}}, **more}

    def test_nothing_produced_shows_nothing(self):
        self.assertEqual(bp.view(27, "he")["work"], [])
        self.queue = [{"id": "c1", "kind": "post", "status": "waiting", "created_at": "2026-11-26T09:00:00"},
                      {"id": "d1", "kind": "design", "status": "waiting", "created_at": "2026-11-26T09:00:00"},
                      self.item(1, status="rejected")]
        self.assertEqual(bp.recent_work(27, "he"), [])                   # copy, a design under review, a rejected file
        html = _src("dashboard", "client", "index.html")
        self.assertIn("if (!work.length) return '';", html)
        self.assertNotIn("כך המותג שלכם נראה בפוסט אמיתי אצלנו", html + _src("core", "brand_fonts.py"))

    def test_the_last_four_real_posts_newest_first(self):
        for i, day in enumerate(("20", "21", "22", "23", "24"), 1):
            self.queue += [self.item(n, post=f"p{i}", at=f"2026-11-{day}T10:00:0{n}") for n in (2, 1, 3)]
        work = bp.recent_work(27, "he")
        self.assertEqual([w["id"] for w in work], ["p5-1", "p4-1", "p3-1", "p2-1"])     # newest first, the cover slide
        self.assertEqual((work[0]["files"], work[0]["title"], work[0]["made_at"]), (3, "Launch", "2026-11-24"))
        self.assertEqual((work[0]["colours"], work[0]["off_palette"]), ("black + red", False))

    def test_a_post_drawn_outside_the_palette_says_so(self):
        off = self.item(1, of=1)
        off["manifest"]["palette"].update(variant="", roles={"bg": "#0E3B43", "fg": "#FFFFFF", "accent": "#E3262F",
                                                              "muted": "#A6A6A6"})
        old = self.item(1, post="p0", of=1, at="2026-11-01T10:00:00")
        del old["manifest"]["palette"]                                   # made before colours were recorded
        self.queue = [off, old]
        work = bp.recent_work(27, "he")
        self.assertTrue(work[0]["off_palette"])
        self.assertIn("#0E3B43", work[0]["colours"])
        self.assertEqual(work[1]["colours"], "")                         # nothing recorded = nothing claimed


class ItIsTheBrief(Base):
    def test_client_27_keeps_its_two_audience_variants(self):
        v = bp.view(27, "he")["colors"]
        self.assertTrue(v["segmented"])
        self.assertEqual([(x["label"], x["segment"]) for x in v["variants"]],
                         [("black + red", "b2c"), ("black + gold", "b2b")])           # never flattened
        self.assertEqual(v["variants"][0]["roles"]["accent"], "#E3262F")
        self.assertEqual(v["variants"][1]["roles"]["accent"], "#C9A227")
        self.assertEqual(v["variants"][0]["segment_label"], "לקוחות פרטיים")
        self.assertTrue(v["provisional"])                                            # our reading, not their values

    def test_a_colour_change_reaches_the_renderer_and_only_that_variant(self):
        bp.save_colors(27, [{"label": "black + red", "roles": {"bg": "#0b0b0b", "fg": "#FFFFFF", "accent": "#d0021b", "muted": ""}}])
        self.assertEqual(brand_brief.roles_for(27, "black + red")["roles"]["accent"], "#D0021B")
        self.assertEqual(brand_brief.roles_for(27, "black + gold")["roles"]["accent"], "#C9A227")
        self.assertIn("muted", brand_brief.roles_for(27, "black + red")["neutral"])   # an empty role is a gap, not a guess
        cur = brand_brief.current(27)
        self.assertEqual((cur["by"], cur["kind"], cur["palette"]["status"]), ("client", "edited", "stated"))
        self.assertFalse(bp.view(27, "he")["colors"]["provisional"])
        with self.assertRaises(bp.BrandPageError) as ctx:
            bp.save_colors(27, [{"label": "black + red", "roles": {"bg": "red"}}])
        self.assertEqual(ctx.exception.code, "bad_color")

    def test_tone_and_typefaces_are_written_into_the_text_the_agents_load(self):
        before = brand_brief.current(27)["text"]
        bp.save_tone(27, ["נועז", "שובב"], ["הנחות", "מבצע"])
        bp.save_typefaces(27, "Rubik", "Assistant")
        text = brand_brief.current(27)["text"]
        self.assertRegex(text, r"- tone_attributes: נועז, שובב \[stated — the client, branding page, \d{4}-\d\d-\d\d\]")
        self.assertRegex(text, r"- avoid: הנחות, מבצע \[stated")
        self.assertRegex(text, r"- typefaces: headline: Rubik; body: Assistant \[stated")
        self.assertNotIn("bold, playful, confident", text)                           # replaced, not duplicated
        self.assertIn("typefaces, PROPOSED", text)                                   # the rest of the brief is untouched
        self.assertIn("- emoji_policy: sparing", text)
        self.assertEqual(len(text.splitlines()), len(before.splitlines()) - 3)       # two wrapped fields became one line each
        self.assertIn("נועז, שובב", post_brief.load_context(27)["brand"])              # what both agents read
        self.assertEqual(post_brief.load_context(27)["brand_facts"]["lead_time_days"], (15, 25))
        v = bp.view(27, "he")
        self.assertEqual((v["tone"]["says"], v["tone"]["not_says"]), (["נועז", "שובב"], ["הנחות", "מבצע"]))
        self.assertEqual((v["typefaces"]["headline"], v["typefaces"]["body"]), ("Rubik", "Assistant"))

    def test_a_client_with_no_brief_starts_one_and_the_system_leads(self):
        v = bp.view(555, "he")
        self.assertTrue(v["lead"])                                                   # no branding at all: we lead
        self.assertEqual(v["assessment"], {})                                        # and never a critique there
        bp.save_tone(555, ["חם"], [])
        cur = brand_brief.current(555)
        self.assertEqual(cur["status"], "draft")
        self.assertIn("- tone_attributes: חם [stated", cur["text"])
        bp.save_colors(555, [{"label": "default", "roles": {"bg": "#FFFFFF", "fg": "#111111", "accent": "#0055AA", "muted": "#777777"}}])
        self.assertEqual(brand_brief.roles_for(555, "default")["roles"]["accent"], "#0055AA")
        self.assertFalse(bp.view(555, "he")["lead"])

    def test_set_field_moves_nothing_else(self):
        body = "# b\n\n## voice\n\n- a: 1\n- tone_attributes: x,\n  y [observed]\n- z: 2\n\n## other\n- k: v\n"
        out = bp.set_field(body, "voice", "tone_attributes", ["- tone_attributes: n"])
        self.assertEqual(out, "# b\n\n## voice\n\n- a: 1\n- tone_attributes: n\n- z: 2\n\n## other\n- k: v\n")
        self.assertEqual(bp.set_field(body, "voice", "new", ["- new: 1"]),
                         body.replace("- z: 2\n", "- z: 2\n- new: 1\n"))
        self.assertTrue(bp.set_field(body, "assets", "logo_files", ["- logo_files: x"]).endswith("## assets\n\n- logo_files: x\n"))
        self.assertEqual(bp.read_field(body, "voice", "tone_attributes"), "x, y")


@unittest.skipUnless(bp._measure(_png(10, 10), "png"), "Pillow is not installed")
class TheClientsFiles(Base):
    def test_what_is_uploaded_is_accepted_as_it_is(self):
        row = bp.upload(27, "full", "My Logo!.png", _png(240, 120, alpha=False))      # small and opaque: still accepted
        self.assertEqual((row["version"], row["width"], row["alpha"], row["name"]), ("full", 240, False, "My Logo.png"))
        bp.upload(27, "on_dark", "white.svg", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>')
        bp.upload(27, "anything-else", "card.pdf", b"%PDF-1.7 ...")
        v = bp.view(27, "he")
        self.assertEqual(sorted(v["logos"]), ["full", "on_dark"])
        self.assertEqual([a["format"] for a in v["assets"]], ["pdf"])
        text = brand_brief.current(27)["text"]                                       # the agents see the files by id
        self.assertIn("- logo_files: the client's own, uploaded on the branding page — use as they are [stated", text)
        self.assertIn("  - full: file_id file1 (My Logo.png)", text)
        self.assertIn("  - on_dark: file_id file2 (white.svg)", text)
        self.assertIn("  - existing asset: file_id file3 (card.pdf)", text)
        self.assertEqual(bp.file_bytes(27, "file2")[1], "image/svg+xml")
        src = _src("core", "brand_page.py")
        for word in ("low_quality", "rejected", "not good enough"):
            self.assertNotIn(word, src)

    def test_only_what_a_file_system_needs_is_refused(self):
        for content, code in ((b"", "empty"), (b"GIF89a....", "bad_format"), (b"MZ\x90\x00", "bad_format")):
            with self.assertRaises(bp.BrandPageError) as ctx:
                bp.upload(27, "full", "x", content)
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(self.drive, {})

    def test_a_newer_file_of_a_version_is_the_current_one_and_delete_updates_the_brief(self):
        bp.upload(27, "full", "old.png", _png())
        bp.upload(27, "full", "new.png", _png())
        v = bp.view(27, "he")
        self.assertEqual((v["logos"]["full"]["name"], [e["name"] for e in v["earlier"]]), ("new.png", ["old.png"]))
        bp.delete(27, "file2")
        self.assertIn("  - full: file_id file1 (old.png)", brand_brief.current(27)["text"])
        with self.assertRaises(bp.BrandPageError):
            bp.delete(27, "someone-elses-file")
        with self.assertRaises(bp.BrandPageError):
            bp.file_bytes(27, "someone-elses-file")

    def test_one_friendly_note_then_never_again(self):
        self.assertEqual(bp.view(27, "he")["assessment"], {})                        # a provisional palette is OUR reading
        bp.upload(27, "full", "logo.png", _png(240, 120, alpha=False))
        note = bp.view(27, "he")["assessment"]
        self.assertEqual(note["codes"], ["small_logo", "no_transparent"])
        self.assertIn("יש לכם לוגו משלכם", note["opens"])                               # opens with what works
        self.assertIn("240", note["points"][0])                                      # specific, measured
        self.assertIn("חלק קטן", note["closes"])                                       # the cost reality
        bp.dismiss_assessment(27)
        self.assertEqual(bp.view(27, "he")["assessment"], {})
        bp.save_colors(27, [{"label": "black + red", "roles": {"bg": "#101010", "fg": "#1A1A1A", "accent": "#151515"}}])
        self.assertEqual(bp.view(27, "he")["assessment"], {})                        # a NEW weakness does not bring it back
        self.assertEqual(self.rows[27]["page"]["assessment_dismissed_at"][:4], "2026"[:4])

    def test_colours_are_judged_only_when_stated_and_strong_branding_gets_no_note(self):
        weak = [{"label": "a", "roles": {"bg": "#FFFFFF", "fg": "#EEEEEE", "accent": "#FFEE88", "muted": ""}}]
        self.assertEqual(bp.weaknesses(weak, {}, stated=False), [])
        self.assertEqual([c for c, _ in bp.weaknesses(weak, {}, stated=True)], ["text_contrast"])
        good = [{"label": "a", "roles": {"bg": "#0B0B0B", "fg": "#FFFFFF", "accent": "#E3262F", "muted": "#A6A6A6"}}]
        logo = {"full": {"format": "png", "width": 1600, "alpha": True}}
        self.assertEqual(bp.assessment(good, logo, True, False, "he"), {})
        self.assertEqual(bp.assessment(weak, {}, False, False, "he"), {})            # nothing of theirs: no note
        self.assertGreater(bp.contrast("#000000", "#FFFFFF"), 20.9)
        for table in list(bp.NOTE.values()) + list(bp.ERRORS.values()) + list(bp.SEGMENTS.values()):
            self.assertEqual(sorted(table), ["ar", "en", "fr", "he", "ru"])


class ThePage(unittest.TestCase):
    html = _src("dashboard", "client", "index.html")
    api = _src("core", "api_server.py")

    def test_the_section_exists_and_is_wired(self):
        self.assertIn('data-view="brand" onclick="showView(\'brand\')"', self.html)
        self.assertIn('<div class="view" id="viewBrand">', self.html)
        self.assertIn("if (name === 'brand') loadBrandPage();", self.html)
        self.assertIn("brUI.variants.map(v => {", self.html)                         # one block per audience variant
        for route in ("", "/colors", "/typefaces/approve", "/typefaces/other", "/tone", "/upload", "/file/{file_id}/delete", "/file/{file_id}",
                      "/assessment/dismiss"):
            self.assertIn(f'"/api/client/brand-identity{route}")', self.api)
        self.assertIn('"client_brand_identity"', _src("core", "admin_view.py"))       # staff see it read-only

    def test_it_writes_through_the_brief_and_keeps_no_store_of_its_own(self):
        src = _src("core", "brand_page.py")
        self.assertIn('brand_brief.save_edit(client_id, new_text=new_text, new_palette=new_palette, by="client")', src)
        self.assertNotIn("app_settings", src)
        self.assertNotIn(".table(", src)


if __name__ == "__main__":
    unittest.main()
