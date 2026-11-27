"""THE table of Google Ads media specs (2026-11-28, docs/PAID-MEDIA.md).

The same role core/social_formats has for social: every pixel size, aspect
ratio, character limit, asset count and file-size cap of anything heading to
Google Ads is decided HERE and nowhere else. tests/test_google_ads_formats.py
fails on such a literal in any other module of core/ or agents/.

- What a campaign type NEEDS is derived (required_assets / minimum_to_serve):
  the paid-media agent orders from it, the media agent's delivery is checked
  against it, and neither of them picks a size.
- A file is MEASURED (check_file: real type from the bytes, real pixels, real
  byte count) — never assumed from its name or from what was asked for.
- A video is never a file in a campaign: it is uploaded to the client's
  YouTube channel first and referenced by URL (VIDEO_WORKFLOW,
  video_reference_problems). That is a required step, not advice.

These numbers are Google's and they change. VERIFIED / REVERIFY_BY say when
they were last checked; `stale()` is true past the re-verify date and the
order and the delivery check both say so out loud instead of trusting them.
"""
import io
import re
import unicodedata
from dataclasses import dataclass, asdict
from datetime import date

VERIFIED = "2026-11-28"
REVERIFY_BY = "2027-02-28"

KB = 1024
IMAGE_MAX_BYTES = 5120 * KB          # 5 MB: RDA, Performance Max and Demand Gen images
SMALL_MAX_BYTES = 150 * KB           # uploaded banners, Demand Gen logos, companion banners
RATIO_TOLERANCE = 0.01               # a file "is" a ratio within 1%

CAMPAIGN_TYPES = ("pmax", "demand_gen", "display", "youtube", "search")


def stale(today: date = None) -> bool:
    """Past the re-verify date: the table may no longer be Google's."""
    return (today or date.today()).isoformat() > REVERIFY_BY


def stale_line(today: date = None) -> str:
    return (f"Google Ads specs were last verified {VERIFIED} and were due for re-verification by {REVERIFY_BY} — "
            f"re-verify core/google_ads_formats before relying on them") if stale(today) else ""


# ─── Image and logo slots ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Slot:
    name: str
    aspect: str             # "W:H" as Google writes it
    width: int              # recommended
    height: int
    min_width: int
    min_height: int
    kind: str = "image"     # image | logo

    @property
    def size(self) -> tuple:
        return (self.width, self.height)

    @property
    def ratio(self) -> float:
        return self.width / self.height


SLOTS = {s.name: s for s in (
    Slot("landscape", "1.91:1", 1200, 628, 600, 314),
    Slot("square", "1:1", 1200, 1200, 300, 300),
    Slot("portrait", "4:5", 960, 1200, 480, 600),
    Slot("vertical", "9:16", 1080, 1920, 600, 1067),
    Slot("logo_square", "1:1", 1200, 1200, 128, 128, "logo"),
    Slot("logo_landscape", "4:1", 1200, 300, 512, 128, "logo"),
)}

LANDSCAPE_ASPECT = SLOTS["landscape"].aspect
SQUARE_ASPECT = SLOTS["square"].aspect
PORTRAIT_ASPECT = SLOTS["portrait"].aspect
VERTICAL_ASPECT = SLOTS["vertical"].aspect
WIDE_LOGO_ASPECT = SLOTS["logo_landscape"].aspect

# Where Google crops an image of one slot to (knowledge/paid-media-craft.md §2.1):
# a 1.91:1 image is shown in dozens of placements, down to a square and a 4:1
# strip. core/crop_survival crops the PRODUCED file to these and checks the
# subject is still there.
CROP_TARGETS = {"landscape": (SQUARE_ASPECT, WIDE_LOGO_ASPECT),
                "square": (LANDSCAPE_ASPECT,),
                "portrait": (SQUARE_ASPECT,),
                "vertical": (PORTRAIT_ASPECT,)}


def _use(minimum: int, maximum, need: str, recommended=None) -> dict:
    """How a campaign type uses a slot. need: required | recommended | optional.
    maximum None = the verified table states no per-slot ceiling."""
    return {"min": minimum, "max": maximum, "need": need, "recommended": recommended}


