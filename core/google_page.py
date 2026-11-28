"""The client's GOOGLE page (2026-11-28, docs/GOOGLE-PAGE.md) — where Yoav
stops being a black box. Five sections, every line under governance §9
(core/client_speech): no platform vocabulary anywhere, reports included.

1. ADS       the full asset set of every campaign (each image in its ratio,
             every headline, every description, the logo) and two or three
             EXAMPLE combinations, each labelled an example. Google assembles
             the ad on every showing: no single rendering is ever "the ad".
2. NUMBERS   the budget (agreed, spent, left, pace) and the results in the
             client's own action word, PER LANE (§2) — never one blended
             number. Figures that could not be refreshed are shown with the
             date they were last read, and said to be old.
3. TERMS     the search phrases we pay for, grouped by theme with why — ONLY
             for a search campaign. A campaign Google places by itself has no
             phrase list: it has search themes, a hint and not targeting, and
             is labelled so. The client FLAGS a phrase (their domain knowledge);
             Yoav decides. What we deliberately do not pay for is shown too.
4. REPORTS   built from the same figures when the client asks Yoav in chat;
             kept (history), readable on the page, downloadable.
5. STRATEGY  written by Yoav: the money and why, what it assumes, the two or
             three numbers we watch, and WHAT WOULD MAKE US CHANGE COURSE AND
             BY WHEN — a version without a dated abandon condition is refused.
             Every version is kept and visible.

ONE store per client: app_settings `google_page:{client_id}` = {"strategy":
[versions, newest first], "terms": {...}, "flags": [...], "excluded": [...],
"reports": [...], "numbers": {last good figures}}. The readers below
(`_orders`, `_activity`, `_daily`, `_per_campaign`, `_budget`) are the only
doors to other modules, so tests replace them and touch no database.
"""
import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone

from core import client_language, client_speech

SERVICE_NAME = "google_page"
PREFIX = "google_page:"
LANGS = ("he", "en", "fr", "ar", "ru")
ASSEMBLED = ("pmax", "demand_gen", "display", "search")     # Google builds the ad from parts on every showing
THEMES_ONLY = ("pmax", "demand_gen")                        # no phrase list: search themes are a signal, not targeting
LANES = ("system", "client", "unrecorded")
KEEP_VERSIONS = 24
KEEP_REPORTS = 36
MAX_OPEN_FLAGS = 20
REASON_MIN, REASON_MAX = 3, 300
SNAPSHOT_SECONDS = 3600
MAX_DAYS_TO_KNOW = 180
THUMB = "/api/client/media-drive/thumb/{id}"
WHERE = {"he": "עמוד ״גוגל״", "en": "the Google page", "fr": "la page Google", "ar": "صفحة \"جوجل\"", "ru": "страница «Google»"}


class PageError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


