"""Does a display image survive Google's crops? (2026-11-28,
knowledge/paid-media-craft.md §2.1, docs/PAID-MEDIA.md §2)

Google shows one 1.91:1 image in dozens of placements, down to a square and a
4:1 strip, always cut from the CENTRE. A subject sitting off-centre is simply
gone. This module does not advise — it takes the PRODUCED file, makes the
crops the table names for its slot (core/google_ads_formats.CROP_TARGETS) and
measures what is left of the subject in each one. The same rule as the render
manifest: what is said about a file comes from the file.

How the subject is found (no model, no network — pixels only):

  1. the image is reduced to a small working copy;
  2. the BACKGROUND is what the border shows: the median colour of the outer
     ring, and how much the ring itself varies (a textured backdrop is not a
     subject);
  3. every pixel's distance from that background, less that variation, is its
     weight — the subject is where the weight is. A picture with no colour
     separation falls back to its edges, measured the same way;
  4. a crop SURVIVES when it keeps at least MIN_RETAINED of the weight.

A producer that knows where it put the product (a composited cut-out, a badge)
passes `subject_box` — then the box itself must sit inside every crop
(MIN_BOX_INSIDE), and the pixels are still measured.

What this is NOT: understanding. It cannot tell a product from a bright lamp
behind it, and "readable" is approximated by "still in frame". It fails
closed — an image with no findable subject does not pass.

The ratios come from the table. The thresholds below are judgment, not a
Google spec, so they live here.
"""
import io

from core import google_ads_formats as gaf
from core import image_fit

SERVICE_NAME = "crop_survival"
WORK_WIDTH = 256            # the working copy's long side, px
BORDER_SHARE = 0.06         # the outer ring that stands for the background
MIN_RETAINED = 0.70         # share of the subject's weight a crop must keep
MIN_BOX_INSIDE = 0.90       # share of a declared subject box a crop must keep
MIN_SUBJECT_SHARE = 0.004   # under this share of the frame carrying weight: no subject was found
SUBJECT_LEVEL = 0.25        # a pixel is "subject" above this share of the strongest weight


def centre_box(width: int, height: int, aspect: str) -> tuple:
    """(x0, y0, x1, y1) of the largest centred `aspect` box inside width x height."""
    want = image_fit.ratio(aspect)
    if width / height > want:
        w, h = max(1, round(height * want)), height
    else:
        w, h = width, max(1, round(width / want))
    x0, y0 = (width - w) // 2, (height - h) // 2
    return (x0, y0, x0 + w, y0 + h)


def _ring(size: tuple) -> list:
    """The four border strips of a working copy, as boxes."""
    w, h = size
    b = max(2, round(min(w, h) * BORDER_SHARE))
    return [(0, 0, w, b), (0, h - b, w, h), (0, b, b, h - b), (w - b, b, w, h - b)]


