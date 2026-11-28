"""The client's agreed 3-month goals and their progress path on home
(docs/HOME-FOCUS.md §4-5).

- AGREEMENT: after the deep characterisation, the manager proposes ONE card
  (`agree_quarter_goals`, core/quarter_goals_actions) with three tracks
  prefilled from what we know: a steady social presence run by מאיה (posts a
  week), organic growth focused ONLY on the keywords agreed here, and what
  must improve to reach the goals. The client's click stores it
  (app_settings `quarter_goals:<id>`); a new card replaces it.
- BASELINE (2026-11-16, docs/ORGANIC-PLAN.md): the click that agrees the goals
  starts the 3-month clock AND captures a dated baseline — leads, store sales,
  site visitors and the agreed keywords' positions, each MEASURED or marked
  not measured. Every later "now" and the monthly report compare against it,
  on the agreed keywords and metric ONLY (the management-fee commitment is
  measured on exactly these).
- TRACK: `track()` turns it into a simple path — goal, where it stands now,
  the next step — from data that already exists. Every "now" says whether it
  was MEASURED or is not measured yet; nothing is estimated silently.
- NUMERIC TARGETS (2026-11-19, terms §6): every goal the refund commitment is
  measured on is a NUMBER (`targets`: enquiries in 30 days, positions gained on
  the agreed phrases, items a week). A yes/no goal ("page one") is refused at
  the card; it is expressed as positions to gain. `goal_met` is THE rule:
  a target counts as met at MET_RATIO (90%) of it or more. The client sees the
  approved target, the 90% threshold, the dated baseline and the current
  figure (home + the monthly report) — so a refund claim is settled from this
  record. The internal aim and its BUFFER (15%) are stored apart
  (`internal`) and never reach the client or an agent.
"""
import math
import re
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "quarter_goals"
PREFIX = "quarter_goals:"
LANGS = ("he", "en", "fr", "ar", "ru")
MAX_KEYWORDS = 8
CYCLE_DAYS = 90

# Terms §6.3: 90% or more of an approved numeric target counts as met.
MET_RATIO = 0.9
# INTERNAL ONLY: the committed target is the internal aim less this margin.
# Never in client text, an agent's data or the terms.
BUFFER = 0.15
# metric -> where its "now" comes from. All "more is better".
METRICS = ("leads_30d", "sales_30d", "positions_gained", "posts_per_week")
# The RESULT metrics counted in whole results: enquiries, or paid orders for a client who counts sales.
COUNTED = ("leads_30d", "sales_30d")
# Result metrics carry the buffer; posts_per_week is our own output (the
# agreed number IS the target).
BUFFERED = ("leads_30d", "sales_30d", "positions_gained")


def committed_target(metric: str, aim) -> float:
    """The target the client approves, from the internal aim."""
    aim = float(aim)
    if aim <= 0:
        raise ValueError("a target must be above zero")
    if metric not in BUFFERED:
        return round(aim, 1) if aim != int(aim) else int(aim)
    value = aim / (1 + BUFFER)
    return max(1, math.floor(value)) if metric in COUNTED else max(1.0, round(value, 1))


def threshold(target) -> float:
    """The figure that counts as meeting `target` (terms §6.3)."""
    return round(float(target) * MET_RATIO, 2)


def goal_met(current, target):
    """THE rule: met when current >= 90% of the target. None = not measured."""
    if current is None or target in (None, "", 0):
        return None
    return float(current) + 1e-9 >= float(target) * MET_RATIO


def num(value) -> str:
    if value is None:
        return ""
    value = float(value)
    return str(int(value)) if value == int(value) else f"{value:.1f}".rstrip("0").rstrip(".")