# fr / ar / ru below were written without a native speaker (client_speech.UNREVIEWED_LANGS): check them before the
# first client in any of those languages.
TEXT = {
    "sec_ads": _five("איך המודעות ייראו", "How the ads will look", "À quoi ressembleront les annonces", "كيف ستبدو الإعلانات", "Как будут выглядеть объявления"),
    "sec_numbers": _five("הנתונים", "The numbers", "Les chiffres", "الأرقام", "Цифры"),
    "sec_terms": _five("הביטויים", "The search phrases", "Les expressions de recherche", "عبارات البحث", "Поисковые фразы"),
    "sec_reports": _five("דוחות", "Reports", "Rapports", "التقارير", "Отчёты"),
    "sec_strategy": _five("האסטרטגיה", "The strategy", "La stratégie", "الاستراتيجية", "Стратегия"),
    "google_decides": _five(
        "גוגל מחליטה בכל הצגה איזה שילוב להראות, לפי מה שעובד.",
        "Google decides on every showing which combination to show, by what works.",
        "Google décide à chaque affichage quelle combinaison montrer, selon ce qui fonctionne.",
        "جوجل تقرر في كل عرض أي تركيبة تُظهر، بحسب ما ينجح.",
        "Google при каждом показе сам решает, какое сочетание показать, исходя из того, что работает."),
    "example": _five("דוגמה {n} לשילוב אפשרי. זו לא המודעה.", "Example {n} of a possible combination. This is not the ad.",
                     "Exemple {n} de combinaison possible. Ce n'est pas l'annonce.", "مثال {n} على تركيبة ممكنة. هذا ليس الإعلان.",
                     "Пример {n} возможного сочетания. Это не само объявление."),
    "no_campaigns": _five(
        "עוד לא הכנו לכם קמפיין בגוגל, אז אין כאן עדיין מודעות להראות.",
        "We haven't prepared a Google campaign for you yet, so there are no ads to show here.",
        "Nous n'avons pas encore préparé de campagne Google pour vous ; il n'y a donc pas d'annonces à montrer ici.",
        "لم نجهّز لكم حملة على جوجل بعد، فلا توجد إعلانات لعرضها هنا.",
        "Мы ещё не подготовили для вас кампанию в Google, поэтому показывать пока нечего."),
    "video_is_ad": _five("בקמפיין הזה הסרטון הוא המודעה, כמו שהוא.", "In this campaign the video is the ad, as it is.",
                         "Dans cette campagne, la vidéo est l'annonce, telle quelle.", "في هذه الحملة الفيديو هو الإعلان كما هو.",
                         "В этой кампании видео и есть объявление — как есть."),
    "assets_pending": _five("החומרים לקמפיין הזה עוד בהכנה.", "The materials for this campaign are still being prepared.",
                            "Les éléments de cette campagne sont encore en préparation.", "مواد هذه الحملة ما زالت قيد التحضير.",
                            "Материалы для этой кампании ещё готовятся."),
    "st_making": _five("בהכנה.", "Being prepared.", "En préparation.", "قيد التحضير.", "Готовится."),
    "st_incomplete": _five("ההכנה לא הושלמה. הצוות מטפל בזה.", "Preparation was not completed. The team is on it.",
                           "La préparation n'a pas abouti. L'équipe s'en occupe.", "لم يكتمل التحضير. الفريق يعالج ذلك.",
                           "Подготовка не завершена. Команда этим занимается."),
    "st_ready": _five("החומרים מוכנים. הקמפיין עוד לא עלה לאוויר.", "The materials are ready. The campaign is not live yet.",
                      "Les éléments sont prêts. La campagne n'est pas encore en ligne.", "المواد جاهزة. الحملة لم تبدأ بعد.",
                      "Материалы готовы. Кампания ещё не запущена."),
    "st_draft": _five("טיוטה. עוד לא עלה לאוויר.", "A draft. Not live yet.", "Brouillon. Pas encore en ligne.", "مسودة. لم تبدأ بعد.",
                      "Черновик. Ещё не запущено."),
    "st_created": _five("נוצר בחשבון הפרסום שלכם.", "Created in your advertising account.", "Créée dans votre compte publicitaire.",
                        "أُنشئت في حسابكم الإعلاني.", "Создана в вашем рекламном аккаунте."),
    "f_headline": _five("כותרות", "Headlines", "Titres", "العناوين", "Заголовки"),
    "f_long_headline": _five("כותרות ארוכות", "Long headlines", "Titres longs", "عناوين طويلة", "Длинные заголовки"),
    "f_description": _five("תיאורים", "Descriptions", "Descriptions", "الأوصاف", "Описания"),
    "f_business_name": _five("שם העסק", "Business name", "Nom de l'entreprise", "اسم العمل", "Название бизнеса"),
    "f_images": _five("תמונות", "Images", "Images", "الصور", "Изображения"),
    "f_logo": _five("לוגו", "Logo", "Logo", "الشعار", "Логотип"),
    "f_video": _five("סרטון", "Video", "Vidéo", "فيديو", "Видео"),
    "f_examples": _five("דוגמאות לשילובים", "Example combinations", "Exemples de combinaisons", "أمثلة على التركيبات", "Примеры сочетаний"),
    "c_not_connected": _five(
        "חשבון הפרסום שלכם בגוגל לא מחובר, אז אין לנו מאיפה לקרוא תוצאות.",
        "Your Google advertising account is not connected, so we have nowhere to read results from.",
        "Votre compte publicitaire Google n'est pas connecté ; nous n'avons donc aucune source de résultats.",
        "حسابكم الإعلاني على جوجل غير مربوط، فلا مصدر لدينا لقراءة النتائج.",
        "Ваш рекламный аккаунт Google не подключён, поэтому нам неоткуда читать результаты."),
    "n_not_connected": _five(
        "מה צריך: לחבר את חשבון הפרסום בגוגל בעמוד הבית, בכרטיס החיבורים. מרגע החיבור המספרים יופיעו כאן.",
        "What it takes: connect the Google advertising account on the home page, in the connections card. The numbers appear here from that moment.",
        "Ce qu'il faut : connecter le compte publicitaire Google sur la page d'accueil, dans la carte des connexions. Les chiffres apparaîtront ici dès ce moment.",
        "المطلوب: ربط الحساب الإعلاني على جوجل من الصفحة الرئيسية في بطاقة الربط. ستظهر الأرقام هنا من تلك اللحظة.",
        "Что нужно: подключить рекламный аккаунт Google на главной странице, в карточке подключений. С этого момента цифры появятся здесь."),
    "c_choose_account": _five(
        "החיבור לגוגל התחיל, אבל עוד לא נבחר חשבון פרסום.",
        "The Google connection was started, but no advertising account was chosen yet.",
        "La connexion à Google a commencé, mais aucun compte publicitaire n'a encore été choisi.",
        "بدأ الربط مع جوجل، لكن لم يُختر حساب إعلاني بعد.",
        "Подключение к Google начато, но рекламный аккаунт ещё не выбран."),
    "n_choose_account": _five(
        "מה צריך: לבחור את החשבון בעמוד הבית, בכרטיס החיבורים.", "What it takes: choose the account on the home page, in the connections card.",
        "Ce qu'il faut : choisir le compte sur la page d'accueil, dans la carte des connexions.",
        "المطلوب: اختيار الحساب من الصفحة الرئيسية في بطاقة الربط.", "Что нужно: выбрать аккаунт на главной странице, в карточке подключений."),
    "c_reconnect": _five(
        "ההרשאה לחשבון הפרסום בגוגל פגה, אז המספרים לא מתעדכנים.",
        "The permission for the Google advertising account has expired, so the numbers are not updating.",
        "L'autorisation du compte publicitaire Google a expiré ; les chiffres ne se mettent donc plus à jour.",
        "انتهت صلاحية الإذن لحساب جوجل الإعلاني، لذلك الأرقام لا تتحدث.",
        "Доступ к рекламному аккаунту Google истёк, поэтому цифры не обновляются."),
    "n_reconnect": _five(
        "מה צריך: לחבר את החשבון מחדש בעמוד הבית, בכרטיס החיבורים.", "What it takes: connect the account again on the home page, in the connections card.",
        "Ce qu'il faut : reconnecter le compte sur la page d'accueil, dans la carte des connexions.",
        "المطلوب: إعادة ربط الحساب من الصفحة الرئيسية في بطاقة الربط.", "Что нужно: подключить аккаунт заново на главной странице, в карточке подключений."),
    "c_failed": _five(
        "כרגע הקריאה מגוגל לא מצליחה. הצוות קיבל התראה.", "Reading from Google is not working right now. The team was alerted.",
        "La lecture depuis Google ne fonctionne pas pour l'instant. L'équipe est prévenue.",
        "القراءة من جوجل لا تنجح حاليًا. تم تنبيه الفريق.", "Сейчас чтение из Google не работает. Команда уведомлена."),
    "n_failed": _five("מה צריך: כלום מצדכם. נעדכן כאן כשזה יחזור.", "What it takes: nothing from you. This updates when it is back.",
                      "Ce qu'il faut : rien de votre côté. La page se mettra à jour au retour.", "المطلوب: لا شيء من جهتكم. ستتحدث الصفحة عند العودة.",
                      "Что нужно: от вас ничего. Страница обновится, когда всё вернётся."),
    "c_no_data": _five("החשבון מחובר, אבל עוד אין בו פעילות לדווח עליה.", "The account is connected, but there is no activity in it to report yet.",
                       "Le compte est connecté, mais il n'y a pas encore d'activité à rapporter.", "الحساب مربوط، لكن لا يوجد فيه نشاط بعد.",
                       "Аккаунт подключён, но активности для отчёта в нём пока нет."),
    "stale": _five(
        "המספרים עודכנו לאחרונה ב-{date}. מאז הם לא התעדכנו, אז ייתכן שהמצב היום שונה.",
        "These numbers were last updated on {date}. They have not updated since, so today may look different.",
        "Ces chiffres ont été mis à jour pour la dernière fois le {date}. Ils n'ont pas bougé depuis ; la situation actuelle peut différer.",
        "آخر تحديث لهذه الأرقام كان في {date}. لم تتحدث منذ ذلك الحين، فقد يختلف الوضع اليوم.",
        "Эти цифры обновлялись в последний раз {date}. С тех пор они не менялись, сегодня картина может быть другой."),
    "updated": _five("עודכן ב-{date}. הנתונים מגיעים עד {until}.", "Updated on {date}. The data runs up to {until}.",
                     "Mis à jour le {date}. Les données vont jusqu'au {until}.", "حُدّث في {date}. البيانات حتى {until}.",
                     "Обновлено {date}. Данные по {until}."),
    "b_title": _five("התקציב", "The budget", "Le budget", "الميزانية", "Бюджет"),
    "b_committed": _five("התקציב החודשי שסיכמנו", "The monthly budget we agreed", "Le budget mensuel convenu", "الميزانية الشهرية المتفق عليها", "Согласованный месячный бюджет"),
    "b_spent": _five("הוצא החודש עד עכשיו", "Spent so far this month", "Dépensé ce mois-ci à ce jour", "ما صُرف هذا الشهر حتى الآن", "Потрачено в этом месяце"),
    "b_remaining": _five("נשאר", "Remaining", "Reste", "المتبقي", "Осталось"),
    "b_rate": _five("קצב יומי (ממוצע של 7 ימים)", "Daily pace (7-day average)", "Rythme quotidien (moyenne sur 7 jours)", "المعدل اليومي (متوسط 7 أيام)", "В день (среднее за 7 дней)"),
    "b_pace": _five("בקצב הזה התקציב יספיק לעוד", "At this pace the budget lasts another", "À ce rythme, le budget tient encore", "بهذا المعدل تكفي الميزانية لمدة", "В таком темпе бюджета хватит ещё на"),
    "b_month": _five("ימים שנשארו בחודש", "Days left in the month", "Jours restants dans le mois", "الأيام المتبقية في الشهر", "Дней до конца месяца"),
    "days": _five("{n} ימים", "{n} days", "{n} jours", "{n} يومًا", "{n} дн."),
    "b_none": _five("לא רשום אצלנו תקציב חודשי מוסכם, אז אין ממה לחשב כמה נשאר.",
                    "No agreed monthly budget is on record with us, so there is nothing to work out what is left from.",
                    "Aucun budget mensuel convenu n'est enregistré chez nous ; impossible de calculer ce qui reste.",
                    "لا توجد لدينا ميزانية شهرية متفق عليها مسجلة، فلا يمكن حساب المتبقي.",
                    "У нас не записан согласованный месячный бюджет, поэтому посчитать остаток не из чего."),
    "b_over": _five("ההוצאה עברה את התקציב שסיכמנו.", "Spending has passed the budget we agreed.", "Les dépenses ont dépassé le budget convenu.",
                    "تجاوز الصرف الميزانية المتفق عليها.", "Расходы превысили согласованный бюджет."),
    "r_title": _five("התוצאות, 30 הימים האחרונים", "The results, last 30 days", "Les résultats, 30 derniers jours", "النتائج، آخر 30 يومًا", "Результаты за последние 30 дней"),
    "lane_system": _five("מה שהביאו הקמפיינים שאנחנו מנהלים לפי התוכנית", "What the campaigns we run under the plan brought",
                         "Ce qu'ont apporté les campagnes que nous menons selon le plan", "ما حققته الحملات التي نديرها وفق الخطة",
                         "Что принесли кампании, которые мы ведём по плану"),
    "lane_client": _five("מה שהביאו הקמפיינים שביקשתם בנוסף", "What the campaigns you asked for on top brought",
                         "Ce qu'ont apporté les campagnes que vous avez demandées en plus", "ما حققته الحملات التي طلبتموها إضافةً",
                         "Что принесли кампании, которые вы попросили дополнительно"),
    "lane_unrecorded": _five("קמפיינים שנוצרו לפני שהתחלנו לרשום מי יזם אותם", "Campaigns created before we began recording who initiated them",
                             "Campagnes créées avant que nous notions qui les a lancées", "حملات أُنشئت قبل أن نبدأ بتسجيل من بادر بها",
                             "Кампании, созданные до того, как мы начали записывать, кто их инициировал"),
    "lanes_note": _five("כל מסלול מוצג בנפרד. אנחנו לא מחברים אותם למספר אחד.", "Each track is shown on its own. We never add them into one number.",
                        "Chaque volet est présenté séparément. Nous ne les additionnons jamais.", "كل مسار يُعرض منفردًا. لا نجمعها في رقم واحد.",
                        "Каждое направление показано отдельно. Мы не складываем их в одно число."),
    "lane_empty": _five("לא הייתה פעילות במסלול הזה בתקופה.", "There was no activity on this track in the period.",
                        "Aucune activité sur ce volet pendant la période.", "لم يكن هناك نشاط في هذا المسار خلال الفترة.",
                        "В этом направлении за период активности не было."),
    "lanes_unreadable": _five("כרגע אי אפשר לקרוא את הפירוט לפי קמפיין, אז התוצאות לא מוצגות. לא נציג מספר מעורבב במקומן.",
                              "The per-campaign breakdown can't be read right now, so the results are not shown. We will not show a blended number instead.",
                              "Le détail par campagne est illisible pour l'instant ; les résultats ne sont donc pas affichés. Nous ne montrerons pas un chiffre mélangé à la place.",
                              "تعذّرت قراءة التفصيل حسب الحملة الآن، لذلك لا تُعرض النتائج. لن نعرض رقمًا مخلوطًا بدلًا منها.",
                              "Разбивку по кампаниям сейчас прочитать не удаётся, поэтому результаты не показаны. Смешанное число вместо них мы не покажем."),
    "m_cost": _five("הוצאה", "Spend", "Dépense", "الصرف", "Расход"),
    "m_per_result": _five("עלות לכל אחת", "Cost for each one", "Coût pour chacune", "التكلفة لكل واحدة", "Стоимость каждой"),
    "m_clicks": _five("קליקים על המודעות", "Clicks on the ads", "Clics sur les annonces", "النقرات على الإعلانات", "Клики по объявлениям"),
    "m_none_yet": _five("עוד אין", "None yet", "Aucune pour l'instant", "لا يوجد بعد", "Пока нет"),
    "t_title": _five("המגמה", "The trend", "La tendance", "الاتجاه", "Динамика"),
    "t_up": _five("{pct}% יותר מ-30 הימים שלפני כן", "{pct}% more than the 30 days before", "{pct} % de plus que les 30 jours précédents",
                  "أكثر بـ {pct}% من الـ 30 يومًا السابقة", "на {pct}% больше, чем за предыдущие 30 дней"),
    "t_down": _five("{pct}% פחות מ-30 הימים שלפני כן", "{pct}% less than the 30 days before", "{pct} % de moins que les 30 jours précédents",
                    "أقل بـ {pct}% من الـ 30 يومًا السابقة", "на {pct}% меньше, чем за предыдущие 30 дней"),
    "t_flat": _five("בלי שינוי מול 30 הימים שלפני כן", "No change against the 30 days before", "Stable par rapport aux 30 jours précédents",
                    "بلا تغيير مقارنةً بالـ 30 يومًا السابقة", "без изменений к предыдущим 30 дням"),
    "t_none": _five("אין עדיין תקופה קודמת להשוות אליה.", "There is no earlier period to compare with yet.",
                    "Pas encore de période précédente à comparer.", "لا توجد فترة سابقة للمقارنة بعد.", "Пока нет предыдущего периода для сравнения."),
    "t_mixed": _five("המגמה לא מוצגת: יש פעילות ביותר ממסלול אחד, ומגמה אחת הייתה מערבבת ביניהם.",
                     "The trend is not shown: more than one track was active, and a single trend would blend them.",
                     "La tendance n'est pas affichée : plusieurs volets étaient actifs, et une seule tendance les mélangerait.",
                     "الاتجاه غير معروض: هناك نشاط في أكثر من مسار، واتجاه واحد سيخلط بينها.",
                     "Динамика не показана: активность была более чем в одном направлении, и общая динамика смешала бы их."),
    "k_intro": _five("אלה הביטויים שאנחנו משלמים כדי להופיע בהם בחיפוש בגוגל.", "These are the phrases we pay to appear for in Google search.",
                     "Voici les expressions pour lesquelles nous payons afin d'apparaître dans la recherche Google.",
                     "هذه العبارات التي ندفع لنظهر عند البحث عنها في جوجل.", "Это фразы, за показ по которым в поиске Google мы платим."),
    "k_no_why": _five("ההסבר לכל ביטוי עוד לא נכתב. אפשר לבקש אותו מיואב בצ'אט.", "The reason for each phrase has not been written yet. You can ask Yoav for it in the chat.",
                      "La raison de chaque expression n'a pas encore été rédigée. Vous pouvez la demander à Yoav dans le chat.",
                      "لم يُكتب بعد سبب اختيار كل عبارة. يمكنكم طلبه من يوآف في الدردشة.",
                      "Причина выбора каждой фразы ещё не написана. Её можно попросить у Йоава в чате."),
    "k_all": _five("כל הביטויים", "All the phrases", "Toutes les expressions", "كل العبارات", "Все фразы"),
    "k_none": _five("אין כרגע קמפיין חיפוש, אז אין רשימת ביטויים להראות.", "There is no search campaign right now, so there is no phrase list to show.",
                    "Il n'y a pas de campagne de recherche pour l'instant ; aucune liste d'expressions à montrer.",
                    "لا توجد حملة بحث حاليًا، فلا قائمة عبارات لعرضها.", "Сейчас нет поисковой кампании, поэтому списка фраз нет."),
    "k_themes": _five(
        "לקמפיין הזה אין רשימת ביטויים. גוגל מחליטה בעצמה מתי להציג אותו. מה שאנחנו נותנים לה הם נושאי חיפוש: רמז על מי מתאים לנו, לא התחייבות להופיע בחיפוש מסוים.",
        "This campaign has no phrase list. Google decides by itself when to show it. What we give it are search themes: a hint about who suits us, not a commitment to appear for any particular search.",
        "Cette campagne n'a pas de liste d'expressions. Google décide seul quand la montrer. Nous lui donnons des thèmes de recherche : une indication sur qui nous convient, pas un engagement d'apparaître sur une recherche précise.",
        "ليس لهذه الحملة قائمة عبارات. جوجل تقرر بنفسها متى تعرضها. ما نعطيه لها هو مواضيع بحث: إشارة إلى من يناسبنا، لا التزام بالظهور عند بحث معيّن.",
        "У этой кампании нет списка фраз. Google сам решает, когда её показывать. Мы даём ему темы поиска: подсказку о том, кто нам подходит, а не обязательство показываться по конкретному запросу."),
    "k_themes_none": _five("לא הגדרנו לקמפיין הזה נושאי חיפוש.", "We have not set search themes for this campaign.",
                           "Nous n'avons pas défini de thèmes de recherche pour cette campagne.", "لم نحدد مواضيع بحث لهذه الحملة.",
                           "Темы поиска для этой кампании мы не задавали."),
    "k_themes_title": _five("נושאי חיפוש (רמז לגוגל, לא רשימת ביטויים)", "Search themes (a hint to Google, not a phrase list)",
                            "Thèmes de recherche (une indication pour Google, pas une liste d'expressions)", "مواضيع البحث (إشارة لجوجل، لا قائمة عبارات)",
                            "Темы поиска (подсказка для Google, а не список фраз)"),
    "x_title": _five("ביטויים שאנחנו בכוונה לא משלמים עליהם", "Phrases we deliberately do not pay for", "Expressions pour lesquelles nous ne payons volontairement pas",
                     "عبارات لا ندفع مقابلها عن قصد", "Фразы, за которые мы намеренно не платим"),
    "x_none": _five("עוד לא הוצאנו ביטויים. כשנוציא, הם יופיעו כאן עם הסיבה.", "We have not excluded any phrase yet. When we do, it appears here with the reason.",
                    "Nous n'avons encore exclu aucune expression. Le cas échéant, elle apparaîtra ici avec la raison.",
                    "لم نستبعد أي عبارة بعد. عند الاستبعاد ستظهر هنا مع السبب.", "Мы пока не исключали фразы. Когда исключим, они появятся здесь с причиной."),
    "x_pending": _five("הוחלט להוציא. השינוי בחשבון עוד לא בוצע.", "Decided: to be excluded. The change in the account has not been made yet.",
                       "Décidé : à exclure. Le changement dans le compte n'a pas encore été fait.", "تقرر الاستبعاد. لم يُنفذ التغيير في الحساب بعد.",
                       "Решено исключить. Изменение в аккаунте ещё не внесено."),
    "x_done": _five("הוצא מהחשבון.", "Excluded in the account.", "Exclue dans le compte.", "استُبعدت في الحساب.", "Исключено в аккаунте."),
    "g_hint": _five(
        "אתם מכירים את התחום שלכם טוב מאיתנו. אם ביטוי אומר אצלכם משהו אחר, או שזה שם של מתחרה, סמנו אותו וכתבו למה. ההחלטה נשארת אצל יואב.",
        "You know your field better than we do. If a phrase means something else in your field, or is a competitor's name, mark it and say why. The decision stays with Yoav.",
        "Vous connaissez votre domaine mieux que nous. Si une expression signifie autre chose chez vous, ou si c'est le nom d'un concurrent, signalez-la et dites pourquoi. La décision reste à Yoav.",
        "أنتم تعرفون مجالكم أفضل منا. إذا كانت عبارة تعني شيئًا آخر عندكم، أو كانت اسم منافس، أشيروا إليها واكتبوا السبب. القرار يبقى عند يوآف.",
        "Вы знаете свою сферу лучше нас. Если фраза у вас значит другое или это название конкурента, отметьте её и напишите почему. Решение остаётся за Йоавом."),
    "g_button": _five("להעיר על הביטוי", "Comment on this phrase", "Signaler cette expression", "ملاحظة على العبارة", "Замечание к фразе"),
    "g_placeholder": _five("למה הביטוי הזה לא מתאים?", "Why doesn't this phrase fit?", "Pourquoi cette expression ne convient-elle pas ?", "لماذا لا تناسب هذه العبارة؟", "Почему эта фраза не подходит?"),
    "g_send": _five("לשלוח ליואב", "Send to Yoav", "Envoyer à Yoav", "إرسال إلى يوآف", "Отправить Йоаву"),
    "g_sent": _five("ההערה נשלחה ליואב.", "Your note was sent to Yoav.", "Votre remarque a été envoyée à Yoav.", "أُرسلت ملاحظتكم إلى يوآف.", "Замечание отправлено Йоаву."),
    "g_waiting": _five("הערתם על הביטוי הזה. יואב עוד לא החליט.", "You commented on this phrase. Yoav has not decided yet.",
                       "Vous avez signalé cette expression. Yoav n'a pas encore décidé.", "لديكم ملاحظة على هذه العبارة. يوآف لم يقرر بعد.",
                       "Вы оставили замечание к этой фразе. Йоав ещё не решил."),
    "g_keep": _five("יואב החליט להשאיר:", "Yoav decided to keep it:", "Yoav a décidé de la garder :", "قرر يوآف إبقاءها:", "Йоав решил оставить:"),
    "g_remove": _five("יואב החליט להוציא:", "Yoav decided to exclude it:", "Yoav a décidé de l'exclure :", "قرر يوآف استبعادها:", "Йоав решил исключить:"),
    "e_unknown_term": _five("הביטוי הזה לא נמצא ברשימה.", "That phrase is not on the list.", "Cette expression n'est pas dans la liste.", "هذه العبارة ليست في القائمة.", "Этой фразы нет в списке."),
    "e_reason": _five("כתבו במשפט קצר למה הביטוי לא מתאים.", "Say in a short sentence why the phrase doesn't fit.", "Dites en une phrase courte pourquoi l'expression ne convient pas.",
                      "اكتبوا بجملة قصيرة لماذا لا تناسب العبارة.", "Напишите коротко, почему фраза не подходит."),
    "e_too_many": _five("יש כבר הרבה הערות שמחכות ליואב. נחכה להחלטה עליהן לפני שמוסיפים.", "Many notes are already waiting for Yoav. Let's wait for his decision before adding more.",
                        "Beaucoup de remarques attendent déjà Yoav. Attendons sa décision avant d'en ajouter.", "هناك ملاحظات كثيرة تنتظر يوآف. لننتظر قراره قبل إضافة المزيد.",
                        "Йоава уже ждёт много замечаний. Дождёмся его решения, прежде чем добавлять новые."),
    "e_already": _five("כבר הערתם על הביטוי הזה, וההערה מחכה ליואב.", "You already commented on this phrase, and the note is waiting for Yoav.",
                       "Vous avez déjà signalé cette expression ; la remarque attend Yoav.", "سبق أن أبديتم ملاحظة على هذه العبارة وهي بانتظار يوآف.",
                       "Вы уже оставили замечание к этой фразе, оно ждёт Йоава."),
    "e_storage": _five("לא הצלחנו לשמור כרגע. נסו שוב בעוד כמה דקות.", "We couldn't save right now. Please try again in a few minutes.",
                       "Enregistrement impossible pour l'instant. Réessayez dans quelques minutes.", "تعذّر الحفظ الآن. حاولوا مجددًا بعد دقائق.",
                       "Сейчас сохранить не удалось. Попробуйте через несколько минут."),
    "p_how": _five("מבקשים מיואב בצ'אט דוח, והוא מופיע כאן: לקריאה בעמוד וגם להורדה.", "Ask Yoav in the chat for a report and it appears here: to read on the page and to download.",
                   "Demandez un rapport à Yoav dans le chat et il apparaît ici : à lire sur la page et à télécharger.",
                   "اطلبوا تقريرًا من يوآف في الدردشة وسيظهر هنا: للقراءة في الصفحة وللتنزيل.",
                   "Попросите у Йоава отчёт в чате, и он появится здесь: читать на странице и скачать."),
    "p_none": _five("עוד לא הופק דוח.", "No report has been produced yet.", "Aucun rapport n'a encore été produit.", "لم يُنتج أي تقرير بعد.", "Отчётов пока нет."),
    "p_blocked": _five("כרגע אי אפשר להפיק דוח חדש, כי אין מספרים עדכניים מהחשבון.", "A new report can't be produced right now, because there are no current numbers from the account.",
                       "Impossible de produire un nouveau rapport pour l'instant : pas de chiffres à jour du compte.",
                       "لا يمكن إنتاج تقرير جديد الآن لعدم وجود أرقام محدثة من الحساب.", "Новый отчёт сейчас сделать нельзя: нет свежих цифр из аккаунта."),
    "p_title": _five("דוח גוגל", "Google report", "Rapport Google", "تقرير جوجل", "Отчёт Google"),
    "p_period": _five("{since} עד {until}", "{since} to {until}", "du {since} au {until}", "من {since} إلى {until}", "{since} — {until}"),
    "p_word": _five("הורדה כקובץ וורד", "Download as a Word file", "Télécharger en fichier Word", "تنزيل كملف وورد", "Скачать файлом Word"),
    "p_print": _five("פתיחה להדפסה או לשמירה כקובץ", "Open to print or save as a file", "Ouvrir pour imprimer ou enregistrer", "فتح للطباعة أو الحفظ كملف", "Открыть для печати или сохранения"),
    "p_col_what": _five("מה", "What", "Quoi", "البند", "Что"),
    "p_col_value": _five("כמה", "How much", "Combien", "القيمة", "Сколько"),
    "p_watching": _five("על מה אנחנו מסתכלים", "What we are watching", "Ce que nous surveillons", "ما نراقبه", "За чем мы следим"),
    "s_none": _five("האסטרטגיה עוד לא נכתבה. בקשו מיואב בצ'אט והיא תופיע כאן.", "The strategy has not been written yet. Ask Yoav in the chat and it appears here.",
                    "La stratégie n'a pas encore été rédigée. Demandez-la à Yoav dans le chat et elle apparaîtra ici.",
                    "لم تُكتب الاستراتيجية بعد. اطلبوها من يوآف في الدردشة وستظهر هنا.", "Стратегия ещё не написана. Попросите Йоава в чате, и она появится здесь."),
    "s_money": _five("מה אנחנו עושים עם הכסף, ולמה", "What we are doing with the money, and why", "Ce que nous faisons de l'argent, et pourquoi", "ماذا نفعل بالمال، ولماذا", "Что мы делаем с деньгами и почему"),
    "s_assumes": _five("על מה זה נשען", "What it assumes", "Ce que cela suppose", "على ماذا يستند ذلك", "На чём это основано"),
    "s_watching": _five("על מה אנחנו מסתכלים", "What we are watching", "Ce que nous surveillons", "ما نراقبه", "За чем мы следим"),
    "s_change": _five("מה יגרום לנו לשנות כיוון, ומתי נדע", "What would make us change course, and by when we will know",
                      "Ce qui nous ferait changer de cap, et quand nous le saurons", "ما الذي سيجعلنا نغيّر الاتجاه، ومتى سنعرف", "Что заставит нас сменить курс и когда мы это узнаем"),
    "s_by": _five("נדע עד {date}", "We will know by {date}", "Nous le saurons d'ici le {date}", "سنعرف حتى {date}", "Узнаем до {date}"),
    "s_then": _five("ואז:", "And then:", "Et alors :", "وعندها:", "И тогда:"),
    "s_changed": _five("מה השתנה מהגרסה הקודמת", "What changed from the previous version", "Ce qui a changé depuis la version précédente", "ما الذي تغيّر عن النسخة السابقة", "Что изменилось по сравнению с прошлой версией"),
    "s_previous": _five("גרסאות קודמות", "Previous versions", "Versions précédentes", "النسخ السابقة", "Предыдущие версии"),
    "s_written": _five("נכתב ב-{date}", "Written on {date}", "Rédigé le {date}", "كُتبت في {date}", "Написано {date}"),
    "s_by_yoav": _five("כתב: יואב", "Written by Yoav", "Rédigé par Yoav", "كتبها يوآف", "Автор: Йоав"),
}
FIELD_ORDER = ("headline", "long_headline", "description", "business_name")
STATUS_TEXT = {"ordered": "st_making", "in_production": "st_making", "incomplete": "st_incomplete", "handed_over": "st_ready",
               "verified": "st_ready", "drafted": "st_draft", "created": "st_created"}


