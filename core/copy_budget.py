"""How much copy a text slide holds — known where the copy is WRITTEN
(2026-11-25, docs/SLIDE-RENDERER.md §Copy budget).

The renderer refuses text that can't fit without breaking the legibility
rules (core/slide_renderer: overflow / headline_words / overlap). That guard
is right; it used to speak only AFTER the manager approved the copy ("עיצוב
שקופיות הטקסט נכשל"). Now the same limits reach every earlier step:

  budget()      per slide role → per copy field (copy_headline / copy_body):
                the lines its block holds, about how many characters a line
                takes and the word limit — MEASURED from the active template
                (core/slide_design.base_spec: the client's template for this
                palette variant, else the starter) with the bundled font at
                the template's size/weight, never a guessed character count.
                Bigger type in the template = a smaller budget, automatically.
  for_prompt()  that budget for every palette variant, in the content
                personas' data (support_agent._with_content_context).
  fit()         the EXACT check of the copy as written: the renderer's own
                guard pass (encode=False) on the template's geometry. Used at
                submit (one revise round for the social agent), on the
                approval card in the Social tab (per slide, BEFORE approval)
                and after an edit — the manager may still approve, never by
                surprise.
  problem_lines()  a failed render in plain words: which slide, which field,
                by roughly how much (also post_pipeline's production line).

Geometry only: the fit check draws on a plain background with neutral colours
and no photo / logo / character — text size, wrapping, the safe area, the
headline word limit and collisions don't depend on them (contrast does; it is
not a length problem and stays the designer's).
"""
import copy
import hashlib
import json
from collections import OrderedDict

from core import slide_spec

SERVICE_NAME = "copy_budget"
FIELDS = {"headline": "copy_headline", "body": "copy_body"}
SLIDE_ROLES = ("hook", "point", "proof", "cta")
# A representative Hebrew line (common letters, spaces between words): its
# measured width / its length = the average advance of one character in that
# font, size and weight
SAMPLE_HE = "המתנה שנשארת איתכם הרבה אחרי שהעטיפה נפתחה, כי כל זוג מעוצב פעם אחת בלבד"
WRAP_FILL = 0.88          # greedy word wrap never fills every line to the edge
NEUTRAL = {"bg": "#FFFFFF", "fg": "#000000", "accent": "#000000", "muted": "#000000"}
LENGTH_CODES = ("overflow", "headline_words", "overlap")
_CACHE = OrderedDict()
CACHE_MAX = 256


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or next(iter(table.values()))


