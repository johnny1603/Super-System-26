"""Client ownership & portability — what belongs to the client's business,
and how leaving uallak stays easy and dignified.

Principle: every account, key, asset and record belongs to the CLIENT'S
business. uallak holds ACCESS only, and removing that access never deletes
anything of the client's at a vendor (no campaign, post, video, contact,
voice, file or site is touched — the revokes in api_server._revoke_grant only
kill our own token / Application Password).

Three readers, one source:
- the dashboard page "החשבונות והנכסים שלי" (`accounts_view` + `assets_view`,
  GET /api/client/ownership) — 5 languages;
- the handover document (`handover`, Hebrew) — downloadable by the client at any
  time, and produced by the admin offboarding kit;
- the offboarding kit (`revoke_all`) — handover snapshot first, then our access
  is removed group by group.

SECRETS: nothing here ever reads a secret. Rows come from
client_agent.get_accounts (no secret columns), and even `secret_last4` /
`has_secret` are dropped — the client sees the account's identity only, plus
one line saying the key lives in their own account and can be regenerated
there (`key_note`).

NAMES (UX pass 1): titles are capability language; a vendor is named only where
the client acts on it or pays for it — the CRM / automation identity line and
the handover checklist (where the client logs in to that vendor). The creative
tools' titles come from vendor_tools.client_label.

The disconnect groups themselves (which client_accounts platforms die
together) stay in api_server._DISCONNECT_GROUPS and are passed in;
tests/test_ownership.py asserts GROUPS below covers exactly those groups.
"""
from datetime import datetime, timezone

LANGS = ("he", "en", "fr", "ar", "ru")


def _L(he, en, fr, ar, ru) -> dict:
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def _t(table: dict, lang: str) -> str:
    return table.get(lang) or table["he"]


# ─── Per-group texts ─────────────────────────────────────────────────────────
# kind: oauth (a revocable grant) | app_password (WordPress) | api_key (a key
# the client created in their own account). Creative tools take their title
# from vendor_tools.client_label (title=None here).

_GOOGLE_STEP = ("אחרי שנסיר את הגישה: בהגדרות חשבון Google, בחלק של האפליקציות שיש להן גישה לחשבון, "
                "בודקים ש-uallak כבר לא מופיעה.")
_KEY_STEP = "מחקנו את העותק שלנו של המפתח. כדי לסגור גם מהצד שלכם: נכנסים ל-{vendor}, להגדרות ה-API, ומבטלים את המפתח או יוצרים חדש."

