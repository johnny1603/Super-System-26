"""The Social tab: ONE approval queue for everything heading to social media
(2026-11-22, docs/SOCIAL-QUEUE.md).

ליאור produces, מאיה plans and publishes — and the business owner gives the
final yes HERE, nowhere else. Every item for a social channel lands in this
queue whoever made it (a weekly-plan item, a piece made from a chat request,
a media-library video the owner wants on TikTok/YouTube):

  waiting  → the owner approves (one item, or several in one approval card),
             edits the caption, changes the channels or the date, or rejects
             with a reason (the item goes back to the producing agent, who
             proposes the fixed version as a card in their chat)
  approved → "approved", NOT "publish now": on its planned date it goes to
             the channels where WE may publish automatically; everywhere else
             (today: every channel — content_team.AUTO_PUBLISH is all False,
             Meta verification and TikTok's review are not done) the row says
             "ready to publish in one tap": the file and caption are prepared
             and the owner publishes it himself
  published / rejected

THE GATE (`check`): every function that sends anything to a social channel —
meta_content_agent.publish, tiktok_content_agent.begin_upload (the inbox
upload, card and admin route alike), youtube_content_agent.publish (the chat
card, the admin route, the verification demo) — asks this module first and
refuses unless the file (or the item id) is APPROVED here for that channel.
There is no override parameter.

Files stay in the client's Drive folder exactly as before: an item holds the
Drive file id, nothing is copied. Storage: app_settings `social_queue:<id>`
(a small list per client). No migration.
"""
import re
import secrets
from datetime import date, datetime, timedelta, timezone

SERVICE_NAME = "social_queue"
PREFIX = "social_queue:"
LANGS = ("he", "en", "fr", "ar", "ru")
STATUSES = ("waiting", "approved", "published", "rejected")
CHANNELS = ("instagram", "facebook", "tiktok", "youtube")
KINDS = ("image", "video")
PRODUCERS = ("media", "meta")          # ליאור, מאיה (dashboard persona ids)
PAGE_SIZE = 6                          # gradual release: the rest behind "show more"
MAX_ITEMS = 300
CAPTION_MAX = 2200
REASON_MAX = 400
BATCH_MAX = 20
ID_RE = re.compile(r"^[a-z0-9]{6,40}$")
FILE_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
# Which channels take which kind of file
CHANNEL_KINDS = {"instagram": KINDS, "facebook": KINDS, "tiktok": ("video",), "youtube": ("video",)}


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table["en"]


