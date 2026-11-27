"""The text-slide renderer (2026-11-23, docs/SLIDE-RENDERER.md).

Image models cannot write Hebrew, so text slides are DRAWN: real characters,
real fonts (assets/fonts, bundled — never fetched), Pillow with raqm text
shaping (FriBidi + HarfBuzz). Hebrew is drawn with direction="rtl" and
language="he", each line anchored to the right edge of its text box (centred
lines are placed by their right edge too). In-process, milliseconds, no
browser, no image model, no credits.

Input: a design spec (core/slide_spec, knowledge/slide-spec.schema.json) +
the palette variant's role colours + one slide's copy. Output: PNG bytes.

Guards — the render FAILS with a specific, English problem list (for the
agent to fix the SPEC); text is never clipped or shrunk:
  overflow        a word wider than its box, more lines than the box holds,
                  text outside the safe area (margins)
  overlap         two text blocks (or the counter) on top of each other
  headline_words  more words than guards.headline_max_words (default 10)
  contrast        WCAG ratio of the text colour against what is actually
                  under it (the composed background: photo + scrim, split,
                  gradient, accents), measured on the 5th-percentile pixel —
                  below 4.5 (3.0 for large text) fails
  hebrew_align    Hebrew aligned left
  weight / asset / bad_spec

CHARACTER (2026-11-24, core/agent_characters): an agent's transparent cut-out
(a pose from the library, loaded as "pose:<pose_id>") alpha-composited AFTER
the background and accent and BEFORE the logo and the text — the text is
always on top. Placement: x_pct = the figure's horizontal centre, % of the
canvas width measured from the RIGHT edge; y_pct = where its feet stand, % of
the height; scale_pct = its height, % of the canvas height; flip mirrors it;
shadow = a soft elliptical drop shadow under it (true = DEFAULT_SHADOW, or an
intensity 0-1, false = none). A figure past the canvas edge or touching a
text block is a WARNING (character_overflow / character_text_overlap, in the
`warnings` list the caller passes) — never a silent crop, never a failure.
A pose without transparent pixels fails (character_not_transparent).

startup_check() runs when the service starts: no raqm = a loud log line and
an admin alert, and every render raises RendererUnavailable.
"""
import io
import os
import re
import threading
import unicodedata
from collections import OrderedDict

try:
    from PIL import Image, ImageDraw, ImageFont, features
except ImportError:                     # a stdlib-only checkout (CI): every render refuses
    Image = ImageDraw = ImageFont = features = None

from core import font_library, slide_spec
from core.post_brief import HEBREW_RE

SERVICE_NAME = "slide_renderer"
FONTS_DIR = os.path.join(slide_spec.ROOT, "assets", "fonts")
FONT_FILES = {"Rubik": "Rubik[wght].ttf", "Assistant": "Assistant[wght].ttf"}
HEADLINE_MAX_WORDS = 10
CONTRAST_MIN = 4.5
CONTRAST_MIN_LARGE = 3.0
CONTRAST_PERCENTILE = 0.05
CONTRAST_SAMPLE = 64            # the measured region is reduced to at most 64 px a side
FONT_CACHE_MAX = 96
COUNTER_SIZE = 30
DEFAULT_SHADOW = 0.35
CHARACTER_DEFAULTS = {"x_pct": 75, "y_pct": 100, "scale_pct": 55, "flip": False, "shadow": True}
EDGES = ("top", "bottom", "right", "left")
CORNERS = ("top_right", "top_left", "bottom_right", "bottom_left")

_LOCK = threading.Lock()        # FreeType faces are not thread-safe; a render takes ms
_FONTS = OrderedDict()


class RendererUnavailable(RuntimeError):
    pass


class RenderError(ValueError):
    """problems = [{"code", "detail", "block"?, "slide"?}]."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(p["detail"] for p in self.problems[:6]))


# ─── raqm ────────────────────────────────────────────────────────────────────

def raqm_status() -> dict:
    if features is None:
        return {"available": False, "raqm": None, "fribidi": None, "harfbuzz": None, "pillow": False}
    try:
        ok = bool(features.check("raqm"))
    except Exception:
        ok = False
    out = {"available": ok, "pillow": True}
    for name in ("raqm", "fribidi", "harfbuzz"):
        try:
            out[name] = features.version(name)
        except Exception:
            out[name] = None
    return out


def require_raqm():
    if not raqm_status()["available"]:
        raise RendererUnavailable("Pillow reports raqm unavailable (needs libraqm + FriBidi + HarfBuzz; the "
                                  "Docker image installs libfribidi0) — Hebrew cannot be shaped, nothing is rendered")


def startup_check() -> dict:
    """Called once when the service starts. Never raises."""
    status = raqm_status()
    missing = [n for n, f in FONT_FILES.items() if not os.path.isfile(os.path.join(FONTS_DIR, f))]
    if not status["available"] or missing:
        line = (f"SLIDE RENDERER UNAVAILABLE: raqm={status['available']} (raqm {status.get('raqm')}, fribidi "
                f"{status.get('fribidi')}, harfbuzz {status.get('harfbuzz')}), missing fonts={missing} — every "
                f"text-slide render will fail until this is fixed")
        print(f"[{SERVICE_NAME}] {line}")
        try:
            from core.agent_base import agent_alert
            agent_alert(SERVICE_NAME, [line])
        except Exception:
            pass
    else:
        print(f"[{SERVICE_NAME}] raqm {status['raqm']} (fribidi {status['fribidi']}, harfbuzz {status['harfbuzz']}) ok")
    # The Hebrew font library is installed at BUILD time (scripts/fetch_hebrew_fonts.py,
    # which fails the build on a missing pinned family). An image without it is a broken
    # build: said loudly, and a client whose brand font is missing gets it on every card.
    library = font_library.status()
    if library["pinned"] and library["missing"]:
        line = (f"FONT LIBRARY INCOMPLETE: {library['installed']} of {library['pinned']} pinned Hebrew families "
                f"installed (missing: {', '.join(library['missing'][:8])}"
                f"{' …' if len(library['missing']) > 8 else ''}) — a client who chose one gets a fallback font, "
                f"flagged on the card, until the image is rebuilt")
        print(f"[{SERVICE_NAME}] {line}")
        if os.environ.get("K_SERVICE"):                # the deployed service only: a dev box has no library
            try:
                from core.agent_base import agent_alert
                agent_alert(SERVICE_NAME, [line])
            except Exception:
                pass
    else:
        print(f"[{SERVICE_NAME}] font library: {library['installed']} Hebrew families installed")
    return {**status, "missing_fonts": missing, "library": library}


# ─── Fonts and colours ───────────────────────────────────────────────────────

def _font(family: str, size: int, weight: int):
    """A BUNDLED family at an exact weight of its variable axis (ValueError
    outside it) — the spec's own families."""
    key = (family, int(size), int(weight))
    hit = _FONTS.get(key)
    if hit:
        _FONTS.move_to_end(key)
        return hit
    path = os.path.join(FONTS_DIR, FONT_FILES[family])
    font = ImageFont.truetype(path, int(size), layout_engine=ImageFont.Layout.RAQM)
    axis = font.get_variation_axes()[0]
    if not axis["minimum"] <= weight <= axis["maximum"]:
        raise ValueError(f"{family} supports weight {axis['minimum']}-{axis['maximum']}, not {weight}")
    font.set_variation_by_axes([int(weight)])
    _FONTS[key] = font
    while len(_FONTS) > FONT_CACHE_MAX:
        _FONTS.popitem(last=False)
    return font


