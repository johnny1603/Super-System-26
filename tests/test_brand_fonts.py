"""Brand fonts in the renderer (2026-11-26, docs/FONTS.md).

What must stay true:
- the Hebrew library is PINNED (assets/fonts/hebrew-fonts.lock.json): every
  family with the whole Hebrew alphabet, every file with its URL and SHA-256,
  and the build installs it before the code is copied;
- weights are real files: nearest real weight, never a synthetic bold; a
  family without a bold says so and names the nearest family that has one;
- a client's own font is checked on upload and the report says, in plain
  words, what it has: Hebrew or Latin only, the weights, a variable font made
  into static weights, the rights confirmation as a dated LOG (never a gate);
- a font without Hebrew is never saved as the brand font without its Hebrew
  pairing; with one, Hebrew runs draw in the pairing and Latin runs in the
  brand font — per RUN, never per glyph;
- the render manifest names the font that drew, and a brand font that did
  not draw is a line on the card;
- the copy budget is measured with the client's font and MOVES when the
  client changes it;
- every picture on the branding page comes from the slide renderer.

Run from the repo root:  python tests/test_brand_fonts.py -v
"""
import copy
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    from core import drive_service                                              # noqa: E402
except ImportError:                       # no HTTP client on a bare test machine: Drive is faked below anyway
    import core                                                                 # noqa: E402
    drive_service = types.ModuleType("core.drive_service")
    for _name in ("list_files", "upload_bytes", "delete_file", "download_file"):
        setattr(drive_service, _name, None)
    sys.modules["core.drive_service"] = core.drive_service = drive_service

from core import brand_brief, brand_fonts as bf, brand_page as bp, copy_budget as cb     # noqa: E402
from core import font_library as fl, render_manifest as rm, slide_renderer as sr        # noqa: E402
from core import slide_spec, slide_templates                                             # noqa: E402


class _Settings:
    """app_settings in memory: just enough of the query builder for slide_templates."""

    def __init__(self):
        self.rows, self._key, self._value = {}, None, None

    def table(self, _name):
        self._key = self._value = None
        return self

    def select(self, *_a):
        return self

    def eq(self, _column, value):
        self._key = value
        return self

    def limit(self, _n):
        return self

    def upsert(self, row, **_kw):
        self._value = row
        return self

    def execute(self):
        if self._value is not None:
            self.rows[self._value["key"]] = self._value
            return types.SimpleNamespace(data=[self._value])
        return types.SimpleNamespace(data=[self.rows[self._key]] if self._key in self.rows else [])

RAQM = sr.raqm_status()["available"]
NEEDS_RAQM = unittest.skipUnless(RAQM or os.environ.get("CI"), "Pillow + raqm not installed here")
try:
    from fontTools.ttLib import TTFont
except ImportError:
    TTFont = None
NEEDS_FONTTOOLS = unittest.skipUnless(TTFont or os.environ.get("CI"), "fonttools not installed here")
RUBIK = os.path.join(ROOT, "assets", "fonts", "Rubik[wght].ttf")
ASSISTANT = os.path.join(ROOT, "assets", "fonts", "Assistant[wght].ttf")
LATIN = [[0x20, 0x7E], [0xA0, 0xFF]]
ROLES = {"bg": "#FFFFFF", "fg": "#111111", "accent": "#C8102E", "muted": "#555555"}


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def record(family: str, path: str, weights=(400,), hebrew=True, ranges=None) -> dict:
    """A stored client font whose file is one of the repo's own fonts."""
    data = _bytes(path)
    return {"id": bf.slug(family), "family": family, "hebrew": hebrew, "latin": True,
            "missing_hebrew": "" if hebrew else fl.HEBREW_LETTERS, "ranges": ranges,
            "weights": {str(w): {"file_id": f"font:{os.path.basename(path)}", "sha256": hashlib.sha256(data).hexdigest(),
                                 "ext": "ttf", "bytes": len(data), "name": f"{family}-{w}.ttf"} for w in weights},
            "rights": {"confirmed": False}, "updated_at": "2026-11-26T10:00:00+00:00"}


