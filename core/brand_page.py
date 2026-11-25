"""The client's "מיתוג" page (2026-11-26, knowledge/client-governance.md §7).

NOT a store. Everything the client sets here is written into the brand brief
the agents already read (core/brand_brief — one new VERSION per save, by
"client"), so a change on the page reaches them on their next turn:

- colours   → the brief's PALETTE (`palette.variants[label][role]`), the same
              object the slide renderer takes its roles from. A palette with
              segment variants (client 27: black + red for private customers,
              black + gold for business) is shown and edited PER VARIANT — it
              is never flattened to one palette.
- typefaces → `visual_identity.typefaces` in the brief's text, marked [stated].
- tone      → `voice.tone_attributes` (says) and `voice.avoid` (does not say).
- logos and existing assets → files in the client's own media folder
              (sub-folder "brand", appProperties carry the version), listed
              in the brief under `assets.logo_files` with their file ids, so
              an agent can put the logo on a slide (`logo.asset_ref`).

Behaviour (§7): what the client uploads is ACCEPTED as it is — nothing here
refuses a logo for its quality, substitutes one, or stores it as a draft.
If the branding is measurably weak the page may show ONE friendly note
(`assessment`): what works first, the specific weakness, the investment
framing, the cost reality. Dismissed once = never shown again, whatever
changes later. A business with nothing at all gets no note: there the
system leads (`lead`: the branding studio starts).
"""
import re
from datetime import datetime, timezone

from core import brand_brief, drive_storage

SERVICE_NAME = "brand_page"
FOLDER = "brand"
LOGO_VERSIONS = ("full", "mark", "on_light", "on_dark")
ASSET = "asset"
MAX_MB = 15
MAX_FILES = 40
MAX_WORDS = 12
WORD_CHARS = 60
FORMATS = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml",
           "pdf": "application/pdf"}
RASTER = ("png", "jpg", "webp")
SMALL_LOGO_PX = 500          # under this a logo blurs on a 1080 px slide and in print
TEXT_CONTRAST = 4.5          # WCAG AA for body text
ACCENT_CONTRAST = 3.0        # WCAG for large text / graphic elements


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or next(iter(table.values()))


ERRORS = {
    "empty": _five("הקובץ ריק", "The file is empty", "Le fichier est vide", "الملف فارغ", "Файл пуст"),
    "too_big": _five(f"הקובץ גדול מ-{MAX_MB}MB", f"The file is larger than {MAX_MB} MB",
                     f"Le fichier dépasse {MAX_MB} Mo", f"الملف أكبر من {MAX_MB} ميغابايت", f"Файл больше {MAX_MB} МБ"),
    "bad_format": _five("אפשר להעלות PNG, JPG, WEBP, SVG או PDF", "You can upload PNG, JPG, WEBP, SVG or PDF",
                        "Formats acceptés : PNG, JPG, WEBP, SVG ou PDF", "يمكن رفع PNG أو JPG أو WEBP أو SVG أو PDF",
                        "Можно загрузить PNG, JPG, WEBP, SVG или PDF"),
    "too_many": _five(f"יש כבר {MAX_FILES} קבצים — מחקו אחד כדי להוסיף", f"There are already {MAX_FILES} files — delete one to add",
                      f"Il y a déjà {MAX_FILES} fichiers — supprimez-en un", f"يوجد {MAX_FILES} ملفًا — احذفوا واحدًا للإضافة",
                      f"Уже {MAX_FILES} файлов — удалите один, чтобы добавить"),
    "storage": _five("לא הצלחנו לשמור כרגע — נסו שוב בעוד רגע", "We couldn't save right now — try again in a moment",
                     "Enregistrement impossible pour l'instant — réessayez", "تعذّر الحفظ الآن — حاولوا بعد قليل",
                     "Не удалось сохранить — попробуйте чуть позже"),
    "bad_color": _five("צבע צריך להיות בפורמט ‎#RRGGBB", "A colour must look like #RRGGBB", "Une couleur s'écrit #RRGGBB",
                       "اللون بصيغة ‎#RRGGBB", "Цвет в формате #RRGGBB"),
    "not_found": _five("הקובץ לא נמצא", "File not found", "Fichier introuvable", "الملف غير موجود", "Файл не найден"),
}

