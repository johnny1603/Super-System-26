"""The client home page's agent invitations and the prerequisites check
(docs/HOME-FOCUS.md).

Three things, one module, no new storage beyond two app_settings rows:

1. INVITATIONS — the family of cards in the style of ליאור's branding banner
   (avatar + one persuasive line + chevron). Each card is a service that
   REALLY exists in the system, shown only while the client has not started
   it, at most `MAX_INVITES` at a time. A click starts a conversation with that
   agent: the dashboard sends the client's own opening line (`opener`) and the
   agent answers with a ready proposal, as its rules already say. A card never
   carries an offer of its own and never names a tool or vendor.

2. PREREQUISITES — ONE check per service: what THIS agent needs connected for
   ITS job (`SERVICES[...]["needs"]`). A click on a card whose needs are not
   met does not start the job: the agent first says, calmly and in its own
   chat, "before we start, let's finish connecting what I need", lists exactly
   those items, one connect button each (the existing connect conversation,
   `connection_catalog.flow`, which gives the sign-up link when the client has
   no account yet), and the request is remembered (`invite_resume:<id>`). When
   every item is connected, the next home load hands the original request back
   (`resume`) and the conversation continues where it was asked. The same
   check reaches the chat (`prompt_block` → "prerequisites" in the payload, and
   `support_agent.PREREQ_RULE`).

3. ONBOARDING FOCUS — whether the client has finished the three first tasks
   (every required connection, the deep characterisation with the manager, the
   agreed 3-month goals). While not, the home page shows only those tasks and
   hides the idea feed behind a small counter.

Never raises to a caller: a read that fails hides that card / says "not
known", it never invents a state.
"""
import time
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "home_focus"
LANGS = ("he", "en", "fr", "ar", "ru")
RESUME_PREFIX = "invite_resume:"
RESUME_DAYS = 14
MAX_INVITES = 3
BLOCK_CACHE_SECONDS = 60


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or table.get("he") or ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db():
    from agents.client_agent import _db as db
    return db()


# ─── What each connection is, in words a client reads (never a tool's name) ──
# step key → the connect conversation's key (connection_catalog) and the
# client_accounts platforms that prove it.
STEPS = {
    "website": {"connect": "website", "platforms": ("wordpress",)},
    "google_ads": {"connect": "google_ads", "platforms": ("google_ads",)},
    "meta": {"connect": "meta", "platforms": ("meta_ads", "meta_page", "meta_instagram")},
    "creative": {"connect": "higgsfield", "platforms": ("higgsfield",)},
    "search_console": {"connect": "search_console", "platforms": ("search_console",)},
}
STEP_LABEL = {
    "website": _five("האתר שלכם", "Your website", "Votre site", "موقعكم", "Ваш сайт"),
    "google_ads": _five("חשבון Google Ads שלכם", "Your Google Ads account", "Votre compte Google Ads",
                        "حساب Google Ads الخاص بكم", "Ваш аккаунт Google Ads"),
    "meta": _five("פייסבוק ואינסטגרם של העסק", "Your business Facebook and Instagram",
                  "Le Facebook et l'Instagram de votre entreprise", "فيسبوك وإنستغرام الخاصان بنشاطكم",
                  "Facebook и Instagram вашего бизнеса"),
    "creative": _five("כלי התמונות והסרטונים מבוססי AI", "The AI image and video tool",
                      "L'outil d'images et de vidéos par IA", "أداة الصور والفيديو بالذكاء الاصطناعي",
                      "Инструмент ИИ для изображений и видео"),
    "search_console": _five("נתוני החיפוש של האתר בגוגל", "Your site's Google search data",
                            "Les données de recherche Google de votre site", "بيانات بحث Google لموقعكم",
                            "Данные поиска Google по вашему сайту"),
}
# Why THIS job needs it — one short clause per (service, step)
WHY = {
    ("media", "creative"): _five("שם נוצרים התמונות והסרטונים שלכם, על החשבון שלכם",
                                 "that's where your images and videos are made, on your own account",
                                 "c'est là que vos images et vidéos sont créées, sur votre propre compte",
                                 "هناك تُصنع صوركم وفيديوهاتكم، على حسابكم الخاص",
                                 "там создаются ваши изображения и видео, на вашем аккаунте"),
    ("avatar", "creative"): _five("שם נבנית הדמות שלכם והסרטונים שהיא מגישה",
                                  "that's where your character and the clips it presents are made",
                                  "c'est là que votre personnage et ses vidéos sont créés",
                                  "هناك تُبنى شخصيتكم والمقاطع التي تقدّمها",
                                  "там создаются ваш персонаж и его ролики"),
    ("social", "meta"): _five("כדי שאכין ואתזמן את הפוסטים בעמודים שלכם",
                              "so I can prepare and schedule the posts on your pages",
                              "pour préparer et programmer les publications sur vos pages",
                              "لأجهّز المنشورات وأجدولها على صفحاتكم",
                              "чтобы готовить и планировать публикации на ваших страницах"),
    ("organic", "website"): _five("כדי שאראה את העמודים שלכם ואכתוב בשבילם",
                                  "so I can see your pages and write for them",
                                  "pour voir vos pages et écrire pour elles",
                                  "لأرى صفحاتكم وأكتب لها",
                                  "чтобы видеть ваши страницы и писать для них"),
    ("organic", "search_console"): _five("כדי לראות על אילו חיפושים האתר כבר מופיע",
                                         "so I can see which searches your site already shows up for",
                                         "pour voir sur quelles recherches votre site apparaît déjà",
                                         "لأرى في أي عمليات بحث يظهر موقعكم",
                                         "чтобы видеть, по каким запросам сайт уже показывается"),
    ("google_ads", "google_ads"): _five("שם רצים הקמפיינים, והתקציב נשאר בשליטתכם",
                                        "that's where the campaigns run, and the budget stays in your hands",
                                        "c'est là que tournent les campagnes, et le budget reste entre vos mains",
                                        "هناك تعمل الحملات، وتبقى الميزانية بأيديكم",
                                        "там идут кампании, и бюджет остаётся под вашим контролем"),
}

