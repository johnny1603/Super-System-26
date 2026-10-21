"""agent_actions — what an agent may SET IN MOTION, and only through a human click.

The shape (propose → approve → execute):

1. A chat model (client concierge/persona, or a Control Room persona) may
   return `proposed_action` {name, params, summary}. It runs nothing.
2. `propose()` checks it in CODE against the whitelist below: the name, who
   may propose it from this chat, the internal-client flag, whether it makes
   sense for this client right now (client_state), and every param against
   its validator. Only then is a row stored (table `agent_actions`,
   migrations/2026-10-21-agent-actions.sql) and a confirmation card returned.
   The card's text is OURS (registry), never the model's summary.
3. Only a human click calls `decide()`. The browser sends the proposal id and
   approve/cancel — never params, so there is nothing to tamper with. The
   server re-checks everything from the stored row: caller vs approver,
   ownership, expiry, internal-client flag, params, then claims the row with a
   compare-and-set (status open → running) so a double click runs it once.
4. The action runs through the SAME function its fallback button uses
   (website_agent.configure_lead_conversion, core/verification_demo,
   engagement_agent.decide_suggestion, the OAuth start routes); the action's
   own guards (container match, dead token, already live, PAUSED, Private)
   stay where they are and refuse here exactly as they do for the button.
5. The outcome is written to the audit row (a short code — no Google data, see
   the migration) and posted into the chat thread it came from.

Who approves: "client" actions are low-risk and the client approves them
with one click (a reconnect, approving their own pending suggestion).
Anything that publishes or can spend is "admin": proposed from the client
chat it becomes a staff request (status awaiting_staff, the team is alerted,
the client is told plainly what happens next) and is approved in the
Control Room.
"""
import json
import re
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "agent_actions"
TABLE = "agent_actions"
PROPOSAL_TTL_HOURS = 24
OPEN_STATUSES = ("proposed", "awaiting_staff")
LANGS = ("he", "en", "fr", "ar", "ru")