# THE wording of the money commitment for every prompt that may speak of it
# (sales chat, client chats). It says exactly terms §6, never more.
COMMITMENT_RULE = (
    "OUR ONLY RESULTS COMMITMENT (terms section 6, /terms/#s6): after the client approves numeric 3-month "
    "targets (dated baseline recorded that day), if at the end of the 3 months they were not met, the "
    "MANAGEMENT FEES charged in those 3 months are refunded. 90% or more of a numeric target counts as met. "
    "Only the approved targets, phrases and metrics count. Never refunded: the setup fee, ad budgets paid to "
    "the platforms, production costs, third-party purchases, other one-time payments. It does not apply if "
    "proposed work needed for the goals was not approved, accounts/permissions were not connected or were "
    "disconnected, the site or campaigns were changed outside us, campaigns were paused or their budget "
    "cut, or requested materials were not supplied in reasonable time. The service can be stopped any time; "
    "no charge for later months. Never promise more than this (no 'no fee at all', no refund of other "
    "payments, no guaranteed ranking or sales), and never deny it. Never mention any internal margin.")


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


def save(client_id: int, keywords: list, posts_per_week: int, improve: str = "", plan: dict = None,
         aims: dict = None, bases: dict = None, learning: dict = None) -> dict:
    """`aims` = {metric: internal aim}; the stored `targets` are the COMMITTED
    numbers (what the client approved), the aims stay in `internal`.
    `bases` = {metric: what the number rests on} (core/target_inputs);
    `learning` = the learning period the client approved IN PLACE of a result
    target, when the baseline was too thin to carry one."""
    words = clean_keywords(keywords)
    posts = max(1, min(7, int(posts_per_week)))
    baseline = capture_baseline(client_id, words)
    value = {"agreed_at": _now().isoformat(), "keywords": words,
             "posts_per_week": posts, "improve": str(improve or "")[:300],
             "baseline": baseline, "reported_months": []}
    value["targets"] = build_targets(words, posts, aims or {}, baseline, bases)
    if learning:
        value["learning"] = learning
    value["internal"] = {m: float(a) for m, a in (aims or {}).items() if m in BUFFERED and a}
    if plan:
        value["plan"] = {k: v for k, v in plan.items() if k != "internal_months"}
        if plan.get("internal_months"):
            value["internal"]["months"] = plan["internal_months"]
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


def _sales_30(client_id: int):
    """Net store revenue of the last 30 days, or None when no store / unreadable."""
    from agents import store_agent
    out = store_agent.sales_summary(client_id, "last_30_days")
    if not out.get("connected"):
        return None
    return {"net_revenue": out.get("net_revenue"), "paid_orders": out.get("paid_orders"),
            "currency": out.get("currency", ""), "complete": bool(out.get("complete"))}


def _visitors_30(client_id: int):
    from core import site_traffic
    view = site_traffic.for_agent(site_traffic.traffic(client_id))
    if not view.get("has_data"):
        return None
    return (view.get("windows") or {}).get("last_30_days", {}).get("visitors")


def build_targets(words: list, posts: int, aims: dict, baseline: dict = None, bases: dict = None) -> list:
    """The numeric targets the client approves: [{metric, target, baseline,
    basis}]. Every number carries what it rests on (§9 point 4): our own
    output and the organic plan have a fixed basis; a RESULT target (enquiries)
    exists only with the history basis the caller built — with none, there is
    no such target (ValueError), never a bare number."""
    baseline, bases = baseline or {}, bases or {}
    out = [{"metric": "posts_per_week", "target": committed_target("posts_per_week", posts),
            "baseline": baseline.get("posts_week"), "basis": {"kind": "own_output"}}]
    for metric in COUNTED:
        if not aims.get(metric):
            continue
        basis = bases.get(metric) or {}
        if basis.get("kind") != "history":
            raise ValueError("a result target with nothing it rests on")
        start = baseline.get(metric)
        if isinstance(start, dict):                 # the store's 30 days: the target counts its paid orders
            start = start.get("paid_orders")
        out.append({"metric": metric, "target": committed_target(metric, aims[metric]), "baseline": start, "basis": basis})
    if words and aims.get("positions_gained"):
        out.append({"metric": "positions_gained", "target": committed_target("positions_gained", aims["positions_gained"]),
                    "baseline": 0, "basis": {"kind": "plan"}})
    return out


def _client_lane_pieces(client_id: int, days: int) -> int:
    from core import governance
    return governance.client_lane_media_count(client_id, days)