SEGMENTS = {
    "b2c": _five("לקוחות פרטיים", "Private customers", "Clients particuliers", "عملاء أفراد", "Частные клиенты"),
    "b2b": _five("עסקים, קבוצות ואירועים", "Business, teams and events", "Entreprises, équipes et événements",
                 "شركات وفرق وفعاليات", "Бизнес, команды и события"),
    "": _five("כל הקהלים", "All audiences", "Tous les publics", "كل الجماهير", "Все аудитории"),
}

# The one friendly note (§7), built from fragments — never a model's free text
NOTE = {
    "works_logo_colors": _five("יש לכם לוגו וצבעים משלכם, וזה כבר בסיס שעובדים איתו.",
                               "You have your own logo and colours, and that is already a base we work with.",
                               "Vous avez votre logo et vos couleurs : c'est déjà une base avec laquelle nous travaillons.",
                               "لديكم شعار وألوان خاصة بكم، وهذه قاعدة نعمل بها فعلًا.",
                               "У вас есть свой логотип и цвета — это уже основа, с которой мы работаем."),
    "works_logo": _five("יש לכם לוגו משלכם, ואנחנו עובדים איתו כמו שהוא.",
                        "You have your own logo, and we work with it as it is.",
                        "Vous avez votre logo et nous travaillons avec lui tel quel.",
                        "لديكم شعاركم ونحن نعمل به كما هو.", "У вас есть свой логотип, и мы работаем с ним как есть."),
    "works_colors": _five("הצבעים שלכם מוגדרים וברורים, ואנחנו עובדים איתם כמו שהם.",
                          "Your colours are defined and clear, and we work with them as they are.",
                          "Vos couleurs sont définies et claires, nous travaillons avec elles telles quelles.",
                          "ألوانكم محددة وواضحة ونعمل بها كما هي.", "Ваши цвета заданы и понятны — работаем с ними как есть."),
    "text_contrast": _five("בגרסת ״{label}״ צבע הטקסט ({fg}) כמעט נבלע ברקע ({bg}) — במובייל קשה יהיה לקרוא אותו.",
                           "In \"{label}\" the text colour ({fg}) nearly disappears into the background ({bg}) — on a phone it will be hard to read.",
                           "Dans « {label} », la couleur du texte ({fg}) se fond dans le fond ({bg}) — difficile à lire sur mobile.",
                           "في «{label}» لون النص ({fg}) يكاد يختفي في الخلفية ({bg}) — ستصعب قراءته على الهاتف.",
                           "В «{label}» цвет текста ({fg}) почти сливается с фоном ({bg}) — на телефоне его трудно читать."),
    "accent_contrast": _five("בגרסת ״{label}״ צבע ההדגשה ({accent}) חלש מול הרקע ({bg}) — כותרת או כפתור בצבע הזה לא יבלטו.",
                             "In \"{label}\" the accent ({accent}) is weak against the background ({bg}) — a headline or button in it will not stand out.",
                             "Dans « {label} », l'accent ({accent}) ressort peu sur le fond ({bg}) — un titre ou un bouton ne se détachera pas.",
                             "في «{label}» لون التمييز ({accent}) ضعيف أمام الخلفية ({bg}) — العنوان أو الزر به لن يبرز.",
                             "В «{label}» акцент ({accent}) слаб на фоне ({bg}) — заголовок или кнопка не будут выделяться."),
    "small_logo": _five("קובץ הלוגו ברוחב {px} פיקסלים — בפוסט הוא ייראה חד, אבל בשלט או בדפוס הוא ייצא מטושטש.",
                        "The logo file is {px} pixels wide — fine in a post, but it will blur on a sign or in print.",
                        "Le fichier du logo fait {px} pixels de large — correct dans un post, flou sur une enseigne ou à l'impression.",
                        "ملف الشعار بعرض {px} بكسل — جيد في منشور، لكنه سيظهر مشوشًا على لافتة أو في الطباعة.",
                        "Файл логотипа шириной {px} пикселей — для поста хватит, но на вывеске или в печати он расплывётся."),
    "no_transparent": _five("הלוגו שמור עם רקע אטום — אי אפשר להניח אותו נקי על תמונה או על רקע כהה.",
                            "The logo is saved with a solid background — it cannot sit cleanly on a photo or a dark background.",
                            "Le logo a un fond plein — impossible de le poser proprement sur une photo ou un fond sombre.",
                            "الشعار محفوظ بخلفية مصمتة — لا يمكن وضعه بنظافة فوق صورة أو خلفية داكنة.",
                            "Логотип сохранён с непрозрачным фоном — его нельзя чисто поставить на фото или тёмный фон."),
    "invest": _five("אם אתם הולכים לדחוף את העסק כמו שצריך, שווה שהמראה יחזיק את זה. דרכנו עבודה כזאת עולה חלק קטן ממה שהיא עולה בשוק.",
                    "If you are going to push the business properly, the look should carry it. Through us this kind of work costs a fraction of the traditional market.",
                    "Si vous poussez vraiment l'activité, l'image doit suivre. Chez nous, ce travail coûte une fraction du marché traditionnel.",
                    "إذا كنتم ستدفعون العمل كما يجب، فالأفضل أن يحمله المظهر. عبرنا يكلّف هذا العمل جزءًا صغيرًا من سعر السوق.",
                    "Если вы всерьёз двигаете бизнес, внешний вид должен это выдерживать. У нас такая работа стоит малую часть рыночной цены."),
}


