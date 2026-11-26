"""The BRANDING PACKAGE on the client's branding page (2026-11-27, redefined the
same day by the owner): three DELIVERABLES —

    לוגו · אתר ממותג · שני דפי נחיתה, כל אחד לייעוד אחר

Typography and the visual language are not products of their own: they are
what gets established along the way, and each section says so in what it
includes. The page shows the sections as part of the brand, never as blocked
feature tiles. A section opens to:

- the explanation and what it includes;
- "ask Johnny" — a REAL tracked team request (agent_actions.open_team_request,
  topic package_exception: a commercial question a person answers), never a
  model's promise and never a price from code;
- ONE real MOCKUP built from THIS client's material — their logo file, their
  palette variant, their actual product photos from the media library, their
  own latest copy, the brand's fonts — once per section, only on an explicit
  request: the page shows the credit cost first and the request must carry
  the confirmation. After it the section is locked again (the mockup stays
  visible, a second one is refused: `sample_used`).

A mockup is never a generic template with colours dropped in: a website or a
landing page is not drawn at all without a real product photo of the client's
(`needs_photos` — nothing is recorded, the client is told what is missing).
It is COMPOSED IN CODE (Pillow + the slide renderer's own type engine) — no
image model, no generated text inside a picture — which is why its cost is 0
generation credits today (SAMPLE_CREDITS); the cost is still shown and
confirmed, so a paid mockup later changes one number and nothing else. The
finished picture is kept in the client's own brand folder, and what it was
made from is frozen in a snapshot, so it never changes afterwards.

EVERY mockup request and every question is logged — an admin alert carrying
"client {id}" (the Control Room matches on it) and the section: these are
buying signals. Storage: the brand brief's own row (`page.package`), like the
rest of that page. No migration, no new table.
"""
import io
from datetime import datetime, timezone

from core import brand_brief, drive_storage

SERVICE_NAME = "brand_package"
SECTION_IDS = ("logo", "website", "landing_pages")
SAMPLE_CREDITS = {s: 0 for s in SECTION_IDS}      # composed in code: no generation credits (see the module docstring)
SHEET_BG = "#EDEDED"
PAGE_W = 1600
PHOTO_ROLES = ("product_catalog", "customer_photos", "raw_materials")     # the client's own pictures; never our posts, never logos
RTL = ("he", "ar")


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or next(iter(table.values()))


