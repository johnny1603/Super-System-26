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

COPY BEFORE PIXELS (2026-11-23, core/post_brief, core/post_pipeline): a post
made through the contract first lands here as ONE item of kind "post" — no
file, just the copy slide by slide, the caption, the format and its reason,
the audience palette, flags and warnings. The owner edits the copy
(`edit_copy`) and approves it; approving it is what starts production. A
"post" item is never publishable: the gate refuses it, whatever its status.
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
POST_KIND = "post"                     # the copy stage of a contract post (no file)
DESIGN_KIND = "design"                 # a text-slide design under review (core/slide_design; no file)
NO_FILE_KINDS = (POST_KIND, DESIGN_KIND)   # never publishable: the gate refuses them
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
        plan_ref: dict = None, flags: list = None, post_ref: dict = None, real_asset: dict = None,
        characters: list = None) -> dict:
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
    if flags:
        item["flags"] = [f for f in flags if f in FLAG_TEXT][:6]
    if post_ref:
        item["post_ref"] = post_ref        # {"post_id", "set_id", "asset_id", "n", "of"}
    if characters:
        # The agents' character images in it (core/agent_characters): on approval
        # they enter the pose library, tagged
        item["characters"] = [{"agent": str(c.get("agent") or "")[:20], "pose_id": str(c.get("pose_id") or "")[:20]}
                              for c in characters if isinstance(c, dict)][:4]
    if real_asset:
        # A REAL file from the client's connected Drive folder (core/drive_assets):
        # the card shows its folder + role
        item["real_asset"] = {k: str(real_asset.get(k) or "")[:300] for k in REAL_ASSET_KEYS}
    queue.append(item)
    _save(client_id, queue)
    return item


# ─── The copy stage of a contract post (core/post_pipeline) ──────────────────

