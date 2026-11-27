"""The ליאור → יואב handoff contract for a paid campaign (2026-11-28,
docs/PAID-MEDIA.md §3, knowledge/paid-media-craft.md §1).

Three objects, one direction:

  MEDIA ORDER        issued by the paid-media agent (Yoav) when a campaign is
                     asked for. HE decides the campaign type and objective;
                     what the media agent must deliver is DERIVED from the
                     table (core/google_ads_formats.required_assets) — nobody
                     picks a size. `issue()`.
  DELIVERY MANIFEST  returned by the media agent (Lior): one row per produced
                     file. It is CHECKED before Yoav ever sees it
                     (`check_delivery`, from the files' bytes — never from
                     what the manifest claims): every required asset present,
                     every file within its cap, every image through the
                     crop-survival check, every video a YouTube URL, the
                     palette the segment's brand variant.
  HANDOVER           `hand_over()`: an INCOMPLETE set is never handed over.
                     It raises IncompleteDelivery, names exactly what is
                     missing, and Yoav is not notified. An asset group that
                     cannot serve is worse than none: it looks like work.

Then Yoav verifies AGAINST THE PLATFORM MINIMUM, independently of the
manifest (`verify_minimum`: the table's minimum_to_serve, the files' bytes,
the text assets) — a cross-check, not self-approval (social-craft §1).

Text assets are COPY, not media: `media_brief()` (what Lior gets) carries no
text slot and no copy; `copy_brief()` (what the copy step gets) carries no
media row. The two never come from the same step.

Storage: app_settings `media_orders:<client_id>` (the newest KEEP orders, no
file bytes — files live in the client's Drive). No migration. Cleared with the
client (core/client_deletion) and covered by the deletion guard.
"""
import re
import uuid
from datetime import date, datetime, timedelta, timezone

from core import crop_survival
from core import google_ads_formats as gaf

SERVICE_NAME = "media_order"
PREFIX = "media_orders:"
KEEP = 20
OBJECTIVES = ("sales", "leads", "website_traffic", "awareness")
DEFAULT_CONCEPTS = 3          # distinct images per ratio (ours, not Google's): each one in EVERY ratio of the type
DEFAULT_LEAD_DAYS = 3         # a deadline nobody stated
STATUSES = ("ordered", "in_production", "incomplete", "handed_over", "verified", "rejected")
ISSUER, PRODUCER = "yoav", "lior"
MEDIA_KEYS = ("id", "client_id", "campaign_type", "objective", "audience_segment", "palette", "landing_page_url",
              "offer", "required_assets", "deadline", "concepts", "specs")
COPY_KEYS = ("id", "client_id", "campaign_type", "objective", "audience_segment", "landing_page_url", "offer",
             "required_text", "deadline", "specs")
URL_RE = re.compile(r"^https://[^\s/$.?#][^\s]*$", re.IGNORECASE)
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class OrderError(ValueError):
    """An order that cannot be issued: .code names the field."""

    def __init__(self, code: str, detail: str = ""):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