SECTIONS = {
    "logo": {
        "title": _five("לוגו", "A logo", "Un logo", "شعار", "Логотип"),
        "line": _five("סימן אחד שמזהים ממנו את העסק בכל מקום, משלט ועד תמונת פרופיל, כך שמי שראה אתכם פעם אחת מזהה אתכם בפעם השנייה.",
                      "One mark the business is recognised by everywhere, from a sign to a profile picture, so whoever saw you once recognises you the second time.",
                      "Un signe unique qui identifie l'entreprise partout, de l'enseigne à la photo de profil : qui vous a vu une fois vous reconnaît la seconde.",
                      "علامة واحدة يُعرف بها النشاط في كل مكان، من اللافتة إلى صورة الملف الشخصي، فمن رآكم مرة يتعرّف عليكم في الثانية.",
                      "Один знак, по которому бизнес узнают везде — от вывески до аватара: кто увидел вас однажды, узнает во второй раз."),
        "includes": [
            _five("לוגו מלא וסמל, בגרסאות לרקע בהיר ולרקע כהה", "A full logo and a mark, in versions for light and for dark backgrounds",
                  "Un logo complet et un symbole, en versions pour fond clair et fond sombre", "شعار كامل ورمز، بنسخ للخلفية الفاتحة والداكنة",
                  "Полный логотип и знак, в версиях для светлого и тёмного фона"),
            _five("הגופנים והצבעים של המותג נקבעים כאן, ומשמשים את כל מה שנבנה אחר כך", "The brand's typefaces and colours are set here, and serve everything built afterwards",
                  "Les polices et couleurs de la marque sont fixées ici et servent à tout ce qui suit", "تُحدَّد هنا خطوط العلامة وألوانها، وتخدم كل ما يُبنى بعدها",
                  "Здесь определяются шрифты и цвета бренда — они служат всему, что строится дальше"),
            _five("קבצים מוכנים לדיגיטל ולדפוס", "Files ready for screen and for print", "Des fichiers prêts pour l'écran et l'impression",
                  "ملفات جاهزة للشاشة والطباعة", "Файлы, готовые для экрана и печати")]},
    "website": {
        "title": _five("אתר ממותג", "A branded website", "Un site à votre image", "موقع بهوية العلامة", "Фирменный сайт"),
        "line": _five("אתר שנראה ומדבר כמו העסק, כך שמי שמגיע מפוסט או מחיפוש מבין מיד למי הגיע ומה לעשות עכשיו.",
                      "A site that looks and speaks like the business, so whoever arrives from a post or a search sees at once whom they reached and what to do next.",
                      "Un site qui ressemble à l'entreprise et parle comme elle : qui arrive d'un post ou d'une recherche comprend aussitôt où il est et quoi faire.",
                      "موقع يشبه النشاط ويتحدث مثله، فمن يصل من منشور أو بحث يفهم فورًا إلى من وصل وماذا يفعل الآن.",
                      "Сайт, который выглядит и говорит как бизнес: пришедший из поста или поиска сразу понимает, куда попал и что делать дальше."),
        "includes": [
            _five("עמוד בית, עמודי מוצרים או שירותים ועמוד יצירת קשר", "A home page, product or service pages and a contact page",
                  "Une page d'accueil, des pages produits ou services et une page contact", "صفحة رئيسية وصفحات منتجات أو خدمات وصفحة تواصل",
                  "Главная, страницы товаров или услуг и страница контактов"),
            _five("הטיפוגרפיה והשפה החזותית של המותג (צבעים, צורות, סגנון תמונות) מיושמות בכל עמוד", "The brand's typography and visual language (colours, shapes, photo style) applied on every page",
                  "La typographie et le langage visuel de la marque (couleurs, formes, style photo) appliqués à chaque page", "طباعة العلامة ولغتها البصرية (ألوان، أشكال، أسلوب صور) مطبّقة في كل صفحة",
                  "Типографика и визуальный язык бренда (цвета, формы, стиль фото) на каждой странице"),
            _five("מותאם לנייד, עם התמונות והמוצרים האמיתיים שלכם", "Built for phones, with your real photos and products",
                  "Pensé pour le mobile, avec vos vraies photos et vos vrais produits", "ملائم للهاتف، بصوركم ومنتجاتكم الحقيقية",
                  "Адаптирован для телефона, с вашими настоящими фото и товарами")]},
    "landing_pages": {
        "title": _five("שני דפי נחיתה, כל אחד לייעוד אחר", "Two landing pages, each for a different purpose",
                       "Deux pages d'atterrissage, chacune avec son objectif", "صفحتا هبوط، لكل واحدة غرض مختلف",
                       "Два лендинга, каждый для своей задачи"),
        "line": _five("שני דפים שכל אחד בנוי למטרה אחת, למשל מכירה ללקוח פרטי והצעת מחיר לעסק, כך שכל קמפיין מוביל לדף שנכתב בדיוק בשבילו.",
                      "Two pages each built for one goal, for example a sale to a private customer and a quote for a business, so every campaign leads to a page written exactly for it.",
                      "Deux pages construites chacune pour un seul but, par exemple une vente à un particulier et un devis pour une entreprise : chaque campagne mène à la page écrite pour elle.",
                      "صفحتان كل منهما مبنية لهدف واحد، مثل بيع لعميل فرد وعرض سعر لشركة، فتقود كل حملة إلى صفحة كُتبت لها تمامًا.",
                      "Две страницы, каждая под одну цель — например, продажа частному клиенту и расчёт для бизнеса: каждая кампания ведёт на страницу, написанную именно для неё."),
        "includes": [
            _five("דף אחד לכל ייעוד, עם מסר, תמונה וקריאה לפעולה משלו", "One page per purpose, with its own message, picture and call to action",
                  "Une page par objectif, avec son message, son image et son appel à l'action", "صفحة لكل غرض، برسالتها وصورتها ودعوتها للإجراء",
                  "По странице на задачу — со своим сообщением, изображением и призывом"),
            _five("אותה שפה חזותית כמו הלוגו והאתר, כך שמזהים אתכם בכל שלב", "The same visual language as the logo and the site, so you are recognised at every step",
                  "Le même langage visuel que le logo et le site : on vous reconnaît à chaque étape", "اللغة البصرية نفسها للشعار والموقع، فيُتعرَّف عليكم في كل مرحلة",
                  "Тот же визуальный язык, что у логотипа и сайта: вас узнают на каждом шаге"),
            _five("מחוברים למדידה, כדי לראות איזה דף מביא פניות", "Connected to measurement, to see which page brings enquiries",
                  "Reliées à la mesure, pour voir quelle page amène des demandes", "موصولتان بالقياس لمعرفة أي صفحة تجلب الطلبات",
                  "Подключены к аналитике — видно, какая страница приносит обращения")]},
}

