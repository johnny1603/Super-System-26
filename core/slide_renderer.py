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
import threading
from collections import OrderedDict

try:
    from PIL import Image, ImageDraw, ImageFont, features
except ImportError:                     # a stdlib-only checkout (CI): every render refuses
    Image = ImageDraw = ImageFont = features = None

from core import slide_spec
from core.post_brief import HEBREW_RE

SERVICE_NAME = "slide_renderer"
FONTS_DIR = os.path.join(slide_spec.ROOT, "assets", "fonts")
FONT_FILES = {"Rubik": "Rubik[wght].ttf", "Assistant": "Assistant[wght].ttf"}
HEADLINE_MAX_WORDS = 10
CONTRAST_MIN = 4.5
CONTRAST_MIN_LARGE = 3.0
CONTRAST_PERCENTILE = 0.05
CONTRAST_SAMPLE = 64            # the measured region is reduced to at most 64 px a side
FONT_CACHE_MAX = 48
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
    return {**status, "missing_fonts": missing}


# ─── Fonts and colours ───────────────────────────────────────────────────────

def _font(family: str, size: int, weight: int):
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


def _accent(img, accent: dict, size: tuple, m: dict, roles: dict, n: int, problems: list) -> dict:
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
    font = _font("Rubik", max(16, int(s * 0.5)), 700)
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


def _character(img, ch: dict, size: tuple, loader, problems: list, warnings: list) -> dict:
    """Composites the figure onto img. Returns {"rect", "alpha", "origin"}
    (its placed box and alpha, for the text-collision check) or {}."""
    from PIL import ImageFilter, ImageOps
    pose_id = str(ch.get("pose_id") or "")
    if loader is None:
        problems.append({"code": "asset", "detail": f"character pose {pose_id}: no loader"})
        return {}
    try:
        fig = Image.open(io.BytesIO(loader(f"pose:{pose_id}"))).convert("RGBA")
    except Exception as e:
        problems.append({"code": "asset", "detail": f"character pose {pose_id} unusable ({type(e).__name__})"})
        return {}
    alpha = fig.getchannel("A")
    box = alpha.getbbox()
    if alpha.getextrema()[0] > 0 or not box:
        problems.append({"code": "character_not_transparent",
                         "detail": f"character pose {pose_id} has no transparent background"})
        return {}
    fig = fig.crop(box)                                   # the figure itself, not its empty margin
    W, H = size
    h = max(1, round(H * float(ch.get("scale_pct") or CHARACTER_DEFAULTS["scale_pct"]) / 100))
    w = max(1, round(fig.width * h / fig.height))
    fig = fig.resize((w, h), Image.LANCZOS)
    if ch.get("flip"):
        fig = ImageOps.mirror(fig)
    cx = W - W * float(ch.get("x_pct", CHARACTER_DEFAULTS["x_pct"])) / 100
    bottom = H * float(ch.get("y_pct", CHARACTER_DEFAULTS["y_pct"])) / 100
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

