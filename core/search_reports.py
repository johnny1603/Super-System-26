"""The two reports the Google specialist was missing (2026-11-29).

1. SEARCH TERMS — what people actually typed when a search ad was shown
   (`search_term_view`, search campaigns only). The output is a DECISION list,
   not a table: terms that cost money and produced nothing (candidates to
   exclude) and terms that produced results and are not yet phrases of ours
   (candidates to add). Both exist ONLY when the account is `measuring`
   (core/conversion_measurement): "produced nothing" means nothing in an
   account that counts nothing.
2. IMPRESSION SHARE — how much of the demand we capture, per campaign, ALWAYS
   split: lost to BUDGET (the campaign is too small; more money buys more) and
   lost to RANK (relevance / quality; more money burns faster). Opposite
   actions, so there is no blended number anywhere in this module, and the
   action attached is the one of whichever is dominant.

Performance Max is different and is never conflated: it has no search terms
report (only category insights, a separate and more limited resource that this
module does NOT read), and impression share is not reported for it the way it
is for Search. Both are SAID, never shown as a blank or a zero.

Two operations per read (each `search` is one operation, counted by
core/google_ads_service against core/google_ads_quota). Stored per client in
`search_reports:<id>`; read by the daily scan after the measurement state.

NOT VERIFIED LIVE: the two queries were written from Google's field reference
and have not run against a live account from this code. A refused query stores
the error; nothing is concluded from it.
"""
import hashlib
import time
from datetime import datetime, timezone

SERVICE_NAME = "search_reports"
PREFIX = "search_reports:"
LANGS = ("he", "en", "fr", "ar", "ru")
WINDOW_DAYS = 30
PAGE_REFRESH_HOURS = 24
KEEP_TERMS = 200                 # stored, most expensive first
SHOWN_TERMS = 25                 # on the page
MAX_CANDIDATES = 15              # per decision group
# OUR OWN decision thresholds (not market figures): a term is a candidate to exclude once it has had
# a fair chance — this many clicks AND this much spent — and still produced nothing.
WASTE_MIN_CLICKS = 10
WASTE_MIN_COST_ILS = 30.0
# Impression share: Google reports "under 10%" as 0.0999 and "over 90%" as 0.9001.
LOW_MARK, HIGH_MARK = 0.0999, 0.9001
LOST_MATTERS = 0.10              # less lost than this: nothing to act on
LOST_CLEARLY_MORE = 0.05         # one cause leads the other by at least this, else "both"

TERMS_GAQL = (
    "SELECT search_term_view.search_term, search_term_view.status, campaign.id, campaign.name, "
    "segments.keyword.info.text, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions "
    "FROM search_term_view WHERE segments.date DURING LAST_30_DAYS "
    "AND campaign.advertising_channel_type = 'SEARCH' ORDER BY metrics.cost_micros DESC LIMIT 1000")
SHARE_GAQL = (
    "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, metrics.impressions, "
    "metrics.cost_micros, metrics.search_impression_share, metrics.search_budget_lost_impression_share, "
    "metrics.search_rank_lost_impression_share, metrics.search_top_impression_share, "
    "metrics.search_absolute_top_impression_share "
    "FROM campaign WHERE segments.date DURING LAST_30_DAYS AND campaign.status != 'REMOVED'")
STATUS = {"ADDED": "added", "EXCLUDED": "excluded", "ADDED_EXCLUDED": "excluded", "NONE": "none"}


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


def _measuring(client_id: int) -> str:
    from core import conversion_measurement
    return conversion_measurement.state(client_id)


# ─── 1. Search terms ─────────────────────────────────────────────────────────

def term_id(campaign_id, term: str) -> str:
    return hashlib.sha1(f"{campaign_id}\n{term}".encode("utf-8")).hexdigest()[:10]


def terms(rows: list) -> list:
    """One row per (campaign, typed term), most expensive first. The matched
    phrase is the one that took most of the term's cost."""
    merged = {}
    for row in rows or []:
        view, campaign, metrics = row.get("searchTermView") or {}, row.get("campaign") or {}, row.get("metrics") or {}
        text = str(view.get("searchTerm") or "").strip()
        if not text:
            continue
        key = (str(campaign.get("id") or ""), text)
        cost = int(metrics.get("costMicros") or 0) / 1_000_000
        item = merged.setdefault(key, {"id": term_id(*key), "term": text, "campaign": key[0], "campaign_name": campaign.get("name") or "",
                                       "status": "none", "matched": "", "_top": -1.0, "impressions": 0, "clicks": 0,
                                       "cost": 0.0, "conversions": 0.0})
        status = STATUS.get(str(view.get("status") or "NONE"), "none")
        if status != "none":
            item["status"] = "excluded" if "excluded" in (status, item["status"]) else status
        matched = str((((row.get("segments") or {}).get("keyword") or {}).get("info") or {}).get("text") or "")
        if matched and cost > item["_top"]:
            item["matched"], item["_top"] = matched, cost
        item["impressions"] += int(metrics.get("impressions") or 0)
        item["clicks"] += int(metrics.get("clicks") or 0)
        item["cost"] += cost
        item["conversions"] += float(metrics.get("conversions") or 0)
    out = sorted(merged.values(), key=lambda t: (-t["cost"], -t["clicks"], t["term"]))
    for item in out:
        item.pop("_top")
        item["cost"], item["conversions"] = round(item["cost"], 2), round(item["conversions"], 2)
    return out


