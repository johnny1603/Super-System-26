"""The client's brand fonts: chosen, uploaded, and RESOLVED for the renderer
(2026-11-26, docs/FONTS.md).

Before this module a client's typeface reached the agents as words only and
every post was drawn in Rubik and Assistant. Now the names in the brand
brief (`visual_identity.typefaces: headline: X; body: Y; hebrew: Z`, written
by the branding page — core/brand_page) resolve to real font files:

  resolve(client_id)   what the slide renderer draws with — per slot
                       (headline / body) a CHAIN of faces: the brand font,
                       then the client's Hebrew pairing. The renderer adds the
                       spec's own family and the bundled last resort, and
                       falls back per text RUN (core/slide_renderer._Type).
                       A name that is neither in the library nor uploaded is
                       an ISSUE the manifest carries to the card — never a
                       silent substitution. `signature` changes whenever the
                       fonts do: core/copy_budget keys its measurements on it.

  A CLIENT'S OWN FONT (ttf / otf / woff / woff2):
  add_upload()         inspected with fonttools and reported in plain words
                       BEFORE anything depends on it: the Hebrew alphabet
                       (the 22 letters + the five final forms) — a font
                       without it is kept for LATIN text only and never
                       becomes the brand font on its own; a variable font is
                       instanced to real static weights; the weight set
                       (regular + bold) and what is missing; the rights
                       confirmation, logged with its date (a log, not legal
                       advice and not a gate — knowledge/client-governance.md §7).
                       woff / woff2 are converted. The static files live in
                       the client's own Drive (the brand folder) — never on
                       the instance's disk, which is wiped on every deploy —
                       and are fetched into a local cache when a render needs
                       them. The record (weights, file ids, coverage, the
                       rights log) is kept on the brand brief's own row
                       (brand_brief.page_state), like the rest of that page.

  sample_png() / preview_png()   the branding page's pictures, drawn by the
                       SAME renderer that draws posts — never CSS.
"""
import hashlib
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone

from core import brand_brief, drive_storage, font_library

SERVICE_NAME = "brand_fonts"
SLOTS = ("headline", "body")
MAX_MB = 15
MAX_FAMILIES = 8
STOPS = (100, 200, 300, 400, 500, 600, 700, 800, 900)
SNIFF = {b"\x00\x01\x00\x00": "ttf", b"true": "ttf", b"OTTO": "otf", b"wOFF": "woff", b"wOF2": "woff2"}
MIME = {"ttf": "font/ttf", "otf": "font/otf"}
KIND = "brand_font"
SAMPLE = {"hebrew": "המותג שלכם, באותיות שלכם", "latin": "Your brand, in your letters"}
PREVIEW_HEAD = "כך המותג שלכם נראה בפוסט אמיתי אצלנו"
PREVIEW_BODY = ("זו שקופית אמיתית, מצוירת באותה מערכת שמציירת את הפוסטים שלכם: "
                "הגופנים, הצבעים והמידות כאן הם בדיוק מה שיעלה לרשת. Brand 2026.")


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or next(iter(table.values()))