class IncompleteDelivery(Exception):
    """The delivered set is not complete: it was NOT handed over."""

    def __init__(self, order: dict, problems: list):
        self.order, self.problems = order, problems
        super().__init__(f"media order {order.get('id')}: delivery incomplete — " +
                         "; ".join(problem_lines(problems, "en")))


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or next(iter(table.values()))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _line(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


# ─── The order ───────────────────────────────────────────────────────────────

def _palette(client_id: int, audience: str, facts, palette_roles) -> dict:
    """{"variant", "segment", "roles"} — the ONE brand variant for the segment."""
    from core import post_brief
    if facts is None:
        facts = post_brief.load_context(client_id).get("brand_facts") or {}
    try:
        variant = post_brief.resolve_palette(facts, audience)
    except post_brief.AudienceUnresolved as e:
        raise OrderError("audience_unresolved", f"the brand has palette variants ({', '.join(e.variants)}) and "
                                                f"{audience!r} does not land on exactly one")
    label = str(variant.get("label") or "default")
    if palette_roles is None:
        from core import slide_spec
        palette_roles = slide_spec.palette_roles
    try:
        roles = dict(palette_roles(client_id, label))
    except Exception as e:
        raise OrderError("palette_missing", f"no colours for the brand variant {label!r} ({type(e).__name__})")
    return {"variant": label, "segment": variant.get("segment", ""),
            "roles": {r: str(v).upper() for r, v in roles.items() if HEX_RE.match(str(v or ""))}}


def issue(client_id: int, decision: dict, facts: dict = None, today: date = None, palette_roles=None) -> dict:
    """Yoav's decision → a MEDIA ORDER. `decision`: {"campaign_type",
    "objective", "audience_segment", "landing_page_url", "offer", "deadline"?
    (YYYY-MM-DD), "concepts"?, "banners"?, "video"?}. OrderError names the
    field that is wrong; nothing is stored here (see `save`)."""
    today = today or date.today()
    raw = decision or {}
    campaign_type = str(raw.get("campaign_type") or "").strip().lower()
    if campaign_type not in gaf.CAMPAIGN_TYPES:
        raise OrderError("campaign_type", f"one of {', '.join(gaf.CAMPAIGN_TYPES)}")
    objective = str(raw.get("objective") or "").strip().lower()
    if objective not in OBJECTIVES:
        raise OrderError("objective", f"one of {', '.join(OBJECTIVES)}")
    audience = _line(raw.get("audience_segment"), 200)
    if not audience:
        raise OrderError("audience_segment", "who the campaign is for — it decides the brand palette variant")
    url = str(raw.get("landing_page_url") or "").strip()
    if not URL_RE.match(url):
        raise OrderError("landing_page_url", "an https:// address")
    offer = _line(raw.get("offer"), 400)
    if len(offer) < 3:
        raise OrderError("offer", "what is being advertised")
    deadline = str(raw.get("deadline") or "").strip() or (today + timedelta(days=DEFAULT_LEAD_DAYS)).isoformat()
    try:
        due = date.fromisoformat(deadline)
    except ValueError:
        raise OrderError("deadline", "YYYY-MM-DD")
    if due < today:
        raise OrderError("deadline", f"{deadline} is in the past")
    try:
        concepts = int(raw.get("concepts") or DEFAULT_CONCEPTS)
    except (TypeError, ValueError):
        raise OrderError("concepts", "a number")
    ceiling = gaf.max_concepts(campaign_type)
    concepts = max(1, min(concepts, ceiling)) if ceiling else 0
    try:
        rows = gaf.required_assets(campaign_type, concepts=concepts or 1, banners=str(raw.get("banners") or "none"),
                                   video=bool(raw.get("video")) or None)
    except ValueError as e:
        raise OrderError("required_assets", str(e))
    return {"id": uuid.uuid4().hex[:12], "client_id": int(client_id), "campaign_type": campaign_type,
            "objective": objective, "audience_segment": audience,
            "palette": _palette(client_id, audience, facts, palette_roles),
            "landing_page_url": url, "offer": offer, "required_assets": rows,
            "required_text": gaf.required_text(campaign_type), "deadline": deadline, "concepts": concepts,
            "issued_by": ISSUER, "issued_at": _now(), "status": "ordered",
            "specs": {"verified": gaf.VERIFIED, "reverify_by": gaf.REVERIFY_BY, "stale": gaf.stale(today),
                      "warning": gaf.stale_line(today)}}


def media_brief(order: dict) -> dict:
    """What the MEDIA agent receives: the order without a single text slot."""
    return {k: order[k] for k in MEDIA_KEYS if k in order}


def copy_brief(order: dict) -> dict:
    """What the COPY step receives: the order without a single media row."""
    return {k: order[k] for k in COPY_KEYS if k in order}


def generations(order: dict) -> int:
    """The most image generations the order can cost: one per image it lists
    (the producer derives ratios from one picture where the crop allows)."""
    return sum(r["count"] for r in order.get("required_assets") or [] if r["kind"] == "image")


# ─── The delivery manifest ───────────────────────────────────────────────────

def key(slot: str, n: int) -> str:
    return f"{slot}#{int(n)}"


def asset(slot: str, n: int, **fields) -> dict:
    """One manifest row. Fields a producer may set: file_id, link, source
    (generated | derived | logo | real), palette_variant, drawn_colors (every
    colour CODE drew into the file), subject_box (fractions), youtube_url,
    file_path, compress, crop."""
    row = {"slot": str(slot), "n": int(n)}
    row.update({k: v for k, v in fields.items() if v not in (None, "", [], {})})
    return row


def manifest(order: dict, assets: list, notes: list = None) -> dict:
    return {"order_id": order["id"], "produced_by": PRODUCER, "produced_at": _now(),
            "assets": [dict(a) for a in assets], "notes": [str(n)[:300] for n in notes or []]}


def _what(row: dict, n=None) -> dict:
    return {"slot": row["slot"], "n": n, "size": f"{row.get('width')}x{row.get('height')}" if row.get("width") else ""}


def check_delivery(order: dict, delivered: dict, files: dict) -> dict:
    """The manifest against the order, from the FILES (`files` = {key(slot, n):
    bytes}): {"complete", "problems": [{"slot", "n", "code", ...}],
    "counts": {slot: {"have", "need"}}, "checked_at", "warning"}.

    A manifest row is counted only when its file exists and passes EVERY
    check; then the count is held against the order. Nothing here trusts a
    number the manifest states about its own files."""
    problems, counts = [], {}
    by_slot = {}
    for a in (delivered or {}).get("assets") or []:
        by_slot.setdefault(a.get("slot"), []).append(a)
    variant = (order.get("palette") or {}).get("variant") or ""
    allowed = {str(c).upper() for c in ((order.get("palette") or {}).get("roles") or {}).values()}
    for row in order.get("required_assets") or []:
        good = 0
        for a in sorted(by_slot.get(row["slot"], []), key=lambda x: int(x.get("n") or 0)):
            found = []
            if row["kind"] == "video":
                found += [{"code": "video_" + p["code"]} for p in gaf.video_reference_problems(a)]
            else:
                content = (files or {}).get(key(a["slot"], a.get("n") or 0))
                if not content:
                    found.append({"code": "no_file"})
                else:
                    for p in gaf.check_file(row, content):
                        found.append({**p, "aspect": row.get("aspect", ""), "cap_kb": row["max_bytes"] // gaf.KB,
                                      "min": f"{row['min_width']}x{row['min_height']}",
                                      "types": " / ".join(t.upper() for t in row["file_types"])})
                    if row.get("crop_targets") and not any(p["code"] == "unreadable" for p in found):
                        survived = crop_survival.check(content, row["slot"], subject_box=a.get("subject_box"),
                                                       targets=tuple(row["crop_targets"]))
                        if not survived["crops"] and not survived["ok"]:
                            found.append({"code": "crop_no_subject"})
                        for c in survived["crops"]:
                            if not c["ok"]:
                                found.append({"code": "crop", "aspect": c["aspect"],
                                              "pct": round(c["retained"] * 100)})
                if str(a.get("palette_variant") or "") != variant:
                    found.append({"code": "palette_variant", "variant": variant,
                                  "used": str(a.get("palette_variant") or "—")})
                outside = sorted({str(c).upper() for c in a.get("drawn_colors") or []} - allowed)
                if outside:
                    found.append({"code": "palette_colors", "variant": variant, "colors": ", ".join(outside)})
            if found:
                problems += [{**_what(row, a.get("n")), **p} for p in found]
            else:
                good += 1
        counts[row["slot"]] = {"have": good, "need": row["count"]}
        if good < row["count"]:
            problems.append({**_what(row), "code": "missing", "have": good, "need": row["count"]})
    return {"complete": not problems, "problems": problems, "counts": counts, "checked_at": _now(),
            "warning": gaf.stale_line()}


def hand_over(client_id: int, order: dict, delivered: dict, files: dict, notify=None, store=True) -> dict:
    """Lior's delivery → Yoav, ONLY when it is complete. Incomplete: the order
    is marked `incomplete` with what is missing, `notify` is NOT called and
    IncompleteDelivery is raised. Complete: marked `handed_over`, then
    `notify(order)` — the first moment Yoav hears of it."""
    result = check_delivery(order, delivered, files)
    order["delivery"] = {"manifest": delivered, "check": result}
    if not result["complete"]:
        order["status"] = "incomplete"
        order.pop("handed_over_at", None)
        if store:
            save(client_id, order)
        print(f"[{SERVICE_NAME}] client {client_id}: order {order['id']} NOT handed over — " +
              "; ".join(problem_lines(result["problems"], "en")))
        raise IncompleteDelivery(order, result["problems"])
    order["status"], order["handed_over_at"] = "handed_over", _now()
    if store:
        save(client_id, order)
    print(f"[{SERVICE_NAME}] client {client_id}: order {order['id']} handed over ({len(delivered['assets'])} assets)")
    if notify:
        notify(order)
    return result


# ─── Yoav's own check: the platform minimum ──────────────────────────────────

def verify_minimum(campaign_type: str, delivered: list, texts: dict) -> dict:
    """Can this set SERVE? Deliberately blind to the order's rows and to the
    manifest's check: only the table's minimum_to_serve, the delivered files
    themselves (`delivered` = [{"slot", "content": bytes} | {"slot": "video",
    "youtube_url"}]) and the text assets. {"can_serve", "problems", "counted":
    {name: n}, "minimum", "minimum_source", "warning"}."""
    c = gaf.campaign(campaign_type)
    minimum = gaf.minimum_to_serve(campaign_type)
    rows = {r["slot"]: r for r in gaf.required_assets(campaign_type, video=True if c["video"] else None)}
    counted, problems = {}, []
    for item in delivered or []:
        slot = str(item.get("slot") or "")
        row = rows.get(slot)
        if not row:
            continue                                  # not something this campaign type takes (a banner): not counted
        if row["kind"] == "video":
            ok = not gaf.video_reference_problems(item)
        else:
            ok = bool(item.get("content")) and not gaf.check_file(row, item["content"])
        if ok:
            counted[slot] = counted.get(slot, 0) + 1
            if row["kind"] == "image":
                counted["image"] = counted.get("image", 0) + 1
            elif row["kind"] == "logo":
                counted["logo"] = counted.get("logo", 0) + 1
    for name, need in minimum.items():
        if name in c["text"]:
            continue
        if counted.get(name, 0) < need:
            problems.append({"slot": name, "n": None, "size": "", "code": "missing", "have": counted.get(name, 0),
                             "need": need})
    for total, name in ((c["images_total_max"], "image"), (c["logos_total_max"], "logo")):
        if total and counted.get(name, 0) > total:
            problems.append({"slot": name, "n": None, "size": "", "code": "over_max", "have": counted[name],
                             "limit": total})
    for p in gaf.check_text(campaign_type, texts or {}):
        problems.append({"slot": p["field"], "n": p.get("index") or None, "size": "", **{k: v for k, v in p.items()
                                                                                       if k not in ("field", "index")},
                         "code": "text_" + p["code"]})
    for field in c["text"]:
        values = (texts or {}).get(field)
        counted[field] = len([v for v in (values if isinstance(values, (list, tuple)) else [values]) if v])
    return {"can_serve": not problems, "problems": problems, "counted": counted, "minimum": minimum,
            "minimum_source": c["minimum_source"], "warning": gaf.stale_line()}


def yoav_verify(client_id: int, order: dict, files: dict, texts: dict, store=True) -> dict:
    """The cross-check on a HANDED-OVER order: `verified` (uploadable) or
    `rejected`, with what the platform would refuse. Refuses to run on an
    order that was never handed over — there is nothing of Lior's to verify."""
    if order.get("status") not in ("handed_over", "verified", "rejected"):
        raise OrderError("not_handed_over", f"order {order.get('id')} is {order.get('status')}")
    assets = ((order.get("delivery") or {}).get("manifest") or {}).get("assets") or []
    delivered = [{"slot": a["slot"], "content": (files or {}).get(key(a["slot"], a.get("n") or 0)),
                  "youtube_url": a.get("youtube_url")} for a in assets]
    result = verify_minimum(order["campaign_type"], delivered, texts)
    order["verification"] = {**result, "verified_by": ISSUER, "verified_at": _now()}
    order["status"] = "verified" if result["can_serve"] else "rejected"
    if store:
        save(client_id, order)
    print(f"[{SERVICE_NAME}] client {client_id}: order {order['id']} {order['status']} by {ISSUER}")
    return result


# ─── Storage (app_settings, no bytes) ────────────────────────────────────────

def _db():
    import os
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def all_orders(client_id: int) -> list:
    """Newest first. [] when none (or unreadable — said in the log)."""
    try:
        rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}")
                .limit(1).execute().data or [])
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: orders unreadable ({type(e).__name__})")
        return []
    value = rows[0].get("value") if rows else None
    return list((value or {}).get("orders") or []) if isinstance(value, dict) else []