class BrandPageError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _alert(lines: list):
    try:
        from agents.master_agent import alert
        alert(SERVICE_NAME, lines)
    except Exception as e:
        print(f"[{SERVICE_NAME}] alert failed ({type(e).__name__})")


# ─── The brief's text: one field read / written in place ─────────────────────

def _section(lines: list, section: str) -> tuple:
    """(first line after the heading, end) of `## section`, or (None, None)."""
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            if start is not None:
                return start, i
            if brand_brief.strip_md(line[3:]).strip().lower() == section:
                start = i + 1
    return (start, len(lines)) if start is not None else (None, None)


def _field(lines: list, section: str, key: str) -> tuple:
    """(start, end) of the top-level `- key:` block in the section, else (None, where to add it)."""
    a, b = _section(lines, section)
    if a is None:
        return None, None
    head = re.compile(r"^-\s+\**" + re.escape(key) + r"\**\s*:", re.IGNORECASE)
    for i in range(a, b):
        if head.match(lines[i]):
            j = i + 1
            while j < b and lines[j].strip() and not re.match(r"^-\s", lines[j]) and not lines[j].startswith("#"):
                j += 1
            return i, j
    end = b
    while end > a and not lines[end - 1].strip():
        end -= 1
    return None, end


def read_field(body: str, section: str, key: str) -> str:
    lines = str(body or "").splitlines()
    i, j = _field(lines, section, key)
    if i is None:
        return ""
    raw = " ".join(l.strip() for l in lines[i:j])
    raw = re.sub(r"^-\s+\**" + re.escape(key) + r"\**\s*:\s*", "", raw, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", brand_brief.MARK_RE.sub("", brand_brief.strip_md(raw))).strip()


def set_field(body: str, section: str, key: str, block: list) -> str:
    """The brief with `- key: …` in `## section` replaced by `block` (lines);
    the field and the section are added when absent. Nothing else moves."""
    lines = str(body or "").splitlines()
    i, j = _field(lines, section, key)
    if i is not None:
        lines[i:j] = block
    elif j is not None:
        lines[j:j] = block
    else:
        lines += ["", f"## {section}", ""] + block
    return "\n".join(lines) + ("\n" if str(body or "").endswith("\n") else "")


def _mark() -> str:
    return f"[stated — the client, branding page, {datetime.now(timezone.utc).date().isoformat()}]"


def _words(values) -> list:
    out = []
    for v in values or []:
        w = re.sub(r"[\[\]\n\r#*`]", " ", str(v or ""))
        w = re.sub(r"\s+", " ", w).strip(" ,;")[:WORD_CHARS]
        if w and w.lower() not in [o.lower() for o in out]:
            out.append(w)
    return out[:MAX_WORDS]


def _split(value: str) -> list:
    return [w.strip() for w in re.split(r"[,;]", value or "") if w.strip()]


# ─── Colours: the brief's palette, per variant ───────────────────────────────

def _variants(client_id: int, cur: dict, lang: str) -> list:
    from core import post_brief
    pal = cur.get("palette") if isinstance(cur.get("palette"), dict) else {}
    declared = {v["label"]: v for v in post_brief.parse_brand(cur.get("text", "")).get("palette_variants") or []}
    out = []
    for label, roles in (pal.get("variants") or {}).items():
        d = declared.get(label) or {}
        out.append({"label": label, "segment": d.get("segment", ""), "applies_to": d.get("applies_to", ""),
                    "segment_label": pick(SEGMENTS.get(d.get("segment", ""), SEGMENTS[""]), lang) if (d or len(pal.get("variants") or {}) == 1) else "",
                    "roles": {r: (roles or {}).get(r, "") for r in brand_brief.COLOR_ROLES}})
    for label, d in declared.items():                # named in the brief, no colours stored yet
        if label not in (pal.get("variants") or {}):
            out.append({"label": label, "segment": d.get("segment", ""), "applies_to": d.get("applies_to", ""),
                        "segment_label": pick(SEGMENTS.get(d.get("segment", ""), SEGMENTS[""]), lang),
                        "roles": {r: "" for r in brand_brief.COLOR_ROLES}})
    return out


def save_colors(client_id: int, variants: list) -> dict:
    """[{label, roles: {bg, fg, accent, muted}}] → the brief's palette. A
    variant keeps its label (the brief's text names it); an empty role stays
    a gap (the renderer's neutral), never a guess."""
    cur = brand_brief.current(client_id)
    pal = dict(cur.get("palette") or {}) if isinstance(cur.get("palette"), dict) else {}
    known = dict(pal.get("variants") or {})
    for v in variants or []:
        label = str((v or {}).get("label") or "").strip()[:60] or "default"
        roles = {}
        for role in brand_brief.COLOR_ROLES:
            value = str(((v or {}).get("roles") or {}).get(role) or "").strip()
            if not value:
                continue
            if not brand_brief.HEX_RE.match(value):
                raise BrandPageError("bad_color")
            roles[role] = value.upper()
        known[label] = roles
    pal.update(variants=known, status="stated", note=f"Set by the client on the branding page, {_now()[:10]}.")
    return _save(client_id, cur, new_palette=pal)


def _save(client_id: int, cur: dict, new_text: str = None, new_palette=None) -> dict:
    if not str(cur.get("text") or "").strip() and new_text is None:
        new_text = _starter(client_id)
    try:
        return brand_brief.save_edit(client_id, new_text=new_text, new_palette=new_palette, by="client")
    except brand_brief.BriefError as e:
        print(f"[{SERVICE_NAME}] client {client_id}: brief not saved ({e})")
        raise BrandPageError("storage")


def _starter(client_id: int) -> str:
    """A client with no brief yet: the page's own facts start one (a DRAFT the
    automatic drafting and the manager build on)."""
    return (f"# Brand Brief\nclient_id: {int(client_id)}\n{brand_brief.DRAFT_STATUS_LINE}\n"
            "Started from what the client set on the branding page.\n\n## voice\n\n## visual_identity\n\n## assets\n")


def _text(cur: dict, client_id: int) -> str:
    return cur.get("text") if str(cur.get("text") or "").strip() else _starter(client_id)


def save_typefaces(client_id: int, headline: str, body: str) -> dict:
    cur = brand_brief.current(client_id)
    h, b = (_words([headline]) or [""])[0], (_words([body]) or [""])[0]
    block = [f"- typefaces: headline: {h or '—'}; body: {b or '—'} {_mark()}"]
    return _save(client_id, cur, new_text=set_field(_text(cur, client_id), "visual_identity", "typefaces", block))


def save_tone(client_id: int, says: list, not_says: list) -> dict:
    cur = brand_brief.current(client_id)
    text = _text(cur, client_id)
    text = set_field(text, "voice", "tone_attributes", [f"- tone_attributes: {', '.join(_words(says)) or '—'} {_mark()}"])
    text = set_field(text, "voice", "avoid", [f"- avoid: {', '.join(_words(not_says)) or '—'} {_mark()}"])
    return _save(client_id, cur, new_text=text)


def _typefaces(body: str) -> dict:
    raw = read_field(body, "visual_identity", "typefaces")
    m = re.match(r"^headline:\s*(.*?);\s*body:\s*(.*)$", raw)
    clean = lambda s: "" if s.strip() in ("—", "-") else s.strip()
    return {"headline": clean(m.group(1)), "body": clean(m.group(2)), "note": ""} if m else {"headline": "", "body": "", "note": raw}


# ─── Logos and existing assets: the client's own folder ──────────────────────

def sniff(content: bytes) -> str:
    head = content[:16]
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    if head[:5] == b"%PDF-":
        return "pdf"
    start = content[:600].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if start.startswith(b"<svg") or (start.startswith(b"<?xml") and b"<svg" in content[:4000].lower()):
        return "svg"
    return ""


def _measure(content: bytes, ext: str) -> dict:
    if ext not in RASTER:
        return {}
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(content)) as im:
            alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
            return {"width": str(im.width), "height": str(im.height), "alpha": "1" if alpha else "0"}
    except Exception:
        return {}