class Base(unittest.TestCase):
    """The brief row, the client's Drive and the font cache, all in memory / a temp folder."""

    def setUp(self):
        self.rows, self.drive, self.n = {}, {}, 0
        self.tmp = tempfile.mkdtemp(prefix="uallak-fonts-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = {(m, n): getattr(m, n) for m, n in (
            (brand_brief, "_read_row"), (brand_brief, "_write_row"), (bp, "_folder"), (bp, "_alert"),
            (bf, "cache_dir"), (bf, "_store"), (bf, "_remove"), (bf, "_download"), (slide_templates, "_db"),
            (drive_service, "list_files"))}
        self.addCleanup(lambda: [setattr(m, n, v) for (m, n), v in saved.items()])
        brand_brief._cache.clear()
        brand_brief._read_row = lambda cid: copy.deepcopy(self.rows.get(int(cid), {}))
        brand_brief._write_row = self._write
        bp._folder = lambda client_id: "brand-folder"
        bp._alert = lambda lines: None
        bf.cache_dir = lambda: self.tmp
        bf._store = self._store
        bf._remove = lambda client_id, file_id: self.drive.pop(file_id, None)
        bf._download = self._download
        settings = _Settings()
        slide_templates._db = lambda: settings
        drive_service.list_files = lambda folder, fields="": []
        cb.invalidate(27)
        self.addCleanup(cb.invalidate, 27)

    def _write(self, cid, value):
        self.rows[int(cid)] = copy.deepcopy(value)
        brand_brief._cache.pop(int(cid), None)

    def _store(self, client_id, name, content, ext, props):
        self.n += 1
        fid = f"file{self.n}"
        self.drive[fid] = {"id": fid, "name": name, "content": content, "props": props}
        return {"id": fid, "name": name}

    def _download(self, client_id, file_id):
        if file_id.startswith("font:"):
            return _bytes(os.path.join(ROOT, "assets", "fonts", file_id[5:]))
        return self.drive[file_id]["content"]

    def own(self, *records):
        fonts = bf.uploads(27)
        fonts.update({r["id"]: r for r in records})
        brand_brief.set_page_state(27, fonts=fonts)

    def choose(self, headline="", body="", hebrew=""):
        return bp.save_typefaces(27, headline, body, hebrew)


# ─── Part 1: the library ─────────────────────────────────────────────────────