def t(key: str, lang: str, **values) -> str:
    row = TEXT[key]
    out = row.get(lang) or row["en"]
    for name, value in values.items():
        out = out.replace("{" + name + "}", str(value))
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── The doors to other modules (tests replace these) ────────────────────────

def _db():
    from core import media_order
    return media_order._db()


def _read(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}").limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _write(client_id: int, value: dict):
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": value,
                                        "updated_at": _now().isoformat()}, on_conflict="key").execute()


def _orders(client_id: int) -> list:
    from core import media_order
    return media_order.all_orders(client_id)


def _activity(client_id: int) -> list:
    from agents.client_agent import get_activity
    return get_activity(client_id, limit=300)


def _daily(client_id: int) -> dict:
    from agents import google_ads_agent
    return google_ads_agent.get_daily_metrics(client_id)


def _per_campaign(client_id: int) -> dict:
    from agents import google_ads_agent
    return google_ads_agent.get_campaign_performance(client_id)


def _pending_choice(client_id: int) -> bool:
    try:
        from agents import google_ads_agent
        return google_ads_agent.connection_info(client_id).get("status") == google_ads_agent.PENDING_STATUS
    except Exception:
        return False


def _budget(client_id: int):
    """The monthly budget the client stated (None = not on record; never guessed)."""
    try:
        from agents.budget_agent import get_forecast
        forecast = get_forecast(client_id)
        return float(forecast.get("stated_marketing_budget_ils") or 0) or None if forecast.get("available") else None
    except Exception:
        return None


