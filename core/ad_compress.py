"""Compress an ad file to its size cap — or fail out loud (2026-11-28,
knowledge/paid-media-craft.md §2.3, docs/PAID-MEDIA.md §2).

Uploaded static banners, Demand Gen logos and companion banners cap at 150 KB.
A 970-wide banner at that weight is a real compression job, not an export
setting. `to_cap` does the job and MEASURES the result against the original:

  - already within the cap, in a type the slot takes → returned untouched;
  - otherwise every encoding the slot takes is tried — JPEG at the highest
    quality that fits (a search, never one guess), a 256-colour PNG, a
    lossless PNG — and each candidate's damage is measured (PSNR against the
    original pixels);
  - the least damaged candidate that fits wins;
  - if none fits without falling under MIN_PSNR → CannotReachCap, naming the
    file, the cap, and how close the best attempt came. Nothing soft ships.

The pixel size is never changed for a banner (its size IS its spec). A slot
with a minimum instead of an exact size (a logo) may be stepped down, never
below `floor` — and that is recorded.

The cap comes from the order row (core/google_ads_formats). MIN_PSNR and
MIN_QUALITY are judgment, not a Google spec: PSNR is a stand-in for "no
visible degradation", not a pair of eyes.
"""
import io
import math

from core import google_ads_formats as gaf

SERVICE_NAME = "ad_compress"
MIN_QUALITY = 60            # JPEG is never pushed under this
MAX_QUALITY = 95
MIN_PSNR = 33.0             # dB against the original; under it the damage is taken to be visible
STEP_DOWN = 0.85            # a resizable slot shrinks by this per step
LOSSLESS = 99.0             # reported PSNR of an identical image


class CannotReachCap(RuntimeError):
    """The file cannot be brought under its cap without visible degradation."""

    def __init__(self, what: str, cap: int, best: dict):
        self.what, self.cap, self.best = what, cap, best
        if best:
            detail = (f"the best attempt under the cap is {best['psnr']:.1f} dB ({best['method']}), below the "
                      f"{MIN_PSNR:g} dB floor" if best.get("fits") else
                      f"the lightest acceptable version is {gaf.kb(best['bytes'])} KB ({best['method']})")
        else:
            detail = "no encoding this slot takes could be produced"
        super().__init__(f"{what}: cannot reach {cap // gaf.KB} KB without visible degradation — {detail}")


def psnr(original, candidate) -> float:
    """Peak signal-to-noise ratio of two same-size RGB images, dB (LOSSLESS when identical)."""
    from PIL import ImageChops, ImageStat
    rms = ImageStat.Stat(ImageChops.difference(original, candidate)).rms
    mse = sum(v * v for v in rms) / len(rms)
    return LOSSLESS if mse <= 1e-9 else min(LOSSLESS, 10 * math.log10(255 * 255 / mse))


def _has_alpha(img) -> bool:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        return img.convert("RGBA").getchannel("A").getextrema()[0] < 255
    return False


def _flat(img):
    """RGB for measuring: transparency over white (what a white page shows)."""
    from PIL import Image
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    return img.convert("RGB")