def get(client_id: int, order_id: str) -> dict:
    return next((o for o in all_orders(client_id) if o.get("id") == str(order_id)), {})


def save(client_id: int, order: dict):
    """Upserts one order (by id) at the front of the client's list."""
    orders = [o for o in all_orders(client_id) if o.get("id") != order.get("id")]
    stamped = {**order, "updated_at": _now()}
    _db().table("app_settings").upsert(
        {"key": f"{PREFIX}{int(client_id)}", "value": {"orders": ([stamped] + orders)[:KEEP]},
         "updated_at": _now()}, on_conflict="key").execute()


# ─── Plain words (the client's language) ─────────────────────────────────────

SLOT_TEXT = {
    "landscape": _five("תמונה לרוחב", "landscape image", "image paysage", "صورة أفقية", "горизонтальное изображение"),
    "square": _five("תמונה ריבועית", "square image", "image carrée", "صورة مربعة", "квадратное изображение"),
    "portrait": _five("תמונה לאורך", "portrait image", "image portrait", "صورة عمودية", "портретное изображение"),
    "vertical": _five("תמונה אנכית למסך מלא", "full-screen vertical image", "image verticale plein écran",
                      "صورة طولية بملء الشاشة", "вертикальное изображение на весь экран"),
    "logo_square": _five("לוגו ריבועי", "square logo", "logo carré", "شعار مربع", "квадратный логотип"),
    "logo_landscape": _five("לוגו רחב", "wide logo", "logo large", "شعار عريض", "широкий логотип"),
    "video": _five("סרטון", "video", "vidéo", "فيديو", "видео"),
    "image": _five("תמונה", "image", "image", "صورة", "изображение"),
    "logo": _five("לוגו", "logo", "logo", "شعار", "логотип"),
    "banner": _five("באנר {size}", "{size} banner", "bannière {size}", "بانر {size}", "баннер {size}"),
    "headline": _five("כותרת", "headline", "titre", "عنوان", "заголовок"),
    "long_headline": _five("כותרת ארוכה", "long headline", "titre long", "عنوان طويل", "длинный заголовок"),
    "description": _five("תיאור", "description", "description", "وصف", "описание"),
    "business_name": _five("שם העסק", "business name", "nom de l'entreprise", "اسم النشاط", "название компании"),
    "path": _five("נתיב תצוגה", "display path", "chemin affiché", "مسار العرض", "отображаемый путь"),
}
PROBLEM_TEXT = {
    "missing": _five("{what}: חסר — יש {have} מתוך {need}.", "{what}: missing — {have} of {need}.",
                     "{what} : manquant — {have} sur {need}.", "{what}: ناقص — {have} من {need}.",
                     "{what}: не хватает — {have} из {need}."),
    "no_file": _five("{what}: הקובץ לא נמסר.", "{what}: the file was not delivered.", "{what} : fichier non livré.",
                     "{what}: الملف لم يُسلَّم.", "{what}: файл не сдан."),
    "unreadable": _five("{what}: הקובץ לא נפתח כתמונה.", "{what}: the file does not open as an image.",
                        "{what} : le fichier ne s'ouvre pas comme image.", "{what}: الملف لا يُفتح كصورة.",
                        "{what}: файл не открывается как изображение."),
    "file_type": _five("{what}: סוג הקובץ {file_type} לא מתקבל כאן (רק {types}).",
                       "{what}: file type {file_type} is not accepted here ({types} only).",
                       "{what} : type {file_type} non accepté ici ({types} uniquement).",
                       "{what}: نوع الملف {file_type} غير مقبول هنا ({types} فقط).",
                       "{what}: тип файла {file_type} здесь не принимается (только {types})."),
    "ratio": _five("{what}: {width}x{height} — זה לא היחס {aspect}.", "{what}: {width}x{height} is not {aspect}.",
                   "{what} : {width}x{height} n'est pas du {aspect}.", "{what}: {width}x{height} ليست بنسبة {aspect}.",
                   "{what}: {width}x{height} — это не {aspect}."),
    "too_small": _five("{what}: {width}x{height} — קטן מהמינימום {min}.", "{what}: {width}x{height} is under the {min} minimum.",
                       "{what} : {width}x{height} est sous le minimum {min}.", "{what}: {width}x{height} أصغر من الحد الأدنى {min}.",
                       "{what}: {width}x{height} меньше минимума {min}."),
    "size": _five("{what}: {width}x{height} — הבאנר חייב להיות בגודל המדויק.", "{what}: {width}x{height} — a banner must be the exact size.",
                  "{what} : {width}x{height} — une bannière doit avoir la taille exacte.",
                  "{what}: {width}x{height} — يجب أن يكون البانر بالحجم الدقيق.",
                  "{what}: {width}x{height} — баннер должен быть точного размера."),
    "file_size": _five("{what}: שוקל {kb} KB, המקסימום {cap_kb} KB.", "{what}: {kb} KB, the cap is {cap_kb} KB.",
                       "{what} : {kb} Ko, la limite est {cap_kb} Ko.", "{what}: {kb} ك.ب، الحد الأقصى {cap_kb} ك.ب.",
                       "{what}: {kb} КБ, лимит {cap_kb} КБ."),
    "crop": _five("{what}: הנושא לא שורד חיתוך מרכזי ל-{aspect} — נשאר ממנו {pct}%.",
                  "{what}: the subject does not survive the {aspect} centre crop — {pct}% of it is left.",
                  "{what} : le sujet ne survit pas au recadrage central {aspect} — il en reste {pct} %.",
                  "{what}: العنصر الرئيسي لا ينجو من القص المركزي {aspect} — بقي منه {pct}%.",
                  "{what}: объект не переживает центральную обрезку {aspect} — остаётся {pct}%."),
    "crop_no_subject": _five("{what}: לא נמצא בתמונה נושא ברור.", "{what}: no clear subject was found in the image.",
                             "{what} : aucun sujet net n'a été trouvé dans l'image.",
                             "{what}: لم يُعثر على عنصر رئيسي واضح في الصورة.", "{what}: на изображении не найден чёткий объект."),
    "video_file_not_url": _five("{what}: צריך קישור YouTube. סרטון מועלה קודם לערוץ ה-YouTube של העסק ורק אז מצורף לקמפיין.",
                                "{what}: it needs a YouTube link. A video is uploaded to the business's YouTube channel first and only then attached to the campaign.",
                                "{what} : il faut un lien YouTube. Une vidéo est d'abord mise en ligne sur la chaîne YouTube de l'entreprise, puis jointe à la campagne.",
                                "{what}: يلزم رابط YouTube. يُرفع الفيديو أولًا إلى قناة النشاط على YouTube ثم يُرفق بالحملة.",
                                "{what}: нужна ссылка YouTube. Видео сначала загружают на YouTube-канал компании и только потом добавляют в кампанию."),
    "video_bad_url": _five("{what}: הקישור אינו קישור לסרטון YouTube.", "{what}: the link is not a YouTube video link.",
                           "{what} : le lien n'est pas un lien de vidéo YouTube.", "{what}: الرابط ليس رابط فيديو على YouTube.",
                           "{what}: ссылка не ведёт на видео YouTube."),
    "palette_variant": _five("{what}: לא הופק בפלטת המותג של הקהל הזה ({variant}).",
                             "{what}: not produced in this audience's brand palette ({variant}).",
                             "{what} : non produit dans la palette de marque de ce public ({variant}).",
                             "{what}: لم يُنتج بلوحة ألوان العلامة لهذا الجمهور ({variant}).",
                             "{what}: сделано не в фирменной палитре этой аудитории ({variant})."),
    "palette_colors": _five("{what}: יש בו צבעים שאינם בפלטת המותג {variant}: {colors}.",
                            "{what}: it has colours outside the {variant} brand palette: {colors}.",
                            "{what} : couleurs hors de la palette de marque {variant} : {colors}.",
                            "{what}: فيه ألوان خارج لوحة العلامة {variant}: {colors}.",
                            "{what}: есть цвета вне фирменной палитры {variant}: {colors}."),
    "over_max": _five("{what}: {have} — יותר מהמקסימום {limit}.", "{what}: {have} — over the maximum of {limit}.",
                      "{what} : {have} — au-delà du maximum de {limit}.", "{what}: {have} — أكثر من الحد الأقصى {limit}.",
                      "{what}: {have} — больше максимума {limit}."),
    "text_too_long": _five("{what}: {chars} תווים, המקסימום {limit}.", "{what}: {chars} characters, the maximum is {limit}.",
                           "{what} : {chars} caractères, le maximum est {limit}.", "{what}: {chars} حرفًا، الحد الأقصى {limit}.",
                           "{what}: {chars} знаков, максимум {limit}."),
    "text_too_few": _five("{what}: יש {have}, צריך לפחות {need}.", "{what}: {have}, at least {need} needed.",
                          "{what} : {have}, il en faut au moins {need}.", "{what}: {have}، المطلوب {need} على الأقل.",
                          "{what}: {have}, нужно не меньше {need}."),
    "text_too_many": _five("{what}: יש {have}, המקסימום {limit}.", "{what}: {have}, the maximum is {limit}.",
                           "{what} : {have}, le maximum est {limit}.", "{what}: {have}، الحد الأقصى {limit}.",
                           "{what}: {have}, максимум {limit}."),
    "text_needs_short": _five("{what}: לפחות אחת חייבת להיות עד {limit} תווים.", "{what}: at least one must be {limit} characters or fewer.",
                              "{what} : au moins un doit faire {limit} caractères ou moins.",
                              "{what}: يجب أن يكون واحد على الأقل بـ{limit} حرفًا أو أقل.",
                              "{what}: хотя бы один должен быть не длиннее {limit} знаков."),
    "text_empty": _five("{what}: ריק.", "{what}: empty.", "{what} : vide.", "{what}: فارغ.", "{what}: пусто."),
    "text_duplicate": _five("{what}: מופיע פעמיים.", "{what}: appears twice.", "{what} : en double.", "{what}: مكرر.",
                            "{what}: повторяется."),
    "text_not_taken": _five("{what}: לסוג הקמפיין הזה אין שדה כזה.", "{what}: this campaign type has no such field.",
                            "{what} : ce type de campagne n'a pas ce champ.", "{what}: نوع الحملة هذا لا يتضمن هذا الحقل.",
                            "{what}: в этом типе кампании такого поля нет."),
}