@drive_storage.scoped
def _folder(client_id: int) -> str:
    from agents.media_agent import _subfolder
    return _subfolder(client_id, FOLDER)


def _clean_name(filename: str, ext: str, version: str) -> str:
    base = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    base = base.rsplit(".", 1)[0] if "." in base else base
    base = "".join(c for c in base if c.isalnum() or c in " -_()").strip()[:70]
    return f"{base or version}.{ext}"


def _row(f: dict) -> dict:
    p = f.get("appProperties") or {}
    num = lambda k: int(p[k]) if str(p.get(k) or "").isdigit() else None
    ext = f.get("name", "").rsplit(".", 1)[-1].lower()
    return {"id": f.get("id"), "name": f.get("name", ""), "version": p.get("brand_version") or ASSET,
            "format": "jpg" if ext == "jpeg" else ext, "size": int(f.get("size") or 0), "width": num("width"),
            "height": num("height"), "alpha": p.get("alpha") == "1" if p.get("alpha") else None,
            "uploaded_at": p.get("uploaded_at") or f.get("createdTime") or ""}


@drive_storage.scoped
def files(client_id: int) -> list:
    """Everything in the brand folder, newest first. Raises when Drive can't be read."""
    from core import drive_service as drive
    listed = drive.list_files(_folder(client_id), fields="id,name,mimeType,size,createdTime,appProperties")
    rows = [_row(f) for f in listed if (f.get("appProperties") or {}).get("uallak_kind") == "brand_file"]
    return sorted(rows, key=lambda r: r["uploaded_at"], reverse=True)