def _layout(block: dict, text: str, index: int, size: tuple, m: dict, roles: dict, problems: list) -> dict:
    W, H = size
    where = f"text_blocks[{index}] ({block['role']})"
    direction, language = direction_of(text)
    align = block.get("align") or "right"
    if direction == "rtl" and align == "left":
        problems.append({"code": "hebrew_align", "block": index, "detail": f"{where}: Hebrew is never left-aligned"})
        align = "right"
    try:
        font = _font(block["font_family"], block["size"], block["weight"])
    except ValueError as e:
        problems.append({"code": "weight", "block": index, "detail": f"{where}: {e}"})
        return {}
    box_w = W * float(block["max_width_pct"]) / 100
    right = W - m["right"] - W * float(block.get("x_pct") or 0) / 100
    left = right - box_w
    if left < m["left"] - 0.5:
        problems.append({"code": "overflow", "block": index,
                         "detail": f"{where}: the box ({round(box_w)} px wide, {round(W - m['right'] - right)} px in "
                                   f"from the right margin) runs past the left margin"})
    try:
        lines = wrap(text, font, box_w, direction, language)
    except _TooWide as e:
        problems.append({"code": "overflow", "block": index,
                         "detail": f"{where}: the word {str(e)!r} is wider than the box at {block['size']} px"})
        return {}
    ascent, descent = font.getmetrics()
    lh = block["size"] * float(block.get("line_spacing") or 1.2)
    text_h = ascent + descent + (len(lines) - 1) * lh
    y = H * float(block["y_pct"]) / 100
    top = {"top": y, "center": y - text_h / 2, "bottom": y - text_h}[block["anchor"]]
    if block.get("max_height_pct"):
        max_h = H * float(block["max_height_pct"]) / 100
        if text_h > max_h + 0.5:
            fits = max(1, int((max_h - ascent - descent) // lh) + 1)
            problems.append({"code": "overflow", "block": index,
                             "detail": f"{where}: {len(lines)} lines need {round(text_h)} px, the box holds "
                                       f"{round(max_h)} px (about {fits} lines)"})
    if top < m["top"] - 0.5 or top + text_h > H - m["bottom"] + 0.5:
        problems.append({"code": "overflow", "block": index,
                         "detail": f"{where}: the text ({round(top)}–{round(top + text_h)} px) leaves the safe area "
                                   f"({m['top']}–{H - m['bottom']} px)"})
    widths = [_measure(font, ln, direction, language) for ln in lines]
    widest = max(widths) if widths else 0
    if align == "right":
        x0 = right - widest
    elif align == "center":
        x0 = (left + right) / 2 - widest / 2
    else:
        x0 = left
    return {"block": block, "index": index, "lines": lines, "widths": widths, "font": font, "lh": lh,
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
        draw.text((x, baseline), line, font=lay["font"], fill=lay["colour"], anchor="rs",
                  direction=lay["direction"], language=lay["language"])


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
                 character: dict = None, warnings: list = None):
    """PNG bytes of ONE slide (None when encode=False — a guard-only pass),
    or RenderError with every problem found. `character` = this slide's own
    agent/pose (merged over the spec's placement); `warnings` (a list) gets
    the character's overflow / text-collision warnings."""
    require_raqm()
    spec = slide_spec.for_slide(slide_spec.validate(spec), role)
    ch = merge_character(spec.get("character"), character)
    if ch:
        slide_spec.validate_character(ch)
    with _LOCK:
        return _render(spec, roles, copy_headline, copy_body, n, total, image_loader, encode, ch,
                       warnings if warnings is not None else [])


def _render(spec, roles, copy_headline, copy_body, n, total, loader, encode, ch=None, warnings=None):
    problems = []
    warnings = warnings if warnings is not None else []
    size = (spec["canvas"]["width"], spec["canvas"]["height"])
    m = slide_spec.margins(spec)
    img = _background(spec["background"], size, roles, loader, problems)
    accent = _accent(img, spec.get("accent") or {}, size, m, roles, n, problems)
    char = _character(img, ch, size, loader, problems, warnings) if ch else {}
    _logo(img, spec.get("logo") or {}, size, m, loader, problems)
    limit = int((spec.get("guards") or {}).get("headline_max_words") or HEADLINE_MAX_WORDS)
    layouts = []
    for i, block in enumerate(spec["text_blocks"]):
        text = slide_spec.bind_text(block, copy_headline, copy_body, n, total)
        if not text:
            continue
        if block["role"] == "headline" and len(text.split()) > limit:
            problems.append({"code": "headline_words", "block": i,
                             "detail": f"text_blocks[{i}] (headline): {len(text.split())} words, the limit is {limit}"})
        lay = _layout(block, text, i, size, m, roles, problems)
        if lay:
            layouts.append(lay)
    counter = _counter_layout(spec.get("counter") or {}, size, m, roles, n, total)
    boxes = [(lay["where"], lay["rect"]) for lay in layouts] + ([("counter", counter["rect"])] if counter else [])
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _intersects(boxes[i][1], boxes[j][1]):
                problems.append({"code": "overlap", "detail": f"{boxes[i][0]} and {boxes[j][0]} overlap"})
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
    for lay in layouts:
        _draw_text(draw, lay)
    if counter:
        _draw_counter(draw, counter)
    if not encode:
        return None
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _counter_layout(counter: dict, size: tuple, m: dict, roles: dict, n: int, total: int) -> dict:
    if not counter.get("show") or total <= 1:
        return {}
    style = counter.get("style") or "fraction"
    pos = counter.get("position") or "bottom_left"
    colour = _rgb(roles[counter.get("color_role") or "muted"])
    x, y = _corner_point(pos, size, m)
    if style == "dots":
        d, gap = 12, 10
        w, h = total * d + (total - 1) * gap, d
        text, font = "", None
    else:
        font = _font("Assistant", COUNTER_SIZE, 600)
        text = f"{n}/{total}"
        w = _measure(font, text, "ltr", "en")
        ascent, descent = font.getmetrics()
        h = ascent + descent
    x0 = x - w if pos.endswith("right") else x
    y0 = y if pos.startswith("top") else y - h
    return {"style": style, "rect": [x0, y0, x0 + w, y0 + h], "colour": colour, "n": n, "total": total,
            "text": text, "font": font, "bg": _rgb(roles["bg"])}


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


def render_set(spec: dict, roles: dict, slides: list, image_loader=None, encode: bool = True,
               warnings: list = None) -> list:
    """Every slide of ONE set from ONE spec and ONE palette (the caller passes
    one of each — a set is never mixed). slides = [{"copy_headline",
    "copy_body", "role", "n"?, "total"?}] — n / total = the slide's place in
    the whole post (default: its place in this list). RenderError lists the
    problems of every slide."""
    out, problems = [], []
    for i, s in enumerate(slides, 1):
        n, total = int(s.get("n") or i), int(s.get("total") or len(slides))
        try:
            got = []
            out.append(render_slide(spec, roles, s.get("copy_headline", ""), s.get("copy_body", ""), n, total,
                                    s.get("role", ""), image_loader, encode, character=s.get("character"),
                                    warnings=got))
            if warnings is not None:
                warnings += [{**w, "slide": n, "detail": f"slide {n}: {w['detail']}"} for w in got]
        except RenderError as e:
            problems += [{**p, "slide": n, "detail": f"slide {n}: {p['detail']}"} for p in e.problems]
    if problems:
        raise RenderError(problems)
    return out