def slot_label(slot: str, lang: str, size: str = "") -> str:
    if str(slot).startswith("banner_"):
        return pick(SLOT_TEXT["banner"], lang).replace("{size}", size or str(slot)[len("banner_"):])
    return pick(SLOT_TEXT.get(slot) or {"en": str(slot)}, lang)


def problem_line(p: dict, lang: str = "he") -> str:
    """ONE problem → which asset, what is wrong, by how much."""
    what = slot_label(p.get("slot", ""), lang, p.get("size", ""))
    if p.get("n"):
        what += f" {p['n']}"
    text = pick(PROBLEM_TEXT.get(p.get("code")) or PROBLEM_TEXT["unreadable"], lang).replace("{what}", what)
    values = {**p, "kb": gaf.kb(p["bytes"]) if p.get("bytes") else "", "file_type": str(p.get("file_type") or "?").upper()}
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def problem_lines(problems: list, lang: str = "he", limit: int = 12) -> list:
    """Every problem once. "missing" for a slot comes after that slot's own
    file problems (they are why it is missing)."""
    out, seen = [], set()
    for p in sorted(problems or [], key=lambda q: (q.get("code") == "missing", str(q.get("slot")), q.get("n") or 0)):
        text = problem_line(p, lang)
        if text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def summary(order: dict) -> dict:
    """The order as the agents read it (prompt payloads, client_state): no rows, no bytes."""
    check = (order.get("delivery") or {}).get("check") or {}
    return {"id": order.get("id"), "campaign_type": order.get("campaign_type"), "objective": order.get("objective"),
            "offer": order.get("offer"), "landing_page_url": order.get("landing_page_url"),
            "deadline": order.get("deadline"), "status": order.get("status"),
            "handed_over": bool(order.get("handed_over_at")),
            "not_delivered": problem_lines(check.get("problems"), "en") if order.get("status") == "incomplete" else [],
            "can_serve": (order.get("verification") or {}).get("can_serve")}
