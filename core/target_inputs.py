"""No target before its inputs (2026-11-28, knowledge/client-governance.md §5).

Observed on client 27: the manager put three targets on an approval card
("5 enquiries in 30 days") and only when asked said that no marketing budget
is on record. That is guessing first and looking for a justification after.

THE RULE, same shape as §1 (no approved brief, no strategic authority): an
agent does not propose targets, a budget split or a strategy while a required
input is missing. It names what is missing, obtains it, and only then proposes.

REQUIRED, all five, before anything is proposed (`REQUIRED`):

  budget       the monthly marketing budget
  offer        what is sold and at what margin
  conversion   which action counts as a result
  baseline     the measured performance of the last 30 days
  constraints  lead time / capacity / seasonality (at least one on record)

Where each is read from, in this order, and the source is always kept:

  recorded     app_settings `target_inputs:{client_id}` — what the client
               approved on the `record_target_inputs` card
  sales_chat   the budget the client stated before buying (budget_agent.get_forecast)
  brief        a line of the client's brand brief that states it (a pattern
               match on the brief's own text — the line is quoted, never
               interpreted)
  settings     clients.conversion_types, when the client confirmed them
  measured     the baseline: our own counts (enquiries, store sales) and the
               advertising spend the platforms report

Nothing is inferred. An input that is not found is MISSING.

THIN BASELINE (§5, B3): a baseline can exist and still be too thin to carry a
target. `thin()` is the rule and it lives HERE, in code, not in a prompt:

  fewer than MIN_BASELINE_RESULTS measured results in 30 days — results of
  the kind THIS client counts (`result_kind`: store sales, or enquiries;
  Shin Sekai counts sales and does not count enquiries at all), OR
  advertising spend in 30 days below MIN_BASELINE_SPEND_ILS (none, or the
  account is not connected / not readable)

A thin baseline gets no result target at all — not a softer number. The first
period is a LEARNING PERIOD: what we will spend, what we expect to learn, and
when there will be enough data for a real target. The threshold is printed on
the card.

BASIS (§9 point 4): every number a client approves carries what it rests on,
as a REQUIRED FIELD (`basis.kind`), with one fixed sentence per kind:

  own_output  our own output (items a week): in our hands, not an expected result
  history     the client's own last 30 days — the figures are in the sentence
  plan        the organic keyword research the client approved — said to be
              our estimate, not a market figure

`basis_line()` raises BasisMissing rather than print a bare number.
"""
import re
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "target_inputs"
PREFIX = "target_inputs:"
LANGS = ("he", "en", "fr", "ar", "ru")
REQUIRED = ("budget", "offer", "conversion", "baseline", "constraints")
MIN_BASELINE_RESULTS = 5          # fewer measured enquiries in 30 days than this is noise (= campaign_presentation.MIN_HISTORY_RESULTS)
MIN_BASELINE_SPEND_ILS = 1        # "no spend history": less than this spent on advertising in 30 days
LEARNING_DAYS = 30                # the learning period: we come back with what we learned by then
BASES = ("own_output", "history", "plan")
FIELDS = ("budget_ils", "sells", "margin_pct", "lead_time", "capacity", "seasonality")

# A line of the brief that STATES the thing. The matched line is quoted as it is.
BRIEF_PATTERNS = {
    "offer": re.compile(r"margin|מרווח|רווח גולמי|רווחיות|marge|هامش|марж", re.IGNORECASE),
    "conversion": re.compile(r"what_counts|conversion|המרה|נחשב(?:ת)? (?:תוצאה|המרה)|تحويل|конверси", re.IGNORECASE),
    "constraints": re.compile(r"lead[ _-]?time|זמן (?:ה)?אספקה|ימי עסקים|business days|capacity|קיבולת|כושר ייצור|"
                              r"season|עונתי|délai|saison|موسم|مهلة|срок (?:поставки|изготовления)|сезон", re.IGNORECASE),
}


class BasisMissing(Exception):
    """A number was about to be shown with nothing it rests on."""


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── Doors (each one is replaced in the tests) ───────────────────────────────

def _db():
    from agents.client_agent import _db as db
    return db()


