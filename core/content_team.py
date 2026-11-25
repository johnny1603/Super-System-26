"""מאיה and ליאור as ONE team: the weekly content plan (2026-11-15,
docs/CONTENT-TEAM.md).

One flow, one object, one approval:
  מאיה proposes the week (what, where, which day, the caption)
  → the client approves the WHOLE plan in ONE card (`approve_content_plan`)
  → ליאור produces every item (the existing media_agent functions, on the
    shared brand kit and into the shared media library)
  → מאיה holds the schedule: each item has its day and its caption, and on
    that day it is "ready to publish".

PUBLISHING — the honest state, visible on every item of the plan:
`AUTO_PUBLISH` says where WE may publish for a client today. Every channel is
False: Meta's verification is not finished and TikTok has not approved the
app. So nothing is auto-published; each item is prepared and scheduled and the
CLIENT publishes it (mode `client_publishes`). There is deliberately NO
auto-publish code path here — flipping a constant must not be enough to post
on a client's page; that path gets built and tested when a channel is approved.

Research is inspiration only: the planning prompt and the agents' rule forbid
copying a competitor's ad, creative or trademarked material.

Storage: app_settings `content_plan:<id>` (the current plan only). No
migration. Never a second plan while one is open and recent.
"""
import re
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "content_team"
PREFIX = "content_plan:"
LANGS = ("he", "en", "fr", "ar", "ru")
MAX_ITEMS = 3
OPEN_DAYS = 5                      # a proposed plan stays "the" plan this long (one Saturday run a week)
SHOW_DAYS = 9                      # an approved plan is shown through its week
FORMATS = ("image", "video", "self_filmed")
PLATFORMS = ("instagram", "facebook", "tiktok", "youtube", "website")

# Where uallak itself may publish for a client TODAY. All False (2026-11-15):
# Meta verification unfinished, TikTok app not approved, YouTube uploads are
# Private by design. Changing one is the owner's decision AND needs the publish
# path built and tested — see the module docstring.
AUTO_PUBLISH = {"instagram": False, "facebook": False, "tiktok": False, "youtube": False}
# client_accounts platform → the plan's channel
ACCOUNT_CHANNELS = {"meta_instagram": "instagram", "meta_page": "facebook", "tiktok": "tiktok", "youtube": "youtube"}


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


