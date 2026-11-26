"""What a BRANDING PACKAGE would add — shown on the client's branding page as
part of the brand ("this is what my brand is missing"), never as blocked
feature tiles (2026-11-27, docs/BRAND-BRIEF.md §The branding package).

Each section (SECTIONS) carries, in the client's language: its name, ONE line
saying what it would do for the business, and what it includes. On the page a
section opens to:

- the explanation and what it includes;
- "ask Johnny" — a REAL tracked team request (agent_actions.open_team_request,
  topic package_exception: a commercial question a person answers), never a
  model's promise and never a price from code;
- ONE real sample built from the client's OWN brand, once per section, only
  on an explicit request: the page shows the credit cost first and the request
  must carry the confirmation. After it the section is locked again — the
  sample stays visible, a second one is refused (`sample_used`).

A sample is DRAWN IN CODE from the brand as it stands (palette, fonts, the
client's own latest copy) by the slide renderer and Pillow — the same rule as
the branding studio: never an image model, never text inside a generated
image. That is why its cost is 0 generation credits today (SAMPLE_CREDITS);
the cost is still shown and confirmed, so a paid sample later changes one
number and nothing else. What a sample shows is frozen in a snapshot taken at
the request, so the picture never changes afterwards.

EVERY sample request and every question is logged — an admin alert carrying
"client {id}" (the Control Room matches on it) and the section: these are
buying signals. Storage: the brand brief's own row (`page.package`), like the
rest of that page. No migration, no new table.
"""
import io
from datetime import datetime, timezone

from core import brand_brief

SERVICE_NAME = "brand_package"
SECTION_IDS = ("typography", "visual_language", "templates", "brand_book")
SAMPLE_CREDITS = {s: 0 for s in SECTION_IDS}      # drawn in code: no generation credits (see the module docstring)
SHEET_BG = "#EDEDED"


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or next(iter(table.values()))