def _text(max_chars: int, minimum: int, maximum, short: tuple = None) -> dict:
    """A text asset: its character limit and how many. short = (chars, count):
    at least `count` of them must be `chars` or fewer. maximum None = the
    verified table states no count ceiling."""
    return {"max_chars": max_chars, "min": minimum, "max": maximum,
            "short": {"max_chars": short[0], "min": short[1]} if short else None}


# ─── Video ───────────────────────────────────────────────────────────────────

VIDEO_RATIOS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
HORIZONTAL_VIDEO_ASPECT = "16:9"
VIDEO_FORMATS = {
    "skippable_in_stream": {"min_seconds": None, "max_seconds": None, "skip_after_seconds": 5,
                            "aim_seconds": (15, 30)},
    "non_skippable_in_stream": {"min_seconds": 7, "max_seconds": 15, "connected_tv_seconds": (16, 30),
                                # a 30-second non-skippable MUST be horizontal
                                "over_standard_must_be": HORIZONTAL_VIDEO_ASPECT},
    "bumper": {"min_seconds": 5, "max_seconds": 6, "skippable": False},
    "in_feed": {"min_seconds": None, "max_seconds": None, "recommended_seconds": 15,
                "remarketing_min_seconds": 12},
    "shorts": {"min_seconds": None, "max_seconds": 60, "aspect": VERTICAL_ASPECT},
}
COMPANION_BANNER = {"width": 300, "height": 60, "file_types": ("jpg", "gif", "png"), "max_bytes": SMALL_MAX_BYTES}

# A video cannot be uploaded into a campaign as a file. These are the steps,
# in order; a video asset that has not been through both is not deliverable.
VIDEO_WORKFLOW = ("upload_to_client_youtube_channel", "reference_by_url")
YOUTUBE_URL_RE = re.compile(
    r"^https://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:[^#\s]*&)?v=|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})(?:[?&#][^\s]*)?$")


# ─── Uploaded static display banners ─────────────────────────────────────────

BANNER_FILE_TYPES = ("jpg", "png", "gif")
BANNER_MAX_BYTES = SMALL_MAX_BYTES
BANNERS = {
    "mobile": ((300, 50), (320, 50), (320, 100)),
    "square_rect": ((200, 200), (240, 400), (250, 250), (250, 360), (300, 250), (336, 280), (580, 400)),
    "skyscraper": ((120, 600), (160, 600), (300, 600), (300, 1050)),
    "leaderboard": ((468, 60), (728, 90), (930, 180), (970, 90), (970, 250), (980, 120)),
}
BANNER_SIZES = tuple(size for group in BANNERS.values() for size in group)
TOP_BANNERS = ((300, 250), (336, 280), (728, 90), (300, 600), (320, 100), (320, 50))
BANNER_SETS = {"none": (), "top": TOP_BANNERS, "all": BANNER_SIZES}


def banner_name(size: tuple) -> str:
    return f"banner_{size[0]}x{size[1]}"


# ─── Campaign types ──────────────────────────────────────────────────────────
# minimum = what the platform needs before the asset group / ad serves at all.
# minimum_source: "stated" (the verified table gives it as a minimum),
# "required_marks" (the slots it marks REQUIRED), "derived" (the table states
# no minimum for this type: one of each thing it cannot run without).

_STATIC = ("jpg", "png")              # GIF is NOT accepted in responsive display ads

