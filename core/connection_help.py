"""In-product help for connecting a tool — "a quick sign-up and copy-paste
of a key", with the client never feeling alone.

ONE entry per connection, and the entry is the whole feature: the dashboard's
"עזרה" button only renders for ACTIVE entries, the general concierge explains
exactly these steps, and it may only hand out exactly these links
(strip_foreign_links enforces that in code). Adding a connection later is a
config change here — flip `active` and fill the steps/links.

LINK RULE: every URL below already existed in the codebase before this file
(client-facing copy, vendor registries, pricing references). Nothing is
guessed. Where the right link is not known, `url` is "" — such a link is
never shown or given — and the owner fills it in. A WordPress admin link is
site-specific, so it can never be a fixed URL here.

Placeholder entries (active=False) are the client-paid per-client tools:
each client opens their OWN account and pays the vendor directly; uallak only
manages and creates with it, and keys/material never mix between clients.
"""
import re

LANGS = ("he", "en", "fr", "ar", "ru")


def _t(he, en, fr, ar, ru) -> dict:
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


CONNECTIONS = [
    {
        "id": "google_ads",
        "title": _t("Google Ads", "Google Ads", "Google Ads", "Google Ads", "Google Ads"),
        "steps": {
            "he": ["בכרטיס Google Ads לוחצים על \"חבר עכשיו\".",
                   "מתחברים עם חשבון הגוגל שמנהל את חשבון הפרסום של העסק, ומאשרים את ההרשאות.",
                   "אם מופיעים כמה חשבונות — בוחרים את החשבון של העסק.",
                   "זהו — הכרטיס יראה \"מחובר\". אין עדיין חשבון פרסום? פותחים אחד ב-Google Ads ואז חוזרים לכאן."],
            "en": ["On the Google Ads card, click \"Connect now\".",
                   "Sign in with the Google account that manages your business's ad account and approve the permissions.",
                   "If several accounts appear, pick your business's account.",
                   "That's it — the card will show \"Connected\". No ad account yet? Create one in Google Ads, then come back here."],
            "fr": ["Sur la carte Google Ads, cliquez sur « Connecter ».",
                   "Connectez-vous avec le compte Google qui gère le compte publicitaire de votre entreprise et validez les autorisations.",
                   "Si plusieurs comptes apparaissent, choisissez celui de votre entreprise.",
                   "C'est tout — la carte affichera « Connecté ». Pas encore de compte publicitaire ? Créez-en un dans Google Ads, puis revenez ici."],
            "ar": ["في بطاقة Google Ads اضغط \"اربط الآن\".",
                   "سجّل الدخول بحساب غوغل الذي يدير الحساب الإعلاني لعملك ووافق على الأذونات.",
                   "إذا ظهرت عدة حسابات، اختر حساب عملك.",
                   "هذا كل شيء — ستظهر البطاقة \"مرتبط\". لا يوجد حساب إعلاني بعد؟ أنشئ واحدًا في Google Ads ثم عُد إلى هنا."],
            "ru": ["На карточке Google Ads нажмите «Подключить».",
                   "Войдите в аккаунт Google, который управляет рекламным аккаунтом бизнеса, и подтвердите разрешения.",
                   "Если появится несколько аккаунтов — выберите аккаунт вашего бизнеса.",
                   "Готово — на карточке появится «Подключено». Рекламного аккаунта ещё нет? Создайте его в Google Ads и вернитесь сюда."],
        },
        # Domain named in the dashboard's "no ad account" copy (gads_none_text)
        "links": [{"label": _t("Google Ads", "Google Ads", "Google Ads", "Google Ads", "Google Ads"),
                   "url": "https://ads.google.com"}],
        "estimated_minutes": 3,
        "needs_api_key": False,
        "active": True,
    },
    {
        "id": "meta",
        "title": _t("Meta — פייסבוק + אינסטגרם", "Meta — Facebook + Instagram", "Meta — Facebook + Instagram",
                    "Meta — فيسبوك + إنستغرام", "Meta — Facebook + Instagram"),
        "steps": {
            "he": ["בכרטיס Meta לוחצים על \"חבר עכשיו\".",
                   "מתחברים לפייסבוק עם המשתמש שמנהל את העמוד העסקי ואת חשבון הפרסום.",
                   "מאשרים את הגישה לעמוד ולחשבון הפרסום של העסק.",
                   "חוזרים אוטומטית לדשבורד — הכרטיס יראה \"מחובר\"."],
            "en": ["On the Meta card, click \"Connect now\".",
                   "Log in to Facebook with the user who manages your business Page and ad account.",
                   "Approve access to your business's Page and ad account.",
                   "You'll return to the dashboard automatically — the card will show \"Connected\"."],
            "fr": ["Sur la carte Meta, cliquez sur « Connecter ».",
                   "Connectez-vous à Facebook avec l'utilisateur qui gère votre Page professionnelle et votre compte publicitaire.",
                   "Autorisez l'accès à la Page et au compte publicitaire de votre entreprise.",
                   "Vous revenez automatiquement au tableau de bord — la carte affichera « Connecté »."],
            "ar": ["في بطاقة Meta اضغط \"اربط الآن\".",
                   "سجّل الدخول إلى فيسبوك بالمستخدم الذي يدير صفحة العمل والحساب الإعلاني.",
                   "وافق على الوصول إلى صفحة عملك وحسابه الإعلاني.",
                   "ستعود تلقائيًا إلى لوحة التحكم — ستظهر البطاقة \"مرتبط\"."],
            "ru": ["На карточке Meta нажмите «Подключить».",
                   "Войдите в Facebook под пользователем, который управляет бизнес-страницей и рекламным аккаунтом.",
                   "Разрешите доступ к странице и рекламному аккаунту бизнеса.",
                   "Вы автоматически вернётесь в кабинет — на карточке появится «Подключено»."],
        },
        # Homepage-level link (no specific official help page confirmed yet)
        "links": [{"label": _t("כניסה ל-Meta Business", "Go to Meta Business", "Accéder à Meta Business",
                               "الدخول إلى Meta Business", "Перейти в Meta Business"),
                   "url": "https://business.facebook.com/"}],
        "estimated_minutes": 3,
        "needs_api_key": False,
        "active": True,
    },
    {
        "id": "tiktok",
        "title": _t("TikTok", "TikTok", "TikTok", "TikTok", "TikTok"),
        "steps": {
            "he": ["בכרטיס TikTok לוחצים על \"חבר עכשיו\" וקוראים את ההסבר הקצר.",
                   "מתחברים לחשבון הטיקטוק של העסק ומאשרים.",
                   "מעכשיו סרטונים שנכין יגיעו לתיבת הנכנסים שלכם בטיקטוק — אתם סוקרים ומפרסמים."],
            "en": ["On the TikTok card, click \"Connect now\" and read the short explanation.",
                   "Log in to your business's TikTok account and approve.",
                   "From now on, videos we prepare arrive in your TikTok inbox — you review and publish them."],
            "fr": ["Sur la carte TikTok, cliquez sur « Connecter » et lisez la courte explication.",
                   "Connectez-vous au compte TikTok de votre entreprise et validez.",
                   "Désormais, les vidéos que nous préparons arrivent dans votre boîte de réception TikTok — vous les vérifiez et les publiez."],
            "ar": ["في بطاقة TikTok اضغط \"اربط الآن\" واقرأ الشرح القصير.",
                   "سجّل الدخول إلى حساب تيك توك الخاص بعملك ووافق.",
                   "من الآن ستصل الفيديوهات التي نجهزها إلى صندوق الوارد في تيك توك — أنت تراجعها وتنشرها."],
            "ru": ["На карточке TikTok нажмите «Подключить» и прочитайте короткое пояснение.",
                   "Войдите в аккаунт TikTok вашего бизнеса и подтвердите.",
                   "Теперь видео, которые мы готовим, приходят во входящие TikTok — вы просматриваете и публикуете их."],
        },
        "links": [{"label": _t("פתיחת חשבון פרסום בטיקטוק", "Create a TikTok ads account",
                               "Créer un compte publicitaire TikTok", "إنشاء حساب إعلانات في تيك توك",
                               "Создать рекламный аккаунт TikTok"),
                   "url": "https://ads.tiktok.com/help/article/create-tiktok-ads-manager-account"}],
        "estimated_minutes": 2,
        "needs_api_key": False,
        "active": True,
    },
    {
        "id": "youtube",
        "title": _t("YouTube", "YouTube", "YouTube", "YouTube", "YouTube"),
        "steps": {
            "he": ["בכרטיס YouTube לוחצים על \"חבר עכשיו\".",
                   "בוחרים את חשבון הגוגל שהערוץ שייך אליו ומאשרים.",
                   "סרטונים שנעלה יופיעו כפרטיים ב-YouTube Studio — שם סוקרים ומפרסמים."],
            "en": ["On the YouTube card, click \"Connect now\".",
                   "Choose the Google account that owns the channel and approve.",
                   "Videos we upload appear as private in YouTube Studio — review and publish them there."],
            "fr": ["Sur la carte YouTube, cliquez sur « Connecter ».",
                   "Choisissez le compte Google propriétaire de la chaîne et validez.",
                   "Les vidéos que nous mettons en ligne apparaissent en privé dans YouTube Studio — vérifiez-les et publiez-les là-bas."],
            "ar": ["في بطاقة YouTube اضغط \"اربط الآن\".",
                   "اختر حساب غوغل الذي تتبع له القناة ووافق.",
                   "الفيديوهات التي نرفعها تظهر كخاصة في YouTube Studio — راجعها وانشرها من هناك."],
            "ru": ["На карточке YouTube нажмите «Подключить».",
                   "Выберите аккаунт Google, которому принадлежит канал, и подтвердите.",
                   "Загруженные нами видео появятся как приватные в YouTube Studio — там их можно проверить и опубликовать."],
        },
        # Domain named in youtube_content_agent's client-facing copy
        "links": [{"label": _t("YouTube Studio", "YouTube Studio", "YouTube Studio", "YouTube Studio", "YouTube Studio"),
                   "url": "https://studio.youtube.com"}],
        "estimated_minutes": 2,
        "needs_api_key": False,
        "active": True,
    },
    {
        "id": "merchant_center",
        "title": _t("Merchant Center — קניות בגוגל", "Merchant Center — Google Shopping",
                    "Merchant Center — Google Shopping", "Merchant Center — التسوق في غوغل",
                    "Merchant Center — Google Покупки"),
        "steps": {
            "he": ["בכרטיס Merchant Center לוחצים על \"חבר עכשיו\" ומאשרים עם חשבון הגוגל שמנהל אותו.",
                   "מעתיקים את מזהה ה-Merchant Center — המספר שמופיע למעלה ליד שם החשבון ב-Merchant Center.",
                   "מדביקים אותו בשדה בכרטיס ולוחצים \"חיבור\"."],
            "en": ["On the Merchant Center card, click \"Connect now\" and approve with the Google account that manages it.",
                   "Copy your Merchant Center ID — the number shown at the top next to the account name in Merchant Center.",
                   "Paste it into the field on the card and click \"Connect\"."],
            "fr": ["Sur la carte Merchant Center, cliquez sur « Connecter » et validez avec le compte Google qui le gère.",
                   "Copiez votre identifiant Merchant Center — le numéro affiché en haut à côté du nom du compte dans Merchant Center.",
                   "Collez-le dans le champ de la carte et cliquez sur « Connecter »."],
            "ar": ["في بطاقة Merchant Center اضغط \"اربط الآن\" ووافق بحساب غوغل الذي يديره.",
                   "انسخ معرّف Merchant Center — الرقم الظاهر في الأعلى بجانب اسم الحساب في Merchant Center.",
                   "الصقه في الحقل داخل البطاقة واضغط \"ربط\"."],
            "ru": ["На карточке Merchant Center нажмите «Подключить» и подтвердите аккаунтом Google, который им управляет.",
                   "Скопируйте идентификатор Merchant Center — число вверху рядом с названием аккаунта в Merchant Center.",
                   "Вставьте его в поле на карточке и нажмите «Подключить»."],
        },
        # Domain named in the dashboard's merchant_id_note copy
        "links": [{"label": _t("Merchant Center", "Merchant Center", "Merchant Center", "Merchant Center", "Merchant Center"),
                   "url": "https://merchants.google.com"}],
        "estimated_minutes": 4,
        "needs_api_key": False,
        # Retired 2026-10-21 with the Merchant Center connection (no `content` scope)
        "active": False,
    },
    {
        "id": "tag_manager",
        "title": _t("מדידת המרות (Google Tag Manager)", "Conversion measurement (Google Tag Manager)",
                    "Mesure des conversions (Google Tag Manager)", "قياس التحويلات (Google Tag Manager)",
                    "Измерение конверсий (Google Tag Manager)"),
        "steps": {
            "he": ["בכרטיס \"מדידת המרות (Google Tag Manager)\" לוחצים על \"חבר עכשיו\".",
                   "מתחברים עם חשבון הגוגל שיש לו גישה ל-Tag Manager של האתר, ומאשרים את כל ההרשאות במסך.",
                   "המדידה מותקנת באתר ברגע שהאתר מחובר — אם עוד לא חיברתם אתר, זה השלב הבא."],
            "en": ["On the \"Conversion measurement (Google Tag Manager)\" card, click \"Connect now\".",
                   "Sign in with the Google account that has access to your site's Tag Manager and approve every permission on the screen.",
                   "Measurement is installed on your site once the site is connected — if it isn't yet, that's the next step."],
            "fr": ["Sur la carte « Mesure des conversions (Google Tag Manager) », cliquez sur « Connecter ».",
                   "Connectez-vous avec le compte Google qui a accès au Tag Manager de votre site et acceptez toutes les autorisations à l'écran.",
                   "La mesure s'installe sur votre site dès qu'il est connecté — sinon, c'est la prochaine étape."],
            "ar": ["في بطاقة \"قياس التحويلات (Google Tag Manager)\" اضغط \"اربط الآن\".",
                   "سجّل الدخول بحساب غوغل الذي لديه وصول إلى Tag Manager الخاص بموقعك ووافق على كل الأذونات في الشاشة.",
                   "يُثبَّت القياس على موقعك بمجرد ربط الموقع — إن لم يكن مربوطًا بعد فهذه الخطوة التالية."],
            "ru": ["На карточке «Измерение конверсий (Google Tag Manager)» нажмите «Подключить».",
                   "Войдите в аккаунт Google с доступом к Tag Manager вашего сайта и подтвердите все разрешения на экране.",
                   "Измерение устанавливается на сайт, как только сайт подключён — если ещё нет, это следующий шаг."],
        },
        "links": [{"label": _t("התחלה עם Tag Manager", "Getting started with Tag Manager",
                               "Bien démarrer avec Tag Manager", "البدء مع Tag Manager",
                               "Начало работы с Tag Manager"),
                   "url": "https://support.google.com/tagmanager/answer/14842164"}],
        "estimated_minutes": 3,
        "needs_api_key": False,
        "active": True,
    },
    {
        "id": "wordpress",
        "title": _t("האתר שלך (WordPress)", "Your website (WordPress)", "Votre site (WordPress)",
                    "موقعك (WordPress)", "Ваш сайт (WordPress)"),
        "steps": {
            "he": ["בכרטיס \"האתר שלך\" לוחצים על \"חבר עכשיו\" וכותבים את כתובת האתר.",
                   "לוחצים \"חיבור בלחיצה אחת\" — תעברו למסך הניהול של האתר שלכם. מתחברים כרגיל ולוחצים \"אישור\".",
                   "לא עובד? יש חיבור ידני: באתר נכנסים ל-משתמשים ← פרופיל ← Application Passwords, יוצרים סיסמת אפליקציה ומדביקים אותה בשדה בכרטיס — אף פעם לא בצ׳אט."],
            "en": ["On the \"Your website\" card, click \"Connect now\" and type your site's address.",
                   "Click \"One-click connect\" — you'll go to your site's admin screen. Log in as usual and click \"Approve\".",
                   "Not working? There's a manual option: on your site go to Users → Profile → Application Passwords, create one and paste it into the field on the card — never into the chat."],
            "fr": ["Sur la carte « Votre site », cliquez sur « Connecter » et saisissez l'adresse de votre site.",
                   "Cliquez sur « Connexion en un clic » — vous arrivez sur l'écran d'administration de votre site. Connectez-vous comme d'habitude et cliquez sur « Approuver ».",
                   "Ça ne marche pas ? Option manuelle : sur votre site, allez dans Utilisateurs → Profil → Application Passwords, créez-en un et collez-le dans le champ de la carte — jamais dans le chat."],
            "ar": ["في بطاقة \"موقعك\" اضغط \"اربط الآن\" واكتب عنوان موقعك.",
                   "اضغط \"ربط بنقرة واحدة\" — ستنتقل إلى شاشة إدارة موقعك. سجّل الدخول كالمعتاد واضغط \"موافقة\".",
                   "لا يعمل؟ يوجد ربط يدوي: في موقعك اذهب إلى المستخدمون ← الملف الشخصي ← Application Passwords، أنشئ كلمة مرور تطبيق والصقها في الحقل داخل البطاقة — وليس في الدردشة أبدًا."],
            "ru": ["На карточке «Ваш сайт» нажмите «Подключить» и введите адрес сайта.",
                   "Нажмите «Подключение в один клик» — откроется админ-панель вашего сайта. Войдите как обычно и нажмите «Одобрить».",
                   "Не получается? Есть ручной способ: на сайте откройте Пользователи → Профиль → Application Passwords, создайте пароль приложения и вставьте его в поле на карточке — никогда не в чат."],
        },
        # A WordPress admin link is the client's OWN site — never a fixed URL
        "links": [],
        "estimated_minutes": 3,
        "needs_api_key": False,
        "active": True,
    },
    # ─── Inactive placeholders: client-paid, per-client accounts ─────────────
    {
        "id": "higgsfield",
        "title": _t("יצירת מדיה (Higgsfield)", "Media generation (Higgsfield)", "Génération de médias (Higgsfield)",
                    "إنشاء الوسائط (Higgsfield)", "Создание медиа (Higgsfield)"),
        "steps": {lang: [] for lang in LANGS},
        # Both already in client-facing copy / the pricing reference
        "links": [{"label": _t("API Keys", "API Keys", "API Keys", "API Keys", "API Keys"),
                   "url": "https://cloud.higgsfield.ai/api-keys"},
                  {"label": _t("מסלולים", "Plans", "Formules", "الباقات", "Тарифы"),
                   "url": "https://higgsfield.ai/pricing"}],
        "estimated_minutes": 5,
        "needs_api_key": True,
        "active": False,
    },
    {
        "id": "heygen",
        "title": _t("HeyGen (אווטאר)", "HeyGen (avatar)", "HeyGen (avatar)", "HeyGen (أفاتار)", "HeyGen (аватар)"),
        "steps": {lang: [] for lang in LANGS},
        "links": [{"label": _t("מחירון API", "API pricing", "Tarifs API", "أسعار API", "Цены API"),
                   "url": "https://www.heygen.com/api-pricing"}],
        "estimated_minutes": 5,
        "needs_api_key": True,
        "active": False,
    },
    {
        "id": "elevenlabs",
        "title": _t("ElevenLabs (קול)", "ElevenLabs (voice)", "ElevenLabs (voix)", "ElevenLabs (صوت)", "ElevenLabs (голос)"),
        "steps": {lang: [] for lang in LANGS},
        "links": [{"label": _t("מסלולים", "Plans", "Formules", "الباقات", "Тарифы"),
                   "url": "https://elevenlabs.io/pricing"}],
        "estimated_minutes": 5,
        "needs_api_key": True,
        "active": False,
    },
    {
        "id": "creatomate",
        "title": _t("Creatomate", "Creatomate", "Creatomate", "Creatomate", "Creatomate"),
        "steps": {lang: [] for lang in LANGS},
        "links": [{"label": _t("איפה מוצאים את מפתח ה-API", "Where to find your API key",
                               "Où trouver votre clé API", "أين تجد مفتاح API", "Где найти API-ключ"),
                   "url": "https://creatomate.com/docs/api/reference/where-can-i-find-my-api-key"}],
        "estimated_minutes": None,
        "needs_api_key": None,   # not known yet — fill in when this is wired
        "active": False,
    },
    {
        "id": "uppbeat",
        "title": _t("Uppbeat", "Uppbeat", "Uppbeat", "Uppbeat", "Uppbeat"),
        "steps": {lang: [] for lang in LANGS},
        # Homepage-level link only. No Uppbeat integration exists in the code and
        # no public API/key is confirmed — keep this entry INACTIVE.
        "links": [{"label": _t("אתר Uppbeat", "Uppbeat website", "Site Uppbeat", "موقع Uppbeat",
                               "Сайт Uppbeat"),
                   "url": "https://uppbeat.io/"}],
        "estimated_minutes": None,
        "needs_api_key": None,   # not known yet — fill in when this is wired
        "active": False,
    },
    {
        "id": "manychat",
        "title": _t("ManyChat", "ManyChat", "ManyChat", "ManyChat", "ManyChat"),
        "steps": {lang: [] for lang in LANGS},
        # From core/automation_service.VENDORS
        "links": [{"label": _t("הרשמה", "Sign up", "Inscription", "التسجيل", "Регистрация"),
                   "url": "https://manychat.com/signup"},
                  {"label": _t("כניסה", "Log in", "Connexion", "تسجيل الدخول", "Вход"),
                   "url": "https://app.manychat.com/"}],
        "estimated_minutes": None,
        "needs_api_key": False,  # credentials go to the team, never a key field (automation_service)
        "active": False,
    },
    {
        "id": "make",
        "title": _t("Make", "Make", "Make", "Make", "Make"),
        "steps": {lang: [] for lang in LANGS},
        "links": [{"label": _t("הרשמה", "Sign up", "Inscription", "التسجيل", "Регистрация"),
                   "url": "https://www.make.com/en/register"},
                  {"label": _t("כניסה", "Log in", "Connexion", "تسجيل الدخول", "Вход"),
                   "url": "https://www.make.com/en/login"}],
        "estimated_minutes": None,
        "needs_api_key": False,
        "active": False,
    },
]