GROUPS = {
    "google_ads": {
        "kind": "oauth",
        "title": _L("פרסום ב-Google Ads", "Google Ads advertising", "Publicité Google Ads",
                    "الإعلان على Google Ads", "Реклама в Google Ads"),
        "access": _L("ניהול קמפיינים וקריאת דוחות בחשבון הפרסום, דרך הרשאת Google שאישרת.",
                     "Managing campaigns and reading reports in the ad account, through the Google permission you approved.",
                     "Gestion des campagnes et lecture des rapports du compte publicitaire, via l'autorisation Google que vous avez approuvée.",
                     "إدارة الحملات وقراءة التقارير في الحساب الإعلاني، عبر إذن Google الذي وافقت عليه.",
                     "Управление кампаниями и чтение отчётов в рекламном аккаунте — через разрешение Google, которое вы одобрили."),
        "steps": ["חשבון הפרסום רשום על העסק שלך ב-Google Ads — הקמפיינים, ההיסטוריה ואמצעי התשלום נשארים בו.",
                  "מוודאים שלמשתמש שלכם יש הרשאת Admin בחשבון.",
                  _GOOGLE_STEP,
                  "לסוכנות חדשה: מזמינים אותה מתוך החשבון, בהגדרות הגישה."],
    },
    "meta": {
        "kind": "oauth",
        "title": _L("פייסבוק ואינסטגרם (Meta)", "Facebook & Instagram (Meta)", "Facebook et Instagram (Meta)",
                    "فيسبوك وإنستغرام (Meta)", "Facebook и Instagram (Meta)"),
        "access": _L("ניהול מודעות וקריאת דוחות, ופרסום בעמוד ובאינסטגרם שחיברת — דרך הרשאת Meta שאישרת.",
                     "Managing ads and reading reports, and posting to the Page and Instagram you connected — through the Meta permission you approved.",
                     "Gestion des publicités et lecture des rapports, et publication sur la Page et l'Instagram connectés — via l'autorisation Meta que vous avez approuvée.",
                     "إدارة الإعلانات وقراءة التقارير، والنشر في الصفحة وحساب إنستغرام اللذين ربطتهما — عبر إذن Meta الذي وافقت عليه.",
                     "Управление рекламой и чтение отчётов, публикации на подключённой странице и в Instagram — через разрешение Meta, которое вы одобрили."),
        "steps": ["חשבון המודעות, העמוד והאינסטגרם שייכים לעסק שלך — המודעות, הפוסטים וההיסטוריה נשארים.",
                  "מוודאים שיש לכם הרשאת אדמין מלאה בחשבון העסקי ב-Meta.",
                  "אחרי שנסיר את הגישה: בהגדרות Facebook, ברשימת האפליקציות המחוברות, בודקים ש-uallak כבר לא מופיעה.",
                  "לסוכנות חדשה: מוסיפים אותה כשותפה בהגדרות העסק ב-Meta."],
    },
    "tiktok": {
        "kind": "oauth",
        "title": _L("טיקטוק", "TikTok", "TikTok", "تيك توك", "TikTok"),
        "access": _L("שליחת סרטונים לטיוטות בחשבון (את הפרסום עושים אתם), וקריאת פרטי הפרופיל הבסיסיים.",
                     "Sending videos to your account's drafts (you publish them), and reading basic profile details.",
                     "Envoi de vidéos dans les brouillons de votre compte (c'est vous qui publiez) et lecture des informations de base du profil.",
                     "إرسال الفيديوهات إلى المسودات في حسابك (النشر يكون منك)، وقراءة بيانات الملف الأساسية.",
                     "Отправка видео в черновики аккаунта (публикуете вы) и чтение основных данных профиля."),
        "steps": ["החשבון והסרטונים שלך ב-TikTok נשארים כמו שהם — שום סרטון או טיוטה לא נמחקים.",
                  "אחרי שנסיר את הגישה: בהגדרות TikTok, בהרשאות האפליקציות, בודקים ש-uallak לא מופיעה ומסירים אותה אם היא עדיין שם."],
    },
    "youtube": {
        "kind": "oauth",
        "title": _L("ערוץ YouTube", "YouTube channel", "Chaîne YouTube", "قناة YouTube", "Канал YouTube"),
        "access": _L("העלאת סרטונים לערוץ כ'פרטיים' בלבד, ורק אחרי שאישרת כל סרטון.",
                     "Uploading videos to the channel as Private only, and only after you approve each one.",
                     "Mise en ligne de vidéos sur la chaîne en privé uniquement, et seulement après votre accord pour chacune.",
                     "رفع الفيديوهات إلى القناة كخاصة فقط، وفقط بعد موافقتك على كل فيديو.",
                     "Загрузка видео на канал только как «Доступ ограничен» и только после вашего одобрения каждого."),
        "steps": ["הערוץ והסרטונים שייכים לך — שום סרטון לא נמחק.",
                  "סרטונים שהעלינו נשארים 'פרטיים' עד שתחליטו אחרת ב-YouTube Studio.",
                  _GOOGLE_STEP],
    },
    "gtm": {
        "kind": "oauth",
        "title": _L("מדידה באתר (Google Tag Manager)", "Site measurement (Google Tag Manager)",
                    "Mesure du site (Google Tag Manager)", "القياس في الموقع (Google Tag Manager)",
                    "Аналитика сайта (Google Tag Manager)"),
        "access": _L("הוספת תג מדידת לידים בקונטיינר שבחרת ופרסום גרסה — רק אחרי שאישרת.",
                     "Adding a lead-measurement tag to the container you chose and publishing a version — only after you approve.",
                     "Ajout d'une balise de mesure des leads dans le conteneur choisi et publication d'une version — seulement après votre accord.",
                     "إضافة وسم لقياس العملاء المحتملين في الحاوية التي اخترتها ونشر نسخة — فقط بعد موافقتك.",
                     "Добавление тега учёта лидов в выбранный контейнер и публикация версии — только после вашего одобрения."),
        "steps": ["הקונטיינר שייך לחשבון Google Tag Manager שלך, וכל התגים בו נשארים ועובדים.",
                  "אם תג המדידה שהוספנו כבר לא נחוץ — אפשר להשהות או למחוק אותו בקונטיינר. אנחנו לא מוחקים אותו בעצמנו.",
                  _GOOGLE_STEP],
    },
    "merchant_center": {
        "kind": "oauth",
        "title": _L("קטלוג מוצרים (Google Merchant Center)", "Product catalog (Google Merchant Center)",
                    "Catalogue produits (Google Merchant Center)", "كتالوج المنتجات (Google Merchant Center)",
                    "Каталог товаров (Google Merchant Center)"),
        "access": _L("גישה לחשבון Merchant Center שחיברת, לניהול רשימת המוצרים.",
                     "Access to the Merchant Center account you connected, to manage the product list.",
                     "Accès au compte Merchant Center connecté, pour gérer la liste des produits.",
                     "الوصول إلى حساب Merchant Center الذي ربطته، لإدارة قائمة المنتجات.",
                     "Доступ к подключённому аккаунту Merchant Center для ведения списка товаров."),
        "steps": ["חשבון Merchant Center והמוצרים בו נשארים שלך.", _GOOGLE_STEP],
    },
    "gmail": {
        "kind": "oauth",
        "title": _L("שליחת מיילים מהכתובת שלך (Gmail)", "Sending email from your address (Gmail)",
                    "Envoi d'e-mails depuis votre adresse (Gmail)", "إرسال البريد من عنوانك (Gmail)",
                    "Отправка писем с вашего адреса (Gmail)"),
        "access": _L("שליחה בלבד, ורק של מייל שאישרת. אין לנו גישה לקריאת תיבת הדואר.",
                     "Sending only, and only an email you approved. We cannot read your mailbox.",
                     "Envoi uniquement, et seulement d'un e-mail que vous avez approuvé. Nous ne pouvons pas lire votre boîte.",
                     "الإرسال فقط، وفقط لرسالة وافقت عليها. لا يمكننا قراءة صندوق بريدك.",
                     "Только отправка и только одобренных вами писем. Читать ваш ящик мы не можем."),
        "steps": ["תיבת הדואר שלך לא נפגעת — מעולם לא קראנו אותה, ושום מייל לא נמחק.", _GOOGLE_STEP],
    },
    "search_console": {
        "kind": "oauth",
        "title": _L("נתוני חיפוש של האתר (Search Console)", "Site search data (Search Console)",
                    "Données de recherche du site (Search Console)", "بيانات البحث للموقع (Search Console)",
                    "Поисковые данные сайта (Search Console)"),
        "access": _L("קריאה בלבד של נתוני החיפוש של האתר: מיקומים, הקלקות וחשיפות.",
                     "Read-only access to the site's search data: positions, clicks and impressions.",
                     "Lecture seule des données de recherche du site : positions, clics et impressions.",
                     "قراءة فقط لبيانات البحث الخاصة بالموقع: المراكز والنقرات ومرات الظهور.",
                     "Только чтение поисковых данных сайта: позиции, клики и показы."),
        "steps": ["הנכס ב-Search Console ונתוניו נשארים שלך; הייתה לנו גישת קריאה בלבד.", _GOOGLE_STEP],
    },
    "analytics": {
        "kind": "oauth",
        "title": _L("תנועה באתר (Google Analytics)", "Site traffic (Google Analytics)",
                    "Trafic du site (Google Analytics)", "زيارات الموقع (Google Analytics)",
                    "Посещаемость сайта (Google Analytics)"),
        "access": _L("קריאה בלבד של נתוני הביקורים בנכס Analytics שבחרת: מבקרים, ביקורים ומקורות תנועה.",
                     "Read-only access to visit data in the Analytics property you chose: visitors, sessions and traffic sources.",
                     "Lecture seule des données de visite de la propriété Analytics choisie : visiteurs, sessions et sources de trafic.",
                     "قراءة فقط لبيانات الزيارات في خاصية Analytics التي اخترتها: الزوار والجلسات ومصادر الزيارات.",
                     "Только чтение данных о посещениях выбранного ресурса Analytics: посетители, сеансы и источники трафика."),
        "steps": ["הנכס ב-Google Analytics ונתוניו נשארים שלך; הייתה לנו גישת קריאה בלבד.", _GOOGLE_STEP],
    },
    "google_drive": {
        "kind": "oauth",
        "title": _L("הקבצים שלך ב-Google Drive שלך", "Your files in your own Google Drive",
                    "Vos fichiers dans votre propre Google Drive", "ملفاتك في Google Drive الخاص بك",
                    "Ваши файлы в вашем собственном Google Drive"),
        "access": _L("ניהול הקבצים שהמערכת עצמה יצרה, בתיקיית uallak ב-Drive שלך — ורק אותם. אין לנו שום גישה לשאר הקבצים ב-Drive שלך.",
                     "Managing only the files the app itself created, in the uallak folder in your Drive. We have no access to anything else in your Drive.",
                     "Gestion uniquement des fichiers créés par l'application, dans le dossier uallak de votre Drive. Aucun accès au reste de votre Drive.",
                     "إدارة الملفات التي أنشأها التطبيق نفسه فقط، في مجلد uallak في Drive الخاص بك. لا وصول لنا إلى أي شيء آخر في Drive.",
                     "Управление только файлами, которые создало само приложение, в папке uallak на вашем Drive. К остальному на вашем Drive у нас нет доступа."),
        "steps": ["תיקיית uallak וכל הקבצים בה נמצאים ב-Drive שלך ושייכים לך — כשנסיר את הגישה שלנו הם נשארים בדיוק כמו שהם.",
                  _GOOGLE_STEP],
    },
    "wordpress": {
        "kind": "app_password",
        "title": _L("האתר שלך (WordPress)", "Your website (WordPress)", "Votre site (WordPress)",
                    "موقعك (WordPress)", "Ваш сайт (WordPress)"),
        "access": _L("עריכת תוכן באתר (פוסטים ועמודים) בעזרת סיסמת אפליקציה שנוצרה במיוחד עבורנו.",
                     "Editing site content (posts and pages) with an application password created just for us.",
                     "Modification du contenu du site (articles et pages) avec un mot de passe d'application créé spécialement pour nous.",
                     "تحرير محتوى الموقع (المقالات والصفحات) بكلمة مرور تطبيق أُنشئت لنا خصيصًا.",
                     "Редактирование контента сайта (записи и страницы) с паролем приложения, созданным специально для нас."),
        "steps": ["האתר, התוכן והמשתמשים שבו נשארים כמו שהם.",
                  "ניסינו למחוק את סיסמת האפליקציה שלנו באתר עצמו. אפשר לוודא: בעמוד הפרופיל של המשתמש, ברשימת סיסמאות האפליקציה, בודקים שהיא כבר לא שם.",
                  "אם האתר נבנה ומאוחסן אצלנו — ראו 'אחסון האתר' בהמשך."],
    },
    "heygen": {"kind": "api_key", "title": None, "vendor": "HeyGen",
               "access": _L("יצירת סרטונים עם הדמות שלך, בחשבון שפתחת ובמפתח שנתת.",
                            "Creating videos with your presenter, in the account you opened and with the key you gave.",
                            "Création de vidéos avec votre présentateur, dans le compte que vous avez ouvert et avec la clé fournie.",
                            "إنشاء فيديوهات بشخصيتك، في الحساب الذي فتحته وبالمفتاح الذي قدّمته.",
                            "Создание видео с вашим ведущим — в открытом вами аккаунте и по вашему ключу."),
               "steps": ["החשבון שלך ב-HeyGen, הדמויות והסרטונים שבו נשארים שלך — שום דבר לא נמחק.", _KEY_STEP]},
    "elevenlabs": {"kind": "api_key", "title": None, "vendor": "ElevenLabs",
                   "access": _L("הקראת טקסטים בקול שלך ושיבוט הקול, בחשבון שפתחת ובמפתח שנתת.",
                                "Reading scripts in your voice and cloning it, in the account you opened and with the key you gave.",
                                "Lecture de textes avec votre voix et clonage de celle-ci, dans le compte que vous avez ouvert et avec la clé fournie.",
                                "قراءة النصوص بصوتك واستنساخه، في الحساب الذي فتحته وبالمفتاح الذي قدّمته.",
                                "Озвучивание текстов вашим голосом и его клонирование — в открытом вами аккаунте и по вашему ключу."),
                   "steps": ["החשבון שלך ב-ElevenLabs והקול המשובט שבו נשארים שלך — שום דבר לא נמחק.", _KEY_STEP]},
    "higgsfield": {"kind": "api_key", "title": None, "vendor": "Higgsfield",
                   "access": _L("יצירת תמונות וסרטונים, בחשבון שפתחת ובמפתח שנתת.",
                                "Creating images and videos, in the account you opened and with the key you gave.",
                                "Création d'images et de vidéos, dans le compte que vous avez ouvert et avec la clé fournie.",
                                "إنشاء الصور والفيديوهات، في الحساب الذي فتحته وبالمفتاح الذي قدّمته.",
                                "Создание изображений и видео — в открытом вами аккаунте и по вашему ключу."),
                   "steps": ["החשבון שלך ב-Higgsfield והקבצים שבו נשארים שלך — שום דבר לא נמחק.", _KEY_STEP]},
    "crm": {"kind": "api_key",
            "title": _L("ה-CRM שלך", "Your CRM", "Votre CRM", "نظام CRM الخاص بك", "Ваша CRM"),
            "access": _L("הוספת לידים חדשים לאנשי הקשר ב-CRM שלך, במפתח שנתת.",
                         "Adding new leads to the contacts in your CRM, with the key you gave.",
                         "Ajout des nouveaux leads aux contacts de votre CRM, avec la clé fournie.",
                         "إضافة العملاء المحتملين الجدد إلى جهات الاتصال في نظام CRM لديك، بالمفتاح الذي قدّمته.",
                         "Добавление новых лидов в контакты вашей CRM — по вашему ключу."),
            "steps": ["ה-CRM ואנשי הקשר בו נשארים שלך — שום ליד לא נמחק.",
                      "מחקנו את העותק שלנו של המפתח; אפשר לבטל אותו גם בהגדרות ה-CRM."]},
    "manychat": {"kind": "api_key", "vendor": "ManyChat",
                 "title": _L("אוטומציות שיחה", "Chat automations", "Automatisations de conversation",
                             "أتمتة المحادثات", "Автоматизация переписки"),
                 "access": _L("בנייה ותחזוקה של אוטומציות השיחה בחשבון שלך.",
                              "Building and maintaining the chat automations in your account.",
                              "Création et maintenance des automatisations de conversation dans votre compte.",
                              "بناء وصيانة أتمتة المحادثات في حسابك.",
                              "Создание и поддержка автоматизаций переписки в вашем аккаунте."),
                 "steps": ["החשבון שלך ב-ManyChat והאוטומציות שנבנו בו נשארים שלך וממשיכים לעבוד.", _KEY_STEP,
                           "אם צירפתם את הצוות שלנו כמשתמש בחשבון — מסירים אותו מרשימת המשתמשים."]},
    # RETIRED vendor (automation_service.RETIRED_VENDORS): never offered any
    # more, but a client who already has a row still sees it here and can
    # disconnect it — the stored row is theirs and is left intact.
    "make": {"kind": "api_key", "vendor": "Make",
             "title": _L("חיבורים בין המערכות שלך", "Connections between your systems", "Connexions entre vos outils",
                         "الربط بين أنظمتك", "Связки между вашими системами"),
             "access": _L("בנייה ותחזוקה של החיבורים בין המערכות בחשבון שלך.",
                          "Building and maintaining the connections between systems in your account.",
                          "Création et maintenance des connexions entre outils dans votre compte.",
                          "بناء وصيانة الربط بين الأنظمة في حسابك.",
                          "Создание и поддержка связок между системами в вашем аккаунте."),
             "steps": ["החשבון שלך ב-Make והתרחישים שנבנו בו נשארים שלך וממשיכים לעבוד.", _KEY_STEP,
                       "אם צירפתם את הצוות שלנו כמשתמש בחשבון — מסירים אותו מרשימת המשתמשים."]},
}