CAMPAIGNS = {
    "display": {
        "label": "Responsive Display Ad",
        "file_types": _STATIC, "image_max_bytes": IMAGE_MAX_BYTES, "logo_max_bytes": IMAGE_MAX_BYTES,
        "images": {"landscape": _use(1, 15, "required", 5), "square": _use(1, 15, "required", 5),
                   "portrait": _use(0, None, "optional")},
        "images_total_max": None,
        "logos": {"logo_square": _use(1, 5, "required"), "logo_landscape": _use(0, 5, "recommended")},
        "logos_total_max": None,
        "text": {"headline": _text(30, 1, 5), "long_headline": _text(90, 1, 1),
                 "description": _text(90, 1, 5), "business_name": _text(25, 1, 1)},
        "video": None,
        "minimum": {"landscape": 1, "square": 1, "logo_square": 1, "headline": 1, "long_headline": 1,
                    "description": 1, "business_name": 1},
        "minimum_source": "required_marks",
    },
    "pmax": {
        "label": "Performance Max asset group",
        "file_types": _STATIC, "image_max_bytes": IMAGE_MAX_BYTES, "logo_max_bytes": IMAGE_MAX_BYTES,
        "images": {"landscape": _use(1, None, "required"), "square": _use(1, None, "required"),
                   "portrait": _use(0, None, "optional")},
        "images_total_max": 20,           # across all three ratios
        "logos": {"logo_square": _use(1, None, "required"), "logo_landscape": _use(0, None, "recommended")},
        "logos_total_max": 5,
        "text": {"headline": _text(30, 3, 15, short=(15, 1)), "long_headline": _text(90, 1, 5),
                 "description": _text(90, 2, 5), "business_name": _text(25, 1, 1)},
        "video": {"min": 0, "max": 15, "min_seconds": 10},     # the ceiling was 5 until early 2026
        "minimum": {"landscape": 1, "square": 1, "logo_square": 1, "headline": 3, "long_headline": 1,
                    "description": 2, "business_name": 1},
        "minimum_source": "stated",       # a group below it does not serve AT ALL
    },
    "demand_gen": {
        "label": "Demand Gen ad",
        "file_types": _STATIC, "image_max_bytes": IMAGE_MAX_BYTES,
        "logo_max_bytes": SMALL_MAX_BYTES,                     # the odd one: 150 KB, not 5 MB
        "images": {"landscape": _use(0, None, "recommended"), "square": _use(0, None, "recommended"),
                   "portrait": _use(0, None, "recommended"),
                   "vertical": _use(0, None, "recommended")},  # needed for Shorts
        "images_total_max": 20,           # per ad
        "images_any_min": 1,
        "logos": {"logo_square": _use(1, None, "required")},
        "logos_total_max": None,
        "text": {"headline": _text(40, 1, None, short=(30, 1)), "description": _text(90, 1, None),
                 "business_name": _text(25, 1, 1)},
        "video": None,
        "carousel": {"min_cards": 2, "max_cards": 10, "one_ratio": True, "card_headline_max_chars": 40,
                     "logo": "logo_square"},
        "minimum": {"image": 1, "logo_square": 1, "headline": 1, "description": 1, "business_name": 1},
        "minimum_source": "derived",
    },
    "youtube": {
        "label": "YouTube video ad",
        "file_types": (), "image_max_bytes": 0, "logo_max_bytes": 0,
        "images": {}, "images_total_max": None, "logos": {}, "logos_total_max": None,
        "text": {},
        "video": {"min": 1, "max": None, "min_seconds": None},
        "minimum": {"video": 1},
        "minimum_source": "derived",
    },
    "search": {
        "label": "Responsive Search Ad",
        "file_types": (), "image_max_bytes": 0, "logo_max_bytes": 0,
        "images": {}, "images_total_max": None, "logos": {}, "logos_total_max": None,
        # the table states "up to 15 / up to 4"; the floors (3 / 2) are the ones
        # agents/google_ads_agent's validator has always enforced before the API call
        "text": {"headline": _text(30, 3, 15), "description": _text(90, 2, 4), "path": _text(15, 0, 2)},
        "video": None,
        "minimum": {"headline": 3, "description": 2},
        "minimum_source": "derived",
    },
}

TEXT_FIELDS = ("headline", "long_headline", "description", "business_name", "path")


def campaign(campaign_type: str) -> dict:
    """The table's entry for a campaign type; ValueError names the valid ones."""
    key = str(campaign_type or "").strip().lower()
    if key not in CAMPAIGNS:
        raise ValueError(f"unknown campaign type {campaign_type!r}: one of {', '.join(CAMPAIGN_TYPES)}")
    return CAMPAIGNS[key]