FLAG_TEXT = {
    "concept": _five("תמונה רעיונית שנוצרה במחשב — לא מוצר אמיתי שאפשר להזמין",
                     "A generated concept image — not a real product that can be ordered",
                     "Image conceptuelle générée — pas un vrai produit commandable",
                     "صورة مفاهيمية مُولَّدة — ليست منتجًا حقيقيًا يمكن طلبه",
                     "Сгенерированное концептуальное изображение — не настоящий товар для заказа"),
    "escalated": _five("הסוכנים לא הסכימו על החלק הזה — ההחלטה שלכם",
                       "The agents disagreed on this part — it's your call",
                       "Les agents ne sont pas d'accord sur cette partie — à vous de décider",
                       "اختلف الوكلاء على هذا الجزء — القرار لكم",
                       "Агенты не сошлись в этой части — решать вам"),
    "text_slide_pending": _five("שקופית טקסט: נכתבת בגופן אמיתי לפי עיצוב המותג (העיצוב יגיע לאישורכם) — לא נשלחת למודל תמונות",
                                "Text slide: drawn in a real font from the brand design (the design comes to you for approval) — never sent to an image model",
                                "Diapositive texte : dessinée avec une vraie police selon le design de marque (soumis à votre accord) — jamais envoyée à un modèle d'image",
                                "شريحة نصية: تُكتب بخط حقيقي حسب تصميم العلامة (يصلكم التصميم للموافقة) — لا تُرسل إلى نموذج صور",
                                "Текстовый слайд: набирается настоящим шрифтом по дизайну бренда (дизайн придёт на одобрение) — не отправляется в модель изображений"),
    "ai_person_forbidden": _five("שימו לב: תקציר המותג שלכם אומר בלי אנשים שנוצרו ב-AI — והפוסט הזה כולל דמות של סוכן שנוצרה ב-AI. ההחלטה שלכם.",
                                 "Note: your brand brief says no AI-generated people — this post includes an AI-generated agent character. Your call.",
                                 "Attention : votre brief de marque exclut les personnes générées par IA — ce post contient un personnage d'agent généré par IA. À vous de décider.",
                                 "انتبهوا: موجز العلامة يقول بلا أشخاص مولَّدين بالذكاء الاصطناعي — وهذا المنشور فيه شخصية وكيل مولَّدة. القرار لكم.",
                                 "Внимание: в брифе бренда сказано «без людей, созданных ИИ» — в этом посте есть персонаж-агент, созданный ИИ. Решать вам."),
    "ai_label": _five("כולל דמות שנוצרה ב-AI: ברוב הרשתות צריך לסמן את הפוסט כתוכן AI כשמפרסמים",
                      "Includes an AI-generated character: most platforms need the post marked as AI content when you publish",
                      "Contient un personnage généré par IA : la plupart des réseaux demandent de marquer le post comme contenu IA",
                      "يتضمن شخصية مولَّدة بالذكاء الاصطناعي: معظم المنصات تطلب وسم المنشور كمحتوى AI عند النشر",
                      "Есть персонаж, созданный ИИ: большинство платформ требуют пометить пост как ИИ-контент при публикации"),
    "character_overflow": _five("הדמות יוצאת מגבולות השקופית — חלק ממנה ייחתך. אפשר לבקש להזיז או להקטין אותה.",
                                "The character runs past the slide's edge — part of it is cut off. Ask to move or shrink it.",
                                "Le personnage dépasse le bord de la diapositive — une partie est coupée. Demandez à le déplacer ou le réduire.",
                                "الشخصية تتجاوز حدود الشريحة — جزء منها مقصوص. اطلبوا تحريكها أو تصغيرها.",
                                "Персонаж выходит за край слайда — часть обрезана. Попросите сдвинуть или уменьшить его."),
    "character_text_overlap": _five("הדמות נוגעת בטקסט — כדאי לבדוק שהכול קריא, או לבקש להזיז אותה.",
                                    "The character touches the text — check it all reads well, or ask to move it.",
                                    "Le personnage touche le texte — vérifiez la lisibilité, ou demandez à le déplacer.",
                                    "الشخصية تلامس النص — تأكدوا أن كل شيء مقروء، أو اطلبوا تحريكها.",
                                    "Персонаж задевает текст — проверьте читаемость или попросите его сдвинуть."),
    "character_unavailable": _five("הדמות של הסוכן לא צוירה בחלק מהשקופיות (הדמות לא זמינה כרגע) — הצוות קיבל הודעה",
                                   "The agent's character was not drawn on some slides (not available right now) — the team was told",
                                   "Le personnage de l'agent n'a pas été dessiné sur certaines diapositives (indisponible pour l'instant) — l'équipe est prévenue",
                                   "لم تُرسم شخصية الوكيل في بعض الشرائح (غير متاحة حاليًا) — أُبلغ الفريق",
                                   "Персонаж агента не нарисован на некоторых слайдах (сейчас недоступен) — команда в курсе"),
    "needs_real_asset": _five("צריך כאן תמונה אמיתית של עבודה שלכם — היא לא נוצרת במחשב",
                              "This needs a real photo of your work — it is not generated",
                              "Il faut ici une vraie photo de votre travail — elle n'est pas générée",
                              "يلزم هنا صورة حقيقية لعملكم — لا تُولَّد",
                              "Здесь нужно настоящее фото вашей работы — оно не генерируется"),
}

# A real file from the client's connected Drive folder (core/drive_assets)
REAL_ASSET_KEYS = ("source_file_id", "name", "folder", "folder_path", "role", "role_text",
                   "connection_id", "media_type")
SOURCE_TEXT = _five("מקור: תמונה אמיתית מהתיקייה \"{folder}\" ({role}) ב-Drive שלכם",
                    "Source: a real file from your Drive folder \"{folder}\" ({role})",
                    "Source : un vrai fichier de votre dossier Drive « {folder} » ({role})",
                    "المصدر: ملف حقيقي من مجلد Drive لديكم \"{folder}\" ({role})",
                    "Источник: настоящий файл из вашей папки Drive «{folder}» ({role})")