STATUS_TEXT = {
    "waiting": _five("מחכה לאישור שלכם", "Waiting for your approval", "En attente de votre validation",
                     "بانتظار موافقتكم", "Ждёт вашего одобрения"),
    "approved": _five("אושר", "Approved", "Approuvé", "تمت الموافقة", "Одобрено"),
    "published": _five("פורסם", "Published", "Publié", "نُشر", "Опубликовано"),
    "rejected": _five("הוחזר לתיקון", "Sent back for a fix", "Renvoyé pour correction", "أُعيد للتعديل",
                      "Возвращено на доработку"),
}
# The state of ONE channel of an approved item
DELIVERY_TEXT = {
    "scheduled": _five("מאיה מפרסמת אוטומטית בתאריך שנקבע", "Maya publishes it automatically on the planned date",
                       "Maya le publie automatiquement à la date prévue", "مايا تنشره تلقائيًا في الموعد المحدد",
                       "Майя опубликует автоматически в запланированный день"),
    "one_tap": _five("מוכן לפרסום בלחיצה — הקובץ והכיתוב מוכנים, הפרסום עצמו אצלכם",
                     "Ready to publish in one tap — the file and caption are ready, you publish it",
                     "Prêt à publier en un geste — fichier et légende prêts, c'est vous qui publiez",
                     "جاهز للنشر بنقرة — الملف والنص جاهزان، والنشر عندكم",
                     "Готово к публикации одним нажатием — файл и подпись готовы, публикуете вы"),
    "not_connected": _five("הערוץ לא מחובר — אפשר להוריד את הקובץ ולפרסם בעצמכם, או לחבר אותו",
                           "This channel isn't connected — download the file and publish it yourself, or connect it",
                           "Ce canal n'est pas connecté — téléchargez le fichier et publiez-le vous-même, ou connectez-le",
                           "القناة غير مربوطة — نزّلوا الملف وانشروه بأنفسكم، أو اربطوها",
                           "Канал не подключён — скачайте файл и опубликуйте сами или подключите его"),
    "drafts": _five("נשלח לטיוטות שלכם — מחכה ללחיצת 'פרסם' שלכם באפליקציה",
                    "Sent to your drafts — waiting for your Post tap in the app",
                    "Envoyé dans vos brouillons — en attente de votre « Publier » dans l'application",
                    "أُرسل إلى مسوداتكم — بانتظار ضغطكم على 'نشر' في التطبيق",
                    "Отправлено в черновики — ждёт вашего нажатия «Опубликовать» в приложении"),
    "private": _five("הועלה כפרטי — מחכה לפרסום שלכם", "Uploaded as private — waiting for you to publish it",
                     "Mis en ligne en privé — en attente de votre publication", "رُفع كخاص — بانتظار نشركم له",
                     "Загружено как приватное — ждёт вашей публикации"),
    "done": _five("פורסם ✓", "Published ✓", "Publié ✓", "نُشر ✓", "Опубликовано ✓"),
}
TEXT = {
    "empty": _five(
        "כאן יופיע כל מה שמיועד לרשתות החברתיות — פוסטים ותמונות שליאור מפיק, פריטים מתוכנית התוכן של מאיה, ומה שתבקשו בצ'אט. שום דבר לא עולה לאף ערוץ לפני שתאשרו אותו כאן.",
        "Everything heading to social media appears here — posts and images Lior produces, items from Maya's content plan, and anything you ask for in the chat. Nothing goes to any channel before you approve it here.",
        "Tout ce qui part sur les réseaux sociaux apparaît ici — les publications et images de Lior, les éléments du plan de Maya et ce que vous demandez dans le chat. Rien ne part sur aucun canal avant votre validation ici.",
        "هنا يظهر كل ما هو موجّه لوسائل التواصل — المنشورات والصور التي ينتجها ليئور، عناصر خطة المحتوى لمايا، وكل ما تطلبونه في المحادثة. لا يذهب شيء إلى أي قناة قبل موافقتكم هنا.",
        "Здесь появляется всё, что идёт в соцсети, — посты и изображения Лиора, пункты контент-плана Майи и всё, что вы попросите в чате. Ничего не уходит ни в один канал до вашего одобрения здесь."),
    "gate": _five("שום דבר לא עולה לרשתות לפני האישור שלכם כאן. אישור = 'מאושר', לא 'לפרסם עכשיו'.",
                  "Nothing goes to social media before your approval here. Approving means 'approved', not 'publish now'.",
                  "Rien ne part sur les réseaux avant votre validation ici. Valider signifie « approuvé », pas « publier maintenant ».",
                  "لا يُنشر شيء قبل موافقتكم هنا. الموافقة تعني 'مُعتمد'، لا 'انشر الآن'.",
                  "Ничего не уходит в соцсети без вашего одобрения здесь. Одобрить — значит «одобрено», а не «опубликовать сейчас»."),
    "queued": _five(
        "\"{file}\" נכנס ללשונית \"סושיאל\" ומחכה לאישור שלכם. אחרי שתאשרו אותו שם, אפשר לשלוח אותו בלחיצה.",
        "\"{file}\" is now in the Social tab, waiting for your approval. Once you approve it there, it can be sent in one tap.",
        "« {file} » est dans l'onglet Social, en attente de votre validation. Une fois validé, il s'envoie en un geste.",
        "\"{file}\" موجود الآن في تبويب \"سوشيال\" بانتظار موافقتكم. بعد الموافقة هناك يمكن إرساله بنقرة.",
        "«{file}» теперь во вкладке «Соцсети» и ждёт вашего одобрения. После одобрения там его можно отправить одним нажатием."),
    "rejected_back": _five(
        "קיבלתי — החזרתם את \"{title}\" לתיקון: \"{reason}\". אני מתקן לפי זה, והגרסה החדשה תחזור ללשונית \"סושיאל\" לאישור שלכם.",
        "Got it — you sent \"{title}\" back for a fix: \"{reason}\". I'm fixing it accordingly, and the new version comes back to the Social tab for your approval.",
        "Bien reçu — vous avez renvoyé « {title} » pour correction : « {reason} ». Je corrige en conséquence, et la nouvelle version revient dans l'onglet Social pour validation.",
        "وصلني — أعدتم \"{title}\" للتعديل: \"{reason}\". أعدّله بناءً على ذلك، وستعود النسخة الجديدة إلى تبويب \"سوشيال\" لموافقتكم.",
        "Понял — вы вернули «{title}» на доработку: «{reason}». Исправляю, и новая версия вернётся во вкладку «Соцсети» на ваше одобрение."),
}