def _read(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}").limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _write(client_id: int, value: dict):
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": value,
                                        "updated_at": _now().isoformat()}, on_conflict="key").execute()


def _forecast_budget(client_id: int):
    from agents.budget_agent import get_forecast
    forecast = get_forecast(client_id)
    return (float(forecast.get("stated_marketing_budget_ils") or 0) or None) if forecast.get("available") else None


def _brief_text(client_id: int) -> str:
    from core import brand_brief
    return brand_brief.text(client_id) or ""


def _conversion(client_id: int) -> dict:
    from core import conversion_types
    return conversion_types.get(client_id)


def _leads_30(client_id: int):
    from core import quarter_goals
    return quarter_goals._count_leads(client_id, 30, 0)


def _sales_30(client_id: int):
    from core import quarter_goals
    return quarter_goals._sales_30(client_id)


def _ad_spend_30(client_id: int):
    """What the platforms report as spent in the last 30 days; None when no
    advertising account is connected or readable (never a guessed zero)."""
    from agents.budget_agent import get_ad_spend
    spend = get_ad_spend(client_id)
    readable = [p for p in (spend.get("google_ads") or {}, spend.get("meta_ads") or {})
                if p.get("connected") and not p.get("error") and not p.get("needs_reconnect")]
    return float(spend.get("total_ils") or 0) if readable else None