def needs_media(campaign_type: str) -> bool:
    """Whether the type takes any image / logo / video asset (search does not)."""
    c = campaign(campaign_type)
    return bool(c["images"] or c["logos"] or (c["video"] and c["video"]["min"]))


def text_limits(campaign_type: str) -> dict:
    return {field: dict(spec) for field, spec in campaign(campaign_type)["text"].items()}


def minimum_to_serve(campaign_type: str) -> dict:
    return dict(campaign(campaign_type)["minimum"])


# ─── What a campaign type needs from the media agent ─────────────────────────

def _row(slot: Slot, c: dict, count: int, need: str) -> dict:
    cap = c["logo_max_bytes"] if slot.kind == "logo" else c["image_max_bytes"]
    return {"slot": slot.name, "kind": slot.kind, "aspect": slot.aspect, "width": slot.width, "height": slot.height,
            "min_width": slot.min_width, "min_height": slot.min_height, "max_bytes": cap,
            "file_types": list(c["file_types"]), "count": count, "need": need,
            "crop_targets": list(CROP_TARGETS.get(slot.name, ())) if slot.kind == "image" else []}


def max_concepts(campaign_type: str) -> int:
    """How many distinct images per ratio the type can take when every one of
    its ratios is filled (coverage): bounded by the per-slot and total ceilings."""
    c = campaign(campaign_type)
    if not c["images"]:
        return 0
    per_slot = [u["max"] for u in c["images"].values() if u["max"]]
    total = c["images_total_max"] // len(c["images"]) if c["images_total_max"] else None
    bounds = per_slot + ([total] if total else [])
    return min(bounds) if bounds else 15


def required_assets(campaign_type: str, concepts: int = 1, banners: str = "none", video: bool = None) -> list:
    """The media rows of an order for `campaign_type`: EVERY ratio the type
    takes (coverage beats polish — craft §2.4) × `concepts` images each, its
    logo slots once each, a video where the type needs one (or `video=True`
    where it merely takes one), and an uploaded-banner set for display
    ("none" | "top" | "all"). Each row carries its own size, cap and file
    types — nobody downstream looks a number up."""
    c = campaign(campaign_type)
    rows = []
    n = max(1, min(int(concepts or 1), max_concepts(campaign_type) or 1))
    for name, use in c["images"].items():
        rows.append(_row(SLOTS[name], c, n, use["need"]))
    for name, use in c["logos"].items():
        rows.append(_row(SLOTS[name], c, 1, use["need"]))
    spec = c["video"]
    if spec and (spec["min"] or video):
        rows.append({"slot": "video", "kind": "video", "count": max(1, spec["min"]), "need":
                     "required" if spec["min"] else "optional", "min_seconds": spec["min_seconds"],
                     "workflow": list(VIDEO_WORKFLOW), "ratios": {a: list(s) for a, s in VIDEO_RATIOS.items()}})
    if banners not in BANNER_SETS:
        raise ValueError(f"unknown banner set {banners!r}: one of {', '.join(BANNER_SETS)}")
    if BANNER_SETS[banners] and str(campaign_type).strip().lower() != "display":
        raise ValueError("uploaded static banners belong to a display campaign only")
    for size in BANNER_SETS[banners]:
        rows.append({"slot": banner_name(size), "kind": "banner", "aspect": "", "width": size[0], "height": size[1],
                     "min_width": size[0], "min_height": size[1], "exact_size": True, "max_bytes": BANNER_MAX_BYTES,
                     "file_types": list(BANNER_FILE_TYPES), "count": 1, "need": "optional", "crop_targets": []})
    return rows


def required_text(campaign_type: str) -> dict:
    """The text assets of an order — the COPY step's contract, never the media
    agent's: {field: {"max_chars", "min", "max", "short"}}."""
    return text_limits(campaign_type)


# ─── Measuring a produced file ───────────────────────────────────────────────

_MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF87a", "gif"), (b"GIF89a", "gif"))


def file_type(content: bytes) -> str:
    """jpg | png | gif from the BYTES ("" = none of them). Never the name."""
    head = bytes(content or b"")[:8]
    return next((kind for magic, kind in _MAGIC if head.startswith(magic)), "")


