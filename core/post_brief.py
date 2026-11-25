"""The post contract between the social agent and the media agent
(2026-11-23, knowledge/social-craft.md §2, docs/POST-BRIEF.md).

Why it exists: a carousel request ("4 reasons…") went to the image model as
one free-text brief — copy and visuals mixed — and came back as four random
AI sneakers with zero readable text. The fix is a CONTRACT, not a better
prompt:

- CONTEXT, in full, before any content work: knowledge/social-craft.md (the
  discipline) and knowledge/brands/<client_id>.md (the client's facts).
  Either missing = ContextMissing, loudly — nothing is produced without them.
- `post_format` is explicit (POST_FORMATS) with a one-line reason. "A
  carousel about why X" is text_carousel. Genuinely ambiguous = ONE short
  question to the manager (FormatQuestion), never a guess.
- One AssetBrief per asset (craft §2.2). `copy_headline` / `copy_body` are
  CONTEXT ONLY: the image prompt is built from `visual_direction` (+
  must_include / must_avoid / the palette) and `assert_copy_free` fails the
  call when the final prompt carries any of the copy — or any Hebrew at all.
- text_slide is NOT sent to a model: its renderer is not built yet
  (RENDERER_NOT_IMPLEMENTED). Hebrew is never drawn by an image model.
- Sourcing (craft §10): real client asset > brand template > generated. A
  generated image may set a scene; it may not stand in for an orderable
  product (proof slides, product / before-after / customer-story formats).
  A generated frame is flagged "concept" on the approval card.
- Objections (craft §2.4): one line each, round 1 = the counterpart revises,
  round 2 = escalate to the manager with both positions, never a round 3.
- Audience-conditional palette (`palette_variants` in the brief): resolved
  once per post, so a set_id never mixes variants.
- Lead time: a dated occasion + the brief's lead_time → an order-by date and
  a WARNING on the card when the publish date leaves too little time.
"""
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE_DIR = os.path.join(ROOT, "knowledge")
CRAFT_PATH = os.path.join(KNOWLEDGE_DIR, "social-craft.md")
BRANDS_DIR = os.path.join(KNOWLEDGE_DIR, "brands")

POST_FORMATS = ("text_carousel", "product_carousel", "before_after", "process", "customer_story",
                "educational", "comparison", "single_image", "reel")
OBJECTIVES = ("awareness", "consideration", "conversion", "retention")
ROLES = ("hook", "point", "proof", "cta")
ASSET_TYPES = ("text_slide", "real_photo", "generated_image", "generated_video")
from core import social_formats  # noqa: E402  (pure data; imports nothing of ours)

# A post's ratio is DERIVED from its post_format (social_formats.for_post) —
# these are simply every ratio the table knows
ASPECTS = tuple(f.aspect for f in social_formats.FORMATS.values())
MAX_ASSETS = 10                     # ours; the platforms' carousel ceiling is social_formats.CAROUSEL_MAX_SLIDES
CAPTION_MAX = 2200
COPY_MAX = 400
DIRECTION_MAX = 800
SET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,39}$")
ASSET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
HEBREW_RE = re.compile(r"[֐-׿יִ-ﭏ]")
RENDERER_NOT_IMPLEMENTED = "renderer_not_implemented"
MAX_OBJECTION_ROUNDS = 2
# A post has to be seen BEFORE the last day to order: this many calendar days
LEAD_MARGIN_DAYS = 7

# Sourcing priority, craft §10 — the order is not optional
SOURCE_PRIORITY = ("real_asset", "template", "generated")
SOURCE_OF = {"real_photo": "real_asset", "text_slide": "template",
             "generated_image": "generated", "generated_video": "generated"}
# Formats whose whole point is the real thing: a generated frame can't be it
REAL_FORMATS = ("product_carousel", "before_after", "customer_story")


class ContextMissing(RuntimeError):
    def __init__(self, missing: list):
        super().__init__("content context missing: " + ", ".join(missing))
        self.missing = list(missing)


class FormatQuestion(ValueError):
    """Genuinely ambiguous: ask the manager this ONE short question."""
    def __init__(self, question: dict):
        super().__init__("post_format is ambiguous")
        self.question = question


class AudienceUnresolved(ValueError):
    def __init__(self, variants: list):
        super().__init__("audience does not resolve to one palette variant")
        self.variants = variants


class CopyLeak(AssertionError):
    """The generation prompt carries slide copy (or Hebrew): the call fails."""