class ActionFailed(Exception):
    """An action's own refusal or failure. `code` goes to the audit row;
    the message only to the admin/log (it may carry platform detail)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


# ─── Seams (patched by tests; everything external goes through these) ────────

def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _client(client_id: int) -> dict:
    from agents.client_agent import get_client
    return get_client(client_id) or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(message: str, **fields):
    from core.redaction import redact
    print(json.dumps({"severity": fields.pop("severity", "INFO"), "message": redact(message),
                      "service": SERVICE_NAME, **fields}, ensure_ascii=False, default=str), flush=True)


def _alert(issue: str):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, [issue])


def _post_client_message(client_id: int, persona: str, text: str):
    from agents.client_agent import log_communication
    from agents.support_agent import persona_channel
    log_communication(client_id, "outbound", persona_channel(persona), text)


def _post_admin_message(client_id: int, persona: str, text: str):
    from core import control_room
    control_room._store(client_id, persona, "agent", text)


# ─── Param validators (value, client_id) -> normalized value | ValueError ────

_GA4_RE = re.compile(r"^G-[A-Z0-9]{4,15}$")


def _v_ga4(value, client_id: int):
    value = str(value or "").strip().upper()
    if value and not _GA4_RE.match(value):
        raise ValueError("ga4_measurement_id must look like G-XXXXXXX")
    return value


def _v_pending_suggestion(value, client_id: int):
    try:
        sid = int(value)
    except (TypeError, ValueError):
        raise ValueError("suggestion_id must be a number")
    from agents.engagement_agent import get_suggestions
    if sid not in {s.get("id") for s in get_suggestions(client_id, status="pending", limit=50)}:
        raise ValueError("not one of this client's pending suggestions")
    return sid


# ─── Runners: the SAME functions the fallback buttons call ───────────────────

def _run_configure_conversion(client_id: int, params: dict, background_tasks):
    from agents.website_agent import configure_lead_conversion
    result = configure_lead_conversion(client_id, params.get("ga4_measurement_id", ""))
    if not result.get("success"):
        raise ActionFailed("refused_by_action", "; ".join(map(str, result.get("errors") or ["unknown"])))
    return result


def _run_demo(fn_name: str):
    def run(client_id: int, params: dict, background_tasks):
        from core import verification_demo
        try:
            return getattr(verification_demo, fn_name)(client_id)
        except verification_demo.DemoRefused as e:
            raise ActionFailed("refused_by_action", str(e))
    return run


def _run_approve_suggestion(client_id: int, params: dict, background_tasks):
    from agents.engagement_agent import decide_suggestion
    result = decide_suggestion(client_id, params["suggestion_id"], "approved", background_tasks)
    if not result.get("success"):
        raise ActionFailed("refused_by_action", str(result.get("error") or "unknown"))
    return result


# ─── Availability: does offering this make sense for THIS client now? ───────

def _cards(state: dict) -> dict:
    return ((state or {}).get("channels") or {}).get("by_channel") or {}


def _channel_needs_connect(channel: str):
    def check(state: dict, client: dict) -> bool:
        card = _cards(state).get(channel) or {}
        return (card.get("status") == "not_connected"
                or (card.get("status") == "needs_attention" and card.get("reason") == "reconnect"))
    return check


def _gtm_needs_connect(state: dict, client: dict) -> bool:
    tracking = (state or {}).get("tracking") or {}
    if tracking.get("connected"):
        return bool(tracking.get("needs_reconnect"))
    return _channel_needs_connect("website_tracking")(state, client)


def _conversion_setup_possible(state: dict, client: dict) -> bool:
    tracking = (state or {}).get("tracking") or {}
    return (bool(tracking.get("connected")) and not tracking.get("needs_reconnect")
            and tracking.get("lead_event_live") is not True)


def _connected(platform: str):
    def check(state: dict, client: dict) -> bool:
        return platform in (((state or {}).get("connections") or {}).get("active_platforms") or [])
    return check


def _has_pending_suggestions(state: dict, client: dict) -> bool:
    return bool(((state or {}).get("approvals") or {}).get("waiting_for_client"))


# ─── The whitelist ───────────────────────────────────────────────────────────
# approver: "client" (one click by the client) | "admin" (Control Room).
# client_requestable: an admin action the CLIENT chat may still propose — it
#   becomes a staff request, never a client button.
# kind: "execute" (server runs it) | "link" (the click navigates to an OAuth
#   start route — the consent itself is the client's) | "focus" (the click
#   opens a dashboard card that carries a consent the click must not bypass).

def _txt(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


ACTIONS = {
    "configure_conversion": {
        "approver": "admin", "client_requestable": True, "internal_only": False, "kind": "execute",
        "does": "Adds ONE form-submission trigger and ONE GA4 generate_lead event tag to the client's "
                "Tag Manager container, creates a version and PUBLISHES it, so leads from their site "
                "are counted. Refused if the site loads a different container, the connection needs "
                "renewing, or a lead event is already live.",
        "params": {"ga4_measurement_id": {"required": False, "validate": _v_ga4,
                                          "hint": "G-XXXXXXX, only if known from the data; empty = detected from the site"}},
        "available": _conversion_setup_possible,
        "run": _run_configure_conversion,
        "title": _txt("הפעלת מדידת לידים באתר", "Turn on lead tracking on your website",
                      "Activer le suivi des prospects sur votre site", "تفعيل قياس العملاء المحتملين في موقعكم",
                      "Включить учёт заявок на сайте"),
        "what": _txt("נוסיף ל-Tag Manager של האתר מדידה של שליחת טופס (אירוע generate_lead) ונפרסם אותה, כך שכל ליד מהאתר ייספר. תגים קיימים לא משתנים.",
                     "We'll add form-submission tracking (a generate_lead event) to your site's Tag Manager and publish it, so every lead from your site is counted. Existing tags stay as they are.",
                     "Nous ajouterons le suivi des envois de formulaire (événement generate_lead) au Tag Manager de votre site et le publierons, pour compter chaque prospect. Les balises existantes ne changent pas.",
                     "سنضيف قياس إرسال النماذج (حدث generate_lead) إلى Tag Manager في موقعكم وننشره، لكي يُحتسب كل عميل محتمل. الوسوم الحالية لا تتغير.",
                     "Мы добавим в Tag Manager вашего сайта учёт отправки форм (событие generate_lead) и опубликуем его, чтобы считалась каждая заявка. Существующие теги не меняются."),
        "done": _txt("מדידת הלידים באתר פעילה ✓ מעכשיו כל שליחת טופס נספרת.",
                     "Lead tracking on your site is live ✓ Every form submission is counted from now on.",
                     "Le suivi des prospects est actif ✓ Chaque envoi de formulaire est désormais compté.",
                     "قياس العملاء المحتملين فعّال ✓ من الآن يُحتسب كل إرسال نموذج.",
                     "Учёт заявок на сайте включён ✓ Теперь считается каждая отправка формы."),
    },
    "demo_google_ads_campaign": {
        "approver": "admin", "client_requestable": False, "internal_only": True, "kind": "execute",
        "does": "Google verification demo: creates ONE test search campaign in the connected Google Ads "
                "account, created PAUSED with a 5 ILS/day budget (spends nothing unless enabled). "
                "Internal test clients only.",
        "params": {},
        "available": _connected("google_ads"),
        "run": _run_demo("create_test_campaign"),
        "title": _txt("קמפיין בדיקה ב-Google Ads (מושהה)", "Test Google Ads campaign (paused)",
                      "Campagne test Google Ads (en pause)", "حملة اختبار في Google Ads (متوقفة)",
                      "Тестовая кампания Google Ads (на паузе)"),
        "what": _txt("ייווצר בחשבון Google Ads של לקוח הבדיקה קמפיין חיפוש אחד במצב PAUSED, תקציב 5 ₪ ליום — לא רץ ולא מוציא כסף.",
                     "Creates one search campaign in the test client's Google Ads account, status PAUSED, 5 ILS/day — it does not run or spend.",
                     "Crée une campagne de recherche en PAUSE (5 ILS/jour) — elle ne tourne pas.",
                     "ينشئ حملة بحث واحدة بحالة PAUSED بميزانية 5 شيكل يوميًا — لا تعمل ولا تنفق.",
                     "Создаёт одну поисковую кампанию в статусе PAUSED, 5 ILS/день — она не работает и не тратит."),
        "done": _txt("קמפיין הבדיקה נוצר — מושהה ✓", "Test campaign created — paused ✓",
                     "Campagne test créée — en pause ✓", "تم إنشاء حملة الاختبار — متوقفة ✓",
                     "Тестовая кампания создана — на паузе ✓"),
    },
    "demo_youtube_upload": {
        "approver": "admin", "client_requestable": False, "internal_only": True, "kind": "execute",
        "does": "Google verification demo: uploads the smallest video (<= 50 MB) from the client's Drive "
                "media folder to their YouTube channel as PRIVATE. Internal test clients only.",
        "params": {},
        "available": _connected("youtube"),
        "run": _run_demo("upload_test_video"),
        "title": _txt("סרטון בדיקה ל-YouTube (פרטי)", "Test YouTube upload (private)",
                      "Vidéo test YouTube (privée)", "رفع فيديو اختبار إلى YouTube (خاص)",
                      "Тестовое видео на YouTube (приватное)"),
        "what": _txt("הסרטון הקטן ביותר מתיקיית המדיה של לקוח הבדיקה יועלה לערוץ שלו כפרטי (Private) — אף אחד לא יראה אותו.",
                     "The smallest video in the test client's media folder is uploaded to their channel as Private — nobody else can see it.",
                     "La plus petite vidéo du dossier média est mise en ligne en Privé.",
                     "يُرفع أصغر فيديو من مجلد الوسائط إلى القناة كخاص.",
                     "Самое маленькое видео из папки медиа загружается на канал как приватное."),
        "done": _txt("הסרטון הועלה — פרטי ✓", "Video uploaded — private ✓", "Vidéo mise en ligne — privée ✓",
                     "تم رفع الفيديو — خاص ✓", "Видео загружено — приватное ✓"),
    },
    "connect_tag_manager": {
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "link",
        "link": "/api/oauth/gtm/start",
        "does": "Opens Google's approval screen to (re)connect the client's Tag Manager — the fix when "
                "tracking is not connected, expired, lost access or lacks permissions.",
        "params": {},
        "available": _gtm_needs_connect,
        "title": _txt("חיבור מחדש של מדידת האתר", "Reconnect your website tracking",
                      "Reconnecter le suivi du site", "إعادة ربط قياس الموقع", "Переподключить аналитику сайта"),
        "what": _txt("ייפתח מסך האישור של Google — מאשרים פעם אחת, וחוזרים לכאן.",
                     "Google's approval screen opens — approve once and you're back here.",
                     "L'écran d'autorisation de Google s'ouvre — validez une fois et revenez ici.",
                     "ستفتح شاشة موافقة Google — وافقوا مرة واحدة وتعودون إلى هنا.",
                     "Откроется окно подтверждения Google — подтвердите один раз и вернитесь сюда."),
        "done": _txt("פותח את מסך האישור של Google…", "Opening Google's approval screen…",
                     "Ouverture de l'écran Google…", "جارٍ فتح شاشة موافقة Google…", "Открываю окно Google…"),
    },
    "connect_youtube": {
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "link",
        "link": "/api/oauth/youtube/start",
        "does": "Opens Google's approval screen to (re)connect the client's YouTube channel.",
        "params": {},
        "available": _channel_needs_connect("youtube"),
        "title": _txt("חיבור ערוץ YouTube", "Connect your YouTube channel", "Connecter votre chaîne YouTube",
                      "ربط قناة YouTube", "Подключить канал YouTube"),
        "what": _txt("ייפתח מסך האישור של Google לערוץ — מאשרים פעם אחת, וחוזרים לכאן.",
                     "Google's approval screen for your channel opens — approve once and you're back here.",
                     "L'écran d'autorisation Google s'ouvre — validez une fois.",
                     "ستفتح شاشة موافقة Google للقناة — وافقوا مرة واحدة.",
                     "Откроется окно подтверждения Google для канала — подтвердите один раз."),
        "done": _txt("פותח את מסך האישור של Google…", "Opening Google's approval screen…",
                     "Ouverture de l'écran Google…", "جارٍ فتح شاشة موافقة Google…", "Открываю окно Google…"),
    },
    "connect_meta": {
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "link",
        "link": "/api/oauth/meta/start",
        "does": "Opens Meta's approval screen to (re)connect the client's Facebook/Instagram.",
        "params": {},
        "available": _channel_needs_connect("meta"),
        "title": _txt("חיבור פייסבוק ואינסטגרם", "Connect Facebook and Instagram", "Connecter Facebook et Instagram",
                      "ربط فيسبوك وإنستغرام", "Подключить Facebook и Instagram"),
        "what": _txt("ייפתח מסך האישור של Meta — מאשרים פעם אחת, וחוזרים לכאן.",
                     "Meta's approval screen opens — approve once and you're back here.",
                     "L'écran d'autorisation Meta s'ouvre — validez une fois.",
                     "ستفتح شاشة موافقة Meta — وافقوا مرة واحدة.",
                     "Откроется окно подтверждения Meta — подтвердите один раз."),
        "done": _txt("פותח את מסך האישור של Meta…", "Opening Meta's approval screen…",
                     "Ouverture de l'écran Meta…", "جارٍ فتح شاشة موافقة Meta…", "Открываю окно Meta…"),
    },
    "connect_google_ads": {
        # "focus", not a direct link: connecting Google Ads requires the
        # staff-access consent tick on its card (GOOGLE_ADS_STAFF_CONSENT, the
        # basis of the privacy policy's human-access section). The click opens
        # that card; it must never skip the consent.
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "focus",
        "focus": "gadsBtn",
        "does": "Opens the Google Ads connection card (it carries the staff-access consent the client "
                "must tick) so the client can (re)connect.",
        "params": {},
        "available": _channel_needs_connect("google_ads"),
        "title": _txt("חיבור Google Ads", "Connect Google Ads", "Connecter Google Ads", "ربط Google Ads",
                      "Подключить Google Ads"),
        "what": _txt("ייפתח כרטיס החיבור של Google Ads — מסמנים את תיבת ההסכמה ומאשרים מול Google.",
                     "The Google Ads card opens — tick the consent box and approve with Google.",
                     "La carte Google Ads s'ouvre — cochez le consentement et validez avec Google.",
                     "ستفتح بطاقة Google Ads — حددوا خانة الموافقة ووافقوا لدى Google.",
                     "Откроется карточка Google Ads — отметьте согласие и подтвердите в Google."),
        "done": _txt("פותח את כרטיס החיבור…", "Opening the connection card…", "Ouverture de la carte…",
                     "جارٍ فتح البطاقة…", "Открываю карточку…"),
    },
    "approve_suggestion": {
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
        "does": "Approves ONE of the client's own pending suggestions (approvals.waiting_for_client) — "
                "the same as their approve tap in the dashboard; approving is what lets the team start.",
        "params": {"suggestion_id": {"required": True, "validate": _v_pending_suggestion,
                                     "hint": "suggestion_id copied exactly from approvals.waiting_for_client"}},
        "available": _has_pending_suggestions,
        "run": _run_approve_suggestion,
        "title": _txt("אישור הצעה", "Approve a suggestion", "Approuver une suggestion", "الموافقة على اقتراح",
                      "Одобрить предложение"),
        "what": _txt("ההצעה \"{title}\" תאושר, והצוות יתחיל לעבוד עליה.",
                     "\"{title}\" will be approved and the team starts working on it.",
                     "« {title} » sera approuvée et l'équipe s'y met.",
                     "سيتم اعتماد \"{title}\" ويبدأ الفريق العمل عليها.",
                     "«{title}» будет одобрено, и команда приступит к работе."),
        "done": _txt("אושר ✓ הצוות מתחיל לעבוד על זה.", "Approved ✓ The team is on it.",
                     "Approuvé ✓ L'équipe s'en occupe.", "تمت الموافقة ✓ الفريق يعمل على ذلك.",
                     "Одобрено ✓ Команда приступает."),
    },
}

# Fixed lines for every other outcome (code decides, the model never writes them)
_STAFF_REQUEST = _txt(
    "שלחתי לצוות לאישור — מכיוון שזה משנה משהו באוויר, איש צוות מאשר את זה. אעדכן כאן כשזה בוצע.",
    "I've sent this to the team for approval — since it changes something live, a team member approves it. I'll update you here when it's done.",
    "Je l'ai envoyé à l'équipe pour validation — comme cela modifie quelque chose en ligne, un membre de l'équipe l'approuve. Je vous tiens au courant ici.",
    "أرسلت ذلك إلى الفريق للموافقة — لأنه يغيّر شيئًا مباشرًا، يوافق عليه أحد أعضاء الفريق. سأبلغكم هنا عند التنفيذ.",
    "Я отправил это команде на подтверждение — это меняет что-то в рабочей системе, поэтому подтверждает сотрудник. Сообщу здесь, когда будет готово.")
_CANCELLED = _txt("בוטל — לא בוצע שום שינוי.", "Cancelled — nothing was changed.", "Annulé — rien n'a été modifié.",
                  "أُلغي — لم يتغير شيء.", "Отменено — ничего не изменено.")
_FAILED = _txt("זה לא הצליח הפעם, ושום דבר לא השתנה. הצוות קיבל את הפרטים ומטפל בזה.",
               "That didn't go through this time, and nothing was changed. The team has the details and is on it.",
               "Cela n'a pas abouti cette fois, et rien n'a été modifié. L'équipe a les détails et s'en occupe.",
               "لم ينجح ذلك هذه المرة ولم يتغير شيء. الفريق لديه التفاصيل ويعالج الأمر.",
               "В этот раз не получилось, ничего не изменено. Команда получила детали и занимается этим.")
_EXPIRED = _txt("הבקשה הזו כבר לא בתוקף — כתבו לי שוב ואכין חדשה.", "This request has expired — message me again and I'll set up a new one.",
                "Cette demande a expiré — écrivez-moi et j'en prépare une nouvelle.", "انتهت صلاحية هذا الطلب — راسلوني وسأجهز طلبًا جديدًا.",
                "Срок запроса истёк — напишите мне, и я подготовлю новый.")
NO_CARD = _txt("(את הכפתור לאישור לא הצלחתי להציג כרגע — הצוות קיבל את הבקשה ויטפל בה.)",
               "(I couldn't show the approval button right now — the team has the request and will handle it.)",
               "(Je n'ai pas pu afficher le bouton de validation — l'équipe a la demande et s'en occupe.)",
               "(تعذّر عرض زر الموافقة الآن — الفريق استلم الطلب وسيتولاه.)",
               "(Не удалось показать кнопку подтверждения — команда получила запрос и займётся им.)")


def _lang(client_id: int) -> str:
    try:
        lang = (_client(client_id) or {}).get("language") or "he"
    except Exception:
        lang = "he"
    return lang if lang in LANGS else "he"


def _pick(texts: dict, lang: str) -> str:
    return texts.get(lang) or texts["he"]


# ─── What a chat may offer this turn ─────────────────────────────────────────

def _actor_for(origin: str) -> str:
    return "client" if origin == "client_chat" else "admin"


def _may_propose(spec: dict, actor: str) -> bool:
    if actor == "admin":
        return spec["approver"] == "admin"
    return spec["approver"] == "client" or spec.get("client_requestable", False)


def _is_internal(client: dict) -> bool:
    return (client or {}).get("billing_exempt") is True


def available(client_id: int, origin: str, state: dict, client: dict = None) -> list:
    """The actions this chat may propose for this client right now, in the
    shape the model sees. Never raises."""
    actor = _actor_for(origin)
    client = client if client is not None else _client(client_id)
    out = []
    for name, spec in ACTIONS.items():
        try:
            if not _may_propose(spec, actor):
                continue
            if spec["internal_only"] and not _is_internal(client):
                continue
            if not spec["available"](state, client):
                continue
        except Exception as e:
            _log(f"client {client_id}: availability check {name} failed: {type(e).__name__}",
                 severity="WARNING", client_id=client_id)
            continue
        out.append({"name": name, "does": spec["does"],
                    "params": {p: d.get("hint", "") for p, d in spec["params"].items()},
                    "approver": "client" if spec["approver"] == "client" else "staff"})
    return out


def validate_params(spec: dict, params, client_id: int) -> dict:
    """Whitelisted keys only, every value through its validator. ValueError
    on anything else — an unknown key is a tampered request, not a typo."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    unknown = set(params) - set(spec["params"])
    if unknown:
        raise ValueError(f"unknown params: {sorted(unknown)}")
    clean = {}
    for name, definition in spec["params"].items():
        value = params.get(name)
        if value in (None, ""):
            if definition.get("required"):
                raise ValueError(f"{name} is required")
            continue
        clean[name] = definition["validate"](value, client_id)
    return clean