def source_text(real_asset: dict, lang: str) -> str:
    """"Source: folder X (role)" for a real asset, "" otherwise."""
    if not real_asset or not real_asset.get("folder"):
        return ""
    role = real_asset.get("role_text") if lang == "he" else str(real_asset.get("role") or "").replace("_", " ")
    return pick(SOURCE_TEXT, lang).replace("{folder}", str(real_asset["folder"])).replace("{role}", role or "")


WARNING_TEXT = {
    "late": _five("שימו לב: עם זמן הייצור ({days} ימי עבודה), הזמנה שתגיע עד {occasion} צריכה להיעשות עד {order_by} — ותאריך הפרסום ({publish}) כבר אחרי זה.",
                  "Note: with the production time ({days} business days), an order that arrives by {occasion} must be placed by {order_by} — and the publish date ({publish}) is already after that.",
                  "Attention : avec le délai de fabrication ({days} jours ouvrés), une commande livrée avant le {occasion} doit être passée avant le {order_by} — et la date de publication ({publish}) est déjà après.",
                  "انتبهوا: مع مدة الإنتاج ({days} أيام عمل)، الطلب الذي يصل حتى {occasion} يجب أن يتم حتى {order_by} — وتاريخ النشر ({publish}) بعد ذلك.",
                  "Внимание: со сроком изготовления ({days} рабочих дней) заказ к {occasion} нужно сделать до {order_by} — а дата публикации ({publish}) уже позже."),
    "tight": _five("שימו לב: עם זמן הייצור ({days} ימי עבודה), אפשר להזמין עד {occasion} רק עד {order_by}. הפרסום ב-{publish} משאיר פחות משבוע להזמנה — כדאי לפרסם מוקדם יותר ולכתוב את התאריך בפוסט.",
                   "Note: with the production time ({days} business days), orders for {occasion} close on {order_by}. Publishing on {publish} leaves less than a week to order — publish earlier and state the date in the post.",
                   "Attention : avec le délai ({days} jours ouvrés), les commandes pour le {occasion} ferment le {order_by}. Publier le {publish} laisse moins d'une semaine — publiez plus tôt et indiquez la date.",
                   "انتبهوا: مع مدة الإنتاج ({days} أيام عمل)، طلبات {occasion} تُغلق في {order_by}. النشر في {publish} يترك أقل من أسبوع — انشروا أبكر واذكروا التاريخ.",
                   "Внимание: со сроком ({days} рабочих дней) заказы к {occasion} принимаются до {order_by}. Публикация {publish} оставляет меньше недели — опубликуйте раньше и укажите дату."),
}

COPY_TEXT = {
    "role": {"hook": _five("פתיחה", "Hook", "Accroche", "افتتاحية", "Крючок"),
             "point": _five("נקודה", "Point", "Point", "نقطة", "Тезис"),
             "proof": _five("הוכחה", "Proof", "Preuve", "إثبات", "Доказательство"),
             "cta": _five("קריאה לפעולה", "Call to action", "Appel à l'action", "دعوة لاتخاذ إجراء", "Призыв")},
    "asset_type": {"text_slide": _five("שקופית טקסט", "Text slide", "Diapositive texte", "شريحة نصية", "Текстовый слайд"),
                   "real_photo": _five("תמונה אמיתית", "Real photo", "Vraie photo", "صورة حقيقية", "Настоящее фото"),
                   "generated_image": _five("תמונה שתיווצר", "Generated image", "Image générée", "صورة مُولَّدة", "Сгенерированное изображение"),
                   "generated_video": _five("סרטון שייווצר", "Generated video", "Vidéo générée", "فيديو مُولَّد", "Сгенерированное видео")},
    "stage": _five("שלב הטקסט: שום דבר לא נוצר לפני שתאשרו את הטקסט",
                   "Copy stage: nothing is generated before you approve the copy",
                   "Étape du texte : rien n'est généré avant votre accord sur le texte",
                   "مرحلة النص: لا يُولَّد شيء قبل موافقتكم على النص",
                   "Этап текста: ничего не создаётся, пока вы не одобрите текст"),
}