class ObjectionCapReached(RuntimeError):
    pass


# ─── Context (both agents, in full) ──────────────────────────────────────────

_CACHE = {}


def _read(path: str) -> str:
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return ""
    hit = _CACHE.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    _CACHE[path] = (stamp, text)
    return text


def brand_path(client_id: int) -> str:
    """The LEGACY repo file (the one-time migration seed, core/brand_brief)."""
    return os.path.join(BRANDS_DIR, f"{int(client_id)}.md")


def load_context(client_id: int) -> dict:
    """{"craft", "brand", "brand_facts", "brand_status", "brand_lacks"}.
    The craft library IN FULL (missing = ContextMissing: a broken deploy).
    The brand brief from the database (core/brand_brief, 2026-11-24): a
    DRAFT is used as it is (its Status line says so); NO brief = the
    MISSING note — the agents still work and say what they lack, and a draft
    is queued. Never a silent empty context."""
    from core import brand_brief
    craft = _read(CRAFT_PATH).strip()
    if not craft:
        raise ContextMissing([os.path.relpath(CRAFT_PATH, ROOT).replace(os.sep, "/")])
    got = brand_brief.for_agents(client_id)
    brand = got["text"].strip() if got["status"] != "missing" else got["text"]
    facts = parse_brand(got.get("source_text", "").strip()) if got["status"] != "missing" else parse_brand("")
    return {"craft": craft, "brand": brand, "brand_facts": facts, "brand_status": got["status"],
            "brand_lacks": got["lacks"]}


def try_context(client_id: int) -> dict:
    """load_context for a prompt: {"missing": [...]} instead of raising
    (only the craft library can be missing now)."""
    try:
        return load_context(client_id)
    except ContextMissing as e:
        return {"missing": e.missing}


# ─── The brand brief's machine-read facts ────────────────────────────────────

_LEAD_RE = re.compile(r"lead_time:\s*(\d+)\s*(?:[–—-]\s*(\d+))?\s*business\s+days", re.IGNORECASE)
_VARIANT_RE = re.compile(r"^\s+-\s+\*\*([^*]+)\*\*\s*[—–-]\s*(.+?)\s*$")
_B2C = ("private", "b2c", "gift", "fans", "fan", "individual", "consumer", "consumers", "personal",
        "פרטי", "פרטיים", "מתנה", "מתנות", "מעריצים", "אישי")
_B2B = ("business", "businesses", "b2b", "teams", "team", "events", "event", "company", "companies",
        "corporate", "group", "groups", "wholesale", "עסקי", "עסקים", "עסק", "קבוצות", "קבוצה", "אירועים",
        "חברות", "חברה")


def parse_brand(text: str) -> dict:
    """lead_time (business days, min/max) and palette_variants, read from the
    brief. Absent = None / [] — the rules that need them simply don't apply."""
    facts = {"lead_time_days": None, "palette_variants": []}
    m = _LEAD_RE.search(text or "")
    if m:
        low = int(m.group(1))
        facts["lead_time_days"] = (low, int(m.group(2)) if m.group(2) else low)
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*-\s*(palette|palette_variants)\s*:", line) and (
                "segment" in line.lower() or "variant" in line.lower()):
            for nxt in lines[i + 1:]:
                if re.match(r"^\s*-\s", nxt) and not re.match(r"^\s{2,}-\s", nxt):
                    break                       # the next top-level field
                v = _VARIANT_RE.match(nxt)
                if v:
                    label, applies = v.group(1).strip(), v.group(2).strip()
                    segment = _segment(applies)
                    if segment:
                        facts["palette_variants"].append({
                            "label": label, "applies_to": applies, "segment": segment,
                            "colors": [c.strip() for c in re.split(r"\+|,|/", label) if c.strip()]})
            break
    return facts


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+|[֐-׿]+", str(text or "").lower()))


def _segment(text: str) -> str:
    """"b2c" | "b2b" | "" (none, or both)."""
    words = _words(text)
    b2c, b2b = bool(words & set(_B2C)), bool(words & set(_B2B))
    return "b2c" if b2c and not b2b else "b2b" if b2b and not b2c else ""