class QueueError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class GateClosed(Exception):
    """Nothing approved here for this file/item and channel. `code` is
    not_approved (no item / waiting / rejected) or already_published."""
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db():
    from agents.client_agent import _db as db
    return db()


def _today() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Jerusalem")).date()
    except Exception:
        return (_now() + timedelta(hours=2)).date()


# ─── Storage ─────────────────────────────────────────────────────────────────

def _load(client_id: int) -> list:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}")
            .limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    items = (value or {}).get("items") if isinstance(value, dict) else None
    return [i for i in (items or []) if isinstance(i, dict) and i.get("id")]


def _save(client_id: int, items: list):
    if len(items) > MAX_ITEMS:
        # the oldest FINISHED items go first; nothing waiting or approved is ever dropped
        done = sorted((i for i in items if i["status"] in ("published", "rejected")), key=lambda i: i["created_at"])
        drop = {i["id"] for i in done[:len(items) - MAX_ITEMS]}
        items = [i for i in items if i["id"] not in drop]
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": {"items": items},
                                        "updated_at": _now().isoformat()}, on_conflict="key").execute()


def items(client_id: int) -> list:
    try:
        return _load(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: queue unreadable ({type(e).__name__})")
        return []


def get(client_id: int, item_id: str) -> dict:
    return next((i for i in items(client_id) if i["id"] == str(item_id)), {})


def waiting_count(client_id: int) -> int:
    return sum(1 for i in items(client_id) if i["status"] == "waiting")


# ─── Validation ──────────────────────────────────────────────────────────────

def _clean_caption(value) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if len(text) > CAPTION_MAX:
        raise QueueError("caption_too_long")
    return text


def _clean_channels(value, kind: str) -> list:
    raw = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    out = []
    for c in raw:
        c = str(c or "").strip().lower()
        if c and c not in out:
            if c not in CHANNELS:
                raise QueueError("bad_channel")
            if kind not in CHANNEL_KINDS[c]:
                raise QueueError("channel_kind")      # an image can't go to TikTok/YouTube
            out.append(c)
    if not out:
        raise QueueError("no_channel")
    return out


def _clean_date(value) -> str:
    if not value:
        return (_today() + timedelta(days=1)).isoformat()
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        raise QueueError("bad_date")
    if d < _today() - timedelta(days=1) or d > _today() + timedelta(days=366):
        raise QueueError("bad_date")
    return d.isoformat()


def next_weekday(day_index: int, after: date = None) -> str:
    """The next date that falls on `day_index` (0 = Sunday … 6 = Saturday),
    today included — a plan item's day as a date."""
    after = after or _today()
    current = (after.weekday() + 1) % 7
    return (after + timedelta(days=(int(day_index) - current) % 7)).isoformat()


# ─── Adding (whoever produced it) ────────────────────────────────────────────

def add(client_id: int, file_id: str, kind: str, channels, caption: str = "", planned_date: str = "",
        producer: str = "media", source: str = "chat", title: str = "", replaces: str = "",
        plan_ref: dict = None) -> dict:
    """One item waiting for the owner. The same Drive file already waiting or
    approved is NOT queued twice (that item is returned)."""
    file_id = str(file_id or "").strip()
    if not FILE_RE.match(file_id):
        raise QueueError("bad_file")
    kind = kind if kind in KINDS else "image"
    channels = _clean_channels(channels, kind)
    queue = _load(client_id)
    for item in queue:
        if item["file_id"] == file_id and item["status"] in ("waiting", "approved"):
            return item
    item = {"id": secrets.token_hex(6), "file_id": file_id, "kind": kind, "channels": channels,
            "caption": _clean_caption(caption)[:CAPTION_MAX], "planned_date": _clean_date(planned_date),
            "producer": producer if producer in PRODUCERS else "media", "source": source,
            "title": re.sub(r"\s+", " ", str(title or "")).strip()[:120], "status": "waiting",
            "created_at": _now().isoformat(), "delivered": {}, "history": [{"at": _now().isoformat(), "do": "added"}]}
    if replaces and ID_RE.match(str(replaces)):
        item["replaces"] = str(replaces)
        for old in queue:
            if old["id"] == str(replaces):
                old["fixed_by"] = item["id"]
    if plan_ref:
        item["plan_ref"] = plan_ref
    queue.append(item)
    _save(client_id, queue)
    return item


def add_safely(client_id: int, *args, **kwargs) -> dict:
    """add() for the producers' background paths: never raises; a failure is
    alerted (an item that never reaches the queue can never be approved)."""
    try:
        return add(client_id, *args, **kwargs)
    except Exception as e:
        try:
            from core.agent_base import agent_alert
            agent_alert(SERVICE_NAME, [f"client {client_id}: an item could not be queued for approval "
                                       f"({type(e).__name__}: {str(e)[:150]})"])
        except Exception:
            pass
        return {}


# ─── The owner's decisions ───────────────────────────────────────────────────

def _log(item: dict, do: str, by: str, **extra):
    item.setdefault("history", []).append({"at": _now().isoformat(), "do": do, "by": by, **extra})
    item["history"] = item["history"][-20:]


def approve(client_id: int, ids, by: str = "client") -> list:
    """Approves the WAITING items among `ids` (one card can hold several).
    Returns the approved items; an id that isn't waiting is skipped."""
    ids = [str(i) for i in (ids if isinstance(ids, (list, tuple)) else [ids]) if ID_RE.match(str(i))]
    if not ids or len(ids) > BATCH_MAX:
        raise QueueError("bad_ids")
    queue = _load(client_id)
    done = []
    for item in queue:
        if item["id"] in ids and item["status"] == "waiting":
            item["status"], item["approved_at"], item["approved_by"] = "approved", _now().isoformat(), by
            _log(item, "approved", by)
            done.append(item)
    if done:
        _save(client_id, queue)
    return done


def edit(client_id: int, item_id: str, caption=None, channels=None, planned_date=None, by: str = "client") -> dict:
    """The caption, the channels, the date. Only before publishing; an edit to
    an APPROVED item puts it back to waiting — what was approved changed."""
    queue = _load(client_id)
    item = next((i for i in queue if i["id"] == str(item_id)), None)
    if not item:
        raise QueueError("not_found")
    if item["status"] not in ("waiting", "approved"):
        raise QueueError("not_editable")
    changed = []
    if caption is not None:
        new = _clean_caption(caption)
        if new != item["caption"]:
            item["caption"], changed = new, changed + ["caption"]
    if channels is not None:
        new = _clean_channels(channels, item["kind"])
        if new != item["channels"]:
            item["channels"], changed = new, changed + ["channels"]
    if planned_date is not None:
        new = _clean_date(planned_date)
        if new != item["planned_date"]:
            item["planned_date"], changed = new, changed + ["date"]
    if not changed:
        return item
    if item["status"] == "approved":
        item["status"] = "waiting"
        item.pop("approved_at", None)
    _log(item, "edited", by, fields=changed)
    _save(client_id, queue)
    return item


def reject(client_id: int, item_id: str, reason: str, by: str = "client", lang: str = "he") -> dict:
    """Back to the producing agent with the reason: a line in THEIR chat, and
    the fixed version proposed there as ONE card (it comes back here)."""
    reason = re.sub(r"\s+", " ", str(reason or "")).strip()
    if len(reason) < 2:
        raise QueueError("reason_required")
    reason = reason[:REASON_MAX]
    queue = _load(client_id)
    item = next((i for i in queue if i["id"] == str(item_id)), None)
    if not item:
        raise QueueError("not_found")
    if item["status"] not in ("waiting", "approved"):
        raise QueueError("not_rejectable")
    item["status"], item["reject_reason"], item["rejected_at"] = "rejected", reason, _now().isoformat()
    _log(item, "rejected", by, reason=reason)
    _save(client_id, queue)
    item["sent_back"] = send_back(client_id, item, lang)
    return item


def send_back(client_id: int, item: dict, lang: str = "he") -> dict:
    """The producer hears it in their own chat and proposes the fix card
    (produce_requested_content with the reason in the brief, replacing this
    item). {"posted": bool, "card": bool}."""
    from agents.client_agent import log_communication
    out = {"posted": False, "card": False}
    persona = item.get("producer") if item.get("producer") in PRODUCERS else "media"
    channel = f"dashboard_chat:{persona}"
    title = item.get("title") or pick(STATUS_TEXT["rejected"], lang)
    try:
        log_communication(client_id, "outbound", channel,
                          pick(TEXT["rejected_back"], lang).replace("{title}", title)
                          .replace("{reason}", item.get("reject_reason", "")))
        out["posted"] = True
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: send-back line failed ({type(e).__name__})")
    platform = next((c for c in item["channels"] if c in ("instagram", "facebook", "tiktok")), "instagram")
    brief = (f"{item.get('title') or 'The same piece'} — a new version. The owner sent it back: "
             f"{item.get('reject_reason', '')}")[:600]
    try:
        from core import agent_actions
        proposal = agent_actions.propose(
            client_id, "client_chat", persona,
            {"name": "produce_requested_content", "params": {"pieces": [
                {"kind": item["kind"], "brief": brief if len(brief) >= 10 else brief + " (fix)",
                 "platform": platform, "caption": item.get("caption", ""), "replaces": item["id"],
                 "channels": item["channels"], "date": item.get("planned_date", "")}]}},
            by_code=True, context={"language": lang})
        out["card"] = bool(proposal.get("card"))
        out["notice"] = proposal.get("notice") or proposal.get("refuse") or ""
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: fix card failed ({type(e).__name__})")
    if not out["card"]:
        try:
            from core.agent_base import agent_alert
            agent_alert(SERVICE_NAME, [f"client {client_id}: the owner rejected social item {item['id']} "
                                       f"(\"{item.get('reject_reason', '')[:120]}\") and no fix card could be "
                                       f"opened — the producer needs a look"])
        except Exception:
            pass
    return out


def mark_published(client_id: int, item_id: str, by: str = "client") -> dict:
    """The owner's "I published it" (the one-tap channels), or a channel's
    automatic publish."""
    queue = _load(client_id)
    item = next((i for i in queue if i["id"] == str(item_id)), None)
    if not item:
        raise QueueError("not_found")
    if item["status"] != "approved":
        raise QueueError("not_approved")
    item["status"], item["published_at"] = "published", _now().isoformat()
    for c in item["channels"]:
        item["delivered"][c] = {"how": "done", "at": _now().isoformat()}
    _log(item, "published", by)
    _save(client_id, queue)
    return item


# ─── THE GATE ────────────────────────────────────────────────────────────────

def check(client_id: int, channel: str, file_id: str = "", item_id: str = "") -> dict:
    """The approved item that lets `file_id` / `item_id` go to `channel`, or
    GateClosed. Every social publisher calls this BEFORE any platform call."""
    if channel not in CHANNELS:
        raise GateClosed("not_approved", f"unknown channel {channel}")
    try:
        queue = _load(client_id)
    except Exception as e:
        raise GateClosed("not_approved", f"queue unreadable ({type(e).__name__}) — nothing is sent")
    match = [i for i in queue
             if (item_id and i["id"] == str(item_id)) or (file_id and not item_id and i["file_id"] == str(file_id))]
    approved = [i for i in match if i["status"] == "approved" and channel in i["channels"]]
    if approved:
        item = approved[0]
        if (item.get("delivered") or {}).get(channel, {}).get("how") == "done":
            raise GateClosed("already_published", f"item {item['id']} is already published on {channel}")
        return item
    states = sorted({i["status"] for i in match}) or ["none"]
    raise GateClosed("not_approved", f"no item approved for {channel} in the Social tab ({', '.join(states)})")


def mark_delivered(client_id: int, channel: str, how: str, file_id: str = "", item_id: str = ""):
    """After a send succeeded: TikTok drafts ("drafts"), a private YouTube
    upload ("private"), a real publish ("done"). Never raises."""
    try:
        queue = _load(client_id)
        item = next((i for i in queue if i["status"] == "approved" and channel in i["channels"]
                     and ((item_id and i["id"] == item_id) or (file_id and i["file_id"] == file_id))), None)
        if not item:
            return
        item["delivered"][channel] = {"how": how, "at": _now().isoformat()}
        if all((item["delivered"].get(c) or {}).get("how") == "done" for c in item["channels"]):
            item["status"], item["published_at"] = "published", _now().isoformat()
        _log(item, f"delivered:{channel}:{how}", "system")
        _save(client_id, queue)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: delivery not recorded ({type(e).__name__})")


# ─── What the tab shows ──────────────────────────────────────────────────────

def delivery(item: dict, modes: dict) -> dict:
    """{channel: state} for an approved/published item: scheduled (we publish
    on the date) | one_tap | not_connected | drafts | private | done."""
    out = {}
    for c in item["channels"]:
        how = ((item.get("delivered") or {}).get(c) or {}).get("how")
        if item["status"] == "published" or how == "done":
            out[c] = "done"
        elif how in ("drafts", "private"):
            out[c] = how
        else:
            mode = modes.get(c, "not_connected")
            out[c] = {"auto": "scheduled", "client_publishes": "one_tap"}.get(mode, "not_connected")
    return out


def _order_key(item: dict):
    """Waiting first, then approved items still needing the owner's tap, then
    the rest; inside each, the nearest planned date first (overdue on top)."""
    rank = {"waiting": 0, "approved": 1, "rejected": 2, "published": 3}[item["status"]]
    stamp = item.get("planned_date") or "9999-12-31"
    if item["status"] in ("published", "rejected"):
        return (rank, "".join(chr(255 - ord(ch)) for ch in (item.get("published_at") or item.get("rejected_at")
                                                             or item["created_at"])))
    return (rank, stamp, item["created_at"])


def _modes(client_id: int) -> dict:
    try:
        from core import content_team
        return content_team.channel_modes(client_id)
    except Exception:
        return {}


def view(client_id: int, lang: str = None, offset: int = 0, limit: int = PAGE_SIZE, status: str = "") -> dict:
    """The tab: a page of rows, ordered by status, planned date and urgency."""
    from core import client_language as _cl
    try:
        lang = _cl.resolve(client_id, lang)
    except Exception:
        lang = lang or "he"
    lang = lang if lang in LANGS else "en"
    try:
        queue = _load(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: queue unreadable ({type(e).__name__})")
        return {"available": False}
    counts = {s: sum(1 for i in queue if i["status"] == s) for s in STATUSES}
    counts["needs_tap"] = 0
    modes = _modes(client_id)
    rows = sorted((i for i in queue if not status or i["status"] == status), key=_order_key)
    today = _today().isoformat()
    out = []
    for item in rows:
        dl = delivery(item, modes) if item["status"] in ("approved", "published") else {}
        needs_tap = item["status"] == "approved" and any(v in ("one_tap", "not_connected", "drafts", "private")
                                                         for v in dl.values())
        out.append({"id": item["id"], "file_id": item["file_id"], "kind": item["kind"], "title": item.get("title", ""),
                    "caption": item.get("caption", ""), "channels": item["channels"],
                    "planned_date": item.get("planned_date", ""), "overdue": item["status"] in ("waiting", "approved")
                    and (item.get("planned_date") or today) < today,
                    "producer": item.get("producer", "media"), "source": item.get("source", ""),
                    "status": item["status"], "status_text": pick(STATUS_TEXT[item["status"]], lang),
                    "delivery": {c: {"state": s, "text": pick(DELIVERY_TEXT[s], lang)} for c, s in dl.items()},
                    "needs_tap": needs_tap, "reject_reason": item.get("reject_reason", ""),
                    "fixed_by": item.get("fixed_by", ""), "replaces": item.get("replaces", "")})
    counts["needs_tap"] = sum(1 for r in out if r["needs_tap"])
    offset, limit = max(0, int(offset or 0)), max(1, min(int(limit or PAGE_SIZE), 50))
    return {"available": True, "counts": counts, "total": len(out), "items": out[offset:offset + limit],
            "has_more": offset + limit < len(out), "next_offset": offset + limit,
            "empty": pick(TEXT["empty"], lang), "gate": pick(TEXT["gate"], lang),
            "channels": list(CHANNELS), "channel_kinds": {c: list(k) for c, k in CHANNEL_KINDS.items()}}


def all_pending() -> list:
    """The admin Control Room: every client's queue in one place —
    [{client_id, waiting, needs_tap, next_date}] for clients with anything open."""
    rows = (_db().table("app_settings").select("key,value").like("key", f"{PREFIX}%").limit(2000)
            .execute().data or [])
    modes_cache = {}
    out = []
    for row in rows:
        try:
            client_id = int(str(row.get("key", "")).split(":", 1)[1])
        except (IndexError, ValueError):
            continue
        queue = [i for i in ((row.get("value") or {}).get("items") or []) if isinstance(i, dict)]
        waiting = [i for i in queue if i.get("status") == "waiting"]
        approved = [i for i in queue if i.get("status") == "approved"]
        if not waiting and not approved:
            continue
        if client_id not in modes_cache:
            modes_cache[client_id] = _modes(client_id)
        needs_tap = sum(1 for i in approved if any(v != "scheduled" for v in delivery(i, modes_cache[client_id]).values()))
        dates = sorted(i.get("planned_date") or "" for i in waiting + approved if i.get("planned_date"))
        out.append({"client_id": client_id, "waiting": len(waiting), "approved": len(approved),
                    "needs_tap": needs_tap, "next_date": dates[0] if dates else ""})
    return sorted(out, key=lambda r: (-r["waiting"], r["next_date"] or "9999"))


def prompt_block(client_id: int) -> dict:
    """`social_queue` in the agents' payload: counts only, and the rule."""
    queue = items(client_id)
    waiting = [i for i in queue if i["status"] == "waiting"]
    return {"waiting": len(waiting), "approved": sum(1 for i in queue if i["status"] == "approved"),
            "waiting_items": [{"id": i["id"], "title": i.get("title", ""), "channels": i["channels"],
                               "planned_date": i.get("planned_date", "")} for i in waiting[:10]]}