FOCUS_LINE = _five(
    "לפני שנתחיל, בואו נסיים לחבר את מה שאני צריך בשביל זה:",
    "Before we can start, let's finish connecting what I need for this:",
    "Avant de commencer, terminons de connecter ce dont j'ai besoin pour cela :",
    "قبل أن نبدأ، لننتهِ من ربط ما أحتاجه لذلك:",
    "Прежде чем начать, давайте подключим то, что мне для этого нужно:")
FOCUS_AFTER = _five(
    "כשזה מחובר, אני חוזר בדיוק לכאן וממשיכים מאיפה שעצרנו.",
    "Once it's connected, I'll come right back here and we'll continue where we stopped.",
    "Une fois connecté, je reviens ici et nous reprenons là où nous en étions.",
    "عندما يكتمل الربط أعود إلى هنا ونكمل من حيث توقفنا.",
    "Когда подключим, я вернусь сюда, и продолжим с того же места.")


# ─── The services and their invitation cards ─────────────────────────────────
# `persona` = the dashboard window id; `face` = /assets/agents/<face>-*.webp
# ("" = the client's chosen manager). `needs` = the steps THIS job needs.
SERVICES = {
    "media": {"persona": "media", "face": "lior", "needs": ("creative",)},
    "avatar": {"persona": "media", "face": "lior", "needs": ("creative",)},
    "branding": {"persona": "media", "face": "lior", "needs": ()},
    "automation": {"persona": "general", "face": "", "needs": ()},
    "social": {"persona": "meta", "face": "maya", "needs": ("meta",)},
    "organic": {"persona": "website", "face": "uri", "needs": ("website",)},
    "google_ads": {"persona": "google", "face": "yoav", "needs": ("google_ads",)},
}

# The invitation cards, in the order they are offered. Branding has its own
# banner already (the studio, core/branding) — it is the model for these.
INVITES = ("avatar", "social", "organic", "google_ads", "automation")