def _mid(values: list) -> float:
    ordered = sorted(values)
    return (ordered[1] + ordered[2]) / 2 if len(ordered) == 4 else ordered[len(ordered) // 2]


def _weight_map(small):
    """(L image of subject weight, method). Colour distance from the border's
    median colour, less the border's own spread; edges when colour finds nothing."""
    from PIL import Image, ImageChops, ImageFilter, ImageStat
    soft = small.filter(ImageFilter.GaussianBlur(1.2))
    strips = [soft.crop(box) for box in _ring(soft.size)]
    background = tuple(int(_mid([ImageStat.Stat(s).median[c] for s in strips])) for c in range(3))
    diff = ImageChops.difference(soft, Image.new("RGB", soft.size, background))
    r, g, b = diff.split()
    distance = ImageChops.lighter(ImageChops.lighter(r, g), b)

    def floored(layer):
        ring = [layer.crop(box) for box in _ring(layer.size)]
        floor = _mid([ImageStat.Stat(s).mean[0] + 2 * ImageStat.Stat(s).stddev[0] for s in ring])
        return layer.point(lambda v: max(0, int(v - floor)))

    weight = floored(distance)
    if _covered(weight) >= MIN_SUBJECT_SHARE:
        return weight, "colour"
    edges = soft.convert("L").filter(ImageFilter.FIND_EDGES)
    w, h = edges.size                             # the filter invents an edge along the frame itself: not a subject
    framed = Image.new("L", edges.size, 0)
    framed.paste(edges.crop((2, 2, w - 2, h - 2)), (2, 2))
    return floored(framed), "edges"


def _covered(weight) -> float:
    """Share of the frame whose weight reaches SUBJECT_LEVEL of the strongest."""
    top = weight.getextrema()[1]
    if top < 12:                                  # nothing rises above noise
        return 0.0
    level = max(12, top * SUBJECT_LEVEL)
    hist = weight.histogram()
    return sum(hist[int(level):]) / float(weight.size[0] * weight.size[1])


def _mass(weight, box=None) -> float:
    from PIL import ImageStat
    return float(ImageStat.Stat(weight.crop(box) if box else weight).sum[0])


def _subject_box(weight) -> tuple:
    top = weight.getextrema()[1]
    level = max(12, top * SUBJECT_LEVEL)
    return weight.point(lambda v: 255 if v >= level else 0).getbbox()


def _inside(box: tuple, crop: tuple) -> float:
    """Share of `box`'s area that lies inside `crop`."""
    area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
    w = max(0, min(box[2], crop[2]) - max(box[0], crop[0]))
    h = max(0, min(box[3], crop[3]) - max(box[1], crop[1]))
    return w * h / area


def check(content: bytes, slot: str, subject_box: tuple = None, targets: tuple = None) -> dict:
    """The crop-survival result of ONE produced image for `slot` (a
    google_ads_formats image slot):

        {"ok", "slot", "size": [w, h], "method": colour | edges | none,
         "subject": {"box": [x0, y0, x1, y1] px in the file, "share": of the frame},
         "crops": [{"aspect", "box": [...] px in the file, "size": [w, h],
                    "retained": 0..1, "box_inside": 0..1 | None, "ok", "reason"}],
         "problems": [plain lines, one per failed crop]}

    `subject_box` = (x0, y0, x1, y1) as FRACTIONS of the frame, when the
    producer knows where the product is. `targets` overrides the table's crops
    for the slot (tests). Never raises: an unreadable file is a failed check."""
    from PIL import Image
    wanted = tuple(targets if targets is not None else gaf.CROP_TARGETS.get(slot, ()))
    out = {"ok": False, "slot": slot, "size": [0, 0], "method": "none", "subject": {}, "crops": [], "problems": []}
    try:
        with Image.open(io.BytesIO(content)) as opened:
            img = opened.convert("RGB")
    except Exception:
        out["problems"].append("the file is not a readable image — nothing could be cropped")
        return out
    W, H = img.size
    out["size"] = [W, H]
    scale = WORK_WIDTH / float(max(W, H))
    small = img.resize((max(8, round(W * scale)), max(8, round(H * scale))), Image.BILINEAR)
    weight, method = _weight_map(small)
    total = _mass(weight)
    found = _subject_box(weight) if total > 0 and _covered(weight) >= MIN_SUBJECT_SHARE else None
    if not found:
        out["problems"].append("no subject could be found in the image (nothing stands out from its background) — "
                               "a display image needs one clear subject")
        return out
    out["method"] = method
    sw, sh = small.size
    out["subject"] = {"box": [round(found[0] * W / sw), round(found[1] * H / sh),
                              round(found[2] * W / sw), round(found[3] * H / sh)],
                      "share": round(_covered(weight), 4)}
    declared = None
    if subject_box:
        declared = (subject_box[0] * W, subject_box[1] * H, subject_box[2] * W, subject_box[3] * H)
    for aspect in wanted:
        box = centre_box(W, H, aspect)
        small_box = (round(box[0] * sw / W), round(box[1] * sh / H), round(box[2] * sw / W), round(box[3] * sh / H))
        retained = _mass(weight, small_box) / total
        box_inside = _inside(declared, box) if declared else None
        row = {"aspect": aspect, "box": list(box), "size": [box[2] - box[0], box[3] - box[1]],
               "retained": round(retained, 3), "box_inside": None if box_inside is None else round(box_inside, 3),
               "ok": True, "reason": ""}
        if retained < MIN_RETAINED:
            row.update(ok=False, reason=f"only {round(retained * 100)}% of the subject is left in the {aspect} centre "
                                        f"crop (at least {round(MIN_RETAINED * 100)}% must be)")
        elif box_inside is not None and box_inside < MIN_BOX_INSIDE:
            row.update(ok=False, reason=f"only {round(box_inside * 100)}% of the declared product area is inside the "
                                        f"{aspect} centre crop (at least {round(MIN_BOX_INSIDE * 100)}% must be)")
        out["crops"].append(row)
        if not row["ok"]:
            out["problems"].append(f"{slot} {W}x{H}: {row['reason']}")
    out["ok"] = not out["problems"]
    return out


def crops(content: bytes, slot: str, targets: tuple = None) -> dict:
    """{aspect: PNG bytes} — the centre crops themselves, for a human to look at."""
    from PIL import Image
    with Image.open(io.BytesIO(content)) as opened:
        img = opened.convert("RGB")
    out = {}
    for aspect in tuple(targets if targets is not None else gaf.CROP_TARGETS.get(slot, ())):
        buf = io.BytesIO()
        img.crop(centre_box(img.size[0], img.size[1], aspect)).save(buf, "PNG")
        out[aspect] = buf.getvalue()
    return out


def line(result: dict) -> str:
    """One log line for a result: slot, measured size, method, then each crop's share and ok / FAIL."""
    parts = ", ".join(f"{c['aspect']} {round(c['retained'] * 100)}% {'ok' if c['ok'] else 'FAIL'}" for c in result["crops"])
    size = "x".join(str(v) for v in result["size"])
    return (f"[{SERVICE_NAME}] {result['slot']} {size} {result['method']}: "
            f"{parts or (result['problems'] or ['nothing to crop for this slot'])[0]}")