def resolve_palette(facts: dict, audience: str) -> dict:
    """The ONE palette variant for this post's audience. No variants in the
    brief = {} (nothing to choose). Variants + an audience that doesn't land
    on exactly one = AudienceUnresolved: the social agent decides the
    audience before anything is produced."""
    variants = (facts or {}).get("palette_variants") or []
    if not variants:
        return {}
    segment = _segment(audience)
    match = [v for v in variants if segment and v["segment"] == segment]
    if len(match) != 1:
        raise AudienceUnresolved([v["label"] for v in variants])
    return dict(match[0])


# ─── post_format ─────────────────────────────────────────────────────────────

_CAROUSEL = re.compile(r"carousel|swipe|slides|קרוסלה|קרוסלת|קרוסלות|סליידים|שקופיות", re.IGNORECASE)
_ARGUMENT = re.compile(r"\bwhy\b|reasons?|tips?|\bhow\b|ways|mistakes|myths|steps|למה|מדוע|סיבות|סיבה|טיפים|"
                       r"איך|דרכים|טעויות|מיתוסים|שלבים|דברים ש", re.IGNORECASE)
_RULES = (
    ("before_after", re.compile(r"before\s*(and|/|&)\s*after|לפני\s*(ו|/)\s*אחרי", re.IGNORECASE)),
    ("process", re.compile(r"behind the scenes|process|making of|מאחורי הקלעים|תהליך|איך זה נעשה", re.IGNORECASE)),
    ("customer_story", re.compile(r"customer story|testimonial|review from|סיפור לקוח|המלצת לקוח|לקוח מספר", re.IGNORECASE)),
    ("comparison", re.compile(r"\bvs\.?\b|versus|compared|comparison|לעומת|השוואה|מול ", re.IGNORECASE)),
    ("reel", re.compile(r"\breels?\b|ריל|רילס|סרטון קצר|short video", re.IGNORECASE)),
    ("educational", re.compile(r"\bteach|explain|guide|tutorial|מדריך|ללמד|הסבר", re.IGNORECASE)),
)
_PRODUCTS = re.compile(r"products?|our work|catalog|collection|מוצרים|עבודות|קולקציה|קטלוג", re.IGNORECASE)

FORMAT_QUESTION = {
    "he": "רק כדי לבחור נכון: זה פוסט של טקסט שמסביר משהו (שקופיות עם כותרות), או פוסט שמראה את העבודות שלכם בתמונות אמיתיות?",
    "en": "Just so I pick the right format: is this a text post that explains something (slides with headlines), or a post that shows your work in real photos?",
    "fr": "Pour choisir le bon format : un post texte qui explique quelque chose (des diapositives avec titres), ou un post qui montre vos réalisations en vraies photos ?",
    "ar": "فقط لأختار الشكل الصحيح: هل هو منشور نصي يشرح شيئًا (شرائح بعناوين)، أم منشور يعرض أعمالكم بصور حقيقية؟",
    "ru": "Чтобы выбрать правильный формат: это текстовый пост, который что-то объясняет (слайды с заголовками), или пост, который показывает ваши работы на настоящих фото?",
}


def infer_format(text: str) -> list:
    """The formats the request's own words point to (strongest first)."""
    text = str(text or "")
    if _CAROUSEL.search(text):
        if _ARGUMENT.search(text):
            return ["text_carousel"]               # "a carousel about why X"
        for fmt, rule in _RULES[:3]:
            if rule.search(text):
                return [fmt]
        if _PRODUCTS.search(text):
            return ["product_carousel"]
        return ["text_carousel", "product_carousel"]   # a carousel of what? ambiguous
    found = [fmt for fmt, rule in _RULES if rule.search(text)]
    return found


def resolve_format(chosen: str, reason: str, request_text: str = "", lang: str = "he") -> dict:
    """{"post_format", "format_reason"}. The request's own unambiguous words
    win ("a carousel about why X" = text_carousel, whatever was chosen); an
    explicit valid choice needs its one-line reason; nothing chosen and the
    request ambiguous = FormatQuestion (ONE question, never a guess)."""
    chosen = str(chosen or "").strip().lower()
    reason = re.sub(r"\s+", " ", str(reason or "")).strip()[:200]
    inferred = infer_format(request_text)
    if len(inferred) == 1 and inferred[0] == "text_carousel" and chosen != "text_carousel" and _CAROUSEL.search(
            str(request_text or "")) and _ARGUMENT.search(str(request_text or "")):
        return {"post_format": "text_carousel",
                "format_reason": "the request is a carousel that argues or explains (why / reasons / tips): "
                                 "the text is the content" + (f" (proposed: {chosen})" if chosen else "")}
    if chosen in POST_FORMATS:
        if len(reason) < 8:
            raise ValueError("format_reason (one line) is required with post_format")
        return {"post_format": chosen, "format_reason": reason}
    if chosen:
        raise ValueError(f"post_format must be one of {', '.join(POST_FORMATS)}")
    if len(inferred) == 1:
        return {"post_format": inferred[0], "format_reason": f"inferred from the request's words: {inferred[0]}"}
    raise FormatQuestion(FORMAT_QUESTION)