def _load(face, size: int, weight: int) -> tuple:
    """(font, the weight that really draws) for any Face (core/font_library):
    a bundled variable file at the asked weight inside its axis, a library or
    client file at the family's NEAREST REAL weight. Bold is never synthesized
    — no stroke, no thickening: a family without a bold draws its own heaviest
    file and the manifest says which. FontUnavailable when the file can't be had."""
    drawn = face.weight_for(weight)
    if face.source == "bundled":
        return _font(face.family, size, drawn), drawn
    key = (face.key, int(size), drawn)
    hit = _FONTS.get(key)
    if hit:
        _FONTS.move_to_end(key)
        return hit, drawn
    path = face.path(drawn)
    try:
        font = ImageFont.truetype(path, int(size), layout_engine=ImageFont.Layout.RAQM)
    except OSError as e:
        raise font_library.FontUnavailable("unreadable", f"{face.family} {drawn}: {str(e)[:80]}")
    _FONTS[key] = font
    while len(_FONTS) > FONT_CACHE_MAX:
        _FONTS.popitem(last=False)
    return font, drawn


# ─── Which font draws which run ──────────────────────────────────────────────

def slot_of(role: str) -> str:
    """The brand's font slot a text block belongs to."""
    return "headline" if role in ("headline", "kicker") else "body"


def _bidi(ch: str) -> str:
    kind = unicodedata.bidirectional(ch)
    return "R" if kind in ("R", "AL") else kind if kind in ("L", "EN") else "N"


def split_runs(text: str, rtl: bool) -> list:
    """[(segment, "base" | "ltr")] in LOGICAL order — the direction runs of
    one line, the way the bidi algorithm sees them: in a right-to-left line a
    left-to-right run is a stretch of Latin letters with the digits that
    follow them and whatever lies between two of them; everything else
    (Hebrew, digits after Hebrew, punctuation and spaces at a boundary)
    belongs to the base direction. A left-to-right line is one run."""
    if not rtl:
        return [(text, "ltr")] if text else []
    kinds = [_bidi(ch) for ch in text]
    strong = "R"
    for i, k in enumerate(kinds):
        if k in ("L", "R"):
            strong = k
        elif k == "EN":
            kinds[i] = strong                      # a number continues the run it follows
    i, n = 0, len(kinds)
    while i < n:
        if kinds[i] != "N":
            i += 1
            continue
        j = i
        while j < n and kinds[j] == "N":
            j += 1
        both = (kinds[i - 1] if i else "R") == "L" and (kinds[j] if j < n else "R") == "L"
        kinds[i:j] = ["L" if both else "R"] * (j - i)
        i = j
    out, start = [], 0
    for i in range(1, n + 1):
        if i == n or kinds[i] != kinds[start]:
            out.append((text[start:i], "ltr" if kinds[start] == "L" else "base"))
            start = i
    return out


def _script(text: str) -> str:
    kinds = {_bidi(ch) for ch in text}
    return "hebrew" if "R" in kinds else "latin" if "L" in kinds else "neutral"


class _Type:
    """How ONE text block is set: its chain of faces (the client's brand font
    for the block's slot, their Hebrew pairing, the spec's own family, the
    bundled last resort) loaded at the block's size and weight, and the font
    of every RUN of a line. The fallback is per run, never per glyph: a WORD
    is drawn whole by the first face of the chain that has all its characters,
    and neighbouring words of one face are one run."""

    def __init__(self, chain: list, size: int, weight: int):
        self.entries, self.skipped, self._runs = [], [], {}
        for face in chain:
            if any(e["face"].key == face.key for e in self.entries):
                continue
            try:
                font, drawn = _load(face, size, weight)
            except font_library.FontUnavailable as e:
                self.skipped.append({"family": face.family, "reason": e.reason})
                continue
            self.entries.append({"face": face, "font": font, "weight": drawn})
        if not self.entries:
            raise RendererUnavailable("no font of the chain could be loaded, not even the bundled one")

    def _pick(self, word: str) -> int:
        for i, e in enumerate(self.entries):
            if e["face"].covers(word):
                return i
        letters = "".join(ch for ch in word if ch.isalpha())
        for i, e in enumerate(self.entries):
            if letters and e["face"].covers(letters):
                return i
        return len(self.entries) - 1

    def runs(self, text: str, rtl: bool) -> list:
        """[{"text", "entry", "dir", "script"}] in logical order."""
        key = (text, rtl)
        hit = self._runs.get(key)
        if hit is not None:
            return hit
        out = []
        for segment, kind in split_runs(text, rtl):
            direction = "rtl" if kind == "base" else "ltr"
            for piece in re.findall(r"\s*\S+\s*|\s+", segment):
                entry = self._pick(piece.strip()) if piece.strip() else (out[-1]["entry"] if out else 0)
                if out and out[-1]["entry"] == entry and out[-1]["dir"] == direction:
                    out[-1]["text"] += piece
                else:
                    out.append({"text": piece, "entry": entry, "dir": direction})
        if len({r["entry"] for r in out}) <= 1:
            # one face draws the whole line: ONE run, shaped and ordered by raqm as a whole
            out = [{"text": text, "entry": out[0]["entry"] if out else 0, "dir": "rtl" if rtl else "ltr"}]
        for r in out:
            r["script"] = _script(r["text"])
        if len(self._runs) > 4000:
            self._runs.clear()
        self._runs[key] = out
        return out

    def measure(self, text: str, direction: str, language: str) -> float:
        runs = self.runs(text, direction == "rtl")
        if len(runs) == 1:
            return _measure(self.entries[runs[0]["entry"]]["font"], text, direction, language)
        return sum(_measure(self.entries[r["entry"]]["font"], r["text"], r["dir"],
                            language if r["dir"] == "rtl" else "en") for r in runs)

    def primary(self, text: str, rtl: bool) -> dict:
        """The entry that draws most of the text: its metrics are the line's."""
        count = {}
        for r in self.runs(text, rtl):
            count[r["entry"]] = count.get(r["entry"], 0) + len(r["text"].strip())
        return self.entries[max(count, key=lambda i: (count[i], -i))] if count else self.entries[0]


