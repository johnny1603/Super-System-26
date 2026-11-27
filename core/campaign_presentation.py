"""A campaign, as the client is shown it (2026-11-28, knowledge/client-governance.md §9).

Four things, in this order, or it does not go out:

  1. WHERE it will appear — places a person recognises (core/client_speech.where)
  2. THE BUDGET — total, and per day
  3. THE GOAL — in business terms
  4. WHAT WE EXPECT for that money, and what the expectation rests on

Point 4 is never a bare number. `expected` carries its basis:

  history    this client's own past performance (always preferred) — the
             period, what was spent and what it brought
  benchmark  an industry figure, with the name of its source
  estimate   no history: a RANGE, with the assumptions said out loud, and
             SAID to be our own estimate and not a market figure

Which of the three it is is a REQUIRED FIELD on the presentation
(`basis_type`), never wording a model chooses: the sentence that tells the
client what the number rests on is fixed per type (`BASIS_SAYS`), and an
estimate whose assumptions cite a study, a market figure or an industry
average is refused (`estimate_claims_a_source`).

The number shown already holds the internal margin (§5, governance.BAND):
from history the range runs from the aim less the margin up to the aim.

`problems(p)` names what is missing; `render(p, lang)` raises
PresentationIncomplete rather than produce a partial text, and passes its own
output through core/client_speech before returning it.

Not here: any benchmark figures. None are stored in the system, so the
benchmark basis is supported and unused; and an estimate's cost range comes
from the Google Ads specialist's model call — it is shown AS an estimate, with
its assumptions, never as data.
"""
import math
import re

from core import client_speech

SERVICE_NAME = "campaign_presentation"
LANGS = client_speech.LANGS
BASES = ("history", "benchmark", "estimate")
MIN_HISTORY_RESULTS = 5         # fewer past results than this is noise, not a basis
MIN_ESTIMATE_SPREAD = 1.25      # an estimate's cost range must be a real range
DEFAULT_DAYS = 30
HISTORY_METRIC = {"sales": "conversions", "leads": "conversions", "website_traffic": "clicks"}


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