# ─── Storage ─────────────────────────────────────────────────────────────────

def _get(proposal_id: int) -> dict:
    rows = (_db().table(TABLE).select("*").eq("id", int(proposal_id)).limit(1).execute().data or [])
    return rows[0] if rows else {}


def _claim(proposal_id: int, from_statuses, changes: dict) -> bool:
    """Compare-and-set: moves the row only if it is still in one of
    from_statuses. False = someone else got there first (double click)."""
    rows = (_db().table(TABLE).update(changes).eq("id", int(proposal_id))
            .in_("status", list(from_statuses)).execute().data or [])
    return bool(rows)


def _open_rows(client_id: int) -> list:
    try:
        return (_db().table(TABLE).select("*").eq("client_id", int(client_id))
                .in_("status", list(OPEN_STATUSES)).order("created_at", desc=True)
                .limit(20).execute().data or [])
    except Exception as e:
        _log(f"client {client_id}: agent_actions unreadable ({type(e).__name__}) — migration pending?",
             severity="WARNING", client_id=client_id)
        return []


def _expired(row: dict) -> bool:
    try:
        created = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
    except Exception:
        return True
    return _now() - created > timedelta(hours=PROPOSAL_TTL_HOURS)


def card(row: dict, actor: str, lang: str = "he") -> dict:
    spec = ACTIONS.get(row.get("action")) or {}
    params = row.get("params") or {}
    what = _pick(spec.get("what") or {"he": ""}, lang)
    if "{title}" in what:
        what = what.replace("{title}", _suggestion_title(row.get("client_id"), params.get("suggestion_id")))
    return {"id": row.get("id"), "action": row.get("action"), "status": row.get("status"),
            "persona": row.get("persona", "general"), "origin": row.get("origin"),
            "title": _pick(spec.get("title") or {"he": row.get("action", "")}, lang),
            "what": what, "params": params if actor == "admin" else {},
            "approver": row.get("approver"), "kind": spec.get("kind", "execute"),
            "can_decide": row.get("status") in OPEN_STATUSES and row.get("approver") == actor,
            "created_at": row.get("created_at")}


