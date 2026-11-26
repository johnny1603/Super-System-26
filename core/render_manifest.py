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
        {"type": "text", "role": "headline" | "body" | ..., "text": the final string, "box": [...],
         "font": the family that drew most of it, "weight": the REAL weight drawn, "weight_asked": the spec's,
         "fonts": [{"font", "source": bundled | library | upload, "weight", "script": hebrew | latin | neutral,
                    "chars"}]},                     # one row per font and script: which font drew which runs
        {"type": "counter", "text": "2/5", "font": family},
        # an asset an image / video model made (core/post_pipeline):
        {"type": "generated_image" | "generated_video", "generated": True, "characters": [agent, ...]},
        {"type": "real_asset", "asset": id}],
        # only for a client with a brand font (core/brand_fonts): what the brief asked for, and
        # every run another font drew instead — a visible condition on the card, never silent
        "fonts": {"wanted": {"headline", "body", "hebrew"},
                  "fallbacks": [{"role", "wanted", "drew", "script", "reason"}]}}

- `declared(slide)`     what the BRIEF asked for on that slide.
- `missing(...)`        declared and not in the manifest → the card FAILS, by name.
- `disclosure(...)`     the AI-content notice: if and only if the manifest
                        holds a generated element.
- `facts(...)`          the manifest as the agents read it (prompt payloads).
- `trace(...)`          one log line per stage of a character's way to the file:
                        brief_declared → pose_selected → pose_fetched →
                        background_removed → composited → file_written.
- `font_trace(...)`     one log line per text block of an encoded slide: which
                        font drew each of its runs.
- `font_fallbacks(...)` / `font_lines(...)`  the brand font the brief names
                        against the font the manifest shows.
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


def font_trace(slide, role: str, used: list, fallbacks: list = None):
    """Which font drew each run of ONE text block, logged. Never raises."""
    try:
        state = _trace.get() or {}
        runs = " ".join(f"{u.get('script')}:{u.get('font')}/{u.get('weight')}[{u.get('source')}]({u.get('chars')}ch)"
                        for u in used or [])
        fell = " ".join(f"FALLBACK {f.get('script')}: wanted {f.get('wanted')!r}, drew {f.get('drew')!r} "
                        f"({f.get('reason')})" for f in fallbacks or [])
        print(f"[font_trace] client={state.get('client_id')} post={state.get('post') or '-'} "
              f"path={state.get('path') or '-'} slide={slide if slide is not None else '-'} block={role} runs={runs}"
              + (f" | {fell}" if fell else ""))
    except Exception:
        pass


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


def fonts(manifest, wanted: dict = None, fallbacks: list = None):
    """Records the brand fonts the brief asked for and every run a different
    font drew. A client with no brand font and no fallback = nothing added."""
    if manifest is None:
        return
    wanted = {k: v for k, v in (wanted or {}).items() if v}
    seen, rows = set(), []
    for f in fallbacks or []:
        key = (f.get("role"), f.get("wanted"), f.get("drew"), f.get("script"), f.get("reason"))
        if key not in seen:
            seen.add(key)
            rows.append(dict(f))
    if wanted or rows:
        manifest["fonts"] = {"wanted": wanted, "fallbacks": rows}


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


def drawn_fonts(manifests) -> list:
    """The families that drew text, in order of first use, for one manifest or several."""
    many = manifests if isinstance(manifests, list) else [manifests]
    return list(dict.fromkeys(u.get("font") for m in many for l in layers(m, "text") for u in l.get("fonts") or []
                              if u.get("font")))


def font_fallbacks(manifests) -> list:
    """Every run a font other than the brief's brand font drew, once each:
    [{"role", "wanted", "drew", "script", "reason", "slides": [n, …]}]."""
    many = manifests if isinstance(manifests, list) else [manifests]
    out = {}
    for i, m in enumerate(many, 1):
        for f in ((m or {}).get("fonts") or {}).get("fallbacks") or []:
            key = (f.get("wanted"), f.get("drew"), f.get("script"), f.get("reason"))
            row = out.setdefault(key, {**f, "slides": []})
            n = (m or {}).get("n") or i
            if n not in row["slides"]:
                row["slides"].append(n)
    return list(out.values())


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


FONT_SCRIPT = {"hebrew": _five("הטקסט בעברית", "the Hebrew text", "le texte en hébreu", "النص العبري", "текст на иврите"),
               "latin": _five("הטקסט באנגלית", "the Latin text", "le texte en caractères latins", "النص اللاتيني",
                              "латинский текст"),
               "neutral": _five("המספרים והסימנים", "the numbers and symbols", "les chiffres et symboles",
                                "الأرقام والرموز", "цифры и знаки")}
