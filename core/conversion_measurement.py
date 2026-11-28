"""Is anything being measured in the client's Google Ads account? (2026-11-28)

"Zero conversions" and "nobody is measuring" used to look identical: every
read took metrics.conversions and a missing measurement was a plain 0. Three
things rested on that zero — the optimisation cycle (pause a keyword with 30
clicks and no conversions), the "spent with ZERO conversions" alert, and every
result shown on the Google page.

THE STATE, read from the account itself and STORED (app_settings
`conversion_measurement:{client_id}`). Three states, never collapsed:

  measuring          conversion actions exist and the account COUNTED at least
                     one conversion in the last 30 days
  configured_silent  conversion actions exist, nothing counted in 30 days
                     (`recorded_not_counted` = something was recorded, but only
                     by actions the account does not count as conversions)
  not_measured       no conversion action at all

and one that is not a fourth kind of account but the honest answer when the
read failed or is too old: `unknown`. Every gate treats `unknown` like "not
measuring" — a decision is never made on a state we could not read.

Two reads, two operations (core/google_ads_quota):
  ACTIONS_GAQL   the enabled conversion actions configured in the account
  RECORDED_GAQL  what each action recorded in the last 30 days — conversions
                 (what the account counts, the number every other read uses)
                 and all_conversions (everything recorded)
An action shared from a manager account shows up in the second read only; it
counts.

THE GATE (`require_measuring`): no optimisation decision that depends on
conversion data may run unless the state is `measuring` and was read in the
last FRESH_HOURS. It raises NotMeasuring, which names the state. Called at the
cycle, at the keyword lever and at the single write choke point
(google_ads_agent._apply) — so it holds whatever OPTIMIZATION_MODE says.

THE SITE SIDE (`compare`): website_agent.get_conversion_tracking_status reads
the PUBLISHED Tag Manager container. Tags on the site do not mean the event
was imported into Google Ads as a conversion — that import is manual. With
both sides read, the mismatch is named: tags_present_import_missing.

NOT VERIFIED LIVE: the two queries were written from Google's reference and
have not run against a real account from this code. A query Google refuses
gives `unknown` with Google's own message — the safe side.
"""
import time
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "conversion_measurement"
PREFIX = "conversion_measurement:"
STATES = ("measuring", "configured_silent", "not_measured")
UNKNOWN = "unknown"
NOT_CONNECTED = "not_connected"
WINDOW_DAYS = 30
FRESH_HOURS = 36                 # the daily scan refreshes it; older than this is `unknown` to every gate
PAGE_REFRESH_HOURS = 24          # the Google page re-reads at most this often
LANGS = ("he", "en", "fr", "ar", "ru")
GA4_TYPES_PREFIX = "GOOGLE_ANALYTICS_4"      # a conversion action imported from the site's analytics events

ACTIONS_GAQL = """
    SELECT conversion_action.id, conversion_action.name, conversion_action.status,
           conversion_action.type, conversion_action.category,
           conversion_action.include_in_conversions_metric
    FROM conversion_action
    WHERE conversion_action.status = 'ENABLED'
"""
RECORDED_GAQL = """
    SELECT segments.conversion_action, segments.conversion_action_name,
           metrics.conversions, metrics.all_conversions
    FROM customer
    WHERE segments.date DURING LAST_30_DAYS
"""


class NotMeasuring(RuntimeError):
    """A decision that needs conversion data was asked of an account that is
    not measuring (or whose state could not be read). `.state` names it."""

    def __init__(self, client_id: int, state: str):
        self.client_id, self.state = int(client_id), state
        super().__init__(f"account_not_measuring: client {client_id} conversion measurement is '{state}' — "
                         f"no decision that depends on conversion data runs unless it is 'measuring'")


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── Doors (each replaced in the tests) ──────────────────────────────────────

def _db():
    from agents.client_agent import _db as db
    return db()


def _read_store(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}").limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _write_store(client_id: int, value: dict):
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": value,
                                        "updated_at": _now().isoformat()}, on_conflict="key").execute()