def _posts_week(client_id: int):
    """Our own output a week — the SYSTEM lane only (knowledge/client-governance.md
    §2): pieces the client asked for beyond the plan are not counted toward the
    system's target. The lane cannot be read = not measured (None), never a
    blended number."""
    n = _count_activity(client_id, ("media_image_created", "media_video_created"), 28)
    n = max(0, n - _client_lane_pieces(client_id, 28))
    return round(n / 4, 1)


def _positions_gained(client_id: int, value: dict):
    """Average positions gained on the agreed phrases since the baseline, on
    the phrases measured BOTH times (None = none of them was)."""
    words = value.get("keywords") or []
    before = (value.get("baseline") or {}).get("positions") or {}
    now = _positions(client_id, words) if words else {}
    pairs = [(before[k], now[k]) for k in words if before.get(k) is not None and now.get(k) is not None]
    if not pairs:
        return None
    return round(sum(b - n for b, n in pairs) / len(pairs), 1)


def current_value(client_id: int, value: dict, metric: str):
    """The measured "now" of one metric, or None (= not measured, said so)."""
    try:
        if metric == "leads_30d":
            return _count_leads(client_id, 30, 0)
        if metric == "sales_30d":
            return (_sales_30(client_id) or {}).get("paid_orders")      # no store / unreadable = not measured
        if metric == "posts_per_week":
            return _posts_week(client_id)
        if metric == "positions_gained":
            return _positions_gained(client_id, value)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: {metric} not measured ({type(e).__name__})")
    return None


def evaluate(client_id: int, value: dict) -> list:
    """Per approved target: target, 90% threshold, baseline + its date, now,
    measured, met (goal_met). The record a refund claim is settled from."""
    base = value.get("baseline") or {}
    date = str(base.get("captured_at") or value.get("agreed_at") or "")[:10]
    rows = []
    for item in value.get("targets") or []:
        now = current_value(client_id, value, item["metric"])
        rows.append({"metric": item["metric"], "target": item["target"], "threshold": threshold(item["target"]),
                     "baseline": item.get("baseline"), "baseline_date": date, "current": now,
                     "basis": item.get("basis"),
                     "measured": now is not None, "met": goal_met(now, item["target"])})
    return rows


def capture_baseline(client_id: int, keywords: list) -> dict:
    """The dated starting point. Each figure is a measurement or None (= not
    measured, said so) — never a guess and never a zero for "unknown"."""
    out = {"captured_at": _now().isoformat()}
    for name, read in (("leads_30d", lambda: _count_leads(client_id, 30, 0)),
                       ("sales_30d", lambda: _sales_30(client_id)),
                       ("visitors_30d", lambda: _visitors_30(client_id)),
                       ("posts_week", lambda: _posts_week(client_id)),
                       ("positions", lambda: _positions(client_id, keywords) if keywords else {})):
        try:
            out[name] = read()
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: baseline {name} not measured ({type(e).__name__})")
            out[name] = None
    return out


# ─── Words ───────────────────────────────────────────────────────────────────