PRODUCTION_STATES = ("waiting_copy", "running", "done", "partial", "failed", "stopped")


def _post_channels(post: dict) -> list:
    media_kind = "video" if post.get("post_format") == "reel" else "image"
    try:
        return _clean_channels(post.get("channels") or ["instagram"], media_kind)
    except QueueError:
        return [c for c in CHANNELS if media_kind in CHANNEL_KINDS[c]][:1]


def add_post(client_id: int, post: dict, palette: dict = None, warnings: list = None,
             escalations: dict = None, flags: dict = None, producer: str = "media", sources: dict = None) -> dict:
    """The copy of ONE contract post, waiting for the owner. The same set_id
    still waiting = the SAME item, revised (never a second card)."""
    queue = _load(client_id)
    slides = []
    for a in post["assets"]:
        slides.append({k: a[k] for k in ("asset_id", "role", "copy_headline", "copy_body", "asset_type",
                                          "visual_direction", "must_include", "must_avoid", "aspect_ratio",
                                          "budget_credits")}
                      | {"characters": list(a.get("characters") or []), "character_mode": a.get("character_mode") or ""}
                      | {"flags": list((flags or {}).get(a["asset_id"]) or []),
                         "escalation": (escalations or {}).get(a["asset_id"]) or {},
                         "real_asset": {k: str(((sources or {}).get(a["asset_id"]) or {}).get(k) or "")[:300]
                                        for k in REAL_ASSET_KEYS} if (sources or {}).get(a["asset_id"]) else {}}
                      | ({"needs_room": a["needs_room"]} if a.get("needs_room") else {}))
    body = {"post_format": post["post_format"], "format_reason": post["format_reason"],
            "objective": post["objective"], "audience": post["audience"], "set_id": post["set_id"],
            "occasion": post.get("occasion", ""), "occasion_date": post.get("occasion_date", ""),
            "palette": palette or {}, "warnings": list(warnings or []), "slides": slides}
    if post.get("colors"):
        body["colors"] = post["colors"]           # the manager's explicit colours (core/post_brief.clean_colors)
    existing = next((i for i in queue if i.get("kind") == POST_KIND and i["status"] == "waiting"
                     and (i.get("post") or {}).get("set_id") == post["set_id"]), None)
    if existing:
        existing.update({"post": body, "caption": _clean_caption(post["caption"]),
                         "channels": _post_channels(post), "title": post.get("title") or post["set_id"],
                         "planned_date": _clean_date(post.get("publish_date") or existing.get("planned_date"))})
        _log(existing, "revised", producer)
        _save(client_id, queue)
        return existing
    item = {"id": secrets.token_hex(6), "file_id": "", "kind": POST_KIND, "channels": _post_channels(post),
            "caption": _clean_caption(post["caption"]), "planned_date": _clean_date(post.get("publish_date")),
            "producer": producer if producer in PRODUCERS else "media", "source": "post",
            "title": re.sub(r"\s+", " ", str(post.get("title") or post["set_id"])).strip()[:120],
            "status": "waiting", "created_at": _now().isoformat(), "delivered": {}, "post": body,
            "production": {"state": "waiting_copy"}, "history": [{"at": _now().isoformat(), "do": "added"}]}
    queue.append(item)
    _save(client_id, queue)
    return item


def edit_copy(client_id: int, item_id: str, slides: list = None, caption=None, by: str = "client") -> dict:
    """The owner edits the copy (headline / body per slide, the caption) of a
    post still waiting — production hasn't spent anything yet."""
    queue = _load(client_id)
    item = next((i for i in queue if i["id"] == str(item_id)), None)
    if not item:
        raise QueueError("not_found")
    if item.get("kind") != POST_KIND:
        raise QueueError("not_a_post")
    if item["status"] != "waiting":
        raise QueueError("not_editable")
    changed = []
    by_id = {s["asset_id"]: s for s in item["post"]["slides"]}
    for s in slides or []:
        slide = by_id.get(str((s or {}).get("asset_id") or ""))
        if not slide:
            raise QueueError("bad_slide")
        for key in ("copy_headline", "copy_body"):
            if key in s and s[key] is not None:
                text = re.sub(r"[ \t]+", " ", str(s[key]).replace("\r\n", "\n")).strip()
                if len(text) > 400:
                    raise QueueError("copy_too_long")
                if text != slide[key]:
                    slide[key] = text
                    changed.append(f"{slide['asset_id']}.{key}")
    if caption is not None:
        new = _clean_caption(caption)
        if new != item["caption"]:
            item["caption"] = new
            changed.append("caption")
    if changed:
        _log(item, "copy_edited", by, fields=changed[:20])
        _save(client_id, queue)
    return item