GOALS = {
    "sales": _five("מכירות", "sales", "des ventes", "مبيعات", "продажи"),
    "leads": _five("פניות מלקוחות חדשים", "enquiries from new customers", "des demandes de nouveaux clients",
                   "استفسارات من عملاء جدد", "заявки от новых клиентов"),
    "website_traffic": _five("כניסות לאתר שלכם", "visits to your website", "des visites sur votre site",
                             "زيارات لموقعكم", "посещения вашего сайта"),
    "awareness": _five("שיותר אנשים יכירו אתכם", "more people knowing you", "que plus de personnes vous connaissent",
                       "أن يعرفكم عدد أكبر من الناس", "чтобы о вас узнало больше людей"),
}
UNITS = {
    "sales": _five("מכירות", "sales", "ventes", "مبيعات", "продаж"),
    "leads": _five("פניות", "enquiries", "demandes", "استفسارات", "заявок"),
    "website_traffic": _five("כניסות לאתר", "website visits", "visites du site", "زيارات للموقع", "посещений сайта"),
    "awareness": _five("אנשים שיראו את המודעה", "people who see the ad", "personnes qui voient l'annonce",
                       "أشخاص يرون الإعلان", "человек, которые увидят объявление"),
}
UNIT_ONE = {
    "sales": _five("מכירה", "a sale", "une vente", "عملية بيع", "одна продажа"),
    "leads": _five("פנייה", "an enquiry", "une demande", "استفسار واحد", "одна заявка"),
    "website_traffic": _five("כניסה לאתר", "a website visit", "une visite du site", "زيارة واحدة للموقع", "одно посещение сайта"),
    "awareness": _five("הגעה לאלף אנשים", "reaching a thousand people", "le fait de toucher mille personnes",
                       "الوصول إلى ألف شخص", "показ тысяче человек"),
}
T = {
    "where": _five("איפה זה יופיע: {where}.", "Where it will appear: {where}.", "Où elle apparaîtra : {where}.",
                   "أين ستظهر: {where}.", "Где это появится: {where}."),
    "budget": _five("התקציב: {total} בסך הכול — {daily} ליום, למשך {days} ימים.",
                    "The budget: {total} in total — {daily} a day, for {days} days.",
                    "Le budget : {total} au total — {daily} par jour, pendant {days} jours.",
                    "الميزانية: {total} إجمالًا — {daily} في اليوم، لمدة {days} يومًا.",
                    "Бюджет: {total} всего — {daily} в день, на {days} дней."),
    "goal": _five("המטרה: {goal}.", "The goal: {goal}.", "L'objectif : {goal}.", "الهدف: {goal}.", "Цель: {goal}."),
    "history": _five(
        "מה אנחנו מצפים: על בסיס הביצועים שלכם ב-{period} הימים האחרונים ({results} {unit} בהוצאה של {spend}), בתקציב הזה אנחנו מכוונים ל-{low} עד {high} {unit}.",
        "What we expect: based on your own results in the last {period} days ({results} {unit} for {spend} spent), with this budget we are aiming for {low} to {high} {unit}.",
        "Ce que nous attendons : sur la base de vos résultats des {period} derniers jours ({results} {unit} pour {spend} dépensés), avec ce budget nous visons {low} à {high} {unit}.",
        "ما نتوقعه: بناءً على نتائجكم في آخر {period} يومًا ({results} {unit} مقابل إنفاق {spend})، بهذه الميزانية نستهدف {low} إلى {high} {unit}.",
        "Чего мы ждём: на основе ваших результатов за последние {period} дней ({results} {unit} при расходе {spend}) с этим бюджетом мы нацелены на {low} до {high} {unit}."),
    "benchmark": _five(
        "מה אנחנו מצפים: לפי ממוצע בענף ({source}) — עדיין לא נתונים שלכם — בתקציב הזה אנחנו מכוונים ל-{low} עד {high} {unit}.",
        "What we expect: by an industry average ({source}) — not yet your own data — with this budget we are aiming for {low} to {high} {unit}.",
        "Ce que nous attendons : selon une moyenne du secteur ({source}) — pas encore vos propres données — avec ce budget nous visons {low} à {high} {unit}.",
        "ما نتوقعه: وفق متوسط القطاع ({source}) — وليست بياناتكم بعد — بهذه الميزانية نستهدف {low} إلى {high} {unit}.",
        "Чего мы ждём: по среднему по отрасли ({source}) — это ещё не ваши данные — с этим бюджетом мы нацелены на {low} до {high} {unit}."),
    # The client must be able to tell a guess from data: the first sentence is fixed and says it in these words
    "estimate": _five(
        "מה אנחנו מצפים: אין עדיין נתונים מהחשבון שלכם, אז זו הערכה שלנו לפי היכרות עם התחום ולא מדד מהשוק. בהנחה ש{one} עולה בין {cost_low} ל-{cost_high} ({assumptions}), בתקציב הזה אנחנו מכוונים ל-{low} עד {high} {unit}.",
        "What we expect: there is no data from your account yet, so this is our own estimate from knowing the field, not a market figure. Assuming {one} costs between {cost_low} and {cost_high} ({assumptions}), with this budget we are aiming for {low} to {high} {unit}.",
        "Ce que nous attendons : il n'y a pas encore de données de votre compte, c'est donc notre estimation d'après notre connaissance du secteur, et non un chiffre du marché. En supposant que {one} coûte entre {cost_low} et {cost_high} ({assumptions}), avec ce budget nous visons {low} à {high} {unit}.",
        "ما نتوقعه: لا توجد بيانات من حسابكم بعد، لذا هذا تقديرنا بحسب معرفتنا بالمجال وليس مؤشرًا من السوق. بافتراض أن {one} يكلّف بين {cost_low} و{cost_high} ({assumptions})، بهذه الميزانية نستهدف {low} إلى {high} {unit}.",
        "Чего мы ждём: данных из вашего аккаунта пока нет, поэтому это наша оценка по знанию отрасли, а не рыночный показатель. При условии, что {one} стоит от {cost_low} до {cost_high} ({assumptions}), с этим бюджетом мы нацелены на {low} до {high} {unit}."),
}
# What the client is told the expectation rests on — one fixed phrase per BASIS TYPE, chosen by the
# type and never by a model. `render` refuses a text that does not carry its type's phrase.
BASIS_SAYS = {
    "history": _five("על בסיס הביצועים שלכם", "based on your own results", "sur la base de vos résultats", "بناءً على نتائجكم",
                     "на основе ваших результатов"),
    "benchmark": _five("לפי ממוצע בענף", "by an industry average", "selon une moyenne du secteur", "وفق متوسط القطاع",
                       "по среднему по отрасли"),
    "estimate": _five("זו הערכה שלנו לפי היכרות עם התחום ולא מדד מהשוק", "this is our own estimate from knowing the field, not a market figure",
                      "notre estimation d'après notre connaissance du secteur, et non un chiffre du marché",
                      "هذا تقديرنا بحسب معرفتنا بالمجال وليس مؤشرًا من السوق", "это наша оценка по знанию отрасли, а не рыночный показатель"),
}
# An estimate's assumptions are a model's words. They may not borrow the authority of data they do not have.
SOURCE_CLAIM = re.compile(
    r"ממוצע (?:ב|ה)?ענף|ממוצע ענפי|נתוני (?:ה)?שוק|לפי (?:ה)?(?:נתונים|מחקר|סקר|סטטיסטיק)|מחקר(?:ים)? מרא|בנצ'?מרק|"
    r"\bbenchmark\w*|\bindustry (?:average|data|standard)\w*|\bmarket (?:data|average|research)\b|\baccording to\b|\bstud(?:y|ies) show\w*|\bstatistic\w*|"
    r"\bmoyenne du secteur\b|\bdonnées du marché\b|\bselon (?:les|une|des) (?:données|études?)\b|"
    r"متوسط القطاع|بيانات السوق|بحسب (?:ال)?(?:بيانات|دراس)|"
    r"средн\w+ по отрасли|данны\w+ рынка|по данным|исследовани\w+ показ", re.IGNORECASE)