T = {
    "title": _five("היעדים שלנו ל-3 החודשים הקרובים", "Our goals for the next 3 months", "Nos objectifs pour les 3 prochains mois",
                   "أهدافنا للأشهر الثلاثة القادمة", "Наши цели на ближайшие 3 месяца"),
    "month": _five("חודש {m} מתוך 3", "Month {m} of 3", "Mois {m} sur 3", "الشهر {m} من 3", "Месяц {m} из 3"),
    "honest": _five("כל ״איפה זה עומד״ מסומן אם נמדד או עוד לא. ההתחייבות שלנו נמדדת רק על היעדים המספריים שאישרת, לפי סעיף 6 בתקנון.",
                    "Every \"where it stands\" says whether it was measured or not yet. Our commitment is measured only on the numeric targets you approved, under section 6 of the Terms.",
                    "Chaque « où on en est » indique s'il est mesuré ou pas encore. Notre engagement est mesuré uniquement sur les objectifs chiffrés que vous avez approuvés, selon l'article 6 des Conditions.",
                    "كل \"أين نقف\" يوضح إن كان مقاسًا أم ليس بعد. التزامنا يُقاس فقط على الأهداف الرقمية التي وافقتم عليها، وفق البند 6 من الشروط.",
                    "Каждое «где мы сейчас» отмечено: измерено или пока нет. Наше обязательство измеряется только по утверждённым вами числовым целям, согласно разделу 6 Условий."),
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
    "improve_now_sales": _five("נמדד: {a} מכירות ב-30 הימים האחרונים", "Measured: sales in the last 30 days: {a}",
                               "Mesuré : {a} ventes ces 30 derniers jours", "مقاس: {a} مبيعات خلال آخر 30 يومًا",
                               "Измерено: продаж за последние 30 дней: {a}"),
    "improve_next_sales": _five("הצוות ממשיך לחזק את הערוצים שמביאים קונים לאתר",
                                "The team keeps strengthening the channels that bring buyers to the site",
                                "L'équipe renforce les canaux qui amènent des acheteurs sur le site",
                                "يواصل الفريق تقوية القنوات التي تجلب المشترين إلى الموقع",
                                "Команда продолжает усиливать каналы, которые приводят покупателей на сайт"),
    "improve_next": _five("לענות לכל פנייה חדשה מהר; הצוות ממשיך לחזק את הערוצים שמביאים אותן",
                          "Answer every new enquiry quickly; the team keeps strengthening the channels that bring them",
                          "Répondre vite à chaque nouvelle demande ; l'équipe renforce les canaux qui les apportent",
                          "الرد بسرعة على كل استفسار جديد؛ يواصل الفريق تقوية القنوات التي تجلبها",
                          "Быстро отвечать на каждое обращение; команда усиливает каналы, которые их приносят"),
    "not_measured": _five("עוד לא נמדד", "Not measured yet", "Pas encore mesuré", "لم يُقس بعد", "Пока не измерено"),
    "baseline": _five("נקודת ההתחלה ({d})", "Starting point ({d})", "Point de départ ({d})", "نقطة البداية ({d})",
                      "Точка отсчёта ({d})"),
    "base_leads": _five("פניות ב-30 יום: {n}", "enquiries in 30 days: {n}", "demandes sur 30 jours : {n}",
                        "استفسارات خلال 30 يومًا: {n}", "обращений за 30 дней: {n}"),
    "base_positions": _five("ביטויים עם מיקום בגוגל: {n} מתוך {t}", "phrases with a Google position: {n} of {t}",
                            "expressions positionnées sur Google : {n} sur {t}", "عبارات لها موقع في Google: {n} من {t}",
                            "фраз с позицией в Google: {n} из {t}"),
    # Only the committed figure; the internal one never reaches the client.
    "estimate": _five("הערכה לקידום האורגני: תזוזה ברורה בביטויים תוך כ-{c} חודשים.",
                      "Organic estimate: clear movement on the phrases in about {c} months.",
                      "Estimation organique : un mouvement net sur les expressions en environ {c} mois.",
                      "تقدير عضوي: تحرّك واضح في العبارات خلال نحو {c} أشهر.",
                      "Органическая оценка: заметное движение по фразам примерно за {c} мес."),
    "m_leads_30d": _five("פניות ב-30 יום", "Enquiries in 30 days", "Demandes sur 30 jours", "الاستفسارات خلال 30 يومًا",
                         "Обращений за 30 дней"),
    "m_sales_30d": _five("מכירות ב-30 יום", "Sales in 30 days", "Ventes sur 30 jours", "المبيعات خلال 30 يومًا", "Продаж за 30 дней"),
    "base_sales": _five("מכירות ב-30 יום: {n}", "sales in 30 days: {n}", "ventes sur 30 jours : {n}", "مبيعات خلال 30 يومًا: {n}",
                        "продаж за 30 дней: {n}"),
    "monthly_sales": _five("מכירות ב-30 יום: {a} (בהתחלה: {b})", "Sales in 30 days: {a} (at the start: {b})",
                           "Ventes sur 30 jours : {a} (au départ : {b})", "المبيعات خلال 30 يومًا: {a} (في البداية: {b})",
                           "Продаж за 30 дней: {a} (в начале: {b})"),
    "m_positions_gained": _five("שיפור ממוצע בדירוג בביטויים שסיכמנו (מקומות)",
                                "Average ranking gain on the agreed phrases (positions)",
                                "Gain moyen de classement sur les expressions convenues (places)",
                                "متوسط التحسن في الترتيب على العبارات المتفق عليها (مراتب)",
                                "Средний рост позиций по согласованным фразам (мест)"),
    "m_posts_per_week": _five("פריטי תוכן בשבוע", "Content items a week", "Contenus par semaine", "عناصر محتوى أسبوعيًا",
                              "Материалов в неделю"),
    "targets_title": _five("היעדים המספריים שאישרת", "The numeric targets you approved", "Les objectifs chiffrés que vous avez approuvés",
                           "الأهداف الرقمية التي وافقتم عليها", "Числовые цели, которые вы утвердили"),
    "target_row": _five("{m}: יעד {t} · נחשב עמידה מ-{h} (90%) · נקודת בסיס {b} ({d}) · עכשיו {c}",
                        "{m}: target {t} · counts as met from {h} (90%) · baseline {b} ({d}) · now {c}",
                        "{m} : objectif {t} · atteint à partir de {h} (90 %) · référence {b} ({d}) · maintenant {c}",
                        "{m}: الهدف {t} · يُعدّ محققًا من {h} (90%) · نقطة الأساس {b} ({d}) · الآن {c}",
                        "{m}: цель {t} · засчитывается от {h} (90%) · база {b} ({d}) · сейчас {c}"),
    "met_yes": _five("בטווח היעד", "at the target", "objectif atteint", "ضمن الهدف", "цель достигнута"),
    "met_no": _five("עוד לא ביעד", "not at the target yet", "pas encore atteint", "لم يُحقَّق بعد", "пока не достигнута"),
    "terms_line": _five("איך זה נמדד ומה קורה אם לא עמדנו ביעדים: סעיף 6 בתקנון (/terms/#s6).",
                        "How this is measured, and what happens if we miss the targets: section 6 of the Terms (/terms/#s6).",
                        "Comment c'est mesuré et ce qui se passe si nous manquons les objectifs : article 6 des Conditions (/terms/#s6).",
                        "كيف يُقاس ذلك وما يحدث إن لم نحقق الأهداف: البند 6 من الشروط (/terms/#s6).",
                        "Как это измеряется и что будет, если мы не достигнем целей: раздел 6 Условий (/terms/#s6)."),
    "monthly_title": _five("📊 דוח חודש {m} מול נקודת ההתחלה ({d})", "📊 Month {m} report against the starting point ({d})",
                           "📊 Rapport du mois {m} par rapport au point de départ ({d})", "📊 تقرير الشهر {m} مقارنة بنقطة البداية ({d})",
                           "📊 Отчёт за месяц {m} относительно точки отсчёта ({d})"),
    "monthly_leads": _five("פניות ב-30 יום: {a} (בהתחלה: {b})", "Enquiries in 30 days: {a} (at the start: {b})",
                           "Demandes sur 30 jours : {a} (au départ : {b})", "الاستفسارات خلال 30 يومًا: {a} (في البداية: {b})",
                           "Обращений за 30 дней: {a} (в начале: {b})"),
    "monthly_kw": _five("{k}: מיקום {a} (בהתחלה: {b})", "{k}: position {a} (at the start: {b})", "{k} : position {a} (au départ : {b})",
                        "{k}: الموقع {a} (في البداية: {b})", "{k}: позиция {a} (в начале: {b})"),
    "monthly_scope": _five("נמדד רק על הביטויים והמדד שסיכמנו. מה שלא נמדד מסומן כך.",
                           "Measured only on the phrases and the metric we agreed. Whatever is not measured says so.",
                           "Mesuré uniquement sur les expressions et l'indicateur convenus. Ce qui n'est pas mesuré est indiqué.",
                           "يُقاس فقط على العبارات والمؤشر المتفق عليها. ما لم يُقس مذكور كذلك.",
                           "Измеряется только по согласованным фразам и показателю. Неизмеренное так и отмечено."),
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


def _counts_sales(client_id: int):
    """True when this client's stated result is SALES (core/target_inputs),
    False when enquiries, None when it cannot be read. A business that counts
    sales is never reported in enquiries (client 27's live card did that)."""
    try:
        from core import target_inputs
        return target_inputs.result_kind(target_inputs._conversion(client_id)) == "sales"
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: result definition unreadable ({type(e).__name__})")
        return None


def _improve(client_id: int, value: dict, lang: str) -> dict:
    out = {"key": "improve", "persona": "general",
           "goal": _t("improve_goal", lang, x=value.get("improve") or pick(T["improve_default"], lang))}
    sales = _counts_sales(client_id)
    if sales or sales is None:
        orders = current_value(client_id, value, "sales_30d") if sales else None
        if orders is None:
            out.update(now=_t("not_measured", lang), measured=False)
        else:
            out.update(now=_t("improve_now_sales", lang, a=num(orders)), measured=True)
        out["next"] = _t("improve_next_sales" if sales else "improve_next", lang)
        return out
    try:
        a, b = _count_leads(client_id, 30, 0), _count_leads(client_id, 60, 30)
        out.update(now=_t("improve_now", lang, a=a, b=b), measured=True)
    except Exception:
        out.update(now=_t("not_measured", lang), measured=False)
    out["next"] = _t("improve_next", lang)
    return out


def _fmt(value, lang: str) -> str:
    return pick(T["not_measured"], lang) if value is None else str(value)


def _baseline_line(value: dict, lang: str, client_id: int = None) -> str:
    base = value.get("baseline") or {}
    if not base:
        return ""
    parts = [] if (client_id is not None and _counts_sales(client_id)) else [_t("base_leads", lang, n=_fmt(base.get("leads_30d"), lang))]
    if isinstance(base.get("sales_30d"), dict):          # a store is connected: its sales are part of the starting point
        parts.append(_t("base_sales", lang, n=_fmt(base["sales_30d"].get("paid_orders"), lang)))
    words = value.get("keywords") or []
    if words:
        positions = base.get("positions")
        parts.append(_t("base_positions", lang, n=_fmt(len(positions) if positions is not None else None, lang),
                        t=len(words)))
    return _t("baseline", lang, d=str(base.get("captured_at", ""))[:10]) + ": " + " · ".join(parts)


def target_line(row: dict, lang: str) -> str:
    """One approved target: the target, the 90% threshold, the dated baseline, now."""
    nm = pick(T["not_measured"], lang)
    line = _t("target_row", lang, m=pick(T["m_" + row["metric"]], lang), t=num(row["target"]), h=num(row["threshold"]),
              b=num(row["baseline"]) if row.get("baseline") is not None else nm, d=row.get("baseline_date") or "—",
              c=num(row["current"]) if row.get("current") is not None else nm)
    if row.get("met") is not None:
        line += " · " + pick(T["met_yes" if row["met"] else "met_no"], lang)
    if row.get("basis"):          # goals agreed before 2026-11-28 carry none: nothing is invented for them
        from core import target_inputs
        try:
            line += " · " + target_inputs.basis_line(row["basis"], lang)
        except target_inputs.BasisMissing:
            pass
    return line


def learning_lines(value: dict, lang: str) -> list:
    """The learning period the client approved in place of a result target."""
    if not value.get("learning"):
        return []
    from core import target_inputs
    try:
        return target_inputs.learning_lines(value["learning"], lang)
    except target_inputs.BasisMissing:
        return []


def month_of(value: dict) -> int:
    try:
        days = (_now() - datetime.fromisoformat(str(value["agreed_at"]).replace("Z", "+00:00"))).days
    except (KeyError, TypeError, ValueError):
        return 0
    return days // 30


def monthly_report(client_id: int, value: dict, lang: str = None) -> str:
    """The month's report against the dated baseline, on the agreed keywords and metric only."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    base = value.get("baseline") or {}
    lines = [_t("monthly_title", lang, m=max(1, month_of(value)), d=str(base.get("captured_at", value.get("agreed_at", "")))[:10])]
    try:
        now_leads = _count_leads(client_id, 30, 0)
    except Exception:
        now_leads = None
    if not _counts_sales(client_id):                      # a business that counts sales is not reported in enquiries
        lines.append("• " + _t("monthly_leads", lang, a=_fmt(now_leads, lang), b=_fmt(base.get("leads_30d"), lang)))
    if isinstance(base.get("sales_30d"), dict):
        lines.append("• " + _t("monthly_sales", lang, a=_fmt(current_value(client_id, value, "sales_30d"), lang),
                               b=_fmt(base["sales_30d"].get("paid_orders"), lang)))
    words = value.get("keywords") or []
    try:
        now_pos = _positions(client_id, words) if words else {}
    except Exception:
        now_pos = {}
    before = base.get("positions") or {}
    for word in words:
        lines.append("• " + _t("monthly_kw", lang, k=word, a=_fmt(now_pos.get(word), lang), b=_fmt(before.get(word), lang)))
    rows = evaluate(client_id, value)
    if rows:
        lines.append(pick(T["targets_title"], lang) + ":")
        lines.extend("• " + target_line(r, lang) for r in rows)
    lines.extend(learning_lines(value, lang))
    lines.append(pick(T["monthly_scope"], lang))
    lines.append(pick(T["terms_line"], lang))
    return "\n".join(lines)


def due_month(value: dict) -> int:
    """The completed month (1, 2 or 3) whose report has not been posted yet; 0 = none due."""
    month = min(3, month_of(value))
    if month >= 1 and month not in (value.get("reported_months") or []):
        return month
    return 0


def post_monthly_if_due(client_id: int, lang: str = None) -> bool:
    """Posts the due monthly report ONCE into the manager's chat. The month is
    marked BEFORE posting, so a retry never posts it twice."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    value = agreed(client_id)
    month = due_month(value) if value else 0
    if not month:
        return False
    value["reported_months"] = sorted(set((value.get("reported_months") or []) + [month]))
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": value,
                                        "updated_at": _now().isoformat()}, on_conflict="key").execute()
    from core import agent_actions
    agent_actions._post_client_message(client_id, "general", monthly_report(client_id, value, lang))
    return True


