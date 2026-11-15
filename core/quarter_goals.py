"""The client's agreed 3-month goals and their progress path on home
(docs/HOME-FOCUS.md §4-5).

- AGREEMENT: after the deep characterisation, the manager proposes ONE card
  (`agree_quarter_goals`, core/quarter_goals_actions) with three tracks
  prefilled from what we know: a steady social presence run by מאיה (posts a
  week), organic growth focused ONLY on the keywords agreed here, and what
  must improve to reach the goals. The client's click stores it
  (app_settings `quarter_goals:<id>`); a new card replaces it.
- TRACK: `track()` turns it into a simple path — goal, where it stands now,
  the next step — from data that already exists. Every "now" says whether it
  was MEASURED or is not measured yet; nothing is estimated silently and
  nothing is promised.
"""
import re
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "quarter_goals"
PREFIX = "quarter_goals:"
LANGS = ("he", "en", "fr", "ar", "ru")
MAX_KEYWORDS = 8
CYCLE_DAYS = 90


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db():
    from agents.client_agent import _db as db
    return db()


# ─── Storage ─────────────────────────────────────────────────────────────────

def agreed(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}")
            .limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) and value.get("agreed_at") else {}


def clean_keywords(value) -> list:
    """Comma / newline / list → at most MAX_KEYWORDS short, distinct phrases."""
    parts = value if isinstance(value, list) else re.split(r"[,\n;،]+", str(value or ""))
    out, seen = [], set()
    for part in parts:
        text = " ".join(str(part).split())[:60].strip()
        if len(text) >= 2 and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out[:MAX_KEYWORDS]


def save(client_id: int, keywords: list, posts_per_week: int, improve: str = "") -> dict:
    value = {"agreed_at": _now().isoformat(), "keywords": clean_keywords(keywords),
             "posts_per_week": max(1, min(7, int(posts_per_week))), "improve": str(improve or "")[:300]}
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": value,
                                        "updated_at": _now().isoformat()}, on_conflict="key").execute()
    return value


def suggested_keywords(client_id: int) -> list:
    """The search phrases the client gave in the sales chat — the starting point
    the manager proposes from (never invented here)."""
    try:
        from core.lead_tracking import client_sales_lead
        lead, _how = client_sales_lead(client_id, columns="answers")
        return clean_keywords((lead.get("answers") or {}).get("organic_target_phrases") or "")
    except Exception:
        return []


# ─── Measured reads (each a seam the tests replace) ──────────────────────────

def _count_activity(client_id: int, action_types: tuple, days: int) -> int:
    since = (_now() - timedelta(days=days)).isoformat()
    rows = (_db().table("client_activity").select("id").eq("client_id", client_id)
            .in_("action_type", list(action_types)).gte("created_at", since).limit(500).execute().data or [])
    return len(rows)


def _count_leads(client_id: int, start_days: int, end_days: int) -> int:
    start = (_now() - timedelta(days=start_days)).isoformat()
    end = (_now() - timedelta(days=end_days)).isoformat()
    rows = (_db().table("client_leads").select("id").eq("client_id", client_id)
            .gte("created_at", start).lt("created_at", end).limit(5000).execute().data or [])
    return len(rows)


def _positions(client_id: int, keywords: list) -> dict:
    """keyword → measured average position, only where a tracking source
    really returned one ({} = not measured)."""
    from core import rank_tracking
    table = rank_tracking.organic_table(client_id)
    if not table.get("tracking_connected"):
        return {}
    wanted = {k.lower() for k in keywords}
    return {r["keyword"]: r["current_position"] for r in table.get("rows") or []
            if r.get("current_position") is not None and str(r.get("keyword", "")).lower() in wanted}


def _site_connected(client_id: int) -> bool:
    rows = (_db().table("client_accounts").select("platform").eq("client_id", client_id)
            .eq("platform", "wordpress").eq("status", "active").limit(1).execute().data or [])
    return bool(rows)


# ─── Words ───────────────────────────────────────────────────────────────────