TEXT = {
    "title": _five("חבילת המיתוג: לוגו, אתר ושני דפי נחיתה", "The branding package: a logo, a website and two landing pages",
                   "Le forfait branding : un logo, un site et deux pages d'atterrissage", "باقة الهوية: شعار وموقع وصفحتا هبوط",
                   "Пакет брендинга: логотип, сайт и два лендинга"),
    "sub": _five("שלושה דברים שמקבלים ביד. בדרך נקבעות גם הטיפוגרפיה והשפה החזותית של המותג, והן נשארות שלכם לכל מה שיבוא אחר כך. "
                 "כל חלק נפתח להסבר, ואפשר לבקש הדמיה אחת אמיתית שנבנית מהחומרים שלכם.",
                 "Three things you get in hand. Along the way the brand's typography and visual language are set too, and they stay "
                 "yours for everything that comes after. Each part opens to an explanation, and you can ask for one real mockup built from your own material.",
                 "Trois choses que vous recevez. En chemin, la typographie et le langage visuel de la marque sont aussi fixés, et restent "
                 "à vous pour la suite. Chaque partie s'ouvre sur une explication, et vous pouvez demander une vraie maquette faite avec vos éléments.",
                 "ثلاثة أشياء تستلمونها. وفي الطريق تُحدَّد أيضًا طباعة العلامة ولغتها البصرية وتبقى لكم لكل ما يأتي لاحقًا. "
                 "كل جزء يُفتح على شرح، ويمكنكم طلب نموذج حقيقي واحد يُبنى من موادكم.",
                 "Три вещи, которые вы получаете. По пути определяются типографика и визуальный язык бренда — они остаются вашими "
                 "для всего дальнейшего. Каждая часть раскрывается в объяснение, и можно попросить один настоящий макет из ваших материалов."),
    "cost": _five("הדמיה אחת לחלק הזה, נבנית מהלוגו, מהצבעים ומתמונות המוצרים שלכם. עלות: {n} קרדיטים.",
                  "One mockup for this part, built from your logo, your colours and your product photos. Cost: {n} credits.",
                  "Une maquette pour cette partie, faite avec votre logo, vos couleurs et vos photos produits. Coût : {n} crédits.",
                  "نموذج واحد لهذا الجزء، يُبنى من شعاركم وألوانكم وصور منتجاتكم. التكلفة: {n} رصيد.",
                  "Один макет для этой части — из вашего логотипа, цветов и фото товаров. Стоимость: {n} кредитов."),
    "used": _five("ההדמיה לחלק הזה כבר הופקה ({date}). החלק עצמו הוא חלק מחבילת המיתוג.",
                  "The mockup for this part was already made ({date}). The part itself belongs to the branding package.",
                  "La maquette de cette partie a déjà été réalisée ({date}). La partie elle-même fait partie du forfait branding.",
                  "أُنتج نموذج هذا الجزء من قبل ({date}). الجزء نفسه ضمن باقة الهوية.",
                  "Макет для этой части уже сделан ({date}). Сама часть входит в пакет брендинга."),
    "asked": _five("השאלה שלכם הועברה לג'וני, והוא יחזור אליכם.", "Your question was passed to Johnny, and he will get back to you.",
                   "Votre question a été transmise à Johnny, qui reviendra vers vous.", "أُرسل سؤالكم إلى جوني وسيعود إليكم.",
                   "Ваш вопрос передан Джонни, он свяжется с вами."),
    # the owner's name is said by the SERVER, never kept in the page
    "ask": _five("לשאול את ג'וני על זה", "Ask Johnny about it", "Poser la question à Johnny", "اسألوا جوني عن ذلك", "Спросить об этом Джонни"),
    "asked_label": _five("השאלה הועברה לג'וני", "Your question was passed to Johnny", "Question transmise à Johnny",
                         "أُرسل السؤال إلى جوني", "Вопрос передан Джонни"),
    "cap_logo": _five("הלוגו שלכם כמערכת: על צבעי המותג, כתמונת פרופיל, על תמונת מוצר שלכם ועל כרטיס.",
                      "Your logo as a system: on the brand colours, as a profile picture, on a product photo of yours and on a card.",
                      "Votre logo en système : sur les couleurs de la marque, en photo de profil, sur une de vos photos produits et sur une carte.",
                      "شعاركم كنظام: على ألوان العلامة، كصورة ملف شخصي، على صورة منتج لكم وعلى بطاقة.",
                      "Ваш логотип как система: на цветах бренда, как аватар, на вашем фото товара и на визитке."),
    "cap_logo_sketch": _five("עוד אין לכם קובץ לוגו, אז זו סקיצה ראשונה בלבד: שם העסק בגופן ובצבעים של המותג. לוגו אמיתי מעוצב בחבילה.",
                             "You have no logo file yet, so this is only a first sketch: the business name in the brand's font and colours. A real logo is designed in the package.",
                             "Vous n'avez pas encore de fichier logo : ceci n'est qu'une première esquisse, le nom de l'entreprise dans la police et les couleurs de la marque. Un vrai logo est conçu dans le forfait.",
                             "لا ملف شعار لديكم بعد، فهذه مسودة أولى فقط: اسم النشاط بخط العلامة وألوانها. الشعار الحقيقي يُصمَّم ضمن الباقة.",
                             "Файла логотипа у вас пока нет, поэтому это лишь первый набросок: название бизнеса шрифтом и цветами бренда. Настоящий логотип создаётся в пакете."),
    "cap_website": _five("ראש עמוד הבית של האתר שלכם: הלוגו, הצבעים, הטקסט ותמונות המוצרים הם שלכם.",
                         "The top of your site's home page: the logo, the colours, the copy and the product photos are yours.",
                         "Le haut de la page d'accueil de votre site : logo, couleurs, texte et photos produits sont les vôtres.",
                         "أعلى الصفحة الرئيسية لموقعكم: الشعار والألوان والنص وصور المنتجات لكم.",
                         "Верх главной страницы вашего сайта: логотип, цвета, текст и фото товаров — ваши."),
    "cap_landing_pages": _five("שני דפי נחיתה שלכם, כל אחד לייעוד אחר, עם הלוגו, הצבעים ותמונות המוצרים שלכם.",
                               "Two landing pages of yours, each for a different purpose, with your logo, colours and product photos.",
                               "Vos deux pages d'atterrissage, chacune pour un objectif, avec votre logo, vos couleurs et vos photos produits.",
                               "صفحتا هبوط لكم، لكل واحدة غرض، بشعاركم وألوانكم وصور منتجاتكم.",
                               "Два ваших лендинга, каждый под свою задачу, с вашим логотипом, цветами и фото товаров."),
}