# What the client reads about the credential itself — never the credential
KEY_NOTE = {
    "oauth": _L("אין אצלנו סיסמה שלך — רק הרשאה שאישרת, ואפשר לבטל אותה בכל רגע בהגדרות החשבון שלך.",
                "We hold no password of yours — only a permission you approved, which you can cancel any time in your account settings.",
                "Nous ne détenons aucun mot de passe — seulement une autorisation que vous avez approuvée, révocable à tout moment dans les paramètres de votre compte.",
                "لا نحتفظ بأي كلمة مرور لك — فقط بإذن وافقت عليه، ويمكنك إلغاؤه في أي وقت من إعدادات حسابك.",
                "У нас нет вашего пароля — только разрешение, которое вы одобрили и можете отменить в любой момент в настройках аккаунта."),
    "api_key": _L("המפתח עצמו לא מוצג כאן ולא נשלח לשום מקום. הוא נמצא בחשבון שלך אצל הכלי, ואפשר לבטל אותו או ליצור חדש שם בכל רגע.",
                  "The key itself is never shown here or sent anywhere. It lives in your own account at the tool, and you can cancel it or create a new one there at any time.",
                  "La clé elle-même n'est jamais affichée ici ni envoyée ailleurs. Elle se trouve dans votre propre compte chez l'outil, où vous pouvez l'annuler ou en créer une nouvelle à tout moment.",
                  "المفتاح نفسه لا يُعرض هنا ولا يُرسل إلى أي مكان. إنه موجود في حسابك لدى الأداة، ويمكنك إلغاؤه أو إنشاء مفتاح جديد هناك في أي وقت.",
                  "Сам ключ здесь не показывается и никуда не отправляется. Он хранится в вашем аккаунте в сервисе, и там его можно отменить или создать новый в любой момент."),
    "app_password": _L("סיסמת האפליקציה לא מוצגת כאן. היא נמצאת באתר שלך, בעמוד הפרופיל של המשתמש, ואפשר לבטל אותה או ליצור חדשה שם בכל רגע.",
                       "The application password is never shown here. It lives on your site, on the user's profile page, where you can cancel it or create a new one at any time.",
                       "Le mot de passe d'application n'est jamais affiché ici. Il se trouve sur votre site, dans la page de profil de l'utilisateur, où vous pouvez l'annuler ou en créer un nouveau à tout moment.",
                       "كلمة مرور التطبيق لا تُعرض هنا. إنها موجودة في موقعك، في صفحة الملف الشخصي للمستخدم، ويمكنك إلغاؤها أو إنشاء جديدة هناك في أي وقت.",
                       "Пароль приложения здесь не показывается. Он хранится на вашем сайте, на странице профиля пользователя, где его можно отменить или создать новый в любой момент."),
}