def set_production(client_id: int, item_id: str, production: dict):
    """The post item's production record (core/post_pipeline). Never raises."""
    try:
        queue = _load(client_id)
        item = next((i for i in queue if i["id"] == str(item_id)), None)
        if item:
            item["production"] = production
            _save(client_id, queue)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: production not recorded ({type(e).__name__})")


# ─── A text-slide design under review (core/slide_design) ────────────────────

def add_design(client_id: int, design: dict, title: str = "", channels=None, caption: str = "",
               planned_date: str = "", producer: str = "media", flags: list = None) -> dict:
    """ONE design waiting for the manager. The same post's design (or the same
    standalone template) still waiting = the SAME item, replaced."""
    queue = _load(client_id)
    try:
        chans = _clean_channels(channels or ["instagram"], "image")
    except QueueError:
        chans = ["instagram"]
    key = (design.get("post_id") or "", design.get("template_name") if not design.get("post_id") else "")
    existing = next((i for i in queue if i.get("kind") == DESIGN_KIND and i["status"] == "waiting"
                     and ((i.get("design") or {}).get("post_id") or "",
                          (i.get("design") or {}).get("template_name") if not (i.get("design") or {}).get("post_id") else "")
                     == key), None)
    if existing:
        existing.update({"design": design, "caption": _clean_caption(caption), "channels": chans,
                         "title": re.sub(r"\s+", " ", str(title or design.get("template_name") or "")).strip()[:120]})
        existing["flags"] = [f for f in flags or [] if f in FLAG_TEXT][:6]
        _log(existing, "redesigned", producer)
        _save(client_id, queue)
        return existing
    item = {"id": secrets.token_hex(6), "file_id": "", "kind": DESIGN_KIND, "channels": chans,
            "caption": _clean_caption(caption), "planned_date": _clean_date(planned_date),
            "producer": producer if producer in PRODUCERS else "media", "source": "design",
            "title": re.sub(r"\s+", " ", str(title or design.get("template_name") or "")).strip()[:120],
            "status": "waiting", "created_at": _now().isoformat(), "delivered": {}, "design": design,
            "history": [{"at": _now().isoformat(), "do": "added"}]}
    if flags:
        item["flags"] = [f for f in flags if f in FLAG_TEXT][:6]
    queue.append(item)
    _save(client_id, queue)
    return item


def update_design(client_id: int, item_id: str, changes: dict) -> dict:
    """Merges `changes` into a design item's design. {} when it is gone."""
    queue = _load(client_id)
    item = next((i for i in queue if i["id"] == str(item_id) and i.get("kind") == DESIGN_KIND), None)
    if not item:
        return {}
    item["design"].update(changes)
    if "rev" in changes:
        _log(item, "revised", "media", rev=changes["rev"])
    _save(client_id, queue)
    return item


def warning_lines(warnings: list, lang: str) -> list:
    out = []
    for w in warnings or []:
        table = WARNING_TEXT.get(w.get("level"))
        if not table:
            continue
        days = w.get("lead_time_days") or ["", ""]
        out.append(pick(table, lang).replace("{days}", f"{days[0]}–{days[1]}" if days[0] != days[1] else str(days[0]))
                   .replace("{occasion}", w.get("occasion_date", "")).replace("{order_by}", w.get("order_by", ""))
                   .replace("{publish}", w.get("publish_date", "")))
    return out