def track(client_id: int, lang: str = None) -> dict:
    """{} until goals are agreed. Otherwise the title, the month, the honesty
    line and three steps {key, persona, goal, now, measured, next}."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    lang = lang if lang in LANGS else "he"
    value = agreed(client_id)
    if not value:
        return {}
    try:
        days = (_now() - datetime.fromisoformat(str(value["agreed_at"]).replace("Z", "+00:00"))).days
    except (TypeError, ValueError):
        days = 0
    month = max(1, min(3, days // 30 + 1))
    plan = value.get("plan") or {}
    estimate = _t("estimate", lang, c=num(plan["committed_months"])) if plan.get("committed_months") else ""
    rows = evaluate(client_id, value)
    return {"agreed": True, "title": pick(T["title"], lang), "month": _t("month", lang, m=month),
            "honest": pick(T["honest"], lang), "baseline": _baseline_line(value, lang, client_id), "estimate": estimate,
            "targets_title": pick(T["targets_title"], lang) if rows else "",
            "targets": [dict(r, text=target_line(r, lang)) for r in rows],
            "learning": learning_lines(value, lang),
            "terms_url": "/terms/#s6",
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
        # the committed targets only: `internal` (the aims + the buffer) never reaches an agent
        out = ({"agreed_on": value["agreed_at"][:10], "keywords": value.get("keywords") or [],
                "posts_per_week": value.get("posts_per_week"), "improve": value.get("improve", ""),
                "baseline": value.get("baseline") or {},
                "plan_estimate": {k: v for k, v in (value.get("plan") or {}).items() if k != "internal_months"},
                "targets": value.get("targets") or [],
                "learning_period": ({"until": value["learning"].get("until"),
                                     "note": "no result target was agreed: the baseline was too thin. Never state one."}
                                    if value.get("learning") else None),
                "met_rule": "90% of a numeric target counts as met (terms §6)",
                "measure_only_these": True}
               if value else {"agreed": False,
                              # the client's OWN phrases are an INPUT to the keyword research
                              # (core/organic_plan) — never proposed as goals before it ran
                              "client_phrases_for_research_only": suggested_keywords(client_id)})
    except Exception:
        return {}
    _block_cache[int(client_id)] = (time.time(), out)
    return out