# Rows that carry more than one thing (one Meta consent = ad account + Page + Instagram)
_ITEM = {
    "meta_ads": _L("חשבון מודעות", "Ad account", "Compte publicitaire", "الحساب الإعلاني", "Рекламный аккаунт"),
    "meta_page": _L("עמוד", "Page", "Page", "الصفحة", "Страница"),
    "meta_instagram": _L("אינסטגרם", "Instagram", "Instagram", "إنستغرام", "Instagram"),
    "google_tagmanager": _L("קונטיינר", "Container", "Conteneur", "الحاوية", "Контейнер"),
    "search_console": _L("נכס", "Property", "Propriété", "الموقع", "Ресурс"),
    "google_analytics": _L("נכס", "Property", "Propriété", "الخاصية", "Ресурс"),
    "google_drive_file": _L("תיקייה", "Folder", "Dossier", "المجلد", "Папка"),
}

# account_id values that are a marker, not something the client would recognise
_PLACEHOLDER_IDS = {"", "pending_page", "pending_channel", "api_token", "heygen", "elevenlabs", "higgsfield"}

ASSET_TITLES = {
    "website": _L("האתר", "Website", "Site web", "الموقع", "Сайт"),
    "hosted_site": _L("אחסון האתר", "Website hosting", "Hébergement du site", "استضافة الموقع", "Хостинг сайта"),
    "landing_pages": _L("דפי נחיתה", "Landing pages", "Pages d'atterrissage", "صفحات الهبوط", "Лендинги"),
    "landing_domain": _L("הדומיין של דפי הנחיתה", "Landing-page domain", "Domaine des pages d'atterrissage",
                         "نطاق صفحات الهبوط", "Домен лендингов"),
    "drive_folder": _L("תיקיית המדיה ב-Drive", "Media folder in Drive", "Dossier médias dans Drive",
                       "مجلد الوسائط في Drive", "Папка медиа в Drive"),
    "drive_folder_own": _L("תיקיית uallak ב-Drive שלך", "The uallak folder in your Drive",
                           "Le dossier uallak dans votre Drive", "مجلد uallak في Drive الخاص بك",
                           "Папка uallak на вашем Drive"),
    "leads": _L("הלידים שלך", "Your leads", "Vos leads", "عملاؤك المحتملون", "Ваши лиды"),
}

