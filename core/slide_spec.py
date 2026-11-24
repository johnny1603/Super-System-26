"""The slide design spec (2026-11-23, docs/SLIDE-RENDERER.md).

The media agent DESIGNS text slides by writing a declarative JSON spec; it
never writes rendering code. knowledge/slide-spec.schema.json is the ONE
definition of that design space: the agent reads it in its prompt, and
validate() checks every spec against the same file (a small JSON Schema
subset validator — no extra dependency). core/slide_renderer draws a valid
spec.

Colours in a spec are ROLES (bg / fg / accent / muted). They resolve against
the client's palette variant (knowledge/brands/<id>.palette.json, keyed by the
brief's palette_variants labels), so a palette switch changes nothing else.
The variant itself comes from the brand brief + the post's audience
(post_brief.resolve_palette) — never guessed, never chosen by the agent.
"""
import copy
import json
import os
import re

from core import post_brief

ROOT = post_brief.ROOT
SCHEMA_PATH = os.path.join(ROOT, "knowledge", "slide-spec.schema.json")
STARTERS_DIR = os.path.join(ROOT, "knowledge", "slide-specs")
CANVASES = {"4:5": (1080, 1350), "1:1": (1080, 1080), "9:16": (1080, 1920)}
DEFAULT_CANVAS = (1080, 1350)
COLOR_ROLES = ("bg", "fg", "accent", "muted")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
PLACEHOLDERS = ("{headline}", "{body}", "{n}", "{total}")
SEGMENTS = ("b2c", "b2b")


class SpecInvalid(ValueError):
    """The spec breaks the schema. `errors` = ["path: what", …] (English, for the agent)."""

    def __init__(self, errors):
        self.errors = list(errors)[:30]
        super().__init__("; ".join(self.errors[:6]))


class PaletteMissing(RuntimeError):
    """No role colours for the variant — never guessed."""


# ─── The schema (the file is the source of truth) ────────────────────────────

_SCHEMA = {}


def schema() -> dict:
    stamp = os.path.getmtime(SCHEMA_PATH)
    if _SCHEMA.get("stamp") != stamp:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            _SCHEMA.update(stamp=stamp, value=json.load(f))
    return _SCHEMA["value"]


def schema_text() -> str:
    """The schema as the agent's prompt sees it (compact JSON)."""
    return json.dumps(schema(), ensure_ascii=False, separators=(",", ":"))


def _resolve(node: dict, root: dict) -> dict:
    while "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise RuntimeError(f"unsupported $ref {ref}")
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
    return node


def _check(value, node: dict, root: dict, path: str, errors: list):
    node = _resolve(node, root)
    if "oneOf" in node:
        hits = []
        for option in node["oneOf"]:
            sub = []
            _check(value, option, root, path, sub)
            hits.append(sub)
        if sum(1 for h in hits if not h) != 1:
            errors.append(f"{path}: must match exactly one allowed shape")
        return
    if "const" in node and value != node["const"]:
        errors.append(f"{path}: must be {node['const']!r}")
        return
    if "enum" in node and value not in node["enum"]:
        errors.append(f"{path}: {value!r} is not one of {node['enum']}")
        return
    kind = node.get("type")
    ok = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
          "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
          "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
          "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)}
    if kind and not ok[kind](value):
        errors.append(f"{path}: must be {kind}")
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in node and value < node["minimum"]:
            errors.append(f"{path}: {value} < minimum {node['minimum']}")
        if "maximum" in node and value > node["maximum"]:
            errors.append(f"{path}: {value} > maximum {node['maximum']}")
    if isinstance(value, str):
        if "maxLength" in node and len(value) > node["maxLength"]:
            errors.append(f"{path}: longer than {node['maxLength']} characters")
        if "pattern" in node and not re.match(node["pattern"], value):
            errors.append(f"{path}: bad format")
    if isinstance(value, list):
        if "minItems" in node and len(value) < node["minItems"]:
            errors.append(f"{path}: needs at least {node['minItems']} items")
        if "maxItems" in node and len(value) > node["maxItems"]:
            errors.append(f"{path}: at most {node['maxItems']} items")
        if "items" in node:
            for i, item in enumerate(value):
                _check(item, node["items"], root, f"{path}[{i}]", errors)
    if isinstance(value, dict):
        props = node.get("properties") or {}
        for key in node.get("required") or []:
            if key not in value:
                errors.append(f"{path}.{key}: required")
        for key, item in value.items():
            if key in props:
                _check(item, props[key], root, f"{path}.{key}", errors)
            elif node.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: unknown field")