class Library(unittest.TestCase):
    def test_the_pinned_set_is_whole_and_every_file_is_pinned_by_hash(self):
        lock = fl.lock()
        families = lock["families"]
        self.assertGreaterEqual(len(families), 50)
        self.assertEqual(len(families), lock["count"])
        self.assertIn("hebrew", lock["source"])
        names = [f["family"] for f in families]
        self.assertEqual(len(names), len({fl.norm(n) for n in names}))
        for must in ("Heebo", "Frank Ruhl Libre", "Secular One", "Alef"):
            self.assertIn(must, names)
        seen = set()
        for f in families:
            self.assertIn(f["category"], fl.CATEGORIES, f["family"])
            self.assertEqual(sorted(f["weights"]), sorted(x["weight"] for x in f["files"]), f["family"])
            self.assertTrue(f["ranges"], f["family"])
            face = fl.face(f["family"])
            self.assertTrue(face.covers(fl.HEBREW_LETTERS), f"{f['family']} lacks Hebrew letters")
            for x in f["files"]:
                self.assertRegex(x["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(x["url"].startswith("https://fonts.gstatic.com/"), x["url"])
                self.assertNotIn(x["file"], seen)
                self.assertRegex(x["file"], r"^[A-Za-z0-9._-]+\.(ttf|otf)$")
                seen.add(x["file"])

    def test_the_picker_is_grouped_by_category(self):
        groups = fl.grouped()
        self.assertEqual([g["category"] for g in groups][:4], ["sans", "serif", "display", "handwriting"])
        self.assertEqual(sum(len(g["families"]) for g in groups), len(fl.lock()["families"]))

    def test_a_weight_is_a_real_file_and_never_made_up(self):
        thin = next(f for f in fl.catalog() if not f["has_bold"])
        face = fl.face(thin["family"])
        self.assertFalse(face.has_bold)
        self.assertEqual(face.weight_for(800), max(face.weights))       # the heaviest REAL weight
        heebo = fl.face("heebo")
        self.assertEqual(heebo.family, "Heebo")
        self.assertIn(heebo.weight_for(700), heebo.weights)
        self.assertEqual(fl.bundled("Rubik").weight_for(800), 800)      # inside a variable axis: itself
        # the renderer has no way to thicken an outline
        src = _src("core", "slide_renderer.py")
        for word in ("stroke_width", "embolden", "ImageFilter.MaxFilter", "FT_Outline"):
            self.assertNotIn(word, src)

    def test_a_family_without_bold_names_the_nearest_one_that_has_it(self):
        lock = {fl.norm(f["family"]): f for f in fl.lock()["families"]}
        some = 0
        for f in fl.catalog():
            if f["has_bold"]:
                continue
            near = fl.nearest_with_bold(f["family"])
            if not near:
                continue
            some += 1
            self.assertGreaterEqual(max(lock[fl.norm(near)]["weights"]), 700)
            self.assertEqual(lock[fl.norm(near)]["category"], f["category"])
        self.assertGreater(some, 5)
        self.assertEqual(fl.nearest_with_bold("No Such Font"), "")

    def test_the_build_installs_the_pinned_set_before_the_code(self):
        docker = _src("Dockerfile")
        install = docker.index("fetch_hebrew_fonts.py --install")
        self.assertLess(docker.index("COPY assets/fonts/hebrew-fonts.lock.json"), install)
        self.assertLess(install, docker.rindex("COPY . ."))
        self.assertIn("assets/fonts/library/", _src(".dockerignore"))
        self.assertIn("assets/fonts/library/", _src(".gitignore"))
        script = _src("scripts", "fetch_hebrew_fonts.py")
        self.assertIn("sha256", script)
        self.assertIn("sys.exit(1)", script.replace("return 1", "sys.exit(1)"))
        req = _src("requirements.txt")
        self.assertIn("fonttools==", req)
        self.assertIn("brotli==", req)

    def test_the_final_image_is_checked_against_the_lock_and_a_mismatch_fails_the_build(self):
        docker = _src("Dockerfile")
        check = "RUN python scripts/fetch_hebrew_fonts.py --check --dest /app/assets/fonts/library"
        self.assertIn(check, docker)
        self.assertGreater(docker.index(check), docker.rindex("COPY . ."))      # the image as it ships, not a stage of it
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        self.addCleanup(sys.path.remove, os.path.join(ROOT, "scripts"))
        import fetch_hebrew_fonts as fetch
        dest = tempfile.mkdtemp(prefix="uallak-fontlib-test-")
        self.addCleanup(shutil.rmtree, dest, True)
        data = {"A-400.ttf": b"font a", "A-700.ttf": b"font a bold", "B-400.ttf": b"font b"}
        entry = lambda name, weight: {"weight": weight, "file": name, "url": f"https://fonts.gstatic.com/{name}",   # noqa: E731
                                      "sha256": hashlib.sha256(data[name]).hexdigest()}
        lock = {"count": 2, "families": [{"family": "A", "files": [entry("A-400.ttf", 400), entry("A-700.ttf", 700)]},
                                         {"family": "B", "files": [entry("B-400.ttf", 400)]}]}
        saved = (fetch.load_lock, fetch._get, sys.stdout)
        self.addCleanup(lambda: (setattr(fetch, "load_lock", saved[0]), setattr(fetch, "_get", saved[1]),
                                 setattr(sys, "stdout", saved[2])))
        said = sys.stdout = io.StringIO()
        fetch.load_lock = lambda: copy.deepcopy(lock)
        self.assertEqual(fetch.check(dest), 1)                           # an image with no fonts at all
        self.assertIn("FONT LIBRARY CHECK FAILED", said.getvalue())
        for name, content in data.items():
            with open(os.path.join(dest, name), "wb") as f:
                f.write(content)
        self.assertEqual(fetch.check(dest), 0)
        with open(os.path.join(dest, "B-400.ttf"), "wb") as f:           # a different file under a pinned name
            f.write(b"not the pinned file")
        self.assertEqual(fetch.check(dest), 1)
        os.remove(os.path.join(dest, "B-400.ttf"))                       # a pinned file gone
        self.assertEqual(fetch.check(dest), 1)
        with open(os.path.join(dest, "B-400.ttf"), "wb") as f:
            f.write(data["B-400.ttf"])
        with open(os.path.join(dest, "Stray-400.ttf"), "wb") as f:       # a font nobody pinned
            f.write(b"stray")
        self.assertEqual(fetch.check(dest), 1)
        os.remove(os.path.join(dest, "Stray-400.ttf"))
        self.assertEqual(fetch.check(dest), 0)
        # a lock that pins nothing (empty, cut off) is a failure, never "nothing missing"
        for broken in ({}, {"count": 0, "families": []}, {**lock, "count": 5},
                       {"count": 1, "families": [{"family": "A", "files": []}]}):
            fetch.load_lock = lambda broken=broken: copy.deepcopy(broken)
            self.assertEqual(fetch.check(dest), 1, broken)
            self.assertEqual(fetch.install(dest), 1, broken)
        def unreadable():
            raise ValueError("cut off")
        fetch.load_lock = unreadable
        self.assertEqual(fetch.check(dest), 1)
        self.assertEqual(fetch.install(dest), 1)
        # the install: a download whose hash is not the pinned one fails, and a dropped one gets one more pass
        fetch.load_lock = lambda: copy.deepcopy(lock)
        shutil.rmtree(dest)
        fetch._get = lambda url: b"something else"
        self.assertEqual(fetch.install(dest), 1)
        self.assertIn("FONT LIBRARY BUILD FAILED", said.getvalue())
        self.assertEqual(os.listdir(dest), [])                           # nothing unverified is ever written
        dropped = set()
        def flaky(url):
            name = url.rsplit("/", 1)[1]
            if name not in dropped:
                dropped.add(name)
                raise RuntimeError("could not fetch")
            return data[name]
        fetch._get = flaky
        self.assertEqual(fetch.install(dest), 0)
        self.assertEqual(fetch.check(dest), 0)
        fetch._get = lambda url: (_ for _ in ()).throw(RuntimeError("could not fetch"))
        shutil.rmtree(dest)
        self.assertEqual(fetch.install(dest), 1)                         # still down on the second pass: the build fails

    def test_a_missing_library_is_said_and_never_hidden(self):
        saved = os.environ.get("FONT_LIBRARY_DIR")
        os.environ["FONT_LIBRARY_DIR"] = os.path.join(tempfile.gettempdir(), "uallak-no-such-library")
        self.addCleanup(lambda: os.environ.pop("FONT_LIBRARY_DIR", None) if saved is None
                        else os.environ.__setitem__("FONT_LIBRARY_DIR", saved))
        status = fl.status()
        self.assertEqual(status["installed"] + len(status["missing"]), status["pinned"])
        self.assertLessEqual(status["installed"], len(fl.BUNDLED))      # only what the repo itself carries
        self.assertIn("Heebo", status["missing"])
        with self.assertRaises(fl.FontUnavailable):
            fl.face("Heebo").path(400)
        self.assertIn("FONT LIBRARY INCOMPLETE", _src("core", "slide_renderer.py"))


# ─── Part 3.1: runs ──────────────────────────────────────────────────────────

class Runs(unittest.TestCase):
    def test_a_line_is_split_into_direction_runs_not_glyphs(self):
        self.assertEqual(sr.split_runs("שלום עולם", True), [("שלום עולם", "base")])
        got = sr.split_runs("המותג Nike Air כאן", True)
        self.assertEqual("".join(seg for seg, _ in got), "המותג Nike Air כאן")
        self.assertEqual([k for _, k in got], ["base", "ltr", "base"])
        self.assertEqual(got[1][0].strip(), "Nike Air")                 # the space between two Latin words stays inside
        self.assertEqual(sr.split_runs("Hello world", False), [("Hello world", "ltr")])
        digits = sr.split_runs("רק 50 שקלים", True)
        self.assertEqual([k for _, k in digits], ["base"])               # a number after Hebrew is not a Latin run

    def test_the_slot_of_each_role(self):
        self.assertEqual([sr.slot_of(r) for r in ("headline", "kicker", "body", "cta")],
                         ["headline", "headline", "body", "body"])


@NEEDS_RAQM
class Fallback(Base):
    def test_hebrew_runs_in_the_pairing_latin_runs_in_the_brand_font(self):
        self.own(record("Acme Sans", RUBIK, hebrew=False, ranges=LATIN))
        with self.assertRaises(bp.BrandPageError) as e:                 # never accepted silently
            self.choose("Acme Sans", "")
        self.assertEqual(e.exception.code, "needs_pairing")
        self.choose("Acme Sans", "", "Assistant")
        self.assertIn("- typefaces: headline: Acme Sans; body: —; hebrew: Assistant [stated", brand_brief.current(27)["text"])
        fonts = bf.resolve(27)
        self.assertEqual([f.family for f in fonts["chains"]["headline"]], ["Acme Sans", "Assistant"])
        block = {"role": "headline", "font_family": "Rubik", "size": 80, "weight": 700}
        kind = sr._type_for(block, fonts)
        runs = kind.runs("המותג Nike Air כאן", True)
        drew = [(kind.entries[r["entry"]]["face"].family, r["script"]) for r in runs]
        self.assertEqual(drew, [("Assistant", "hebrew"), ("Acme Sans", "latin"), ("Assistant", "hebrew")])
        self.assertEqual("".join(r["text"] for r in runs), "המותג Nike Air כאן")
        rows, fell = sr.font_report(kind, ["המותג Nike Air כאן"], True, block, fonts)
        self.assertEqual({(r["font"], r["script"]) for r in rows}, {("Assistant", "hebrew"), ("Acme Sans", "latin")})
        self.assertEqual(fell, [])                                      # the pairing is the client's choice, not a fallback

    def test_no_pairing_means_the_fallback_is_named(self):
        self.own(record("Acme Sans", RUBIK, hebrew=False, ranges=LATIN))
        fonts = bf.resolve(27, {"headline": "Acme Sans"})
        block = {"role": "headline", "font_family": "Assistant", "size": 80, "weight": 700}
        kind = sr._type_for(block, fonts)
        _rows, fell = sr.font_report(kind, ["שלום לכולם"], True, block, fonts)
        self.assertEqual([(f["wanted"], f["drew"], f["reason"]) for f in fell], [("Acme Sans", "Assistant", "no_hebrew_pairing")])

    def test_the_manifest_names_the_font_and_the_card_says_a_fallback(self):
        spec = slide_spec.starter("b2c", "default")
        plain = {}
        sr.render_slide(spec, ROLES, "מתנה שנשארת", "זוג מעוצב אחד", 1, 1, "", manifest=plain, fonts=bf.resolve(27))
        text = [l for l in plain["layers"] if l.get("type") == "text"]
        self.assertTrue(text)
        self.assertTrue(all(l["font"] in ("Rubik", "Assistant") and l["weight"] for l in text))
        self.assertEqual(rm.font_fallbacks(plain), [])
        self.assertEqual(rm.font_lines([plain], "he"), [])
        # the brief names a font we have no file for: the slide still renders, and says so
        self.choose("Totally Unknown Font", "")
        fonts = bf.resolve(27)
        self.assertEqual(fonts["issues"], {"headline": "unknown_family"})
        drawn = {}
        sr.render_slide(spec, ROLES, "מתנה שנשארת", "זוג מעוצב אחד", 1, 1, "", manifest=drawn, fonts=fonts)
        self.assertEqual(drawn["fonts"]["wanted"]["headline"], "Totally Unknown Font")
        fell = rm.font_fallbacks(drawn)
        self.assertTrue(fell and all(f["wanted"] == "Totally Unknown Font" for f in fell))
        lines = rm.font_lines([drawn], "he")
        self.assertEqual(len(lines), 1)
        self.assertIn("Totally Unknown Font", lines[0])
        self.assertIn("Rubik", lines[0])
        self.assertIn("fonts", rm.facts(drawn))

    def test_the_client_font_draws_and_is_cached_from_storage_by_hash(self):
        self.own(record("House Face", ASSISTANT, weights=(400, 700)))
        self.choose("House Face", "House Face")
        spec = slide_spec.starter("b2c", "default")
        drawn = {}
        sr.render_slide(spec, ROLES, "מתנה שנשארת", "זוג מעוצב אחד", 1, 1, "", manifest=drawn, fonts=bf.resolve(27))
        self.assertEqual(rm.drawn_fonts(drawn), ["House Face"])
        cached = [n for _d, _s, names in os.walk(self.tmp) for n in names]
        self.assertEqual(len(cached), 1)                                # one file, named by its hash
        self.assertTrue(cached[0].startswith(hashlib.sha256(_bytes(ASSISTANT)).hexdigest()[:40]))
        # a stored file that is not the one we checked is refused, not drawn
        bad = record("Swapped", ASSISTANT)
        bad["weights"]["400"]["sha256"] = "0" * 64
        self.own(bad)
        with self.assertRaises(fl.FontUnavailable):
            bf.find(27, "Swapped")[0].path(400)

    def test_every_render_outside_the_renderer_passes_the_client_fonts(self):
        for name in sorted(os.listdir(os.path.join(ROOT, "core"))):
            if not name.endswith(".py") or name == "slide_renderer.py":
                continue
            src = _src("core", name)
            for m in re.finditer(r"slide_renderer\.(render_slide|render_set)\(", src):
                depth, i = 1, m.end()
                while depth and i < len(src):
                    depth += {"(": 1, ")": -1}.get(src[i], 0)
                    i += 1
                self.assertIn("fonts=", src[m.end():i], f"core/{name}: {m.group(1)} without the client's fonts")


# ─── Part 3.3: the copy budget moves with the font ───────────────────────────

@NEEDS_RAQM
class Budget(Base):
    def test_a_font_change_moves_the_budget_and_drops_the_cache(self):
        before = cb.budget(27, {})
        self.assertEqual(cb.budget(27, {})["fonts"], before["fonts"])
        self.choose("Assistant", "Assistant")                           # Rubik starter → a narrower face
        after = cb.budget(27, {})
        self.assertNotEqual(after["fonts"], before["fonts"])
        head = lambda b: b["all"]["copy_headline"]                      # noqa: E731
        self.assertEqual(head(after)["font"].split()[0], "Assistant")
        self.assertNotEqual(head(after)["max_chars"], head(before)["max_chars"],
                            "a font change that does not move the budgets is a bug")
        self.assertGreater(head(after)["max_chars"], head(before)["max_chars"])    # Assistant is narrower than Rubik
        self.choose("", "")
        self.assertEqual(head(cb.budget(27, {}))["max_chars"], head(before)["max_chars"])

    def test_the_cache_is_per_client_and_font(self):
        cb.budget(27, {})
        keys = [k for k in cb._BUDGETS if k[0] == 27]
        self.assertTrue(keys)
        sig = bf.signature(27)
        self.assertTrue(all(k[1] == sig for k in keys))
        cb.invalidate(27)
        self.assertFalse([k for k in cb._BUDGETS if k[0] == 27])


# ─── Part 2: a client's own font ─────────────────────────────────────────────

def _static(path: str, weight: int = 400, hebrew: bool = True, family: str = "", flavor: str = None) -> bytes:
    """A static font cut from a bundled variable file (fonttools)."""
    from fontTools.varLib import instancer
    font = TTFont(path)
    font = instancer.instantiateVariableFont(font, {"wght": weight}, inplace=True)
    font["OS/2"].usWeightClass = weight
    if not hebrew:
        for table in font["cmap"].tables:
            for code in [c for c in table.cmap if 0x0590 <= c <= 0x05FF or 0xFB1D <= c <= 0xFB4F]:
                del table.cmap[code]
    if family:
        for rec in font["name"].names:
            if rec.nameID in (1, 4, 16):
                rec.string = family
    font.flavor = flavor
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


@NEEDS_FONTTOOLS
class Upload(Base):
    def test_a_hebrew_font_is_kept_and_the_report_is_in_plain_words(self):
        got = bf.add_upload(27, "house.ttf", _static(RUBIK, 400, family="House Hebrew"), rights=True, lang="he")
        font, report = got["font"], got["report"]
        self.assertEqual(font["family"], "House Hebrew")
        self.assertTrue(report["hebrew"])
        self.assertFalse(report["latin_only"])
        self.assertEqual(report["weights"], [400])
        self.assertFalse(report["has_bold"])
        said = " ".join(report["lines"])
        self.assertIn("עברית", said)
        self.assertIn("400", said)                                      # one weight: what is missing is said
        self.assertTrue(report["rights_confirmed"])
        rec = bf.uploads(27)[font["id"]]
        self.assertRegex(rec["rights"]["at"], r"^2\d{3}-\d\d-\d\d")      # the confirmation, with its date
        self.assertEqual(len(self.drive), 1)                            # the file is in the client's storage
        stored = next(iter(self.drive.values()))
        self.assertEqual(stored["props"]["uallak_kind"], bf.KIND)
        # a second file of the family adds its weight
        bf.add_upload(27, "house-bold.ttf", _static(RUBIK, 700, family="House Hebrew"), lang="he")
        self.assertEqual(sorted(bf.uploads(27)[font["id"]]["weights"]), ["400", "700"])
        self.assertTrue(bf.find(27, "house hebrew")[0].has_bold)
        self.assertIn(font["id"], [f["id"] for f in bp.view(27, "he")["fonts"]["own"]])

    def test_a_font_without_hebrew_is_latin_only_and_is_said_exactly(self):
        got = bf.add_upload(27, "latin.otf", _static(RUBIK, 400, hebrew=False, family="Acme Latin"), lang="he")
        report = got["report"]
        self.assertFalse(report["hebrew"])
        self.assertTrue(report["latin_only"])
        self.assertTrue(report["needs_pairing"])
        self.assertIn("לטיני", report["lines"][0])
        self.assertFalse(report["rights_confirmed"])                    # no tick: still stored — a log, not a gate
        self.assertEqual(len(bf.uploads(27)), 1)
        with self.assertRaises(bp.BrandPageError) as e:
            self.choose("Acme Latin", "Acme Latin")
        self.assertEqual(e.exception.code, "needs_pairing")
        self.assertTrue(bp.error_text("needs_pairing", "he"))
        with self.assertRaises(bp.BrandPageError) as e:
            self.choose("Acme Latin", "", "Acme Latin")
        self.assertEqual(e.exception.code, "pairing_no_hebrew")
        self.choose("Acme Latin", "", "Rubik")                          # kept for the Latin, Hebrew paired
        face = bf.find(27, "Acme Latin")[0]
        self.assertFalse(face.covers("שלום"))
        self.assertTrue(face.covers("Hello 2026"))

    def test_woff2_becomes_a_plain_ttf(self):
        try:
            import brotli                                               # noqa: F401
        except ImportError:
            if not os.environ.get("CI"):
                self.skipTest("brotli not installed here")
            raise
        raw = _static(ASSISTANT, 400, family="Webby", flavor="woff2")
        self.assertEqual(raw[:4], b"wOF2")
        got = bf.add_upload(27, "webby.woff2", raw, lang="he")
        stored = next(iter(self.drive.values()))
        self.assertEqual(stored["content"][:4], b"\x00\x01\x00\x00")
        self.assertTrue(stored["name"].endswith(".ttf"))
        self.assertIn("WOFF2", " ".join(got["report"]["lines"]))

    def test_a_variable_font_becomes_real_static_weights(self):
        got = bf.add_upload(27, "Assistant[wght].ttf", _bytes(ASSISTANT), lang="he")
        weights = got["report"]["weights"]
        self.assertEqual(weights, [200, 300, 400, 500, 600, 700, 800])
        self.assertTrue(got["font"]["variable"])
        self.assertEqual(len(self.drive), len(weights))
        for stored in self.drive.values():
            self.assertNotIn("fvar", TTFont(io.BytesIO(stored["content"])))
        self.assertTrue(got["report"]["has_bold"])

    def test_what_is_not_a_font_is_refused_in_plain_words(self):
        for junk, code in ((b"", "bad_font"), (b"%PDF-1.7 not a font", "bad_font"),
                           (b"\x00\x01\x00\x00" + b"\x00" * 64, "font_unreadable")):
            with self.assertRaises(bf.FontError) as e:
                bf.add_upload(27, "x.ttf", junk)
            self.assertEqual(e.exception.code, code)
            self.assertTrue(bp.error_text(code, "he"))
        self.assertEqual(self.drive, {})

    def test_rights_are_logged_once_and_delete_frees_the_choice(self):
        got = bf.add_upload(27, "house.ttf", _static(RUBIK, 400, family="House Hebrew"), lang="he")
        fid = got["font"]["id"]
        first = bf.confirm_rights(27, fid)["rights"]
        self.assertTrue(first["confirmed"])
        self.assertEqual(bf.confirm_rights(27, fid)["rights"]["at"], first["at"])
        with self.assertRaises(bf.FontError):
            bf.confirm_rights(27, "no-such-font")
        self.assertTrue(bf.delete_upload(27, fid))
        self.assertEqual(bf.uploads(27), {})
        self.assertEqual(self.drive, {})


# ─── Part 3.4 + the page ─────────────────────────────────────────────────────

class Page(Base):
    def test_the_typefaces_line_keeps_its_shape(self):
        self.assertEqual(bf.parse_typefaces("headline: Heebo; body: Assistant"),
                         {"headline": "Heebo", "body": "Assistant", "hebrew": "", "note": ""})
        self.assertEqual(bf.parse_typefaces("headline: Acme; body: —; hebrew: Heebo")["hebrew"], "Heebo")
        self.assertEqual(bf.parse_typefaces("a geometric sans on the sign")["note"], "a geometric sans on the sign")
        self.choose("heebo", "frank ruhl libre")                        # the family's own spelling is what is saved
        view = bp.view(27, "he")
        self.assertEqual(view["typefaces"]["headline"], "Heebo")
        self.assertEqual(view["typefaces"]["body"], "Frank Ruhl Libre")
        self.assertEqual(view["fonts"]["chosen"], {"headline": "Heebo", "body": "Frank Ruhl Libre", "hebrew": ""})
        self.assertNotIn("hebrew:", brand_brief.current(27)["text"])

    def test_the_page_says_no_bold_and_offers_the_nearest(self):
        thin = next(f for f in fl.catalog() if not f["has_bold"] and fl.nearest_with_bold(f["family"]))
        self.choose(thin["family"], "")
        fonts = bp.view(27, "he")["fonts"]
        note = next(n for n in fonts["notes"] if n["code"] == "no_bold")
        self.assertEqual(note["suggest"], fl.nearest_with_bold(thin["family"]))
        self.assertIn(thin["family"], note["text"])
        self.assertIn(note["suggest"], note["text"])
        row = next(f for g in fonts["groups"] for f in g["families"] if f["family"] == thin["family"])
        self.assertEqual(row["suggest"], note["suggest"])
        self.assertEqual([g["category"] for g in fonts["groups"]][:4], ["sans", "serif", "display", "handwriting"])

    def test_every_picture_on_the_page_is_made_by_the_post_renderer(self):
        src = _src("core", "brand_fonts.py")
        self.assertIn("slide_renderer.render_slide(spec, roles, head, body", src)
        self.assertIn("slide_renderer.render_sample(", src)
        html = _src("dashboard", "client", "index.html")
        self.assertIn("/api/client/brand-identity/preview?", html)
        self.assertIn("/api/client/brand-identity/font-sample?", html)
        self.assertNotIn('class="br-preview"', html)                    # the CSS imitation is gone
        self.assertNotIn(".br-preview {", html)
        self.assertNotIn("@font-face", html[html.index("BRANDING PAGE"):html.index("AVATARS: pose libraries")])
        api = _src("core", "api_server.py")
        for route in ("/api/client/brand-identity/font/upload", "/api/client/brand-identity/font-sample",
                      "/api/client/brand-identity/preview", "/api/client/brand-identity/font/{font_id}/rights",
                      "/api/client/brand-identity/font/{font_id}/delete"):
            self.assertIn(f'"{route}"', api)

    @NEEDS_RAQM
    def test_the_preview_is_a_real_slide_in_the_chosen_fonts(self):
        variant = bp.view(27, "he")["colors"]["variants"][0]["label"]    # client 27: one palette per audience
        stored = copy.deepcopy(self.rows)                               # the brief as the page found it
        got = bf.preview_png(27, variant, {"headline": "Assistant", "body": "Assistant"}, {"bg": "#101010", "fg": "#FAFAFA"})
        self.assertEqual(got["png"][:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(got["fonts"], ["Assistant"])
        self.assertEqual(got["lines"], [])
        missing = bf.preview_png(27, variant, {"headline": "Totally Unknown Font"}, {}, "he")
        self.assertTrue(missing["lines"])
        self.assertEqual(self.rows, stored)                             # a preview saves nothing: not the fonts, not the colours
        self.assertEqual(bf.wanted(27), {"headline": "", "body": "", "hebrew": ""})
        sample = bf.sample_png(27, "Rubik", 700)
        self.assertEqual(sample[:8], b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(bf.FontError):
            bf.sample_png(27, "Totally Unknown Font")

    def test_the_rights_line_is_a_log_and_not_a_gate(self):
        src = _src("core", "brand_fonts.py")
        self.assertIn("never a gate", _src("core", "api_server.py"))
        body = src[src.index("def add_upload"):src.index("def confirm_rights")]
        self.assertNotIn('FontError("rights', body)
        for lang in ("he", "en", "fr", "ar", "ru"):
            self.assertTrue(bf.pick(bf.TEXT["rights_ask"], lang))
            self.assertTrue(bf.pick(bf.TEXT["rights_logged"], lang))


if __name__ == "__main__":
    unittest.main()
