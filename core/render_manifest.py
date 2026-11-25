"""What a render ACTUALLY drew (2026-11-26, knowledge/client-governance.md §8).

The rule this module exists for: an agent reports what was PRODUCED, never
what was planned. Every statement about a deliverable — the card's notices,
the AI-content disclosure, the chat line, what the agents may say — is built
from the MANIFEST: the list of layers the renderer committed to the output.
Never from the brief.

    manifest = {"canvas": [W, H], "encoded": bool, "layers": [
        {"type": "background", "kind": "solid" | "split" | "gradient" | "image", "asset": id?},
        {"type": "accent", "kind": ..., "box": [x0, y0, x1, y1]?},
        {"type": "character", "id": agent, "pose_id": ..., "box": [...], "generated": True},
        {"type": "logo", "asset": id, "box": [...]},
        {"type": "text", "role": "headline" | "body" | ..., "text": the final string, "box": [...]},
        {"type": "counter", "text": "2/5"},
        # an asset an image / video model made (core/post_pipeline):
        {"type": "generated_image" | "generated_video", "generated": True, "characters": [agent, ...]},
        {"type": "real_asset", "asset": id}]}

- `declared(slide)`     what the BRIEF asked for on that slide.
- `missing(...)`        declared and not in the manifest → the card FAILS, by name.
- `disclosure(...)`     the AI-content notice: if and only if the manifest
                        holds a generated element.
- `facts(...)`          the manifest as the agents read it (prompt payloads).
- `trace(...)`          one log line per stage of a character's way to the file:
                        brief_declared → pose_selected → pose_fetched →
                        background_removed → composited → file_written.
"""
import contextvars
import re

SERVICE_NAME = "render_manifest"
STAGES = ("brief_declared", "pose_selected", "pose_fetched", "background_removed", "composited", "file_written")
GENERATED_TYPES = ("character", "generated_image", "generated_video")
MAX_TRACE = 60

_trace = contextvars.ContextVar("render_trace", default=None)


# ─── The stage log ───────────────────────────────────────────────────────────

class tracing:
    """`with tracing(client_id, post) as rows:` — every trace() inside is
    printed (the service log) AND collected in `rows` (kept on the item)."""

    def __init__(self, client_id=None, post: str = "", path: str = ""):
        self.state = {"client_id": client_id, "post": str(post or ""), "path": str(path or ""), "rows": []}

    def __enter__(self) -> list:
        self.token = _trace.set(self.state)
        return self.state["rows"]

    def __exit__(self, *exc):
        _trace.reset(self.token)
        return False


def trace(stage: str, character: str, outcome: str, slide="", detail: str = ""):
    """One stage of one character, logged. `outcome` starts with "ok" or says
    what happened instead. Never raises."""
    try:
        state = _trace.get() or {}
        row = {"stage": stage, "character": str(character or ""), "outcome": str(outcome or "")[:80],
               "slide": str(slide if slide is not None else ""), "detail": str(detail or "")[:160]}
        if state.get("path"):
            row["path"] = state["path"]
        print(f"[character_trace] client={state.get('client_id')} post={state.get('post') or '-'} "
              f"path={state.get('path') or '-'} slide={row['slide'] or '-'} character={row['character'] or '-'} "
              f"stage={stage} outcome={row['outcome']}" + (f" | {row['detail']}" if row["detail"] else ""))
        if state:
            state["rows"].append(row)
            del state["rows"][:-MAX_TRACE]
    except Exception:
        pass


def lost_at(rows: list, character: str = "") -> dict:
    """The first stage that did not end "ok" (for `character`, or anyone): {} = it got through."""
    for row in rows or []:
        if (not character or row.get("character") == character) and not str(row.get("outcome") or "").startswith("ok"):
            return row
    return {}


# ─── Building a manifest ─────────────────────────────────────────────────────

def new(width: int, height: int) -> dict:
    return {"canvas": [int(width), int(height)], "encoded": False, "layers": []}


def _box(rect) -> list:
    return [int(round(float(v))) for v in rect][:4] if rect else []


def layer(manifest, layer_type: str, **fields):
    """Adds one committed layer. A manifest of None (nobody asked) = no-op."""
    if manifest is None:
        return
    row = {"type": layer_type}
    for key, value in fields.items():
        if value in (None, "", [], {}):
            continue
        row[key] = _box(value) if key == "box" else value
    if layer_type in GENERATED_TYPES:
        row["generated"] = True
    manifest["layers"].append(row)