FIELD_TEXT = {
    "kicker": _five("הכותרת העליונה", "the kicker", "le surtitre", "العنوان العلوي", "надзаголовок"),
    "headline": _five("הכותרת", "the headline", "le titre", "العنوان", "заголовок"),
    "body": _five("הטקסט", "the body text", "le texte", "النص", "текст"),
    "cta": _five("הקריאה לפעולה", "the call to action", "l'appel à l'action", "الدعوة لإجراء", "призыв"),
    "counter": _five("מונה השקופיות", "the slide counter", "le compteur", "عداد الشرائح", "счётчик слайдов"),
}
LINE_TEXT = {
    "lines": _five(
        "שקופית {n}, {field}: ארוך בערך ב-{pct}% ממה שנכנס ({lines} שורות, יש מקום ל-{fit}). צריך לקצר בערך {cut} תווים.",
        "Slide {n}, {field}: about {pct}% longer than fits ({lines} lines, room for {fit}). Cut about {cut} characters.",
        "Diapositive {n}, {field} : environ {pct}% de trop ({lines} lignes, place pour {fit}). Retirez environ {cut} caractères.",
        "الشريحة {n}، {field}: أطول بنحو {pct}% مما يتسع ({lines} أسطر، المساحة لـ{fit}). يجب حذف نحو {cut} حرفًا.",
        "Слайд {n}, {field}: примерно на {pct}% длиннее, чем помещается ({lines} строк, места на {fit}). Сократите примерно на {cut} знаков."),
    "word": _five(
        "שקופית {n}, {field}: המילה \"{word}\" רחבה מדי לשורה אחת בגודל הזה — צריך מילה קצרה יותר.",
        "Slide {n}, {field}: the word \"{word}\" is too wide for one line at this size — use a shorter word.",
        "Diapositive {n}, {field} : le mot « {word} » est trop large pour une ligne à cette taille — un mot plus court.",
        "الشريحة {n}، {field}: الكلمة \"{word}\" أعرض من سطر واحد بهذا الحجم — يلزم كلمة أقصر.",
        "Слайд {n}, {field}: слово «{word}» не помещается в строку при этом размере — нужно слово короче."),
    "words": _five(
        "שקופית {n}, {field}: {words} מילים, המקסימום {limit}. צריך להוריד {cut} מילים.",
        "Slide {n}, {field}: {words} words, the maximum is {limit}. Remove {cut} words.",
        "Diapositive {n}, {field} : {words} mots, le maximum est {limit}. Retirez {cut} mots.",
        "الشريحة {n}، {field}: {words} كلمات، الحد الأقصى {limit}. احذفوا {cut} كلمات.",
        "Слайд {n}, {field}: {words} слов, максимум {limit}. Уберите {cut} слов."),
    "overlap": _five(
        "שקופית {n}: {field} נוגע ב{other} — הטקסט ארוך מדי למקום שלו.",
        "Slide {n}: {field} runs into {other} — the text is too long for its place.",
        "Diapositive {n} : {field} empiète sur {other} — le texte est trop long pour sa place.",
        "الشريحة {n}: {field} يتداخل مع {other} — النص أطول من مكانه.",
        "Слайд {n}: {field} наезжает на {other} — текст длиннее своего места."),
    "contrast": _five(
        "שקופית {n}, {field}: הטקסט לא בולט מספיק על הרקע.",
        "Slide {n}, {field}: the text doesn't stand out enough from the background.",
        "Diapositive {n}, {field} : le texte ne ressort pas assez du fond.",
        "الشريحة {n}، {field}: النص لا يبرز بما يكفي على الخلفية.",
        "Слайд {n}, {field}: текст недостаточно выделяется на фоне."),
    "other": _five(
        "שקופית {n}: העיצוב לא עבר את כללי הקריאוּת.",
        "Slide {n}: the design failed the legibility rules.",
        "Diapositive {n} : le design ne respecte pas les règles de lisibilité.",
        "الشريحة {n}: التصميم لم يجتز قواعد الوضوح.",
        "Слайд {n}: дизайн не прошёл правила читаемости."),
}
CARD_TEXT = {
    "fits": _five("נכנס בתבנית הנוכחית", "Fits the current template", "Tient dans le modèle actuel",
                  "يتسع في القالب الحالي", "Помещается в текущий шаблон"),
    "summary": _five(
        "{k} מתוך {n} שקופיות הטקסט ארוכות מדי לתבנית הנוכחית ({template}). אפשר לאשר כך — אבל עיצוב השקופיות האלה ייכשל עד שהטקסט יקוצר או שהתבנית תשתנה.",
        "{k} of {n} text slides are too long for the current template ({template}). You can still approve — but those slides' design will fail until the copy is shortened or the template changes.",
        "{k} des {n} diapositives texte sont trop longues pour le modèle actuel ({template}). Vous pouvez valider — mais leur design échouera tant que le texte n'est pas raccourci ou le modèle changé.",
        "{k} من {n} شرائح نصية أطول من القالب الحالي ({template}). يمكن الموافقة — لكن تصميمها سيفشل حتى يُختصر النص أو يتغير القالب.",
        "{k} из {n} текстовых слайдов слишком длинные для текущего шаблона ({template}). Одобрить можно — но их оформление не получится, пока текст не сократят или не сменят шаблон."),
    "room": _five("הסוכן ציין שהרעיון צריך יותר מקום: {why}", "The agent noted the idea needs more room: {why}",
                  "L'agent indique que l'idée demande plus de place : {why}",
                  "أشار الوكيل إلى أن الفكرة تحتاج مساحة أكبر: {why}",
                  "Агент отметил, что идее нужно больше места: {why}"),
}