SECTIONS = {
    "typography": {
        "title": _five("טיפוגרפיה משלכם", "Typography of your own", "Votre propre typographie", "طباعة خاصة بكم", "Собственная типографика"),
        "line": _five("זוג גופנים שנבחר ומכויל לעסק שלכם, כך שכל כותרת מזוהה כשלכם עוד לפני שקוראים אותה.",
                      "A pair of typefaces chosen and tuned for your business, so every headline is recognised as yours before it is read.",
                      "Un duo de polices choisi et réglé pour votre entreprise : chaque titre se reconnaît comme le vôtre avant même d'être lu.",
                      "زوج خطوط مختار ومضبوط لنشاطكم، ليُعرف كل عنوان بأنه لكم قبل قراءته.",
                      "Пара шрифтов, подобранная и настроенная под ваш бизнес: каждый заголовок узнаётся как ваш ещё до прочтения."),
        "includes": [
            _five("גופן לכותרות וגופן לטקסט רץ, שנבדקו יחד בעברית ובאנגלית", "A headline face and a body face, tested together in Hebrew and English",
                  "Une police de titre et une de texte, testées ensemble en hébreu et en anglais", "خط للعناوين وخط للنص، مجرّبان معًا بالعبرية والإنجليزية",
                  "Шрифт для заголовков и шрифт для текста, проверенные вместе на иврите и английском"),
            _five("גדלים, משקלים וריווח קבועים לכל סוג פוסט", "Fixed sizes, weights and spacing for every kind of post",
                  "Tailles, graisses et espacements fixes pour chaque type de post", "أحجام وأوزان ومسافات ثابتة لكل نوع منشور",
                  "Фиксированные размеры, насыщенность и интервалы для каждого типа поста"),
            _five("הגדרה שנכנסת לבריף המותג, וכל פוסט מצויר לפיה", "A definition that enters the brand brief, and every post is drawn by it",
                  "Une définition inscrite dans le brief de marque, appliquée à chaque post", "تعريف يدخل موجز العلامة ويُرسم كل منشور وفقه",
                  "Определение в бриф бренда — по нему рисуется каждый пост")]},
    "visual_language": {
        "title": _five("שפה חזותית", "A visual language", "Un langage visuel", "لغة بصرية", "Визуальный язык"),
        "line": _five("אייקונים, דוגמאות וטקסטורות משלכם, כך שגם פוסט בלי לוגו נראה מיד שלכם.",
                      "Icons, patterns and textures of your own, so even a post without a logo looks like yours at once.",
                      "Icônes, motifs et textures à vous : même un post sans logo se reconnaît aussitôt.",
                      "أيقونات وأنماط وخامات خاصة بكم، ليبدو حتى المنشور بلا شعار أنه لكم فورًا.",
                      "Свои иконки, паттерны и текстуры: даже пост без логотипа сразу выглядит вашим."),
        "includes": [
            _five("סט אייקונים בקו אחד לשירותים ולמוצרים שלכם", "An icon set in one line style for your services and products",
                  "Un jeu d'icônes au trait unique pour vos services et produits", "مجموعة أيقونات بأسلوب خط واحد لخدماتكم ومنتجاتكم",
                  "Набор иконок в едином стиле для ваших услуг и товаров"),
            _five("דוגמאות (פטרנים) בצבעי המותג לרקעים ולמסגרות", "Patterns in the brand colours for backgrounds and frames",
                  "Des motifs aux couleurs de la marque pour fonds et cadres", "أنماط بألوان العلامة للخلفيات والإطارات",
                  "Паттерны в цветах бренда для фонов и рамок"),
            _five("טקסטורות וצורות שחוזרות בכל החומרים", "Textures and shapes that repeat across all materials",
                  "Textures et formes qui reviennent sur tous les supports", "خامات وأشكال تتكرر في كل المواد",
                  "Текстуры и формы, повторяющиеся во всех материалах")]},
    "templates": {
        "title": _five("סט תבניות", "A template set", "Un jeu de modèles", "مجموعة قوالب", "Набор шаблонов"),
        "line": _five("תבניות מוכנות לפוסט, לסטורי ולקרוסלה, כך שכל מה שיוצא מכם נראה כמו סדרה אחת ולא כמו אוסף מקרי.",
                      "Ready templates for a post, a story and a carousel, so everything you publish looks like one series and not a random collection.",
                      "Des modèles prêts pour post, story et carrousel : tout ce que vous publiez forme une série, pas une collection au hasard.",
                      "قوالب جاهزة للمنشور والستوري والكاروسيل، ليبدو كل ما تنشرونه سلسلة واحدة لا مجموعة عشوائية.",
                      "Готовые шаблоны для поста, сторис и карусели: всё, что вы публикуете, выглядит одной серией, а не случайным набором."),
        "includes": [
            _five("תבנית לכל פורמט: פיד, ריבוע וסטורי", "A template per format: feed, square and story",
                  "Un modèle par format : fil, carré et story", "قالب لكل صيغة: الخلاصة والمربع والستوري",
                  "Шаблон для каждого формата: лента, квадрат и сторис"),
            _five("גרסה לכל קהל ולכל פלטה של המותג", "A version for each audience and each palette of the brand",
                  "Une version par public et par palette de la marque", "نسخة لكل جمهور ولكل لوحة ألوان للعلامة",
                  "Версия для каждой аудитории и каждой палитры бренда"),
            _five("התבניות נשמרות אצלנו, וכל פוסט חדש מתחיל מהן", "The templates are kept with us, and every new post starts from them",
                  "Les modèles sont conservés chez nous et chaque nouveau post en part", "تُحفظ القوالب لدينا ويبدأ منها كل منشور جديد",
                  "Шаблоны хранятся у нас, и каждый новый пост начинается с них")]},
    "brand_book": {
        "title": _five("מסמך מותג", "A brand document", "Un document de marque", "وثيقة العلامة", "Бренд-документ"),
        "line": _five("מסמך אחד שמרכז את הלוגו, הצבעים, הגופנים והטון, כך שכל מי שעובד בשבילכם (מעצב, דפוס, עובד חדש) מוציא את אותו מותג.",
                      "One document holding the logo, colours, typefaces and tone, so everyone who works for you (a designer, a printer, a new employee) produces the same brand.",
                      "Un seul document réunissant logo, couleurs, polices et ton : toute personne qui travaille pour vous (graphiste, imprimeur, nouvel employé) produit la même marque.",
                      "وثيقة واحدة تجمع الشعار والألوان والخطوط والنبرة، ليُخرج كل من يعمل لكم (مصمم، مطبعة، موظف جديد) العلامة نفسها.",
                      "Один документ с логотипом, цветами, шрифтами и тоном: любой, кто работает на вас (дизайнер, типография, новый сотрудник), выдаёт один и тот же бренд."),
        "includes": [
            _five("כללי שימוש בלוגו: גרסאות, שוליים ומה אסור", "Logo rules: versions, clear space and what is not allowed",
                  "Règles du logo : versions, marges et interdits", "قواعد الشعار: النسخ والهوامش وما لا يُسمح به",
                  "Правила логотипа: версии, поля и запреты"),
            _five("הצבעים עם הערכים המדויקים לדיגיטל ולדפוס", "The colours with exact values for screen and print",
                  "Les couleurs avec leurs valeurs exactes pour l'écran et l'impression", "الألوان بقيمها الدقيقة للشاشة والطباعة",
                  "Цвета с точными значениями для экрана и печати"),
            _five("הגופנים, הטון ודוגמאות של נכון ולא נכון", "The typefaces, the tone, and examples of right and wrong",
                  "Les polices, le ton et des exemples de bon et de mauvais usage", "الخطوط والنبرة وأمثلة للصحيح والخاطئ",
                  "Шрифты, тон и примеры «правильно / неправильно»")]},
}