def _jpeg(rgb, quality: int) -> bytes:
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _candidates(img, cap: int, file_types: tuple) -> list:
    """Every encoding tried for one pixel size: [{"content", "file_type",
    "bytes", "psnr", "method", "quality", "fits"}]."""
    from PIL import Image
    reference = _flat(img)
    alpha = _has_alpha(img)
    out = []

    def add(content, kind, method, quality=None, shown=None):
        with Image.open(io.BytesIO(content)) as back:
            score = psnr(reference, _flat(back)) if shown is None else shown
        out.append({"content": content, "file_type": kind, "bytes": len(content), "psnr": round(score, 2),
                    "method": method, "quality": quality, "fits": len(content) <= cap})

    if "png" in file_types:
        add(_png(img if alpha else img.convert("RGB")), "png", "png lossless", shown=LOSSLESS)
        palette = (img.convert("RGBA").quantize(256, method=Image.Quantize.FASTOCTREE) if alpha
                   else img.convert("RGB").quantize(256, method=Image.Quantize.MEDIANCUT))
        add(_png(palette), "png", "png 256 colours")
    if "jpg" in file_types and not alpha:          # JPEG has no transparency: a cut-out logo is never flattened
        rgb = img.convert("RGB")
        top = _jpeg(rgb, MAX_QUALITY)
        if len(top) <= cap:
            add(top, "jpg", f"jpeg q{MAX_QUALITY}", MAX_QUALITY)
        else:
            low, high, best = MIN_QUALITY, MAX_QUALITY - 1, None
            while low <= high:                     # the highest quality that fits
                mid = (low + high) // 2
                data = _jpeg(rgb, mid)
                if len(data) <= cap:
                    best, low = (data, mid), mid + 1
                else:
                    high = mid - 1
            if best:
                add(best[0], "jpg", f"jpeg q{best[1]}", best[1])
            else:
                floor = _jpeg(rgb, MIN_QUALITY)    # does not fit even at the floor: kept only to report how far off
                add(floor, "jpg", f"jpeg q{MIN_QUALITY}", MIN_QUALITY)
    return out


def _pick(found: list):
    good = [c for c in found if c["fits"] and c["psnr"] >= MIN_PSNR]
    return max(good, key=lambda c: (c["psnr"], -c["bytes"])) if good else None


def _closest(found: list) -> dict:
    """The attempt to name in the failure: the best that fits (too damaged), else the lightest acceptable."""
    fitting = [c for c in found if c["fits"]]
    if fitting:
        return max(fitting, key=lambda c: c["psnr"])
    return min(found, key=lambda c: c["bytes"]) if found else {}


def to_cap(content: bytes, max_bytes: int, file_types: tuple = ("jpg", "png"), what: str = "file",
           floor: tuple = None) -> dict:
    """`content` within `max_bytes` in one of `file_types`:

        {"content", "file_type", "bytes", "original_bytes", "size": [w, h], "psnr", "method",
         "quality", "changed": bool, "resized_from": [w, h] | None}

    `floor` = (min_width, min_height): the slot has a minimum, not an exact
    size, so the picture may be stepped down to it when no encoding fits at
    full size. None = the pixel size is the spec (a banner) and never moves.
    Raises CannotReachCap; ValueError for an unreadable file."""
    from PIL import Image
    file_types = tuple(file_types)
    try:
        with Image.open(io.BytesIO(content)) as opened:
            opened.load()
            img = opened.copy()
    except Exception:
        raise ValueError(f"{what}: not a readable image")
    kind = gaf.file_type(content)
    base = {"original_bytes": len(content), "size": list(img.size), "resized_from": None}
    if len(content) <= max_bytes and kind in file_types:
        return {**base, "content": content, "file_type": kind, "bytes": len(content), "psnr": LOSSLESS,
                "method": "as is", "quality": None, "changed": False}
    tried = _candidates(img, max_bytes, file_types)
    best = _pick(tried)
    if best:
        return {**base, **{k: best[k] for k in ("content", "file_type", "bytes", "psnr", "method", "quality")},
                "changed": True}
    closest = _closest(tried)
    if floor:
        original, current = img.size, img
        while True:
            w, h = round(current.size[0] * STEP_DOWN), round(current.size[1] * STEP_DOWN)
            if w < floor[0] or h < floor[1]:
                break
            current = img.resize((w, h), Image.LANCZOS)
            best = _pick(_candidates(current, max_bytes, file_types))   # damage measured at the new size
            if best:
                return {**base, **{k: best[k] for k in ("content", "file_type", "bytes", "psnr", "method", "quality")},
                        "size": [w, h], "resized_from": list(original), "changed": True,
                        "method": f"{best['method']}, stepped down from {original[0]}x{original[1]}"}
    raise CannotReachCap(what, max_bytes, {k: v for k, v in closest.items() if k != "content"})


def line(what: str, result: dict) -> str:
    return (f"[{SERVICE_NAME}] {what}: {gaf.kb(result['original_bytes'])} KB -> {gaf.kb(result['bytes'])} KB "
            f"({result['method']}, {result['psnr']:.1f} dB, {result['size'][0]}x{result['size'][1]})")