ASSET_STEPS = {
    "website": "כתובת האתר: {ref}. הדומיין, התוכן והמשתמשים שלו שלך.",
    "hosted_site": ("האתר מאוחסן בחשבון האחסון של uallak, והוא לא נמחק אוטומטית כשעוזבים. "
                    "הצוות מתאם איתך העברה לאחסון שלך — זה תהליך ידני, ונעשה אותו יחד."),
    "landing_pages": ("{ref} דפי נחיתה. הם מוגשים מהשרת של uallak, ולכן לא עוברים כמו שהם — כל הטקסטים שלהם "
                      "נמצאים ב'ייצוא הכל', וכל הלידים שהגיעו מהם בקובץ הלידים."),
    "landing_domain": "הדומיין {ref} רשום אצלך. מפנים את רשומת ה-DNS שלו ליעד החדש.",
    "drive_folder": ("תיקיית המדיה משותפת איתך מה-Drive של uallak. לפני היציאה מורידים את הקבצים או יוצרים מהם "
                     "עותק ב-Drive שלך — 'ייצוא הכל' כולל קישור לכל קובץ."),
    "drive_folder_own": ("הקבצים כבר נמצאים ב-Drive שלך, בתיקיית uallak, ושייכים לך. בהעברה רק מסירים את הגישה "
                         "שלנו — אין מה להוריד ואין מה להעתיק."),
    "leads": "{ref} לידים — כולם ב'ייצוא הכל' (Excel ו-JSON).",
}