RANGE_JOIN = _five(" עד ", " to ", " à ", " إلى ", " до ")        # as the templates above join a range
PROBLEM_NAMES = {
    "where_missing": "where it will appear", "budget_missing": "the budget (total and per day)",
    "goal_missing": "the goal in business terms", "expected_missing": "the expected result",
    "basis_missing": "what the expected result rests on", "basis_incomplete": "the basis is named but its details are not given",
    "estimate_not_a_range": "an estimate must be a range", "assumptions_missing": "an estimate must state its assumptions",
    "basis_type_missing": "the basis type (history | benchmark | estimate) is not set on the presentation",
    "basis_type_mismatch": "the basis type on the presentation is not the one the expected result carries",
    "estimate_claims_a_source": "an estimate with no data may not cite a market figure, a study or an industry average",
    "basis_not_said": "the text does not say what kind of basis the expected result has",
    "language": "the text uses internal vocabulary",
}


class PresentationIncomplete(Exception):
    """A required element is missing: .codes names each."""

    def __init__(self, codes: list, detail: str = ""):
        self.codes = list(codes)
        super().__init__("campaign presentation is missing: " + ", ".join(PROBLEM_NAMES.get(c, c) for c in self.codes)
                         + (f" ({detail})" if detail else ""))


def _num(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


# ─── The expected result, with its basis ─────────────────────────────────────

def history(client_id: int) -> dict:
    """The account's own last 30 days of Google campaigns — {"cost",
    "conversions", "clicks", ...}; {} = none, not connected or not read."""
    try:
        from agents.google_ads_agent import get_campaign_performance
        return (get_campaign_performance(client_id) or {}).get("totals") or {}
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: campaign history unreadable ({type(e).__name__})")
        return {}


def from_history(objective: str, budget_total, totals: dict, period_days: int = 30):
    """An expectation built on the client's own last `period_days`, or None
    when there is not enough of it (no spend, too few results, an objective
    the account does not measure)."""
    from core import governance
    metric = HISTORY_METRIC.get(objective)
    spend, results, budget = _num((totals or {}).get("cost")), _num((totals or {}).get(metric or "")), _num(budget_total)
    if not metric or not spend or not results or not budget or spend <= 0 or results < MIN_HISTORY_RESULTS:
        return None
    aim = budget / (spend / results)
    high, low = int(math.floor(aim)), int(math.floor(aim * (1 - governance.BAND)))
    if high < 1:
        return None
    return {"low": low, "high": high, "unit": objective,
            "basis": {"kind": "history", "period_days": int(period_days), "spend": round(spend, 2), "results": round(results, 1)}}


def from_estimate(objective: str, budget_total, cost_low, cost_high, assumptions: str):
    """An expectation with NO history: a range from an assumed cost per
    result, with the assumptions kept beside it. None when the inputs are not
    a real range."""
    low_cost, high_cost, budget = _num(cost_low), _num(cost_high), _num(budget_total)
    if not low_cost or not high_cost or not budget or low_cost <= 0 or high_cost < low_cost * MIN_ESTIMATE_SPREAD:
        return None
    per = 1000 if objective == "awareness" else 1
    return {"low": int(math.floor(budget / high_cost)) * per, "high": int(math.floor(budget / low_cost)) * per, "unit": objective,
            "basis": {"kind": "estimate", "cost_low": round(low_cost, 2), "cost_high": round(high_cost, 2),
                      "assumptions": str(assumptions or "").strip()[:240]}}


def clean_expected(value) -> dict:
    """A stored `expected` reduced to the known keys ({} = unusable)."""
    if not isinstance(value, dict) or not isinstance(value.get("basis"), dict):
        return {}
    low, high = _num(value.get("low")), _num(value.get("high"))
    if low is None or high is None:
        return {}
    basis = {k: value["basis"][k] for k in ("kind", "period_days", "spend", "results", "source", "cost_low", "cost_high",
                                           "assumptions") if value["basis"].get(k) not in (None, "")}
    return {"low": int(low), "high": int(high), "unit": str(value.get("unit") or ""), "basis": basis}


# ─── The four elements ───────────────────────────────────────────────────────

def build(campaign_type: str, objective: str, budget_total, days=None, expected: dict = None, basis_type: str = "") -> dict:
    """`basis_type` is given by the caller, from the path that PRODUCED the
    expectation (from_history / a named benchmark / from_estimate) — it is not
    read back out of `expected`, so the two must agree or nothing is shown."""
    total, days = _num(budget_total), int(_num(days) or DEFAULT_DAYS)
    return {"campaign_type": campaign_type, "objective": objective, "budget_total": total, "days": days,
            "budget_daily": round(total / days, 2) if total and days > 0 else None,
            "expected": clean_expected(expected),
            # REQUIRED, and a field — never wording: which of the three bases the expected result has
            "basis_type": str(basis_type or "")}


def problems(p: dict) -> list:
    """The codes of what is missing — [] = all four elements are there."""
    out = []
    if not client_speech.where(p.get("campaign_type") or ""):
        out.append("where_missing")
    if not (p.get("budget_total") or 0) > 0 or not (p.get("days") or 0) > 0 or not p.get("budget_daily"):
        out.append("budget_missing")
    if p.get("objective") not in GOALS:
        out.append("goal_missing")
    expected = p.get("expected") or {}
    if not expected or expected.get("unit") not in UNITS or (expected.get("high") or 0) < 1:
        return out + ["expected_missing"]
    basis = expected.get("basis") or {}
    kind = basis.get("kind")
    if kind not in BASES:
        return out + ["basis_missing"]
    if p.get("basis_type") not in BASES:
        return out + ["basis_type_missing"]
    if p["basis_type"] != kind:
        return out + ["basis_type_mismatch"]
    if kind == "history" and not (basis.get("period_days") and basis.get("spend") and basis.get("results")):
        out.append("basis_incomplete")
    if kind == "benchmark" and not str(basis.get("source") or "").strip():
        out.append("basis_incomplete")
    if kind == "estimate":
        if not (basis.get("cost_low") and basis.get("cost_high")) or not expected["low"] < expected["high"]:
            out.append("estimate_not_a_range")
        if not str(basis.get("assumptions") or "").strip():
            out.append("assumptions_missing")
        elif SOURCE_CLAIM.search(str(basis["assumptions"])):
            out.append("estimate_claims_a_source")
    return out


def render(p: dict, lang: str = "he") -> str:
    """The four lines, in order, in the client's language — or
    PresentationIncomplete. The result has passed core/client_speech."""
    from core import client_language
    lang = lang if lang in LANGS else "he"
    missing = problems(p)
    if missing:
        raise PresentationIncomplete(missing)
    expected, objective = p["expected"], p["objective"]
    basis = expected["basis"]
    money = lambda v: client_language.fmt_money(v, lang)                    # noqa: E731
    count = lambda v: client_language.fmt_number(v, lang, 0)                # noqa: E731
    assumptions = ""
    if basis["kind"] == "estimate":
        try:                                   # model-written: the table's words, or it does not go out
            assumptions = client_speech.enforce(basis["assumptions"], lang, where="campaign estimate assumptions").rstrip(". ")
        except client_speech.Blocked:
            raise PresentationIncomplete(["assumptions_missing"], "the assumptions used internal vocabulary")
    low = expected["low"] if expected["low"] < expected["high"] else None
    fourth = (T[basis["kind"]][lang]
              .replace("{period}", str(basis.get("period_days", ""))).replace("{results}", count(basis.get("results", 0)))
              .replace("{spend}", money(basis.get("spend", 0))).replace("{source}", str(basis.get("source", "")))
              .replace("{one}", UNIT_ONE[expected["unit"]][lang]).replace("{cost_low}", money(basis.get("cost_low", 0)))
              .replace("{cost_high}", money(basis.get("cost_high", 0))).replace("{assumptions}", assumptions)
              .replace("{low}", count(low if low is not None else expected["high"]))
              .replace("{high}", count(expected["high"])).replace("{unit}", UNITS[expected["unit"]][lang]))
    if low is None:                            # history that rounds to one number: say the one number, not "9 to 9"
        one = count(expected["high"])
        fourth = fourth.replace(f"{one}{RANGE_JOIN[lang]}{one}", one)
    lines = [T["where"][lang].replace("{where}", client_speech.where(p["campaign_type"], lang)),
             T["budget"][lang].replace("{total}", money(p["budget_total"])).replace("{daily}", money(p["budget_daily"]))
             .replace("{days}", str(p["days"])),
             T["goal"][lang].replace("{goal}", GOALS[objective][lang]), fourth]
    text = "\n".join(lines)
    if BASIS_SAYS[p["basis_type"]][lang] not in text:
        raise PresentationIncomplete(["basis_not_said"])
    found = client_speech.problems(text, lang)
    if found:
        raise PresentationIncomplete(["language"], "; ".join(client_speech.problem_lines(found)))
    return text


def check_text(text: str, lang: str = None) -> list:
    """The five checks on a campaign text someone ELSE wrote (a model, a
    human): forbidden terms and unbased expectations by name. The four
    elements are checked on the structure (`problems`), never guessed from prose."""
    return client_speech.problem_lines(client_speech.problems(text, lang))