def for_asset(kind: str, out: dict) -> dict:
    """The manifest of an asset an image / video model (or the client's own
    folder) produced — core/media_agent's result `out`. What the generator
    reports it was given, not a reading of the pixels."""
    manifest = {"canvas": [], "encoded": True, "layers": []}
    if out.get("real_asset"):
        layer(manifest, "real_asset", asset=str((out.get("real_asset") or {}).get("source_file_id") or out.get("file_id") or ""))
        return manifest
    names = [str(c.get("agent") or "") for c in out.get("characters") or [] if isinstance(c, dict) and c.get("agent")]
    layer(manifest, "generated_video" if kind == "video" else "generated_image", characters=names)
    for c in out.get("characters") or []:
        if isinstance(c, dict) and c.get("agent"):
            layer(manifest, "character", id=str(c["agent"]), pose_id=str(c.get("pose_id") or ""), in_scene=True)
    return manifest


# ─── Reading a manifest ──────────────────────────────────────────────────────

def layers(manifest, kind: str = "") -> list:
    return [l for l in ((manifest or {}).get("layers") or []) if isinstance(l, dict) and (not kind or l.get("type") == kind)]


def characters(manifest) -> list:
    return list(dict.fromkeys(str(l.get("id") or "") for l in layers(manifest, "character") if l.get("id")))


def has_generated(manifest) -> bool:
    return any(l.get("generated") for l in layers(manifest))


def disclosure(manifests) -> list:
    """The AI-content notice codes for one manifest or several: "ai_label"
    when a generated CHARACTER was drawn, "ai_content" when the picture
    itself is generated, [] when nothing in it is generated. If and only if."""
    many = manifests if isinstance(manifests, list) else [manifests]
    if any(characters(m) for m in many):
        return ["ai_label"]
    if any(has_generated(m) for m in many):
        return ["ai_content"]
    return []


def _same_text(a: str, b: str) -> bool:
    squash = lambda s: re.sub(r"\s+", " ", str(s or "")).strip()   # noqa: E731  (wrapping adds line breaks)
    return squash(a) == squash(b)


def declared(slide: dict) -> list:
    """What the BRIEF declared for one slide: its characters and its copy."""
    out = []
    names = list((slide.get("declared") or {}).get("characters") or [])
    wants = (slide.get("wants_character") or {}).get("agent")
    if wants and wants not in names:
        names.append(wants)
    out += [{"type": "character", "id": str(n)} for n in names if n]
    for role, key in (("headline", "copy_headline"), ("body", "copy_body")):
        if str(slide.get(key) or "").strip():
            out.append({"type": "text", "id": role, "text": str(slide[key])})
    return out


def missing(wanted: list, manifest) -> list:
    """Declared elements the manifest lacks: [{"type", "id"}]. No manifest =
    everything declared is missing (nothing is known to have been drawn)."""
    drawn_characters = set(characters(manifest))
    texts = [str(l.get("text") or "") for l in layers(manifest, "text")]
    out = []
    for w in wanted or []:
        if w["type"] == "character":
            if w["id"] not in drawn_characters:
                out.append({"type": "character", "id": w["id"]})
        elif w["type"] == "text":
            # the copy is drawn when some text layer carries it (a block may add a prefix around it)
            if not any(_same_text(w["text"], t) or re.sub(r"\s+", " ", w["text"]).strip() in re.sub(r"\s+", " ", t)
                       for t in texts):
                out.append({"type": "text", "id": w["id"]})
    return out


def facts(manifest) -> dict:
    """The manifest as an agent reads it: only what was drawn."""
    if not manifest:
        return {}
    bg = (layers(manifest, "background") or [{}])[0]
    return {"characters": characters(manifest),
            "texts": {l.get("role") or "text": l.get("text", "") for l in layers(manifest, "text")},
            "background": bg.get("kind", ""), "logo": bool(layers(manifest, "logo")),
            "generated_picture": bool(layers(manifest, "generated_image") or layers(manifest, "generated_video")),
            "real_photo": bool(layers(manifest, "real_asset"))}


FACTS_RULE = ("`drawn` on an item is its RENDER MANIFEST: what was actually drawn into the file. It is the only "
              "source for anything you say a post CONTAINS — who appears in it, what it shows, which text is on "
              "it, whether it needs an AI label. An item without `drawn` has not been rendered: speak of it as a "
              "plan only (\"מתוכנן\"), never as containing something. Never say a character appears in a post "
              "unless drawn.characters names it. `not_drawn` lists what the brief asked for and the render "
              "lacks: that post FAILED — say so plainly, name what is missing, and never call it ready.")