# The upload report and the page's notes — plain words, said at upload time
TEXT = {
    "hebrew_ok": _five("הגופן {family} נקלט. יש בו את כל אותיות העברית, כולל האותיות הסופיות.",
                       "{family} is in. It has the whole Hebrew alphabet, final letters included.",
                       "{family} est enregistrée. Elle contient tout l'alphabet hébreu, lettres finales comprises.",
                       "تم استلام {family}. يحتوي الأبجدية العبرية كاملة مع الحروف النهائية.",
                       "{family} принят. В нём есть весь ивритский алфавит, включая конечные буквы."),
    "hebrew_none": _five("בגופן {family} אין אותיות עבריות. אפשר להשתמש בו רק לטקסט באותיות לטיניות (אנגלית). "
                         "נשמור אותו למילים באנגלית — ואיזה גופן עברי תרצו שיופיע לצידו?",
                         "{family} has no Hebrew letters. It can be used for Latin (English) text only. We will keep "
                         "it for the English words — which Hebrew font should go with it?",
                         "{family} n'a pas de lettres hébraïques. Elle ne sert que pour le texte latin. Nous la gardons "
                         "pour les mots en caractères latins — quelle police hébraïque doit l'accompagner ?",
                         "{family} لا يحتوي حروفًا عبرية. يصلح للنص اللاتيني فقط. سنحتفظ به للكلمات اللاتينية — "
                         "أي خط عبري تريدون أن يرافقه؟",
                         "В {family} нет ивритских букв. Он годится только для латиницы. Оставим его для латинских "
                         "слов — какой ивритский шрифт поставить рядом?"),
    "hebrew_partial": _five("בגופן {family} חסרות אותיות עבריות ({letters}), ולכן אי אפשר לכתוב בו עברית. אפשר להשתמש "
                            "בו רק לטקסט באותיות לטיניות — ואיזה גופן עברי תרצו שיופיע לצידו?",
                            "{family} is missing Hebrew letters ({letters}), so Hebrew cannot be set in it. It can be "
                            "used for Latin text only — which Hebrew font should go with it?",
                            "Il manque des lettres hébraïques à {family} ({letters}) : l'hébreu ne peut pas y être "
                            "composé. Texte latin uniquement — quelle police hébraïque doit l'accompagner ?",
                            "تنقص {family} حروف عبرية ({letters})، فلا يمكن كتابة العبرية به. للنص اللاتيني فقط — "
                            "أي خط عبري تريدون أن يرافقه؟",
                            "В {family} не хватает ивритских букв ({letters}), набирать иврит им нельзя. Только "
                            "латиница — какой ивритский шрифт поставить рядом?"),
    "no_latin": _five("אין בו אותיות לטיניות — מילים באנגלית יצוירו בגופן אחר, ונסמן את זה על הפוסט.",
                      "It has no Latin letters — English words will be drawn in another font, and the post will say so.",
                      "Elle n'a pas de lettres latines — les mots latins seront dans une autre police, et le post le dira.",
                      "لا يحتوي حروفًا لاتينية — الكلمات اللاتينية سترسم بخط آخر وسيُذكر ذلك على المنشور.",
                      "В нём нет латинских букв — латинские слова будут другим шрифтом, и это будет отмечено."),
    "weights_ok": _five("משקלים: {weights}. יש רגיל ויש מודגש.", "Weights: {weights}. Regular and bold are both there.",
                        "Graisses : {weights}. Le normal et le gras sont là.", "الأوزان: {weights}. العادي والعريض موجودان.",
                        "Начертания: {weights}. Есть и обычное, и жирное."),
    "one_weight": _five("יש בו משקל אחד בלבד ({weights}). אפשר לעבוד איתו, אבל כותרות וטקסט ייראו באותו עובי — "
                        "אנחנו לא מעבים גופן באופן מלאכותי. כדי לקבל הדגשה, העלו גם את קובץ ה-Bold של אותו גופן.",
                        "It has a single weight ({weights}). It is workable, but headlines and text will be the same "
                        "thickness — we never thicken a font artificially. To get emphasis, upload the same font's Bold file too.",
                        "Une seule graisse ({weights}). C'est utilisable, mais titres et texte auront la même épaisseur — "
                        "nous n'épaississons jamais une police artificiellement. Téléversez aussi le fichier Bold.",
                        "وزن واحد فقط ({weights}). يمكن العمل به، لكن العناوين والنص سيكونان بالسماكة نفسها — لا نُسمّك "
                        "الخط اصطناعيًا. ارفعوا أيضًا ملف Bold للخط نفسه.",
                        "Только одно начертание ({weights}). Работать можно, но заголовки и текст будут одной толщины — "
                        "мы не утолщаем шрифт искусственно. Загрузите ещё и файл Bold."),
    "no_bold": _five("משקלים: {weights}. אין משקל מודגש (700 ומעלה): כותרות יצוירו במשקל הכבד ביותר שיש, {heaviest}. "
                     "אנחנו לא מעבים גופן באופן מלאכותי.",
                     "Weights: {weights}. There is no bold (700 or heavier): headlines will be drawn in the heaviest "
                     "one there is, {heaviest}. We never thicken a font artificially.",
                     "Graisses : {weights}. Pas de gras (700 ou plus) : les titres seront dans la plus épaisse, "
                     "{heaviest}. Nous n'épaississons jamais une police artificiellement.",
                     "الأوزان: {weights}. لا يوجد وزن عريض (700 فأكثر): العناوين سترسم بأثقل وزن موجود، {heaviest}. "
                     "لا نُسمّك الخط اصطناعيًا.",
                     "Начертания: {weights}. Жирного (700 и выше) нет: заголовки будут самым плотным из имеющихся, "
                     "{heaviest}. Мы не утолщаем шрифт искусственно."),
    "no_regular": _five("משקלים: {weights}. אין משקל רגיל (400–500): טקסט רץ יצויר במשקל הקרוב ביותר, {nearest}.",
                        "Weights: {weights}. There is no regular (400–500): body text will be drawn in the nearest one, {nearest}.",
                        "Graisses : {weights}. Pas de normal (400–500) : le texte courant sera dans la plus proche, {nearest}.",
                        "الأوزان: {weights}. لا يوجد وزن عادي (400–500): النص سيرسم بأقرب وزن، {nearest}.",
                        "Начертания: {weights}. Обычного (400–500) нет: основной текст будет ближайшим, {nearest}."),
    "variable": _five("זה גופן משתנה (variable). הפקנו ממנו קובץ נפרד לכל משקל ({weights}), כדי שכל משקל ייראה בדיוק "
                      "כמו שתוכנן ולא רק ברירת המחדל שלו.",
                      "This is a variable font. We made a separate file for each weight ({weights}), so every weight "
                      "looks exactly as designed, not only its default.",
                      "C'est une police variable. Nous avons créé un fichier par graisse ({weights}) pour que chacune "
                      "soit exactement comme prévu, pas seulement la valeur par défaut.",
                      "هذا خط متغيّر. أنشأنا ملفًا منفصلًا لكل وزن ({weights}) ليظهر كل وزن كما صُمّم تمامًا.",
                      "Это вариативный шрифт. Мы сделали отдельный файл для каждого начертания ({weights}), чтобы "
                      "каждое выглядело как задумано, а не только начертание по умолчанию."),
    "variable_default": _five("זה גופן משתנה (variable), אבל לא הצלחנו להפיק ממנו משקלים נפרדים. הוא יצויר רק במשקל "
                              "ברירת המחדל שלו ({weights}). אם יש לכם קבצים נפרדים של Regular ו-Bold — העלו אותם.",
                              "This is a variable font, but we could not split it into separate weights. It will be "
                              "drawn in its default weight only ({weights}). If you have separate Regular and Bold files, upload them.",
                              "C'est une police variable, mais nous n'avons pas pu en extraire les graisses. Elle sera "
                              "dessinée dans sa graisse par défaut seulement ({weights}). Téléversez les fichiers Regular et Bold si vous les avez.",
                              "هذا خط متغيّر، لكن تعذّر استخراج أوزان منفصلة منه. سيرسم بوزنه الافتراضي فقط ({weights}). "
                              "إن كانت لديكم ملفات Regular وBold منفصلة فارفعوها.",
                              "Это вариативный шрифт, но разделить его на начертания не удалось. Он будет только в "
                              "начертании по умолчанию ({weights}). Если есть отдельные Regular и Bold — загрузите их."),
    "converted": _five("המרנו את הקובץ מ-{format} לפורמט שהמערכת מציירת איתו.",
                       "We converted the file from {format} to the format the system draws with.",
                       "Nous avons converti le fichier {format} au format utilisé pour le rendu.",
                       "حوّلنا الملف من {format} إلى الصيغة التي يرسم بها النظام.",
                       "Мы преобразовали файл из {format} в формат, которым рисует система."),
    "rights_logged": _five("אישרתם ב-{date} שיש לכם זכות להשתמש בגופן הזה בחומרים מפורסמים. האישור נרשם.",
                           "On {date} you confirmed you have the right to use this font in published material. The confirmation is recorded.",
                           "Le {date}, vous avez confirmé avoir le droit d'utiliser cette police dans des contenus publiés. C'est noté.",
                           "في {date} أكدتم أن لكم حق استخدام هذا الخط في مواد منشورة. تم تسجيل التأكيد.",
                           "{date} вы подтвердили право использовать этот шрифт в публикуемых материалах. Подтверждение записано."),
    "rights_ask": _five("נשאר דבר אחד: לאשר שיש לכם זכות להשתמש בגופן הזה בחומרים שמתפרסמים. זה רישום בלבד, לא ייעוץ משפטי.",
                        "One thing left: confirm that you have the right to use this font in published material. It is a record only, not legal advice.",
                        "Reste une chose : confirmer que vous avez le droit d'utiliser cette police dans des contenus publiés. Simple enregistrement, pas un avis juridique.",
                        "بقي أمر واحد: تأكيد أن لكم حق استخدام هذا الخط في مواد منشورة. مجرد تسجيل وليس استشارة قانونية.",
                        "Осталось одно: подтвердить право использовать этот шрифт в публикуемых материалах. Это только запись, не юридическая консультация."),
    "lib_no_bold": _five("לגופן {family} אין משקל מודגש — הכותרות יצוירו במשקל {heaviest}, ואנחנו לא מעבים גופן באופן "
                         "מלאכותי. הגופן הקרוב אליו ביותר שכן יש לו מודגש: {suggest}.",
                         "{family} has no bold — headlines will be drawn at {heaviest}, and we never thicken a font "
                         "artificially. The closest family that does have a bold: {suggest}.",
                         "{family} n'a pas de gras — les titres seront en {heaviest}, et nous n'épaississons jamais une "
                         "police artificiellement. La famille la plus proche qui a un gras : {suggest}.",
                         "{family} بلا وزن عريض — العناوين سترسم بوزن {heaviest}، ولا نُسمّك الخط اصطناعيًا. أقرب خط له وزن عريض: {suggest}.",
                         "У {family} нет жирного — заголовки будут в {heaviest}, а искусственно мы шрифт не утолщаем. "
                         "Ближайший шрифт с жирным начертанием: {suggest}."),
    "lib_no_bold_alone": _five("לגופן {family} אין משקל מודגש — הכותרות יצוירו במשקל {heaviest}. אנחנו לא מעבים גופן באופן מלאכותי.",
                               "{family} has no bold — headlines will be drawn at {heaviest}. We never thicken a font artificially.",
                               "{family} n'a pas de gras — les titres seront en {heaviest}. Nous n'épaississons jamais une police artificiellement.",
                               "{family} بلا وزن عريض — العناوين سترسم بوزن {heaviest}. لا نُسمّك الخط اصطناعيًا.",
                               "У {family} нет жирного — заголовки будут в {heaviest}. Искусственно мы шрифт не утолщаем."),
    "unknown_family": _five("אין לנו גופן בשם \"{family}\". עד שתבחרו אותו מהספרייה או תעלו את הקובץ שלו, הפוסטים יצוירו "
                            "בגופן אחר — וזה יסומן על כל פוסט.",
                            "We have no font called \"{family}\". Until you choose it from the library or upload its "
                            "file, posts are drawn in another font — and every post will say so.",
                            "Nous n'avons pas de police « {family} ». Tant qu'elle n'est pas choisie dans la bibliothèque "
                            "ou téléversée, les posts sont dans une autre police — et chaque post le dira.",
                            "لا يوجد لدينا خط باسم \"{family}\". إلى أن تختاروه من المكتبة أو ترفعوا ملفه، سترسم المنشورات بخط آخر — وسيُذكر ذلك على كل منشور.",
                            "У нас нет шрифта «{family}». Пока вы не выберете его в библиотеке или не загрузите файл, "
                            "посты рисуются другим шрифтом — и это отмечается на каждом посте."),
    "not_installed": _five("הגופן {family} בספרייה שלנו, אבל הקבצים שלו לא מותקנים כרגע בשרת. עד שזה יתוקן הפוסטים "
                           "יצוירו בגופן אחר, וזה יסומן.",
                           "{family} is in our library, but its files are not installed on the server right now. Until "
                           "that is fixed posts are drawn in another font, and they say so.",
                           "{family} est dans notre bibliothèque, mais ses fichiers ne sont pas installés pour l'instant. "
                           "En attendant, les posts sont dans une autre police, et c'est indiqué.",
                           "{family} في مكتبتنا لكن ملفاته غير مثبتة على الخادم الآن. إلى أن يُصلح ذلك سترسم المنشورات بخط آخر مع الإشارة لذلك.",
                           "{family} есть в библиотеке, но его файлы сейчас не установлены на сервере. Пока это не "
                           "исправят, посты рисуются другим шрифтом, с пометкой."),
    "needs_pairing": _five("ב{family} אין עברית. בחרו גופן עברי שיופיע לצידו — בלי זה העברית תצויר בגופן ברירת המחדל.",
                           "{family} has no Hebrew. Choose a Hebrew font to go with it — without one, Hebrew is drawn in the default font.",
                           "{family} n'a pas d'hébreu. Choisissez une police hébraïque pour l'accompagner — sinon l'hébreu sera dans la police par défaut.",
                           "{family} بلا عبرية. اختاروا خطًا عبريًا يرافقه — وإلا رُسمت العبرية بالخط الافتراضي.",
                           "В {family} нет иврита. Выберите парный ивритский шрифт — иначе иврит будет шрифтом по умолчанию."),
}
ERRORS = {
    "bad_font": _five("אפשר להעלות קובץ גופן מסוג TTF, OTF, WOFF או WOFF2", "You can upload a TTF, OTF, WOFF or WOFF2 font file",
                      "Formats acceptés : TTF, OTF, WOFF ou WOFF2", "يمكن رفع ملف خط TTF أو OTF أو WOFF أو WOFF2",
                      "Можно загрузить файл шрифта TTF, OTF, WOFF или WOFF2"),
    "font_unreadable": _five("לא הצלחנו לקרוא את קובץ הגופן — ייתכן שהוא פגום. נסו לייצא אותו מחדש או להעלות קובץ אחר.",
                             "We could not read the font file — it may be damaged. Try exporting it again or uploading another file.",
                             "Fichier de police illisible — il est peut-être endommagé. Réexportez-le ou téléversez-en un autre.",
                             "تعذّرت قراءة ملف الخط — قد يكون تالفًا. جرّبوا تصديره من جديد أو رفع ملف آخر.",
                             "Не удалось прочитать файл шрифта — возможно, он повреждён. Экспортируйте заново или загрузите другой."),
    "font_tools": _five("בדיקת הגופן לא זמינה כרגע — נסו שוב בעוד רגע", "The font check is unavailable right now — try again in a moment",
                        "La vérification de police est indisponible — réessayez dans un instant",
                        "فحص الخط غير متاح الآن — حاولوا بعد قليل", "Проверка шрифта сейчас недоступна — попробуйте чуть позже"),
    "too_many_fonts": _five(f"יש כבר {MAX_FAMILIES} גופנים משלכם — מחקו אחד כדי להוסיף",
                            f"There are already {MAX_FAMILIES} fonts of yours — delete one to add",
                            f"Il y a déjà {MAX_FAMILIES} polices — supprimez-en une", f"لديكم {MAX_FAMILIES} خطوط — احذفوا واحدًا للإضافة",
                            f"Уже {MAX_FAMILIES} своих шрифтов — удалите один, чтобы добавить"),
    "needs_pairing": _five("בגופן שבחרתם אין עברית — בחרו גם גופן עברי שיופיע לצידו",
                           "The font you chose has no Hebrew — choose a Hebrew font to go with it",
                           "La police choisie n'a pas d'hébreu — choisissez aussi une police hébraïque",
                           "الخط المختار بلا عبرية — اختاروا خطًا عبريًا يرافقه",
                           "В выбранном шрифте нет иврита — выберите и парный ивритский шрифт"),
    "pairing_no_hebrew": _five("הגופן שבחרתם לעברית לא מכיל עברית — בחרו גופן עברי אחר",
                               "The font chosen for Hebrew has no Hebrew — choose another Hebrew font",
                               "La police choisie pour l'hébreu n'en contient pas — choisissez-en une autre",
                               "الخط المختار للعبرية لا يحتوي عبرية — اختاروا خطًا عبريًا آخر",
                               "В шрифте, выбранном для иврита, иврита нет — выберите другой"),
}