def _connection(client_id: int) -> dict:
    from agents.google_ads_agent import _get_connection
    return _get_connection(client_id)


def _search(conn: dict, gaql: str) -> list:
    from core import google_ads_service as gads
    return gads.search(conn["access_token"], conn["account_id"], gaql)


def _site_tracking(client_id: int) -> dict:
    from agents.website_agent import get_conversion_tracking_status
    return get_conversion_tracking_status(client_id)


# ─── The read ────────────────────────────────────────────────────────────────

def _action_id(resource: str) -> str:
    return str(resource or "").rsplit("/", 1)[-1]


def classify(action_rows: list, recorded_rows: list) -> dict:
    """Pure: Google's rows → {"state", "actions": [...], totals}. An action is
    {"id", "name", "type", "category", "counted": bool (the account counts it
    as a conversion), "conversions_30d", "recorded_30d"}."""
    actions = {}
    for row in action_rows or []:
        a = row.get("conversionAction") or {}
        aid = str(a.get("id") or "")
        if aid:
            actions[aid] = {"id": aid, "name": str(a.get("name") or "")[:120], "type": str(a.get("type") or ""),
                            "category": str(a.get("category") or ""),
                            "counted": bool(a.get("includeInConversionsMetric")),
                            "conversions_30d": 0.0, "recorded_30d": 0.0, "in_account": True}
    for row in recorded_rows or []:
        segments, metrics = row.get("segments") or {}, row.get("metrics") or {}
        aid = _action_id(segments.get("conversionAction"))
        if not aid:
            continue
        entry = actions.setdefault(aid, {"id": aid, "name": str(segments.get("conversionActionName") or "")[:120],
                                         "type": "", "category": "", "counted": None,
                                         "conversions_30d": 0.0, "recorded_30d": 0.0, "in_account": False})
        entry["conversions_30d"] += float(metrics.get("conversions") or 0)
        entry["recorded_30d"] += float(metrics.get("allConversions") or 0)
    rows = sorted(actions.values(), key=lambda a: (-a["conversions_30d"], -a["recorded_30d"], a["name"]))
    for a in rows:
        a["conversions_30d"], a["recorded_30d"] = round(a["conversions_30d"], 1), round(a["recorded_30d"], 1)
    counted = round(sum(a["conversions_30d"] for a in rows), 1)
    recorded = round(sum(a["recorded_30d"] for a in rows), 1)
    state = "not_measured" if not rows else "measuring" if counted > 0 else "configured_silent"
    return {"state": state, "actions": rows[:40], "action_count": len(rows),
            "conversions_30d": counted, "recorded_30d": recorded,
            "recorded_not_counted": state == "configured_silent" and recorded > 0}


def compare(measure: dict, tracking: dict) -> dict:
    """The site side against the account side. {"mismatch": "" |
    tags_present_import_missing | import_present_tags_missing |
    site_side_unknown, "site_types": [...], "import_actions": n}."""
    tracking = tracking or {}
    imports = [a for a in measure.get("actions") or [] if str(a.get("type") or "").startswith(GA4_TYPES_PREFIX)]
    out = {"site_types": [], "import_actions": len(imports), "mismatch": ""}
    if not tracking.get("gtm_api_connected"):
        return {**out, "mismatch": "site_side_unknown"}
    out["site_types"] = list(tracking.get("live_types") or [])
    if out["site_types"] and not imports:
        out["mismatch"] = "tags_present_import_missing"
    elif imports and not out["site_types"]:
        out["mismatch"] = "import_present_tags_missing"
    return out