TEXT = {
    "title": _five("מה חבילת מיתוג תוסיף למותג שלכם", "What a branding package would add to your brand",
                   "Ce qu'un forfait branding ajouterait à votre marque", "ما الذي تضيفه باقة الهوية لعلامتكم",
                   "Что пакет брендинга добавит вашему бренду"),
    "sub": _five("אלה החלקים שעדיין אין למותג שלכם. כל אחד נפתח להסבר, ואפשר לבקש דוגמה אחת אמיתית שנבנית מהמותג שלכם.",
                 "These are the parts your brand does not have yet. Each opens to an explanation, and you can ask for one real sample built from your own brand.",
                 "Voici ce qui manque encore à votre marque. Chaque partie s'ouvre sur une explication, et vous pouvez demander un vrai exemple construit à partir de votre marque.",
                 "هذه الأجزاء التي لا تملكها علامتكم بعد. كل جزء يُفتح على شرح، ويمكنكم طلب عيّنة حقيقية واحدة مبنية من علامتكم.",
                 "Это части, которых у вашего бренда пока нет. Каждая раскрывается в объяснение, и можно попросить один настоящий образец из вашего бренда."),
    "cost": _five("דוגמה אחת לחלק הזה, מצוירת מהמותג שלכם. עלות: {n} קרדיטים.", "One sample for this part, drawn from your brand. Cost: {n} credits.",
                  "Un exemple pour cette partie, dessiné à partir de votre marque. Coût : {n} crédits.", "عيّنة واحدة لهذا الجزء، مرسومة من علامتكم. التكلفة: {n} رصيد.",
                  "Один образец для этой части, нарисованный из вашего бренда. Стоимость: {n} кредитов."),
    "used": _five("הדוגמה לחלק הזה כבר הופקה ({date}). החלק המלא הוא חלק מחבילת המיתוג.",
                  "The sample for this part was already made ({date}). The full part belongs to the branding package.",
                  "L'exemple de cette partie a déjà été réalisé ({date}). La partie complète fait partie du forfait branding.",
                  "أُنتجت عيّنة هذا الجزء من قبل ({date}). الجزء الكامل ضمن باقة الهوية.",
                  "Образец для этой части уже сделан ({date}). Полная часть входит в пакет брендинга."),
    "asked": _five("השאלה שלכם הועברה לג'וני, והוא יחזור אליכם.", "Your question was passed to Johnny, and he will get back to you.",
                   "Votre question a été transmise à Johnny, qui reviendra vers vous.", "أُرسل سؤالكم إلى جوني وسيعود إليكم.",
                   "Ваш вопрос передан Джонни, он свяжется с вами."),
    # the owner's name is said by the SERVER, never kept in the page
    "ask": _five("לשאול את ג'וני על זה", "Ask Johnny about it", "Poser la question à Johnny", "اسألوا جوني عن ذلك", "Спросить об этом Джонни"),
    "asked_label": _five("השאלה הועברה לג'וני", "Your question was passed to Johnny", "Question transmise à Johnny",
                         "أُرسل السؤال إلى جوني", "Вопрос передан Джонни"),
    "cap_typography": _five("הטקסט שלכם בזוג גופנים מוצע: {head} לכותרת, {body} לטקסט.", "Your copy in a proposed pair: {head} for headlines, {body} for text.",
                            "Votre texte dans un duo proposé : {head} pour les titres, {body} pour le texte.", "نصكم بزوج خطوط مقترح: {head} للعناوين و{body} للنص.",
                            "Ваш текст в предложенной паре: {head} для заголовков, {body} для текста."),
    "cap_visual_language": _five("שלוש דוגמאות (פטרנים) שנבנו מצבעי המותג שלכם.", "Three patterns built from your brand colours.",
                                 "Trois motifs construits à partir de vos couleurs.", "ثلاثة أنماط مبنية من ألوان علامتكم.",
                                 "Три паттерна, построенные из цветов вашего бренда."),
    "cap_templates": _five("אותו פוסט שלכם בשלושה פורמטים: פיד, ריבוע וסטורי.", "The same post of yours in three formats: feed, square and story.",
                           "Le même post en trois formats : fil, carré et story.", "منشوركم نفسه بثلاث صيغ: الخلاصة والمربع والستوري.",
                           "Один и тот же ваш пост в трёх форматах: лента, квадрат и сторис."),
    "cap_brand_book": _five("עמוד אחד מתוך מסמך מותג: הצבעים והגופנים שלכם כפי שהם מוגדרים היום.", "One page of a brand document: your colours and typefaces as they are defined today.",
                            "Une page d'un document de marque : vos couleurs et polices telles que définies aujourd'hui.", "صفحة واحدة من وثيقة العلامة: ألوانكم وخطوطكم كما هي معرّفة اليوم.",
                            "Одна страница бренд-документа: ваши цвета и шрифты в нынешнем виде."),
}