def _suggestion_title(client_id, suggestion_id) -> str:
    if not suggestion_id:
        return ""
    try:
        from agents.engagement_agent import get_suggestions
        for s in get_suggestions(client_id, status="", limit=50):
            if s.get("id") == suggestion_id:
                return s.get("title", "")
    except Exception:
        pass
    return ""


def open_cards(client_id: int, actor: str, persona: str = None) -> list:
    """Open proposals as cards. A client sees only its own chat's cards (all
    of them, including ones waiting on staff, which carry no buttons); the
    admin sees every open card for the client."""
    lang = _lang(client_id) if actor == "client" else "he"
    out = []
    for row in _open_rows(client_id):
        if actor == "client" and (row.get("origin") != "client_chat"
                                  or (persona and row.get("persona", "general") != persona)):
            continue
        if _expired(row):
            continue
        out.append(card(row, actor, lang))
    return out


# ─── Propose ─────────────────────────────────────────────────────────────────

def propose(client_id: int, origin: str, persona: str, raw, state: dict = None,
            client: dict = None, dry_run: bool = False) -> dict:
    """The model's proposed_action -> a stored proposal + card, or
    {"error": code}. Everything is checked here; nothing runs."""
    if not isinstance(raw, dict):
        return {"error": "malformed"}
    name = str(raw.get("name") or "").strip()
    if not name:
        return {}
    spec = ACTIONS.get(name)
    if not spec:
        return {"error": "unknown_action"}
    actor = _actor_for(origin)
    if not _may_propose(spec, actor):
        return {"error": "not_allowed_here"}
    client = client if client is not None else _client(client_id)
    if spec["internal_only"] and not _is_internal(client):
        return {"error": "internal_only"}
    try:
        if state is not None and not spec["available"](state, client):
            return {"error": "not_available"}
        params = validate_params(spec, raw.get("params"), client_id)
    except ValueError as e:
        _log(f"client {client_id}: proposal {name} refused: {e}", client_id=client_id)
        return {"error": "invalid_params"}
    if dry_run:
        return {"dry_run": True, "action": name, "params": params}

    status = "proposed" if spec["approver"] == actor else "awaiting_staff"
    lang = _lang(client_id) if actor == "client" else "he"
    for row in _open_rows(client_id):
        # Same action + params already open: show that card, don't stack a second
        if row.get("action") == name and (row.get("params") or {}) == params and not _expired(row):
            return {"card": card(row, actor, lang), "status": row.get("status")}
    try:
        rows = _db().table(TABLE).insert({
            "client_id": int(client_id), "action": name, "params": params, "origin": origin,
            "persona": persona or "general", "approver": spec["approver"], "status": status,
        }).execute().data or []
    except Exception as e:
        _log(f"client {client_id}: proposal {name} not stored: {type(e).__name__}: {e}",
             severity="WARNING", client_id=client_id)
        return {"error": "storage_unavailable"}
    if not rows:
        return {"error": "storage_unavailable"}
    row = rows[0]
    _log(f"client {client_id}: proposed {name} ({status}) from {origin}/{persona}",
         client_id=client_id, proposal=row.get("id"), action=name, status=status)
    if status == "awaiting_staff":
        _alert(f"client {client_id}: asked in chat for '{name}' — approve or cancel it in the "
               f"Control Room (proposal {row.get('id')})")
    return {"card": card(row, actor, lang), "status": status}