def measure(content: bytes) -> tuple:
    """(width, height) of image bytes; (0, 0) when unreadable."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as img:
            return img.size
    except Exception:
        return (0, 0)


def kb(size: int) -> int:
    return -(-int(size) // KB)          # rounded up: 150.2 KB is over a 150 KB cap


def check_file(row: dict, content: bytes) -> list:
    """What is wrong with one produced image / logo / banner against its order
    row — every number MEASURED from the bytes: [{"code", "detail"}] ([] = it
    is within its spec). Codes: unreadable | file_type | ratio | too_small |
    size | file_size."""
    out = []
    kind = file_type(content)
    w, h = measure(content)
    if not w or not h:
        return [{"code": "unreadable", "detail": "the file is not a readable image"}]
    if kind not in row.get("file_types", ()):
        out.append({"code": "file_type", "detail": f"it is {kind.upper() or 'an unknown type'}; this slot takes "
                                                   f"{' / '.join(t.upper() for t in row.get('file_types', ()))}"})
    if row.get("exact_size"):
        if (w, h) != (row["width"], row["height"]):
            out.append({"code": "size", "detail": f"{w}x{h}; this banner is exactly {row['width']}x{row['height']}"})
    else:
        want = row["width"] / row["height"]
        if abs((w / h) / want - 1) > RATIO_TOLERANCE:
            out.append({"code": "ratio", "detail": f"{w}x{h} is not {row['aspect']}"})
        if w < row["min_width"] or h < row["min_height"]:
            out.append({"code": "too_small", "detail": f"{w}x{h} is under the {row['min_width']}x{row['min_height']} "
                                                       f"minimum"})
    if len(content) > row["max_bytes"]:
        out.append({"code": "file_size", "detail": f"{kb(len(content))} KB; the cap is {row['max_bytes'] // KB} KB"})
    return out


# ─── Text assets ─────────────────────────────────────────────────────────────

def char_count(text: str) -> int:
    """Characters the way Google counts an ad's text: a full-width character
    (CJK and the like) counts as two, everything else — Hebrew, Latin, digits,
    punctuation, a space — as one."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text or ""))


def _as_list(value) -> list:
    if value is None:
        return []
    return [str(v) for v in (value if isinstance(value, (list, tuple)) else [value])]


def check_text(campaign_type: str, texts: dict) -> list:
    """Every way `texts` ({field: str | [str]}) breaks the type's limits:
    [{"field", "index", "code", ...}]. Codes: too_long (chars, limit, over) |
    empty | too_few (have, need) | too_many (have, limit) | needs_short
    (limit, need: at least one this short) | duplicate | not_taken (a field
    this campaign type has no slot for)."""
    limits = text_limits(campaign_type)
    out = []
    for field in texts or {}:
        if field not in limits and _as_list(texts[field]):
            out.append({"field": field, "index": 0, "code": "not_taken"})
    for field, spec in limits.items():
        values = [v.strip() for v in _as_list((texts or {}).get(field))]
        seen = set()
        for i, value in enumerate(values, 1):
            n = char_count(value)
            if not value:
                out.append({"field": field, "index": i, "code": "empty"})
            elif n > spec["max_chars"]:
                out.append({"field": field, "index": i, "code": "too_long", "chars": n, "limit": spec["max_chars"],
                            "over": n - spec["max_chars"], "text": value})
            if value and value in seen:
                out.append({"field": field, "index": i, "code": "duplicate", "text": value})
            seen.add(value)
        have = len([v for v in values if v])
        if have < spec["min"]:
            out.append({"field": field, "index": 0, "code": "too_few", "have": have, "need": spec["min"]})
        if spec["max"] and len(values) > spec["max"]:
            out.append({"field": field, "index": 0, "code": "too_many", "have": len(values), "limit": spec["max"]})
        short = spec.get("short")
        if short and have and len([v for v in values if v and char_count(v) <= short["max_chars"]]) < short["min"]:
            out.append({"field": field, "index": 0, "code": "needs_short", "limit": short["max_chars"],
                        "need": short["min"]})
    return out