INVITE_TEXT = {
    "avatar": {
        "title": _five("דמות AI משלכם, שמציגה את העסק בסרטונים",
                       "Your own AI character, presenting your business in videos",
                       "Votre propre personnage IA, qui présente votre entreprise en vidéo",
                       "شخصية ذكاء اصطناعي خاصة بكم تقدّم نشاطكم في الفيديوهات",
                       "Собственный ИИ-персонаж, который представляет ваш бизнес в видео"),
        "sub": _five("מתמונה אחת שלכם, או דמות שנעצב יחד. כל סרטון יוצא רק אחרי אישור שלכם.",
                     "From one photo of you, or a character we design together. Every clip goes out only after you approve it.",
                     "À partir d'une photo de vous, ou d'un personnage conçu ensemble. Chaque vidéo n'avance qu'après votre accord.",
                     "من صورة واحدة لكم أو شخصية نصممها معًا. لا يخرج أي مقطع إلا بعد موافقتكم.",
                     "Из одной вашей фотографии или персонажа, которого создадим вместе. Каждый ролик — только после вашего одобрения."),
        "opener": _five("אני רוצה ליצור דמות AI משלי שתציג את העסק בסרטונים. מה הצעד הראשון?",
                        "I'd like to create my own AI character to present my business in videos. What's the first step?",
                        "J'aimerais créer mon propre personnage IA pour présenter mon entreprise en vidéo. Quelle est la première étape ?",
                        "أريد إنشاء شخصية ذكاء اصطناعي خاصة بي تقدّم نشاطي في الفيديوهات. ما الخطوة الأولى؟",
                        "Хочу создать своего ИИ-персонажа, который будет представлять бизнес в видео. С чего начнём?"),
    },
    "social": {
        "title": _five("מוכנים לקחת את הנוכחות שלכם ברשתות צעד קדימה?",
                       "Ready to take your social presence a step further?",
                       "Prêt à faire passer votre présence sur les réseaux à l'étape suivante ?",
                       "هل أنتم مستعدون للارتقاء بحضوركم على الشبكات خطوة إلى الأمام؟",
                       "Готовы сделать следующий шаг в соцсетях?"),
        "sub": _five("אביא לכם את התוכנית של השבוע, מוכנה לאישור בלחיצה אחת.",
                     "I'll bring you this week's plan, ready to approve in one tap.",
                     "Je vous apporte le plan de la semaine, prêt à valider en un clic.",
                     "سأحضر لكم خطة هذا الأسبوع جاهزة للموافقة بنقرة واحدة.",
                     "Принесу план на неделю — готовый к одобрению одним нажатием."),
        "opener": _five("אני מוכן לקחת את הנוכחות שלנו ברשתות צעד קדימה. מה הצעד הבא?",
                        "I'm ready to take our social presence a step further. What's the next step?",
                        "Je suis prêt à faire avancer notre présence sur les réseaux. Quelle est la prochaine étape ?",
                        "أنا مستعد للارتقاء بحضورنا على الشبكات. ما الخطوة التالية؟",
                        "Я готов сделать следующий шаг в соцсетях. Что дальше?"),
    },
    "organic": {
        "title": _five("שלקוחות ימצאו אתכם בגוגל, בלי לשלם על כל קליק",
                       "Let customers find you on Google, without paying per click",
                       "Que les clients vous trouvent sur Google, sans payer chaque clic",
                       "ليجدكم العملاء على Google دون الدفع مقابل كل نقرة",
                       "Пусть клиенты находят вас в Google без оплаты за каждый клик"),
        "sub": _five("אתחיל ממה שהכי כדאי לעסק שלכם, ואראה לכם איפה ההזדמנויות.",
                     "I'll start with what pays off most for your business and show you where the opportunities are.",
                     "Je commence par ce qui rapporte le plus à votre entreprise et vous montre les opportunités.",
                     "سأبدأ بما يفيد نشاطكم أكثر وأريكم أين الفرص.",
                     "Начну с самого выгодного для вашего бизнеса и покажу, где возможности."),
        "opener": _five("אני רוצה שימצאו אותי בגוגל בלי פרסום ממומן. מה הצעד הבא?",
                        "I'd like to be found on Google without paid ads. What's the next step?",
                        "J'aimerais être trouvé sur Google sans publicité payante. Quelle est la prochaine étape ?",
                        "أريد أن يجدني الناس على Google دون إعلانات مدفوعة. ما الخطوة التالية؟",
                        "Хочу, чтобы меня находили в Google без платной рекламы. Что дальше?"),
    },
    "google_ads": {
        "title": _five("לקוחות שמחפשים אתכם בגוגל, ממש עכשיו",
                       "Customers searching for you on Google, right now",
                       "Des clients qui vous cherchent sur Google, en ce moment",
                       "عملاء يبحثون عنكم على Google الآن",
                       "Клиенты, которые ищут вас в Google прямо сейчас"),
        "sub": _five("נתכנן יחד קמפיין ראשון. שום דבר לא רץ לפני שאישרתם, ואתם קובעים את התקציב.",
                     "We'll plan a first campaign together. Nothing runs before you approve it, and you set the budget.",
                     "Nous planifions ensemble une première campagne. Rien ne tourne avant votre accord, et vous fixez le budget.",
                     "سنخطط معًا لحملة أولى. لا يعمل شيء قبل موافقتكم، وأنتم تحددون الميزانية.",
                     "Спланируем первую кампанию вместе. Ничего не запускается без вашего одобрения, бюджет задаёте вы."),
        "opener": _five("אני רוצה להתחיל לפרסם בגוגל. מה הצעד הבא?",
                        "I'd like to start advertising on Google. What's the next step?",
                        "J'aimerais commencer à faire de la publicité sur Google. Quelle est la prochaine étape ?",
                        "أريد أن أبدأ الإعلان على Google. ما الخطوة التالية؟",
                        "Хочу начать рекламу в Google. Что дальше?"),
    },
    "automation": {
        "title": _five("לא לפספס אף לקוח, גם כשאתם לא ליד הטלפון",
                       "Never miss a customer, even when you're away from your phone",
                       "Ne ratez plus aucun client, même loin de votre téléphone",
                       "لا تفوّتوا أي عميل حتى وأنتم بعيدون عن الهاتف",
                       "Не упускайте ни одного клиента, даже когда вы не у телефона"),
        "sub": _five("מענה מיידי לפנייה חדשה ותשובות גם מחוץ לשעות העבודה, בערוצי ההודעות שלכם. נבנה יחד עם הצוות שלנו.",
                     "An instant reply to every new enquiry, and answers outside working hours, on your messaging channels. Built with our team.",
                     "Une réponse immédiate à chaque nouvelle demande, et des réponses hors horaires, sur vos messageries. Mis en place avec notre équipe.",
                     "ردّ فوري على كل استفسار جديد وإجابات خارج ساعات العمل على قنوات الرسائل لديكم. نبنيه مع فريقنا.",
                     "Мгновенный ответ на каждую новую заявку и ответы в нерабочее время в ваших мессенджерах. Настраиваем вместе с нашей командой."),
        "opener": _five("ספרו לי על אוטומציה לשיחות: מענה מיידי ללידים ותשובות מחוץ לשעות העבודה.",
                        "Tell me about chat automation: instant replies to leads and answers outside working hours.",
                        "Parlez-moi de l'automatisation des conversations : réponses immédiates aux prospects et hors horaires.",
                        "حدّثوني عن أتمتة المحادثات: ردود فورية على العملاء المحتملين وإجابات خارج ساعات العمل.",
                        "Расскажите об автоматизации переписки: мгновенные ответы на заявки и ответы в нерабочее время."),
    },
}