# ─── Decide (the human click) ────────────────────────────────────────────────

def _refuse(code: str, http: int = 400) -> dict:
    return {"success": False, "code": code, "http": http}


def _post(row: dict, text: str, to_client: bool):
    """Outcome into the thread the proposal came from (and, for a staff
    request, also the client's thread — they are waiting for it)."""
    try:
        if row.get("origin") == "client_chat" or to_client:
            _post_client_message(row["client_id"], row.get("persona", "general"), text)
        if row.get("origin") == "admin_chat":
            _post_admin_message(row["client_id"], row.get("persona", "general"), text)
    except Exception as e:
        _log(f"client {row.get('client_id')}: outcome not posted: {type(e).__name__}",
             severity="WARNING", client_id=row.get("client_id"))


def decide(proposal_id: int, actor: str, actor_client_id: int, decision: str,
           background_tasks=None) -> dict:
    if actor not in ("client", "admin"):
        return _refuse("bad_actor", 403)
    if decision not in ("approve", "cancel"):
        return _refuse("bad_decision")
    try:
        row = _get(proposal_id)
    except Exception as e:
        _log(f"proposal {proposal_id}: unreadable ({type(e).__name__})", severity="WARNING")
        return _refuse("storage_unavailable", 503)
    # Another client's proposal is answered exactly like a missing one
    if not row or int(row.get("client_id") or 0) != int(actor_client_id or 0):
        return _refuse("not_found", 404)
    spec = ACTIONS.get(row.get("action"))
    if not spec:
        return _refuse("unknown_action")
    if row.get("approver") != actor:
        return _refuse("not_your_decision", 403)
    client_id = int(row["client_id"])
    lang = _lang(client_id)
    if row.get("status") not in OPEN_STATUSES:
        # Double click / second tab: report what already happened, run nothing
        return {"success": True, "already": True, "status": row.get("status"),
                "card": card(row, actor, lang if actor == "client" else "he")}
    stamp = _now().isoformat()
    if _expired(row):
        _claim(proposal_id, OPEN_STATUSES, {"status": "expired", "finished_at": stamp, "outcome": "expired"})
        return {**_refuse("expired", 410), "message": _pick(_EXPIRED, lang)}

    if decision == "cancel":
        if not _claim(proposal_id, OPEN_STATUSES, {"status": "cancelled", "decided_at": stamp,
                                                   "decided_by": actor, "finished_at": stamp,
                                                   "outcome": "cancelled"}):
            return {"success": True, "already": True, "status": (_get(proposal_id) or {}).get("status")}
        _log(f"client {client_id}: {row['action']} cancelled by {actor}", client_id=client_id,
             proposal=proposal_id)
        _post(row, _pick(_CANCELLED, lang), to_client=row.get("origin") == "client_chat")
        return {"success": True, "status": "cancelled", "message": _pick(_CANCELLED, lang)}

    # Re-check everything from the STORED row before claiming it
    client = _client(client_id)
    if spec["internal_only"] and not _is_internal(client):
        _claim(proposal_id, OPEN_STATUSES, {"status": "failed", "decided_at": stamp, "decided_by": actor,
                                            "finished_at": stamp, "outcome": "refused_internal_only"})
        return _refuse("internal_only", 403)
    try:
        params = validate_params(spec, row.get("params"), client_id)
    except ValueError as e:
        _log(f"client {client_id}: {row['action']} params refused at approval: {e}",
             severity="WARNING", client_id=client_id, proposal=proposal_id)
        _claim(proposal_id, OPEN_STATUSES, {"status": "failed", "decided_at": stamp, "decided_by": actor,
                                            "finished_at": stamp, "outcome": "invalid_params"})
        return _refuse("invalid_params")
    if not _claim(proposal_id, OPEN_STATUSES, {"status": "running", "decided_at": stamp,
                                               "decided_by": actor}):
        return {"success": True, "already": True, "status": (_get(proposal_id) or {}).get("status")}
    _log(f"client {client_id}: {row['action']} approved by {actor}", client_id=client_id,
         proposal=proposal_id)

    if spec["kind"] in ("link", "focus"):
        _claim(proposal_id, ("running",), {"status": "done", "finished_at": _now().isoformat(),
                                           "outcome": "opened"})
        out = {"success": True, "status": "done", "message": _pick(spec["done"], lang)}
        if spec["kind"] == "link":
            out["redirect"] = spec["link"]
        else:
            out["focus"] = spec["focus"]
        return out

    try:
        result = spec["run"](client_id, params, background_tasks) or {}
    except Exception as e:
        code = e.code if isinstance(e, ActionFailed) else "error"
        _claim(proposal_id, ("running",), {"status": "failed", "finished_at": _now().isoformat(),
                                           "outcome": code})
        _log(f"client {client_id}: {row['action']} FAILED ({code}): {type(e).__name__}: {e}",
             severity="WARNING", client_id=client_id, proposal=proposal_id)
        if actor == "admin":
            detail = f"✕ {_pick(spec['title'], 'he')} — לא בוצע: {str(e)[:400]}"
            if row.get("origin") == "admin_chat":
                _post_admin_message(client_id, row.get("persona", "general"), detail)
            else:
                # A client's request the team approved: they hear the outcome too
                _post(row, _pick(_FAILED, lang), to_client=True)
            return {"success": True, "status": "failed", "message": detail}
        _alert(f"client {client_id}: approved action '{row['action']}' failed: {str(e)[:300]}")
        _post(row, _pick(_FAILED, lang), to_client=True)
        return {"success": True, "status": "failed", "message": _pick(_FAILED, lang)}

    _claim(proposal_id, ("running",), {"status": "done", "finished_at": _now().isoformat(),
                                       "outcome": "done"})
    message = _pick(spec["done"], lang if actor == "client" else "he")
    if row.get("origin") == "client_chat":
        _post(row, _pick(spec["done"], lang), to_client=True)
    elif row.get("origin") == "admin_chat":
        _post_admin_message(client_id, row.get("persona", "general"), _admin_summary(spec, result))
    return {"success": True, "status": "done", "message": message,
            "result": result if actor == "admin" else None}