DAYS = {"he": ["יום ראשון", "יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "שבת"],
        "en": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
        "fr": ["Dimanche", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"],
        "ar": ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"],
        "ru": ["Воскресенье", "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]}
CHANNEL_TEXT = {
    "instagram": _five("אינסטגרם", "Instagram", "Instagram", "إنستغرام", "Instagram"),
    "facebook": _five("פייסבוק", "Facebook", "Facebook", "فيسبوك", "Facebook"),
    "tiktok": _five("טיקטוק", "TikTok", "TikTok", "تيك توك", "TikTok"),
    "youtube": _five("יוטיוב", "YouTube", "YouTube", "يوتيوب", "YouTube"),
    "website": _five("האתר", "Website", "Site web", "الموقع", "Сайт"),
}
FORMAT_TEXT = {
    "image": _five("תמונה", "Image", "Image", "صورة", "Изображение"),
    "video": _five("סרטון", "Video", "Vidéo", "فيديو", "Видео"),
    "self_filmed": _five("סרטון שאתם מצלמים, עם ערכת צילום מאיתנו", "A clip you film, with our filming kit",
                         "Une vidéo que vous filmez, avec notre kit de tournage", "مقطع تصوّرونه أنتم مع عدّة تصوير منا",
                         "Ролик, который снимаете вы, с нашим съёмочным набором"),
}
MODE_TEXT = {
    "auto": _five("מתפרסם אוטומטית ביום שנקבע", "Published automatically on its day",
                  "Publié automatiquement le jour prévu", "يُنشر تلقائيًا في يومه", "Публикуется автоматически в свой день"),
    "client_publishes": _five("אנחנו מכינים ומתזמנים, אתם מפרסמים בלחיצה", "We prepare and schedule it, you publish in one tap",
                              "Nous préparons et planifions, vous publiez en un geste",
                              "نحن نجهّز ونجدول، وأنتم تنشرون بنقرة", "Мы готовим и планируем, вы публикуете одним нажатием"),
    "not_connected": _five("הערוץ עוד לא מחובר: אנחנו מכינים, והקובץ והכיתוב מחכים לכם בספריית המדיה",
                           "This channel isn't connected yet: we prepare it, and the file and caption wait in your media library",
                           "Ce canal n'est pas encore connecté : nous préparons, le fichier et la légende vous attendent dans la médiathèque",
                           "هذه القناة غير مربوطة بعد: نحن نجهّز، والملف والنص بانتظاركم في مكتبة الوسائط",
                           "Канал ещё не подключён: мы готовим, файл и подпись ждут вас в медиатеке"),
    "library": _five("נכנס לספריית המדיה שלכם", "Goes into your media library", "Va dans votre médiathèque",
                     "يدخل إلى مكتبة الوسائط", "Попадает в вашу медиатеку"),
}
TEXT = {
    "why_manual": _five(
        "למה אתם מפרסמים בעצמכם: האישורים של הרשתות לפרסום אוטומטי בשמכם עוד בתהליך. עד אז שום דבר לא עולה בלי הלחיצה שלכם.",
        "Why you publish yourselves: the networks' approvals for us to publish automatically on your behalf are still in process. Until then nothing goes up without your tap.",
        "Pourquoi vous publiez vous-mêmes : les autorisations des réseaux pour publier automatiquement en votre nom sont encore en cours. D'ici là, rien n'est mis en ligne sans votre geste.",
        "لماذا تنشرون بأنفسكم: موافقات الشبكات على النشر التلقائي باسمكم ما زالت قيد الإجراء. حتى ذلك الحين لا يُنشر شيء دون نقرتكم.",
        "Почему публикуете вы: разрешения соцсетей на автоматическую публикацию от вашего имени ещё оформляются. До тех пор ничего не выходит без вашего нажатия."),
    "team": _five(
        "מאיה תכננה את השבוע, ליאור מפיק את החומרים בצבעים ובסגנון של המותג שלכם, ומאיה מחזיקה את הלוח.",
        "Maya planned the week, Lior produces the pieces in your brand's colours and style, and Maya keeps the schedule.",
        "Maya a planifié la semaine, Lior produit les contenus aux couleurs et au style de votre marque, et Maya tient le calendrier.",
        "مايا خططت للأسبوع، ليئور ينتج المواد بألوان وأسلوب علامتكم، ومايا تمسك بالجدول.",
        "Майя спланировала неделю, Лиор готовит материалы в цветах и стиле вашего бренда, а Майя ведёт расписание."),
    "caption": _five("כיתוב", "Caption", "Légende", "النص", "Подпись"),
    "nudge": _five(
        "שבוע טוב 🌙 תוכנית התוכן לשבוע הקרוב מוכנה, והיא מחכה לכם בכרטיס אחד כאן. אישור אחד, ואנחנו מתחילים להפיק את הכול. רוצים לשנות משהו? כתבו לי כאן.",
        "Good week 🌙 Next week's content plan is ready and waiting for you in one card here. One approval and we start producing all of it. Want something changed? Write to me here.",
        "Bonne semaine 🌙 Le plan de contenu de la semaine prochaine est prêt, dans une seule carte ici. Une validation et nous produisons le tout. Un changement ? Écrivez-moi ici.",
        "أسبوع طيب 🌙 خطة محتوى الأسبوع القادم جاهزة وتنتظركم في بطاقة واحدة هنا. موافقة واحدة ونبدأ إنتاج كل شيء. تريدون تغييرًا؟ اكتبوا لي هنا.",
        "Хорошей недели 🌙 План контента на следующую неделю готов и ждёт вас в одной карточке здесь. Одно одобрение — и мы начинаем готовить всё. Хотите что-то изменить? Напишите мне здесь."),
}
STATUS_TEXT = {
    "proposed": _five("מחכה לאישור שלכם", "Waiting for your approval", "En attente de votre validation",
                      "بانتظار موافقتكم", "Ждёт вашего одобрения"),
    "in_production": _five("בהפקה", "In production", "En production", "قيد الإنتاج", "В работе"),
    "ready": _five("מוכן — מחכה לאישור שלכם בלשונית \"סושיאל\"", "Ready — waiting for your approval in the Social tab",
                   "Prêt — en attente de votre validation dans l'onglet Social",
                   "جاهز — بانتظار موافقتكم في تبويب \"سوشيال\"", "Готово — ждёт вашего одобрения во вкладке «Соцсети»"),
    "kit_sent": _five("ערכת הצילום נשלחה", "Filming kit sent", "Kit de tournage envoyé", "أُرسلت عدّة التصوير",
                      "Съёмочный набор отправлен"),
    "needs_attention": _five("מתעכב, הצוות מטפל", "Delayed, the team is on it", "Retardé, l'équipe s'en occupe",
                             "متأخر، الفريق يعالجه", "Задерживается, команда занимается"),
}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table["en"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db():
    from agents.client_agent import _db as db
    return db()


def _alert(message: str):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, [message])


# ─── Channels: where it can be published, honestly ───────────────────────────

def _connected_channels(client_id: int) -> set:
    from agents.client_agent import get_accounts
    return {ACCOUNT_CHANNELS[a.get("platform")] for a in (get_accounts(client_id) or [])
            if a.get("platform") in ACCOUNT_CHANNELS and (a.get("status") or "active") == "active"}


def channel_modes(client_id: int) -> dict:
    """{channel: mode}. `auto` only where WE are approved to publish AND the
    channel is connected — nowhere today. A read failure = nothing connected
    is claimed (`not_connected` is the cautious answer)."""
    try:
        connected = _connected_channels(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: connections unreadable ({type(e).__name__})")
        connected = set()
    out = {"website": "library"}
    for channel, approved in AUTO_PUBLISH.items():
        out[channel] = "not_connected" if channel not in connected else ("auto" if approved else "client_publishes")
    return out


# ─── The plan ────────────────────────────────────────────────────────────────

def _line(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def clean_items(raw) -> list:
    """The planner's items → what we store. Anything malformed is dropped."""
    items = []
    for item in (raw if isinstance(raw, list) else [])[:MAX_ITEMS * 2]:
        if not isinstance(item, dict):
            continue
        title, body = _line(item.get("title"), 90), _line(item.get("body"), 400)
        if not title or not body:
            continue
        try:
            day = int(item.get("day"))
        except (TypeError, ValueError):
            day = -1
        items.append({"title": title, "body": body,
                      "format": item.get("format") if item.get("format") in FORMATS else "image",
                      "platform": item.get("platform") if item.get("platform") in PLATFORMS else "instagram",
                      "day": day if 0 <= day <= 6 else len(items) * 2 % 6,       # spread over the week when unreadable
                      "caption": str(item.get("caption") or "").strip()[:600],
                      "event_slug": _line(item.get("event_slug"), 60), "status": "proposed"})
    return sorted(items[:MAX_ITEMS], key=lambda i: i["day"])


def latest(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}")
            .limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) and value.get("items") else {}


def _save(client_id: int, value: dict):
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": value,
                                        "updated_at": _now().isoformat()}, on_conflict="key").execute()
    _block_cache.pop(int(client_id), None)


def _age_days(value: dict) -> float:
    try:
        at = datetime.fromisoformat(str(value.get("created_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 10 ** 6
    return (_now() - at) / timedelta(days=1)


def open_plan(client_id: int) -> dict:
    """The plan still waiting for the client's decision ({} when none)."""
    value = latest(client_id)
    return value if value.get("status") == "proposed" and _age_days(value) < OPEN_DAYS else {}


def current(client_id: int) -> dict:
    """The plan worth showing: proposed and recent, or approved this week."""
    value = latest(client_id)
    if not value:
        return {}
    limit = OPEN_DAYS if value.get("status") == "proposed" else SHOW_DAYS
    return value if value.get("status") in ("proposed", "approved") and _age_days(value) < limit else {}


def store(client_id: int, items: list, lang: str = None, source: str = "weekly") -> dict:
    # The planner is a model: no tool or vendor name reaches the plan the client reads
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    from core import vendor_names
    if isinstance(items, list):
        vendor_names.scrub_tree(items, lang if lang in LANGS else "he")
    items = clean_items(items)
    if not items:
        return {}
    value = {"id": _now().strftime("%Y%m%d%H%M%S"), "status": "proposed", "created_at": _now().isoformat(),
             "language": lang if lang in LANGS else "he", "source": source, "items": items}
    _save(client_id, value)
    return value


def lines(value: dict, lang: str, modes: dict) -> str:
    """The plan as the card shows it: one item per line, with its day, channel
    and how it gets published."""
    lang = lang if lang in LANGS else "en"
    out = []
    for item in value.get("items") or []:
        mode = modes.get(item["platform"], "not_connected")
        out.append(f'• {DAYS[lang][item["day"]]} · {pick(CHANNEL_TEXT[item["platform"]], lang)} · '
                   f'{pick(FORMAT_TEXT[item["format"]], lang)}: {item["title"]}. {item["body"]} '
                   f'({pick(MODE_TEXT[mode], lang)})')
    return "\n".join(out)


def plan_persona(client_id: int) -> str:
    """Whose chat carries the plan: מאיה's when the package has her channels,
    otherwise ליאור's. Either way it is the same plan and the same card."""
    try:
        from core.client_journey import _chosen_package
        services = " ".join(str(s).lower() for s in (_chosen_package(client_id) or {}).get("recommended_services") or [])
    except Exception:
        services = ""
    return "meta" if any(token in services for token in ("meta", "facebook", "instagram", "social")) else "media"


def propose(client_id: int, items: list, lang: str = None, source: str = "weekly", persona: str = "") -> dict:
    """Stores the plan and opens its ONE approval card. {"plan", "card"} —
    card None when the card could not be opened (the caller falls back)."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    from core import agent_actions
    value = store(client_id, items, lang, source)
    if not value:
        return {"plan": {}, "card": None}
    persona = persona or plan_persona(client_id)
    out = agent_actions.propose(client_id, "client_chat", persona,
                                {"name": "approve_content_plan", "params": {"plan": value["id"]}}, by_code=True)
    value["persona"] = persona
    if out.get("card"):
        value["card_id"] = (out["card"] or {}).get("id")
    _save(client_id, value)
    return {"plan": value, "card": out.get("card"), "error": out.get("error", "")}


class PlanError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def approve(client_id: int, plan_id: str) -> dict:
    value = latest(client_id)
    if not value or value.get("id") != str(plan_id):
        raise PlanError("plan_missing")
    if value.get("status") != "proposed":
        raise PlanError("already_decided")
    value["status"], value["approved_at"] = "approved", _now().isoformat()
    for item in value["items"]:
        item["status"] = "in_production"
    _save(client_id, value)
    return value


def decline(client_id: int, plan_id: str):
    value = latest(client_id)
    if value and value.get("id") == str(plan_id) and value.get("status") == "proposed":
        value["status"], value["declined_at"] = "declined", _now().isoformat()
        _save(client_id, value)


def _produce_item(client_id: int, item: dict) -> dict:
    """ליאור's part — the SAME media_agent functions the old per-item approval ran."""
    from agents import media_agent
    brief = f'{item["title"]} — {item["body"]}'
    if item["format"] == "self_filmed":
        return media_agent.create_filming_kit(client_id, item["title"])
    platform = item["platform"] if item["platform"] in ("instagram", "facebook", "tiktok") else "instagram"
    if item["format"] == "video":
        return media_agent.generate_video(client_id, brief, platform=platform)
    return media_agent.generate_image(client_id, brief, platform=item["platform"] if item["platform"] == "website" else platform)


def _queue_for_approval(client_id: int, plan_id: str, index: int, item: dict):
    """A produced plan item for a social channel → the owner's Social tab
    (core/social_queue): approving the PLAN started production; the owner's
    final yes on the finished file is given there. The plan's day becomes its
    planned date; the caption is the plan's."""
    from core import social_queue
    kind = "video" if item["format"] == "video" else "image"
    if item["format"] == "self_filmed" or not item.get("file_id") or item["platform"] not in social_queue.CHANNELS \
            or kind not in social_queue.CHANNEL_KINDS[item["platform"]]:
        return
    try:
        planned = social_queue.next_weekday(int(item.get("day")))
    except (TypeError, ValueError):
        planned = ""                          # the queue's default: tomorrow
    social_queue.add_safely(client_id, item["file_id"], kind, [item["platform"]], caption=item.get("caption", ""),
                            planned_date=planned, producer="media", source="plan",
                            title=item["title"], plan_ref={"plan": str(plan_id), "index": index})


def produce(client_id: int, plan_id: str):
    """Runs in the BACKGROUND after the approval: every item, one by one, each
    saved as it finishes. One failed item never stops the others; the team is
    alerted once with what did not come out."""
    from core import governance
    with governance.lane_scope("system"):              # the weekly plan IS the system lane (items and costs)
        return _produce_plan(client_id, plan_id)


def _produce_plan(client_id: int, plan_id: str):
    failed = []
    value = latest(client_id)
    if not value or value.get("id") != str(plan_id) or value.get("status") != "approved":
        return
    for index, item in enumerate(value["items"]):
        if item.get("status") != "in_production":
            continue
        try:
            result = _produce_item(client_id, item) or {}
            ok = bool(result.get("success"))
        except Exception as e:
            result, ok = {"errors": [type(e).__name__]}, False
        fresh = latest(client_id)                      # the client may have talked to us meanwhile
        if not fresh or fresh.get("id") != str(plan_id):
            return
        target = fresh["items"][index]
        if ok:
            target["status"] = "kit_sent" if item["format"] == "self_filmed" else "ready"
            target["file_id"] = result.get("file_id") or ""
            _queue_for_approval(client_id, plan_id, index, target)
        else:
            target["status"] = "needs_attention"
            failed.append(item["title"])
        _save(client_id, fresh)
    if failed:
        _alert(f"client {client_id}: the approved weekly content plan has {len(failed)} item(s) that were not "
               f"produced ({'; '.join(failed)[:300]}) — needs a look")


# ─── What the dashboard and the agents read ──────────────────────────────────

def _today_index() -> int:
    """0 = Sunday … 6 = Saturday, in Israel."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Jerusalem"))
    except Exception:
        now = _now() + timedelta(hours=2)
    return (now.weekday() + 1) % 7


def view(client_id: int, lang: str = None) -> dict:
    """The plan for the Media tab; never raises."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    lang = lang if lang in LANGS else "en"
    try:
        value = current(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: plan unreadable ({type(e).__name__})")
        return {"available": False}
    if not value:
        return {"available": True, "has_plan": False}
    modes, today = channel_modes(client_id), _today_index()
    items = []
    for item in value["items"]:
        mode = modes.get(item["platform"], "not_connected")
        items.append({"title": item["title"], "body": item["body"], "caption": item.get("caption", ""),
                      "day": DAYS[lang][item["day"]], "is_today": item["day"] == today,
                      "channel": pick(CHANNEL_TEXT[item["platform"]], lang), "format": pick(FORMAT_TEXT[item["format"]], lang),
                      "mode": mode, "mode_text": pick(MODE_TEXT[mode], lang),
                      "status": item.get("status", "proposed"),
                      "status_text": pick(STATUS_TEXT.get(item.get("status"), STATUS_TEXT["proposed"]), lang)})
    return {"available": True, "has_plan": True, "status": value["status"], "card_id": value.get("card_id"),
            "persona": value.get("persona") or "media", "items": items,
            "note": pick(TEXT["why_manual"], lang) if any(i["mode"] != "auto" for i in items) else "",
            "team": pick(TEXT["team"], lang)}


_block_cache = {}    # client_id -> (at, block): a chat turn must not wait on these reads
BLOCK_CACHE_SECONDS = 60


def prompt_block(client_id: int) -> dict:
    """`content_plan` in every agent's payload ({} when unreadable): the plan,
    each item's state, and where publishing really stands."""
    import time
    hit = _block_cache.get(int(client_id))
    if hit and hit[0] > time.time() - BLOCK_CACHE_SECONDS:
        return hit[1]
    data = view(client_id, "en")
    if not data.get("available"):
        return {}
    modes = channel_modes(client_id)
    out = {"has_plan": bool(data.get("has_plan")),
           "publishing_by_channel": {c: m for c, m in modes.items() if c != "website"},
           "auto_publishing_available_anywhere": any(m == "auto" for m in modes.values())}
    try:
        from core import social_queue
        out["social_queue"] = social_queue.prompt_block(client_id)
    except Exception:
        pass
    if data.get("has_plan"):
        out["status"] = data["status"]
        out["items"] = [{k: i[k] for k in ("title", "day", "is_today", "channel", "format", "mode", "status", "caption")}
                        for i in data["items"]]
    _block_cache[int(client_id)] = (time.time(), out)
    return out