ERRORS = {
    "bad_section": _five("החלק הזה לא קיים", "This part does not exist", "Cette partie n'existe pas", "هذا الجزء غير موجود", "Такой части нет"),
    "confirm_needed": _five("צריך לאשר את העלות לפני שמפיקים דוגמה", "The cost must be confirmed before a sample is made",
                            "Le coût doit être confirmé avant de réaliser un exemple", "يجب تأكيد التكلفة قبل إنتاج عيّنة",
                            "Перед созданием образца нужно подтвердить стоимость"),
    "sample_used": _five("הדוגמה לחלק הזה כבר הופקה — יש דוגמה אחת לכל חלק", "The sample for this part was already made — there is one per part",
                         "L'exemple de cette partie existe déjà — un seul par partie", "أُنتجت عيّنة هذا الجزء من قبل — عيّنة واحدة لكل جزء",
                         "Образец для этой части уже сделан — один на часть"),
    "sample_unavailable": _five("לא הצלחנו להפיק את הדוגמה כרגע. לא נרשם דבר, ואפשר לנסות שוב.", "We couldn't make the sample right now. Nothing was recorded, and you can try again.",
                                "Impossible de réaliser l'exemple pour l'instant. Rien n'a été enregistré, vous pouvez réessayer.", "تعذّر إنتاج العيّنة الآن. لم يُسجَّل شيء ويمكنكم المحاولة مجددًا.",
                                "Сейчас не удалось сделать образец. Ничего не записано, можно попробовать снова."),
    "not_found": _five("עוד אין דוגמה לחלק הזה", "There is no sample for this part yet", "Pas encore d'exemple pour cette partie", "لا توجد عيّنة لهذا الجزء بعد",
                       "Для этой части образца пока нет"),
}