class FontError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(line: str):
    print(f"[{SERVICE_NAME}] {line}")


def slug(family: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(family or "").lower()).strip("-")[:48] or hashlib.sha1(
        str(family).encode("utf-8")).hexdigest()[:12]


# ─── What the brief asks for ─────────────────────────────────────────────────

_TYPEFACES_RE = re.compile(r"^headline:\s*(.*?);\s*body:\s*(.*?)(?:;\s*hebrew:\s*(.*))?$")


def parse_typefaces(raw: str) -> dict:
    """The brief's `typefaces` value → {"headline", "body", "hebrew", "note"}.
    Anything not in the page's own shape is a NOTE (words for the agents —
    for example the intake agent's observation), never a font choice."""
    m = _TYPEFACES_RE.match(str(raw or "").strip())
    clean = lambda s: "" if str(s or "").strip() in ("—", "-", "") else str(s).strip()   # noqa: E731
    if not m:
        return {"headline": "", "body": "", "hebrew": "", "note": str(raw or "").strip()}
    return {"headline": clean(m.group(1)), "body": clean(m.group(2)), "hebrew": clean(m.group(3)), "note": ""}


def wanted(client_id: int, body: str = None) -> dict:
    """{"headline", "body", "hebrew"} — the family names the client's brief states."""
    from core import brand_page
    if body is None:
        body = brand_brief.current(client_id).get("text", "")
    got = parse_typefaces(brand_page.read_field(body, "visual_identity", "typefaces"))
    return {k: got[k] for k in ("headline", "body", "hebrew")}