# ─── Video references ────────────────────────────────────────────────────────

def youtube_id(url: str) -> str:
    """The 11-character video id of a YouTube watch / shorts / youtu.be URL ("" = not one)."""
    m = YOUTUBE_URL_RE.match(str(url or "").strip())
    return m.group(1) if m else ""


def video_reference_problems(asset: dict) -> list:
    """A video asset is a YouTube URL on the client's channel — never a file:
    [{"code", "detail"}]. Codes: file_not_url (it still carries a file and no
    URL: VIDEO_WORKFLOW's first step has not happened) | bad_url."""
    url = str((asset or {}).get("youtube_url") or "").strip()
    has_file = bool((asset or {}).get("file_path") or (asset or {}).get("file_id") or (asset or {}).get("content"))
    if not url:
        return [{"code": "file_not_url", "detail":
                 ("it is still a file — " if has_file else "it has no YouTube URL — ") +
                 "a video is uploaded to the client's YouTube channel first and referenced by its URL"}]
    if not youtube_id(url):
        return [{"code": "bad_url", "detail": f"{url[:80]!r} is not a YouTube video URL"}]
    return []


def video_length_problems(video_format: str, seconds: float, aspect: str = "") -> list:
    """A clip's length (and, where it matters, its ratio) against a YouTube ad format."""
    spec = VIDEO_FORMATS.get(str(video_format or ""))
    if not spec:
        return [{"code": "unknown_format", "detail": f"unknown video format {video_format!r}"}]
    out = []
    ceiling = (spec.get("connected_tv_seconds") or (None, spec.get("max_seconds")))[1]
    if spec.get("min_seconds") and seconds < spec["min_seconds"]:
        out.append({"code": "too_short", "detail": f"{seconds:g}s; {video_format} starts at {spec['min_seconds']}s"})
    if ceiling and seconds > ceiling:
        out.append({"code": "too_long", "detail": f"{seconds:g}s; {video_format} ends at {ceiling}s"})
    must = spec.get("over_standard_must_be")
    if must and spec.get("max_seconds") and seconds > spec["max_seconds"] and aspect and aspect != must:
        out.append({"code": "must_be_horizontal", "detail": f"over {spec['max_seconds']}s a {video_format} ad runs "
                                                            f"on connected TV only and must be {must}"})
    if spec.get("aspect") and aspect and aspect != spec["aspect"]:
        out.append({"code": "ratio", "detail": f"{video_format} is {spec['aspect']}, not {aspect}"})
    return out


# ─── The whole table as data ─────────────────────────────────────────────────

def table() -> dict:
    """Everything above as plain data (for a prompt or a page — neither keeps a copy)."""
    return {"verified": VERIFIED, "reverify_by": REVERIFY_BY, "stale": stale(),
            "slots": {n: {**asdict(s), "ratio": round(s.ratio, 5)} for n, s in SLOTS.items()},
            "campaigns": {n: {"label": c["label"], "file_types": list(c["file_types"]),
                              "image_max_bytes": c["image_max_bytes"], "logo_max_bytes": c["logo_max_bytes"],
                              "images": c["images"], "images_total_max": c["images_total_max"],
                              "logos": c["logos"], "logos_total_max": c["logos_total_max"], "text": c["text"],
                              "video": c["video"], "carousel": c.get("carousel"), "minimum": c["minimum"],
                              "minimum_source": c["minimum_source"]} for n, c in CAMPAIGNS.items()},
            "crop_targets": {n: list(t) for n, t in CROP_TARGETS.items()},
            "banners": {"max_bytes": BANNER_MAX_BYTES, "file_types": list(BANNER_FILE_TYPES),
                        "groups": {g: [list(s) for s in sizes] for g, sizes in BANNERS.items()},
                        "top": [list(s) for s in TOP_BANNERS]},
            "video": {"formats": VIDEO_FORMATS, "ratios": {a: list(s) for a, s in VIDEO_RATIOS.items()},
                      "companion_banner": {**COMPANION_BANNER, "file_types": list(COMPANION_BANNER["file_types"])},
                      "workflow": list(VIDEO_WORKFLOW)}}