def _admin_summary(spec: dict, result: dict) -> str:
    """The admin's own record of what ran — ids included (admin_agent_chat is
    purged with the client's Google data)."""
    keys = ("campaign_name", "campaign_id", "status", "customer_id", "video_id", "title",
            "privacy_status", "studio_url", "event_name", "live_version", "ga4_measurement_id")
    facts = " · ".join(f"{k}: {result[k]}" for k in keys if result.get(k))
    return f"✓ {_pick(spec['done'], 'he')}" + (f"\n{facts}" if facts else "")


# ─── Capability check (dry run, admin-triggered) ─────────────────────────────

CHECK_QUESTIONS = (
    ("website", "המדידה באתר שלי עובדת? רואים את הלידים?"),
    ("website", "תעדכן את נתוני המדידה באתר בבקשה"),
    ("google", "ה-Google Ads שלי מחובר?"),
    ("general", "כמה לידים היו לי השבוע?"),
    ("general", "מה רץ אצלי עכשיו?"),
    ("general", "מה מחכה לאישור שלי?"),
    ("google", "כמה הוצאתי החודש על פרסום ביחס לתקציב?"),
    ("website", "האתר שלי מחובר? מה המצב שלו?"),
    ("media", "כמה תמונות וסרטונים יצרתם לי החודש?"),
    ("general", "הכל מחובר אצלי כמו שצריך?"),
)

