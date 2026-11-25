"""THE table of social formats (2026-11-25, docs/SOCIAL-FORMATS.md).

Every pixel size, aspect ratio and safe zone of anything heading to a social
channel is decided HERE and nowhere else: the slide renderer's canvases, the
post contract's aspect_ratio, the generation requests (what is asked of the
image model and what the result is cropped to), the Social tab's preview.
tests/test_social_formats.py fails on a size or ratio literal in any other
module of core/ or agents/ (the vendors' own documented enums in
core/media_gen_service are the one listed exception: they are the vendor's
facts, not our choice).

- A post's ratio is DERIVED (for_post): reel / story → vertical; everything
  else → the feed default. The agent does not choose it.
- The image model has no 4:5: it is asked for its nearest (3:4) at 1080p and
  the result is cropped from the centre (core/image_fit). What came back is
  MEASURED (audit) — never assumed — and anything narrower than the publish
  width alerts instead of shipping quietly.
- A vertical text slide is laid out inside the SAFE BOX of the channels it
  goes to (zone_for → insets); a feed slide uses the whole canvas.
"""
import io
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Format:
    name: str
    width: int
    height: int
    aspect: str             # "W:H" as the platforms write it
    kind: str               # feed | vertical | cover | site
    publishable: bool = True
    note: str = ""

    @property
    def size(self) -> tuple:
        return (self.width, self.height)

    @property
    def ratio(self) -> float:
        return self.width / self.height


FORMATS = {f.name: f for f in (
    Format("feed_portrait", 1080, 1350, "4:5", "feed", note="the default for all feed output"),
    Format("feed_square", 1080, 1080, "1:1", "feed"),
    Format("feed_landscape", 1080, 566, "1.91:1", "feed"),
    Format("feed_tall", 1080, 1440, "3:4", "feed", publishable=False,
           note="app-only: Meta's publishing API rejects it — never a publish format; it is the ratio the "
                "image model is asked for before the crop to feed_portrait"),
    Format("vertical", 1080, 1920, "9:16", "vertical", note="story / reels / tiktok / shorts"),
    Format("reel_cover", 420, 654, "1:1.55", "cover"),
    Format("site_banner", 1920, 1080, "16:9", "site", publishable=False,
           note="a website banner — not a social format, listed so no second table exists"),
)}

DEFAULT_FEED = "feed_portrait"
VERTICAL = "vertical"

FEED_ASPECT = FORMATS[DEFAULT_FEED].aspect
SQUARE_ASPECT = FORMATS["feed_square"].aspect
LANDSCAPE_ASPECT = FORMATS["feed_landscape"].aspect
TALL_ASPECT = FORMATS["feed_tall"].aspect
VERTICAL_ASPECT = FORMATS[VERTICAL].aspect
BANNER_ASPECT = FORMATS["site_banner"].aspect
PUBLISH_WIDTH = FORMATS[DEFAULT_FEED].width          # nothing is published narrower than this

# The ratios the slide renderer draws (knowledge/slide-spec.schema.json's canvas enum is checked against it)
SLIDE_FORMATS = (DEFAULT_FEED, "feed_square", VERTICAL)
SLIDE_ASPECTS = tuple(FORMATS[n].aspect for n in SLIDE_FORMATS)
CANVASES = {FORMATS[n].aspect: FORMATS[n].size for n in SLIDE_FORMATS}
DEFAULT_CANVAS = FORMATS[DEFAULT_FEED].size

# What an image / video for a platform is made at when there is no post contract (the old free-brief path)
PLATFORM_IMAGE_FORMAT = {"instagram": DEFAULT_FEED, "facebook": DEFAULT_FEED, "tiktok": VERTICAL,
                         "website": "site_banner"}
PLATFORM_VIDEO_FORMAT = {"instagram": VERTICAL, "facebook": VERTICAL, "tiktok": VERTICAL}

# ─── Generation ──────────────────────────────────────────────────────────────

GENERATION_RESOLUTION = "1080p"         # never 720p for anything going to a feed
GENERATION_FALLBACK_ASPECT = TALL_ASPECT   # the model has no 4:5: its nearest, then a centre crop
POSE_ASPECT = TALL_ASPECT               # a character cut-out (never published by itself)

# ─── Carousel ────────────────────────────────────────────────────────────────

CAROUSEL_MAX_SLIDES = 20
IMAGE_MAX_BYTES = 30 * 1024 * 1024

# ─── Safe zones, px on the vertical canvas ───────────────────────────────────

SAFE_ZONES = {
    # IG Story, IG Reels, FB Story, FB Reels — one zone since March 2026 (14% / 35% / 6%)
    "meta_unified": {"top": 269, "bottom": 672, "left": 65, "right": 65},
    # TikTok's bottom reserve GROWS with the caption: a floor, not a fixed number
    "tiktok": {"top": 130, "bottom": 484, "left": 44, "right": 140, "bottom_is_floor": True},
    # one file for more than one of the above
    "cross_platform": {"top": 270, "bottom": 672, "left": 65, "right": 140},
}
ZONE_CHANNELS = {"meta_unified": ("instagram", "facebook"), "tiktok": ("tiktok",)}
SIDES = ("top", "right", "bottom", "left")
PLACEMENTS = ("feed", "story")
VERTICAL_POST_FORMATS = ("reel",)


def get(name: str) -> Format:
    return FORMATS[name]


def by_aspect(aspect: str):
    """The format with that ratio string, or None."""
    return next((f for f in FORMATS.values() if f.aspect == str(aspect or "").strip()), None)


def canvas(aspect: str) -> tuple:
    """(width, height) the renderer draws for a ratio; the feed default for anything else."""
    return CANVASES.get(str(aspect or "").strip(), DEFAULT_CANVAS)


