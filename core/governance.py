"""Client governance, wired (2026-11-26): knowledge/client-governance.md is
POLICY, and this module is where the code holds it. docs/GOVERNANCE.md.

- THE TEXT (§ all): `text()` — loaded in full into every client-facing agent
  prompt (`with_rules`), next to the craft library. Missing = the content
  path refuses loudly (core/post_brief.ContextMissing), like the craft file.
- AUTHORITY (§1): `authority(client_id)` — "decide" only when the brand brief
  is APPROVED, else "ask". In every agent's data (`prompt_block`).
- LANES (§2): every work item carries `lane` = "system" | "client", set at
  CREATION (`lane_at_creation`) and never inferred later; an item created
  before 2026-11-26 has none and is reported as "unrecorded", never guessed.
  Costs carry the lane of the work that caused them (`lane_scope` →
  cost_tracker). `lanes_report` returns the lanes SEPARATELY; nothing here
  returns a blended number.
- OVERRIDES (§3): `log_override` — recommendation, the client's decision, the
  date. Logged only together with a line the client reads in that same reply
  (`OVERRIDE_NOTED`), so no objection is logged that he never saw. Topics
  already decided go back to the agents as "settled — do not argue again".
- WEEKLY SYNC (§4): one presentation a week on the client's evening
  (thu / fri / sat), built by CODE from what exists; the client approves; a
  silent client is escalated to the owner (alert + email) who can approve on
  his behalf; nothing is published or frozen by silence. Training accounts
  (Shin Sekai) are exempt from the escalation.
- CREDITS (§6): `credits(client_id)` — consumed by the system lane, by the
  client's own requests, what is left, how long it lasts. No ceiling: this
  module never refuses anything.

Storage: ONE app_settings row per client, `governance:<client_id>`
= {"sync_day", "overrides": [...], "sync": {...}, "sync_history": [...]}.
No migration. Cleared with the client (client_deletion, deletion_guard).
"""
import contextvars
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SERVICE_NAME = "governance"
PREFIX = "governance:"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "knowledge", "client-governance.md")
TZ_NAME = "Asia/Jerusalem"


def _tz():
    """Israel time, resolved when used (a machine without the zone database can still import this)."""
    return ZoneInfo(TZ_NAME)

LANES = ("system", "client")
UNRECORDED = "unrecorded"            # created before lanes were recorded — never guessed into a lane
SYNC_DAYS = {"thu": 3, "fri": 4, "sat": 5}          # datetime.weekday()
DEFAULT_SYNC_DAY = "thu"
SYNC_HOUR = 18                       # "evening": not presented before this hour, Israel time
ESCALATE_AFTER_HOURS = 24            # a presented week still unanswered after this → the owner
TRAINING_ACCOUNTS = (27,)            # Shin Sekai: the training account, exempt from the escalation (§4)
MAX_OVERRIDES = 200
BAND = 0.15                          # §5: the room for error around the real aim (quarter_goals.BUFFER)

_lane = contextvars.ContextVar("uallak_lane", default="")


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or next(iter(table.values()))


# ─── The text, in every agent's prompt (4.1) ─────────────────────────────────

_text_cache = {}


def text() -> str:
    """knowledge/client-governance.md in full ('' = missing: a broken deploy)."""
    try:
        stamp = os.path.getmtime(PATH)
    except OSError:
        return ""
    hit = _text_cache.get("t")
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with open(PATH, encoding="utf-8") as f:
            body = f.read().strip()
    except OSError:
        return ""
    _text_cache["t"] = (stamp, body)
    return body


WIRING_RULE = """
=== CLIENT GOVERNANCE (knowledge/client-governance.md) — POLICY, not background ===
How it reaches you in the data (`governance`):
- governance.authority: "ask" = this client's brand brief is NOT approved: for a strategic choice (audience,
  angle, palette, calendar, budget split) you PROPOSE, explain and wait. "decide" = the brief is approved: you
  decide and INFORM — do not ask permission for each post, angle or palette choice.
- LANE: everything you set in motion is in exactly one lane. Set "lane" on what you propose: "system" = the
  system's own plan (the weekly plan, the calendar, what you decided); "client" = the client asked for it beyond
  the plan (more posts, another page, his own taste). A client-lane request costs HIS credits: say the cost
  BEFORE it runs. Never blend the two lanes into one number.
- OVERRIDE: when the client wants it done differently from what you would do — advise ONCE (the real reason:
  the segment, the evidence, the brief; one warm paragraph), state the commitment ("אנחנו מחויבים ליעדים
  שסיכמנו, והשינוי הזה עשוי להשפיע עליהם"), then COMPLY. On the turn you comply, set "override_log":
  {"topic": short English key, "recommendation": what you advised (one sentence, the client's language),
  "decision": what the client chose}. The system records it and tells the client it was recorded.
- governance.settled lists what this client already decided against your advice. NEVER argue any of them
  again: do it his way, without a second warning.
- A miss is a miss: report a target outside its band as missed — never softened, reframed or averaged away.
- A failing campaign: say it first, apologise plainly, then the fix.
- SPEAKING TO THE CLIENT (§9): no platform vocabulary, no acronyms, none of our internals; a campaign is
  presented as where it appears, the budget (total and per day), the goal, and the expected result WITH what
  it rests on. The code checks what you write (core/client_speech) and rewrites or blocks it.

"""