def decide(found: list, measuring: str) -> dict:
    """THE decision list. Both groups are empty, and say why, unless the
    account is `measuring`: a conclusion from conversions needs conversions
    to be counted."""
    if measuring != "measuring":
        return {"gated": measuring or "unknown", "exclude": [], "add": []}
    fresh = [t for t in found if t["status"] == "none"]
    exclude = [t for t in fresh if t["conversions"] <= 0 and t["clicks"] >= WASTE_MIN_CLICKS and t["cost"] >= WASTE_MIN_COST_ILS]
    add = sorted((t for t in fresh if t["conversions"] >= 1), key=lambda t: (-t["conversions"], t["cost"]))
    return {"gated": "", "exclude": exclude[:MAX_CANDIDATES], "add": add[:MAX_CANDIDATES]}


# ─── 2. Impression share ─────────────────────────────────────────────────────

def _part(value):
    """A share as Google gave it: None = not reported (never a zero)."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def dominant(lost_budget, lost_rank) -> str:
    """"budget" | "rank" | "both" | "none" — which loss leads. THE rule the
    attached action follows; "under 10%" is not a loss to act on."""
    budget = 0.0 if lost_budget is None or abs(lost_budget - LOW_MARK) < 1e-4 else lost_budget
    rank = 0.0 if lost_rank is None or abs(lost_rank - LOW_MARK) < 1e-4 else lost_rank
    if max(budget, rank) < LOST_MATTERS:
        return "none"
    if abs(budget - rank) < LOST_CLEARLY_MORE:
        return "both"
    return "budget" if budget > rank else "rank"


def shares(rows: list) -> list:
    """Per campaign that showed at all: the share and its SPLIT. A campaign
    type for which Google reports no share is `available: False`."""
    out = []
    for row in rows or []:
        campaign, metrics = row.get("campaign") or {}, row.get("metrics") or {}
        if not int(metrics.get("impressions") or 0):
            continue
        channel = str(campaign.get("advertisingChannelType") or "")
        share = _part(metrics.get("searchImpressionShare"))
        lost_budget, lost_rank = _part(metrics.get("searchBudgetLostImpressionShare")), _part(metrics.get("searchRankLostImpressionShare"))
        # the split is the value: a share with no split is not shown as a share at all
        available = channel == "SEARCH" and share is not None and lost_budget is not None and lost_rank is not None
        item = {"campaign": str(campaign.get("id") or ""), "campaign_name": campaign.get("name") or "", "channel": channel,
                "status": campaign.get("status") or "", "cost": round(int(metrics.get("costMicros") or 0) / 1_000_000, 2),
                "available": available}
        if available:
            item.update(share=share, lost_budget=lost_budget, lost_rank=lost_rank,
                        top=_part(metrics.get("searchTopImpressionShare")),
                        absolute_top=_part(metrics.get("searchAbsoluteTopImpressionShare")),
                        dominant=dominant(lost_budget, lost_rank))
        out.append(item)
    return sorted(out, key=lambda c: -c["cost"])


# ─── The read ────────────────────────────────────────────────────────────────

def refresh(client_id: int, conn: dict = None, measuring: str = None) -> dict:
    """Read both reports now (two operations), STORE and return. Never raises:
    an unreadable report is stored with Google's message and concludes nothing."""
    value = {"checked_at": _now().isoformat(), "window_days": WINDOW_DAYS, "connected": True}
    try:
        conn = conn or _connection(client_id)
        if not (conn.get("access_token") and conn.get("account_id")):
            value.update(connected=False)
    except Exception as e:
        value.update(connected=False, error=f"{type(e).__name__}: {str(e)[:300]}")
    if value["connected"]:
        value["measurement"] = measuring if measuring is not None else _measuring(client_id)
        for name, gaql, shape in (("terms", TERMS_GAQL, terms), ("shares", SHARE_GAQL, shares)):
            try:
                value[name] = shape(_search(conn, gaql))
            except Exception as e:
                print(f"[{SERVICE_NAME}] client {client_id}: {name} unreadable ({type(e).__name__})")
                value[name + "_error"] = f"{type(e).__name__}: {str(e)[:300]}"
        if "terms" in value:
            value["terms_total"] = len(value["terms"])
            value["decisions"] = decide(value["terms"], value["measurement"])
            value["terms"] = value["terms"][:KEEP_TERMS]
    try:
        _write_store(client_id, value)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: not stored ({type(e).__name__})")
    _cache.pop(int(client_id), None)
    return value