T = {
    "title": _five("היעדים שלנו ל-3 החודשים הקרובים", "Our goals for the next 3 months", "Nos objectifs pour les 3 prochains mois",
                   "أهدافنا للأشهر الثلاثة القادمة", "Наши цели на ближайшие 3 месяца"),
    "month": _five("חודש {m} מתוך 3", "Month {m} of 3", "Mois {m} sur 3", "الشهر {m} من 3", "Месяц {m} из 3"),
    "honest": _five("אלה יעדים משותפים, לא הבטחה לתוצאה. כל ״איפה זה עומד״ מסומן אם נמדד או עוד לא.",
                    "These are shared goals, not a promise of results. Every \"where it stands\" says whether it was measured or not yet.",
                    "Ce sont des objectifs communs, pas une promesse de résultat. Chaque « où on en est » indique s'il est mesuré ou pas encore.",
                    "هذه أهداف مشتركة وليست وعدًا بنتائج. كل \"أين نقف\" يوضح إن كان مقاسًا أم ليس بعد.",
                    "Это общие цели, а не обещание результата. Каждое «где мы сейчас» отмечено: измерено или пока нет."),
    "social_goal": _five("נוכחות קבועה ברשתות: {n} פריטים בשבוע, בניהול מאיה",
                         "A steady social presence: {n} items a week, run by Maya",
                         "Une présence régulière sur les réseaux : {n} contenus par semaine, gérés par Maya",
                         "حضور ثابت على الشبكات: {n} عناصر أسبوعيًا بإدارة مايا",
                         "Стабильное присутствие в соцсетях: {n} материалов в неделю, ведёт Майя"),
    "social_now": _five("נמדד: {n} פריטים הוכנו ב-7 הימים האחרונים",
                        "Measured: items prepared in the last 7 days: {n}",
                        "Mesuré : {n} contenus préparés ces 7 derniers jours",
                        "مقاس: {n} عناصر جُهّزت خلال آخر 7 أيام",
                        "Измерено: за последние 7 дней подготовлено материалов — {n}"),
    "social_next_approve": _five("לאשר את התוכנית של השבוע שמחכה אצל מאיה", "Approve this week's plan waiting with Maya",
                                 "Valider le plan de la semaine qui attend chez Maya", "الموافقة على خطة الأسبوع التي تنتظر لدى مايا",
                                 "Одобрить план недели, который ждёт у Майи"),
    "social_next_running": _five("הפריטים של השבוע בהפקה; כל אחד מתפרסם בלחיצה אחת שלכם ביום שלו",
                                 "This week's items are in production; each goes out with one tap of yours on its day",
                                 "Les contenus de la semaine sont en production ; chacun part d'un clic de votre part le jour prévu",
                                 "عناصر الأسبوع قيد الإنتاج؛ ينشر كل منها بنقرة منكم في يومه",
                                 "Материалы недели готовятся; каждый выходит одним вашим нажатием в свой день"),
    "social_next_ask": _five("לבקש ממאיה את התוכנית של השבוע", "Ask Maya for this week's plan",
                             "Demander à Maya le plan de la semaine", "اطلبوا من مايا خطة الأسبوع",
                             "Попросить у Майи план на неделю"),
    "organic_goal": _five("קידום אורגני שמשתפר כל חודש, רק על הביטויים שסיכמנו: {k}",
                          "Organic growth every month, focused only on the phrases we agreed: {k}",
                          "Une progression naturelle chaque mois, uniquement sur les expressions convenues : {k}",
                          "تحسّن عضوي كل شهر يركّز فقط على العبارات المتفق عليها: {k}",
                          "Органический рост каждый месяц — только по согласованным фразам: {k}"),
    "organic_now_pos": _five("נמדד: ל-{n} מתוך {t} ביטויים יש כבר מיקום בגוגל",
                             "Measured: {n} of {t} phrases already have a position on Google",
                             "Mesuré : {n} expressions sur {t} ont déjà une position sur Google",
                             "مقاس: {n} من أصل {t} عبارات لها موقع على Google بالفعل",
                             "Измерено: у {n} из {t} фраз уже есть позиция в Google"),
    "organic_now_none": _five("המיקומים עוד לא נמדדים; ב-30 הימים האחרונים נכתבו {a} מאמרים",
                              "Positions are not measured yet; articles written in the last 30 days: {a}",
                              "Les positions ne sont pas encore mesurées ; {a} articles rédigés ces 30 derniers jours",
                              "المواقع لم تُقس بعد؛ كُتبت {a} مقالات خلال آخر 30 يومًا",
                              "Позиции пока не измеряются; за 30 дней написано статей — {a}"),
    "organic_next_site": _five("לחבר את האתר, כדי שאורי יוכל להתחיל", "Connect the website so Uri can start",
                               "Connecter le site pour qu'Uri puisse commencer", "ربط الموقع ليبدأ أوري",
                               "Подключить сайт, чтобы Ури мог начать"),
    "organic_next_work": _five("לאשר את המאמר הבא של אורי על אחד הביטויים", "Approve Uri's next article on one of the phrases",
                               "Valider le prochain article d'Uri sur l'une des expressions",
                               "الموافقة على مقال أوري التالي حول إحدى العبارات",
                               "Одобрить следующую статью Ури по одной из фраз"),
    "improve_goal": _five("מה צריך להשתפר: {x}", "What has to improve: {x}", "Ce qui doit progresser : {x}",
                          "ما يجب أن يتحسن: {x}", "Что должно улучшиться: {x}"),
    "improve_default": _five("יותר פניות מלקוחות, חודש אחרי חודש", "More enquiries from customers, month after month",
                             "Plus de demandes de clients, mois après mois", "مزيد من استفسارات العملاء شهرًا بعد شهر",
                             "Больше обращений клиентов месяц за месяцем"),
    "improve_now": _five("נמדד: {a} פניות ב-30 הימים האחרונים, לעומת {b} ב-30 שלפניהם",
                         "Measured: enquiries in the last 30 days: {a} (the 30 days before: {b})",
                         "Mesuré : {a} demandes ces 30 derniers jours, contre {b} les 30 précédents",
                         "مقاس: {a} استفسارات خلال آخر 30 يومًا مقابل {b} في الثلاثين التي قبلها",
                         "Измерено: {a} обращений за последние 30 дней против {b} за предыдущие 30"),
    "improve_next": _five("לענות לכל פנייה חדשה מהר; הצוות ממשיך לחזק את הערוצים שמביאים אותן",
                          "Answer every new enquiry quickly; the team keeps strengthening the channels that bring them",
                          "Répondre vite à chaque nouvelle demande ; l'équipe renforce les canaux qui les apportent",
                          "الرد بسرعة على كل استفسار جديد؛ يواصل الفريق تقوية القنوات التي تجلبها",
                          "Быстро отвечать на каждое обращение; команда усиливает каналы, которые их приносят"),
    "not_measured": _five("עוד לא נמדד", "Not measured yet", "Pas encore mesuré", "لم يُقس بعد", "Пока не измерено"),
}