# ─── Reads (each one a seam the tests replace) ───────────────────────────────

def _connected_platforms(client_id: int) -> set:
    rows = (_db().table("client_accounts").select("platform,status")
            .eq("client_id", client_id).eq("status", "active").execute().data or [])
    return {r.get("platform") for r in rows}


def _package(client_id: int) -> dict:
    """{"resolved", "steps": {keys}, "services": [...]} from the purchased package."""
    from core import client_journey
    checklist = client_journey.required_connections(client_id)
    return {"resolved": bool(checklist.get("resolved")),
            "steps": {s["key"] for s in checklist.get("steps") or []},
            "services": checklist.get("services") or []}


def _search_console_on() -> bool:
    try:
        from core import round2_flags
        return bool(round2_flags.search_console_enabled())
    except Exception:
        return False


def _activity_exists(client_id: int, action_types: tuple) -> bool:
    rows = (_db().table("client_activity").select("id").eq("client_id", client_id)
            .in_("action_type", list(action_types)).limit(1).execute().data or [])
    return bool(rows)


def _app_setting(key: str):
    rows = _db().table("app_settings").select("value").eq("key", key).limit(1).execute().data or []
    return rows[0].get("value") if rows else None


def _save_setting(key: str, value):
    _db().table("app_settings").upsert({"key": key, "value": value, "updated_at": _now().isoformat()},
                                       on_conflict="key").execute()


def _delete_setting(key: str):
    _db().table("app_settings").delete().eq("key", key).execute()


# ─── Prerequisites: ONE check for every card and every chat ──────────────────