_BY_ID = {c["id"]: c for c in CONNECTIONS}


def get(connection_id: str, active_only: bool = True) -> dict:
    entry = _BY_ID.get((connection_id or "").strip())
    if not entry or (active_only and not entry["active"]):
        return {}
    return entry


def _pick(table: dict, lang: str):
    return table.get(lang) or table.get("he")


def public_list(lang: str = "he") -> list:
    """What the dashboard needs to render "עזרה" buttons: active ids + title."""
    return [{"id": c["id"], "title": _pick(c["title"], lang),
             "estimated_minutes": c["estimated_minutes"]}
            for c in CONNECTIONS if c["active"]]


def missing_links() -> list:
    """Every link still waiting for the owner to fill in (report/admin aid)."""
    return [{"connection": c["id"], "label": _pick(link["label"], "en")}
            for c in CONNECTIONS for link in c["links"] if not link["url"]]


def for_agent(entry: dict, lang: str, whatsapp: str) -> dict:
    """The block the concierge receives: steps in the client's language and
    ONLY links that have a real URL."""
    return {
        "connection": _pick(entry["title"], lang),
        "steps": _pick(entry["steps"], lang) or [],
        "links": [{"label": _pick(link["label"], lang), "url": link["url"]}
                  for link in entry["links"] if link["url"]],
        "estimated_minutes": entry["estimated_minutes"],
        "needs_api_key": entry["needs_api_key"],
        "human_support_whatsapp": whatsapp,
    }


_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


def strip_foreign_links(reply: str, entry: dict) -> str:
    """Removes any URL from the reply that is not one of this entry's links —
    the agent may only hand out the links from the config, for that
    connection only."""
    allowed = {link["url"].rstrip("/") for link in entry.get("links", []) if link["url"]}

    def keep(match):
        url = match.group(0).rstrip(".,;:!?")
        return match.group(0) if url.rstrip("/") in allowed else ""
    return re.sub(r"\s{2,}", " ", _URL_RE.sub(keep, reply or "")).strip()