def refresh(client_id: int, conn: dict = None, with_site: bool = True) -> dict:
    """Read the account now (two operations), compare with the site, STORE and
    return. Never raises: an unreadable account is stored as `unknown` with the
    reason (Google's own message carries no token)."""
    value = {"checked_at": _now().isoformat(), "window_days": WINDOW_DAYS}
    try:
        conn = conn or _connection(client_id)
        if not (conn.get("access_token") and conn.get("account_id")):
            value.update(state=NOT_CONNECTED)
        else:
            value.update(classify(_search(conn, ACTIONS_GAQL), _search(conn, RECORDED_GAQL)))
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: measurement unreadable ({type(e).__name__})")
        value.update(state=UNKNOWN, error=f"{type(e).__name__}: {str(e)[:300]}")
    if with_site and value["state"] in STATES:
        try:
            value["site"] = compare(value, _site_tracking(client_id))
        except Exception as e:
            value["site"] = {"mismatch": "site_side_unknown", "site_types": [], "import_actions": 0,
                             "error": type(e).__name__}
    try:
        _write_store(client_id, value)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: state not stored ({type(e).__name__})")
    _cache.pop(int(client_id), None)
    return value


_cache = {}
CACHE_S = 60


def current(client_id: int) -> dict:
    """The stored read ({} = never read). Cached for a minute."""
    hit = _cache.get(int(client_id))
    if hit and hit[0] > time.time() - CACHE_S:
        return hit[1]
    try:
        value = _read_store(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: stored state unreadable ({type(e).__name__})")
        return {}
    _cache[int(client_id)] = (time.time(), value)
    return value


def _age_hours(value: dict):
    try:
        then = datetime.fromisoformat(str(value["checked_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return (_now() - then).total_seconds() / 3600


def state(client_id: int, max_age_hours: float = FRESH_HOURS) -> str:
    """One of STATES, NOT_CONNECTED or UNKNOWN (never read, unreadable, or
    read longer ago than `max_age_hours`)."""
    value = current(client_id)
    age = _age_hours(value)
    if not value or age is None or age > max_age_hours:
        return UNKNOWN
    return value.get("state") if value.get("state") in STATES + (NOT_CONNECTED,) else UNKNOWN


def require_measuring(client_id: int):
    """THE gate. Raises NotMeasuring unless the account is `measuring`."""
    now = state(client_id)
    if now != "measuring":
        raise NotMeasuring(client_id, now)


def for_page(client_id: int) -> dict:
    """The stored read for the Google page — refreshed when it is older than
    PAGE_REFRESH_HOURS (two operations, at most once a day per client)."""
    value = current(client_id)
    age = _age_hours(value)
    if not value or age is None or age > PAGE_REFRESH_HOURS:
        value = refresh(client_id)
    return value


def prompt_block(client_id: int) -> dict:
    """For the agents: the state and what it forbids."""
    now = state(client_id)
    if now == NOT_CONNECTED:
        return {}
    return {"state": now,
            "rule": ("measuring: results in the account are real counts." if now == "measuring" else
                     "The account is NOT known to be measuring results. A zero is NOT 'no results' — it is 'not "
                     "measured'. Never say the advertising brought no results, never judge a campaign or a search "
                     "phrase by results, never state a cost per result. Say plainly that results are not being "
                     "measured in the advertising account and that fixing this comes first.")}


# ─── Words (the Google page; no platform vocabulary, governance §9) ──────────

T = {
    "title": _five("האם התוצאות נמדדות", "Are results being measured", "Les résultats sont-ils mesurés", "هل تُقاس النتائج",
                   "Измеряются ли результаты"),
    "measuring": _five(
        "כן. חשבון הפרסום שלכם סופר תוצאות: {n} ב-30 הימים האחרונים.",
        "Yes. Your advertising account is counting results: {n} in the last 30 days.",
        "Oui. Votre compte publicitaire compte les résultats : {n} sur les 30 derniers jours.",
        "نعم. حسابكم الإعلاني يحتسب النتائج: {n} خلال آخر 30 يومًا.",
        "Да. Ваш рекламный аккаунт считает результаты: {n} за последние 30 дней."),
    "configured_silent": _five(
        "המדידה מוגדרת בחשבון הפרסום, אבל ב-30 הימים האחרונים היא לא ספרה אף תוצאה. ייתכן שבאמת לא היו תוצאות, וייתכן שהמדידה לא פועלת. עד שנדע איזה מהשניים, לא נסיק מסקנות מהאפס הזה ולא נשנה את הפרסום לפיו.",
        "Measurement is set up in the advertising account, but in the last 30 days it counted no result. Either there really were none, or the measurement is not working. Until we know which, we draw no conclusion from that zero and change nothing in the advertising because of it.",
        "La mesure est configurée dans le compte publicitaire, mais elle n'a compté aucun résultat sur les 30 derniers jours. Soit il n'y en a vraiment pas eu, soit la mesure ne fonctionne pas. Tant que nous ne le savons pas, nous ne tirons aucune conclusion de ce zéro et ne modifions rien à cause de lui.",
        "القياس مُعدّ في الحساب الإعلاني، لكنه لم يحتسب أي نتيجة خلال آخر 30 يومًا. إما أنه لم تكن هناك نتائج فعلًا، أو أن القياس لا يعمل. إلى أن نعرف أيهما، لا نستنتج شيئًا من هذا الصفر ولا نغيّر الإعلان بسببه.",
        "Измерение в рекламном аккаунте настроено, но за последние 30 дней оно не засчитало ни одного результата. Либо результатов действительно не было, либо измерение не работает. Пока мы не знаем, что из двух, мы не делаем выводов из этого нуля и не меняем рекламу из-за него."),
    "not_measured": _five(
        "חשוב שתדעו: בחשבון הפרסום שלכם לא מוגדרת מדידה של תוצאות. אפשר לראות כמה כסף יצא וכמה אנשים לחצו, אבל לא מה יצא מזה. כל אפס שמופיע כאן ליד תוצאות אומר ״לא נמדד״, לא ״לא היו״. עד שהמדידה תוגדר לא נוכל לומר אם הפרסום עובד, ולא נשנה אותו לפי תוצאות.",
        "You should know: no measurement of results is set up in your advertising account. We can see how much money went out and how many people clicked, but not what came of it. Every zero shown here next to results means \"not measured\", not \"there were none\". Until measurement is set up we cannot say whether the advertising works, and we will not change it by results.",
        "À savoir : aucune mesure des résultats n'est configurée dans votre compte publicitaire. On voit combien d'argent est sorti et combien de personnes ont cliqué, mais pas ce qui en est sorti. Chaque zéro affiché ici à côté des résultats signifie « non mesuré », pas « il n'y en a pas eu ». Tant que la mesure n'est pas configurée, nous ne pouvons pas dire si la publicité fonctionne, et nous ne la modifierons pas d'après les résultats.",
        "يجب أن تعرفوا: لا يوجد في حسابكم الإعلاني أي قياس للنتائج. نرى كم خرج من المال وكم شخصًا نقر، لكن لا نرى ما نتج عن ذلك. كل صفر يظهر هنا بجانب النتائج يعني \"لم يُقس\" لا \"لم تكن هناك نتائج\". إلى أن يُعدّ القياس لا نستطيع القول إن كان الإعلان يعمل، ولن نغيّره بحسب النتائج.",
        "Важно: в вашем рекламном аккаунте не настроено измерение результатов. Видно, сколько денег ушло и сколько людей нажали, но не видно, что из этого вышло. Каждый ноль рядом с результатами здесь означает «не измерено», а не «не было». Пока измерение не настроено, мы не можем сказать, работает ли реклама, и не будем менять её по результатам."),
    "unknown": _five(
        "לא הצלחנו לבדוק אם התוצאות נמדדות בחשבון הפרסום. עד שנדע, לא נשנה את הפרסום לפי תוצאות. הצוות קיבל התראה.",
        "We could not check whether results are being measured in the advertising account. Until we know, we will not change the advertising by results. The team was alerted.",
        "Nous n'avons pas pu vérifier si les résultats sont mesurés dans le compte publicitaire. D'ici là, nous ne modifierons pas la publicité d'après les résultats. L'équipe est prévenue.",
        "لم نتمكن من التحقق مما إذا كانت النتائج تُقاس في الحساب الإعلاني. إلى أن نعرف، لن نغيّر الإعلان بحسب النتائج. تم تنبيه الفريق.",
        "Нам не удалось проверить, измеряются ли результаты в рекламном аккаунте. Пока мы не знаем, мы не будем менять рекламу по результатам. Команда уведомлена."),
    "recorded_not_counted": _five(
        "המדידה כן רשמה פעילות, אבל החשבון לא סופר אותה כתוצאה. זו הגדרה בחשבון שהצוות שלנו בודק.",
        "The measurement did record activity, but the account does not count it as a result. That is a setting in the account our team is checking.",
        "La mesure a bien enregistré de l'activité, mais le compte ne la compte pas comme résultat. C'est un réglage du compte que notre équipe vérifie.",
        "سجّل القياس نشاطًا، لكن الحساب لا يحتسبه كنتيجة. هذا إعداد في الحساب يفحصه فريقنا.",
        "Измерение зафиксировало активность, но аккаунт не считает её результатом. Это настройка аккаунта, которую проверяет наша команда."),
    "tags_present_import_missing": _five(
        "באתר שלכם המדידה כבר מותקנת ועובדת, אבל היא עוד לא מחוברת לחשבון הפרסום, ולכן הפרסום לא ״רואה״ אותה. זה צעד חד-פעמי שהצוות שלנו משלים.",
        "On your website the measurement is already installed and working, but it is not yet linked to the advertising account, so the advertising does not \"see\" it. It is a one-time step our team completes.",
        "Sur votre site, la mesure est déjà installée et fonctionne, mais elle n'est pas encore reliée au compte publicitaire ; la publicité ne la « voit » donc pas. C'est une étape unique que notre équipe réalise.",
        "القياس مُثبّت ويعمل في موقعكم، لكنه غير مرتبط بعد بالحساب الإعلاني، لذلك الإعلان لا \"يراه\". هذه خطوة لمرة واحدة يُكملها فريقنا.",
        "На вашем сайте измерение уже установлено и работает, но оно ещё не связано с рекламным аккаунтом, поэтому реклама его «не видит». Это разовый шаг, который выполняет наша команда."),
    "import_present_tags_missing": _five(
        "חשבון הפרסום מחכה לנתוני מדידה מהאתר, אבל באתר עצמו המדידה לא פועלת כרגע. הצוות שלנו בודק את זה.",
        "The advertising account is waiting for measurement data from the website, but on the site itself the measurement is not running right now. Our team is checking it.",
        "Le compte publicitaire attend des données de mesure du site, mais la mesure ne fonctionne pas sur le site pour l'instant. Notre équipe vérifie.",
        "الحساب الإعلاني ينتظر بيانات القياس من الموقع، لكن القياس في الموقع نفسه لا يعمل حاليًا. فريقنا يفحص ذلك.",
        "Рекламный аккаунт ждёт данные измерения с сайта, но на самом сайте измерение сейчас не работает. Наша команда проверяет."),
}


def view(client_id: int, lang: str = "he", value: dict = None) -> dict:
    """What the client reads on the Google page: {} when the account is not
    connected (the page already says that), else {"state", "title", "text",
    "notes": [...], "checked": iso}."""
    lang = lang if lang in LANGS else "he"
    value = value if value is not None else for_page(client_id)
    now = value.get("state")
    if now == NOT_CONNECTED or not value:
        return {}
    if now not in STATES:
        now = UNKNOWN
    text = T[now][lang]
    if now == "measuring":
        from core import client_language
        text = text.replace("{n}", client_language.fmt_number(value.get("conversions_30d") or 0, lang))
    notes = []
    if value.get("recorded_not_counted"):
        notes.append(T["recorded_not_counted"][lang])
    mismatch = (value.get("site") or {}).get("mismatch")
    if mismatch in ("tags_present_import_missing", "import_present_tags_missing"):
        notes.append(T[mismatch][lang])
    return {"state": now, "ok": now == "measuring", "title": T["title"][lang], "text": text, "notes": notes,
            "checked": value.get("checked_at") or ""}