@drive_storage.scoped
def upload(client_id: int, version: str, filename: str, content: bytes) -> dict:
    """Store what the client uploaded, AS IT IS. The only refusals are the
    ones a file system needs (empty, not an image/PDF, size) — never quality."""
    from core import drive_service as drive
    version = version if version in LOGO_VERSIONS else ASSET
    if not content:
        raise BrandPageError("empty")
    if len(content) > MAX_MB * 1024 * 1024:
        raise BrandPageError("too_big")
    ext = sniff(content)
    if not ext:
        raise BrandPageError("bad_format")
    props = {"uallak_kind": "brand_file", "brand_version": version, "uploaded_at": _now(), **_measure(content, ext)}
    try:
        if len(files(client_id)) >= MAX_FILES:
            raise BrandPageError("too_many")
        stored = drive.upload_bytes(_folder(client_id), _clean_name(filename, ext, version), content, FORMATS[ext],
                                    app_properties=props)
    except BrandPageError:
        raise
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: upload failed ({type(e).__name__})")
        raise BrandPageError("storage")
    _sync_brief(client_id)
    return _row({"id": stored.get("id"), "name": stored.get("name") or _clean_name(filename, ext, version),
                 "size": len(content), "appProperties": props})


@drive_storage.scoped
def delete(client_id: int, file_id: str) -> bool:
    from core import drive_service as drive
    try:
        if not any(r["id"] == file_id for r in files(client_id)):
            raise BrandPageError("not_found")
        drive.delete_file(file_id)
    except BrandPageError:
        raise
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: delete failed ({type(e).__name__})")
        raise BrandPageError("storage")
    _sync_brief(client_id)
    return True