# The words of a page's own furniture (navigation, buttons, section names). Everything
# that SAYS something about the business comes from the client: their name, their copy.
CHROME = {
    "nav": _five("מוצרים   ·   אודות   ·   יצירת קשר", "Products   ·   About   ·   Contact", "Produits   ·   À propos   ·   Contact",
                 "المنتجات   ·   من نحن   ·   تواصل", "Товары   ·   О нас   ·   Контакты"),
    "products": _five("המוצרים שלנו", "Our products", "Nos produits", "منتجاتنا", "Наши товары"),
    "purpose_b2c": _five("ללקוחות פרטיים", "For private customers", "Pour les particuliers", "للعملاء الأفراد", "Для частных клиентов"),
    "purpose_b2b": _five("לעסקים, קבוצות ואירועים", "For business, teams and events", "Pour entreprises, équipes et événements",
                         "للشركات والفرق والفعاليات", "Для бизнеса, команд и событий"),
    "cta_b2c": _five("להזמנה עכשיו", "Order now", "Commander", "اطلبوا الآن", "Заказать"),
    "cta_b2b": _five("לקבלת הצעת מחיר", "Get a quote", "Demander un devis", "اطلبوا عرض سعر", "Получить расчёт"),
}

ERRORS = {
    "bad_section": _five("החלק הזה לא קיים", "This part does not exist", "Cette partie n'existe pas", "هذا الجزء غير موجود", "Такой части нет"),
    "confirm_needed": _five("צריך לאשר את העלות לפני שמפיקים הדמיה", "The cost must be confirmed before a mockup is made",
                            "Le coût doit être confirmé avant de réaliser une maquette", "يجب تأكيد التكلفة قبل إنتاج النموذج",
                            "Перед созданием макета нужно подтвердить стоимость"),
    "sample_used": _five("ההדמיה לחלק הזה כבר הופקה. יש הדמיה אחת לכל חלק.", "The mockup for this part was already made. There is one per part.",
                         "La maquette de cette partie existe déjà. Une seule par partie.", "أُنتج نموذج هذا الجزء من قبل. نموذج واحد لكل جزء.",
                         "Макет для этой части уже сделан. Один на часть."),
    "sample_unavailable": _five("לא הצלחנו להפיק את ההדמיה כרגע. לא נרשם דבר, ואפשר לנסות שוב.", "We couldn't make the mockup right now. Nothing was recorded, and you can try again.",
                                "Impossible de réaliser la maquette pour l'instant. Rien n'a été enregistré, vous pouvez réessayer.", "تعذّر إنتاج النموذج الآن. لم يُسجَّل شيء ويمكنكم المحاولة مجددًا.",
                                "Сейчас не удалось сделать макет. Ничего не записано, можно попробовать снова."),
    "needs_photos": _five("כדי לבנות הדמיה אמיתית אנחנו צריכים לפחות תמונה אחת של מוצר שלכם, ועוד אין כזו בספריית המדיה. "
                          "חברו תיקיית מוצרים בלשונית המדיה ונסו שוב. לא נרשם דבר.",
                          "To build a real mockup we need at least one photo of a product of yours, and the media library has none yet. "
                          "Connect a products folder in the Media tab and try again. Nothing was recorded.",
                          "Pour une vraie maquette, il nous faut au moins une photo d'un de vos produits, et la médiathèque n'en a pas encore. "
                          "Connectez un dossier produits dans l'onglet Média et réessayez. Rien n'a été enregistré.",
                          "لبناء نموذج حقيقي نحتاج صورة واحدة على الأقل لمنتج لكم، ولا توجد بعد في مكتبة الوسائط. "
                          "اربطوا مجلد منتجات في تبويب الوسائط وحاولوا مجددًا. لم يُسجَّل شيء.",
                          "Для настоящего макета нужна хотя бы одна фотография вашего товара, а в медиатеке их пока нет. "
                          "Подключите папку с товарами во вкладке «Медиа» и попробуйте снова. Ничего не записано."),
    "needs_logo": _five("כדי לבנות את ההדמיה הזו אנחנו צריכים קובץ לוגו או את שם העסק, ועוד אין לנו אף אחד מהם. לא נרשם דבר.",
                        "To build this mockup we need a logo file or the business name, and we have neither yet. Nothing was recorded.",
                        "Pour cette maquette, il nous faut un fichier logo ou le nom de l'entreprise ; nous n'avons ni l'un ni l'autre. Rien n'a été enregistré.",
                        "لبناء هذا النموذج نحتاج ملف شعار أو اسم النشاط، ولا نملك أيًّا منهما بعد. لم يُسجَّل شيء.",
                        "Для этого макета нужен файл логотипа или название бизнеса — у нас пока нет ни того, ни другого. Ничего не записано."),
    "not_found": _five("עוד אין הדמיה לחלק הזה", "There is no mockup for this part yet", "Pas encore de maquette pour cette partie", "لا يوجد نموذج لهذا الجزء بعد",
                       "Для этой части макета пока нет"),
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
            sketch = sid == "logo" and not snap.get("logos")
            sample = {"state": "made", "at": mine["sample_at"][:10], "credits": int(mine.get("credits") or 0),
                      "url": f"/api/client/brand-identity/package/{sid}/sample?at={mine['sample_at'][:19]}",
                      "caption": pick(TEXT["cap_logo_sketch" if sketch else "cap_" + sid], lang),
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


# ─── The material: THIS client's own ─────────────────────────────────────────

def _real_copies(client_id: int, n: int = 2) -> list:
    """Up to `n` distinct [headline, body] pairs from the client's OWN latest
    posts (first slides) — a mockup never carries copy we invented. [] when
    nothing was written yet (the business name then stands as the headline)."""
    out = []
    try:
        from core import social_queue
        items = sorted(social_queue._load(client_id), key=lambda i: i.get("created_at", ""), reverse=True)
        for item in items:
            slides = ((item.get("design") or {}).get("slides") or (item.get("post") or {}).get("slides") or [])
            for s in slides:
                head = str(s.get("copy_headline") or "").strip()
                if head and head not in [o[0] for o in out]:
                    out.append([head, str(s.get("copy_body") or "").strip()])
                    break
            if len(out) >= n:
                break
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: no real copy read ({type(e).__name__})")
    return out


def _photos(client_id: int, limit: int = 5) -> list:
    """File ids of the client's own pictures in their connected media folders,
    products first. Never our posts and never logo files. [] when there are none."""
    try:
        from core import drive_assets
        rows = drive_assets.find_assets(client_id, roles=list(PHOTO_ROLES), media_type="image", limit=limit)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: media library unreadable ({type(e).__name__})")
        return []
    return [r["file_id"] for r in rows if r.get("file_id")][:limit]


def _photo_bytes(client_id: int, file_id: str) -> bytes:
    """One of the client's connected-folder pictures (read as those folders are
    read everywhere: the service account). A large preview when Drive has one,
    else the file."""
    from core import drive_service
    with drive_storage.as_service_account():
        try:
            link = drive_service.get_meta(file_id, "id,thumbnailLink").get("thumbnailLink") or ""
            if link:
                from core import drive_assets
                data, _type = drive_service.fetch_thumbnail(drive_assets._sized_link(link, 1600), 12 * 1024 * 1024)
                if data:
                    return data
        except Exception:
            pass
        return drive_service.download_file(file_id)


def _business_name(client_id: int) -> str:
    try:
        from agents.client_agent import get_client
        return str((get_client(client_id) or {}).get("business_name") or "").strip()[:60]
    except Exception:
        return ""


def _logo_files(client_id: int) -> dict:
    """{version: file id} of the client's logo files Pillow can open."""
    try:
        from core import brand_page
        logos = brand_page.current_logos(brand_page.files(client_id))
        return {v: f["id"] for v, f in logos.items() if f.get("format") in brand_page.RASTER}
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: logo files unreadable ({type(e).__name__})")
        return {}


def snapshot(client_id: int, section: str, lang: str = "he") -> dict:
    """What ONE mockup is made from, frozen at the request: the palette
    variants (each with its role colours), the logo files, the product photos,
    the business name, the client's own copy, the brand's fonts. PackageError
    needs_photos / needs_logo when the client's material is not there — a
    mockup is never filled with someone else's."""
    from core import brand_fonts, brand_page, slide_spec
    cur = brand_brief.current(client_id)
    variants = []
    for v in brand_page._variants(client_id, cur, "en")[:2]:
        label = slide_spec.variant_for({"label": v["label"]})
        try:
            variants.append({"label": v["label"], "segment": v.get("segment") or "", "roles": slide_spec.palette_roles(client_id, label)})
        except Exception:
            continue
    if not variants:
        variants = [{"label": "", "segment": "", "roles": slide_spec.palette_roles(client_id, "default")}]
    snap = {"lang": lang, "name": _business_name(client_id), "variants": variants, "logos": _logo_files(client_id),
            "photos": _photos(client_id), "copy": _real_copies(client_id), "fonts": dict(brand_fonts.wanted(client_id))}
    if section in ("website", "landing_pages") and not snap["photos"]:
        raise PackageError("needs_photos")
    if section == "logo" and not snap["logos"] and not snap["name"]:
        raise PackageError("needs_logo")
    return snap


# ─── Composing (Pillow + the slide renderer's type engine) ───────────────────

class _Kit:
    """One mockup's tools: the fonts, the loaded pictures, text as strips."""

    def __init__(self, client_id: int, snap: dict):
        from core import brand_fonts, font_library
        self.client_id, self.snap, self.lang = client_id, snap, snap.get("lang") or "he"
        self.rtl = self.lang in RTL
        chains = brand_fonts.resolve(client_id, snap.get("fonts") or {}).get("chains") or {}
        self.bundled = {"headline": font_library.face("Rubik"), "body": font_library.face("Assistant")}
        self.face = {slot: (chains.get(slot) or [self.bundled[slot]])[0] for slot in ("headline", "body")}
        self._pictures = {}

    def word(self, key: str) -> str:
        return pick(CHROME[key], self.lang)

    def text(self, slot: str, text: str, weight: int, size: int, width: int, height: int, fg: str, bg: str):
        """`text` as an RGB strip in the brand's font for the slot (the bundled
        face when the brand font cannot draw these characters)."""
        from PIL import Image
        from core import slide_renderer
        for face in (self.face[slot], self.bundled[slot]):
            try:
                png = slide_renderer.render_sample(face, text, weight=weight, size=size, width=width, height=height, fg=fg, bg=bg, pad=0)
                return Image.open(io.BytesIO(png)).convert("RGB")
            except slide_renderer.RenderError:
                continue
        raise PackageError("sample_unavailable")

    def tight(self, strip, bg: str):
        """The strip cut to its ink (to centre a label on a button)."""
        from PIL import Image, ImageChops
        box = ImageChops.difference(strip, Image.new("RGB", strip.size, bg)).getbbox()
        return strip.crop(box) if box else strip

    def photo(self, index: int):
        ids = self.snap.get("photos") or []
        if not ids:
            return None
        file_id = ids[index % len(ids)]
        if file_id not in self._pictures:
            from PIL import Image
            self._pictures[file_id] = Image.open(io.BytesIO(_photo_bytes(self.client_id, file_id))).convert("RGB")
        return self._pictures[file_id]

    def logo(self, bg: str):
        """The client's logo file that suits `bg` (RGBA), or None."""
        from core import brand_page
        logos = self.snap.get("logos") or {}
        dark = brand_page._lum(bg) < 0.4
        for version in (("on_dark", "full", "mark", "on_light") if dark else ("on_light", "full", "mark", "on_dark")):
            file_id = logos.get(version)
            if not file_id:
                continue
            if file_id not in self._pictures:
                from PIL import Image
                try:
                    content, _mime = brand_page.file_bytes(self.client_id, file_id)
                    self._pictures[file_id] = Image.open(io.BytesIO(content)).convert("RGBA")
                except Exception as e:
                    print(f"[{SERVICE_NAME}] client {self.client_id}: logo file not opened ({type(e).__name__})")
                    self._pictures[file_id] = None
            if self._pictures[file_id] is not None:
                return self._pictures[file_id]
        return None

    def mark(self, bg: str, fg: str, max_w: int, max_h: int):
        """What stands for the brand on `bg`, at most max_w × max_h: the
        client's logo file, else the business name set in the brand's font."""
        from PIL import Image
        logo = self.logo(bg)
        if logo is not None:
            out = logo.copy()
            out.thumbnail((max_w, max_h), Image.LANCZOS)
            return out
        name = self.snap.get("name") or ""
        if not name:
            return None
        return self.tight(self.text("headline", name, 700, int(max_h * 0.7), max_w, max_h, fg, bg), bg)


def _paste(canvas, image, x: int, y: int):
    canvas.paste(image, (int(x), int(y)), image if image.mode == "RGBA" else None)


def _cover(image, width: int, height: int):
    from PIL import Image
    scale = max(width / image.width, height / image.height)
    big = image.resize((max(width, round(image.width * scale)), max(height, round(image.height * scale))), Image.LANCZOS)
    left, top = (big.width - width) // 2, (big.height - height) // 2
    return big.crop((left, top, left + width, top + height))


def _lines(text: str, per_line: int, most: int) -> list:
    out, line = [], []
    for word in str(text or "").split():
        if line and len(" ".join(line + [word])) > per_line:
            out.append(" ".join(line))
            line = []
        line.append(word)
    if line:
        out.append(" ".join(line))
    return out[:most]


def _on(accent: str, a: str, b: str) -> str:
    """Of two colours, the one that reads better on `accent` (a button's label)."""
    from core import brand_page
    return a if brand_page.contrast(a, accent) >= brand_page.contrast(b, accent) else b


def _page(kit: _Kit, roles: dict, copy: list, photo: int, purpose: str = "", cta: str = "cta_b2c", products: bool = False):
    """One page as a visitor would see it in a browser: the bar, the client's
    logo, a hero — their copy beside their product photo — and, for a site,
    a row of their other products."""
    from PIL import Image, ImageDraw
    bg, fg, accent, muted = (roles[r] for r in ("bg", "fg", "accent", "muted"))
    bar, nav, hero, row = 44, 88, 760, 470
    extra = products and len(kit.snap.get("photos") or []) > 1
    page = Image.new("RGB", (PAGE_W, bar + nav + hero + (row if extra else 0)), bg)
    d = ImageDraw.Draw(page)
    # the browser bar
    d.rectangle((0, 0, PAGE_W, bar), fill="#E4E4E7")
    for i, colour in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        d.ellipse((20 + i * 24, 15, 34 + i * 24, 29), fill=colour)
    d.rounded_rectangle((120, 9, PAGE_W - 120, bar - 9), radius=13, fill="#FFFFFF")
    # the navigation: the logo on the reading side, the menu opposite
    pad, top = 64, bar
    mark = kit.mark(bg, fg, 300, 52)
    if mark is not None:
        _paste(page, mark, PAGE_W - pad - mark.width if kit.rtl else pad, top + (nav - mark.height) // 2)
    menu = kit.tight(kit.text("body", kit.word("nav"), 500, 24, 620, 44, fg, bg), bg)
    _paste(page, menu, pad if kit.rtl else PAGE_W - pad - menu.width, top + (nav - menu.height) // 2)
    d.line((0, top + nav - 1, PAGE_W, top + nav - 1), fill=muted, width=1)
    # the hero: the text panel on the reading side, the client's product photo beside it
    top += nav
    panel = 700
    picture = kit.photo(photo)
    if picture is not None:
        page.paste(_cover(picture, PAGE_W - panel, hero), (0 if kit.rtl else panel, top))
    x0 = (PAGE_W - panel if kit.rtl else 0) + pad
    inner = panel - 2 * pad

    def put(strip, y):
        # trimmed at the sides only: every line keeps its own baseline, so lines sit evenly
        box = kit.tight(strip, bg).size[0]
        cut = strip.crop((strip.width - box, 0, strip.width, strip.height)) if kit.rtl else strip.crop((0, 0, box, strip.height))
        _paste(page, cut, x0 + inner - cut.width if kit.rtl else x0, y)

    head, body = (copy + ["", ""])[:2]
    head = head or kit.snap.get("name") or ""
    y = top + 96
    if purpose:
        put(kit.text("body", kit.word(purpose), 700, 26, inner, 44, accent, bg), y)
        y += 64
    for line in _lines(head, 20, 3):
        put(kit.text("headline", line, 700, 66, inner, 92, fg, bg), y)
        y += 96
    y += 12
    for line in _lines(body, 42, 3):
        put(kit.text("body", line, 400, 28, inner, 46, muted, bg), y)
        y += 50
    y = max(y + 36, top + hero - 200)
    label = kit.tight(kit.text("body", kit.word(cta), 700, 30, 320, 56, _on(accent, bg, fg), accent), accent)
    width = max(300, label.width + 96)
    bx = x0 + inner - width if kit.rtl else x0
    d.rounded_rectangle((bx, y, bx + width, y + 84), radius=42, fill=accent)
    _paste(page, label, bx + (width - label.width) // 2, y + (84 - label.height) // 2)
    # a site's home page goes on: the client's other products
    if extra:
        top += hero
        d.rectangle((0, top, PAGE_W, top + row), fill="#FFFFFF")
        title = kit.tight(kit.text("headline", kit.word("products"), 700, 40, 600, 64, "#111111", "#FFFFFF"), "#FFFFFF")
        _paste(page, title, PAGE_W - pad - title.width if kit.rtl else pad, top + 40)
        gap, card = 40, (PAGE_W - 2 * pad - 2 * 40) // 3
        for i in range(3):
            shot = kit.photo(photo + 1 + i)
            x = pad + i * (card + gap)
            page.paste(_cover(shot, card, 300), (x, top + 130))
            d.rectangle((x, top + 130 + 292, x + card, top + 130 + 300), fill=accent)
    return page


def _stack(pages: list, gap: int = 48):
    from PIL import Image
    sheet = Image.new("RGB", (PAGE_W + 2 * gap, sum(p.height for p in pages) + gap * (len(pages) + 1)), SHEET_BG)
    y = gap
    for p in pages:
        sheet.paste(p, (gap, y))
        y += p.height + gap
    return sheet


def _logo_sheet(kit: _Kit):
    """The brand's mark as a SYSTEM on the client's own material: on the brand
    colour, on a light ground, as a profile picture, on a product photo of
    theirs, on a card. With no logo file it is the business name in the
    brand's font — a first sketch, and the caption says so."""
    from PIL import Image, ImageDraw
    roles = kit.snap["variants"][0]["roles"]
    bg, fg, accent, muted = (roles[r] for r in ("bg", "fg", "accent", "muted"))
    from core import brand_page
    light = fg if brand_page._lum(fg) > 0.6 else "#FFFFFF"
    ink = bg if brand_page._lum(bg) < 0.4 else "#111111"
    gap, h = 40, 440
    sheet = Image.new("RGB", (PAGE_W, 3 * gap + 2 * h), SHEET_BG)
    d = ImageDraw.Draw(sheet)

    def tile(x, y, w, colour):
        d.rectangle((x, y, x + w, y + h), fill=colour)
        return x, y, w

    def centre(x, y, w, hh, image):
        if image is not None:
            _paste(sheet, image, x + (w - image.width) // 2, y + (hh - image.height) // 2)

    x, y, w = tile(gap, gap, 740, bg)
    centre(x, y, w, h, kit.mark(bg, fg, 480, 240))
    x, y, w = tile(gap + 740 + gap, gap, 740, light)
    centre(x, y, w, h, kit.mark(light, ink, 480, 240))
    y2 = 2 * gap + h
    x, y, w = tile(gap, y2, 360, accent)
    d.ellipse((x + 50, y + 90, x + 310, y + 350), fill=bg)
    centre(x + 50, y + 90, 260, 260, kit.mark(bg, fg, 170, 150))
    x, y, w = tile(gap + 360 + gap, y2, 600, bg)
    shot = kit.photo(0)
    if shot is not None:
        sheet.paste(_cover(shot, w, h), (x, y))
        d.rectangle((x + w - 250, y + h - 110, x + w, y + h), fill=bg)
        centre(x + w - 250, y + h - 110, 250, 110, kit.mark(bg, fg, 200, 70))
    else:
        centre(x, y, w, h, kit.mark(bg, fg, 380, 200))
    x, y, w = tile(gap + 360 + gap + 600 + gap, y2, PAGE_W - (gap + 360 + gap + 600 + gap) - gap, muted)
    d.rectangle((x + 40, y + 100, x + w - 40, y + 340), fill=light)
    d.rectangle((x + 40, y + 328, x + w - 40, y + 340), fill=accent)
    centre(x + 40, y + 100, w - 80, 228, kit.mark(light, ink, w - 160, 120))
    return sheet


def render(client_id: int, section: str, snap: dict) -> bytes:
    """The mockup's PNG, composed from its snapshot — in code, never a model."""
    _check(section)
    kit = _Kit(client_id, snap)
    variants = snap["variants"]
    copies = list(snap.get("copy") or [])
    if section == "logo":
        image = _logo_sheet(kit)
    elif section == "website":
        image = _stack([_page(kit, variants[0]["roles"], copies[0] if copies else [], 0, products=True)])
    else:
        # two pages, two purposes: private customers on the first variant, business on the second (or the same palette)
        second = variants[1] if len(variants) > 1 else variants[0]
        order = sorted((variants[0], second), key=lambda v: 0 if v.get("segment") != "b2b" else 1)
        if order[0] is order[1] or order[0].get("segment") == order[1].get("segment"):
            order = [variants[0], second]
        image = _stack([
            _page(kit, order[0]["roles"], copies[0] if copies else [], 0, purpose="purpose_b2c", cta="cta_b2c"),
            _page(kit, order[1]["roles"], copies[1] if len(copies) > 1 else (copies[0] if copies else []), 1,
                  purpose="purpose_b2b", cta="cta_b2b")])
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


# ─── Keeping the finished picture (the client's own brand folder) ────────────

@drive_storage.scoped
def _keep(client_id: int, section: str, png: bytes) -> str:
    from core import brand_page, drive_service as drive
    stored = drive.upload_bytes(brand_page._folder(client_id), f"branding-package-mockup-{section}.png", png, "image/png",
                                app_properties={"uallak_kind": "package_mockup", "section": section, "uploaded_at": _now()})
    return str(stored.get("id") or "")


@drive_storage.scoped
def _kept(client_id: int, file_id: str) -> bytes:
    from core import drive_service as drive
    return drive.download_file(file_id)


_PNG = {}          # (client_id, section, sample_at) -> bytes


def request_sample(client_id: int, section: str, confirmed: bool, lang: str = "he") -> dict:
    """ONE mockup for this section, on the client's explicit, confirmed
    request. PackageError: bad_section | confirm_needed (the cost was not
    confirmed) | sample_used (already made — the section is locked again) |
    needs_photos / needs_logo (the client's own material is not there) |
    sample_unavailable (could not be composed). On any refusal NOTHING is
    recorded or charged. Every request is logged for the owner."""
    _check(section)
    title = pick(SECTIONS[section]["title"], "en")
    if (state(client_id).get(section) or {}).get("sample_at"):
        _alert([f"client {client_id}: asked for ANOTHER branding-package mockup — {title} — refused, one per section "
                f"(buying signal)"])
        raise PackageError("sample_used")
    if not confirmed:
        raise PackageError("confirm_needed")
    try:
        snap = snapshot(client_id, section, lang)
        png = render(client_id, section, snap)
    except PackageError as e:
        _alert([f"client {client_id}: asked for a branding-package mockup — {title} — and it was NOT made ({e.code}); "
                f"nothing was recorded (buying signal)"])
        raise
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: mockup {section} not composed ({type(e).__name__}: {str(e)[:160]})")
        _alert([f"client {client_id}: asked for a branding-package mockup — {title} — and it could NOT be composed "
                f"({type(e).__name__}); nothing was recorded (buying signal)"])
        raise PackageError("sample_unavailable")
    at, file_id = _now(), ""
    try:
        file_id = _keep(client_id, section, png)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: mockup {section} not kept as a file ({type(e).__name__}) — redrawn from its snapshot")
    _save(client_id, section, sample_at=at, snapshot=snap, credits=SAMPLE_CREDITS[section], file_id=file_id)
    _PNG[(int(client_id), section, at)] = png
    used = ("their logo file" if snap["logos"] else "their business name (no logo file)") + f", {len(snap['photos'])} of their photos"
    _alert([f"client {client_id}: requested the branding-package mockup — {title} — {SAMPLE_CREDITS[section]} credits, "
            f"built from {used} (buying signal)"])
    return {"section": section, "at": at}


def sample_png(client_id: int, section: str) -> bytes:
    """The section's mockup as it was made (PackageError not_found when none):
    the kept file, else composed again from the frozen snapshot."""
    _check(section)
    mine = state(client_id).get(section) or {}
    if not mine.get("sample_at") or not isinstance(mine.get("snapshot"), dict):
        raise PackageError("not_found")
    key = (int(client_id), section, mine["sample_at"])
    if key not in _PNG:
        png = b""
        if mine.get("file_id"):
            try:
                png = _kept(client_id, mine["file_id"])
            except Exception as e:
                print(f"[{SERVICE_NAME}] client {client_id}: kept mockup {section} unreadable ({type(e).__name__})")
        if not png:
            try:
                png = render(client_id, section, mine["snapshot"])
            except Exception as e:
                print(f"[{SERVICE_NAME}] client {client_id}: mockup {section} not redrawn ({type(e).__name__})")
                raise PackageError("not_found")
        if len(_PNG) > 40:
            for old in list(_PNG)[:20]:
                _PNG.pop(old, None)
        _PNG[key] = png
    return _PNG[key]