class PackageError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def error_text(code: str, lang: str) -> str:
    return pick(ERRORS.get(code) or ERRORS["sample_unavailable"], lang)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _alert(lines: list):
    try:
        from agents.master_agent import alert
        alert(SERVICE_NAME, lines)
    except Exception as e:
        print(f"[{SERVICE_NAME}] alert failed ({type(e).__name__})")


def state(client_id: int) -> dict:
    value = brand_brief.page_state(client_id).get("package")
    return dict(value) if isinstance(value, dict) else {}


def _save(client_id: int, section: str, **fields):
    value = state(client_id)
    value[section] = {**(value.get(section) or {}), **fields}
    brand_brief.set_page_state(client_id, package=value)


def _check(section: str) -> str:
    if section not in SECTIONS:
        raise PackageError("bad_section")
    return section


# ─── The page ────────────────────────────────────────────────────────────────

def view(client_id: int, lang: str = "he") -> dict:
    """{"title", "sub", "sections": [{"id", "title", "line", "includes",
    "asked", "sample": {"state": "available" | "made", "credits",
    "cost_text" | ("at", "url", "caption", "used_text")}}]}. Never raises."""
    try:
        stored = state(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: package state unreadable ({type(e).__name__})")
        stored = {}
    rows = []
    for sid in SECTION_IDS:
        spec, mine = SECTIONS[sid], stored.get(sid) or {}
        if mine.get("sample_at"):
            snap = mine.get("snapshot") or {}
            fonts = snap.get("fonts") or {}
            sample = {"state": "made", "at": mine["sample_at"][:10], "credits": int(mine.get("credits") or 0),
                      "url": f"/api/client/brand-identity/package/{sid}/sample?at={mine['sample_at'][:19]}",
                      "caption": pick(TEXT["cap_" + sid], lang).replace("{head}", fonts.get("headline") or "—")
                      .replace("{body}", fonts.get("body") or "—"),
                      "used_text": pick(TEXT["used"], lang).replace("{date}", mine["sample_at"][:10])}
        else:
            sample = {"state": "available", "credits": SAMPLE_CREDITS[sid],
                      "cost_text": pick(TEXT["cost"], lang).replace("{n}", str(SAMPLE_CREDITS[sid]))}
        rows.append({"id": sid, "title": pick(spec["title"], lang), "line": pick(spec["line"], lang),
                     "includes": [pick(i, lang) for i in spec["includes"]], "asked": bool(mine.get("asked_at")),
                     "sample": sample})
    return {"title": pick(TEXT["title"], lang), "sub": pick(TEXT["sub"], lang), "sections": rows,
            "ask_label": pick(TEXT["ask"], lang), "asked_label": pick(TEXT["asked_label"], lang)}


# ─── Ask Johnny ──────────────────────────────────────────────────────────────

def ask(client_id: int, section: str, lang: str = "he") -> dict:
    """The client asks about one section: a tracked team request (a person
    answers; no price from code) + the buying-signal alert. {"message"}."""
    _check(section)
    title = pick(SECTIONS[section]["title"], "en")
    opened = {}
    try:
        from core import agent_actions
        opened = agent_actions.open_team_request(
            client_id, "media", "package_exception",
            f"Branding page: the client asks about the branding package — {title}", origin="brand_page")
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: team request not opened ({type(e).__name__})")
    if not opened or opened.get("error") or opened.get("already"):
        # no new request row carried it (storage down, or one is already open): the owner still hears of THIS section
        _alert([f"client {client_id}: asked about the branding package — {title} (branding page, buying signal)"])
    try:
        _save(client_id, section, asked_at=_now())
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: question not recorded ({type(e).__name__})")
    return {"message": pick(TEXT["asked"], lang)}


# ─── The one sample per section ──────────────────────────────────────────────

def _real_copy(client_id: int) -> tuple:
    """(headline, body) of the client's OWN latest post (its first slide) —
    a sample never carries copy we invented. No post yet: the font-sample
    line the page already uses, and no body."""
    try:
        from core import social_queue
        items = sorted(social_queue._load(client_id), key=lambda i: i.get("created_at", ""), reverse=True)
        for item in items:
            slides = ((item.get("design") or {}).get("slides") or (item.get("post") or {}).get("slides") or [])
            for s in slides:
                if str(s.get("copy_headline") or "").strip():
                    return str(s["copy_headline"]).strip(), str(s.get("copy_body") or "").strip()
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: no real copy read ({type(e).__name__})")
    from core import brand_fonts
    return brand_fonts.SAMPLE["hebrew"], ""


def _pairing(client_id: int, chosen: dict) -> dict:
    """A proposed headline + body pair from the installed library — a
    character face with a bold for headlines, a sans with several weights for
    text — never the families the client already uses. {} when the library
    has none to offer."""
    from core import font_library
    used = {font_library.norm(v) for v in (chosen or {}).values() if v}
    rows = [f for f in font_library.catalog() if f["installed"] and font_library.norm(f["family"]) not in used]
    heads = [f for f in rows if f["has_bold"] and f["category"] in ("display", "serif")]
    bodies = [f for f in rows if f["category"] == "sans" and len(f["weights"]) >= 2]
    if not heads or not bodies:
        return {}
    return {"headline": heads[int(client_id) % len(heads)]["family"], "body": bodies[int(client_id) % len(bodies)]["family"],
            "hebrew": ""}


def snapshot(client_id: int, section: str) -> dict:
    """The brand as it stands NOW, frozen for one sample: palette variant +
    its role colours, the fonts the sample is drawn with, the client's own copy."""
    from core import brand_fonts, brand_page, slide_spec
    cur = brand_brief.current(client_id)
    variants = brand_page._variants(client_id, cur, "en")
    first = variants[0] if variants else {"label": "", "segment": ""}
    label = slide_spec.variant_for({"label": first["label"]} if first["label"] else {})
    fonts = dict(brand_fonts.wanted(client_id))
    if section == "typography":
        fonts = _pairing(client_id, fonts)
        if not fonts:
            raise PackageError("sample_unavailable")
    head, body = _real_copy(client_id)
    return {"variant": first["label"], "segment": first.get("segment") or "b2c",
            "roles": slide_spec.palette_roles(client_id, label), "fonts": fonts, "copy": [head, body]}


def _fit(text: str, budget: dict) -> str:
    """The client's copy cut at a word to what this template holds in these
    fonts — a sample must not fail on length."""
    if not budget or not text:
        return text
    out, limit = [], int(budget["max_chars"] * 0.85)
    for w in text.split()[:budget.get("max_words") or 10 ** 6]:
        if len(" ".join(out + [w])) > limit:
            break
        out.append(w)
    return " ".join(out) or text.split()[0]


def _slide(client_id: int, snap: dict, aspect: str = ""):
    from PIL import Image
    from core import brand_fonts, copy_budget, render_manifest, slide_renderer, slide_spec
    fonts = brand_fonts.resolve(client_id, snap.get("fonts") or {})
    spec = slide_spec.starter(snap.get("segment") or "b2c", slide_spec.variant_for({"label": snap["variant"]} if snap.get("variant") else {}), aspect)
    budget = copy_budget.spec_budget(spec, fonts=fonts)
    head, body = snap["copy"]
    with render_manifest.tracing(client_id, "", "brand_package_sample"):
        png = slide_renderer.render_slide(spec, snap["roles"], _fit(head, budget.get("copy_headline")),
                                          _fit(body, budget.get("copy_body")), 1, 1, "", fonts=fonts)
    return Image.open(io.BytesIO(png)).convert("RGB")


def _sheet(images: list, height: int = 760, gap: int = 36):
    from PIL import Image
    scaled = [im.resize((max(1, round(im.width * height / im.height)), height), Image.LANCZOS) for im in images]
    sheet = Image.new("RGB", (sum(i.width for i in scaled) + gap * (len(scaled) + 1), height + 2 * gap), SHEET_BG)
    x = gap
    for im in scaled:
        sheet.paste(im, (x, gap))
        x += im.width + gap
    return sheet


def _patterns(roles: dict, tile: int = 520):
    """Three pattern tiles from the brand's own colours — shapes only, no text."""
    from PIL import Image, ImageDraw
    bg, fg, accent, muted = (roles[r] for r in ("bg", "fg", "accent", "muted"))
    tiles = []
    dots = Image.new("RGB", (tile, tile), bg)
    d = ImageDraw.Draw(dots)
    for row, y in enumerate(range(26, tile, 52)):
        for x in range(26 + (26 if row % 2 else 0), tile, 52):
            d.ellipse((x - 9, y - 9, x + 9, y + 9), fill=accent if (x // 52 + row) % 4 else muted)
    tiles.append(dots)
    stripes = Image.new("RGB", (tile, tile), accent)
    d = ImageDraw.Draw(stripes)
    for x in range(-tile, tile * 2, 64):
        d.polygon([(x, 0), (x + 26, 0), (x + 26 + tile, tile), (x + tile, tile)], fill=bg)
    tiles.append(stripes)
    tri = Image.new("RGB", (tile, tile), bg)
    d = ImageDraw.Draw(tri)
    step = 104
    for row, y in enumerate(range(0, tile, step)):
        for col, x in enumerate(range(0, tile, step)):
            colour = (accent, muted, fg)[(row + col) % 3]
            d.polygon([(x, y + step), (x + step // 2, y), (x + step, y + step)] if (row + col) % 2
                      else [(x, y), (x + step, y), (x + step // 2, y + step)], fill=colour)
    tiles.append(tri)
    return tiles


def _book(client_id: int, snap: dict):
    """One page of a brand document: the four role colours with their values
    and the brand's typefaces, each drawn by its own face."""
    from PIL import Image, ImageDraw
    from core import brand_fonts, slide_renderer
    roles, fonts = snap["roles"], brand_fonts.resolve(client_id, snap.get("fonts") or {})
    width, pad, sw = 1600, 60, 340
    page = Image.new("RGB", (width, 1000), "#FFFFFF")
    d = ImageDraw.Draw(page)
    d.rectangle((0, 0, width, 240), fill=roles["bg"])
    d.rectangle((pad, 180, pad + 220, 196), fill=roles["accent"])
    from core import font_library
    bundled = {"headline": font_library.face("Rubik"), "body": font_library.face("Assistant")}

    def face_of(slot):
        # the brand's own font for the slot; no brand font = the bundled face posts are drawn with today
        chain = (fonts.get("chains") or {}).get(slot) or []
        return chain[0] if chain else bundled[slot]
    label_face = bundled["body"]                                   # the colour values: Latin digits, the bundled face
    for i, role in enumerate(("bg", "fg", "accent", "muted")):
        x = pad + i * (sw + 40)
        d.rectangle((x, 300, x + sw, 300 + 200), fill=roles[role], outline="#CCCCCC")
        strip = slide_renderer.render_sample(label_face, roles[role], weight=400, size=34, width=sw, height=64, pad=4)
        page.paste(Image.open(io.BytesIO(strip)).convert("RGB"), (x, 512))
    head, _body = snap["copy"]
    y = 640
    for slot, weight in (("headline", 700), ("body", 400)):
        face = face_of(slot)
        text = head if getattr(face, "hebrew", True) else brand_fonts.SAMPLE["latin"]
        try:
            strip = slide_renderer.render_sample(face, " ".join(text.split()[:6]), weight=weight, size=60, width=width - 2 * pad, height=140, pad=8)
        except slide_renderer.RenderError:
            strip = slide_renderer.render_sample(bundled[slot], " ".join(text.split()[:6]), weight=weight, size=60,
                                                 width=width - 2 * pad, height=140, pad=8)
        page.paste(Image.open(io.BytesIO(strip)).convert("RGB"), (pad, y))
        y += 160
    return page


def render(client_id: int, section: str, snap: dict) -> bytes:
    """The sample's PNG, drawn from its snapshot — in code, never a model."""
    _check(section)
    if section == "typography":
        image = _sheet([_slide(client_id, snap)], height=900)
    elif section == "templates":
        from core import social_formats
        image = _sheet([_slide(client_id, snap, a) for a in social_formats.SLIDE_ASPECTS])   # feed, square, story
    elif section == "visual_language":
        image = _sheet(_patterns(snap["roles"]), height=520)
    else:
        image = _book(client_id, snap)
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


_PNG = {}          # (client_id, section, sample_at) -> bytes; rebuilt from the snapshot after a restart


def request_sample(client_id: int, section: str, confirmed: bool, lang: str = "he") -> dict:
    """ONE sample for this section, on the client's explicit, confirmed
    request. PackageError: bad_section | confirm_needed (the cost was not
    confirmed) | sample_used (already made — the section is locked again) |
    sample_unavailable (could not be drawn: NOTHING is recorded or charged).
    Every request is logged for the owner."""
    _check(section)
    title = pick(SECTIONS[section]["title"], "en")
    if (state(client_id).get(section) or {}).get("sample_at"):
        _alert([f"client {client_id}: asked for ANOTHER branding-package sample — {title} — refused, one per section "
                f"(buying signal)"])
        raise PackageError("sample_used")
    if not confirmed:
        raise PackageError("confirm_needed")
    try:
        snap = snapshot(client_id, section)
        png = render(client_id, section, snap)
    except PackageError:
        raise
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: sample {section} not drawn ({type(e).__name__}: {str(e)[:160]})")
        _alert([f"client {client_id}: asked for a branding-package sample — {title} — and it could NOT be drawn "
                f"({type(e).__name__}); nothing was recorded (buying signal)"])
        raise PackageError("sample_unavailable")
    at = _now()
    _save(client_id, section, sample_at=at, snapshot=snap, credits=SAMPLE_CREDITS[section])
    _PNG[(int(client_id), section, at)] = png
    _alert([f"client {client_id}: requested the branding-package sample — {title} — "
            f"{SAMPLE_CREDITS[section]} credits, drawn from their brand (buying signal)"])
    return {"section": section, "at": at}


def sample_png(client_id: int, section: str) -> bytes:
    """The section's sample as it was made (PackageError not_found when none)."""
    _check(section)
    mine = state(client_id).get(section) or {}
    if not mine.get("sample_at") or not isinstance(mine.get("snapshot"), dict):
        raise PackageError("not_found")
    key = (int(client_id), section, mine["sample_at"])
    if key not in _PNG:
        try:
            _PNG[key] = render(client_id, section, mine["snapshot"])
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: sample {section} not redrawn ({type(e).__name__})")
            raise PackageError("not_found")
        if len(_PNG) > 40:
            for old in list(_PNG)[:20]:
                _PNG.pop(old, None)
    return _PNG[key]