@drive_storage.scoped
def file_bytes(client_id: int, file_id: str) -> tuple:
    """(bytes, mime) of a file in THIS client's brand folder, else BrandPageError."""
    from core import drive_service as drive
    row = next((r for r in files(client_id) if r["id"] == file_id), None)
    if not row or row["format"] not in FORMATS:
        raise BrandPageError("not_found")
    return drive.download_file(file_id), FORMATS[row["format"]]


def current_logos(rows: list) -> dict:
    """{version: the newest file of that version}."""
    out = {}
    for r in rows:
        if r["version"] in LOGO_VERSIONS and r["version"] not in out:
            out[r["version"]] = r
    return out


def _sync_brief(client_id: int):
    """The brief's `assets.logo_files` = the files as they are NOW (agents
    reference a logo by its file id). A failure alerts; the upload stands."""
    try:
        rows = files(client_id)
        logos = current_logos(rows)
        cur = brand_brief.current(client_id)
        block = [f"- logo_files: the client's own, uploaded on the branding page — use as they are {_mark()}"]
        block += [f"  - {v}: file_id {logos[v]['id']} ({logos[v]['name']})" for v in LOGO_VERSIONS if v in logos]
        others = [r for r in rows if r["version"] == ASSET][:12]
        block += [f"  - existing asset: file_id {r['id']} ({r['name']})" for r in others]
        if len(block) == 1:
            block = ["- logo_files: none uploaded"]
        _save(client_id, cur, new_text=set_field(_text(cur, client_id), "assets", "logo_files", block))
    except Exception as e:
        _alert([f"client {client_id}: a branding file changed but the brand brief was not updated "
                f"({type(e).__name__}) — the agents do not see it yet"])


# ─── The one friendly note (§7) ──────────────────────────────────────────────