def copy_fit(client_id: int, item: dict, lang: str) -> dict:
    """Does a WAITING post's copy fit the CURRENT template (core/copy_budget)?
    Per text slide, checked when the card is read — so it follows an edit and
    a template change. {} once approved, or when it can't be checked."""
    post = item.get("post") or {}
    if item.get("kind") != POST_KIND or item.get("status") != "waiting" or not client_id:
        return {}
    try:
        from core import copy_budget
        slides = post.get("slides") or []
        return copy_budget.card(copy_budget.fit(client_id, post.get("palette") or {}, slides, lang), slides, lang)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: copy fit not checked ({type(e).__name__})")
        return {}


def _post_view(item: dict, lang: str, client_id: int = None) -> dict:
    post = item.get("post") or {}
    fit = copy_fit(client_id, item, lang)
    slides = []
    for n, s in enumerate(post.get("slides") or [], 1):
        slides.append({"asset_id": s["asset_id"], "n": n, "role": s["role"],
                       "role_text": pick(COPY_TEXT["role"].get(s["role"], {"en": s["role"]}), lang),
                       "copy_headline": s.get("copy_headline", ""), "copy_body": s.get("copy_body", ""),
                       "asset_type": s["asset_type"],
                       "asset_type_text": pick(COPY_TEXT["asset_type"].get(s["asset_type"], {"en": s["asset_type"]}), lang),
                       "flags": [{"code": f, "text": pick(FLAG_TEXT[f], lang)} for f in s.get("flags") or [] if f in FLAG_TEXT],
                       "escalation": s.get("escalation") or {},
                       "source_text": source_text(s.get("real_asset"), lang),
                       # Fits the current template? Shown BEFORE approval, never a block
                       "fit": (fit.get("slides") or {}).get(s["asset_id"]) or {}})
    return {"post_format": post.get("post_format", ""), "format_reason": post.get("format_reason", ""),
            "objective": post.get("objective", ""), "audience": post.get("audience", ""),
            "palette": (post.get("palette") or {}).get("label", ""), "slides": slides,
            "fit_summary": fit.get("summary", ""), "fit_over": fit.get("over", 0),
            "warnings": warning_lines(post.get("warnings"), lang), "stage_text": pick(COPY_TEXT["stage"], lang),
            "production": _production_view(item, lang, client_id)}


def _production_view(item: dict, lang: str, client_id: int = None) -> dict:
    """The stored production record + "progress": the SAME determinate bar the
    chat card shows (core/production_progress) — while it runs, and frozen
    with its reasons when it ended short. Never raises."""
    production = dict(item.get("production") or {})
    if not client_id or production.get("state") not in ("running", "failed", "partial", "stopped"):
        return production
    try:
        from core import production_progress
        since = None
        try:
            since = datetime.fromisoformat(str(production.get("started_at")))
        except ValueError:
            pass
        got = production_progress.read(client_id, item["id"], lang, since=since)
        if not got and production.get("state") == "running":
            got = production_progress.preparing(lang)
        if got:
            production["progress"] = got
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: production progress not read ({type(e).__name__})")
    return production


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
        if any(i.get("characters") or i.get("kind") == DESIGN_KIND for i in done):
            # Every character image an approved item used → the pose library, approved
            try:
                from core import agent_characters
                agent_characters.on_items_approved(client_id, done)
            except Exception as e:
                print(f"[{SERVICE_NAME}] client {client_id}: poses not recorded ({type(e).__name__})")
        # What the system produced and the manager approved → the client's own
        # backup folder (core/drive_backup; copied by the minute tick)
        try:
            from core import drive_backup
            drive_backup.enqueue(client_id, done)
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: backup not queued ({type(e).__name__})")
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
    if item.get("kind") == DESIGN_KIND:
        raise QueueError("not_editable")          # a design changes through a critique (core/slide_design)
    changed = []
    if caption is not None:
        new = _clean_caption(caption)
        if new != item["caption"]:
            item["caption"], changed = new, changed + ["caption"]
    if item.get("kind") == POST_KIND and item["status"] != "waiting":
        raise QueueError("not_editable")          # its copy is approved and production started
    if channels is not None:
        media_kind = item["kind"] if item["kind"] in KINDS else (
            "video" if (item.get("post") or {}).get("post_format") == "reel" else "image")
        new = _clean_channels(channels, media_kind)
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