FONT_LINE = _five("גופן המותג {wanted} לא צויר: {script} צויר בגופן {drew} — {why}.",
                  "The brand font {wanted} was not drawn: {script} was drawn in {drew} — {why}.",
                  "La police de marque {wanted} n'a pas été utilisée : {script} est en {drew} — {why}.",
                  "خط العلامة {wanted} لم يُستخدم: {script} رُسم بخط {drew} — {why}.",
                  "Фирменный шрифт {wanted} не использован: {script} набран шрифтом {drew} — {why}.")
FONT_REASON = {
    "unknown_family": _five("אין לנו גופן בשם הזה: צריך לבחור אותו מהספרייה או להעלות את הקובץ שלו בעמוד המיתוג",
                            "we have no font by that name: choose it from the library or upload its file on the branding page",
                            "nous n'avons pas de police de ce nom : choisissez-la dans la bibliothèque ou téléversez son fichier",
                            "لا يوجد لدينا خط بهذا الاسم: اختاروه من المكتبة أو ارفعوا ملفه في صفحة الهوية",
                            "у нас нет шрифта с таким названием: выберите его в библиотеке или загрузите файл"),
    "no_hebrew_pairing": _five("אין בו אותיות עבריות, ועוד לא נבחר גופן עברי שילווה אותו",
                               "it has no Hebrew letters, and no Hebrew font has been chosen to go with it yet",
                               "elle n'a pas de lettres hébraïques et aucune police hébraïque ne l'accompagne encore",
                               "لا يحتوي حروفًا عبرية ولم يُختر بعد خط عبري يرافقه",
                               "в нём нет ивритских букв, а парный ивритский шрифт ещё не выбран"),
    "missing_glyphs": _five("חסרים בו תווים שמופיעים בטקסט", "it lacks characters that appear in the text",
                            "il lui manque des caractères présents dans le texte", "تنقصه حروف واردة في النص",
                            "в нём нет знаков, которые есть в тексте"),
    "unavailable": _five("קובץ הגופן לא היה זמין בזמן הציור", "the font file was not available when the slide was drawn",
                         "le fichier de police n'était pas disponible au moment du rendu",
                         "ملف الخط لم يكن متاحًا وقت الرسم", "файл шрифта был недоступен в момент отрисовки"),
}
FONT_REASON_ALIAS = {"not_installed": "unavailable", "fetch_failed": "unavailable", "unreadable": "unavailable",
                     "no_file": "unavailable", "hebrew_pairing_unavailable": "unavailable"}


def font_lines(manifests, lang: str = "he") -> list:
    """The card's lines for a brand font that was NOT what drew the text —
    one per fallback, in the client's language. [] = the brand font drew it
    all (or the client has no brand font)."""
    def pick(table):
        return table.get(lang) or table["en"]
    out = []
    for f in font_fallbacks(manifests):
        reason = FONT_REASON_ALIAS.get(f.get("reason"), f.get("reason"))
        why = pick(FONT_REASON.get(reason) or FONT_REASON["unavailable"])
        out.append(pick(FONT_LINE).replace("{wanted}", str(f.get("wanted") or "")).replace("{drew}", str(f.get("drew") or ""))
                   .replace("{script}", pick(FONT_SCRIPT.get(f.get("script")) or FONT_SCRIPT["neutral"]))
                   .replace("{why}", why))
    return out


def facts(manifest) -> dict:
    """The manifest as an agent reads it: only what was drawn."""
    if not manifest:
        return {}
    bg = (layers(manifest, "background") or [{}])[0]
    return {"characters": characters(manifest), "fonts": drawn_fonts(manifest),
            "font_fallbacks": [{k: f.get(k) for k in ("wanted", "drew", "script", "reason")}
                               for f in font_fallbacks(manifest)],
            "texts": {l.get("role") or "text": l.get("text", "") for l in layers(manifest, "text")},
            "background": bg.get("kind", ""), "logo": bool(layers(manifest, "logo")),
            "generated_picture": bool(layers(manifest, "generated_image") or layers(manifest, "generated_video")),
            "real_photo": bool(layers(manifest, "real_asset"))}


FACTS_RULE = ("`drawn` on an item is its RENDER MANIFEST: what was actually drawn into the file. It is the only "
              "source for anything you say a post CONTAINS — who appears in it, what it shows, which text is on "
              "it, whether it needs an AI label. An item without `drawn` has not been rendered: speak of it as a "
              "plan only (\"מתוכנן\"), never as containing something. Never say a character appears in a post "
              "unless drawn.characters names it. `not_drawn` lists what the brief asked for and the render "
              "lacks: that post FAILED — say so plainly, name what is missing, and never call it ready. "
              "drawn.fonts = the fonts that really drew the text; drawn.font_fallbacks = where the brand font "
              "was NOT what drew it (wanted / drew / why): never say a post is in the brand font when it is "
              "listed there — say which font drew it and why.")