def validate(spec) -> dict:
    """A deep copy of `spec` if it matches the schema, else SpecInvalid."""
    errors = []
    _check(spec, schema(), schema(), "spec", errors)
    if errors:
        raise SpecInvalid(errors)
    return copy.deepcopy(spec)


def validate_character(character) -> dict:
    """One slide's merged character (placement + agent/pose) against
    $defs/character, else SpecInvalid."""
    errors = []
    _check(character, {"$ref": "#/$defs/character"}, schema(), "character", errors)
    if errors:
        raise SpecInvalid(errors)
    return copy.deepcopy(character)


# ─── Palette roles ───────────────────────────────────────────────────────────

def palette_path(client_id: int) -> str:
    return os.path.join(post_brief.BRANDS_DIR, f"{int(client_id)}.palette.json")


def palette_roles(client_id: int, variant_label: str) -> dict:
    """{"bg", "fg", "accent", "muted"} hex for ONE variant (core/brand_brief,
    the database). A client with NO palette, or a role that could not be
    determined, gets the NEUTRAL colour for it (the gap shows in the Control
    Room). A palette that exists but has no such variant is a real mismatch
    with the brief: PaletteMissing."""
    from core import brand_brief
    got = brand_brief.roles_for(client_id, variant_label)
    if got["known"] and not got["variant_found"]:
        raise PaletteMissing(f"no role colours for palette variant {variant_label!r} in client "
                             f"{int(client_id)}'s brand brief palette")
    return dict(got["roles"])


def variant_for(palette: dict) -> str:
    """The variant label of a resolved palette (post_brief.resolve_palette), or
    "default" for a brief without variants."""
    return str((palette or {}).get("label") or "default")


# ─── Starters (deliberately plain; the manager drives the design) ────────────

def starter(segment: str, variant_label: str, aspect: str = "4:5") -> dict:
    segment = segment if segment in SEGMENTS else "b2c"
    with open(os.path.join(STARTERS_DIR, f"starter-{segment}.json"), encoding="utf-8") as f:
        spec = json.load(f)
    spec["palette_variant"] = variant_label
    width, height = CANVASES.get(aspect, DEFAULT_CANVAS)
    spec["canvas"] = {"width": width, "height": height}
    return validate(spec)


# ─── One slide of a set ──────────────────────────────────────────────────────

def for_slide(spec: dict, role: str = "") -> dict:
    """The spec with that role's overrides applied (top-level keys replaced)."""
    out = {k: copy.deepcopy(v) for k, v in spec.items() if k != "role_overrides"}
    for key, value in ((spec.get("role_overrides") or {}).get(role) or {}).items():
        out[key] = copy.deepcopy(value)
    return out


def bind_text(block: dict, copy_headline: str, copy_body: str, n: int, total: int) -> str:
    """A block's text with the slide's copy in place. No text: headline →
    {headline}, body → {body}, other roles → nothing."""
    text = block.get("text")
    if text is None or text == "":
        text = {"headline": "{headline}", "body": "{body}"}.get(block.get("role"), "")
    return (str(text).replace("{headline}", copy_headline or "").replace("{body}", copy_body or "")
            .replace("{n}", str(n)).replace("{total}", str(total))).strip()


def margins(spec: dict) -> dict:
    m = spec.get("margins", 64)
    if isinstance(m, dict):
        return {k: int(m[k]) for k in ("top", "right", "bottom", "left")}
    return {k: int(m) for k in ("top", "right", "bottom", "left")}


def assets_used(spec: dict) -> list:
    """Every Drive file id a spec references (background images, the logo)."""
    ids = []
    for part in [spec] + list((spec.get("role_overrides") or {}).values()):
        bg = part.get("background") or {}
        if bg.get("type") == "image" and bg.get("image_asset_id"):
            ids.append(bg["image_asset_id"])
        logo = part.get("logo") or {}
        if logo.get("show") and logo.get("asset_ref"):
            ids.append(logo["asset_ref"])
    return list(dict.fromkeys(ids))