# ─── The asset brief (craft §2.2) ────────────────────────────────────────────

def _clean_text(value, limit: int) -> str:
    return re.sub(r"[ \t]+", " ", str(value or "").replace("\r\n", "\n")).strip()[:limit]


def _clean_list(value, limit: int = 8) -> list:
    raw = value if isinstance(value, (list, tuple)) else ([value] if value else [])
    return [_clean_text(v, 160) for v in raw if _clean_text(v, 160)][:limit]


@dataclass(frozen=True)
class AssetBrief:
    asset_id: str
    post_format: str
    objective: str
    client_id: int
    role: str
    copy_headline: str            # CONTEXT ONLY — never in an image prompt
    copy_body: str                # CONTEXT ONLY — never in an image prompt
    asset_type: str
    visual_direction: str         # what should be SEEN: the only prompt source
    must_include: list = field(default_factory=list)
    must_avoid: list = field(default_factory=list)
    aspect_ratio: str = social_formats.FEED_ASPECT    # derived from post_format — never the agent's choice
    set_id: str = ""
    budget_credits: int = 0
    characters: list = field(default_factory=list)   # agents appearing as themselves (core/agent_characters)
    character_mode: str = ""      # composite | in_scene ("" = no characters)
    safe_zone: str = ""           # a vertical asset: the zone its text stays inside (social_formats.zone_for)

    @classmethod
    def from_dict(cls, raw: dict, post: dict) -> "AssetBrief":
        raw = raw if isinstance(raw, dict) else {}
        asset_id = str(raw.get("asset_id") or "").strip().lower()
        if not ASSET_ID_RE.match(asset_id):
            raise ValueError("asset_id is required (a-z, 0-9, - or _)")
        role = str(raw.get("role") or "").strip().lower()
        if role not in ROLES:
            raise ValueError(f"{asset_id}: role must be one of {', '.join(ROLES)}")
        asset_type = str(raw.get("asset_type") or "").strip().lower()
        if asset_type not in ASSET_TYPES:
            raise ValueError(f"{asset_id}: asset_type must be one of {', '.join(ASSET_TYPES)}")
        fmt = str(raw.get("post_format") or post["post_format"]).strip().lower()
        if fmt != post["post_format"]:
            raise ValueError(f"{asset_id}: post_format differs from the post's")
        visual = _clean_text(raw.get("visual_direction"), DIRECTION_MAX)
        if asset_type != "text_slide" and len(visual) < 10:
            raise ValueError(f"{asset_id}: visual_direction (what should be seen) is required")
        # The ratio comes from the post's format — whatever the agent wrote in
        # aspect_ratio is not read (every slide of a set has the same one)
        aspect = social_formats.aspect_for_post(post["post_format"], post.get("placement", ""))
        set_id = str(raw.get("set_id") or post["set_id"]).strip().lower()
        if set_id != post["set_id"]:
            raise ValueError(f"{asset_id}: one post is one set — set_id must be the post's")
        try:
            budget = int(raw.get("budget_credits") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{asset_id}: budget_credits must be a whole number")
        if not 0 <= budget <= 500:
            raise ValueError(f"{asset_id}: budget_credits must be 0-500")
        characters, mode = character_fields(asset_id, asset_type, raw.get("characters"), raw.get("character_mode"))
        return cls(asset_id=asset_id, post_format=fmt, objective=post["objective"], client_id=int(post["client_id"]),
                   role=role, copy_headline=_clean_text(raw.get("copy_headline"), COPY_MAX),
                   copy_body=_clean_text(raw.get("copy_body"), COPY_MAX), asset_type=asset_type,
                   visual_direction=visual, must_include=_clean_list(raw.get("must_include")),
                   must_avoid=_clean_list(raw.get("must_avoid")), aspect_ratio=aspect, set_id=set_id,
                   budget_credits=budget, characters=characters, character_mode=mode,
                   safe_zone=post.get("safe_zone", "") if social_formats.is_vertical(aspect) else "")

    def to_dict(self) -> dict:
        return asdict(self)

    def prompt_material(self) -> dict:
        """What the media agent may turn into a generation prompt. The copy is
        deliberately NOT here."""
        return {"visual_direction": self.visual_direction, "must_include": list(self.must_include),
                "must_avoid": list(self.must_avoid), "aspect_ratio": self.aspect_ratio,
                "media_kind": "video" if self.asset_type == "generated_video" else "image",
                "role": self.role}


def validate_post(raw, client_id: int, request_text: str = "", lang: str = "he") -> dict:
    """The social agent's whole post → a normalized dict (assets as dicts).
    Raises ValueError (malformed) or FormatQuestion (ask the manager)."""
    import json as _json
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except ValueError:
            raise ValueError("post must be a JSON object")
    if not isinstance(raw, dict):
        raise ValueError("post must be an object")
    fmt = resolve_format(raw.get("post_format"), raw.get("format_reason"), request_text, lang)
    objective = str(raw.get("objective") or "").strip().lower()
    if objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {', '.join(OBJECTIVES)}")
    set_id = str(raw.get("set_id") or "").strip().lower()
    if not SET_ID_RE.match(set_id):
        raise ValueError("set_id is required (3-40 of a-z, 0-9, - or _)")
    audience = _clean_text(raw.get("audience"), 160)
    if len(audience) < 2:
        raise ValueError("audience (who this post is for) is required")
    caption = _clean_text(raw.get("caption"), CAPTION_MAX + 1)
    if not caption:
        raise ValueError("caption is required — the copy is written before any pixel")
    if len(caption) > CAPTION_MAX:
        raise ValueError("caption is too long")
    placement = str(raw.get("placement") or "").strip().lower()
    if placement and placement not in social_formats.PLACEMENTS:
        raise ValueError(f"placement must be one of {', '.join(social_formats.PLACEMENTS)}")
    shape = social_formats.for_post(fmt["post_format"], placement)
    post = {"client_id": int(client_id), "set_id": set_id, "objective": objective, "audience": audience,
            "caption": caption, "title": _clean_text(raw.get("title"), 120) or set_id, **fmt,
            "format": shape.name, "placement": placement or ("feed" if shape.kind == "feed" else "reel"),
            "safe_zone": ""}
    for key in ("publish_date", "occasion_date"):
        value = str(raw.get(key) or "").strip()[:10]
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError(f"{key} must be YYYY-MM-DD")
            post[key] = value
    post["occasion"] = _clean_text(raw.get("occasion"), 120)
    channels = [c for c in (raw.get("channels") or ["instagram"]) if c in ("instagram", "facebook", "tiktok", "youtube")]
    post["channels"] = channels[:4] or ["instagram"]
    if shape.kind == "vertical":
        post["safe_zone"] = social_formats.zone_for(post["channels"])
    assets_raw = raw.get("assets")
    if not isinstance(assets_raw, list) or not 1 <= len(assets_raw) <= MAX_ASSETS:
        raise ValueError(f"assets must be a list of 1-{MAX_ASSETS}")
    assets, seen = [], set()
    for a in assets_raw:
        brief = AssetBrief.from_dict(a, post)
        if brief.asset_id in seen:
            raise ValueError(f"asset_id {brief.asset_id} appears twice")
        seen.add(brief.asset_id)
        entry = brief.to_dict()
        response = _clean_text((a or {}).get("response"), 300)
        if response:
            entry["response"] = response       # the social agent's answer to an objection
        needs_room = _clean_text((a or {}).get("needs_room"), 300)
        if needs_room:
            entry["needs_room"] = needs_room   # the idea needs more room than the template holds (core/copy_budget)
        assets.append(entry)
    if not any((a["copy_headline"] or a["copy_body"]) for a in assets):
        raise ValueError("the slides' copy is written first — every post has copy")
    post["assets"] = assets
    colors = clean_colors(raw.get("colors"), len(assets))
    if colors:
        post["colors"] = colors
    return post


# ─── An explicit colour instruction beats the brand palette (2026-11-25) ─────
#
# The audience palette rule stops RANDOM palettes; it never overrides the
# manager. When they explicitly ask for other colours ("colourful, not the
# brand colours", "a different colour per slide"), the post carries
# `colors`: {"requested": the manager's own words, "slides": [{bg, fg?,
# accent?, muted?}, …]} — one entry for every slide, or one per slide in
# order. The text slides render with them (slide_renderer.slide_roles) and
# the agent says in one line that this post deviates from the brand palette
# on request (post_pipeline TEXT["colors"]). Without "requested" there is no
# override: an agent never swaps the palette on its own taste.

COLOR_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
COLOR_KEYS = ("bg", "fg", "accent", "muted")


def clean_colors(raw, n_slides: int) -> dict:
    """{} or {"requested", "slides": [one per slide]}; ValueError when malformed."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("colors must be an object: {requested, slides}")
    requested = _clean_text(raw.get("requested"), 200)
    if len(requested) < 3:
        raise ValueError("colors.requested must quote the manager's explicit colour instruction — "
                         "without it the brand palette stays")
    entries = raw.get("slides")
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list) or len(entries) not in (1, n_slides):
        raise ValueError(f"colors.slides must have 1 entry (all slides) or {n_slides} (one per slide)")
    slides = []
    for i, e in enumerate(entries, 1):
        if not isinstance(e, dict) or not COLOR_HEX_RE.match(str(e.get("bg") or "")):
            raise ValueError(f"colors.slides[{i}].bg must be a #RRGGBB colour")
        entry = {}
        for k in COLOR_KEYS:
            if e.get(k):
                if not COLOR_HEX_RE.match(str(e[k])):
                    raise ValueError(f"colors.slides[{i}].{k} must be a #RRGGBB colour")
                entry[k] = str(e[k]).upper()
        slides.append(entry)
    if len(slides) == 1:
        slides = slides * n_slides
    return {"requested": requested, "slides": slides}


def asset(entry: dict) -> AssetBrief:
    optional = {"characters": [], "character_mode": "", "safe_zone": ""}      # entries stored before these fields
    return AssetBrief(**{k: entry.get(k, optional[k]) if k in optional else entry[k]
                         for k in AssetBrief.__dataclass_fields__})


# ─── Agents as their own characters (core/agent_characters) ──────────────────

CHARACTER_MODES = ("composite", "in_scene")


def default_mode(asset_type: str) -> str:
    """No character_mode given: a text slide / brand-template background →
    composite (a cut-out on the template); a photographic or lifestyle
    scene → in_scene (generated inside it)."""
    return "composite" if asset_type == "text_slide" else "in_scene"


def character_fields(asset_id: str, asset_type: str, characters, mode) -> tuple:
    """(characters, character_mode) validated. No characters = ([], "")."""
    from core import agent_characters
    raw = characters if isinstance(characters, (list, tuple)) else ([characters] if characters else [])
    names = []
    for c in raw:
        name = str((c.get("agent") if isinstance(c, dict) else c) or "").strip().lower()
        if name not in agent_characters.AGENTS:
            raise ValueError(f"{asset_id}: characters must be agents ({', '.join(agent_characters.AGENTS)})")
        if name not in names:
            names.append(name)
    mode = str(mode or "").strip().lower()
    if not names:
        return [], ""
    if mode and mode not in CHARACTER_MODES:
        raise ValueError(f"{asset_id}: character_mode must be composite or in_scene")
    mode = mode or default_mode(asset_type)
    if asset_type == "real_photo":
        raise ValueError(f"{asset_id}: a real photo is never altered with a character — use a text_slide "
                         f"(composite, the photo as its background) or a generated_image (in_scene)")
    if mode == "composite" and asset_type != "text_slide":
        raise ValueError(f"{asset_id}: composite places a cut-out on a brand-template slide (text_slide); "
                         f"a generated scene takes in_scene")
    if mode == "in_scene" and asset_type == "text_slide":
        raise ValueError(f"{asset_id}: a text slide is drawn from the template, never generated — "
                         f"use composite")
    if mode == "composite" and len(names) > 1:
        raise ValueError(f"{asset_id}: composite places ONE character per slide")
    if len(names) > agent_characters.MAX_CHARACTERS:
        raise ValueError(f"{asset_id}: at most {agent_characters.MAX_CHARACTERS} characters in one image")
    return names, mode


# ─── THE root-cause guard ────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", str(text or "").lower()))


def assert_copy_free(prompt: str, brief: AssetBrief):
    """Fails the generation call (CopyLeak) when the prompt string carries the
    slide's copy: the whole headline/body, or any run of 4 of its words in a
    row — and when it carries Hebrew at all (never drawn by a model)."""
    if HEBREW_RE.search(str(prompt or "")):
        raise CopyLeak("the generation prompt contains Hebrew text")
    p = f" {_norm(prompt)} "
    for name in ("copy_headline", "copy_body"):
        copy = _norm(getattr(brief, name))
        if not copy:
            continue
        words = copy.split()
        if (len(words) >= 2 or len(copy) >= 12) and f" {copy} " in p:
            raise CopyLeak(f"the generation prompt contains {name}")
        for i in range(0, max(0, len(words) - 3)):
            if f" {' '.join(words[i:i + 4])} " in p:
                raise CopyLeak(f"the generation prompt contains part of {name}")


def assert_no_hebrew(prompt: str):
    """Every generation path, old and new: Hebrew never reaches an image or
    video model to be drawn."""
    if HEBREW_RE.search(str(prompt or "")):
        raise CopyLeak("the generation prompt contains Hebrew text")


# ─── Sourcing (craft §10) ────────────────────────────────────────────────────

SOURCING_OBJECTION = {
    "proof": "A proof slide must show the real thing: use a real photo of delivered work (real_photo), "
             "not a generated image of a product that may not exist.",
    "format": "This format only works with the real work ({fmt}): use real_photo for this asset; generate "
              "only the scene around it.",
}


def sourcing(brief: AssetBrief) -> dict:
    """{"source", "priority", "objection", "flags"} for one asset."""
    source = SOURCE_OF[brief.asset_type]
    out = {"source": source, "priority": SOURCE_PRIORITY.index(source), "objection": "", "flags": []}
    if source == "generated":
        if brief.role == "proof":
            out["objection"] = SOURCING_OBJECTION["proof"]
        elif brief.post_format in REAL_FORMATS:
            out["objection"] = SOURCING_OBJECTION["format"].format(fmt=brief.post_format)
        else:
            out["flags"].append("concept")       # a scene, not a real orderable item
    return out


# ─── Objection protocol (craft §2.4) ─────────────────────────────────────────

def record_objection(history: list, asset_id: str, by: str, text: str, response: str = "") -> dict:
    """Appends one objection to `history` (mutated). Round 1 → "revise"
    (the counterpart revises); round 2 → "escalate" with both positions;
    a third round is never opened (ObjectionCapReached)."""
    text = _clean_text(text, 300)
    rounds = sum(1 for h in history if h.get("asset_id") == asset_id)
    if rounds >= MAX_OBJECTION_ROUNDS:
        raise ObjectionCapReached(f"{asset_id}: already escalated to the manager")
    entry = {"asset_id": asset_id, "round": rounds + 1, "by": by, "text": text}
    if response:
        entry["response"] = _clean_text(response, 300)
    history.append(entry)
    if entry["round"] == 1:
        return {"round": 1, "action": "revise", "text": text}
    return {"round": 2, "action": "escalate", "text": text,
            "positions": {by: text, "counterpart": entry.get("response") or "kept the asset unchanged"}}


def escalated(history: list, asset_id: str) -> bool:
    return sum(1 for h in history if h.get("asset_id") == asset_id) >= MAX_OBJECTION_ROUNDS


# ─── Lead time ───────────────────────────────────────────────────────────────

def business_days_before(day: date, n: int) -> date:
    """`n` Israeli business days (Sunday-Thursday) before `day`."""
    d = day
    while n > 0:
        d -= timedelta(days=1)
        if d.weekday() not in (4, 5):        # Friday, Saturday
            n -= 1
    return d


def lead_time_warning(facts: dict, occasion_date: str, publish_date: str, today: date = None) -> dict:
    """{} when fine or not applicable; else {"order_by", "publish_date",
    "level": "late" | "tight"} — a warning on the card, never a block."""
    lead = (facts or {}).get("lead_time_days")
    if not lead or not occasion_date:
        return {}
    try:
        occasion = date.fromisoformat(str(occasion_date)[:10])
        publish = date.fromisoformat(str(publish_date)[:10]) if publish_date else (today or date.today())
    except ValueError:
        return {}
    order_by = business_days_before(occasion, int(lead[1]))
    if publish >= order_by:
        level = "late"
    elif (order_by - publish).days < LEAD_MARGIN_DAYS:
        level = "tight"
    else:
        return {}
    return {"order_by": order_by.isoformat(), "publish_date": publish.isoformat(), "level": level,
            "lead_time_days": list(lead)}
