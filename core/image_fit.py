"""A generated image at the REQUESTED aspect ratio, exactly (2026-11-25).

A model gets the requested ratio when its documented enum has it; otherwise
the NEAREST ratio it has (core/media_gen_service), and the result is cropped
here from the centre — 4:5 from 3:4 = 6.25% off the height. Used by post
images (media_gen_service.generate_image) and the agents' characters
(core/agent_characters).

With transparency (a cut-out) the crop window moves to keep every visible
pixel — head and feet are never clipped; when the figure does not fit the
window, nothing is cropped and `warning` says so.
"""
import io

CROP_TOLERANCE = 0.005


def ratio(ar: str) -> float:
    w, _, h = str(ar or "").partition(":")
    return float(w) / float(h)


def needs_crop(target: str, sent: str) -> bool:
    try:
        return bool(target and sent) and abs(ratio(target) - ratio(sent)) > CROP_TOLERANCE
    except (ValueError, ZeroDivisionError):
        return False


def nearest(target: str, allowed: tuple) -> str:
    """`target` when `allowed` has it, else the allowed ratio closest to it
    (by log-ratio); an unreadable target → the first allowed one."""
    if target in allowed:
        return target
    import math
    try:
        want = ratio(target)
    except (ValueError, ZeroDivisionError):
        return allowed[0]
    return min(allowed, key=lambda a: (abs(math.log(ratio(a) / want)), a))


def fit_aspect(content: bytes, target: str, sent: str = "") -> dict:
    """{"content", "cropped", "warning"} — see the module docstring."""
    out = {"content": content, "cropped": False, "warning": ""}
    if not target or (sent and not needs_crop(target, sent)):
        return out
    from PIL import Image
    img = Image.open(io.BytesIO(content))
    w, h = img.size
    want = ratio(target)
    if abs(w / h - want) <= CROP_TOLERANCE:
        return out
    if w / h > want:                                   # too wide: cut the sides
        box_w, box_h = max(1, round(h * want)), h
    else:                                              # too tall: cut top and bottom
        box_w, box_h = w, max(1, round(w / want))
    x0, y0 = (w - box_w) // 2, (h - box_h) // 2
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        bbox = img.convert("RGBA").getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
        if bbox:
            left, top, right, bottom = bbox
            if right - left > box_w or bottom - top > box_h:
                out["warning"] = (f"character_crop_skipped: the figure ({right - left}x{bottom - top}) does not fit "
                                  f"a {target} crop of {w}x{h} — left uncropped rather than clipped")
                return out
            x0 = min(max(x0, right - box_w), left)
            y0 = min(max(y0, bottom - box_h), top)
    cropped = img.crop((x0, y0, x0 + box_w, y0 + box_h))
    buf = io.BytesIO()
    cropped.save(buf, "PNG")
    return {"content": buf.getvalue(), "cropped": True, "warning": ""}