def _safe(read, what: str, client_id: int):
    try:
        return read(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: {what} unreadable ({type(e).__name__})")
        return None


# ─── What is on record ───────────────────────────────────────────────────────

def recorded(client_id: int) -> dict:
    return (_safe(_read, "store", client_id) or {}).get("inputs") or {}


def _brief_line(text: str, key: str) -> str:
    for line in str(text or "").splitlines():
        if line.lstrip().startswith("#"):          # a heading names a section, it states nothing
            continue
        if BRIEF_PATTERNS[key].search(line):
            clean = re.sub(r"\[(?:stated|observed|inferred)[^\]]*\]", "", line, flags=re.IGNORECASE)
            return re.sub(r"^[\s\-*•#]+|\*\*|__|`", "", clean).strip()[:200]
    return ""


def budget(client_id: int):
    """The monthly marketing budget on record ({"value", "source"}) or None."""
    stored = recorded(client_id).get("budget_ils")
    if stored:
        return {"value": float(stored), "source": "recorded"}
    stated = _safe(_forecast_budget, "sales-chat budget", client_id)
    return {"value": float(stated), "source": "sales_chat"} if stated else None


def baseline(client_id: int) -> dict:
    """The measured last 30 days. Each figure is a measurement or None."""
    sales = _safe(_sales_30, "store sales", client_id) or {}
    return {"leads": _safe(_leads_30, "enquiries", client_id), "orders": sales.get("paid_orders"),
            "revenue": sales.get("net_revenue"), "spend": _safe(_ad_spend_30, "ad spend", client_id)}


SALES_WORDS = re.compile(r"sales|purchase|orders|מכירות|רכישות|הזמנות|ventes|مبيعات|продаж", re.IGNORECASE)


def result_kind(conversion: dict) -> str:
    """What this client counts as a result: "sales" (a store) or "leads"
    (enquiries) — from the recorded conversion, never assumed to be enquiries."""
    value = (conversion or {}).get("value")
    if isinstance(value, list):
        return "sales" if "store_sales" in value else "leads"
    return "sales" if SALES_WORDS.search(str(value or "")) else "leads"


def results_of(base: dict, kind: str = "leads"):
    return base.get("orders") if kind == "sales" else base.get("leads")


def thin(base: dict, kind: str = "leads") -> list:
    """Why the baseline cannot carry a result target ([] = it can). THE rule."""
    out = []
    if (results_of(base, kind) or 0) < MIN_BASELINE_RESULTS:
        out.append("few_results")
    if (base.get("spend") or 0) < MIN_BASELINE_SPEND_ILS:
        out.append("no_spend")
    return out


def state(client_id: int) -> dict:
    """{"inputs": {key: {"known", "value", "source"}}, "missing": [...],
    "baseline": {...}, "thin": [...]} — what every proposer asks first."""
    stored, brief = recorded(client_id), _safe(_brief_text, "brief", client_id) or ""
    inputs = {}
    money = budget(client_id)
    inputs["budget"] = {"known": bool(money), **(money or {})}
    if stored.get("sells") and stored.get("margin_pct"):
        inputs["offer"] = {"known": True, "value": f"{stored['sells']} · {stored['margin_pct']:g}%", "source": "recorded"}
    else:
        line = _brief_line(brief, "offer")
        inputs["offer"] = {"known": bool(line), "value": line, "source": "brief"} if line else {"known": False}
    conversion = _safe(_conversion, "conversion types", client_id) or {}
    line = _brief_line(brief, "conversion")
    if conversion.get("confirmed"):
        inputs["conversion"] = {"known": True, "value": list(conversion.get("types") or []), "source": "settings"}
    else:
        inputs["conversion"] = {"known": bool(line), "value": line, "source": "brief"} if line else {"known": False}
    base, kind = baseline(client_id), result_kind(inputs["conversion"])
    measured = results_of(base, kind) is not None
    inputs["baseline"] = {"known": measured, "value": base, "source": "measured"} if measured else {"known": False}
    kept = [str(stored[k]) for k in ("lead_time", "capacity", "seasonality") if stored.get(k)]
    line = _brief_line(brief, "constraints")
    if kept:
        inputs["constraints"] = {"known": True, "value": " · ".join(kept), "source": "recorded"}
    else:
        inputs["constraints"] = {"known": bool(line), "value": line, "source": "brief"} if line else {"known": False}
    return {"inputs": inputs, "missing": [k for k in REQUIRED if not inputs[k].get("known")],
            "baseline": base, "kind": kind, "thin": thin(base, kind)}


def missing(client_id: int) -> list:
    return state(client_id)["missing"]


def clean(fields: dict) -> dict:
    """The recordable fields, validated; ValueError names the bad one."""
    out = {}
    fields = fields if isinstance(fields, dict) else {}
    if fields.get("budget_ils") not in (None, ""):
        amount = float(fields["budget_ils"])
        if not 1 <= amount <= 10_000_000:
            raise ValueError("budget out of range")
        out["budget_ils"] = round(amount, 2)
    if fields.get("margin_pct") not in (None, ""):
        pct = float(fields["margin_pct"])
        if not 0 < pct < 100:
            raise ValueError("margin must be a percentage between 0 and 100")
        out["margin_pct"] = round(pct, 1)
    for key, limit in (("sells", 200), ("lead_time", 120), ("capacity", 160), ("seasonality", 160)):
        text = " ".join(str(fields.get(key) or "").split())[:limit]
        if text:
            out[key] = text
    return out


def record(client_id: int, fields: dict, by: str = "client") -> dict:
    """Merge what the client approved into the record. Returns the record."""
    new = clean(fields)
    if not new:
        raise ValueError("nothing to record")
    value = _read(client_id)
    inputs = {**(value.get("inputs") or {}), **new}
    history = (value.get("history") or [])[-29:] + [{"at": _now().isoformat(), "by": str(by)[:40], "fields": sorted(new)}]
    _write(client_id, {"inputs": inputs, "history": history})
    _block_cache.pop(int(client_id), None)
    return inputs


_block_cache = {}
BLOCK_CACHE_S = 300      # the state reads the store and the advertising account: not on every chat turn


def prompt_block(client_id: int) -> dict:
    """For the agents: what is missing, and the rule. Never raises."""
    import time
    hit = _block_cache.get(int(client_id))
    if hit and hit[0] > time.time() - BLOCK_CACHE_S:
        return hit[1]
    try:
        now = state(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: state unreadable ({type(e).__name__})")
        return {}
    out = _block(now)
    _block_cache[int(client_id)] = (time.time(), out)
    return out


def _block(now: dict) -> dict:
    return {"missing": now["missing"], "baseline_too_thin_for_a_result_target": bool(now["thin"]),
            "rule": "While `missing` is not empty do NOT propose a target, a budget split or a strategy: name what "
                    "is missing, ask for it, record it with record_target_inputs, and only then propose. When the "
                    "baseline is too thin, never state a result number: the first period is a learning period "
                    "(the goals card says so itself)."}


# ─── Words ───────────────────────────────────────────────────────────────────

NAMES = {
    "budget": _five("התקציב החודשי לשיווק", "the monthly marketing budget", "le budget marketing mensuel",
                    "ميزانية التسويق الشهرية", "месячный бюджет на маркетинг"),
    "offer": _five("מה נמכר, ובאיזה מרווח", "what is sold, and at what margin", "ce qui est vendu, et avec quelle marge",
                   "ما الذي يُباع وبأي هامش", "что продаётся и с какой наценкой"),
    "conversion": _five("מה נחשב אצלכם תוצאה", "what counts as a result for you", "ce qui compte comme un résultat pour vous",
                        "ما الذي يُعدّ نتيجة لديكم", "что у вас считается результатом"),
    "baseline": _five("נקודת ההתחלה: מה נמדד ב-30 הימים האחרונים", "the starting point: what was measured in the last 30 days",
                      "le point de départ : ce qui a été mesuré ces 30 derniers jours",
                      "نقطة البداية: ما قيس في آخر 30 يومًا", "точка отсчёта: что измерено за последние 30 дней"),
    "constraints": _five("מגבלות: זמן אספקה, כמה אפשר לספק, עונתיות", "constraints: lead time, how much you can supply, seasonality",
                         "contraintes : délai, capacité, saisonnalité", "القيود: مهلة التسليم، القدرة على التوريد، الموسمية",
                         "ограничения: сроки, сколько вы можете выполнить, сезонность"),
}
TYPE_NAMES = {
    "form_submit": _five("פניות מטופס", "form enquiries", "demandes par formulaire", "استفسارات النموذج", "заявки из формы"),
    "whatsapp_click": _five("פניות בוואטסאפ", "WhatsApp enquiries", "demandes WhatsApp", "استفسارات واتساب", "обращения в WhatsApp"),
    "phone_click": _five("שיחות טלפון", "phone calls", "appels", "مكالمات هاتفية", "звонки"),
    "store_sales": _five("מכירות באתר", "sales on the site", "ventes sur le site", "مبيعات الموقع", "продажи на сайте"),
}
T = {
    "missing_one": _five("לפני שאני מציע יעדים או תוכנית, חסר לי נתון אחד. בלעדיו כל מספר שאתן יהיה ניחוש:",
                         "Before I propose targets or a plan, one thing is missing. Without it any number I give would be a guess:",
                         "Avant de proposer des objectifs ou un plan, il me manque une donnée. Sans elle, tout chiffre serait une supposition :",
                         "قبل أن أقترح أهدافًا أو خطة ينقصني معطى واحد. بدونه أي رقم أعطيه سيكون تخمينًا:",
                         "Прежде чем предлагать цели или план, мне не хватает одного. Без этого любая цифра будет догадкой:"),
    "missing_many": _five("לפני שאני מציע יעדים או תוכנית, חסרים לי כמה נתונים. בלעדיהם כל מספר שאתן יהיה ניחוש:",
                          "Before I propose targets or a plan, a few things are missing. Without them any number I give would be a guess:",
                          "Avant de proposer des objectifs ou un plan, il me manque quelques données. Sans elles, tout chiffre serait une supposition :",
                          "قبل أن أقترح أهدافًا أو خطة تنقصني بعض المعطيات. بدونها أي رقم أعطيه سيكون تخمينًا:",
                          "Прежде чем предлагать цели или план, мне не хватает нескольких вещей. Без них любая цифра будет догадкой:"),
    "have": _five("מה כבר יש לנו:", "What we already have:", "Ce que nous avons déjà :", "ما لدينا بالفعل:", "Что у нас уже есть:"),
    "ask": _five("כתבו לי את מה שחסר, ואז אציע.", "Tell me what is missing, and then I will propose.",
                 "Indiquez-moi ce qui manque, et je proposerai ensuite.", "اكتبوا لي ما ينقص، ثم أقترح.",
                 "Напишите мне недостающее — и тогда я предложу."),
    "measured_leads": _five("ב-30 הימים האחרונים נמדדו: {n} פניות, והוצאה על פרסום: {spend}",
                            "measured in the last 30 days: {n} enquiries, and advertising spend: {spend}",
                            "mesuré ces 30 derniers jours : {n} demandes, et dépense publicitaire : {spend}",
                            "قيس في آخر 30 يومًا: {n} استفسارات، والإنفاق على الإعلان: {spend}",
                            "за последние 30 дней измерено: обращений — {n}, расход на рекламу — {spend}"),
    "measured_sales": _five("ב-30 הימים האחרונים נמדדו: {n} מכירות ({revenue} נטו), והוצאה על פרסום: {spend}",
                            "measured in the last 30 days: {n} sales ({revenue} net), and advertising spend: {spend}",
                            "mesuré ces 30 derniers jours : {n} ventes ({revenue} net), et dépense publicitaire : {spend}",
                            "قيس في آخر 30 يومًا: {n} مبيعات ({revenue} صافي)، والإنفاق على الإعلان: {spend}",
                            "за последние 30 дней измерено: продаж — {n} ({revenue} нетто), расход на рекламу — {spend}"),
    "on_record": _five("רשום אצלנו, מהאפיון שלכם", "on record with us, from your characterisation", "enregistré chez nous, d'après votre brief",
                       "مسجّل لدينا من توصيف نشاطكم", "записано у нас, из вашего брифа"),
    "unknown": _five("לא נמדד", "not measured", "non mesuré", "لم يُقس", "не измерено"),
    "thin_note": _five(
        "ועוד דבר, שכדאי לדעת כבר עכשיו: נקודת ההתחלה הזו דקה מדי בשביל יעד תוצאות. יעד אמיתי דורש לפחות {min} {unit} נמדדות ב-30 יום והוצאה בפועל על פרסום. לכן גם אחרי שיהיה תקציב, התקופה הראשונה תהיה תקופת למידה ולא מספר.",
        "One more thing you should know now: this starting point is too thin for a results target. A real target needs at least {min} measured {unit} in 30 days and actual advertising spend. So even once there is a budget, the first period will be a learning period, not a number.",
        "Autre chose à savoir dès maintenant : ce point de départ est trop mince pour un objectif de résultats. Un vrai objectif demande au moins {min} {unit} mesurées sur 30 jours et une dépense publicitaire réelle. Même avec un budget, la première période sera donc une période d'apprentissage, pas un chiffre.",
        "وأمر آخر يحسن معرفته الآن: نقطة البداية هذه أضعف من أن تحمل هدف نتائج. الهدف الحقيقي يتطلب {min} {unit} مقاسة على الأقل خلال 30 يومًا وإنفاقًا فعليًا على الإعلان. لذلك حتى بعد تحديد الميزانية ستكون الفترة الأولى فترة تعلّم لا رقمًا.",
        "И ещё одно, что стоит знать уже сейчас: такая точка отсчёта слишком слаба для цели по результатам. Настоящей цели нужно минимум {min} измеренных {unit} за 30 дней и реальный расход на рекламу. Поэтому даже с бюджетом первый период будет периодом обучения, а не цифрой."),
    "learn_title": _five("תקופת למידה, לא יעד תוצאות:", "A learning period, not a results target:",
                         "Une période d'apprentissage, pas un objectif de résultats :", "فترة تعلّم، لا هدف نتائج:",
                         "Период обучения, а не цель по результатам:"),
    "learn_why": _five(
        "למה: יעד אמיתי דורש לפחות {min} {unit} נמדדות ב-30 יום והוצאה בפועל על פרסום. אצלכם {measured}. מזה אי אפשר לגזור מספר, וכל מספר שאתן יהיה מומצא.",
        "Why: a real target needs at least {min} measured {unit} in 30 days and actual advertising spend. For you, {measured}. No number can be derived from that, and any number I gave would be invented.",
        "Pourquoi : un vrai objectif demande au moins {min} {unit} mesurées sur 30 jours et une dépense publicitaire réelle. Chez vous, {measured}. On ne peut en tirer aucun chiffre, et tout chiffre serait inventé.",
        "لماذا: الهدف الحقيقي يتطلب {min} {unit} مقاسة على الأقل خلال 30 يومًا وإنفاقًا فعليًا على الإعلان. لديكم {measured}. لا يمكن اشتقاق رقم من ذلك، وأي رقم أعطيه سيكون مختلقًا.",
        "Почему: настоящей цели нужно минимум {min} измеренных {unit} за 30 дней и реальный расход на рекламу. У вас {measured}. Из этого нельзя вывести цифру — любая была бы выдуманной."),
    "learn_spend": _five("מה נוציא: עד {budget} בחודש, התקציב שקבעתם.", "What we will spend: up to {budget} a month, the budget you set.",
                         "Ce que nous dépenserons : jusqu'à {budget} par mois, le budget que vous avez fixé.",
                         "ما سننفقه: حتى {budget} شهريًا، الميزانية التي حددتموها.",
                         "Сколько потратим: до {budget} в месяц — бюджет, который вы назначили."),
    "learn_what": _five("מה נלמד: כמה עולה להביא {one}, ומה מביא אותה.",
                        "What we will learn: what it costs to bring {one}, and what brings it.",
                        "Ce que nous apprendrons : ce que coûte {one}, et ce qui l'amène.",
                        "ما سنتعلمه: كم يكلّف جلب {one}، وما الذي يجلبه.",
                        "Что узнаем: сколько стоит {one} и что её приносит."),
    "learn_when": _five(
        "מתי יהיה יעד אמיתי: כשיצטברו {min} {unit} נמדדות עם הוצאה על פרסום. לא יאוחר מ-{date} נחזור אליכם עם מה שלמדנו; אם עד אז לא הצטבר מספיק, נגיד את זה ולא נמציא מספר.",
        "When there will be a real target: once {min} measured {unit} have come in with advertising spend. No later than {date} we come back with what we learned; if there is still not enough by then, we will say so and not invent a number.",
        "Quand il y aura un vrai objectif : dès que {min} {unit} mesurées seront arrivées avec une dépense publicitaire. Au plus tard le {date}, nous revenons avec ce que nous avons appris ; s'il n'y a toujours pas assez de données, nous le dirons sans inventer de chiffre.",
        "متى سيكون هناك هدف حقيقي: عند تجمّع {min} {unit} مقاسة مع إنفاق على الإعلان. في موعد أقصاه {date} نعود إليكم بما تعلمناه؛ وإن لم تكفِ البيانات حتى ذلك الحين فسنقول ذلك ولن نختلق رقمًا.",
        "Когда появится настоящая цель: когда накопится {min} измеренных {unit} при расходе на рекламу. Не позже {date} мы вернёмся с тем, что узнали; если данных всё ещё мало — скажем это и не станем выдумывать цифру."),
}
UNIT = {
    "leads": _five("פניות", "enquiries", "demandes", "استفسارات", "обращений"),
    "sales": _five("מכירות", "sales", "ventes", "مبيعات", "продаж"),
}
UNIT_ONE = {
    "leads": _five("פנייה אחת", "one enquiry", "une demande", "استفسار واحد", "одна заявка"),
    "sales": _five("מכירה אחת", "one sale", "une vente", "عملية بيع واحدة", "одна продажа"),
}
BASIS = {
    "own_output": _five("זה בידינו: כמות שאנחנו מפיקים, לא תוצאה צפויה", "in our hands: how much we produce, not an expected result",
                        "entre nos mains : ce que nous produisons, pas un résultat attendu",
                        "بأيدينا: كمية ما ننتجه، لا نتيجة متوقعة", "в наших руках: сколько мы производим, а не ожидаемый результат"),
    "history": _five("על בסיס הנתונים שלכם: {leads} פניות ב-30 הימים האחרונים בהוצאה של {spend} על פרסום, ותקציב חודשי של {budget}",
                     "based on your own figures: {leads} enquiries in the last 30 days for {spend} spent on advertising, and a monthly budget of {budget}",
                     "sur la base de vos chiffres : {leads} demandes ces 30 derniers jours pour {spend} dépensés en publicité, et un budget mensuel de {budget}",
                     "بناءً على أرقامكم: {leads} استفسارات في آخر 30 يومًا مقابل {spend} إنفاق على الإعلان، وميزانية شهرية {budget}",
                     "на основе ваших цифр: {leads} обращений за последние 30 дней при расходе на рекламу {spend} и месячном бюджете {budget}"),
    "history_sales": _five(
        "על בסיס הנתונים שלכם: {leads} מכירות בחנות ב-30 הימים האחרונים (כל המכירות, לא רק אלה שהגיעו מפרסום), הוצאה של {spend} על פרסום, ותקציב חודשי של {budget}",
        "based on your own figures: {leads} sales in the store in the last 30 days (all sales, not only those that came from advertising), {spend} spent on advertising, and a monthly budget of {budget}",
        "sur la base de vos chiffres : {leads} ventes dans la boutique ces 30 derniers jours (toutes les ventes, pas seulement celles venues de la publicité), {spend} dépensés en publicité, et un budget mensuel de {budget}",
        "بناءً على أرقامكم: {leads} مبيعات في المتجر في آخر 30 يومًا (كل المبيعات، لا تلك القادمة من الإعلان فقط)، و{spend} إنفاق على الإعلان، وميزانية شهرية {budget}",
        "на основе ваших цифр: {leads} продаж в магазине за последние 30 дней (все продажи, не только пришедшие с рекламы), расход на рекламу {spend} и месячный бюджет {budget}"),
    "plan": _five("זו הערכה שלנו לפי מחקר הביטויים שאישרתם, לא מדד מהשוק", "our own estimate from the phrase research you approved, not a market figure",
                  "notre estimation d'après la recherche d'expressions que vous avez approuvée, pas un chiffre du marché",
                  "تقديرنا بحسب بحث العبارات الذي وافقتم عليه، وليس مؤشرًا من السوق",
                  "наша оценка по согласованному вами исследованию фраз, а не рыночный показатель"),
}


def _pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or ""


def _money(value, lang: str) -> str:
    from core import client_language
    return client_language.fmt_money(round(float(value)), lang)


def measured_line(base: dict, lang: str, kind: str = "leads") -> str:
    """The last 30 days as they were measured, in the client's own kind of
    result — an unmeasured figure says so, it is never shown as zero."""
    unknown = _pick(T["unknown"], lang)
    n = results_of(base, kind)
    return (_pick(T["measured_sales" if kind == "sales" else "measured_leads"], lang)
            .replace("{n}", unknown if n is None else str(int(n)))
            .replace("{revenue}", unknown if base.get("revenue") is None else _money(base["revenue"], lang))
            .replace("{spend}", unknown if base.get("spend") is None else _money(base["spend"], lang)))


def _value_text(key: str, item: dict, lang: str, kind: str = "leads") -> str:
    value = item.get("value")
    if key == "budget":
        return _money(value, lang)
    if key == "baseline":
        return measured_line(value or {}, lang, kind)
    if key == "conversion" and isinstance(value, list):
        return ", ".join(_pick(TYPE_NAMES.get(t, {}), lang) or t for t in value)
    if key == "conversion":
        return _pick(UNIT[kind], lang)
    if item.get("source") == "brief":          # the brief is our internal, English document: its line is never quoted to a client
        return _pick(T["on_record"], lang)
    return str(value or "")


def missing_text(now: dict, lang: str) -> str:
    """What the client reads INSTEAD of a targets card while an input is
    missing: what is missing, what we already have, and — when the baseline is
    thin — that the first period will be a learning period anyway."""
    lang = lang if lang in LANGS else "he"
    gaps = now["missing"]
    lines = [_pick(T["missing_one" if len(gaps) == 1 else "missing_many"], lang)]
    lines += [f"• {_pick(NAMES[k], lang)}" for k in gaps]
    have = [k for k in REQUIRED if k not in gaps]
    if have:
        lines.append(_pick(T["have"], lang))
        for key in have:
            name = _pick(NAMES[key], lang).split(":")[0]
            lines.append(f"• {name}: {_value_text(key, now['inputs'][key], lang, now.get('kind') or 'leads')}")
    if now.get("thin") and "baseline" not in gaps:
        lines.append(_pick(T["thin_note"], lang).replace("{min}", str(MIN_BASELINE_RESULTS))
                     .replace("{unit}", _pick(UNIT[now.get("kind") or "leads"], lang)))
    lines.append(_pick(T["ask"], lang))
    return "\n".join(lines)


def learning(now: dict, today: datetime = None) -> dict:
    """What a learning-period card is built from (stored on the card, so what
    the client approved is what was shown)."""
    return {"baseline": dict(now["baseline"]), "budget": (now["inputs"]["budget"] or {}).get("value"),
            "kind": now.get("kind") or "leads", "min_results": MIN_BASELINE_RESULTS, "why": list(now["thin"]),
            "until": ((today or _now()) + timedelta(days=LEARNING_DAYS)).date().isoformat()}


def learning_lines(plan: dict, lang: str) -> list:
    """The learning period as the client reads it: why (with the threshold),
    what we spend, what we learn, when there will be a real target."""
    lang = lang if lang in LANGS else "he"
    if not plan or not plan.get("budget") or not plan.get("until"):
        raise BasisMissing("a learning period needs its budget and its date")
    from core import client_language
    low = str(plan.get("min_results") or MIN_BASELINE_RESULTS)
    kind = plan.get("kind") if plan.get("kind") in UNIT else "leads"
    unit = _pick(UNIT[kind], lang)
    return [_pick(T["learn_title"], lang),
            "• " + _pick(T["learn_why"], lang).replace("{min}", low).replace("{unit}", unit)
            .replace("{measured}", measured_line(plan.get("baseline") or {}, lang, kind)),
            "• " + _pick(T["learn_spend"], lang).replace("{budget}", _money(plan["budget"], lang)),
            "• " + _pick(T["learn_what"], lang).replace("{one}", _pick(UNIT_ONE[kind], lang)),
            "• " + _pick(T["learn_when"], lang).replace("{min}", low).replace("{unit}", unit)
            .replace("{date}", client_language.fmt_date(plan["until"], lang) or plan["until"])]


def history_basis(now: dict):
    """The basis of a RESULT target, or None when the client's own history
    cannot carry one (thin, or no budget)."""
    money = (now["inputs"].get("budget") or {}).get("value")
    if now["thin"] or not money:
        return None
    base = now["baseline"]
    out = {"kind": "history", "spend": round(float(base["spend"]), 2), "budget": float(money)}
    if now.get("kind") == "sales":          # this client counts sales: the basis is the store's paid orders
        return {**out, "unit": "sales", "orders": int(base["orders"])}
    return {**out, "leads": int(base["leads"])}


def basis_results(basis: dict):
    """The measured results a history basis rests on, in its own unit."""
    return basis.get("orders") if basis.get("unit") == "sales" else basis.get("leads")


def metric_of(kind: str) -> str:
    """The quarter-goals RESULT metric for what this client counts."""
    return "sales_30d" if kind == "sales" else "leads_30d"


def history_ceiling(basis: dict) -> int:
    """The most enquiries in 30 days the client's own history supports at the
    recorded budget (budget / what one enquiry cost). An aim above it has no
    basis, so the card never carries one."""
    cost_per = float(basis["spend"]) / float(basis_results(basis))
    return int(float(basis["budget"]) // cost_per)


def basis_line(basis: dict, lang: str) -> str:
    """The fixed sentence for what a number rests on — BasisMissing when the
    number has no basis or the basis is incomplete."""
    lang = lang if lang in LANGS else "he"
    kind = (basis or {}).get("kind")
    if kind not in BASES:
        raise BasisMissing("no basis type on the number")
    text = _pick(BASIS[kind], lang)
    if kind == "history":
        if any(v in (None, "") for v in (basis_results(basis), basis.get("spend"), basis.get("budget"))):
            raise BasisMissing("a history basis without its figures")
        if basis.get("unit") == "sales":
            text = _pick(BASIS["history_sales"], lang)
        text = (text.replace("{leads}", str(int(basis_results(basis)))).replace("{spend}", _money(basis["spend"], lang))
                .replace("{budget}", _money(basis["budget"], lang)))
    return text