def slide_aspect(aspect) -> str:
    """A ratio the renderer draws, else the feed default."""
    return aspect if aspect in SLIDE_ASPECTS else FEED_ASPECT


def for_post(post_format: str, placement: str = "") -> Format:
    """THE format of a post: a reel or a story is vertical, everything else is
    the feed default. Derived — the agent never picks a ratio."""
    if str(post_format or "").strip().lower() in VERTICAL_POST_FORMATS or str(placement or "").strip().lower() == "story":
        return FORMATS[VERTICAL]
    return FORMATS[DEFAULT_FEED]


def aspect_for_post(post_format: str, placement: str = "") -> str:
    return for_post(post_format, placement).aspect


def is_vertical(aspect: str) -> bool:
    return str(aspect or "").strip() == VERTICAL_ASPECT


def zone_for(channels) -> str:
    """The safe zone of a vertical file going to `channels`: one family →
    that family's zone; more than one (or a channel with no zone of its own)
    → cross_platform, the strictest."""
    wanted = {str(c).strip().lower() for c in (channels or []) if str(c).strip()}
    if not wanted:
        return "meta_unified"
    hits = [zone for zone, chans in ZONE_CHANNELS.items() if wanted & set(chans)]
    covered = set().union(*(ZONE_CHANNELS[z] for z in hits)) if hits else set()
    if len(hits) == 1 and wanted <= covered:
        return hits[0]
    return "cross_platform"


def insets(zone: str) -> dict:
    """{"top", "right", "bottom", "left"} px of a zone ({} for none)."""
    z = SAFE_ZONES.get(str(zone or ""))
    return {k: int(z[k]) for k in SIDES} if z else {}


def safe_box(zone: str) -> dict:
    """The usable box of a zone on the vertical canvas: x, y, width, height."""
    i = insets(zone)
    if not i:
        return {}
    w, h = FORMATS[VERTICAL].size
    return {"x": i["left"], "y": i["top"], "width": w - i["left"] - i["right"], "height": h - i["top"] - i["bottom"]}


def carousel_problems(sizes: list, byte_sizes: list = None) -> list:
    """What breaks the carousel rules: every slide the FIRST slide's ratio,
    at most CAROUSEL_MAX_SLIDES, each image at most IMAGE_MAX_BYTES."""
    out = []
    if len(sizes) > CAROUSEL_MAX_SLIDES:
        out.append(f"{len(sizes)} slides — a carousel takes at most {CAROUSEL_MAX_SLIDES}")
    if sizes:
        first = sizes[0][0] / sizes[0][1]
        for n, (w, h) in enumerate(sizes[1:], 2):
            if abs(w / h - first) > 0.005:
                out.append(f"slide {n} is {w}x{h}: not the first slide's ratio ({sizes[0][0]}x{sizes[0][1]})")
    for n, size in enumerate(byte_sizes or [], 1):
        if size > IMAGE_MAX_BYTES:
            out.append(f"slide {n} is {size // (1024 * 1024)} MB — over {IMAGE_MAX_BYTES // (1024 * 1024)} MB")
    return out


# ─── What actually came back (measured, never assumed) ───────────────────────

def measure(content: bytes) -> tuple:
    """(width, height) of image bytes; (0, 0) when unreadable."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as img:
            return img.size
    except Exception:
        return (0, 0)


def audit(content: bytes = None, aspect: str = "", size: tuple = None) -> dict:
    """{"width", "height", "ok", "reason"} for an image heading to a channel
    at `aspect`: ok = at least the publish width across, and on the short side."""
    w, h = size if size else measure(content or b"")
    fmt = by_aspect(aspect) or FORMATS[DEFAULT_FEED]
    need = min(PUBLISH_WIDTH, fmt.width) if fmt.kind == "cover" else PUBLISH_WIDTH
    out = {"width": w, "height": h, "aspect": fmt.aspect, "ok": True, "reason": ""}
    if not w or not h:
        out.update(ok=False, reason="unreadable image — its size could not be measured")
    elif w < need:
        out.update(ok=False, reason=f"{w}x{h}: narrower than the {need} px a {fmt.aspect} post is published at")
    return out


def report(what: str, content: bytes = None, aspect: str = "", size: tuple = None, source: str = "social_formats") -> dict:
    """audit + the log line with the REAL dimensions + an alert when it is
    below the publish width. Never raises. `what` names the image for the
    alert ("client 27: set/asset generated image")."""
    try:
        found = audit(content, aspect, size)
        print(f"[{source}] {what}: {found['width']}x{found['height']} (for {found['aspect']})")
        if not found["ok"]:
            from core.agent_base import agent_alert
            agent_alert(source, [f"{what}: {found['reason']} — below the publish size, not shipped quietly"])
        return found
    except Exception as e:
        print(f"[{source}] size of {what} not checked ({type(e).__name__})")
        return {"width": 0, "height": 0, "aspect": aspect, "ok": True, "reason": ""}


def table() -> dict:
    """The whole table as plain data (the Social tab's preview reads it:
    GET /api/social/formats — the page keeps no copy of these numbers)."""
    return {"formats": {n: {**asdict(f), "ratio": round(f.ratio, 5)} for n, f in FORMATS.items()},
            "default_feed": DEFAULT_FEED, "vertical": VERTICAL, "publish_width": PUBLISH_WIDTH,
            "safe_zones": {z: {**insets(z), "box": safe_box(z), "bottom_is_floor": bool(v.get("bottom_is_floor"))}
                           for z, v in SAFE_ZONES.items()},
            "carousel": {"max_slides": CAROUSEL_MAX_SLIDES, "max_image_bytes": IMAGE_MAX_BYTES},
            "generation": {"resolution": GENERATION_RESOLUTION, "fallback_aspect": GENERATION_FALLBACK_ASPECT}}