_cache = {}
CACHE_S = 60


def current(client_id: int) -> dict:
    hit = _cache.get(int(client_id))
    if hit and hit[0] > time.time() - CACHE_S:
        return hit[1]
    try:
        value = _read_store(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: stored reports unreadable ({type(e).__name__})")
        return {}
    _cache[int(client_id)] = (time.time(), value)
    return value


def _age_hours(value: dict):
    try:
        then = datetime.fromisoformat(str(value["checked_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return (_now() - then).total_seconds() / 3600


def for_page(client_id: int) -> dict:
    """The stored read — refreshed when older than a day (two operations)."""
    value = current(client_id)
    age = _age_hours(value)
    if not value or age is None or age > PAGE_REFRESH_HOURS:
        value = refresh(client_id)
    return value


def conclusions(client_id: int, value: dict = None) -> dict:
    """`value` (default: the stored read) as it may be USED: measurement state
    read now, the decision groups emptied unless it is `measuring`."""
    value = dict(value if value is not None else current(client_id))
    if not value:
        return {}
    try:
        now = _measuring(client_id)
    except Exception:
        now = "unknown"
    value["measurement"] = now
    if "terms" in value:
        value["decisions"] = decide(value["terms"], now)
    return value


# ─── What an agent is given (the strategy, the chat) ─────────────────────────

RULE = ("SEARCH TERMS: read for WASTE first, opportunity second. `exclude_candidates` and `add_candidates` exist only when the "
        "account measures results; when `conclusions_withheld` is set, draw NO conclusion about which term works. "
        "IMPRESSION SHARE is never one number: `lost_to_budget` means the campaign is too small (more budget buys more showings); "
        "`lost_to_rank` means the ads and phrases do not fit well enough (more budget only spends faster). NEVER recommend "
        "more budget for a campaign whose `leading_loss` is rank or both. A campaign listed under `no_share_reported` has no such "
        "figure from Google: say so, never a zero. A campaign that runs across all of Google has no list of typed searches.")


def prompt_block(client_id: int) -> dict:
    """{} when nothing was read. English, internal: an agent turns it into the client's words."""
    value = conclusions(client_id)
    if not value or not value.get("connected"):
        return {}
    decisions = value.get("decisions") or {}

    def short(t):
        return {"id": t["id"], "typed": t["term"], "campaign": t["campaign"], "matched_our_phrase": t.get("matched") or "",
                "clicks": t["clicks"], "cost_ils": t["cost"], **({"results": t["conversions"]} if not decisions.get("gated") else {})}
    out = {"read_on": str(value.get("checked_at") or "")[:10], "window_days": WINDOW_DAYS, "rule": RULE}
    if "terms" in value:
        out["search_terms"] = {"count": value.get("terms_total", len(value["terms"])),
                               "most_expensive": [short(t) for t in value["terms"][:10]],
                               "exclude_candidates": [short(t) for t in decisions.get("exclude") or []],
                               "add_candidates": [short(t) for t in decisions.get("add") or []]}
        if decisions.get("gated"):
            out["search_terms"]["conclusions_withheld"] = f"the account is not measuring results ({decisions['gated']})"
    if "shares" in value:
        out["impression_share"] = [{"campaign": c["campaign"], "shown_share": c["share"], "lost_to_budget": c["lost_budget"],
                                    "lost_to_rank": c["lost_rank"], "leading_loss": c["dominant"]}
                                   for c in value["shares"] if c.get("available")]
        out["no_share_reported"] = [c["campaign"] for c in value["shares"] if not c.get("available")]
    return out


def candidates(client_id: int) -> dict:
    """{id: {"group": "exclude"|"add", ...term}} — what a decision may name."""
    decisions = (conclusions(client_id) or {}).get("decisions") or {}
    return {t["id"]: {**t, "group": group} for group in ("exclude", "add") for t in decisions.get(group) or []}


# ─── Words ───────────────────────────────────────────────────────────────────

T = {
    "none_read": _five("הנתון הזה עוד לא נקרא מהחשבון.", "This has not been read from the account yet.",
                       "Cette donnée n'a pas encore été lue dans le compte.", "لم تُقرأ هذه المعلومة من الحساب بعد.",
                       "Эти данные ещё не прочитаны из аккаунта."),
    "unreadable": _five("לא הצלחנו לקרוא את הנתון הזה מגוגל עכשיו. לא מסיקים ממנו דבר עד שייקרא.",
                        "We could not read this from Google just now. Nothing is concluded from it until it is read.",
                        "Nous n'avons pas pu lire cette donnée chez Google pour l'instant. Rien n'en est conclu tant qu'elle n'est pas lue.",
                        "لم نتمكن من قراءة هذه المعلومة من جوجل الآن. لا نستنتج منها شيئًا حتى تُقرأ.",
                        "Сейчас не удалось прочитать эти данные из Google. Пока они не прочитаны, выводов из них нет."),
    "checked": _five("נקרא מהחשבון ב-{date}, על 30 הימים שלפני כן.", "Read from the account on {date}, for the 30 days before it.",
                     "Lu dans le compte le {date}, pour les 30 jours précédents.", "قُرئ من الحساب في {date}، عن الثلاثين يومًا السابقة.",
                     "Прочитано из аккаунта {date}, за предыдущие 30 дней."),
    "st_empty": _five("אין עדיין מה להציג: ב-30 הימים האחרונים לא רצו מודעות בחיפוש של גוגל. הרשימה תתמלא אחרי שקמפיין חיפוש ירוץ.",
                      "Nothing to show yet: no ads ran in Google search in the last 30 days. The list fills once a search campaign has run.",
                      "Rien à afficher pour l'instant : aucune annonce n'a été diffusée dans la recherche Google ces 30 derniers jours. La liste se remplira après la diffusion d'une campagne de recherche.",
                      "لا شيء للعرض بعد: لم تُعرض إعلانات في بحث جوجل خلال آخر 30 يومًا. ستمتلئ القائمة بعد تشغيل حملة بحث.",
                      "Пока показывать нечего: за последние 30 дней объявления в поиске Google не шли. Список заполнится после работы поисковой кампании."),
    "st_gated": _five("החשבון עדיין לא מודד תוצאות. לכן מוצג כאן רק מה שחיפשו וכמה זה עלה, ואיננו מסיקים מזה מה להוציא ומה להוסיף.",
                      "The account is not measuring results yet. So only what was searched and what it cost is shown here, and we draw no conclusion about what to exclude or add.",
                      "Le compte ne mesure pas encore les résultats. Seuls ce qui a été recherché et son coût sont donc affichés, et nous n'en tirons aucune conclusion sur ce qu'il faut exclure ou ajouter.",
                      "الحساب لا يقيس النتائج بعد. لذلك يُعرض هنا فقط ما بُحث عنه وكم كلّف، ولا نستنتج منه ما يجب استبعاده أو إضافته.",
                      "Аккаунт пока не измеряет результаты. Поэтому здесь показано только то, что искали и сколько это стоило, и выводов о том, что исключить или добавить, мы не делаем."),
    "st_pmax": _five("לקמפיין שרץ בכל המקומות של גוגל אין רשימה כזו: גוגל מוסרת עליו רק קבוצות נושאים כלליות, לא את המילים עצמן, ואיננו מציגים אותן כאילו היו הרשימה הזו.",
                     "A campaign that runs across all of Google has no such list: Google gives only general topic groups for it, not the words themselves, and we do not show those as if they were this list.",
                     "Une campagne diffusée sur l'ensemble de Google n'a pas de liste de ce type : Google n'en fournit que des groupes de thèmes généraux, pas les mots eux-mêmes, et nous ne les présentons pas comme cette liste.",
                     "الحملة التي تظهر في كل أماكن جوجل ليست لها قائمة كهذه: جوجل تعطي عنها مجموعات مواضيع عامة فقط، لا الكلمات نفسها، ولا نعرضها كأنها هذه القائمة.",
                     "У кампании, которая идёт по всему Google, такого списка нет: Google даёт по ней только общие группы тем, а не сами слова, и мы не выдаём их за этот список."),
    "st_exclude": _five("מועמדים להוצאה: עלו כסף ולא הביאו תוצאה", "Candidates to exclude: cost money and produced nothing",
                        "Candidats à l'exclusion : ont coûté de l'argent sans rien produire", "مرشّحة للاستبعاد: كلّفت مالًا ولم تُنتج شيئًا",
                        "Кандидаты на исключение: стоили денег и ничего не дали"),
    "st_add": _five("מועמדים להוספה: הביאו תוצאה ועדיין לא ברשימת הביטויים שלנו", "Candidates to add: produced results and are not yet among our phrases",
                    "Candidats à l'ajout : ont produit des résultats et ne figurent pas encore parmi nos expressions",
                    "مرشّحة للإضافة: أنتجت نتائج وليست بعد ضمن عباراتنا", "Кандидаты на добавление: дали результат и пока не входят в наши фразы"),
    "st_exclude_why": _five("{clicks} קליקים ו-{cost} ב-30 יום, בלי אף תוצאה", "{clicks} clicks and {cost} in 30 days, with no result at all",
                            "{clicks} clics et {cost} en 30 jours, sans aucun résultat", "{clicks} نقرة و{cost} خلال 30 يومًا، بلا أي نتيجة",
                            "{clicks} кликов и {cost} за 30 дней — без единого результата"),
    "st_add_why": _five("תוצאות ב-30 יום: {n}, בעלות של {cost}", "results in 30 days: {n}, for {cost}", "résultats en 30 jours : {n}, pour {cost}",
                        "النتائج خلال 30 يومًا: {n}، بتكلفة {cost}", "результатов за 30 дней: {n}, при расходе {cost}"),
    "st_no_candidates": _five("אין כרגע מועמדים.", "No candidates right now.", "Aucun candidat pour l'instant.", "لا مرشّحات حاليًا.", "Сейчас кандидатов нет."),
    "st_decides": _five("אלה מועמדים, לא החלטות. יואב מחליט על כל אחד מהם כשהוא כותב את האסטרטגיה, וההחלטה והסיבה מופיעות ברשימת הביטויים שהוצאנו.",
                        "These are candidates, not decisions. Yoav decides each one when he writes the strategy, and the decision and its reason appear in the list of excluded phrases.",
                        "Ce sont des candidats, pas des décisions. Yoav tranche chacun en rédigeant la stratégie ; la décision et sa raison apparaissent dans la liste des expressions exclues.",
                        "هذه مرشّحات لا قرارات. يوآف يقرر في كل منها عند كتابة الاستراتيجية، ويظهر القرار وسببه في قائمة العبارات المستبعدة.",
                        "Это кандидаты, а не решения. Йоав решает по каждому, когда пишет стратегию; решение и причина появляются в списке исключённых фраз."),
    "st_table": _five("מה שעלה הכי הרבה", "What cost the most", "Ce qui a coûté le plus", "الأعلى تكلفة", "Что стоило дороже всего"),
    "c_typed": _five("מה חיפשו", "What was searched", "Ce qui a été recherché", "ما بُحث عنه", "Что искали"),
    "c_matched": _five("הביטוי שלנו שהתאים", "Our phrase that matched", "Notre expression correspondante", "عبارتنا المطابقة", "Наша совпавшая фраза"),
    "c_shown": _five("הוצגנו", "Times shown", "Affichages", "مرات الظهور", "Показы"),
    "c_clicks": _five("קליקים", "Clicks", "Clics", "نقرات", "Клики"),
    "c_cost": _five("עלות", "Cost", "Coût", "التكلفة", "Расход"),
    "c_status": _five("מצב", "Status", "État", "الحالة", "Статус"),
    "s_added": _five("כבר ברשימה שלנו", "already one of our phrases", "déjà parmi nos expressions", "ضمن عباراتنا بالفعل", "уже среди наших фраз"),
    "s_excluded": _five("כבר הוצא", "already excluded", "déjà exclu", "مستبعَد بالفعل", "уже исключено"),
    "x_in_account": _five("הוצא בחשבון: המודעות שלכם לא מוצגות למי שמחפש את זה.", "Excluded in the account: your ads are not shown to people searching for this.",
                          "Exclu dans le compte : vos annonces ne sont pas affichées aux personnes qui recherchent cela.",
                          "مستبعَد في الحساب: لا تُعرض إعلاناتكم لمن يبحث عن هذا.", "Исключено в аккаунте: тем, кто это ищет, ваши объявления не показываются."),
    "add_title": _five("ביטויים שהחלטנו להוסיף", "Phrases we decided to add", "Expressions que nous avons décidé d'ajouter",
                       "عبارات قررنا إضافتها", "Фразы, которые мы решили добавить"),
    "add_pending": _five("הוחלט להוסיף. השינוי בחשבון עוד לא בוצע.", "Decided: to be added. The change in the account has not been made yet.",
                         "Décidé : à ajouter. La modification dans le compte n'a pas encore été faite.",
                         "تقرر: ستُضاف. لم يُنفَّذ التغيير في الحساب بعد.", "Решено добавить. Изменение в аккаунте ещё не внесено."),
    "add_done": _five("נוסף בחשבון.", "Added in the account.", "Ajouté dans le compte.", "أُضيف في الحساب.", "Добавлено в аккаунте."),
    "is_empty": _five("אין עדיין מה להציג: אף קמפיין לא הוצג ב-30 הימים האחרונים. הנתון יופיע אחרי שקמפיין ירוץ.",
                      "Nothing to show yet: no campaign was shown in the last 30 days. The figure appears once a campaign has run.",
                      "Rien à afficher pour l'instant : aucune campagne n'a été diffusée ces 30 derniers jours. Le chiffre apparaîtra après la diffusion d'une campagne.",
                      "لا شيء للعرض بعد: لم تُعرض أي حملة خلال آخر 30 يومًا. سيظهر الرقم بعد تشغيل حملة.",
                      "Пока показывать нечего: за последние 30 дней ни одна кампания не показывалась. Показатель появится после работы кампании."),
    "is_shown": _five("הופענו ב-{p} מהחיפושים המתאימים", "We appeared in {p} of the relevant searches", "Nous sommes apparus dans {p} des recherches pertinentes",
                      "ظهرنا في {p} من عمليات البحث المناسبة", "Мы показались в {p} подходящих поисков"),
    "is_top": _five("מתוך ההופעות שלנו, מעל התוצאות הרגילות", "Of our showings, above the regular results", "Parmi nos affichages, au-dessus des résultats habituels",
                    "من مرات ظهورنا، فوق النتائج العادية", "Из наших показов — над обычными результатами"),
    "is_first": _five("מתוך ההופעות שלנו, במקום הראשון", "Of our showings, in first place", "Parmi nos affichages, en première place",
                      "من مرات ظهورنا، في المركز الأول", "Из наших показов — на первом месте"),
    "is_low": _five("פחות מ-10%", "under 10%", "moins de 10 %", "أقل من 10%", "меньше 10%"),
    "is_high": _five("יותר מ-90%", "over 90%", "plus de 90 %", "أكثر من 90%", "больше 90%"),
    "is_unavailable": _five("לסוג הקמפיין הזה גוגל לא מוסרת את הנתון הזה, ולכן אין כאן מספר.", "Google does not report this figure for this kind of campaign, so there is no number here.",
                            "Google ne communique pas ce chiffre pour ce type de campagne ; il n'y a donc pas de nombre ici.",
                            "جوجل لا تعطي هذا الرقم لهذا النوع من الحملات، لذلك لا يوجد رقم هنا.", "Для этого вида кампаний Google не сообщает этот показатель, поэтому числа здесь нет."),
    "act_budget": _five("רוב מה שהפסדנו הוא בגלל תקציב: הקמפיין קטן מהביקוש, ותקציב גדול יותר יביא יותר הופעות.",
                        "Most of what we lost was lost to budget: the campaign is smaller than the demand, and a larger budget would bring more showings.",
                        "L'essentiel de ce que nous avons perdu l'a été à cause du budget : la campagne est plus petite que la demande, et un budget plus élevé apporterait plus d'affichages.",
                        "معظم ما خسرناه كان بسبب الميزانية: الحملة أصغر من الطلب، وميزانية أكبر ستجلب ظهورًا أكثر.",
                        "Большую часть мы потеряли из-за бюджета: кампания меньше спроса, и больший бюджет даст больше показов."),
    "act_rank": _five("רוב מה שהפסדנו הוא בגלל דירוג: המודעות והביטויים צריכים להתאים טוב יותר למה שמחפשים. תוספת תקציב לא תפתור את זה, היא רק תוציא את הכסף מהר יותר.",
                      "Most of what we lost was lost to ranking: the ads and phrases need to fit what people search for better. More budget would not fix it, it would only spend the money faster.",
                      "L'essentiel de ce que nous avons perdu l'a été à cause du classement : les annonces et les expressions doivent mieux correspondre à ce qui est recherché. Plus de budget n'y changerait rien, l'argent partirait seulement plus vite.",
                      "معظم ما خسرناه كان بسبب الترتيب: يجب أن تتوافق الإعلانات والعبارات أكثر مع ما يُبحث عنه. زيادة الميزانية لن تحلّ ذلك، بل ستُنفق المال أسرع فقط.",
                      "Большую часть мы потеряли из-за рейтинга: объявления и фразы должны лучше соответствовать тому, что ищут. Больший бюджет этого не исправит, деньги просто уйдут быстрее."),
    "act_both": _five("הפסדנו גם בגלל תקציב וגם בגלל דירוג, בערך באותה מידה. קודם מתקנים את הדירוג, ורק אחר כך שוקלים תקציב.",
                      "We lost to budget and to ranking in roughly equal measure. The ranking is fixed first, and only then is budget considered.",
                      "Nous avons perdu à cause du budget et du classement dans des proportions voisines. On corrige d'abord le classement, puis seulement on examine le budget.",
                      "خسرنا بسبب الميزانية وبسبب الترتيب بالقدر نفسه تقريبًا. نصلح الترتيب أولًا، ثم ننظر في الميزانية.",
                      "Мы теряли и из-за бюджета, и из-за рейтинга примерно поровну. Сначала исправляется рейтинг, и только потом рассматривается бюджет."),
    "act_none": _five("כמעט לא הפסדנו הופעות. אין כאן מה לשנות.", "We lost almost no showings. There is nothing to change here.",
                      "Nous n'avons presque pas perdu d'affichages. Il n'y a rien à changer ici.", "لم نخسر ظهورًا يُذكر. لا شيء يستدعي التغيير هنا.",
                      "Показов мы почти не теряли. Менять здесь нечего."),
    "act_gated": _five("החשבון עדיין לא מודד תוצאות, ולכן לא נמליץ על תוספת תקציב לפני שנדע מה ההופעות האלה מביאות.",
                       "The account is not measuring results yet, so we will not recommend more budget before we know what these showings bring.",
                       "Le compte ne mesure pas encore les résultats ; nous ne recommanderons donc pas de budget supplémentaire avant de savoir ce que ces affichages apportent.",
                       "الحساب لا يقيس النتائج بعد، لذلك لن نوصي بزيادة الميزانية قبل أن نعرف ما الذي يجلبه هذا الظهور.",
                       "Аккаунт пока не измеряет результаты, поэтому мы не станем рекомендовать больший бюджет, пока не узнаем, что дают эти показы."),
}


def t(key: str, lang: str, **values) -> str:
    text = T[key].get(lang) or T[key]["en"]
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def percent(value, lang: str) -> str:
    if value is None:
        return ""
    if abs(value - LOW_MARK) < 1e-4:
        return t("is_low", lang)
    if abs(value - HIGH_MARK) < 1e-4:
        return t("is_high", lang)
    return f"{round(value * 100)}%"


def action_line(item: dict, measuring: str, lang: str) -> str:
    """The action attached to whichever loss leads. More budget is recommended
    ONLY for a budget-led loss in an account that measures results."""
    lead = item.get("dominant")
    if lead == "budget" and measuring != "measuring":
        return t("act_gated", lang)
    return t({"budget": "act_budget", "rank": "act_rank", "both": "act_both"}.get(lead, "act_none"), lang)


def _words(key: str, lang: str, form: str = "short") -> str:
    from core import client_speech
    return client_speech.words(key, lang, form)


def _money(value, lang: str) -> str:
    from core import client_language
    return client_language.fmt_money(round(float(value)), lang)


def _checked(value: dict, lang: str) -> str:
    from core import client_language
    return t("checked", lang, date=client_language.fmt_date(value.get("checked_at"), lang) or str(value.get("checked_at") or "")[:10])


def share_rows(item: dict, lang: str) -> list:
    """The split, as the client reads it. Never one number alone."""
    rows = [{"label": t("is_shown", lang, p=percent(item["share"], lang)), "value": ""},
            {"label": _words("lost_budget", lang), "value": percent(item["lost_budget"], lang)},
            {"label": _words("lost_rank", lang), "value": percent(item["lost_rank"], lang)}]
    if item.get("top") is not None:
        rows.append({"label": t("is_top", lang), "value": percent(item["top"], lang)})
    if item.get("absolute_top") is not None:
        rows.append({"label": t("is_first", lang), "value": percent(item["absolute_top"], lang)})
    return rows


def view(client_id: int, lang: str = "he", value: dict = None, campaign_types: dict = None) -> dict:
    """{"terms": {...}, "share": {...}, "excluded_in_account": [...]} for the
    Google page. {} when the account is not connected. `campaign_types`:
    {campaign id: our type} to name a campaign by where it appears."""
    lang = lang if lang in LANGS else "he"
    value = conclusions(client_id, value if value is not None else for_page(client_id))
    if not value or not value.get("connected"):
        return {}
    from core import client_speech
    measuring = value.get("measurement") or "unknown"

    def named(item):
        return client_speech.plain(item.get("campaign_name") or "", lang)[:120]
    terms_out = {"title": _words("search_terms", lang), "long": _words("search_terms", lang, "long"), "checked": _checked(value, lang),
                 "notes": [], "groups": [], "table": None, "empty": ""}
    has_pmax = any(c.get("channel") == "PERFORMANCE_MAX" for c in value.get("shares") or [])
    if "terms" not in value:
        terms_out["empty"] = t("unreadable" if value.get("terms_error") else "none_read", lang)
    elif not value["terms"]:
        terms_out["empty"] = t("st_empty", lang)
    else:
        decisions = value.get("decisions") or {}
        if decisions.get("gated"):
            terms_out["notes"].append(t("st_gated", lang))
        else:
            for key, why in (("exclude", lambda x: t("st_exclude_why", lang, clicks=x["clicks"], cost=_money(x["cost"], lang))),
                             ("add", lambda x: t("st_add_why", lang, n=f"{x['conversions']:g}", cost=_money(x["cost"], lang)))):
                rows = [{"text": x["term"], "why": why(x), "matched": x.get("matched") or ""} for x in decisions.get(key) or []]
                terms_out["groups"].append({"kind": key, "title": t("st_" + key, lang), "rows": rows,
                                            "empty": "" if rows else t("st_no_candidates", lang)})
            terms_out["notes"].append(t("st_decides", lang))
        status = {"added": t("s_added", lang), "excluded": t("s_excluded", lang), "none": ""}
        terms_out["table"] = {"title": t("st_table", lang),
                              "columns": [t(k, lang) for k in ("c_typed", "c_matched", "c_shown", "c_clicks", "c_cost", "c_status")],
                              "rows": [[x["term"], x.get("matched") or "", str(x["impressions"]), str(x["clicks"]), _money(x["cost"], lang),
                                        status.get(x["status"], "")] for x in value["terms"][:SHOWN_TERMS]]}
    if has_pmax:
        terms_out["notes"].append(t("st_pmax", lang))
    share_out = {"title": _words("impression_share", lang), "long": _words("impression_share", lang, "long"),
                 "checked": _checked(value, lang), "campaigns": [], "empty": ""}
    if "shares" not in value:
        share_out["empty"] = t("unreadable" if value.get("shares_error") else "none_read", lang)
    elif not value["shares"]:
        share_out["empty"] = t("is_empty", lang)
    for item in value.get("shares") or []:
        kind = (campaign_types or {}).get(item["campaign"]) or {"SEARCH": "search", "PERFORMANCE_MAX": "pmax", "DEMAND_GEN": "demand_gen",
                                                                "DISPLAY": "display", "VIDEO": "youtube"}.get(item["channel"], "")
        title = (client_speech.words(kind, lang) if kind else "") or named(item)
        if item.get("available"):
            share_out["campaigns"].append({"title": title, "name": named(item), "rows": share_rows(item, lang),
                                           "action": action_line(item, measuring, lang), "lead": item["dominant"], "note": ""})
        else:
            share_out["campaigns"].append({"title": title, "name": named(item), "rows": [], "action": "", "lead": "",
                                           "note": t("is_unavailable", lang)})
    excluded = [{"text": x["term"], "why": t("x_in_account", lang), "applied": True}
                for x in value.get("terms") or [] if x["status"] == "excluded"]
    return {"terms": terms_out, "share": share_out, "excluded_in_account": excluded}


def snapshot(client_id: int) -> dict:
    """What a monthly report keeps: the split per campaign and the decision
    groups, as they stood. {} when nothing was read."""
    value = conclusions(client_id)
    if not value or not value.get("connected"):
        return {}
    decisions = value.get("decisions") or {}
    return {"checked_at": value.get("checked_at"), "measurement": value.get("measurement"),
            "shares": value.get("shares"), "terms_total": value.get("terms_total"),
            "gated": decisions.get("gated", ""), "exclude": decisions.get("exclude") or [], "add": decisions.get("add") or []}


def report_rows(kept: dict, lang: str) -> list:
    """[[label, value]] for the monthly report: the SPLIT with its action per
    campaign, then the decision groups (or why there are none)."""
    if not kept:
        return []
    from core import client_speech
    rows = [[_words("impression_share", lang, "long"), ""]]
    shares_kept = kept.get("shares")
    if not shares_kept:
        rows.append([t("is_empty" if shares_kept == [] else "none_read", lang), ""])
    for item in shares_kept or []:
        name = client_speech.plain(item.get("campaign_name") or "", lang)[:120]
        if not item.get("available"):
            rows.append([name, t("is_unavailable", lang)])
            continue
        rows += [[f"{name} · {r['label']}".strip(" ·"), r["value"]] for r in share_rows(item, lang)]
        rows.append([action_line(item, kept.get("measurement") or "unknown", lang), ""])
    rows.append([_words("search_terms", lang, "long"), ""])
    if kept.get("terms_total") is None:
        rows.append([t("none_read", lang), ""])
    elif not kept["terms_total"]:
        rows.append([t("st_empty", lang), ""])
    elif kept.get("gated"):
        rows.append([t("st_gated", lang), ""])
    else:
        for key in ("exclude", "add"):
            rows.append([t("st_" + key, lang), "" if kept.get(key) else t("st_no_candidates", lang)])
            for x in kept.get(key) or []:
                rows.append([x["term"], t("st_exclude_why", lang, clicks=x["clicks"], cost=_money(x["cost"], lang)) if key == "exclude"
                             else t("st_add_why", lang, n=f"{x['conversions']:g}", cost=_money(x["cost"], lang))])
    return rows