# ─── The budget, measured from the template ──────────────────────────────────

def _font(block: dict):
    from core import slide_renderer
    return slide_renderer._font(block["font_family"], block["size"], block["weight"])


def _field_of(block: dict) -> tuple:
    """(copy field, fixed characters around it) — the field a block shows."""
    text = block.get("text")
    if text is None or text == "":
        text = {"headline": "{headline}", "body": "{body}"}.get(block.get("role"), "")
    for name, field in FIELDS.items():
        if "{" + name + "}" in text:
            fixed = text
            for ph in slide_spec.PLACEHOLDERS:
                fixed = fixed.replace(ph, "")
            return field, len(fixed.strip())
    return "", 0


def block_budget(spec: dict, block: dict) -> dict:
    """One text block of a (role-resolved) spec: the lines it holds and about
    how many characters, measured with the block's own font."""
    from core import slide_renderer
    W, H = spec["canvas"]["width"], spec["canvas"]["height"]
    m = slide_spec.margins(spec)
    font = _font(block)
    box_w = W * float(block["max_width_pct"]) / 100
    ascent, descent = font.getmetrics()
    lh = block["size"] * float(block.get("line_spacing") or 1.2)
    y = H * float(block["y_pct"]) / 100
    room = {"top": H - m["bottom"] - y, "bottom": y - m["top"],
            "center": 2 * min(y - m["top"], H - m["bottom"] - y)}[block["anchor"]]
    if block.get("max_height_pct"):
        room = min(room, H * float(block["max_height_pct"]) / 100)
    lines = max(0, int((room - ascent - descent) // lh) + 1) if room >= ascent + descent else 0
    per_char = slide_renderer._measure(font, SAMPLE_HE, "rtl", "he") / len(SAMPLE_HE)
    per_line = max(1, int(box_w // per_char))
    field, fixed = _field_of(block)
    out = {"block": block["role"], "field": field, "lines": lines, "chars_per_line": per_line,
           "max_chars": max(0, int(per_line * lines * WRAP_FILL) - fixed),
           "font": f"{block['font_family']} {block['size']}px/{block['weight']}"}
    if block["role"] == "headline":
        out["max_words"] = int((spec.get("guards") or {}).get("headline_max_words") or slide_renderer.HEADLINE_MAX_WORDS)
    return out


def spec_budget(spec: dict, role: str = "") -> dict:
    """{copy field: budget} for one slide role (role_overrides applied). Two
    blocks showing the same field = the tighter one."""
    spec = slide_spec.for_slide(slide_spec.validate(spec), role)
    out = {}
    for block in spec["text_blocks"]:
        b = block_budget(spec, block)
        if not b["field"]:
            continue
        have = out.get(b["field"])
        if not have or b["max_chars"] < have["max_chars"]:
            out[b["field"]] = b
    return out


def budget(client_id: int, palette: dict, aspect: str = "4:5") -> dict:
    """The active template's budget: {"template", "version", "aspect",
    "variant", "all": {field: …}, "by_role": {hook|point|proof|cta: {field: …}}}
    (by_role only for roles the template draws differently)."""
    from core import slide_design
    spec, name, based = slide_design.base_spec(client_id, palette, aspect or "4:5")
    out = {"template": name, "version": (based or {}).get("version") or 0, "aspect": aspect or "4:5",
           "variant": slide_spec.variant_for(palette), "all": spec_budget(spec)}
    roles = [r for r in SLIDE_ROLES if r in (spec.get("role_overrides") or {})]
    if roles:
        out["by_role"] = {r: spec_budget(spec, r) for r in roles}
    return out


PROMPT_RULE = ("slide_copy_budget = how much copy each TEXT SLIDE holds in the client's current template, measured "
               "with the real font (per palette variant = audience; by_role where a slide role is drawn differently): "
               "for copy_headline and copy_body the lines, max_chars (about) and the headline's max_words. Write every "
               "text_slide within it — shorter is better. If an idea genuinely needs more room, do NOT write copy that "
               "can't be drawn: say so to the manager, and if you still propose it set that asset's \"needs_room\" to "
               "one line on why. The budget follows the template: after a design change it is re-measured.")


def for_prompt(client_id: int) -> dict:
    """{"rule", "variants": {label: budget}} for the content personas.
    {} when it can't be measured (no renderer here) — never raises."""
    try:
        from core import post_brief
        facts = post_brief.load_context(client_id).get("brand_facts") or {}
        variants = facts.get("palette_variants") or [{}]
        return {"rule": PROMPT_RULE,
                "variants": {slide_spec.variant_for(v): _compact(budget(client_id, v)) for v in variants}}
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: no budget for the prompt ({type(e).__name__})")
        return {}


def _compact(b: dict) -> dict:
    keep = ("lines", "max_chars", "max_words")
    shrink = lambda fields: {f: {k: v[k] for k in keep if k in v} for f, v in fields.items()}
    out = {"template": b["template"], "all": shrink(b["all"])}
    if b.get("by_role"):
        out["by_role"] = {r: shrink(f) for r, f in b["by_role"].items()}
    return out


# ─── The exact check of the copy as written ──────────────────────────────────

def geometry_spec(spec: dict) -> dict:
    """The template with everything that doesn't decide where text goes made
    plain: solid background, no logo, no character."""
    out = copy.deepcopy(spec)
    parts = [out] + [v for v in (out.get("role_overrides") or {}).values() if isinstance(v, dict)]
    for part in parts:
        if "background" in part:
            part["background"] = {"type": "solid", "color_role": "bg"}
        if "logo" in part:
            part["logo"] = {"show": False}
        part.pop("character", None)
    return out


def _check_slide(spec: dict, s: dict, n: int, total: int) -> list:
    from core import slide_renderer
    try:
        slide_renderer.render_slide(spec, NEUTRAL, s.get("copy_headline", ""), s.get("copy_body", ""), n, total,
                                    s.get("role", ""), image_loader=lambda ref: b"", encode=False)
    except slide_renderer.RenderError as e:
        return [p for p in e.problems if p.get("code") in LENGTH_CODES]
    return []


def fit(client_id: int, palette: dict, slides: list, lang: str = "he") -> dict:
    """{"template", "checked", "over": [asset_id…], "slides": {asset_id:
    {"n", "fits", "problems", "lines"}}} for every text_slide of a post
    (n = its place in the post). {"checked": False} when it can't run."""
    from core import slide_design
    try:
        text = [(n, s) for n, s in enumerate(slides, 1) if s.get("asset_type", "text_slide") == "text_slide"]
        if not text:
            return {"checked": True, "over": [], "slides": {}, "template": ""}
        aspect = text[0][1].get("aspect_ratio") or "4:5"
        spec, name, based = slide_design.base_spec(client_id, palette, aspect)
        geo = geometry_spec(slide_spec.validate(spec))
        key = hashlib.sha1(json.dumps([geo, [(n, s.get("role"), s.get("copy_headline"), s.get("copy_body"))
                                              for n, s in text], len(slides), lang],
                                      sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return copy.deepcopy(_CACHE[key])
        out = {"template": name, "version": (based or {}).get("version") or 0, "checked": True, "over": [],
               "slides": {}}
        for n, s in text:
            problems = _check_slide(geo, s, n, len(slides))
            out["slides"][s["asset_id"]] = {"n": n, "fits": not problems, "problems": problems,
                                           "lines": problem_lines([{**p, "slide": n} for p in problems], lang)}
            if problems:
                out["over"].append(s["asset_id"])
        _CACHE[key] = copy.deepcopy(out)
        while len(_CACHE) > CACHE_MAX:
            _CACHE.popitem(last=False)
        return out
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: fit check not run ({type(e).__name__}: {str(e)[:120]})")
        return {"checked": False, "over": [], "slides": {}, "template": ""}


# ─── Plain words ─────────────────────────────────────────────────────────────

def _round_pct(value: float) -> int:
    return max(5, int(round(value / 5.0)) * 5)


def problem_line(p: dict, lang: str = "he") -> str:
    """ONE render problem → which slide, which field, by roughly how much."""
    n = p.get("slide") or "?"
    field = pick(FIELD_TEXT.get(p.get("role") or "", FIELD_TEXT["body"]), lang)
    kind = p.get("kind") or ""
    if p.get("code") == "overflow" and kind in ("lines", "safe_area") and p.get("have_px"):
        need, have = float(p.get("need_px") or 0), float(p.get("have_px") or 1)
        pct = _round_pct((need / have - 1) * 100) if have else 100
        lines, fit_lines = int(p.get("lines") or 0), int(p.get("fit_lines") or 0)
        cut = _cut_chars(p, lines, fit_lines) if lines and fit_lines < lines else 5
        return (pick(LINE_TEXT["lines"], lang).replace("{n}", str(n)).replace("{field}", field)
                .replace("{pct}", str(pct)).replace("{lines}", str(lines)).replace("{fit}", str(fit_lines))
                .replace("{cut}", str(cut)))
    if p.get("code") == "overflow" and kind == "word":
        return (pick(LINE_TEXT["word"], lang).replace("{n}", str(n)).replace("{field}", field)
                .replace("{word}", str(p.get("word") or "")))
    if p.get("code") == "headline_words":
        words, limit = int(p.get("words") or 0), int(p.get("limit") or 0)
        return (pick(LINE_TEXT["words"], lang).replace("{n}", str(n)).replace("{field}", field)
                .replace("{words}", str(words)).replace("{limit}", str(limit)).replace("{cut}", str(max(1, words - limit))))
    if p.get("code") == "overlap":
        other = pick(FIELD_TEXT.get(p.get("other") or "", FIELD_TEXT["body"]), lang)
        return (pick(LINE_TEXT["overlap"], lang).replace("{n}", str(n)).replace("{field}", field)
                .replace("{other}", other))
    if p.get("code") == "contrast":
        return pick(LINE_TEXT["contrast"], lang).replace("{n}", str(n)).replace("{field}", field)
    return pick(LINE_TEXT["other"], lang).replace("{n}", str(n))


def _cut_chars(p: dict, lines: int, fit_lines: int) -> int:
    """About how many characters to remove: the share of lines that don't fit,
    of the text's length when the renderer reported it (else a line's worth)."""
    length = int(p.get("text_chars") or 0)
    if length:
        return max(5, int(round(length * (lines - fit_lines) / lines)))
    return 5 * (lines - fit_lines)


def problem_lines(problems: list, lang: str = "he", limit: int = 8) -> list:
    """Every problem once, per slide and field. Overflow is said first; a
    collision of that same field on that slide is its consequence and isn't
    said again (a too-long body that also runs into the counter = one line)."""
    out, seen = [], set()
    for p in sorted(problems or [], key=lambda q: (int(q.get("slide") or 0), LENGTH_CODES.index(q["code"])
                                                   if q.get("code") in LENGTH_CODES else 9)):
        code = p.get("code")
        key = (p.get("slide"), p.get("role"), "length" if code in ("overflow", "overlap") else code)
        if key in seen:
            continue
        seen.add(key)
        out.append(problem_line(p, lang))
        if len(out) >= limit:
            break
    return out


def card(fitted: dict, slides: list, lang: str = "he") -> dict:
    """The approval card's part: per slide {"fits", "lines"} and one summary."""
    if not fitted.get("checked"):
        return {}
    per = {}
    for s in slides:
        got = (fitted.get("slides") or {}).get(s.get("asset_id"))
        if not got:
            continue
        lines = list(got["lines"])
        if s.get("needs_room"):
            lines.append(pick(CARD_TEXT["room"], lang).replace("{why}", s["needs_room"]))
        per[s["asset_id"]] = {"fits": got["fits"], "lines": lines,
                              "text": pick(CARD_TEXT["fits"], lang) if got["fits"] else ""}
    over = len(fitted.get("over") or [])
    summary = (pick(CARD_TEXT["summary"], lang).replace("{k}", str(over)).replace("{n}", str(len(per)))
               .replace("{template}", fitted.get("template") or "")) if over else ""
    return {"slides": per, "over": over, "summary": summary, "template": fitted.get("template", "")}