HANDOVER_SUMMARY = [
    "המסמך הזה מפרט כל חשבון ונכס ששייכים לעסק שלך, ומה עושים עם כל אחד בהעברה.",
    "uallak מסירה את הגישה שלה לכל החשבונות. שום מידע שלך אצל הספקים לא נמחק — לא קמפיינים, "
    "לא פוסטים, לא סרטונים, לא קולות ולא אנשי קשר.",
    "אף מפתח או סיסמה לא מופיעים במסמך. הם נמצאים בחשבונות שלך, ואפשר לבטל או לחדש אותם שם בכל רגע.",
]


def title(group: str, lang: str = "he") -> str:
    spec = GROUPS[group]
    if spec.get("title") is None:
        from core import vendor_tools
        return vendor_tools.client_label(group, lang)
    return _t(spec["title"], lang)


# ─── Reads ───────────────────────────────────────────────────────────────────

def _accounts(client_id: int) -> list:
    from agents.client_agent import get_accounts
    return get_accounts(client_id) or []


def _identity(group: str, row: dict) -> dict:
    """What the client recognises the account by: an id, a site, an address.
    Never a credential — and nothing at all for marker ids."""
    platform = row.get("platform") or ""
    aid = str(row.get("account_id") or "").strip()
    if group in ("manychat", "make"):
        return {"text": GROUPS[group]["vendor"], "link": ""}
    if group == "crm":
        try:
            from core import crm_service
            label = next((v["label"] for v in crm_service.supported_vendors() if v["key"] == aid), "")
        except Exception:
            label = ""
        return {"text": label, "link": ""}
    if aid in _PLACEHOLDER_IDS or aid == platform:
        return {"text": "", "link": ""}
    text, link = aid, ""
    if platform == "google_ads":
        try:
            from core import google_ads_service
            text = google_ads_service.format_customer_id(aid)
        except Exception:
            pass
    elif platform == "youtube":
        link = f"https://www.youtube.com/channel/{aid}"
    elif platform == "meta_page":
        link = f"https://www.facebook.com/{aid}"
    elif platform in ("wordpress", "search_console") and aid.startswith("https://"):
        link = aid
    elif platform == "google_drive_file":
        # The uallak folder in the client's own Drive (its id means nothing to them)
        text, link = "", f"https://drive.google.com/drive/folders/{aid}"
    return {"text": text, "link": link}