def block_chain(block: dict, fonts: dict = None) -> list:
    """The faces a block may be drawn with, first choice first: the client's
    brand font for the block's slot and their Hebrew pairing (core/brand_fonts
    — `fonts`), then the spec's own family, then the bundled last resort."""
    chain = list(((fonts or {}).get("chains") or {}).get(slot_of(block["role"])) or [])
    chain.append(font_library.bundled(block["font_family"]))
    chain.append(font_library.bundled())
    return chain


def _type_for(block: dict, fonts: dict = None) -> _Type:
    """The block's _Type. With no brand font for its slot the spec's family is
    the design: a weight outside its axis is the spec's mistake (ValueError)."""
    if not ((fonts or {}).get("chains") or {}).get(slot_of(block["role"])):
        _font(block["font_family"], block["size"], block["weight"])
    return _Type(block_chain(block, fonts), block["size"], block["weight"])


def font_report(kind: _Type, lines: list, rtl: bool, block: dict, fonts: dict = None) -> tuple:
    """(drawn, fallbacks) of one block, from the runs that were laid out:
    drawn = [{"font", "source", "weight", "script", "chars"}] (one row per
    font and script); fallbacks = the runs a font OTHER than the brand's drew
    — [{"role", "wanted", "drew", "script", "reason"}]. A Hebrew run in the
    client's own Hebrew pairing is their choice, not a fallback."""
    rows, fallbacks = {}, {}
    wanted = (fonts or {}).get("wanted") or {}
    slot = slot_of(block["role"])
    want, pair = wanted.get(slot) or "", wanted.get("hebrew") or ""
    issues = (fonts or {}).get("issues") or {}
    brand = next((e["face"] for e in kind.entries if font_library.norm(e["face"].family) == font_library.norm(want)), None)
    for line in lines:
        for r in kind.runs(line, rtl):
            e = kind.entries[r["entry"]]
            face = e["face"]
            key = (face.family, r["script"])
            row = rows.setdefault(key, {"font": face.family, "source": face.source, "weight": e["weight"],
                                        "script": r["script"], "chars": 0})
            row["chars"] += len(r["text"].strip())
            if not want or font_library.norm(face.family) in (font_library.norm(want), font_library.norm(pair)):
                continue
            if brand is None:
                reason = issues.get(slot) or next((s["reason"] for s in kind.skipped
                                                   if font_library.norm(s["family"]) == font_library.norm(want)), "unavailable")
            elif r["script"] == "hebrew" and not brand.hebrew:
                reason = (issues.get("hebrew") or "hebrew_pairing_unavailable") if pair else "no_hebrew_pairing"
            else:
                reason = "missing_glyphs"
            fallbacks.setdefault((face.family, r["script"], reason), {
                "role": block["role"], "wanted": want, "drew": face.family, "script": r["script"], "reason": reason})
    return list(rows.values()), list(fallbacks.values())


def _rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lin(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple) -> float:
    r, g, b = rgb[:3]
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: tuple, b: tuple) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def is_large(size: int, weight: int) -> bool:
    return size >= 40 or (size >= 32 and weight >= 700)


def direction_of(text: str) -> tuple:
    return ("rtl", "he") if HEBREW_RE.search(text or "") else ("ltr", "en")


def _measure(font, text: str, direction: str, language: str) -> float:
    if isinstance(font, _Type):                  # a block's fonts: each run measured with its own
        return font.measure(text, direction, language)
    return font.getlength(text, direction=direction, language=language)


# ─── Wrapping (measured with the real font) ──────────────────────────────────

class _TooWide(ValueError):
    pass


def wrap(text: str, font, width: float, direction: str = "rtl", language: str = "he") -> list:
    """Greedy word wrap in LOGICAL order (raqm reorders each line for display),
    measured with the font itself. Hard line breaks are kept. A single word
    wider than the box raises _TooWide — never split or clipped."""
    lines = []
    for paragraph in str(text).split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            if _measure(font, word, direction, language) > width:
                raise _TooWide(word)
            candidate = f"{line} {word}" if line else word
            if not line or _measure(font, candidate, direction, language) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return lines


# ─── Background and decorations ──────────────────────────────────────────────

def _background(bg: dict, size: tuple, roles: dict, loader, problems: list) -> "Image.Image":
    W, H = size
    first = _rgb(roles[bg.get("color_role") or "bg"])
    kind = bg.get("type")
    if kind == "solid":
        return Image.new("RGB", size, first)
    if kind in ("split", "gradient"):
        if not bg.get("second_color_role"):
            problems.append({"code": "bad_spec", "detail": f"background {kind} needs second_color_role"})
            return Image.new("RGB", size, first)
        second = _rgb(roles[bg["second_color_role"]])
        if kind == "split":
            img = Image.new("RGB", size, second)
            ratio = float(bg.get("split_ratio") or 0.5)
            draw = ImageDraw.Draw(img)
            if (bg.get("split_direction") or "horizontal") == "horizontal":
                draw.rectangle([0, 0, W, round(H * ratio) - 1], fill=first)
            else:                                   # the first part on the RIGHT (RTL)
                draw.rectangle([W - round(W * ratio), 0, W, H], fill=first)
            return img
        way = bg.get("gradient_direction") or "top_to_bottom"
        steps = 256
        strip = Image.new("RGB", (1, steps) if way in ("top_to_bottom", "bottom_to_top") else (steps, 1))
        for i in range(steps):
            t = i / (steps - 1)
            colour = tuple(round(first[c] + (second[c] - first[c]) * t) for c in range(3))
            if way == "top_to_bottom":
                strip.putpixel((0, i), colour)
            elif way == "bottom_to_top":
                strip.putpixel((0, steps - 1 - i), colour)
            elif way == "right_to_left":
                strip.putpixel((steps - 1 - i, 0), colour)
            else:
                strip.putpixel((i, 0), colour)
        return strip.resize(size, Image.BILINEAR)
    # image: the client's own photo, cover-fitted, with an optional scrim
    asset = bg.get("image_asset_id") or ""
    photo = None
    if not asset or loader is None:
        problems.append({"code": "asset", "detail": "background image needs image_asset_id (a photo in the client's media folder)"})
    else:
        try:
            photo = Image.open(io.BytesIO(loader(asset)))
            photo.draft("RGB", size)
            photo = photo.convert("RGB")
        except Exception as e:
            problems.append({"code": "asset", "detail": f"background image {asset} unusable ({type(e).__name__}: {str(e)[:120]})"})
    if photo is None:
        return Image.new("RGB", size, first)
    scale = max(W / photo.width, H / photo.height)
    photo = photo.resize((max(W, round(photo.width * scale)), max(H, round(photo.height * scale))), Image.LANCZOS)
    left, top = (photo.width - W) // 2, (photo.height - H) // 2
    photo = photo.crop((left, top, left + W, top + H))
    opacity = float(bg.get("scrim_opacity") or 0)
    if opacity > 0:
        photo = Image.blend(photo, Image.new("RGB", size, _rgb(roles[bg.get("scrim_color_role") or "bg"])), opacity)
    return photo


