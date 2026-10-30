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
site-specific, so it can never be a fixed URL here. One step further, used
only for the creative tools' "open an account" step: the HOMEPAGE of a
domain whose page was already linked (heygen.com, elevenlabs.io). Deep links
inside a vendor's app (its API-keys screen) are not confirmed for HeyGen or
ElevenLabs, so those steps describe the screen instead of linking it.

The client-paid creative tools are ACTIVE, one entry and one dashboard card
each (core/vendor_tools), and each has a `guide` — the card's PRIMARY connect
path (guide_view): numbered steps, what the screen looks like, a direct link,
where the key is, then "stuck? we'll do it with you" (a staff request,
POST /api/client/connection-help/{id}/assist). The raw key field is the
secondary "I already have a key". Their titles are capabilities ("your
voice", "your video presenter"); the vendor is named only inside the steps,
where the client must open the account. Each client opens their OWN account
and pays the vendor directly; uallak only manages and creates with it, and
keys/material never mix between clients. The remaining placeholders
(active=False: Creatomate, Uppbeat) have no connect flow at all.
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
    # ─── Client-paid creative tools (core/vendor_tools): a GUIDED flow ───────
    # The card's primary action is this guide (step → what the screen looks
    # like → a direct link), with "stuck? we'll do it with you" (a staff
    # request) and the raw key field only as "I already have a key". The
    # TITLE is the capability (what the client reads on the card); the vendor
    # is named only inside the steps, where the client opens the account.
    # `steps` (the concierge's text) is derived from `guide` below the list.
    {
        "id": "higgsfield",
        "title": _t("הדמות שבסרטונים — דמות AI", "Your video presenter — an AI presenter",
                    "Votre présentateur vidéo — un présentateur IA",
                    "الشخصية في الفيديوهات — شخصية بالذكاء الاصطناعي", "Ведущий ваших видео — ИИ-ведущий"),
        "guide": {
            "he": [{"do": "פותחים חשבון ב-Higgsfield (הכלי שמייצר את דמות ה-AI, תמונות וסרטונים) ובוחרים מסלול, עם אמצעי התשלום שלכם.",
                    "see": "עמוד מסלולים: לכל מסלול כמות קרדיטים לחודש. ההרשמה היא מייל או כניסה עם Google.",
                    "link": "https://higgsfield.ai/pricing"},
                   {"do": "נכנסים לעמוד מפתחות ה-API ולוחצים ליצירת מפתח חדש.",
                    "see": "רשימת מפתחות (ריקה בהתחלה) וכפתור ליצירת מפתח. המפתח החדש מופיע על המסך — מעתיקים אותו כולו.",
                    "link": "https://cloud.higgsfield.ai/api-keys"},
                   {"do": "חוזרים לכאן, לוחצים \"יש לי כבר מפתח\", מדביקים ולוחצים \"חיבור\".",
                    "see": "הכרטיס יראה \"מחובר\". את הכלי הזה אי אפשר לבדוק מראש — הוא נבדק ביצירה הראשונה. אף פעם לא שולחים מפתח בצ׳אט.",
                    "link": ""}],
            "en": [{"do": "Open an account at Higgsfield (the tool that makes the AI presenter, images and videos) and pick a plan, with your own payment method.",
                    "see": "A plans page: each plan has monthly credits. Sign-up is e-mail or Google.",
                    "link": "https://higgsfield.ai/pricing"},
                   {"do": "Open the API keys page and create a new key.",
                    "see": "A list of keys (empty at first) and a button to create one. The new key appears on screen — copy all of it.",
                    "link": "https://cloud.higgsfield.ai/api-keys"},
                   {"do": "Come back here, click \"I already have a key\", paste it and click \"Connect\".",
                    "see": "The card shows \"Connected\". This tool can't be checked in advance — the first creation checks it. Never send a key in the chat.",
                    "link": ""}],
            "fr": [{"do": "Ouvrez un compte chez Higgsfield (l'outil qui crée le présentateur IA, les images et les vidéos) et choisissez une formule, avec votre moyen de paiement.",
                    "see": "Une page de formules avec des crédits mensuels. Inscription par e-mail ou Google.",
                    "link": "https://higgsfield.ai/pricing"},
                   {"do": "Ouvrez la page des clés API et créez une nouvelle clé.",
                    "see": "Une liste de clés (vide au début) et un bouton de création. La nouvelle clé s'affiche — copiez-la entièrement.",
                    "link": "https://cloud.higgsfield.ai/api-keys"},
                   {"do": "Revenez ici, cliquez sur « J'ai déjà une clé », collez-la et cliquez sur « Connecter ».",
                    "see": "La carte affiche « Connecté ». Cet outil ne peut pas être vérifié à l'avance — la première création le vérifie. Jamais de clé dans le chat.",
                    "link": ""}],
            "ar": [{"do": "افتحوا حسابًا في Higgsfield (الأداة التي تصنع شخصية الذكاء الاصطناعي والصور والفيديوهات) واختاروا باقة بوسيلة الدفع الخاصة بكم.",
                    "see": "صفحة باقات: لكل باقة رصيد شهري. التسجيل بالبريد أو بحساب Google.",
                    "link": "https://higgsfield.ai/pricing"},
                   {"do": "افتحوا صفحة مفاتيح API وأنشئوا مفتاحًا جديدًا.",
                    "see": "قائمة مفاتيح (فارغة في البداية) وزر لإنشاء مفتاح. يظهر المفتاح الجديد على الشاشة — انسخوه كاملًا.",
                    "link": "https://cloud.higgsfield.ai/api-keys"},
                   {"do": "عودوا إلى هنا، اضغطوا \"لديّ مفتاح بالفعل\"، الصقوه واضغطوا \"ربط\".",
                    "see": "ستظهر البطاقة \"مرتبط\". لا يمكن فحص هذه الأداة مسبقًا — يُفحص عند أول إنشاء. لا ترسلوا مفتاحًا في الدردشة أبدًا.",
                    "link": ""}],
            "ru": [{"do": "Откройте аккаунт в Higgsfield (инструмент, который делает ИИ-ведущего, изображения и видео) и выберите тариф со своим способом оплаты.",
                    "see": "Страница тарифов: у каждого тарифа месячные кредиты. Регистрация по e-mail или через Google.",
                    "link": "https://higgsfield.ai/pricing"},
                   {"do": "Откройте страницу API-ключей и создайте новый ключ.",
                    "see": "Список ключей (сначала пустой) и кнопка создания. Новый ключ появится на экране — скопируйте его целиком.",
                    "link": "https://cloud.higgsfield.ai/api-keys"},
                   {"do": "Вернитесь сюда, нажмите «У меня уже есть ключ», вставьте его и нажмите «Подключить».",
                    "see": "На карточке появится «Подключено». Этот инструмент нельзя проверить заранее — проверка при первом создании. Никогда не отправляйте ключ в чат.",
                    "link": ""}],
        },
        "key_where": _t("המפתח נמצא בעמוד מפתחות ה-API בחשבון ה-Higgsfield (הקישור בשלב 2).",
                        "The key is on the API keys page of your Higgsfield account (the link in step 2).",
                        "La clé se trouve sur la page des clés API de votre compte Higgsfield (le lien de l'étape 2).",
                        "المفتاح في صفحة مفاتيح API في حساب Higgsfield (الرابط في الخطوة 2).",
                        "Ключ — на странице API-ключей вашего аккаунта Higgsfield (ссылка в шаге 2)."),
        # Both already in client-facing copy / the pricing reference
        "links": [{"label": _t("מפתחות API", "API keys", "Clés API", "مفاتيح API", "API-ключи"),
                   "url": "https://cloud.higgsfield.ai/api-keys"},
                  {"label": _t("מסלולים", "Plans", "Formules", "الباقات", "Тарифы"),
                   "url": "https://higgsfield.ai/pricing"}],
        "estimated_minutes": 5,
        "needs_api_key": True,
        "active": True,
    },
    {
        "id": "heygen",
        "title": _t("הדמות שבסרטונים — דמות מצולמת שלך", "Your video presenter — a filmed likeness of you",
                    "Votre présentateur vidéo — votre image filmée", "الشخصية في الفيديوهات — شخصيتك المصوَّرة",
                    "Ведущий ваших видео — ваш снятый образ"),
        "guide": {
            "he": [{"do": "פותחים חשבון ב-HeyGen (הכלי שמייצר את הדמות המצולמת שלך), עם המייל ואמצעי התשלום שלכם. גם המסלול החינמי מתאים לדמות אחת.",
                    "see": "מסך הרשמה רגיל: מייל או כניסה עם Google. אחרי ההרשמה נפתח מסך הבית של החשבון.",
                    "link": "https://www.heygen.com"},
                   {"do": "טוענים יתרת API (החל מ-5$) — ממנה משולמים הסרטונים, ישירות מהחשבון שלכם.",
                    "see": "עמוד מחירון ה-API מראה כמה עולה דקת וידאו (בערך 1-4$ לדקה).",
                    "link": "https://www.heygen.com/api-pricing"},
                   {"do": "בהגדרות החשבון (Settings) נכנסים לאזור ה-API ומעתיקים את המפתח.",
                    "see": "מסך עם הכותרת API ומפתח מוסתר בנקודות, ולידו כפתור העתקה.",
                    "link": ""},
                   {"do": "חוזרים לכאן, לוחצים \"יש לי כבר מפתח\", מדביקים, מסמנים את אישור השימוש בדמות (חובה) ולוחצים \"חיבור\".",
                    "see": "הכרטיס יראה \"מחובר ✓\", ואפשר ללחוץ \"בדיקת חיבור\" בכל רגע. אף פעם לא שולחים מפתח בצ׳אט.",
                    "link": ""}],
            "en": [{"do": "Open an account at HeyGen (the tool that makes your filmed presenter), with your own e-mail and payment method. The free plan is enough for one presenter.",
                    "see": "A normal sign-up screen: e-mail or Google. After it, your account's home screen opens.",
                    "link": "https://www.heygen.com"},
                   {"do": "Top up API credit (from $5) — videos are paid from it, directly from your account.",
                    "see": "The API pricing page shows what a video minute costs (roughly $1-4 a minute).",
                    "link": "https://www.heygen.com/api-pricing"},
                   {"do": "In your account Settings, open the API area and copy the key.",
                    "see": "A screen titled API with a key hidden behind dots and a copy button next to it.",
                    "link": ""},
                   {"do": "Come back here, click \"I already have a key\", paste it, tick the likeness consent (required) and click \"Connect\".",
                    "see": "The card shows \"Connected ✓\"; \"Test connection\" works at any time. Never send a key in the chat.",
                    "link": ""}],
            "fr": [{"do": "Ouvrez un compte chez HeyGen (l'outil qui crée votre présentateur filmé), avec votre e-mail et votre moyen de paiement. La formule gratuite suffit pour un présentateur.",
                    "see": "Un écran d'inscription classique : e-mail ou Google. Ensuite s'ouvre l'accueil de votre compte.",
                    "link": "https://www.heygen.com"},
                   {"do": "Rechargez un crédit API (dès 5 $) — les vidéos sont payées avec, directement depuis votre compte.",
                    "see": "La page des tarifs API indique le prix d'une minute de vidéo (environ 1-4 $).",
                    "link": "https://www.heygen.com/api-pricing"},
                   {"do": "Dans les paramètres (Settings) du compte, ouvrez la zone API et copiez la clé.",
                    "see": "Un écran intitulé API avec une clé masquée par des points et un bouton de copie.",
                    "link": ""},
                   {"do": "Revenez ici, cliquez sur « J'ai déjà une clé », collez-la, cochez le consentement d'image (obligatoire) et cliquez sur « Connecter ».",
                    "see": "La carte affiche « Connecté ✓ » ; « Tester la connexion » fonctionne à tout moment. Jamais de clé dans le chat.",
                    "link": ""}],
            "ar": [{"do": "افتحوا حسابًا في HeyGen (الأداة التي تصنع شخصيتكم المصوَّرة) ببريدكم ووسيلة الدفع الخاصة بكم. الباقة المجانية تكفي لشخصية واحدة.",
                    "see": "شاشة تسجيل عادية: بريد أو حساب Google. بعدها تُفتح الشاشة الرئيسية للحساب.",
                    "link": "https://www.heygen.com"},
                   {"do": "اشحنوا رصيد API (ابتداءً من 5$) — منه تُدفع الفيديوهات مباشرة من حسابكم.",
                    "see": "صفحة أسعار API تُظهر تكلفة دقيقة الفيديو (نحو 1-4$ للدقيقة).",
                    "link": "https://www.heygen.com/api-pricing"},
                   {"do": "في إعدادات الحساب (Settings) افتحوا قسم API وانسخوا المفتاح.",
                    "see": "شاشة بعنوان API ومفتاح مخفي بنقاط وبجانبه زر نسخ.",
                    "link": ""},
                   {"do": "عودوا إلى هنا، اضغطوا \"لديّ مفتاح بالفعل\"، الصقوه، حدّدوا الموافقة على استخدام الصورة (إلزامي) واضغطوا \"ربط\".",
                    "see": "ستظهر البطاقة \"مرتبط ✓\"، و\"فحص الاتصال\" متاح في أي وقت. لا ترسلوا مفتاحًا في الدردشة أبدًا.",
                    "link": ""}],
            "ru": [{"do": "Откройте аккаунт в HeyGen (инструмент, который делает вашего снятого ведущего) со своим e-mail и способом оплаты. Для одного ведущего хватит бесплатного тарифа.",
                    "see": "Обычная регистрация: e-mail или Google. После неё откроется главная страница аккаунта.",
                    "link": "https://www.heygen.com"},
                   {"do": "Пополните API-баланс (от $5) — из него оплачиваются видео, напрямую с вашего аккаунта.",
                    "see": "Страница цен API показывает стоимость минуты видео (примерно $1-4 за минуту).",
                    "link": "https://www.heygen.com/api-pricing"},
                   {"do": "В настройках аккаунта (Settings) откройте раздел API и скопируйте ключ.",
                    "see": "Экран с заголовком API, ключ скрыт точками, рядом кнопка копирования.",
                    "link": ""},
                   {"do": "Вернитесь сюда, нажмите «У меня уже есть ключ», вставьте его, отметьте согласие на образ (обязательно) и нажмите «Подключить».",
                    "see": "На карточке появится «Подключено ✓»; «Проверить подключение» можно в любой момент. Никогда не отправляйте ключ в чат.",
                    "link": ""}],
        },
        "key_where": _t("המפתח נמצא בחשבון ה-HeyGen, בהגדרות (Settings) ← אזור ה-API.",
                        "The key is in your HeyGen account, under Settings → the API area.",
                        "La clé se trouve dans votre compte HeyGen, dans Settings → la zone API.",
                        "المفتاح في حساب HeyGen، ضمن Settings ← قسم API.",
                        "Ключ — в аккаунте HeyGen: Settings → раздел API."),
        # The homepage is the domain of the pricing link that already existed
        "links": [{"label": _t("פתיחת חשבון", "Open an account", "Ouvrir un compte", "فتح حساب",
                               "Открыть аккаунт"),
                   "url": "https://www.heygen.com"},
                  {"label": _t("מחירון API", "API pricing", "Tarifs API", "أسعار API", "Цены API"),
                   "url": "https://www.heygen.com/api-pricing"}],
        "estimated_minutes": 5,
        "needs_api_key": True,
        "active": True,
    },
    {
        "id": "elevenlabs",
        "title": _t("הקול שלך", "Your voice", "Votre voix", "صوتك", "Ваш голос"),
        "guide": {
            "he": [{"do": "פותחים חשבון ב-ElevenLabs (הכלי שמשבט את הקול שלך ומקריא בו), עם המייל שלכם.",
                    "see": "מסך הרשמה רגיל: מייל או כניסה עם Google.",
                    "link": "https://elevenlabs.io"},
                   {"do": "בוחרים מסלול בתשלום שכולל שיבוט קול (Voice Cloning). התשלום עובר ישירות מכם אליהם.",
                    "see": "עמוד מסלולים: ליד כל מסלול כתוב אם הוא כולל Voice Cloning.",
                    "link": "https://elevenlabs.io/pricing"},
                   {"do": "בתפריט החשבון נכנסים ל-API Keys (לפעמים תחת Developers), יוצרים מפתח חדש ומאפשרים לו גישה ל-Voices ול-Text to Speech.",
                    "see": "נפתח חלון קטן: שם למפתח ורשימת הרשאות. אחרי Create המפתח מוצג פעם אחת בלבד — מעתיקים אותו מיד.",
                    "link": ""},
                   {"do": "חוזרים לכאן, לוחצים \"יש לי כבר מפתח\", מדביקים ולוחצים \"חיבור\". רוצים שנשבט את הקול? מסמנים גם את הסכמת שיבוט הקול בכרטיס.",
                    "see": "הכרטיס יראה \"מחובר ✓\". בלי הסכמת השיבוט, הקלטות משמשות רק כפסקול כמו שהן. אף פעם לא שולחים מפתח בצ׳אט.",
                    "link": ""}],
            "en": [{"do": "Open an account at ElevenLabs (the tool that clones your voice and reads in it), with your e-mail.",
                    "see": "A normal sign-up screen: e-mail or Google.",
                    "link": "https://elevenlabs.io"},
                   {"do": "Pick a paid plan that includes voice cloning. You pay them directly.",
                    "see": "A plans page: each plan says whether it includes Voice Cloning.",
                    "link": "https://elevenlabs.io/pricing"},
                   {"do": "In the account menu open API Keys (sometimes under Developers), create a new key and give it access to Voices and Text to Speech.",
                    "see": "A small window: a name for the key and a list of permissions. After Create the key is shown only once — copy it right away.",
                    "link": ""},
                   {"do": "Come back here, click \"I already have a key\", paste it and click \"Connect\". Want us to clone your voice? Also tick the voice-cloning consent on the card.",
                    "see": "The card shows \"Connected ✓\". Without the cloning consent, recordings are used only as an audio track, as they are. Never send a key in the chat.",
                    "link": ""}],
            "fr": [{"do": "Ouvrez un compte chez ElevenLabs (l'outil qui clone votre voix et lit avec), avec votre e-mail.",
                    "see": "Un écran d'inscription classique : e-mail ou Google.",
                    "link": "https://elevenlabs.io"},
                   {"do": "Choisissez une formule payante qui inclut le clonage de voix. Vous les payez directement.",
                    "see": "Une page de formules : chacune indique si elle inclut Voice Cloning.",
                    "link": "https://elevenlabs.io/pricing"},
                   {"do": "Dans le menu du compte, ouvrez API Keys (parfois sous Developers), créez une clé et donnez-lui l'accès à Voices et Text to Speech.",
                    "see": "Une petite fenêtre : un nom et une liste d'autorisations. Après Create, la clé ne s'affiche qu'une fois — copiez-la tout de suite.",
                    "link": ""},
                   {"do": "Revenez ici, cliquez sur « J'ai déjà une clé », collez-la et cliquez sur « Connecter ». Pour cloner votre voix, cochez aussi le consentement de clonage.",
                    "see": "La carte affiche « Connecté ✓ ». Sans le consentement, les enregistrements servent seulement de piste audio. Jamais de clé dans le chat.",
                    "link": ""}],
            "ar": [{"do": "افتحوا حسابًا في ElevenLabs (الأداة التي تستنسخ صوتكم وتقرأ به) ببريدكم.",
                    "see": "شاشة تسجيل عادية: بريد أو حساب Google.",
                    "link": "https://elevenlabs.io"},
                   {"do": "اختاروا باقة مدفوعة تشمل استنساخ الصوت. الدفع مباشرة منكم إليهم.",
                    "see": "صفحة باقات: بجانب كل باقة مكتوب إن كانت تشمل Voice Cloning.",
                    "link": "https://elevenlabs.io/pricing"},
                   {"do": "في قائمة الحساب افتحوا API Keys (أحيانًا تحت Developers)، أنشئوا مفتاحًا جديدًا وامنحوه صلاحية Voices وText to Speech.",
                    "see": "نافذة صغيرة: اسم للمفتاح وقائمة صلاحيات. بعد Create يظهر المفتاح مرة واحدة فقط — انسخوه فورًا.",
                    "link": ""},
                   {"do": "عودوا إلى هنا، اضغطوا \"لديّ مفتاح بالفعل\"، الصقوه واضغطوا \"ربط\". تريدون استنساخ صوتكم؟ حدّدوا أيضًا موافقة الاستنساخ في البطاقة.",
                    "see": "ستظهر البطاقة \"مرتبط ✓\". بدون موافقة الاستنساخ تُستخدم التسجيلات كمسار صوتي فقط. لا ترسلوا مفتاحًا في الدردشة أبدًا.",
                    "link": ""}],
            "ru": [{"do": "Откройте аккаунт в ElevenLabs (инструмент, который клонирует ваш голос и озвучивает им) на свой e-mail.",
                    "see": "Обычная регистрация: e-mail или Google.",
                    "link": "https://elevenlabs.io"},
                   {"do": "Выберите платный тариф с клонированием голоса. Оплата идёт напрямую от вас.",
                    "see": "Страница тарифов: у каждого указано, есть ли Voice Cloning.",
                    "link": "https://elevenlabs.io/pricing"},
                   {"do": "В меню аккаунта откройте API Keys (иногда в разделе Developers), создайте ключ и дайте ему доступ к Voices и Text to Speech.",
                    "see": "Небольшое окно: имя ключа и список разрешений. После Create ключ показывается один раз — скопируйте его сразу.",
                    "link": ""},
                   {"do": "Вернитесь сюда, нажмите «У меня уже есть ключ», вставьте его и нажмите «Подключить». Хотите клон голоса? Отметьте и согласие на клонирование.",
                    "see": "На карточке появится «Подключено ✓». Без согласия записи используются только как звуковая дорожка. Никогда не отправляйте ключ в чат.",
                    "link": ""}],
        },
        "key_where": _t("המפתח נמצא בחשבון ה-ElevenLabs, בתפריט החשבון ← API Keys (לפעמים תחת Developers).",
                        "The key is in your ElevenLabs account menu → API Keys (sometimes under Developers).",
                        "La clé se trouve dans le menu du compte ElevenLabs → API Keys (parfois sous Developers).",
                        "المفتاح في قائمة حساب ElevenLabs ← API Keys (أحيانًا تحت Developers).",
                        "Ключ — в меню аккаунта ElevenLabs → API Keys (иногда в разделе Developers)."),
        # The homepage is the domain of the pricing link that already existed
        "links": [{"label": _t("פתיחת חשבון", "Open an account", "Ouvrir un compte", "فتح حساب",
                               "Открыть аккаунт"),
                   "url": "https://elevenlabs.io"},
                  {"label": _t("מסלולים", "Plans", "Formules", "الباقات", "Тарифы"),
                   "url": "https://elevenlabs.io/pricing"}],
        "estimated_minutes": 5,
        "needs_api_key": True,
        "active": True,
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

# A guided entry's concierge steps ARE its guide's "do" lines — one text, no drift
for _entry in CONNECTIONS:
    if _entry.get("guide"):
        _entry["steps"] = {lang: [g["do"] for g in _entry["guide"].get(lang) or []] for lang in LANGS}

_BY_ID = {c["id"]: c for c in CONNECTIONS}


def guide_view(connection_id: str, lang: str = "he") -> dict:
    """What a dashboard card renders as its primary connect path: numbered
    steps, what the screen looks like, a direct link, where the key is.
    {} for an entry without a guide (or inactive). Links come only from the
    entry's own `links`, so nothing outside the config is ever shown."""
    entry = get(connection_id)
    if not entry or not entry.get("guide"):
        return {}
    allowed = {link["url"]: _pick(link["label"], lang) for link in entry["links"] if link["url"]}
    steps = []
    for item in _pick(entry["guide"], lang) or []:
        url = item.get("link") or ""
        steps.append({"do": item["do"], "see": item.get("see", ""),
                      "link": {"url": url, "label": allowed[url]} if url in allowed else None})
    return {"id": entry["id"], "title": _pick(entry["title"], lang), "steps": steps,
            "key_where": _pick(entry.get("key_where") or {"he": ""}, lang),
            "estimated_minutes": entry["estimated_minutes"]}


def get(connection_id: str, active_only: bool = True) -> dict:
    entry = _BY_ID.get((connection_id or "").strip())
    if not entry or (active_only and not entry["active"]):
        return {}
    return entry


def request_assist(client_id: int, connection_id: str) -> dict:
    """"Stuck? we'll do it with you": a staff request — one admin alert per
    client + connection a day — so the client is never left alone with a key
    or sent off to a website. ONE implementation for the guide's button
    (POST /api/client/connection-help/{id}/assist) and the chat card
    (agent_actions request_connection_help). ValueError: no such guide."""
    entry = get(connection_id)
    if not entry:
        raise ValueError("no_guide")
    from core import chat_fallback
    try:
        from agents.client_agent import get_client
        name = (get_client(client_id) or {}).get("name", "")
    except Exception:
        name = ""
    alerted = chat_fallback.alert_once(
        "connection_help", client_id, f"assist:{entry['id']}",
        f"client {client_id} ({name}) is stuck connecting '{entry['id']}' and pressed "
        f"\"we'll do it with you\" — contact them and connect it together", days=1)
    return {"requested": True, "alerted": alerted}


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