def _lum(hex_color: str) -> float:
    v = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    v = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in v]
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def weaknesses(variants: list, logos: dict, stated: bool) -> list:
    """Measured, specific problems — [(code, {fields})], the worst first.
    Colours are judged only when someone STATED them (a provisional or
    site-read palette is ours to fix, not the client's weakness)."""
    out = []
    if stated:
        for v in variants:
            r = v["roles"]
            ok = lambda k: bool(brand_brief.HEX_RE.match(r.get(k) or ""))
            if ok("bg") and ok("fg") and contrast(r["bg"], r["fg"]) < TEXT_CONTRAST:
                out.append(("text_contrast", {"label": v["label"], "bg": r["bg"], "fg": r["fg"]}))
            elif ok("bg") and ok("accent") and contrast(r["bg"], r["accent"]) < ACCENT_CONTRAST:
                out.append(("accent_contrast", {"label": v["label"], "bg": r["bg"], "accent": r["accent"]}))
    rasters = [l for l in logos.values() if l["format"] in RASTER]
    if rasters and len(rasters) == len(logos):
        widest = max((l["width"] or 0) for l in rasters)
        if 0 < widest < SMALL_LOGO_PX:
            out.append(("small_logo", {"px": widest}))
        if all(l["alpha"] is False for l in rasters):
            out.append(("no_transparent", {}))
    return out


def assessment(variants: list, logos: dict, stated: bool, dismissed: bool, lang: str) -> dict:
    """{} or the ONE note. Never for a business with no branding (the system
    leads there), never after a dismissal."""
    if dismissed or not (logos or stated):
        return {}
    found = weaknesses(variants, logos, stated)[:2]
    if not found:
        return {}
    opens = "works_logo_colors" if logos and stated else "works_logo" if logos else "works_colors"
    return {"opens": pick(NOTE[opens], lang),
            "points": [pick(NOTE[code], lang).format(**fields) for code, fields in found],
            "closes": pick(NOTE["invest"], lang), "codes": [c for c, _ in found]}


def dismiss_assessment(client_id: int) -> bool:
    """Dismissed once = it does not come back (kept on the brief's own row)."""
    return brand_brief.set_page_state(client_id, assessment_dismissed_at=_now())


# ─── The page ────────────────────────────────────────────────────────────────

def view(client_id: int, lang: str = "he") -> dict:
    cur = brand_brief.current(client_id)
    body = cur.get("text", "")
    pal = cur.get("palette") if isinstance(cur.get("palette"), dict) else {}
    variants = _variants(client_id, cur, lang)
    try:
        rows, files_ok = files(client_id), True
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: brand folder unreadable ({type(e).__name__})")
        rows, files_ok = [], False
    logos = current_logos(rows)
    stated = str(pal.get("status") or "").lower() == "stated"
    has_colors = any(any(v["roles"].values()) for v in variants)
    kit = {}
    try:
        from core import branding
        kit = branding.kit(client_id) or {}
    except Exception:
        kit = {}
    state = brand_brief.page_state(client_id)
    return {
        "brief_status": cur.get("status") or "missing",
        "colors": {"variants": variants, "roles": list(brand_brief.COLOR_ROLES), "stated": stated,
                   "provisional": bool(variants) and not stated, "segmented": len(variants) > 1},
        "typefaces": _typefaces(body),
        "tone": {"says": _split(read_field(body, "voice", "tone_attributes").replace("—", "")),
                 "not_says": _split(read_field(body, "voice", "avoid").replace("—", ""))},
        "logo_versions": list(LOGO_VERSIONS), "logos": logos,
        "assets": [r for r in rows if r["version"] == ASSET],
        "earlier": [r for r in rows if r["version"] in LOGO_VERSIONS and logos.get(r["version"], {}).get("id") != r["id"]],
        "files_ok": files_ok, "studio_kit": bool(kit),
        # nothing of their own and nothing approved in the studio: the system leads
        "lead": files_ok and not rows and not kit and not stated and not has_colors,
        "assessment": assessment(variants, logos, stated, bool(state.get("assessment_dismissed_at")), lang),
    }


def error_text(code: str, lang: str) -> str:
    return pick(ERRORS.get(code) or ERRORS["storage"], lang)