def _status(client_id: int, group: str, rows: list) -> str:
    """connected | pending (a step is still missing) | needs_attention."""
    live = [r for r in rows if r.get("status") == "active" and not str(r.get("platform", "")).endswith("_pending")]
    if not live:
        return "pending"
    try:
        if group in ("heygen", "elevenlabs", "higgsfield"):
            from core import vendor_tools
            if vendor_tools.status(client_id, group) == "key_rejected":
                return "needs_attention"
        elif group == "google_ads":
            from agents.google_ads_agent import grant_needs_reconnect
            if grant_needs_reconnect(client_id):
                return "needs_attention"
    except Exception as e:
        print(f"[ownership] status check {group} for client {client_id}: {type(e).__name__}")
    return "connected"


def accounts_view(client_id: int, groups: dict, lang: str = "he", accounts: list = None) -> list:
    """One entry per connected group — identity, access, since when, status.
    No secret, no last-4, no has_secret: only what identifies the account."""
    lang = lang if lang in LANGS else "he"
    accounts = _accounts(client_id) if accounts is None else accounts
    out = []
    for group, spec in GROUPS.items():
        platforms = groups.get(group) or []
        rows = [a for a in accounts if a.get("platform") in platforms]
        if not rows:
            continue
        items = []
        for row in rows:
            ident = _identity(group, row)
            what = _ITEM.get(row.get("platform"))
            if ident["text"] or ident["link"]:
                items.append({"what": _t(what, lang) if what else "", **ident})
        dates = sorted(str(r.get("created_at") or "") for r in rows if r.get("created_at"))
        out.append({
            "group": group,
            "title": title(group, lang),
            "access": _t(spec["access"], lang),
            "key_note": _t(KEY_NOTE[spec["kind"]], lang),
            "kind": spec["kind"],
            "items": items,
            "connected_at": dates[0] if dates else None,
            "status": _status(client_id, group, rows),
        })
    return out


def _asset_activity(client_id: int) -> list:
    from agents.client_agent import _db
    return (_db().table("client_activity").select("action_type, details, created_at")
            .eq("client_id", client_id)
            .in_("action_type", ["website_provisioned", "media_folder_created"])
            .order("created_at", desc=True).limit(20).execute().data or [])


def _count_landing_pages(client_id: int) -> int:
    from core import landing_pages
    return landing_pages.count_pages(client_id)


def _count_leads(client_id: int) -> int:
    from core import client_leads
    return len(client_leads.list_leads(client_id, limit=1000) or [])