def reject(client_id: int, item_id: str, reason: str, by: str = "client", lang: str = "he",
           asset_rejected: bool = False, asset_reason: str = "") -> dict:
    """Back to the producing agent with the reason: a line in THEIR chat, and
    the fixed version proposed there as ONE card (it comes back here).
    `asset_rejected`: the owner marked the PICTURE the agent chose as the
    problem (optional short `asset_reason`) — recorded against that real
    asset (core/asset_feedback) so it ranks lower and the reason reaches the
    agent's next choice. With no own text, the picture reason is the reason."""
    asset_reason = re.sub(r"\s+", " ", str(asset_reason or "")).strip()
    reason = re.sub(r"\s+", " ", str(reason or "")).strip() or (asset_reason if asset_rejected else "")
    if len(reason) < 2:
        raise QueueError("reason_required")
    reason = reason[:REASON_MAX]
    queue = _load(client_id)
    item = next((i for i in queue if i["id"] == str(item_id)), None)
    if not item:
        raise QueueError("not_found")
    if item["status"] not in ("waiting", "approved"):
        raise QueueError("not_rejectable")
    if item.get("kind") == DESIGN_KIND:
        raise QueueError("use_critique")          # a design goes back to the agent as a critique
    item["status"], item["reject_reason"], item["rejected_at"] = "rejected", reason, _now().isoformat()
    _log(item, "rejected", by, reason=reason)
    _save(client_id, queue)
    source = (item.get("real_asset") or {}).get("source_file_id")
    if asset_rejected and source:
        from core import asset_feedback
        shows = ""
        try:                                   # what the picture shows, from the index (for the agent)
            from core import drive_assets
            shows = ((drive_assets._load(client_id).get("items") or {}).get(source) or {}).get("description", "")
        except Exception:
            pass
        asset_feedback.record_rejection(client_id, source, asset_reason, item_id=item["id"],
                                        asset=shows or (item.get("real_asset") or {}).get("name", ""))
        item["asset_rejection_recorded"] = True
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
    if item.get("kind") == POST_KIND or item.get("post_ref"):
        # A contract post (or one of its assets): the social agent rewrites the
        # POST from the reason in its chat — never the old free-text brief path,
        # which would put copy and visuals back into one image prompt
        return out
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


DELETE_MAX = MAX_ITEMS


def delete(client_id: int, ids, by: str = "client") -> dict:
    """Removes queue items and their approval state (2026-11-24). The media
    FILES are never touched — they stay in the Media tab. A published item
    can't be un-published: deleting it only takes it out of the queue view.
    One entry in the existing deletion log (media_inventory, mode "queue").
    {"deleted": [{id, title, status, kind}], "published": n, "missing": [ids]}"""
    ids = [str(i) for i in (ids if isinstance(ids, (list, tuple)) else [ids]) if ID_RE.match(str(i))]
    ids = list(dict.fromkeys(ids))
    if not ids or len(ids) > DELETE_MAX:
        raise QueueError("bad_ids")
    queue = _load(client_id)
    gone = [i for i in queue if i["id"] in ids]
    if gone:
        _save(client_id, [i for i in queue if i["id"] not in ids])
        try:                      # the agents' 60 s view of the queue must not show them again
            from core import content_team
            content_team._block_cache.pop(int(client_id), None)
        except Exception:
            pass
    deleted = [{"id": i["id"], "title": i.get("title", ""), "status": i["status"], "kind": i.get("kind", ""),
                "file_id": i.get("file_id", "")} for i in gone]
    if deleted:
        try:
            from core import media_inventory
            media_inventory.log_entry(client_id, {
                "by": by, "mode": "queue", "count": len(deleted), "size": 0, "failed": 0, "folders": [],
                "affected_posts": [], "published": sum(1 for d in deleted if d["status"] == "published"),
                "items": [{"id": d["id"], "name": d["title"] or d["id"], "size": 0, "source": "social_queue",
                           "folder": d["status"], "how": "queue", "file_id": d["file_id"]} for d in deleted]})
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: deletion not logged ({type(e).__name__})")
    return {"deleted": deleted, "published": sum(1 for d in deleted if d["status"] == "published"),
            "missing": [i for i in ids if i not in {d["id"] for d in deleted}]}