def _t(key: str, lang: str, **values) -> str:
    text = pick(T[key], lang)
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v))
    return text


# ─── The path on home ────────────────────────────────────────────────────────

def _social(client_id: int, value: dict, lang: str) -> dict:
    out = {"key": "social", "persona": "meta", "goal": _t("social_goal", lang, n=value["posts_per_week"])}
    try:
        n = _count_activity(client_id, ("media_image_created", "media_video_created"), 7)
        out.update(now=_t("social_now", lang, n=n), measured=True)
    except Exception:
        out.update(now=_t("not_measured", lang), measured=False)
    try:
        from core import content_team
        status = content_team.current(client_id).get("status")
    except Exception:
        status = None
    out["next"] = _t({"proposed": "social_next_approve", "approved": "social_next_running"}.get(status, "social_next_ask"), lang)
    return out


def _organic(client_id: int, value: dict, lang: str) -> dict:
    words = value.get("keywords") or []
    out = {"key": "organic", "persona": "website",
           "goal": _t("organic_goal", lang, k=", ".join(words) if words else "—")}
    try:
        positions = _positions(client_id, words) if words else {}
    except Exception:
        positions = {}
    if positions:
        out.update(now=_t("organic_now_pos", lang, n=len(positions), t=len(words)), measured=True)
    else:
        try:
            articles = _count_activity(client_id, ("seo_article_generated",), 30)
            out.update(now=_t("organic_now_none", lang, a=articles), measured=False)
        except Exception:
            out.update(now=_t("not_measured", lang), measured=False)
    try:
        site = _site_connected(client_id)
    except Exception:
        site = True   # unknown: no claim that it is missing
    out["next"] = _t("organic_next_work" if site else "organic_next_site", lang)
    return out


def _improve(client_id: int, value: dict, lang: str) -> dict:
    out = {"key": "improve", "persona": "general",
           "goal": _t("improve_goal", lang, x=value.get("improve") or pick(T["improve_default"], lang))}
    try:
        a, b = _count_leads(client_id, 30, 0), _count_leads(client_id, 60, 30)
        out.update(now=_t("improve_now", lang, a=a, b=b), measured=True)
    except Exception:
        out.update(now=_t("not_measured", lang), measured=False)
    out["next"] = _t("improve_next", lang)
    return out


def track(client_id: int, lang: str = "he") -> dict:
    """{} until goals are agreed. Otherwise the title, the month, the honesty
    line and three steps {key, persona, goal, now, measured, next}."""
    lang = lang if lang in LANGS else "he"
    value = agreed(client_id)
    if not value:
        return {}
    try:
        days = (_now() - datetime.fromisoformat(str(value["agreed_at"]).replace("Z", "+00:00"))).days
    except (TypeError, ValueError):
        days = 0
    month = max(1, min(3, days // 30 + 1))
    return {"agreed": True, "title": pick(T["title"], lang), "month": _t("month", lang, m=month),
            "honest": pick(T["honest"], lang),
            "steps": [_social(client_id, value, lang), _organic(client_id, value, lang), _improve(client_id, value, lang)]}


_block_cache = {}


def prompt_block(client_id: int) -> dict:
    """`quarter_goals` for the agents: the agreed keywords and posts a week
    (the organic work focuses ONLY on these keywords)."""
    import time
    hit = _block_cache.get(int(client_id))
    if hit and hit[0] > time.time() - 60:
        return hit[1]
    try:
        value = agreed(client_id)
        out = ({"agreed_on": value["agreed_at"][:10], "keywords": value.get("keywords") or [],
                "posts_per_week": value.get("posts_per_week"), "improve": value.get("improve", "")}
               if value else {"agreed": False, "suggested_keywords": suggested_keywords(client_id)})
    except Exception:
        return {}
    _block_cache[int(client_id)] = (time.time(), out)
    return out