def assets_view(client_id: int, lang: str = "he", accounts: list = None) -> list:
    """The business's assets that are not a connection: the site, its hosting,
    landing pages + domain, the Drive media folder, the leads. A read that
    fails leaves its asset out (never a guess)."""
    lang = lang if lang in LANGS else "he"
    accounts = _accounts(client_id) if accounts is None else accounts
    found = []
    for row in accounts:
        if row.get("platform") == "wordpress" and row.get("account_id"):
            found.append(("website", str(row["account_id"]), str(row["account_id"])
                          if str(row["account_id"]).startswith("https://") else ""))
        if row.get("platform") == "landing_domain" and row.get("account_id"):
            found.append(("landing_domain", str(row["account_id"]), ""))
    try:
        activity = _asset_activity(client_id)
    except Exception as e:
        print(f"[ownership] activity read failed for client {client_id}: {type(e).__name__}")
        activity = []
    if any(a.get("action_type") == "website_provisioned" for a in activity):
        found.append(("hosted_site", "", ""))
    # Where the files ACTUALLY live (core/drive_storage): the client's own
    # Drive once the pointer flipped, otherwise our Shared Drive
    try:
        from core import drive_storage
        where = drive_storage.location(client_id)
    except Exception as e:
        print(f"[ownership] storage location read failed for client {client_id}: {type(e).__name__}")
        where = {"kind": "shared_drive"}
    if where.get("kind") == "client_drive":
        found.append(("drive_folder_own", "", where.get("link") or ""))
    elif any(a.get("action_type") == "media_folder_created" for a in activity):
        found.append(("drive_folder", "", ""))
    for kind, counter in (("landing_pages", _count_landing_pages), ("leads", _count_leads)):
        try:
            n = counter(client_id)
        except Exception as e:
            print(f"[ownership] {kind} count failed for client {client_id}: {type(e).__name__}")
            continue
        if n:
            found.append((kind, str(n), ""))
    order = list(ASSET_TITLES)
    found.sort(key=lambda f: order.index(f[0]))
    return [{"kind": kind, "title": _t(ASSET_TITLES[kind], lang), "text": text, "link": link}
            for kind, text, link in found]


# ─── The handover document ───────────────────────────────────────────────────

def _steps(group: str) -> list:
    vendor = GROUPS[group].get("vendor") or ""
    return [s.replace("{vendor}", vendor) for s in GROUPS[group]["steps"]]


def handover(client_id: int, groups: dict, client: dict = None) -> dict:
    """Everything the business owns + what to do with each on the way out.
    Hebrew. Built BEFORE access is removed (the kit snapshots it)."""
    accounts = _accounts(client_id)
    rows = []
    for entry in accounts_view(client_id, groups, "he", accounts=accounts):
        rows.append({
            "what": entry["title"],
            "identity": " · ".join(" ".join(x for x in (i["what"], i["text"]) if x) for i in entry["items"]) or "—",
            "access": entry["access"],
            "steps": " • ".join(_steps(entry["group"])),
        })
    for asset in assets_view(client_id, "he", accounts=accounts):
        rows.append({
            "what": asset["title"],
            "identity": asset["text"] or "—",
            "access": "",
            "steps": ASSET_STEPS[asset["kind"]].replace("{ref}", asset["text"]),
        })
    client = client or {}
    return {
        "title": "מסמך העברה — החשבונות והנכסים של העסק",
        "subtitle": (client.get("business_name") or client.get("name") or f"client-{client_id}")
                    + " · " + datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "summary": list(HANDOVER_SUMMARY),
        "columns": [("what", "מה"), ("identity", "החשבון / הנכס"), ("access", "הגישה שהייתה ל-uallak"),
                    ("steps", "מה עושים בהעברה")],
        "rows": rows,
    }


# ─── The offboarding kit's revoke step ───────────────────────────────────────

def revoke_all(client_id: int, groups: dict, disconnect) -> dict:
    """Remove OUR access group by group, through the same disconnect the
    client's own button uses (api_server._disconnect_platform: revoke the
    grant / delete our Application Password / delete our stored key). Never
    deletes anything of the client's at a vendor. One failure never stops
    the others."""
    revoked, failed = [], []
    for group, platforms in groups.items():
        if group not in GROUPS:          # the legacy "avatar" alias
            continue
        try:
            result = disconnect(client_id, group)
        except Exception as e:
            failed.append({"group": group, "error": type(e).__name__})
            continue
        if result.get("removed"):
            revoked.append({"group": group, "provider_revoked": result.get("revoked")})
    return {"revoked": revoked, "failed": failed}