def with_rules(system: str) -> str:
    """The agent's system prompt + the governance doctrine in full (static,
    so the cached prefix holds)."""
    return system + WIRING_RULE + (text() or "(knowledge/client-governance.md is MISSING — tell the manager)")


# ─── Storage ─────────────────────────────────────────────────────────────────

def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _key(client_id: int) -> str:
    return f"{PREFIX}{int(client_id)}"


def _read(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", _key(client_id)).limit(1).execute().data) or []
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _write(client_id: int, value: dict):
    _db().table("app_settings").upsert({"key": _key(client_id), "value": value, "updated_at": _now().isoformat()},
                                       on_conflict="key").execute()


def state(client_id: int) -> dict:
    """The row, {} when unreadable (logged) — readers degrade, writers raise."""
    try:
        return _read(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: unreadable ({type(e).__name__})")
        return {}


def _alert(lines: list):
    try:
        from agents.master_agent import alert
        alert(SERVICE_NAME, lines)
    except Exception as e:
        print(f"[{SERVICE_NAME}] alert failed ({type(e).__name__})")


# ─── Authority (§1, 4.6) ─────────────────────────────────────────────────────

def authority(client_id: int) -> dict:
    """{"mode": "decide" | "ask", "brief_status"} — no approved brand brief,
    no strategic authority. An unreadable brief is "ask" (the safe side)."""
    try:
        from core import brand_brief
        status = str(brand_brief.current(client_id).get("status") or "missing")
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: brief status unreadable ({type(e).__name__})")
        status = "unknown"
    return {"mode": "decide" if status == "approved" else "ask", "brief_status": status}


# ─── Lanes (§2, 4.2) ─────────────────────────────────────────────────────────

SYSTEM_SOURCES = ("weekly", "plan", "content_plan", "system")


def clean_lane(value) -> str:
    value = str(value or "").strip().lower()
    return value if value in LANES else ""


def lane_at_creation(declared=None, source: str = "", plan_ref=None) -> str:
    """The lane of a work item, decided ONCE, when it is created: what the
    creator declared; else the lane of the work in progress (a post's assets
    inherit the post's); else by origin — the system's own plan is the system
    lane, anything else was asked for."""
    return (clean_lane(declared) or clean_lane(_lane.get())
            or ("system" if plan_ref or str(source or "") in SYSTEM_SOURCES else "client"))


@contextmanager
def lane_scope(lane: str):
    """Work running for one lane: items it creates and costs it causes carry it."""
    token = _lane.set(clean_lane(lane))
    try:
        yield
    finally:
        _lane.reset(token)


def current_lane() -> str:
    return _lane.get()


def lane_of(item: dict) -> str:
    """What is RECORDED on the item — never inferred."""
    return clean_lane((item or {}).get("lane")) or UNRECORDED


def _queue(client_id: int) -> list:
    from core import social_queue
    return social_queue.items(client_id)


def _since(days: int) -> str:
    return (_now() - timedelta(days=days)).isoformat()


def lanes_report(client_id: int, days: int = 7) -> dict:
    """Per lane, SEPARATELY: what was created, approved, sent back and is
    waiting in the window. {"system": {...}, "client": {...}, "unrecorded":
    {...}} — there is deliberately no total."""
    since = _since(days)
    out = {lane: {"created": 0, "approved": 0, "rejected": 0, "waiting": 0, "titles": []}
           for lane in LANES + (UNRECORDED,)}
    try:
        items = _queue(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: queue unreadable ({type(e).__name__})")
        return {**out, "readable": False, "days": days}
    for item in items:
        if str(item.get("created_at") or "") < since or item.get("kind") == "design":
            continue                                   # a design item is a step of its post, not its own work
        row = out[lane_of(item)]
        row["created"] += 1
        status = item.get("status")
        if status in ("approved", "waiting", "rejected"):
            row[status] += 1
        title = str(item.get("title") or (item.get("post") or {}).get("objective") or item.get("caption") or "")[:80]
        if title and len(row["titles"]) < 6:
            row["titles"].append(title)
    return {**out, "readable": True, "days": days}


def client_lane_media_count(client_id: int, days: int) -> int:
    """Finished media pieces of the CLIENT lane in the window — what the
    system's own output target must not count (quarter_goals._posts_week)."""
    since = _since(days)
    return sum(1 for i in _queue(client_id)
               if i.get("kind") in ("image", "video") and lane_of(i) == "client" and str(i.get("created_at") or "") >= since)


# ─── Credits (§6, 4.5) ───────────────────────────────────────────────────────

def _cost_rows(client_id: int, since: str) -> list:
    return (_db().table("client_costs").select("details,created_at").eq("client_id", int(client_id))
            .gte("created_at", since).limit(5000).execute().data) or []


def _package_credits(client_id: int):
    try:
        from core import client_settings
        value = client_settings.get(client_id).get("package_credits")
        return int(value) if value not in (None, "") else None
    except Exception:
        return None


def credits(client_id: int) -> dict:
    """This month's generation credits, per lane, and what is left.
    `package`/`left`/`days_left` are None when no package size is set for the
    client — shown as "not set", never as zero. Never a gate."""
    now = _now()
    month_start = now.astimezone(_tz()).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    out = {"system": 0.0, "client": 0.0, UNRECORDED: 0.0, "readable": True, "month": month_start.strftime("%Y-%m"),
           "package": None, "left": None, "days_left": None, "per_day": None}
    try:
        rows = _cost_rows(client_id, month_start.astimezone(timezone.utc).isoformat())
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: costs unreadable ({type(e).__name__})")
        return {**out, "readable": False}
    recent, recent_from = 0.0, (now - timedelta(days=14)).isoformat()
    for r in rows:
        d = r.get("details") if isinstance(r.get("details"), dict) else {}
        try:
            n = float(d.get("credits") or 0)
        except (TypeError, ValueError):
            n = 0.0
        if n <= 0:
            continue
        out[clean_lane(d.get("lane")) or UNRECORDED] += n
        if str(r.get("created_at") or "") >= recent_from:
            recent += n
    for k in LANES + (UNRECORDED,):
        out[k] = round(out[k], 1)
    used = out["system"] + out["client"] + out[UNRECORDED]
    days_in_window = max(1.0, min(14.0, (now - month_start.astimezone(timezone.utc)).total_seconds() / 86400))
    out["per_day"] = round(recent / days_in_window, 2) if recent else 0.0
    package = _package_credits(client_id)
    if package is not None:
        out["package"] = package
        out["left"] = round(package - used, 1)
        if out["per_day"] and out["left"] > 0:
            out["days_left"] = int(out["left"] / out["per_day"])
    return out


# ─── The override log (§3, 4.3) ──────────────────────────────────────────────

OVERRIDE_NOTED = _five(
    "רשמתי: ההמלצה שלנו הייתה — {rec} בחרתם להמשיך בדרך שלכם, וזה מה שנעשה. זה יופיע בסיכום השבועי.",
    "Noted: our recommendation was — {rec} You chose to go your way, and that is what we will do. It will appear in the weekly summary.",
    "C'est noté : notre recommandation était — {rec} Vous avez choisi votre voie, et c'est ce que nous ferons. Cela figurera dans le bilan hebdomadaire.",
    "تم التسجيل: توصيتنا كانت — {rec} اخترتم طريقتكم، وهذا ما سنفعله. سيظهر ذلك في الملخص الأسبوعي.",
    "Записано: наша рекомендация была — {rec} Вы решили по-своему, так и сделаем. Это появится в недельной сводке.")


def _clean(value, n: int) -> str:
    return " ".join(str(value or "").split())[:n]


def log_override(client_id: int, recommendation: str, decision: str, topic: str = "", agent: str = "",
                 item_id: str = "") -> dict:
    """Record ONE override: what the system recommended, what the client
    decided, when. Raises when it cannot be stored (step 4 is not optional —
    the caller must not tell the client it was recorded)."""
    rec, dec = _clean(recommendation, 400), _clean(decision, 300)
    if not rec or not dec:
        raise ValueError("an override needs the recommendation and the client's decision")
    value = _read(client_id)
    row = {"id": secrets.token_hex(5), "at": _now().isoformat(), "topic": _clean(topic, 60).lower() or "general",
           "recommendation": rec, "decision": dec, "agent": _clean(agent, 30), "item_id": _clean(item_id, 20)}
    value["overrides"] = (list(value.get("overrides") or []) + [row])[-MAX_OVERRIDES:]
    _write(client_id, value)
    return row


def overrides(client_id: int, days: int = None, value: dict = None) -> list:
    value = value if value is not None else state(client_id)
    rows = list(value.get("overrides") or [])
    if days is not None:
        since = _since(days)
        rows = [r for r in rows if str(r.get("at") or "") >= since]
    return rows


def settled(client_id: int, value: dict = None) -> list:
    """What the client already decided against advice — the agents never
    argue these again. Newest first, one per topic."""
    seen, out = set(), []
    for r in reversed(overrides(client_id, value=value)):
        if r.get("topic") not in seen:
            seen.add(r.get("topic"))
            out.append({"topic": r.get("topic"), "decision": r.get("decision"), "date": str(r.get("at") or "")[:10]})
    return out[:20]


def note_override(client_id: int, raw, reply: str, agent: str, lang: str) -> str:
    """The chat model complied with an override and set `override_log`: store
    it and add the line the client reads (so nothing is logged he never saw).
    Not stored = no line and an alert; the reply is returned unchanged."""
    if not isinstance(raw, dict) or not _clean(raw.get("recommendation"), 400) or not _clean(raw.get("decision"), 300):
        return reply
    try:
        row = log_override(client_id, raw.get("recommendation"), raw.get("decision"), raw.get("topic"), agent)
    except Exception as e:
        _alert([f"client {client_id}: an override was NOT logged ({type(e).__name__}) — the client was not told it was"])
        return reply
    rec = row["recommendation"] if row["recommendation"].endswith((".", "!", "?")) else row["recommendation"] + "."
    return (reply or "").rstrip() + "\n\n" + pick(OVERRIDE_NOTED, lang).format(rec=rec)


def prompt_block(client_id: int) -> dict:
    """`governance` in every agent's data."""
    value = state(client_id)
    who = authority(client_id)
    return {"authority": who["mode"], "brief_status": who["brief_status"], "settled": settled(client_id, value),
            "sync_day": sync_day(client_id, value)}


# ─── The weekly sync (§4, 4.4) ───────────────────────────────────────────────

T = {
    "title": _five("סיכום שבועי ותכנון השבוע הבא", "Weekly summary and next week's plan", "Bilan de la semaine et plan de la suivante",
                   "ملخص الأسبوع وخطة الأسبوع القادم", "Итоги недели и план на следующую"),
    "posts": _five("מה מתוכנן לפרסום", "What is planned to publish", "Ce qui est prévu à la publication", "ما هو مخطط للنشر", "Что запланировано к публикации"),
    "posts_none": _five("עוד אין פריטים מתוכננים לשבוע הבא.", "Nothing is planned for next week yet.", "Rien de prévu pour la semaine prochaine.",
                        "لا شيء مخطط للأسبوع القادم بعد.", "На следующую неделю пока ничего не запланировано."),
    "system_lane": _five("מסלול המערכת (מה שאנחנו תכננו)", "System lane (what we planned)", "Voie système (ce que nous avons planifié)",
                         "مسار النظام (ما خططنا له)", "Линия системы (что запланировали мы)"),
    "client_lane": _five("מסלול הלקוח (מה שביקשתם מעבר לתוכנית)", "Client lane (what you asked for beyond the plan)",
                         "Voie client (vos demandes hors plan)", "مسار العميل (ما طلبتموه خارج الخطة)",
                         "Линия клиента (ваши запросы сверх плана)"),
    "unrecorded": _five("לפני שהתחלנו לרשום מסלולים", "From before lanes were recorded", "Avant l'enregistrement des voies",
                        "قبل بدء تسجيل المسارات", "До начала учёта линий"),
    "lane_line": _five("{created} פריטים · {approved} אושרו · {waiting} מחכים לכם · {rejected} חזרו לתיקון",
                       "{created} items · {approved} approved · {waiting} waiting for you · {rejected} sent back",
                       "{created} éléments · {approved} validés · {waiting} en attente · {rejected} renvoyés",
                       "{created} عناصر · {approved} معتمدة · {waiting} بانتظاركم · {rejected} أُعيدت",
                       "{created} единиц · {approved} одобрено · {waiting} ждут вас · {rejected} возвращено"),
    "credits": _five("קרדיטים החודש", "Credits this month", "Crédits ce mois-ci", "الأرصدة هذا الشهر", "Кредиты за месяц"),
    "credits_line": _five("המערכת: {system} · הבקשות שלכם: {client}", "System: {system} · Your requests: {client}",
                          "Système : {system} · Vos demandes : {client}", "النظام: {system} · طلباتكم: {client}",
                          "Система: {system} · Ваши запросы: {client}"),
    "credits_left": _five("נשארו {left} מתוך {package}", "{left} left of {package}", "Reste {left} sur {package}",
                          "المتبقي {left} من {package}", "Осталось {left} из {package}"),
    "credits_days": _five("בקצב הנוכחי זה מספיק לעוד כ-{days} ימים", "At the current rate that lasts about {days} more days",
                          "Au rythme actuel, il reste environ {days} jours", "بالمعدل الحالي يكفي نحو {days} يومًا",
                          "При текущем темпе хватит примерно на {days} дн."),
    "credits_unset": _five("גודל החבילה עוד לא הוגדר, ולכן אין כאן יתרה.", "The package size is not set yet, so no balance is shown.",
                           "La taille du forfait n'est pas définie : pas de solde affiché.", "حجم الباقة غير محدد بعد، لذا لا يظهر رصيد.",
                           "Размер пакета ещё не задан, поэтому остаток не показан."),
    "credits_unread": _five("לא הצלחנו לקרוא את הקרדיטים כרגע.", "We couldn't read the credits right now.", "Lecture des crédits impossible pour l'instant.",
                            "تعذّرت قراءة الأرصدة الآن.", "Не удалось прочитать кредиты."),
    "overrides": _five("המלצות שבחרתם לא לקבל", "Recommendations you chose not to take", "Recommandations que vous n'avez pas suivies",
                       "توصيات اخترتم عدم الأخذ بها", "Рекомендации, которым вы решили не следовать"),
    "override_line": _five("{date}: המלצנו — {rec} בחרתם — {dec}", "{date}: we recommended — {rec} You chose — {dec}",
                           "{date} : nous recommandions — {rec} Vous avez choisi — {dec}", "{date}: أوصينا — {rec} اخترتم — {dec}",
                           "{date}: мы рекомендовали — {rec} Вы выбрали — {dec}"),
    "targets": _five("יעדים", "Targets", "Objectifs", "الأهداف", "Цели"),
    "hit": _five("עומדים ביעד", "On target", "Objectif atteint", "ضمن الهدف", "Цель выполняется"),
    "miss": _five("לא עומדים ביעד", "Missed", "Objectif manqué", "لم يتحقق الهدف", "Цель не выполнена"),
    "unmeasured": _five("עוד לא נמדד", "Not measured yet", "Pas encore mesuré", "لم يُقَس بعد", "Ещё не измерено"),
    "campaigns": _five("קמפיינים", "Campaigns", "Campagnes", "الحملات", "Кампании"),
    "campaigns_unread": _five("נתוני הקמפיינים לא נקראו לסיכום הזה.", "Campaign data was not read for this summary.",
                              "Les données de campagne n'ont pas été lues pour ce bilan.", "لم تُقرأ بيانات الحملات لهذا الملخص.",
                              "Данные кампаний для этой сводки не прочитаны."),
    "campaigns_none": _five("אין קמפיינים רצים ב-30 הימים האחרונים.", "No campaigns ran in the last 30 days.",
                            "Aucune campagne sur les 30 derniers jours.", "لا حملات خلال آخر 30 يومًا.", "За последние 30 дней кампаний не было."),
    "campaign_line": _five("{platform} · {name} · {status} · הוצאה {cost} · המרות {conversions} (30 יום)",
                           "{platform} · {name} · {status} · spend {cost} · conversions {conversions} (30 days)",
                           "{platform} · {name} · {status} · dépense {cost} · conversions {conversions} (30 jours)",
                           "{platform} · {name} · {status} · الإنفاق {cost} · التحويلات {conversions} (30 يومًا)",
                           "{platform} · {name} · {status} · расход {cost} · конверсии {conversions} (30 дней)"),
    "ask": _five("הסיכום השבועי מחכה לאישור שלכם בעמוד הראשי.", "The weekly summary is waiting for your approval on the main page.",
                 "Le bilan hebdomadaire attend votre validation sur la page principale.", "الملخص الأسبوعي بانتظار اعتمادكم في الصفحة الرئيسية.",
                 "Недельная сводка ждёт вашего одобрения на главной странице."),
}


def sync_day(client_id: int, value: dict = None) -> str:
    value = value if value is not None else state(client_id)
    day = str(value.get("sync_day") or "")
    return day if day in SYNC_DAYS else DEFAULT_SYNC_DAY


def set_sync_day(client_id: int, day: str) -> str:
    if day not in SYNC_DAYS:
        raise ValueError(f"sync day must be one of {tuple(SYNC_DAYS)}")
    value = _read(client_id)
    value["sync_day"] = day
    _write(client_id, value)
    return day


def week_id(now: datetime = None) -> str:
    local = (now or _now()).astimezone(_tz())
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"


def _planned(client_id: int, now: datetime) -> list:
    """What is heading to the channels in the coming 8 days (the queue's
    waiting and approved items with a date), with the thinking recorded on it."""
    today = now.astimezone(_tz()).date()
    until = today + timedelta(days=8)
    out = []
    for item in _queue(client_id):
        if item.get("status") not in ("waiting", "approved") or item.get("kind") == "design":
            continue
        date = str(item.get("planned_date") or "")[:10]
        if date and not (today.isoformat() <= date <= until.isoformat()):
            continue
        post = item.get("post") or {}
        out.append({"id": item.get("id"), "title": str(item.get("title") or post.get("objective") or item.get("caption") or "")[:120],
                    "date": date, "status": item.get("status"), "lane": lane_of(item),
                    "why": str(post.get("format_reason") or "")[:200], "channels": list(item.get("channels") or [])})
    return sorted(out, key=lambda r: (r["date"] or "9999", r["title"]))[:20]


def _targets(client_id: int) -> list:
    try:
        from core import quarter_goals
        value = quarter_goals.agreed(client_id)
        if not value:
            return []
        return [{"metric": r["metric"], "target": r["target"], "current": r["current"],
                 "state": "unmeasured" if r["met"] is None else "hit" if r["met"] else "miss"}
                for r in quarter_goals.evaluate(client_id, value)]
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: targets unreadable ({type(e).__name__})")
        return []


def _campaigns(client_id: int):
    """The running campaigns as the account snapshot has them, or None (not
    read — said so, never shown as "no campaigns")."""
    try:
        from core import client_state
        snap = client_state.snapshot(client_id) or {}
        out, read = [], False
        for platform, key in (("Google", "google_campaigns"), ("Meta", "meta_campaigns")):
            section = snap.get(key)
            if not isinstance(section, dict):
                continue                               # that read failed: not shown, not counted as "none"
            read = True
            for c in section.get("campaigns") or []:
                out.append({"platform": platform, "name": str(c.get("name") or "")[:80], "status": str(c.get("status") or ""),
                            "cost": c.get("cost"), "conversions": c.get("conversions"), "clicks": c.get("clicks")})
        return out if read else None
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: campaigns unreadable ({type(e).__name__})")
        return None


def build_sync(client_id: int, now: datetime = None) -> dict:
    """The week's presentation, from what EXISTS (no model call): planned
    items with their reasons, the lanes separately, credits by lane, the
    overrides of the week, the targets as hit / miss."""
    now = now or _now()
    try:
        planned = _planned(client_id, now)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: planned items unreadable ({type(e).__name__})")
        planned = None
    return {"week": week_id(now), "built_at": now.isoformat(), "planned": planned,
            "lanes": lanes_report(client_id, 7), "credits": credits(client_id),
            "overrides": overrides(client_id, days=7), "targets": _targets(client_id),
            "campaigns": _campaigns(client_id)}


def sync_lines(summary: dict, lang: str) -> list:
    """The summary as plain lines (the chat message and the owner's email)."""
    t = lambda k, **v: pick(T[k], lang).format(**v)
    lines = [t("title"), "", t("posts") + ":"]
    planned = summary.get("planned")
    if not planned:
        lines.append("• " + t("posts_none"))
    for p in planned or []:
        lines.append(f"• {p['date'] or '—'} · {p['title']}" + (f" — {p['why']}" if p.get("why") else ""))
    lanes = summary.get("lanes") or {}
    for lane, key in (("system", "system_lane"), ("client", "client_lane"), (UNRECORDED, "unrecorded")):
        row = lanes.get(lane) or {}
        if lane == UNRECORDED and not row.get("created"):
            continue
        lines += ["", t(key) + ":", "• " + t("lane_line", **{k: row.get(k, 0) for k in ("created", "approved", "waiting", "rejected")})]
    c = summary.get("credits") or {}
    lines += ["", t("credits") + ":"]
    if not c.get("readable"):
        lines.append("• " + t("credits_unread"))
    else:
        lines.append("• " + t("credits_line", system=_n(c.get("system")), client=_n(c.get("client"))))
        if c.get("package") is None:
            lines.append("• " + t("credits_unset"))
        else:
            lines.append("• " + t("credits_left", left=_n(c.get("left")), package=_n(c.get("package"))))
            if c.get("days_left") is not None:
                lines.append("• " + t("credits_days", days=c["days_left"]))
    if summary.get("overrides"):
        lines += ["", t("overrides") + ":"]
        lines += ["• " + t("override_line", date=str(o.get("at") or "")[:10], rec=o.get("recommendation", ""),
                           dec=o.get("decision", "")) for o in summary["overrides"]]
    if summary.get("targets"):
        lines += ["", t("targets") + ":"]
        lines += [f"• {r['metric']}: {_n(r['current']) if r['current'] is not None else '—'} / {_n(r['target'])} — {t(r['state'])}"
                  for r in summary["targets"]]
    campaigns = summary.get("campaigns")
    lines += ["", t("campaigns") + ":"]
    if campaigns is None:
        lines.append("• " + t("campaigns_unread"))
    elif not campaigns:
        lines.append("• " + t("campaigns_none"))
    for c in campaigns or []:
        lines.append("• " + t("campaign_line", platform=c.get("platform", ""), name=c.get("name", ""), status=c.get("status", ""),
                              cost=_n(c.get("cost")), conversions=_n(c.get("conversions"))))
    return lines


def _n(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "—"
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def _lang(client_id: int) -> str:
    try:
        from core import client_language
        return client_language.of(client_id)
    except Exception:
        return "he"


def _post_chat(client_id: int, body: str) -> bool:
    from agents.client_agent import log_communication
    return bool(log_communication(client_id, "outbound", "dashboard_chat", body))


def _email_owner(subject: str, body: str) -> bool:
    from core import email_service
    return bool(email_service.send_owner_notice(subject, body))


def _client_name(client_id: int) -> str:
    try:
        from agents.client_agent import get_client
        return str((get_client(client_id) or {}).get("business_name") or f"client {client_id}")
    except Exception:
        return f"client {client_id}"


def is_due(client_id: int, now: datetime = None, value: dict = None) -> bool:
    """This client's evening has come and this week was not presented yet."""
    now = now or _now()
    local = now.astimezone(_tz())
    value = value if value is not None else state(client_id)
    if local.weekday() != SYNC_DAYS[sync_day(client_id, value)] or local.hour < SYNC_HOUR:
        return False
    return (value.get("sync") or {}).get("week") != week_id(now)


def present(client_id: int, now: datetime = None) -> dict:
    """Build this week's sync, store it as WAITING, and tell the client in the
    manager's chat. Stored BEFORE the message, so a retry never posts twice."""
    now = now or _now()
    value = _read(client_id)
    old = value.get("sync") or {}
    if old.get("week") == week_id(now):
        return old
    summary = build_sync(client_id, now)
    sync = {"week": summary["week"], "state": "waiting", "presented_at": now.isoformat(), "summary": summary,
            "approved_by": "", "approved_at": "", "escalated_at": ""}
    if old.get("week"):
        value["sync_history"] = (list(value.get("sync_history") or [])
                                 + [{k: old.get(k) for k in ("week", "state", "presented_at", "approved_by", "approved_at", "escalated_at")}])[-52:]
    value["sync"] = sync
    _write(client_id, value)
    lang = _lang(client_id)
    try:
        _post_chat(client_id, "\n".join(sync_lines(summary, lang) + ["", pick(T["ask"], lang)]))
    except Exception as e:
        _alert([f"client {client_id}: the weekly sync was stored but its chat message was not posted ({type(e).__name__})"])
    return sync


def approve_sync(client_id: int, by: str = "client") -> dict:
    """The client's approval — or the owner's, on his behalf (by = "owner:<name>").
    Approving publishes NOTHING: each item still goes through its own gate."""
    value = _read(client_id)
    sync = dict(value.get("sync") or {})
    if not sync.get("week"):
        raise LookupError("no weekly sync to approve")
    if sync.get("state") != "approved":
        sync.update(state="approved", approved_by=_clean(by, 60) or "client", approved_at=_now().isoformat())
        value["sync"] = sync
        _write(client_id, value)
    return sync


def exempt_from_escalation(client_id: int) -> bool:
    if int(client_id) in TRAINING_ACCOUNTS:
        return True
    try:
        from core import client_settings
        return client_settings.get(client_id).get("weekly_sync_escalation") is False
    except Exception:
        return False


def escalate_if_silent(client_id: int, now: datetime = None) -> bool:
    """A presented week nobody answered: ONE alert + ONE email to the owner.
    Nothing is published, approved or frozen by the silence."""
    now = now or _now()
    value = _read(client_id)
    sync = dict(value.get("sync") or {})
    if sync.get("state") != "waiting" or sync.get("escalated_at") or exempt_from_escalation(client_id):
        return False
    try:
        age = now - datetime.fromisoformat(str(sync.get("presented_at")))
    except ValueError:
        return False
    if age < timedelta(hours=ESCALATE_AFTER_HOURS):
        return False
    sync["escalated_at"] = now.isoformat()            # marked first: never two escalations for one week
    value["sync"] = sync
    _write(client_id, value)
    name = _client_name(client_id)
    line = (f"client {client_id} ({name}): the weekly sync of {sync.get('week')} was presented "
            f"{int(age.total_seconds() // 3600)} hours ago and is not approved — get the client to approve, "
            f"or approve on their behalf in the Control Room")
    _alert([line])
    try:
        _email_owner(f"סיכום שבועי לא אושר — {name}",
                     f"{name} (לקוח {client_id}) לא אישר את הסיכום השבועי של {sync.get('week')}.\n"
                     f"אפשר לפנות ללקוח, או לאשר בשמו בחדר הבקרה ← הלקוח ← ממשל.\n\n"
                     + "\n".join(sync_lines(sync.get("summary") or {}, "he")))
    except Exception as e:
        _alert([f"client {client_id}: the weekly-sync escalation email was not sent ({type(e).__name__})"])
    return True


def _clients() -> list:
    from agents.client_agent import list_clients
    return list_clients("active")


def run_due(now: datetime = None) -> dict:
    """The scheduler's tick (Thu–Sat evenings + the mornings after): present
    what is due, escalate what stayed silent. One client's failure never
    stops the others."""
    now = now or _now()
    out = {"presented": [], "escalated": [], "failed": []}
    for client in _clients():
        cid = int(client.get("id") or 0)
        if not cid:
            continue
        try:
            if escalate_if_silent(cid, now):
                out["escalated"].append(cid)
            if is_due(cid, now):
                present(cid, now)
                out["presented"].append(cid)
        except Exception as e:
            out["failed"].append(cid)
            _alert([f"client {cid}: the weekly sync did not run ({type(e).__name__}: {str(e)[:120]})"])
    return out


# ─── The views ───────────────────────────────────────────────────────────────

def client_view(client_id: int, lang: str = "he") -> dict:
    """The main page's block: credits in plain sight, the lanes on separate
    lines, the week waiting for approval, the day the client picked."""
    value = state(client_id)
    sync = value.get("sync") or {}
    return {"credits": credits(client_id), "lanes": lanes_report(client_id, 30),
            "sync_day": sync_day(client_id, value), "sync_days": list(SYNC_DAYS),
            "sync": {"week": sync.get("week", ""), "state": sync.get("state", ""), "presented_at": sync.get("presented_at", ""),
                     "approved_at": sync.get("approved_at", ""), "by_owner": str(sync.get("approved_by") or "").startswith("owner"),
                     "lines": sync_lines(sync.get("summary") or {}, lang) if sync.get("summary") else []},
            "overrides": overrides(client_id, days=90, value=value)[-10:][::-1],
            "authority": authority(client_id)["mode"]}


def admin_view(client_id: int) -> dict:
    value = state(client_id)
    sync = value.get("sync") or {}
    return {"authority": authority(client_id), "sync_day": sync_day(client_id, value),
            "sync": {k: sync.get(k, "") for k in ("week", "state", "presented_at", "approved_by", "approved_at", "escalated_at")},
            "sync_lines": sync_lines(sync.get("summary") or {}, "he") if sync.get("summary") else [],
            "sync_history": list(value.get("sync_history") or [])[-8:][::-1],
            "exempt": exempt_from_escalation(client_id),
            "overrides": overrides(client_id, value=value)[-30:][::-1],
            "lanes": lanes_report(client_id, 30), "credits": credits(client_id)}