def _alert(lines: list):
    try:
        from core.agent_base import agent_alert
        agent_alert(SERVICE_NAME, lines)
    except Exception as e:
        print(f"[{SERVICE_NAME}] alert not sent ({type(e).__name__})")


def store(client_id: int) -> dict:
    """The page's own store; {} with "unreadable" when the database did not answer."""
    try:
        return _read(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: store unreadable ({type(e).__name__})")
        return {"unreadable": True}


# ─── The campaigns we know about (our own data, no Google call) ──────────────

def _as_list(value) -> list:
    if isinstance(value, str):
        value = [value]
    return [str(v).strip() for v in value or [] if str(v or "").strip()]


def campaigns(client_id: int) -> list:
    """Every campaign we prepared: the media orders (not the rejected ones) and
    the search campaigns Yoav drafted. Newest first within each source."""
    out = []
    try:
        orders = _orders(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: orders unreadable ({type(e).__name__})")
        orders = []
    for o in orders:
        if o.get("status") == "rejected" or not o.get("campaign_type"):
            continue
        assets = ((o.get("delivery") or {}).get("manifest") or {}).get("assets") or []
        out.append({"id": f"o-{o.get('id')}", "type": o["campaign_type"], "name": str(o.get("offer") or ""), "status": o.get("status") or "ordered",
                    "texts": o.get("texts") or {}, "assets": assets, "keywords": [],
                    "themes": [str(x).strip()[:80] for x in o.get("search_themes") or [] if str(x or "").strip()]})
    try:
        entries = _activity(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: activity unreadable ({type(e).__name__})")
        entries = []
    created = {str((e.get("details") or {}).get("name") or "") for e in entries if e.get("action_type") == "campaign_created"
               and e.get("agent_name") == "google_ads_agent"}
    seen = set()
    for e in entries:
        if e.get("agent_name") != "google_ads_agent" or e.get("action_type") != "google_ads_campaign_drafted":
            continue
        spec = (e.get("result") or {}).get("spec") or {}
        name = str(spec.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        words = []
        for k in spec.get("keywords") or []:
            text = str((k.get("text") if isinstance(k, dict) else k) or "").strip()
            if text and text not in words:
                words.append(text)
        out.append({"id": "s-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10], "type": "search", "name": name,
                    "status": "created" if name in created else "drafted",
                    "texts": {"headline": spec.get("headlines") or [], "description": spec.get("descriptions") or []},
                    "assets": [], "keywords": words, "themes": []})
    return out


def _kind(slot: str) -> str:
    from core import google_ads_formats as gaf
    if slot == "video":
        return "video"
    if slot.startswith("logo"):
        return "logo"
    known = gaf.SLOTS.get(slot)
    return known.kind if known else "image"


def _slot_label(slot: str, lang: str) -> str:
    try:
        from core import media_order
        return media_order.slot_label(slot, lang)
    except Exception:
        return ""


def ads_view(found: list, lang: str) -> dict:
    """Section 1. Each campaign: its whole asset set, then example
    combinations — never one rendering presented as the ad."""
    out = []
    for c in found:
        texts = {f: _as_list((c.get("texts") or {}).get(f)) for f in FIELD_ORDER}
        images, logos, videos = [], [], []
        for a in c.get("assets") or []:
            slot = str(a.get("slot") or "")
            kind = _kind(slot)
            if kind == "video":
                if a.get("youtube_url"):
                    videos.append(str(a["youtube_url"]))
            elif a.get("file_id"):
                (logos if kind == "logo" else images).append(
                    {"url": THUMB.format(id=a["file_id"]), "label": _slot_label(slot, lang)})
        assembled = c["type"] in ASSEMBLED
        examples = []
        if assembled and texts["headline"] and texts["description"]:
            for i in range(min(3, max(len(texts["headline"]), len(texts["description"]), len(images)))):
                examples.append({"label": t("example", lang, n=i + 1),
                                 "headline": texts["headline"][i % len(texts["headline"])],
                                 "description": texts["description"][i % len(texts["description"])],
                                 "image": images[i % len(images)]["url"] if images else ""})
        has_any = bool(images or logos or videos or any(texts.values()))
        out.append({
            "id": c["id"], "title": client_speech.words(c["type"], lang) or c["type"],
            "name": client_speech.plain(c.get("name") or "", lang)[:120],
            "status": t(STATUS_TEXT.get(c.get("status"), "st_making"), lang),
            "assembled": assembled,
            "note": "" if has_any and assembled else (t("video_is_ad", lang) if videos and not assembled else
                                                      "" if has_any else t("assets_pending", lang)),
            "texts": [{"label": t("f_" + f, lang), "lines": texts[f]} for f in FIELD_ORDER if texts[f]],
            "images": images, "logos": logos, "videos": videos, "examples": examples})
    return {"title": t("sec_ads", lang), "campaigns": out, "empty": "" if out else t("no_campaigns", lang),
            "google_decides": t("google_decides", lang) if any(c["assembled"] for c in out) else "",
            "labels": {k: t("f_" + k, lang) for k in ("images", "logo", "video", "examples")}}


# ─── Section 2: the numbers ──────────────────────────────────────────────────

def _window(days: list, first: date, last: date) -> dict:
    out = {"cost": 0.0, "results": 0.0, "clicks": 0, "shown": 0}
    for d in days:
        try:
            day = date.fromisoformat(str(d.get("date") or ""))
        except ValueError:
            continue
        if first <= day <= last:
            out["cost"] += float(d.get("cost") or 0)
            out["results"] += float(d.get("conversions") or 0)
            out["clicks"] += int(d.get("clicks") or 0)
            out["shown"] += int(d.get("impressions") or 0)
    return out


def recorded_lanes(entries: list) -> dict:
    """campaign id -> the lane RECORDED when it was created. Nothing is inferred."""
    from core import governance
    out = {}
    for e in entries or []:
        if e.get("action_type") != "campaign_created":
            continue
        campaign_id = str((e.get("result") or {}).get("campaign_id") or "")
        lane = governance.clean_lane((e.get("details") or {}).get("lane"))
        if campaign_id and lane:
            out.setdefault(campaign_id, lane)
    return out


def lanes_of(per_campaign: dict, recorded: dict):
    """Per lane, SEPARATELY, from the per-campaign read. None when that read
    is not usable — the caller then shows no results rather than a blend."""
    if not per_campaign.get("connected") or per_campaign.get("error") or per_campaign.get("needs_reconnect"):
        return None
    out = {lane: {"campaigns": 0, "cost": 0.0, "results": 0.0, "clicks": 0, "shown": 0} for lane in LANES}
    for c in per_campaign.get("campaigns") or []:
        if not (c.get("impressions") or c.get("clicks") or c.get("cost") or c.get("conversions")):
            continue
        row = out[recorded.get(str(c.get("id") or "")) or "unrecorded"]
        row["campaigns"] += 1
        row["cost"] += float(c.get("cost") or 0)
        row["results"] += float(c.get("conversions") or 0)
        row["clicks"] += int(c.get("clicks") or 0)
        row["shown"] += int(c.get("impressions") or 0)
    return out


def figures(client_id: int, today: date = None) -> dict:
    """The live read: {"state": live | no_data | not_connected | choose_account
    | reconnect | failed, + the raw figures when live}. Language-neutral, so
    the same figures render for any reader and are what a report keeps."""
    try:
        daily = _daily(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: daily read raised ({type(e).__name__})")
        return {"state": "failed"}
    if not daily.get("connected"):
        return {"state": "choose_account" if _pending_choice(client_id) else "not_connected"}
    if daily.get("needs_reconnect"):
        return {"state": "reconnect"}
    if daily.get("error") or not daily.get("until"):
        return {"state": "failed"}
    days = daily.get("days") or []
    if not any(d.get("impressions") or d.get("clicks") or d.get("cost") or d.get("conversions") for d in days):
        return {"state": "no_data", "pulled_at": daily.get("pulled_at") or _now().isoformat(), "until": daily["until"]}
    until = date.fromisoformat(daily["until"])
    month_first = until.replace(day=1)
    next_month = (month_first + timedelta(days=32)).replace(day=1)
    spent = _window(days, month_first, until)["cost"]
    rate = _window(days, until - timedelta(days=6), until)["cost"] / 7
    committed = _budget(client_id)
    remaining = None if committed is None else committed - spent
    current = _window(days, until - timedelta(days=29), until)
    before = _window(days, until - timedelta(days=59), until - timedelta(days=30))
    try:
        lanes = lanes_of(_per_campaign(client_id), recorded_lanes(_activity(client_id)))
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: per-campaign read raised ({type(e).__name__})")
        lanes = None
    return {"state": "live", "pulled_at": daily.get("pulled_at") or _now().isoformat(),
            "since": (until - timedelta(days=29)).isoformat(), "until": until.isoformat(),
            "budget": {"committed": committed, "spent": round(spent, 2), "remaining": None if remaining is None else round(remaining, 2),
                       "rate": round(rate, 2), "month_days_left": (next_month - until).days - 1,
                       "pace_days": int(remaining / rate) if remaining is not None and remaining > 0 and rate > 0 else None},
            "lanes": lanes, "current": current, "before": before}


def _trend(now: float, before: float, lang: str) -> str:
    if before <= 0:
        return t("t_none", lang)
    pct = round((now - before) / before * 100)
    return t("t_flat", lang) if pct == 0 else t("t_up" if pct > 0 else "t_down", lang, pct=abs(pct))


def _lane_rows(row: dict, result_word: str, lang: str) -> list:
    money, number = client_language.fmt_money, client_language.fmt_number
    results = row["results"]
    rows = [{"label": t("m_cost", lang), "value": money(round(row["cost"]), lang)},
            {"label": result_word, "value": number(round(results, 1), lang) if results else t("m_none_yet", lang)}]
    if results:
        rows.append({"label": t("m_per_result", lang), "value": money(round(row["cost"] / results), lang)})
    rows.append({"label": t("m_clicks", lang), "value": number(row["clicks"], lang)})
    if row["shown"]:
        rows.append({"label": client_speech.words("ctr", lang), "value": number(round(row["clicks"] / row["shown"] * 100, 1), lang) + "%"})
    if row["clicks"]:
        rows.append({"label": client_speech.words("cpc", lang), "value": money(round(row["cost"] / row["clicks"], 1), lang)})
    return rows


def render_figures(found: dict, lang: str, client_id=None) -> dict:
    """The figures in the reader's language: budget rows, then one block PER
    LANE. There is deliberately no total across lanes."""
    money = client_language.fmt_money
    client_speech.speak_for(client_id)
    try:
        result_word = client_speech.words("conversion", lang)
        b = found.get("budget") or {}
        budget = []
        if b.get("committed") is not None:
            budget.append({"label": t("b_committed", lang), "value": money(round(b["committed"]), lang)})
        budget.append({"label": t("b_spent", lang), "value": money(round(b.get("spent") or 0), lang)})
        if b.get("remaining") is not None:
            budget.append({"label": t("b_remaining", lang), "value": money(round(max(b["remaining"], 0)), lang)})
        budget.append({"label": t("b_rate", lang), "value": money(round(b.get("rate") or 0), lang)})
        if b.get("pace_days") is not None:
            budget.append({"label": t("b_pace", lang), "value": t("days", lang, n=b["pace_days"])})
        budget.append({"label": t("b_month", lang), "value": t("days", lang, n=b.get("month_days_left") or 0)})
        note = t("b_none", lang) if b.get("committed") is None else t("b_over", lang) if (b.get("remaining") or 0) < 0 else ""
        lanes = found.get("lanes")
        blocks, trend = [], ""
        if lanes is not None:
            active = [lane for lane in LANES if lanes[lane]["campaigns"]]
            for lane in LANES:
                row = lanes[lane]
                if lane == "unrecorded" and not row["campaigns"]:
                    continue
                blocks.append({"lane": lane, "title": t("lane_" + lane, lang),
                               "rows": _lane_rows(row, result_word, lang) if row["campaigns"] else [],
                               "empty": "" if row["campaigns"] else t("lane_empty", lang)})
            if len(active) == 1:
                trend = result_word + ": " + _trend(found["current"]["results"], found["before"]["results"], lang)
                for block in blocks:
                    if block["lane"] == active[0]:
                        block["trend"] = trend
            elif len(active) > 1:
                trend = t("t_mixed", lang)
        return {"budget_title": t("b_title", lang), "budget": budget, "budget_note": note,
                "results_title": t("r_title", lang), "lanes": blocks,
                "lanes_note": t("lanes_note", lang) if lanes is not None else t("lanes_unreadable", lang),
                "trend_title": t("t_title", lang), "trend_mixed": trend if lanes is not None and len(active) > 1 else ""}
    finally:
        client_speech.speak_for(None)


def numbers_view(client_id: int, lang: str, kept: dict, today: date = None, remember=True) -> dict:
    """Section 2 as shown. Live figures are remembered (at most hourly); when
    the read fails the remembered ones are shown WITH their date and said to
    be old — and when there are none, what is missing and what it takes."""
    found = figures(client_id, today)
    state = found["state"]
    out = {"title": t("sec_numbers", lang), "state": state}
    if state == "live":
        last = (kept.get("numbers") or {}).get("pulled_at") or ""
        if remember and not kept.get("unreadable") and (not last or last < (_now() - timedelta(seconds=SNAPSHOT_SECONDS)).isoformat()):
            try:
                fresh = _read(client_id)
                fresh["numbers"] = found
                _write(client_id, fresh)
            except Exception as e:
                print(f"[{SERVICE_NAME}] client {client_id}: figures not remembered ({type(e).__name__})")
        return {**out, **render_figures(found, lang, client_id), "stale": False,
                "updated": t("updated", lang, date=client_language.fmt_date(found["pulled_at"], lang),
                             until=client_language.fmt_date(found["until"], lang))}
    if state == "no_data":
        return {**out, "missing": t("c_no_data", lang), "needs": ""}
    out["missing"] = t("c_" + state, lang)
    out["needs"] = t("n_" + state, lang)
    old = kept.get("numbers") or {}
    if old.get("state") == "live":
        return {**out, **render_figures(old, lang, client_id), "stale": True,
                "updated": t("stale", lang, date=client_language.fmt_date(old.get("pulled_at"), lang))}
    return out


# ─── Section 3: the phrases ──────────────────────────────────────────────────

def terms_key(words: list) -> str:
    return hashlib.sha1("\n".join(sorted(words)).encode("utf-8")).hexdigest()[:16]


def terms_view(found: list, kept: dict, lang: str, seen: dict = None) -> dict:
    flags = {(f.get("campaign"), f.get("term")): f for f in reversed(kept.get("flags") or [])}
    written = kept.get("terms") or {}
    blocks = []
    for c in found:
        title = client_speech.words(c["type"], lang) or c["type"]
        name = client_speech.plain(c.get("name") or "", lang)[:120]
        if c["type"] == "search" and c.get("keywords"):
            notes = (written.get(c["id"]) or {})
            explained = notes.get("key") == terms_key(c["keywords"])
            groups = notes.get("themes") if explained else [{"theme": t("k_all", lang), "terms": [{"text": w, "why": ""} for w in c["keywords"]]}]
            shown = []
            for group in groups or []:
                rows = []
                for term in group.get("terms") or []:
                    flag = flags.get((c["id"], term.get("text"))) or {}
                    status = flag.get("status") or ""
                    line = (t("g_waiting", lang) if status == "waiting" else
                            f"{t('g_' + status, lang)} {flag.get('why') or ''}".strip() if status in ("keep", "remove") else "")
                    rows.append({"text": term.get("text") or "", "why": term.get("why") or "", "flag": status, "flag_line": line,
                                 "can_flag": status != "waiting"})
                shown.append({"theme": group.get("theme") or "", "terms": rows})
            blocks.append({"campaign": c["id"], "title": title, "name": name, "kind": "phrases", "intro": t("k_intro", lang),
                           "note": "" if explained else t("k_no_why", lang), "groups": shown})
        elif c["type"] in THEMES_ONLY:
            blocks.append({"campaign": c["id"], "title": title, "name": name, "kind": "themes", "intro": t("k_themes", lang),
                           "themes_title": t("k_themes_title", lang), "themes": c.get("themes") or [],
                           "note": "" if c.get("themes") else t("k_themes_none", lang)})
    excluded = [{"text": x.get("text") or "", "why": x.get("why") or "",
                 "state": t("x_done" if x.get("applied") else "x_pending", lang)} for x in kept.get("excluded") or []]
    # ...and what is ALREADY excluded in the account, read from what people searched (core/search_reports)
    listed = {x["text"] for x in excluded}
    excluded += [{"text": x["text"], "why": x["why"], "state": t("x_done", lang)}
                 for x in (seen or {}).get("excluded_in_account") or [] if x["text"] not in listed]
    from core import search_reports
    to_add = [{"text": x.get("text") or "", "why": x.get("why") or "",
               "state": search_reports.t("add_done" if x.get("applied") else "add_pending", lang)} for x in kept.get("to_add") or []]
    return {"title": t("sec_terms", lang), "blocks": blocks, "to_add_title": search_reports.t("add_title", lang), "to_add": to_add,
            "empty": "" if any(b["kind"] == "phrases" for b in blocks) else t("k_none", lang),
            "flag": {"hint": t("g_hint", lang), "button": t("g_button", lang), "placeholder": t("g_placeholder", lang),
                     "send": t("g_send", lang), "sent": t("g_sent", lang)},
            "excluded_title": t("x_title", lang), "excluded": excluded, "excluded_empty": "" if excluded else t("x_none", lang)}


def flag(client_id: int, campaign: str, term: str, reason: str) -> dict:
    """The client's note on ONE phrase. It changes nothing: it waits for Yoav,
    who decides (`write_strategy` reads every waiting note). PageError names
    what was wrong."""
    term, reason = str(term or "").strip(), " ".join(str(reason or "").split())
    target = next((c for c in campaigns(client_id) if c["id"] == str(campaign or "") and term in (c.get("keywords") or [])), None)
    if not target:
        raise PageError("unknown_term")
    if not REASON_MIN <= len(reason) <= REASON_MAX:
        raise PageError("reason")
    try:
        kept = _read(client_id)
    except Exception as e:
        raise PageError("storage", type(e).__name__)
    waiting = [f for f in kept.get("flags") or [] if f.get("status") == "waiting"]
    if any(f.get("campaign") == target["id"] and f.get("term") == term for f in waiting):
        raise PageError("already")
    if len(waiting) >= MAX_OPEN_FLAGS:
        raise PageError("too_many")
    row = {"id": uuid.uuid4().hex[:10], "campaign": target["id"], "term": term, "reason": reason, "at": _now().isoformat(), "status": "waiting"}
    kept["flags"] = ((kept.get("flags") or []) + [row])[-200:]
    try:
        _write(client_id, kept)
    except Exception as e:
        raise PageError("storage", type(e).__name__)
    _alert([f"client {client_id}: the client flagged the search phrase \"{term[:80]}\" on the Google page — \"{reason[:200]}\". "
            f"It waits for the strategy to be rewritten (write_google_strategy), which decides it."])
    return row


def error_text(code: str, lang: str) -> str:
    key = {"unknown_term": "e_unknown_term", "reason": "e_reason", "too_many": "e_too_many", "already": "e_already"}.get(code, "e_storage")
    return t(key, lang)


# ─── Section 5: the strategy, written by Yoav ────────────────────────────────

STRATEGY_SYSTEM = """You are Yoav, the Google advertising specialist of uallak, an Israeli marketing agency. You write the STRATEGY
the client reads on their Google page. It is the one place where the agency says, in advance and checkably, what it is doing
with their money and what would make it stop.

You receive: the business, the monthly budget (null = none is on record: say so, never invent one), the campaigns we prepared
(type, what is advertised, the search phrases of a search campaign), the latest figures when the account is connected (null =
no figures: plan from what is known and say the plan rests on no account data yet), the previous strategy when there is one,
the client's notes on single search phrases, today's date, and "search_reports" when the account was read: what people
actually typed when a search ad was shown (with candidates to exclude and to add — present ONLY when the account measures
results) and, per campaign, how much of the relevant searches we appeared in, ALWAYS split into what was lost to budget and
what was lost to ranking.

Write in the language named in "language". Plain words a business owner uses. NEVER a platform's vocabulary: no product
names, no acronyms, no metric jargon, no names of our internals. Name a campaign by where it appears ("ads in Google search").
Never promise a result. An expected result is never a bare number: an estimate is a RANGE said to be our own estimate.
Never invent a fact about the business, a price, or a market statistic. Never cite a source you were not given.

Return JSON only:
{"money": "what we are doing with the money and why THIS split — 2 to 5 sentences",
 "assumes": ["what this assumes about the business and the market — 1 to 4 short sentences, each one checkable"],
 "watching": [{"number": "the plain name of ONE number we watch", "why": "what it tells us"}],
 "change_course": [{"if": "the observable condition that means this is not working",
                    "by": "YYYY-MM-DD — the date by which we will know",
                    "then": "what we do instead"}],
 "changed": "ONLY when a previous strategy is given: what changed and why, 1 to 3 sentences; else empty",
 "themes": {"<campaign id>": [{"theme": "a short name for a group of phrases",
                               "terms": [{"text": "the phrase EXACTLY as given", "why": "why we pay for it, one sentence"}]}]},
 "flag_decisions": [{"id": "the note's id", "decision": "keep" | "remove", "why": "one sentence the client reads"}],
 "term_decisions": [{"id": "the candidate's id", "decision": "exclude" | "add" | "leave", "why": "one sentence the client reads"}]}

Rules that are checked: "watching" has 2 or 3 entries. "change_course" has at least one entry and every "by" is a real date
after today and within 180 days. Every search phrase given appears in exactly one theme of its campaign; you never add a
phrase. Decide EVERY client note: their knowledge of their field is valuable, the decision is yours — remove a phrase that
means something else in their field or is a competitor's name, keep one that is right and say why.

The two search reports (search_reports): decide EVERY exclude candidate and add candidate by its id — read for waste first,
opportunity second; "leave" needs its reason too. Never decide a term that is not a candidate, and when the conclusions are
withheld (the account is not measuring results) decide nothing and conclude nothing about which searches work. The share of
searches is never one number: lost to budget means the campaign is too small and more money buys more; lost to ranking means
the ads and phrases do not fit well enough and more money only spends faster. NEVER propose more budget for a campaign whose
leading loss is ranking or both; say what is fixed first. Where no share is reported for a campaign, say so, never a zero."""


def _line(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean_strategy(raw: dict, found: list, waiting: list, previous: dict, lang: str, today: date = None, speak=None,
                   candidates: dict = None) -> dict:
    """The model's answer → {"version", "terms", "decisions"} or PageError.
    A strategy with no DATED abandon condition is refused: it would be a wish."""
    today = today or date.today()
    raw = raw if isinstance(raw, dict) else {}
    if speak is None:
        def speak(text):
            return client_speech.enforce(text, lang, where="google_strategy", agent="google_strategy")

    def said(value, limit):
        text = _line(value, limit)
        if not text:
            return ""
        try:
            return speak(text)
        except client_speech.Blocked as e:
            raise PageError("speech", str(e)[:200])
    money = said(raw.get("money"), 900)
    if len(money) < 40:
        raise PageError("money_missing")
    assumes = [x for x in (said(a, 300) for a in _as_list(raw.get("assumes"))[:4]) if x]
    if not assumes:
        raise PageError("assumes_missing")
    watching = []
    for w in raw.get("watching") or []:
        if isinstance(w, dict) and _line(w.get("number"), 80) and _line(w.get("why"), 300):
            watching.append({"number": said(w.get("number"), 80), "why": said(w.get("why"), 300)})
    if not 2 <= len(watching) <= 3:
        raise PageError("watching", f"{len(watching)} numbers, 2 or 3 are needed")
    change = []
    for c in raw.get("change_course") or []:
        if not isinstance(c, dict) or not _line(c.get("if"), 300) or not _line(c.get("then"), 300):
            continue
        try:
            by = date.fromisoformat(str(c.get("by") or "").strip())
        except ValueError:
            raise PageError("abandon_date", "a condition without a real date")
        if not today < by <= today + timedelta(days=MAX_DAYS_TO_KNOW):
            raise PageError("abandon_date", f"{by.isoformat()} is not after today and within {MAX_DAYS_TO_KNOW} days")
        change.append({"if": said(c.get("if"), 300), "by": by.isoformat(), "then": said(c.get("then"), 300)})
    if not change:
        raise PageError("abandon_missing", "no condition that would make us change course")
    changed = said(raw.get("changed"), 500) if previous else ""
    if previous and not changed:
        raise PageError("changed_missing", "a rewritten strategy says what changed")
    terms = {}
    themes = raw.get("themes") if isinstance(raw.get("themes"), dict) else {}
    for c in found:
        if c["type"] != "search" or not c.get("keywords"):
            continue
        left, groups = list(c["keywords"]), []
        for group in themes.get(c["id"]) or []:
            rows = []
            for term in (group or {}).get("terms") or []:
                text = str((term or {}).get("text") or "").strip()
                if text in left:
                    left.remove(text)
                    rows.append({"text": text, "why": said(term.get("why"), 240)})
            if rows:
                groups.append({"theme": said(group.get("theme"), 60) or t("k_all", lang), "terms": rows})
        if left:
            raise PageError("terms_uncovered", f"{len(left)} phrases of {c['id']} have no theme")
        terms[c["id"]] = {"key": terms_key(c["keywords"]), "themes": groups, "written_at": _now().isoformat(), "lang": lang}
    open_ids = {f["id"]: f for f in waiting}
    decisions = []
    for d in raw.get("flag_decisions") or []:
        if isinstance(d, dict) and d.get("id") in open_ids and d.get("decision") in ("keep", "remove") and _line(d.get("why"), 240):
            decisions.append({"id": d["id"], "decision": d["decision"], "why": said(d.get("why"), 240)})
            open_ids.pop(d["id"])
    # The search-terms decisions: only a candidate code produced may be decided (never a term the model picked itself)
    open_terms, term_decisions = dict(candidates or {}), []
    for d in raw.get("term_decisions") or []:
        if isinstance(d, dict) and d.get("id") in open_terms and d.get("decision") in ("exclude", "add", "leave") and _line(d.get("why"), 240):
            item = open_terms.pop(d["id"])
            if d["decision"] != "leave" and d["decision"] != item["group"]:
                continue                       # a wasteful term is never "added", a working one never "excluded"
            term_decisions.append({"id": d["id"], "decision": d["decision"], "why": said(d.get("why"), 240),
                                   "text": item["term"], "campaign": item["campaign"]})
    version = {"written_at": _now().isoformat(), "lang": lang, "money": money, "assumes": assumes, "watching": watching,
               "change_course": change, "changed": changed}
    return {"version": version, "terms": terms, "decisions": decisions, "term_decisions": term_decisions}


def _measurement_block(client_id: int) -> dict:
    try:
        from core import conversion_measurement
        return conversion_measurement.prompt_block(client_id)
    except Exception:
        return {}


def _search_block(client_id: int) -> dict:
    """The two search reports as the strategy is given them (core/search_reports)."""
    try:
        from core import search_reports
        return search_reports.prompt_block(client_id)
    except Exception:
        return {}


def _search_candidates(client_id: int) -> dict:
    try:
        from core import search_reports
        return search_reports.candidates(client_id)
    except Exception:
        return {}


def has_material(client_id: int) -> bool:
    """Something to plan with: a campaign we prepared, or a stated budget."""
    return bool(campaigns(client_id)) or _budget(client_id) is not None


def write_strategy(client_id: int, lang: str = "he", reason: str = "", call=None, knowledge=None, today: date = None) -> dict:
    """Yoav writes (or rewrites) the strategy: ONE model call, checked, stored
    as a NEW version in front of the kept ones. The same call explains every
    search phrase and decides every waiting client note."""
    today = today or date.today()
    found = campaigns(client_id)
    if not found and _budget(client_id) is None:
        raise PageError("nothing_to_plan")
    kept = _read(client_id)
    versions = kept.get("strategy") or []
    previous = versions[0] if versions else {}
    waiting = [f for f in kept.get("flags") or [] if f.get("status") == "waiting"]
    live = figures(client_id, today)
    if knowledge is None:
        try:
            from core import client_knowledge
            knowledge = client_knowledge.profile(client_id, "google")
        except Exception:
            knowledge = {}
    client_speech.speak_for(client_id)
    try:
        payload = {"language": client_language.payload(lang), "today": today.isoformat(), "business": knowledge,
                   "monthly_budget_ils": _budget(client_id), "results_measurement": _measurement_block(client_id),
                   "search_reports": _search_block(client_id),
                   "what_a_result_is_called": client_speech.words("conversion", lang),
                   "campaigns": [{"id": c["id"], "where_it_appears": client_speech.words(c["type"], "en", "long"),
                                  "advertises": c.get("name") or "", "state": c.get("status"), "search_phrases": c.get("keywords") or []}
                                 for c in found],
                   "figures": {k: live.get(k) for k in ("since", "until", "budget", "lanes", "current", "before")} if live["state"] == "live" else None,
                   "previous_strategy": {k: previous.get(k) for k in ("written_at", "money", "assumes", "watching", "change_course")} if previous else None,
                   "why_it_is_rewritten": _line(reason, 400),
                   "client_notes_on_phrases": [{"id": f["id"], "campaign": f["campaign"], "phrase": f["term"], "note": f["reason"]} for f in waiting]}
        if call is None:
            from core.claude_json import safe_claude_json_call

            def call(system, user):
                return safe_claude_json_call(system, user, max_tokens=3500, client_id=client_id, cost_category="claude_ads")
        try:
            raw = call(STRATEGY_SYSTEM + "\n\n" + client_speech.PROMPT_RULE, json.dumps(payload, ensure_ascii=False, default=str))
        except PageError:
            raise
        except Exception as e:
            raise PageError("writing_failed", type(e).__name__)
        cleaned = clean_strategy(raw, found, waiting, previous, lang, today, candidates=_search_candidates(client_id))
    finally:
        client_speech.speak_for(None)
    version = {**cleaned["version"], "n": len(versions) + 1}
    kept["strategy"] = ([version] + versions)[:KEEP_VERSIONS]
    kept["terms"] = {**(kept.get("terms") or {}), **cleaned["terms"]}
    removed = []
    by_id = {d["id"]: d for d in cleaned["decisions"]}
    for f in kept.get("flags") or []:
        d = by_id.get(f.get("id"))
        if d and f.get("status") == "waiting":
            f.update({"status": d["decision"], "why": d["why"], "decided_at": _now().isoformat()})
            if d["decision"] == "remove":
                removed.append({"text": f["term"], "campaign": f["campaign"], "why": d["why"], "decided_at": f["decided_at"], "applied": False})
    # What Yoav decided on the search-terms candidates: this is what fills the excluded list
    stamp, added = _now().isoformat(), []
    known = {(x.get("campaign"), x.get("text")) for x in (kept.get("excluded") or []) + (kept.get("to_add") or [])}
    for d in cleaned.get("term_decisions") or []:
        if d["decision"] == "leave" or (d["campaign"], d["text"]) in known:
            continue
        row = {"text": d["text"], "campaign": d["campaign"], "why": d["why"], "decided_at": stamp, "applied": False,
               "source": "search_terms"}
        (removed if d["decision"] == "exclude" else added).append(row)
    kept["term_decisions"] = ([{**d, "decided_at": stamp} for d in cleaned.get("term_decisions") or []]
                              + (kept.get("term_decisions") or []))[:200]
    kept["to_add"] = ((kept.get("to_add") or []) + added)[-200:]
    kept["excluded"] = ((kept.get("excluded") or []) + removed)[-200:]
    _write(client_id, kept)
    if added:
        _alert([f"client {client_id}: Yoav decided to ADD {len(added)} search phrase(s) from what people actually searched — "
                + "; ".join(f"\"{r['text'][:60]}\"" for r in added)
                + ". Nothing was changed in the Google account: add them there, the page says it is not done yet."])
    if removed:
        _alert([f"client {client_id}: Yoav decided to EXCLUDE {len(removed)} search phrase(s) (client notes and search-terms waste) — "
                + "; ".join(f"\"{r['text'][:60]}\"" for r in removed)
                + ". Nothing was changed in the Google account: apply it there, the page says it is not done yet."])
    return {"version": version["n"], "decided": len(cleaned["decisions"]), "removed": len(removed), "explained": len(cleaned["terms"]),
            "to_add": len(added), "term_decisions": len(cleaned.get("term_decisions") or [])}


def strategy_view(kept: dict, lang: str) -> dict:
    def shown(v):
        return {"n": v.get("n"), "written": t("s_written", lang, date=client_language.fmt_date(v.get("written_at"), lang)),
                "money": v.get("money") or "", "assumes": v.get("assumes") or [], "watching": v.get("watching") or [],
                "change_course": [{"if": c.get("if") or "", "then": c.get("then") or "",
                                   "by": t("s_by", lang, date=client_language.fmt_date(c.get("by"), lang))} for c in v.get("change_course") or []],
                "changed": v.get("changed") or ""}
    versions = [shown(v) for v in kept.get("strategy") or []]
    return {"title": t("sec_strategy", lang), "by": t("s_by_yoav", lang), "current": versions[0] if versions else None,
            "previous": versions[1:], "empty": "" if versions else t("s_none", lang),
            "labels": {k: t("s_" + k, lang) for k in ("money", "assumes", "watching", "change", "then", "changed", "previous")}}


# ─── Section 4: reports ──────────────────────────────────────────────────────

def build_report(client_id: int, lang: str = "he", today: date = None) -> dict:
    """A report = the figures of that day, kept. Only from a LIVE read: a
    report built on old numbers would be a stale number with a date on it."""
    found = figures(client_id, today)
    if found["state"] != "live":
        raise PageError("no_numbers", found["state"])
    kept = _read(client_id)
    current = (kept.get("strategy") or [{}])[0]
    try:        # how much of the demand we captured (always split) and what people searched, as they stood that day
        from core import search_reports
        searched = search_reports.snapshot(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: search reports not kept in the report ({type(e).__name__})")
        searched = {}
    report = {"id": uuid.uuid4().hex[:10], "built_at": _now().isoformat(), "since": found["since"], "until": found["until"],
              "lang": lang, "figures": found, "watching": current.get("watching") or [], "search": searched}
    kept["reports"] = ([report] + (kept.get("reports") or []))[:KEEP_REPORTS]
    _write(client_id, kept)
    return report


def report_table(report: dict, lang: str, client_id=None) -> dict:
    """One stored report as rows — what the page shows and what a download holds."""
    shown = render_figures(report.get("figures") or {}, lang, client_id)
    rows = [[shown["budget_title"], ""]] + [[r["label"], r["value"]] for r in shown["budget"]]
    if shown["budget_note"]:
        rows.append([shown["budget_note"], ""])
    for block in shown["lanes"]:
        rows.append([block["title"], ""])
        rows += [[r["label"], r["value"]] for r in block["rows"]] or [[block["empty"], ""]]
        if block.get("trend"):
            rows.append([shown["trend_title"], block["trend"]])
    if shown["trend_mixed"]:
        rows.append([shown["trend_title"], shown["trend_mixed"]])
    if report.get("watching"):
        rows.append([t("p_watching", lang), ""])
        rows += [[w.get("number") or "", w.get("why") or ""] for w in report["watching"]]
    if report.get("search"):
        from core import search_reports
        rows += search_reports.report_rows(report["search"], lang)
    period = t("p_period", lang, since=client_language.fmt_date(report.get("since"), lang), until=client_language.fmt_date(report.get("until"), lang))
    return {"title": t("p_title", lang), "subtitle": period, "lines": rows,
            # the shape core/export_service takes: (key, header) columns and one dict per row
            "columns": [("what", t("p_col_what", lang)), ("value", t("p_col_value", lang))],
            "rows": [{"what": a, "value": b} for a, b in rows],
            "summary": [shown["lanes_note"]], "rtl": client_language.direction(lang) == "rtl"}


def report(client_id: int, report_id: str) -> dict:
    return next((r for r in _read(client_id).get("reports") or [] if r.get("id") == str(report_id)), {})


def reports_view(client_id: int, kept: dict, lang: str, can_build: bool) -> dict:
    out = []
    for r in kept.get("reports") or []:
        table = report_table(r, lang, client_id)
        out.append({"id": r.get("id"), "period": table["subtitle"], "built": client_language.fmt_date(r.get("built_at"), lang),
                    "rows": table["lines"], "note": table["summary"][0],
                    "view_url": f"/api/client/google/report/{r.get('id')}/view",
                    "word_url": f"/api/client/google/report/{r.get('id')}/export"})
    return {"title": t("sec_reports", lang), "how": t("p_how", lang), "reports": out, "empty": "" if out else t("p_none", lang),
            "blocked": "" if can_build else t("p_blocked", lang),
            "labels": {"word": t("p_word", lang), "print": t("p_print", lang)}}


# ─── The page ────────────────────────────────────────────────────────────────

def _measurement(client_id: int, lang: str, numbers_state: str) -> dict:
    """Are results being measured at all (core/conversion_measurement)? {} when
    the account is not connected or cannot be read now — the numbers section
    already says that, with what it takes."""
    if numbers_state not in ("live", "no_data"):
        return {}
    try:
        from core import conversion_measurement
        return conversion_measurement.view(client_id, lang)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: measurement view failed ({type(e).__name__})")
        return {}


def _searched(client_id: int, lang: str, numbers_state: str, found: list) -> dict:
    """What people searched and how much of the demand we captured
    (core/search_reports). {} when the account is not connected or readable."""
    if numbers_state not in ("live", "no_data"):
        return {}
    try:
        from core import search_reports
        return search_reports.view(client_id, lang)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: search reports view failed ({type(e).__name__})")
        return {}


def view(client_id: int, lang: str = "he", today: date = None, remember=True) -> dict:
    lang = lang if lang in LANGS else "he"
    kept = store(client_id)
    found = campaigns(client_id)
    numbers = numbers_view(client_id, lang, kept, today, remember)
    seen = _searched(client_id, lang, numbers["state"], found)
    return {"language": lang, "direction": client_language.direction(lang),
            "ads": ads_view(found, lang), "numbers": numbers, "measurement": _measurement(client_id, lang, numbers["state"]),
            "terms": terms_view(found, kept, lang, seen), "search_terms": seen.get("terms") or {}, "demand_share": seen.get("share") or {},
            "reports": reports_view(client_id, kept, lang, numbers["state"] == "live"), "strategy": strategy_view(kept, lang)}