def mark_published(client_id: int, item_id: str, by: str = "client") -> dict:
    """The owner's "I published it" (the one-tap channels), or a channel's
    automatic publish."""
    queue = _load(client_id)
    item = next((i for i in queue if i["id"] == str(item_id)), None)
    if not item:
        raise QueueError("not_found")
    if item.get("kind") in NO_FILE_KINDS:
        raise QueueError("not_publishable")       # a copy / design approval publishes nothing
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
    match = [i for i in queue if i.get("kind") not in NO_FILE_KINDS and (
             (item_id and i["id"] == str(item_id)) or (file_id and not item_id and i["file_id"] == str(file_id)))]
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
        is_post = item.get("kind") == POST_KIND
        no_file = item.get("kind") in NO_FILE_KINDS
        dl = delivery(item, modes) if item["status"] in ("approved", "published") and not no_file else {}
        needs_tap = item["status"] == "approved" and any(v in ("one_tap", "not_connected", "drafts", "private")
                                                         for v in dl.values())
        extra = {"post": _post_view(item, lang, client_id)} if is_post else {}
        if item.get("kind") == DESIGN_KIND:
            from core import slide_design
            extra["design"] = slide_design.view(item, lang)
        if item.get("flags"):
            extra["flags"] = [{"code": f, "text": pick(FLAG_TEXT[f], lang)} for f in item["flags"] if f in FLAG_TEXT]
        if item.get("post_ref"):
            extra["post_ref"] = item["post_ref"]
        if item.get("real_asset"):
            extra["real_asset"] = item["real_asset"]
            extra["source_text"] = source_text(item["real_asset"], lang)
        out.append({**extra, "id": item["id"], "file_id": item["file_id"], "kind": item["kind"], "title": item.get("title", ""),
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
        # An approved post COPY is in production, not waiting for a publish tap
        approved = [i for i in queue if i.get("status") == "approved" and i.get("kind") not in NO_FILE_KINDS]
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
                               "planned_date": i.get("planned_date", ""),
                               **({"stage": "copy", "set_id": (i.get("post") or {}).get("set_id", "")}
                                  if i.get("kind") == POST_KIND else {}),
                               **({"stage": "design", "template": (i.get("design") or {}).get("template_name", "")}
                                  if i.get("kind") == DESIGN_KIND else {})} for i in waiting[:10]],
            # The rest of the queue, so the manager can name (and delete) them too
            "other_items": [{"id": i["id"], "title": i.get("title", ""), "status": i["status"],
                             "planned_date": i.get("planned_date", "")}
                            for i in queue if i["status"] != "waiting"][:20],
            "counts": {s: sum(1 for i in queue if i["status"] == s) for s in STATUSES},
            # Approved posts whose production failed / partly failed / is stuck, with
            # WHAT failed and why (core/post_pipeline) — never only in a log
            **_production_block(client_id)}


PRODUCTION_RULE = ("post_production lists approved posts whose production FAILED or only PARTLY worked, with "
                   "each problem in plain words. When it is not empty, your reply says so in the same message — "
                   "what failed and why, in plain words — and offers rerun_post_production for it; never answer "
                   "that a post is waiting for approval or that everything is fine while it lists a problem.")


def _production_block(client_id: int) -> dict:
    try:
        from core import post_pipeline
        rows = post_pipeline.retryable(client_id)
    except Exception:
        return {}
    return {"post_production": rows, "post_production_rule": PRODUCTION_RULE} if rows else {}