# ─── The client's own fonts: the record ──────────────────────────────────────

def uploads(client_id: int) -> dict:
    """{font_id: record} — never raises ({} when the row can't be read)."""
    try:
        fonts = brand_brief.page_state(client_id).get("fonts")
    except Exception as e:
        _log(f"client {client_id}: font records unreadable ({type(e).__name__})")
        return {}
    return dict(fonts) if isinstance(fonts, dict) else {}


def _save_uploads(client_id: int, fonts: dict):
    brand_brief.set_page_state(client_id, fonts=fonts)
    invalidate(client_id)


def invalidate(client_id: int):
    """The client's fonts changed: every measurement made with the old ones is void."""
    try:
        from core import copy_budget
        copy_budget.invalidate(client_id)
    except Exception as e:
        _log(f"client {client_id}: copy budget not invalidated ({type(e).__name__})")


def cache_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "uallak-fonts")


@drive_storage.scoped
def _download(client_id: int, file_id: str) -> bytes:
    from core import drive_service as drive
    return drive.download_file(file_id)


def _local_file(client_id: int, entry: dict) -> str:
    """The stored static file on THIS instance's disk: fetched from the
    client's Drive once, checked against its SHA-256, then reused."""
    name = f"{entry['sha256'][:40]}.{entry.get('ext') or 'ttf'}"
    path = os.path.join(cache_dir(), str(int(client_id)), name)
    if os.path.isfile(path):
        return path
    try:
        data = _download(client_id, entry["file_id"])
    except Exception as e:
        raise font_library.FontUnavailable("fetch_failed", f"{type(e).__name__}")
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise font_library.FontUnavailable("fetch_failed", "the stored file is not the one that was checked")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def upload_face(client_id: int, rec: dict):
    files = {int(w): e for w, e in (rec.get("weights") or {}).items()}
    if not files:
        return None
    ident = hashlib.sha1("".join(files[w]["sha256"] for w in sorted(files)).encode()).hexdigest()[:12]
    return font_library.Face(rec["family"], "upload", list(files), rec.get("ranges"),
                             fetch=lambda weight: _local_file(client_id, files[int(weight)]),
                             hebrew=bool(rec.get("hebrew")), latin=bool(rec.get("latin", True)), ident=ident)