FORBIDDEN = re.compile("|".join((
    r"לא\s+רוא[הים]", r"אין\s+לי\s+(גישה|אפשרות\s+לראות)", r"לא\s+יכול\s+לראות",
    r"(אעביר|מעביר|העברתי)\s+(את\s+(זה|הבקשה)\s+)?לצוות",
    r"(היכנס|תיכנס|כנס)[וי]?\s+ל", r"(בדק|תבדק|תבדוק)[וי]?\s+בעצמ",
    r"can'?t\s+see", r"don'?t\s+have\s+access", r"pass(ed)?\s+(this|it)\s+(on\s+)?to\s+the\s+team",
    r"log\s*in\s+to", r"check\s+(it\s+)?yourself",
)), re.IGNORECASE)


def capability_check(client_id: int) -> dict:
    """Asks the client's agents CHECK_QUESTIONS in DRY RUN (nothing stored,
    no card, no alert) and flags any reply that dead-ends. A flagged reply on
    a topic whose read genuinely failed this turn is marked as such."""
    from agents import support_agent
    from core import client_state
    state = client_state.snapshot(client_id, refresh=True)
    failed = client_state.failed_topics(state)
    rows = []
    for persona, question in CHECK_QUESTIONS:
        if persona == "general":
            result = support_agent.answer_support_question(client_id, question, dry_run=True)
        else:
            result = support_agent.answer_persona_question(client_id, persona, question, dry_run=True)
        reply = result.get("reply", "")
        hits = sorted({m.group(0) for m in FORBIDDEN.finditer(reply)})
        rows.append({"persona": persona, "question": question, "reply": reply,
                     "proposed_action": result.get("proposed_action") or "",
                     "data_topic": result.get("data_topic", ""),
                     "forbidden_phrases": hits,
                     "topic_read_failed": result.get("data_topic", "") in failed})
    return {"client_id": client_id, "state_failed_sections": client_state.failed_sections(state),
            "flagged": sum(1 for r in rows if r["forbidden_phrases"]), "total": len(rows), "answers": rows}