def needs(service: str) -> tuple:
    steps = tuple(SERVICES.get(service, {}).get("needs") or ())
    if service == "organic" and _search_console_on():
        steps = steps + ("search_console",)
    return steps


def missing(service: str, connected: set) -> list:
    return [s for s in needs(service) if not any(p in connected for p in STEPS[s]["platforms"])]


def _organic_covered(client_id: int) -> bool:
    """Organic's connections matter only once the client HAS the service: a
    package without it gets Uri's small taste and the ONE upgrade card first,
    never "connect your site" for a service they don't have."""
    from core import seo_report
    return bool(seo_report.coverage(client_id).get("covered"))


def gaps_for(client_id: int, service: str, connected: set) -> list:
    if service == "organic" and not _organic_covered(client_id):
        return []
    return missing(service, connected)


def prerequisites(client_id: int, service: str, lang: str = None, connected: set = None) -> dict:
    """What THIS service's agent needs connected, and what is still missing.
    `ready` False → the agent says FOCUS_LINE and lists `items` (one connect
    button each) instead of starting."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    if service not in SERVICES:
        raise ValueError("unknown_service")
    lang = lang if lang in LANGS else "he"
    if connected is None:
        connected = _connected_platforms(client_id)
    gaps = gaps_for(client_id, service, connected)
    items = [{"step": s, "connect": STEPS[s]["connect"], "label": pick(STEP_LABEL[s], lang),
              "why": pick(WHY.get((service, s), {}), lang)} for s in gaps]
    lines = [pick(FOCUS_LINE, lang)] + [f"• {i['label']}" + (f" — {i['why']}" if i["why"] else "") for i in items]
    if items:
        lines.append(pick(FOCUS_AFTER, lang))
    return {"service": service, "persona": SERVICES[service]["persona"], "ready": not items,
            "items": items, "message": "\n".join(lines) if items else ""}


# ─── Which invitations a client sees ─────────────────────────────────────────

def _started(client_id: int, service: str, cover: dict) -> bool:
    """True = the client already started this service: its card is not shown."""
    if service == "avatar":
        from agents import avatar_agent
        return bool(avatar_agent.list_ready_avatars(client_id))
    if service == "social":
        from core import content_team
        return content_team.latest(client_id).get("status") == "approved"
    if service == "organic":
        from core import seo_report
        return bool(cover.get("organic")) and bool(seo_report.latest(client_id))
    if service == "google_ads":
        return _activity_exists(client_id, ("campaign_created", "google_ads_campaign_created"))
    if service == "automation":
        return "manychat" in cover.get("connected", set())
    return False


def _offered(client_id: int, service: str, package: dict) -> bool:
    """The service really exists for THIS client: in the package (or, for
    organic, offered through its one upgrade card) and open to them."""
    if service == "avatar":
        from core import avatar_video
        return "avatar" in package["steps"] and avatar_video.presenter_allowed(client_id)
    if service == "social":
        return "meta" in package["steps"]
    if service == "google_ads":
        return "google_ads" in package["steps"]
    if service in ("organic", "automation"):
        return True
    return False


def invites(client_id: int, lang: str = None) -> list:
    """The cards to show, most relevant first, at most MAX_INVITES. A package
    that cannot be read = no cards (never a guess)."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    lang = lang if lang in LANGS else "he"
    try:
        package = _package(client_id)
        connected = _connected_platforms(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: package/accounts unreadable ({type(e).__name__})")
        return []
    if not package["resolved"]:
        return []
    from core import seo_report
    cover = {"organic": seo_report.covered(package["services"]), "connected": connected}
    out = []
    for service in INVITES:
        try:
            if not _offered(client_id, service, package) or _started(client_id, service, cover):
                continue
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: {service} card skipped ({type(e).__name__})")
            continue
        text = INVITE_TEXT[service]
        spec = SERVICES[service]
        out.append({"id": service, "persona": spec["persona"], "face": spec["face"],
                    "title": pick(text["title"], lang), "sub": pick(text["sub"], lang),
                    "ready": not missing(service, connected)})
        if len(out) >= MAX_INVITES:
            break
    return out


# ─── A click: start, or focus first and remember the request ─────────────────

def start(client_id: int, service: str, lang: str = None) -> dict:
    """The card's click. Ready → the client's opening line for that agent's
    chat. Not ready → the focus message (posted into that agent's thread, in
    its voice) + one connect item per gap, and the request remembered."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    if service not in INVITE_TEXT:
        raise ValueError("unknown_service")
    lang = lang if lang in LANGS else "he"
    pre = prerequisites(client_id, service, lang)
    opener = pick(INVITE_TEXT[service]["opener"], lang)
    out = {"service": service, "persona": pre["persona"], "ready": pre["ready"], "opener": opener,
           "items": pre["items"], "message": pre["message"]}
    if pre["ready"]:
        forget_resume(client_id)
        return out
    _save_setting(f"{RESUME_PREFIX}{int(client_id)}",
                  {"service": service, "language": lang, "at": _now().isoformat()})
    try:
        from agents.client_agent import log_communication
        from agents.support_agent import persona_channel
        log_communication(client_id, "outbound", persona_channel(pre["persona"]), pre["message"])
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: focus message not stored ({type(e).__name__})")
    return out


def forget_resume(client_id: int):
    try:
        _delete_setting(f"{RESUME_PREFIX}{int(client_id)}")
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: resume row not cleared ({type(e).__name__})")


def resume(client_id: int, connected: set = None) -> dict:
    """The request a client made before its connections were there, handed
    back ONCE when everything it needs is connected ({} otherwise). Older
    than RESUME_DAYS = forgotten."""
    try:
        value = _app_setting(f"{RESUME_PREFIX}{int(client_id)}")
    except Exception:
        return {}
    if not isinstance(value, dict) or value.get("service") not in INVITE_TEXT:
        return {}
    try:
        at = datetime.fromisoformat(str(value.get("at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        at = None
    if at is None or _now() - at > timedelta(days=RESUME_DAYS):
        forget_resume(client_id)
        return {}
    service, lang = value["service"], value.get("language") or "he"
    if connected is None:
        connected = _connected_platforms(client_id)
    if gaps_for(client_id, service, connected):
        return {}
    forget_resume(client_id)
    return {"service": service, "persona": SERVICES[service]["persona"],
            "opener": pick(INVITE_TEXT[service]["opener"], lang)}


# ─── The same check in the chat ──────────────────────────────────────────────

_block_cache = {}


def prompt_block(client_id: int, lang: str = None) -> dict:
    """`prerequisites` in every agent's payload: per service, the items still
    missing ({} when nothing is missing or the read failed). Cached 60 s."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    hit = _block_cache.get(int(client_id))
    if hit and hit[0] > time.time() - BLOCK_CACHE_SECONDS:
        return hit[1]
    try:
        connected = _connected_platforms(client_id)
    except Exception:
        return {}
    out = {}
    for service in SERVICES:
        try:
            gaps = gaps_for(client_id, service, connected)
        except Exception:
            continue   # unknown coverage: no claim about this job
        if gaps:
            out[service] = {"missing": [pick(STEP_LABEL[s], lang) for s in gaps],
                            "connect_cards": [_CONNECT_CARD[s] for s in gaps]}
    _block_cache[int(client_id)] = (time.time(), out)
    return out


# The chat's connect card for each step (agent_actions ACTIONS names)
_CONNECT_CARD = {"website": "connect_website", "google_ads": "connect_google_ads", "meta": "connect_meta",
                 "creative": "connect_creative_tool", "search_console": "connect_search_console"}


# ─── Onboarding focus (the first three tasks) ────────────────────────────────

def onboarding(client_id: int) -> dict:
    """{"known", "done", "connections": {...}, "characterised", "goals_agreed"}.
    `known` False = something could not be read: the page shows its normal
    home (never a focus mode built on a guess)."""
    try:
        from core import client_journey
        status = client_journey.connection_status(client_id)
        from agents.interview_agent import is_completed
        characterised = bool(is_completed(client_id))
        from core import quarter_goals
        agreed = bool(quarter_goals.agreed(client_id))
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: onboarding state unknown ({type(e).__name__})")
        return {"known": False, "done": True}
    if not status.get("resolved"):
        return {"known": False, "done": True}
    connections = {"complete": bool(status.get("complete")), "done": status.get("done_count", 0),
                   "total": status.get("total_count", 0), "next": status.get("next_step") or ""}
    # Onboarding = the connections + the deep characterisation (the owner's definition). The
    # goals are the third task: still shown until agreed, but alone they never hide the feed.
    return {"known": True, "done": connections["complete"] and characterised,
            "connections": connections, "characterised": characterised, "goals_agreed": agreed}