def _corner_point(corner: str, size: tuple, m: dict) -> tuple:
    W, H = size
    x = W - m["right"] if corner.endswith("right") else m["left"]
    y = m["top"] if corner.startswith("top") else H - m["bottom"]
    return x, y


def _digits_font(slot: str, family: str, size: int, weight: int, text: str, fonts: dict = None) -> dict:
    """The entry ({"face", "font", "weight"}) that draws a number (the badge,
    the counter): the brand font of that slot when it has the digits, else
    the bundled family."""
    kind = _Type(list(((fonts or {}).get("chains") or {}).get(slot) or []) + [font_library.bundled(family)], size, weight)
    return kind.entries[kind._pick(text)]


def _accent(img, accent: dict, size: tuple, m: dict, roles: dict, n: int, problems: list, fonts: dict = None) -> dict:
    """Draws bar / corner_mark / number_badge. Returns the underline request
    (drawn after the text is laid out) and the accent's rectangle."""
    kind = (accent or {}).get("type") or "none"
    if kind == "none":
        return {}
    W, H = size
    colour = _rgb(roles[accent.get("color_role") or "accent"])
    s = int(accent.get("size") or 12)
    pos = accent.get("position") or ""
    draw = ImageDraw.Draw(img)
    if kind == "underline":
        if pos not in ("", "under_headline"):
            problems.append({"code": "bad_spec", "detail": "underline position must be under_headline"})
        return {"underline": {"size": s, "colour": colour}}
    if kind == "bar":
        pos = pos or "top"
        if pos not in EDGES:
            problems.append({"code": "bad_spec", "detail": f"bar position must be one of {EDGES}"})
            return {}
        rect = {"top": [0, 0, W, s], "bottom": [0, H - s, W, H], "right": [W - s, 0, W, H], "left": [0, 0, s, H]}[pos]
        draw.rectangle([rect[0], rect[1], rect[2] - 1, rect[3] - 1], fill=colour)
        return {"rect": rect}
    pos = pos or "top_right"
    if pos not in CORNERS:
        problems.append({"code": "bad_spec", "detail": f"{kind} position must be one of {CORNERS}"})
        return {}
    x, y = _corner_point(pos, size, m)
    sx = -1 if pos.endswith("right") else 1
    sy = 1 if pos.startswith("top") else -1
    if kind == "corner_mark":
        t = max(4, s // 8)
        xs = sorted([x, x + sx * s])
        ys = sorted([y, y + sy * t])
        draw.rectangle([xs[0], ys[0], xs[1], ys[1]], fill=colour)
        xs2 = sorted([x, x + sx * t])
        ys2 = sorted([y, y + sy * s])
        draw.rectangle([xs2[0], ys2[0], xs2[1], ys2[1]], fill=colour)
        return {"rect": [min(xs[0], xs2[0]), min(ys[0], ys2[0]), max(xs[1], xs2[1]), max(ys[1], ys2[1])]}
    # number_badge: the slide number in a circle
    box = sorted([x, x + sx * s]) + sorted([y, y + sy * s])
    rect = [box[0], box[2], box[1], box[3]]
    draw.ellipse(rect, fill=colour)
    ink = max((_rgb(roles["fg"]), _rgb(roles["bg"])), key=lambda c: contrast(c, colour))
    font = _digits_font("headline", "Rubik", max(16, int(s * 0.5)), 700, str(n), fonts)["font"]
    draw.text(((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2), str(n), font=font, fill=ink, anchor="mm")
    return {"rect": rect}


def _logo(img, logo: dict, size: tuple, m: dict, loader, problems: list):
    if not (logo or {}).get("show"):
        return None
    asset = logo.get("asset_ref") or ""
    if not asset or loader is None:
        problems.append({"code": "asset", "detail": "logo.show needs asset_ref (the client's logo file in their media folder)"})
        return None
    try:
        mark = Image.open(io.BytesIO(loader(asset))).convert("RGBA")
    except Exception as e:
        problems.append({"code": "asset", "detail": f"logo {asset} unusable ({type(e).__name__})"})
        return None
    W, _ = size
    width = round(W * float(logo.get("size_pct") or 12) / 100)
    mark = mark.resize((width, max(1, round(mark.height * width / mark.width))), Image.LANCZOS)
    pos = logo.get("position") or "top_left"
    x, y = _corner_point(pos, size, m)
    x = x - mark.width if pos.endswith("right") else x
    y = y if pos.startswith("top") else y - mark.height
    img.paste(mark, (x, y), mark)
    return [x, y, x + mark.width, y + mark.height]


# ─── The agent character (a transparent cut-out, under the text) ─────────────

def merge_character(spec_character: dict, slide_character: dict) -> dict:
    """The spec's placement + the slide's own agent/pose (the slide entry
    wins on every key it has). {} = no character on this slide."""
    merged = {**CHARACTER_DEFAULTS, **(spec_character or {}), **(slide_character or {})}
    return merged if merged.get("pose_id") else {}


def shadow_intensity(value) -> float:
    if value is True or value is None:
        return DEFAULT_SHADOW
    if value is False:
        return 0.0
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return DEFAULT_SHADOW


def _character(img, ch: dict, size: tuple, loader, problems: list, warnings: list, frame: dict = None,
               n: int = 0) -> dict:
    """Composites the figure onto img. Returns {"rect", "alpha", "origin"}
    (its placed box and alpha, for the text-collision check) or {}. Every
    stage is logged (core/render_manifest.trace): fetched, background, composited."""
    from PIL import ImageFilter, ImageOps
    from core.render_manifest import trace
    pose_id = str(ch.get("pose_id") or "")
    who = str(ch.get("agent") or "")
    if loader is None:
        problems.append({"code": "asset", "detail": f"character pose {pose_id}: no loader"})
        trace("pose_fetched", who, "failed: no loader", n, f"pose {pose_id}")
        return {}
    try:
        fig = Image.open(io.BytesIO(loader(f"pose:{pose_id}"))).convert("RGBA")
    except Exception as e:
        problems.append({"code": "asset", "detail": f"character pose {pose_id} unusable ({type(e).__name__})"})
        trace("pose_fetched", who, f"failed: {type(e).__name__}", n, f"pose {pose_id}: {str(e)[:100]}")
        return {}
    trace("pose_fetched", who, "ok", n, f"pose {pose_id}, {fig.width}x{fig.height}")
    alpha = fig.getchannel("A")
    box = alpha.getbbox()
    if alpha.getextrema()[0] > 0 or not box:
        problems.append({"code": "character_not_transparent",
                         "detail": f"character pose {pose_id} has no transparent background"})
        trace("background_removed", who, "failed: the pose has no transparent background", n, f"pose {pose_id}")
        return {}
    trace("background_removed", who, "ok", n, "transparent background verified")
    fig = fig.crop(box)                                   # the figure itself, not its empty margin
    W, H = size
    frame = frame or {"x": 0, "y": 0, "w": W, "h": H}      # a vertical canvas: its safe box
    h = max(1, round(frame["h"] * float(ch.get("scale_pct") or CHARACTER_DEFAULTS["scale_pct"]) / 100))
    w = max(1, round(fig.width * h / fig.height))
    fig = fig.resize((w, h), Image.LANCZOS)
    if ch.get("flip"):
        fig = ImageOps.mirror(fig)
    cx = frame["x"] + frame["w"] - frame["w"] * float(ch.get("x_pct", CHARACTER_DEFAULTS["x_pct"])) / 100
    bottom = frame["y"] + frame["h"] * float(ch.get("y_pct", CHARACTER_DEFAULTS["y_pct"])) / 100
    x0, y0 = round(cx - w / 2), round(bottom - h)
    rect = [x0, y0, x0 + w, y0 + h]
    if x0 < 0 or y0 < 0 or x0 + w > W or y0 + h > H:
        warnings.append({"code": "character_overflow",
                         "detail": f"the character ({x0},{y0})-({x0 + w},{y0 + h}) runs past the "
                                   f"{W}x{H} canvas — move or shrink it (x_pct / y_pct / scale_pct)"})
    strength = shadow_intensity(ch.get("shadow"))
    if strength > 0:
        sw, sh = max(8, round(w * 0.8)), max(6, round(h * 0.06))
        blur = max(4, round(h * 0.02))
        pad = blur * 3
        mask = Image.new("L", (sw + 2 * pad, sh + 2 * pad), 0)
        ImageDraw.Draw(mask).ellipse([pad, pad, pad + sw, pad + sh], fill=round(255 * strength))
        mask = mask.filter(ImageFilter.GaussianBlur(blur))
        sx, sy = round(cx - sw / 2) - pad, round(min(bottom, H) - sh * 0.6) - pad
        img.paste((0, 0, 0), (sx, sy, sx + mask.width, sy + mask.height), mask)
    img.paste(fig, (x0, y0), fig)
    trace("composited", who, "ok", n, f"pose {pose_id} at ({x0},{y0})-({x0 + w},{y0 + h}) on {W}x{H}")
    return {"rect": rect, "alpha": fig.getchannel("A"), "origin": (x0, y0)}


def _touches(char: dict, rect: list) -> bool:
    """True when an opaque pixel of the figure lies under a text box."""
    if not char or not _intersects(char["rect"], rect):
        return False
    ox, oy = char["origin"]
    a = char["alpha"]
    x0, y0 = max(0, int(rect[0]) - ox), max(0, int(rect[1]) - oy)
    x1, y1 = min(a.width, int(rect[2]) + 1 - ox), min(a.height, int(rect[3]) + 1 - oy)
    if x1 <= x0 or y1 <= y0:
        return False
    return a.crop((x0, y0, x1, y1)).getextrema()[1] > 24


# ─── Text layout ─────────────────────────────────────────────────────────────

def layout_frame(spec: dict) -> dict:
    """{"x", "y", "w", "h"}: the box every percentage is measured in. A feed
    canvas = the whole canvas. A VERTICAL canvas = its safe box
    (core/social_formats): the platform's own interface covers the rest, so a
    story's text and figure are laid out inside it; the background still fills
    the whole canvas."""
    from core import social_formats
    W, H = spec["canvas"]["width"], spec["canvas"]["height"]
    if (W, H) == social_formats.FORMATS[social_formats.VERTICAL].size:
        box = social_formats.safe_box(spec.get("safe_zone") or social_formats.zone_for([]))
        return {"x": box["x"], "y": box["y"], "w": box["width"], "h": box["height"]}
    return {"x": 0, "y": 0, "w": W, "h": H}


def _layout(block: dict, text: str, index: int, size: tuple, m: dict, roles: dict, problems: list,
            frame: dict = None, fonts: dict = None) -> dict:
    W, H = size
    frame = frame or {"x": 0, "y": 0, "w": W, "h": H}
    FW, FH = frame["w"], frame["h"]
    where = f"text_blocks[{index}] ({block['role']})"
    direction, language = direction_of(text)
    align = block.get("align") or "right"
    if direction == "rtl" and align == "left":
        problems.append({"code": "hebrew_align", "block": index, "detail": f"{where}: Hebrew is never left-aligned"})
        align = "right"
    try:
        kind = _type_for(block, fonts)
    except ValueError as e:
        problems.append({"code": "weight", "block": index, "detail": f"{where}: {e}"})
        return {}
    # the line's metrics are those of the font that draws most of the text
    font = kind.primary(text, direction == "rtl")["font"]
    box_w = FW * float(block["max_width_pct"]) / 100
    right = W - m["right"] - FW * float(block.get("x_pct") or 0) / 100
    left = right - box_w
    if left < m["left"] - 0.5:
        problems.append({"code": "overflow", "block": index,
                         "detail": f"{where}: the box ({round(box_w)} px wide, {round(W - m['right'] - right)} px in "
                                   f"from the right margin) runs past the left margin"})
    try:
        lines = wrap(text, kind, box_w, direction, language)
    except _TooWide as e:
        problems.append({"code": "overflow", "block": index, "role": block["role"], "kind": "word", "word": str(e),
                         "detail": f"{where}: the word {str(e)!r} is wider than the box at {block['size']} px"})
        return {}
    ascent, descent = font.getmetrics()
    lh = block["size"] * float(block.get("line_spacing") or 1.2)
    text_h = ascent + descent + (len(lines) - 1) * lh
    y = frame["y"] + FH * float(block["y_pct"]) / 100
    top = {"top": y, "center": y - text_h / 2, "bottom": y - text_h}[block["anchor"]]
    # Every overflow carries WHICH block and BY HOW MUCH (lines needed / lines
    # that fit, px) — core/copy_budget says it to the manager in plain words
    if block.get("max_height_pct"):
        max_h = FH * float(block["max_height_pct"]) / 100
        if text_h > max_h + 0.5:
            fits = max(1, int((max_h - ascent - descent) // lh) + 1)
            problems.append({"code": "overflow", "block": index, "role": block["role"], "kind": "lines",
                             "lines": len(lines), "fit_lines": fits, "need_px": round(text_h), "have_px": round(max_h), "text_chars": len(text),
                             "detail": f"{where}: {len(lines)} lines need {round(text_h)} px, the box holds "
                                       f"{round(max_h)} px (about {fits} lines)"})
    if top < m["top"] - 0.5 or top + text_h > H - m["bottom"] + 0.5:
        room = {"top": H - m["bottom"] - y, "bottom": y - m["top"],
                "center": 2 * min(y - m["top"], H - m["bottom"] - y)}[block["anchor"]]
        fits = max(0, int((room - ascent - descent) // lh) + 1) if room >= ascent + descent else 0
        problems.append({"code": "overflow", "block": index, "role": block["role"], "kind": "safe_area",
                         "lines": len(lines), "fit_lines": fits, "need_px": round(text_h), "have_px": max(0, round(room)), "text_chars": len(text),
                         "detail": f"{where}: the text ({round(top)}–{round(top + text_h)} px) leaves the safe area "
                                   f"({m['top']}–{H - m['bottom']} px)"})
    widths = [_measure(kind, ln, direction, language) for ln in lines]
    widest = max(widths) if widths else 0
    if align == "right":
        x0 = right - widest
    elif align == "center":
        x0 = (left + right) / 2 - widest / 2
    else:
        x0 = left
    return {"block": block, "index": index, "lines": lines, "widths": widths, "font": font, "type": kind, "lh": lh,
            "ascent": ascent, "top": top, "text_h": text_h, "left": left, "right": right, "align": align,
            "direction": direction, "language": language, "colour": _rgb(roles[block["color_role"]]),
            "rect": [x0, top, x0 + widest, top + text_h], "where": where}


def _draw_text(draw, lay: dict):
    for i, line in enumerate(lay["lines"]):
        if not line:
            continue
        baseline = lay["top"] + lay["ascent"] + i * lay["lh"]
        if lay["align"] == "right":
            x = lay["right"]                                    # the right edge of the box
        elif lay["align"] == "center":
            x = (lay["left"] + lay["right"]) / 2 + lay["widths"][i] / 2   # placed by its right edge
        else:
            x = lay["left"] + lay["widths"][i]
        _draw_line(draw, lay["type"], line, x, baseline, lay["colour"], lay["direction"], lay["language"])


def _draw_line(draw, kind: _Type, line: str, right: float, baseline: float, colour: tuple, direction: str,
               language: str):
    """One line whose RIGHT edge is at `right`. One font = one string, shaped
    and ordered by raqm as a whole. Several fonts = each run with its own
    font, placed in the line's visual order: along a right-to-left line the
    runs follow each other leftwards, and a stretch of left-to-right runs
    inside it reads left to right."""
    runs = kind.runs(line, direction == "rtl")
    if len(runs) == 1:
        draw.text((right, baseline), line, font=kind.entries[runs[0]["entry"]]["font"], fill=colour, anchor="rs",
                  direction=direction, language=language)
        return
    cursor, i = right, 0
    while i < len(runs):
        j = i
        while j < len(runs) and runs[j]["dir"] == runs[i]["dir"]:
            j += 1
        group = runs[i:j]
        fonts = [kind.entries[r["entry"]]["font"] for r in group]
        if group[0]["dir"] == "rtl":
            for r, font in zip(group, fonts):
                draw.text((cursor, baseline), r["text"], font=font, fill=colour, anchor="rs", direction="rtl",
                          language=language)
                cursor -= _measure(font, r["text"], "rtl", language)
        else:
            widths = [_measure(font, r["text"], "ltr", "en") for r, font in zip(group, fonts)]
            x = cursor - sum(widths)
            for r, font, w in zip(group, fonts, widths):
                draw.text((x, baseline), r["text"], font=font, fill=colour, anchor="ls", direction="ltr", language="en")
                x += w
            cursor -= sum(widths)
        i = j


def _contrast_at(img, rect: list, colour: tuple) -> float:
    W, H = img.size
    x0, y0 = max(0, int(rect[0])), max(0, int(rect[1]))
    x1, y1 = min(W, int(rect[2]) + 1), min(H, int(rect[3]) + 1)
    if x1 <= x0 or y1 <= y0:
        return 21.0
    region = img.crop((x0, y0, x1, y1))
    scale = CONTRAST_SAMPLE / max(region.size)
    if scale < 1:
        region = region.resize((max(1, round(region.width * scale)), max(1, round(region.height * scale))), Image.BOX)
    ratios = sorted(contrast(colour, px) for px in region.getdata())
    return ratios[min(len(ratios) - 1, int(len(ratios) * CONTRAST_PERCENTILE))]


def _intersects(a: list, b: list) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# ─── The render ──────────────────────────────────────────────────────────────

def render_slide(spec: dict, roles: dict, copy_headline: str = "", copy_body: str = "", n: int = 1,
                 total: int = 1, role: str = "", image_loader=None, encode: bool = True,
                 character: dict = None, warnings: list = None, manifest: dict = None, fonts: dict = None):
    """PNG bytes of ONE slide (None when encode=False — a guard-only pass),
    or RenderError with every problem found. `character` = this slide's own
    agent/pose (merged over the spec's placement); `warnings` (a list) gets
    the character's overflow / text-collision warnings. `manifest` (a dict)
    is FILLED with what was actually drawn — every layer committed to the
    output (core/render_manifest): the only source for any statement about
    the slide. `fonts` = the client's resolved brand fonts
    (core/brand_fonts.resolve): each text block is drawn with the brand font
    of its slot, run by run, and the manifest records which font drew what —
    None = the spec's own bundled families."""
    require_raqm()
    spec = slide_spec.for_slide(slide_spec.validate(spec), role)
    ch = merge_character(spec.get("character"), character)
    if ch:
        slide_spec.validate_character(ch)
    with _LOCK:
        return _render(spec, roles, copy_headline, copy_body, n, total, image_loader, encode, ch,
                       warnings if warnings is not None else [], manifest, fonts)


def _render(spec, roles, copy_headline, copy_body, n, total, loader, encode, ch=None, warnings=None, manifest=None,
            fonts=None):
    from core import render_manifest
    problems = []
    warnings = warnings if warnings is not None else []
    size = (spec["canvas"]["width"], spec["canvas"]["height"])
    m = slide_spec.margins(spec)
    frame = layout_frame(spec)
    drawn = render_manifest.new(*size)
    img = _background(spec["background"], size, roles, loader, problems)
    render_manifest.layer(drawn, "background", kind=spec["background"].get("type"),
                          asset=spec["background"].get("image_asset_id") if spec["background"].get("type") == "image" else "")
    # The colours this slide is drawn with, and the canvas's average colour measured
    # from the pixels right after the background layer: the card's colour label and
    # the "did the brief's palette reach the file" check are built from this
    render_manifest.palette(drawn, roles, spec["background"], render_manifest.mean_color(img))

    accent = _accent(img, spec.get("accent") or {}, size, m, roles, n, problems, fonts)
    if accent.get("rect"):
        render_manifest.layer(drawn, "accent", kind=(spec.get("accent") or {}).get("type"), box=accent["rect"])
    char = _character(img, ch, size, loader, problems, warnings, frame, n) if ch else {}
    if char:
        render_manifest.layer(drawn, "character", id=str(ch.get("agent") or ""), pose_id=str(ch.get("pose_id") or ""),
                              box=char["rect"])
    logo_box = _logo(img, spec.get("logo") or {}, size, m, loader, problems)
    if logo_box:
        render_manifest.layer(drawn, "logo", asset=(spec.get("logo") or {}).get("asset_ref"), box=logo_box)
    limit = int((spec.get("guards") or {}).get("headline_max_words") or HEADLINE_MAX_WORDS)
    layouts = []
    for i, block in enumerate(spec["text_blocks"]):
        text = slide_spec.bind_text(block, copy_headline, copy_body, n, total)
        if not text:
            continue
        if block["role"] == "headline" and len(text.split()) > limit:
            problems.append({"code": "headline_words", "block": i, "role": "headline", "kind": "words",
                             "words": len(text.split()), "limit": limit,
                             "detail": f"text_blocks[{i}] (headline): {len(text.split())} words, the limit is {limit}"})
        lay = _layout(block, text, i, size, m, roles, problems, frame, fonts)
        if lay:
            layouts.append(lay)
    counter = _counter_layout(spec.get("counter") or {}, size, m, roles, n, total, fonts)
    boxes = ([(lay["where"], lay["rect"], lay["block"]["role"]) for lay in layouts]
             + ([("counter", counter["rect"], "counter")] if counter else []))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _intersects(boxes[i][1], boxes[j][1]):
                problems.append({"code": "overlap", "role": boxes[i][2], "other": boxes[j][2], "kind": "overlap",
                                 "detail": f"{boxes[i][0]} and {boxes[j][0]} overlap"})
    for lay in layouts:
        block = lay["block"]
        need = CONTRAST_MIN_LARGE if is_large(block["size"], block["weight"]) else CONTRAST_MIN
        got = _contrast_at(img, lay["rect"], lay["colour"])
        if got < need:
            problems.append({"code": "contrast", "block": lay["index"],
                             "detail": f"{lay['where']}: contrast {got:.2f} against what is under it, needs {need} "
                                       f"({block['color_role']} on this background)"})
    if counter and counter.get("style") == "fraction":
        got = _contrast_at(img, counter["rect"], counter["colour"])
        if got < CONTRAST_MIN:
            problems.append({"code": "contrast", "detail": f"counter: contrast {got:.2f}, needs {CONTRAST_MIN}"})
    for lay in layouts:
        if _touches(char, lay["rect"]):
            warnings.append({"code": "character_text_overlap", "block": lay["index"],
                             "detail": f"the character is under {lay['where']} — the text stays on top; "
                                       f"move the character if it hurts legibility"})
    underline = accent.get("underline")
    head = next((lay for lay in layouts if lay["block"]["role"] == "headline"), None)
    if underline and not head:
        problems.append({"code": "bad_spec", "detail": "accent underline needs a headline block with text"})
    if problems:
        raise RenderError(problems)
    draw = ImageDraw.Draw(img)
    if underline:
        width = (head["right"] - head["left"]) * 0.3
        y0 = head["rect"][3] + head["block"]["size"] * 0.25
        if head["align"] == "right":
            x1 = head["right"]
        elif head["align"] == "center":
            x1 = (head["left"] + head["right"]) / 2 + width / 2
        else:
            x1 = head["left"] + width
        draw.rectangle([x1 - width, y0, x1, y0 + underline["size"]], fill=underline["colour"])
        render_manifest.layer(drawn, "accent", kind="underline", box=[x1 - width, y0, x1, y0 + underline["size"]])
    fell = []
    for lay in layouts:
        _draw_text(draw, lay)
        # which font ACTUALLY drew each run of this block (never the one that was asked for)
        used, fallbacks = font_report(lay["type"], lay["lines"], lay["direction"] == "rtl", lay["block"], fonts)
        fell += fallbacks
        main = max(used, key=lambda u: u["chars"]) if used else {}
        render_manifest.layer(drawn, "text", role=lay["block"]["role"], text="\n".join(lay["lines"]), box=lay["rect"],
                              font=main.get("font"), weight=main.get("weight"), weight_asked=lay["block"]["weight"],
                              fonts=used)
        if encode:
            render_manifest.font_trace(n, lay["block"]["role"], used, fallbacks)
    if counter:
        _draw_counter(draw, counter)
        render_manifest.layer(drawn, "counter", text=counter.get("text") or f"{counter['n']}/{counter['total']}",
                              box=counter["rect"], font=counter.get("family"))
    render_manifest.fonts(drawn, (fonts or {}).get("wanted"), fell)
    drawn["encoded"] = bool(encode)
    if manifest is not None:
        # only now: every layer above is committed to the image
        manifest.clear()
        manifest.update(drawn)
    if not encode:
        return None
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _counter_layout(counter: dict, size: tuple, m: dict, roles: dict, n: int, total: int, fonts: dict = None) -> dict:
    if not counter.get("show") or total <= 1:
        return {}
    style = counter.get("style") or "fraction"
    pos = counter.get("position") or "bottom_left"
    colour = _rgb(roles[counter.get("color_role") or "muted"])
    x, y = _corner_point(pos, size, m)
    if style == "dots":
        d, gap = 12, 10
        w, h = total * d + (total - 1) * gap, d
        text, font, family = "", None, ""
    else:
        text = f"{n}/{total}"
        entry = _digits_font("body", "Assistant", COUNTER_SIZE, 600, text, fonts)
        font, family = entry["font"], entry["face"].family
        w = _measure(font, text, "ltr", "en")
        ascent, descent = font.getmetrics()
        h = ascent + descent
    x0 = x - w if pos.endswith("right") else x
    y0 = y if pos.startswith("top") else y - h
    return {"style": style, "rect": [x0, y0, x0 + w, y0 + h], "colour": colour, "n": n, "total": total,
            "text": text, "font": font, "family": family, "bg": _rgb(roles["bg"])}


def _draw_counter(draw, c: dict):
    x0, y0 = c["rect"][0], c["rect"][1]
    if c["style"] == "fraction":
        draw.text((x0, y0), c["text"], font=c["font"], fill=c["colour"], anchor="la", direction="ltr", language="en")
        return
    d, gap = 12, 10
    dim = tuple(round(c["colour"][k] * 0.35 + c["bg"][k] * 0.65) for k in range(3))
    for i in range(c["total"]):
        # Dot 1 on the RIGHT: a Hebrew carousel reads right to left
        cx = c["rect"][2] - i * (d + gap) - d
        draw.ellipse([cx, y0, cx + d, y0 + d], fill=c["colour"] if i + 1 == c["n"] else dim)


def slide_roles(roles: dict, colors: dict = None) -> dict:
    """The set's role colours with ONE slide's explicit colours on top — the
    manager asked for colours other than the brand palette (post `colors`,
    core/post_brief.clean_colors). Only `bg` is required there: a missing
    `fg` becomes white or near-black, whichever reads better on that bg; a
    missing `muted` follows the fg; an accent that would vanish on the new bg
    becomes the fg. No colours = the roles unchanged."""
    if not colors or not colors.get("bg"):
        return roles
    out = dict(roles)
    out["bg"] = colors["bg"]
    bg = _rgb(out["bg"])
    out["fg"] = colors.get("fg") or max(("#FFFFFF", "#111111"), key=lambda c: contrast(_rgb(c), bg))
    out["muted"] = colors.get("muted") or out["fg"]
    accent = colors.get("accent") or out.get("accent") or out["fg"]
    out["accent"] = accent if contrast(_rgb(accent), bg) >= 1.6 else out["fg"]
    return out


def render_set(spec: dict, roles: dict, slides: list, image_loader=None, encode: bool = True,
               warnings: list = None, on_slide=None, manifests: list = None, fonts: dict = None) -> list:
    """Every slide of ONE set from ONE spec and ONE palette (the caller passes
    one of each — a set is never mixed). slides = [{"copy_headline",
    "copy_body", "role", "n"?, "total"?}] — n / total = the slide's place in
    the whole post (default: its place in this list). RenderError lists the
    problems of every slide. `on_slide(n, problems_or_None)`: called before
    each slide (None) and after it ([] = rendered) — the production bar."""
    out, problems = [], []
    for i, s in enumerate(slides, 1):
        n, total = int(s.get("n") or i), int(s.get("total") or len(slides))
        if on_slide:
            on_slide(n, None)
        before = len(problems)
        try:
            got, drawn = [], {}
            out.append(render_slide(spec, slide_roles(roles, s.get("colors")), s.get("copy_headline", ""),
                                    s.get("copy_body", ""), n, total,
                                    s.get("role", ""), image_loader, encode, character=s.get("character"),
                                    warnings=got, manifest=drawn, fonts=fonts))
            if manifests is not None:
                # what THIS slide's render committed (core/render_manifest), in slide order
                manifests.append({"n": n, "asset_id": s.get("asset_id", ""), **drawn})
            if warnings is not None:
                warnings += [{**w, "slide": n, "detail": f"slide {n}: {w['detail']}"} for w in got]
        except RenderError as e:
            problems += [{**p, "slide": n, "detail": f"slide {n}: {p['detail']}"} for p in e.problems]
        if on_slide:
            on_slide(n, problems[before:])
    if problems:
        raise RenderError(problems)
    return out


# ─── A font shown as itself (the branding page's picker) ─────────────────────

def render_sample(face, text: str, weight: int = 400, size: int = 44, width: int = 640, height: int = 112,
                  fg: str = "#111111", bg: str = "#FFFFFF", pad: int = 20) -> bytes:
    """PNG of `text` drawn by `face` and by nothing else — the same loader,
    shaping and run logic as a slide. RenderError when the face cannot draw
    the whole text itself (a sample never shows another font in its place).
    The type is made smaller only to fit the strip: this is a specimen, not a slide."""
    require_raqm()
    direction, language = direction_of(text)
    with _LOCK:
        kind = None
        for px in range(int(size), 15, -2):
            try:
                kind = _Type([face], px, weight)
            except RendererUnavailable:
                raise RenderError([{"code": "asset", "detail": f"{face.family} is not available"}])
            if kind.measure(text, direction, language) <= width - 2 * pad:
                break
        if not face.covers(text):
            raise RenderError([{"code": "asset", "detail": f"{face.family} lacks characters of the sample"}])
        font = kind.entries[0]["font"]
        ascent, descent = font.getmetrics()
        img = Image.new("RGB", (width, height), _rgb(bg))
        baseline = (height - ascent - descent) / 2 + ascent
        right = width - pad if direction == "rtl" else pad + kind.measure(text, direction, language)
        _draw_line(ImageDraw.Draw(img), kind, text, right, baseline, _rgb(fg), direction, language)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