def find(client_id: int, family: str, own: dict = None):
    """(Face | None, issue): the client's own upload of that name first, then
    the library. issue "" | "unknown_family" | "not_installed"."""
    own = uploads(client_id) if own is None else own
    for rec in own.values():
        if font_library.norm(rec.get("family")) == font_library.norm(family):
            got = upload_face(client_id, rec)
            if got:
                return got, ""
    got = font_library.face(family)
    if got is None:
        return None, "unknown_family"
    return got, "" if font_library.installed(got.family) else "not_installed"


def resolve(client_id: int, override: dict = None) -> dict:
    """What the renderer draws this client's text with:
    {"wanted": {"headline", "body", "hebrew"}, "chains": {slot: [Face, …]},
     "issues": {slot | "hebrew": reason}, "signature": str}.
    `override` = names to try instead of the brief's (the page's preview of a
    choice not saved yet). Never raises: a brief that cannot be read = no
    brand font (the spec's families), and the log says so."""
    try:
        names = wanted(client_id)
        own = uploads(client_id)
    except Exception as e:
        _log(f"client {client_id}: fonts not resolved ({type(e).__name__}) — the spec's families are used")
        names, own = {"headline": "", "body": "", "hebrew": ""}, {}
    if override:
        names = {**names, **{k: str(v or "").strip() for k, v in override.items() if k in names}}
    chains, issues, ident = {s: [] for s in SLOTS}, {}, []
    pair, pair_issue = find(client_id, names["hebrew"], own) if names["hebrew"] else (None, "")
    if names["hebrew"] and (pair_issue or (pair and not pair.hebrew)):
        issues["hebrew"] = pair_issue or "pairing_no_hebrew"
    for slot in SLOTS:
        if not names[slot]:
            continue
        got, issue = find(client_id, names[slot], own)
        if issue:
            issues[slot] = issue
        if got is not None:
            chains[slot].append(got)
            ident.append(f"{slot}={got.key}")
            names[slot] = got.family
        if pair is not None and pair.hebrew:
            chains[slot].append(pair)
    if pair is not None:
        ident.append(f"hebrew={pair.key}")
        names["hebrew"] = pair.family
    signature = hashlib.sha1(json.dumps([names, ident, sorted(issues.items())], ensure_ascii=False,
                                        sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return {"wanted": names, "chains": chains, "issues": issues, "signature": signature}


def signature(client_id: int) -> str:
    return resolve(client_id)["signature"]


# ─── Inspecting an uploaded file (fonttools) ─────────────────────────────────

def sniff(content: bytes) -> str:
    return SNIFF.get(bytes(content[:4]), "")


def _tools():
    try:
        from fontTools.ttLib import TTFont
        return TTFont
    except ImportError:
        raise FontError("font_tools")


def _ranges(cmap: dict) -> list:
    out = []
    for c in sorted(c for c in cmap if c < 0x10000):
        if out and c == out[-1][1] + 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return out


def _name(font) -> str:
    table = font["name"]
    for name_id in (16, 1, 4):
        value = table.getDebugName(name_id)
        if value and value.strip():
            return re.sub(r"\s+", " ", re.sub(r"[\[\]\n\r#*`;:]", " ", value)).strip()[:60]
    return ""


def _weight_of(font) -> int:
    try:
        raw = int(font["OS/2"].usWeightClass)
    except Exception:
        raw = 400
    return max(100, min(900, int(round(raw / 100.0)) * 100))


def _static_bytes(font) -> tuple:
    """(bytes, ext) of a font saved as a plain sfnt (no WOFF wrapper)."""
    font.flavor = None
    out = io.BytesIO()
    font.save(out)
    return out.getvalue(), "otf" if font.sfntVersion == "OTTO" else "ttf"


def inspect(content: bytes) -> dict:
    """{"family", "format", "hebrew", "missing_hebrew", "latin", "ranges",
    "variable": {"axis": [min, max], "instanced": bool} | None,
    "files": {weight: (bytes, ext)}} — every static file the renderer will
    draw with. FontError("bad_font" | "font_unreadable" | "font_tools")."""
    fmt = sniff(content)
    if not fmt:
        raise FontError("bad_font")
    TTFont = _tools()
    try:
        font = TTFont(io.BytesIO(content))
        cmap = font.getBestCmap() or {}
        family = _name(font)
        if not cmap or not family:
            raise FontError("font_unreadable")
        missing = "".join(ch for ch in font_library.HEBREW_LETTERS if ord(ch) not in cmap)
        latin = all(ord(ch) in cmap for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
        out = {"family": family, "format": fmt, "hebrew": not missing, "missing_hebrew": missing, "latin": latin,
               "ranges": _ranges(cmap), "variable": None, "files": {}}
        axes = {a.axisTag: a for a in font["fvar"].axes} if "fvar" in font else {}
        if axes:
            out["variable"], out["files"] = _instances(TTFont, content, font, axes)
        else:
            out["files"] = {_weight_of(font): _static_bytes(font)}
        return out
    except FontError:
        raise
    except Exception as e:
        _log(f"font not readable ({type(e).__name__}: {str(e)[:120]})")
        raise FontError("font_unreadable")


def _instances(TTFont, content: bytes, font, axes: dict) -> tuple:
    """A variable font → one REAL static file per weight it supports, every
    other axis at its default. If instancing fails the default position is
    kept as the one weight — and the report says so."""
    defaults = {tag: a.defaultValue for tag, a in axes.items()}
    wght = axes.get("wght")
    if wght is None:
        stops = [None]
    else:
        stops = [w for w in STOPS if wght.minValue <= w <= wght.maxValue] or [None]
    info = {"axis": [int(wght.minValue), int(wght.maxValue)] if wght is not None else [], "instanced": True}
    files = {}
    try:
        from fontTools.varLib import instancer
        for stop in stops:
            position = dict(defaults) if stop is None else {**defaults, "wght": stop}
            static = instancer.instantiateVariableFont(TTFont(io.BytesIO(content)), position, inplace=True,
                                                       updateFontNames=False)
            weight = _weight_of(font) if stop is None else stop
            if stop is not None:
                try:
                    static["OS/2"].usWeightClass = stop
                except Exception:
                    pass
            files[weight] = _static_bytes(static)
    except Exception as e:
        _log(f"variable font not instanced ({type(e).__name__}: {str(e)[:120]}) — default position kept")
        info["instanced"] = False
        files = {_weight_of(font): _static_bytes(font)}
    return info, files


# ─── The report, in plain words ──────────────────────────────────────────────

def _weights_text(weights: list) -> str:
    return ", ".join(str(w) for w in sorted(int(w) for w in weights))


def report(rec: dict, lang: str = "he", converted_from: str = "") -> dict:
    """What the client is told about one of their fonts: {"lines": [...],
    "hebrew", "latin_only", "needs_pairing", "has_regular", "has_bold",
    "weights", "rights_confirmed"} — built from the RECORD (what is stored),
    so the page says the same thing on every visit."""
    family = rec.get("family", "")
    weights = sorted(int(w) for w in (rec.get("weights") or {}))
    fill = lambda key, **kw: _fill(pick(TEXT[key], lang), family=family, **kw)     # noqa: E731
    lines = []
    if rec.get("hebrew"):
        lines.append(fill("hebrew_ok"))
    elif rec.get("missing_hebrew") and len(rec["missing_hebrew"]) < len(font_library.HEBREW_LETTERS):
        lines.append(fill("hebrew_partial", letters=" ".join(rec["missing_hebrew"])))
    else:
        lines.append(fill("hebrew_none"))
    if not rec.get("latin", True):
        lines.append(fill("no_latin"))
    variable = rec.get("variable") or {}
    if variable:
        lines.append(fill("variable" if variable.get("instanced") else "variable_default", weights=_weights_text(weights)))
    if converted_from in ("woff", "woff2"):
        lines.append(fill("converted", format=converted_from.upper()))
    has_regular = any(400 <= w <= 500 for w in weights)
    has_bold = any(w >= font_library.BOLD for w in weights)
    if len(weights) == 1:
        lines.append(fill("one_weight", weights=_weights_text(weights)))
    elif not has_bold:
        lines.append(fill("no_bold", weights=_weights_text(weights), heaviest=str(max(weights))))
    elif not has_regular:
        lines.append(fill("no_regular", weights=_weights_text(weights),
                          nearest=str(min(weights, key=lambda w: abs(w - 400)))))
    else:
        lines.append(fill("weights_ok", weights=_weights_text(weights)))
    rights = rec.get("rights") or {}
    lines.append(fill("rights_logged", date=str(rights.get("at") or "")[:10]) if rights.get("confirmed")
                 else fill("rights_ask"))
    return {"lines": lines, "hebrew": bool(rec.get("hebrew")), "latin_only": not rec.get("hebrew"),
            "needs_pairing": not rec.get("hebrew"), "has_regular": has_regular, "has_bold": has_bold,
            "weights": weights, "rights_confirmed": bool(rights.get("confirmed"))}


def _fill(text: str, **fields) -> str:
    for key, value in fields.items():
        text = text.replace("{" + key + "}", str(value))
    return text


# ─── Upload, confirm, delete ─────────────────────────────────────────────────

@drive_storage.scoped
def _store(client_id: int, name: str, content: bytes, ext: str, props: dict) -> dict:
    from core import brand_page, drive_service as drive
    return drive.upload_bytes(brand_page._folder(client_id), name, content, MIME[ext], app_properties=props)


@drive_storage.scoped
def _remove(client_id: int, file_id: str):
    from core import drive_service as drive
    drive.delete_file(file_id)


def add_upload(client_id: int, filename: str, content: bytes, rights: bool = False, lang: str = "he") -> dict:
    """Check, convert and keep ONE uploaded font file. {"font": the page's
    row, "report": report()}. A second file of the same family adds its
    weight to that family (Regular, then Bold). FontError(code) only for what
    cannot be a font at all — never for quality, and never for the rights
    confirmation, which is a log and not a gate."""
    if not content:
        raise FontError("bad_font")
    if len(content) > MAX_MB * 1024 * 1024:
        raise FontError("too_big")
    seen = inspect(content)
    fonts = uploads(client_id)
    font_id = slug(seen["family"])
    rec = dict(fonts.get(font_id) or {})
    if not rec and len(fonts) >= MAX_FAMILIES:
        raise FontError("too_many_fonts")
    now = _now()
    weights = dict(rec.get("weights") or {})
    replaced = []
    try:
        for weight, (data, ext) in sorted(seen["files"].items()):
            digest = hashlib.sha256(data).hexdigest()
            name = f"{re.sub(r'[^A-Za-z0-9]+', '', seen['family']) or font_id}-{weight}.{ext}"
            stored = _store(client_id, name, data, ext, {"uallak_kind": KIND, "font_id": font_id,
                                                         "font_weight": str(weight), "uploaded_at": now})
            if str(weight) in weights:
                replaced.append(weights[str(weight)].get("file_id"))
            weights[str(weight)] = {"file_id": stored.get("id"), "sha256": digest, "ext": ext, "bytes": len(data),
                                    "name": stored.get("name") or name}
    except FontError:
        raise
    except Exception as e:
        _log(f"client {client_id}: font not stored ({type(e).__name__})")
        raise FontError("storage")
    if rec.get("rights", {}).get("confirmed"):
        log = rec["rights"]
    elif rights:
        log = {"confirmed": True, "at": now, "by": "client",
               "statement": "The client confirmed having the right to use this font in published material."}
    else:
        log = {"confirmed": False}
    # coverage of a family = what EVERY one of its files can draw (the first file sets it)
    rec.update(id=font_id, family=rec.get("family") or seen["family"], weights=weights, rights=log,
               hebrew=seen["hebrew"] if not rec else bool(rec.get("hebrew")) and seen["hebrew"],
               missing_hebrew=seen["missing_hebrew"] if not rec else "".join(
                   dict.fromkeys(str(rec.get("missing_hebrew") or "") + seen["missing_hebrew"])),
               latin=seen["latin"] if not rec else bool(rec.get("latin")) and seen["latin"],
               ranges=seen["ranges"] if not rec.get("ranges") else rec["ranges"],
               variable=seen["variable"] or rec.get("variable"),
               uploads=(rec.get("uploads") or [])[-11:] + [{"name": str(filename or "")[:120], "format": seen["format"],
                                                           "at": now, "weights": sorted(seen["files"])}],
               updated_at=now)
    fonts[font_id] = rec
    try:
        _save_uploads(client_id, fonts)
    except Exception as e:
        _log(f"client {client_id}: font record not saved ({type(e).__name__})")
        raise FontError("storage")
    for file_id in replaced:                     # the weight's older file: gone only after the record points away
        try:
            _remove(client_id, file_id)
        except Exception:
            pass
    return {"font": row(rec, lang), "report": report(rec, lang, converted_from=seen["format"])}


def confirm_rights(client_id: int, font_id: str) -> dict:
    """The one confirmation, recorded with its date. Recorded once; a later
    click does not move the date."""
    fonts = uploads(client_id)
    rec = fonts.get(font_id)
    if not rec:
        raise FontError("not_found")
    if not (rec.get("rights") or {}).get("confirmed"):
        rec["rights"] = {"confirmed": True, "at": _now(), "by": "client",
                         "statement": "The client confirmed having the right to use this font in published material."}
        try:
            brand_brief.set_page_state(client_id, fonts=fonts)
        except Exception:
            raise FontError("storage")
    return rec


def delete_upload(client_id: int, font_id: str) -> bool:
    fonts = uploads(client_id)
    rec = fonts.pop(font_id, None)
    if not rec:
        raise FontError("not_found")
    try:
        _save_uploads(client_id, fonts)
    except Exception:
        raise FontError("storage")
    for entry in (rec.get("weights") or {}).values():
        try:
            _remove(client_id, entry.get("file_id"))
        except Exception as e:
            _log(f"client {client_id}: font file not deleted ({type(e).__name__})")
    return True


def row(rec: dict, lang: str = "he") -> dict:
    """One of the client's fonts as the page shows it (no file ids)."""
    return {"id": rec.get("id"), "family": rec.get("family"), **report(rec, lang),
            "variable": bool(rec.get("variable")), "uploaded_at": rec.get("updated_at", ""),
            "rights_at": str((rec.get("rights") or {}).get("at") or "")[:10]}


# ─── The page: picker, notes ─────────────────────────────────────────────────

def notes(client_id: int, names: dict, own: dict, lang: str = "he") -> list:
    """What the page must SAY about the chosen fonts: [{"slot", "code",
    "family", "text", "suggest"}] — an unknown name, a library family with
    no bold (and the nearest family that has one), Hebrew missing with no pairing."""
    out = []
    pair, _ = find(client_id, names.get("hebrew"), own) if names.get("hebrew") else (None, "")
    for slot in SLOTS:
        family = names.get(slot)
        if not family:
            continue
        got, issue = find(client_id, family, own)
        if issue:
            out.append({"slot": slot, "code": issue, "family": family, "suggest": "",
                        "text": _fill(pick(TEXT[issue], lang), family=family)})
            if got is None:
                continue
        if not got.hebrew and not (pair is not None and pair.hebrew):
            out.append({"slot": slot, "code": "needs_pairing", "family": got.family, "suggest": "",
                        "text": _fill(pick(TEXT["needs_pairing"], lang), family=got.family)})
        if slot == "headline" and not got.has_bold and got.source != "upload":
            suggest = font_library.nearest_with_bold(got.family)
            heaviest = str(max(got.weights)) if got.weights else ""
            out.append({"slot": slot, "code": "no_bold", "family": got.family, "suggest": suggest,
                        "text": _fill(pick(TEXT["lib_no_bold" if suggest else "lib_no_bold_alone"], lang),
                                      family=got.family, heaviest=heaviest, suggest=suggest)})
    return out


def check_choice(client_id: int, headline: str, body: str, hebrew: str):
    """A choice the page may save: a font without Hebrew is never accepted
    silently as the brand font — it needs its Hebrew pairing. Names we do not
    have are NOT refused (the client states their brand; the page and every
    post then say the font is not available)."""
    own = uploads(client_id)
    pair, _ = find(client_id, hebrew, own) if hebrew else (None, "")
    if hebrew and pair is not None and not pair.hebrew:
        raise FontError("pairing_no_hebrew")
    for family in (headline, body):
        got, _ = find(client_id, family, own) if family else (None, "")
        if got is not None and not got.hebrew and not (pair is not None and pair.hebrew):
            raise FontError("needs_pairing")


def view(client_id: int, body: str, lang: str = "he") -> dict:
    """The fonts part of the branding page."""
    own = uploads(client_id)
    names = wanted(client_id, body)
    lib = font_library.status()
    groups = [{"category": g["category"],
               "families": [{"family": f["family"], "weights": f["weights"], "has_bold": f["has_bold"],
                             "installed": f["installed"],
                             # no bold: the nearest family that has one, offered on the page
                             "suggest": "" if f["has_bold"] else font_library.nearest_with_bold(f["family"])}
                            for f in g["families"]]}
              for g in font_library.grouped()]
    resolved = resolve(client_id)
    return {"chosen": names, "groups": groups,
            "library": {"pinned": lib["pinned"], "installed": lib["installed"]},
            "own": [row(rec, lang) for rec in sorted(own.values(), key=lambda r: r.get("family", "").lower())],
            "notes": notes(client_id, names, own, lang),
            "resolved": {slot: [f.family for f in resolved["chains"][slot]] for slot in SLOTS},
            "issues": resolved["issues"], "signature": resolved["signature"]}


# ─── Pictures made by the post renderer ──────────────────────────────────────

def sample_png(client_id: int, family: str, weight: int = 400) -> bytes:
    """A font shown as itself: a Hebrew line (a Latin one for a font without
    Hebrew) drawn by core/slide_renderer with THAT face only. FontError
    ("not_found") when we cannot draw with it — never another font's letters."""
    from core import slide_renderer
    got, issue = find(client_id, family)
    if got is None or issue:
        raise FontError("not_found")
    try:
        return slide_renderer.render_sample(got, SAMPLE["hebrew" if got.hebrew else "latin"], weight=weight)
    except (slide_renderer.RenderError, slide_renderer.RendererUnavailable, font_library.FontUnavailable) as e:
        _log(f"client {client_id}: no sample of {family} ({type(e).__name__})")
        raise FontError("not_found")


def preview_copy(spec: dict, fonts: dict) -> tuple:
    """Our own sample copy cut to what this template holds in these fonts —
    the preview shows the brand, it must not fail on OUR words."""
    from core import copy_budget
    budget = copy_budget.spec_budget(spec, fonts=fonts)

    def cut(text: str, field: str) -> str:
        b = budget.get(field)
        if not b:
            return text
        words, out = text.split(), []
        limit = min(int(b["max_chars"] * 0.8), len(text))
        for w in words[:b.get("max_words") or len(words)]:
            if len(" ".join(out + [w])) > limit:
                break
            out.append(w)
        return " ".join(out)
    return cut(PREVIEW_HEAD, "copy_headline"), cut(PREVIEW_BODY, "copy_body")


def preview_png(client_id: int, variant: str = "", override: dict = None, colors: dict = None,
                lang: str = "he") -> dict:
    """ONE real slide — the client's active template, palette variant and
    fonts — rendered by the renderer that renders posts.
    {"png", "manifest", "fonts": families drawn, "lines": fallback lines}.
    slide_renderer.RenderError when the brand as set cannot be drawn (the page
    says why: that is what a post would do too)."""
    from core import post_brief, render_manifest, slide_design, slide_renderer, slide_spec
    facts = post_brief.parse_brand(brand_brief.current(client_id).get("text", ""))
    declared = {v["label"]: v for v in facts.get("palette_variants") or []}
    palette = dict(declared.get(variant) or ({"label": variant} if variant else {}))
    fonts = resolve(client_id, override)
    spec, _name, _based = slide_design.base_spec(client_id, palette)
    spec = slide_design.placement_only(spec)
    roles = slide_spec.palette_roles(client_id, slide_spec.variant_for(palette))
    for role, value in (colors or {}).items():
        if role in roles and brand_brief.HEX_RE.match(str(value or "")):
            roles[role] = str(value).upper()
    head, body = preview_copy(spec, fonts)
    drawn = {}
    with render_manifest.tracing(client_id, "", "brand_preview"):
        png = slide_renderer.render_slide(spec, roles, head, body, 1, 1, "", slide_design.loader_for(client_id),
                                          manifest=drawn, fonts=fonts)
    return {"png": png, "manifest": drawn, "fonts": render_manifest.drawn_fonts(drawn),
            "lines": render_manifest.font_lines(drawn, lang)}


def error_text(code: str, lang: str) -> str:
    return pick(ERRORS[code], lang) if code in ERRORS else ""
