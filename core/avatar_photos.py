"""The photo SET a presenter is made from (2026-11-16, docs/CREATIVE-TOOLS.md §Photo set).

What the generation path supports today (checked in code, not assumed):
the presenter video takes ONE still image as its start frame plus the voice
(media_gen_service.generate_talking_character: `start_image` +
`audio_references`). There is no multi-photo identity training wired in. So
several photos do two real jobs here:
1. we CHOOSE the clearest front-facing, well-lit, sharp photo as the face every
   clip starts from (`best`), instead of whatever was sent first;
2. the client sees, before anything is made, whether their photos are good
   enough, and is asked for more when they are not.
A multi-reference character (trained from the whole set) would need a vendor
call that has not been verified live; it is NOT built and nothing here claims it.

What code checks, per photo: it is an image, its size (the short side,
from Drive's own image metadata), its light (too dark / too bright / flat,
from a small thumbnail), its sharpness, and duplicates (same file, or the
same picture twice). What code CANNOT check: the angle and the expression
(there is no face model here). Those are asked of the client in plain words,
and the agent says so.

The set grows across uploads: app_settings `avatar_photos:<id>` keeps the
file ids sent so far (30 days), so a photo sent now and another tomorrow make
one set and ONE card. Cleared with the client.
"""
import io
from datetime import datetime, timedelta, timezone

PREFIX = "avatar_photos:"
MIN_PHOTOS = 5
IDEAL_PHOTOS = 6
MAX_PHOTOS = 10
MIN_SHORT_SIDE = 720          # px; below this the face is too soft for a presenter
HARD_MIN_SHORT_SIDE = 400     # px; below this the photo is not usable at all
DARK, BRIGHT = 70, 200        # mean luminance 0-255 of the thumbnail
FLAT = 28                     # luminance standard deviation: no contrast at all
BLURRY = 60.0                 # variance of the edge image: below = soft / out of focus
KEEP_DAYS = 30
LANGS = ("he", "en", "fr", "ar", "ru")


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


ISSUE_TEXT = {
    "not_image": _five("זה לא קובץ תמונה", "it isn't an image file", "ce n'est pas une image", "ليست ملف صورة",
                       "это не изображение"),
    "too_small": _five("היא קטנה מדי (הפנים ייצאו מטושטשות)", "it's too small (the face would come out soft)",
                       "elle est trop petite (le visage serait flou)", "صغيرة جدًا (سيظهر الوجه غير واضح)",
                       "оно слишком маленькое (лицо выйдет размытым)"),
    "low_res": _five("הרזולוציה נמוכה, עדיף צילום חד יותר", "the resolution is low; a sharper shot is better",
                     "la résolution est faible ; une photo plus nette serait mieux", "الدقة منخفضة؛ صورة أوضح أفضل",
                     "разрешение низкое, лучше более чёткий снимок"),
    "dark": _five("היא חשוכה מדי", "it's too dark", "elle est trop sombre", "مظلمة جدًا", "оно слишком тёмное"),
    "bright": _five("יש בה יותר מדי אור (שרופה)", "it's too bright (washed out)", "elle est surexposée",
                    "مضاءة أكثر من اللازم", "оно пересвечено"),
    "flat": _five("התאורה שטוחה מאוד, בלי ניגודיות", "the light is very flat, with no contrast",
                  "la lumière est très plate, sans contraste", "الإضاءة مسطّحة جدًا بلا تباين",
                  "освещение очень плоское, без контраста"),
    "blurry": _five("היא לא חדה", "it isn't sharp", "elle n'est pas nette", "ليست حادة", "оно нечёткое"),
    "duplicate": _five("היא כמעט זהה לתמונה אחרת שכבר שלחתם", "it's almost the same as another photo you sent",
                       "elle est presque identique à une autre photo envoyée", "تكاد تطابق صورة أخرى أرسلتموها",
                       "оно почти такое же, как другое присланное фото"),
}

TEXT = {
    "why": _five(
        "כדי שהדמות תיראה בדיוק כמוכם, אני צריך {min}-{ideal} תמונות שלכם: מקדימה, בגובה העיניים, באור יום רך, עם כמה זוויות (ישר, וקצת לכל צד) וכמה הבעות (רגילה וחיוך). רקע פשוט, בלי משקפי שמש, כובע או פילטרים, ורק אתם בתמונה. מכולן אני בוחר את התמונה הקדמית הכי ברורה, וממנה מתחיל כל סרטון.",
        "For the character to look exactly like you, I need {min}-{ideal} photos of you: from the front, at eye level, in soft daylight, with a few angles (straight on, and a little to each side) and a few expressions (neutral and a smile). A plain background, no sunglasses, hat or filters, and only you in the shot. From all of them I choose the clearest front photo, and every video starts from it.",
        "Pour que le personnage vous ressemble vraiment, il me faut {min} à {ideal} photos de vous : de face, à hauteur des yeux, en lumière douce de jour, sous quelques angles (de face, et un peu de chaque côté) et quelques expressions (neutre et sourire). Fond simple, sans lunettes de soleil, chapeau ni filtre, et vous seul(e). Parmi elles je choisis la photo de face la plus nette, et chaque vidéo part d'elle.",
        "لكي تبدو الشخصية مثلكم تمامًا أحتاج {min}-{ideal} صور لكم: من الأمام، بمستوى العينين، في ضوء نهار ناعم، ببضع زوايا (مباشرة وقليلًا لكل جانب) وبضعة تعابير (عادي وابتسامة). خلفية بسيطة، بلا نظارات شمسية أو قبعة أو فلاتر، وأنتم فقط في الصورة. من بينها أختار الصورة الأمامية الأوضح، ومنها يبدأ كل فيديو.",
        "Чтобы персонаж был точно похож на вас, мне нужно {min}-{ideal} ваших фото: анфас, на уровне глаз, при мягком дневном свете, с разных ракурсов (прямо и немного в каждую сторону) и с разными выражениями (спокойное и улыбка). Простой фон, без солнечных очков, шляпы и фильтров, в кадре только вы. Из них я выберу самое чёткое фото анфас — с него начинается каждое видео."),
    "progress": _five("יש לי {count} מתוך {min} תמונות טובות.", "I have {count} of {min} good photos.",
                      "J'ai {count} photos réussies sur {min}.", "لديّ {count} من {min} صور جيدة.",
                      "У меня {count} из {min} хороших фото."),
    "more": _five("שלחו עוד {n} כאן בצ'אט עם ה-+, ואמשיך.", "Send {n} more here in the chat with the +, and I'll carry on.",
                  "Envoyez-en encore {n} ici avec le +, et je continue.", "أرسلوا {n} أخرى هنا في الدردشة عبر +، وسأكمل.",
                  "Пришлите ещё {n} здесь в чате через «+», и я продолжу."),
    "issue_line": _five("תמונה {i}: {issues}.", "Photo {i}: {issues}.", "Photo {i} : {issues}.", "الصورة {i}: {issues}.",
                        "Фото {i}: {issues}."),
    "angles": _five("את הזוויות וההבעות אני לא יכול לבדוק לבד: ודאו שיש לפחות אחת ישר מקדימה, אחת עם חיוך ואחת בזווית קלה.",
                    "Angles and expressions are something I can't check on my own: make sure there is at least one straight from the front, one with a smile and one at a slight angle.",
                    "Les angles et les expressions, je ne peux pas les vérifier seul : assurez-vous d'avoir au moins une photo de face, une avec un sourire et une légèrement de côté.",
                    "لا أستطيع التحقق من الزوايا والتعابير وحدي: تأكدوا أن هناك صورة واحدة على الأقل من الأمام، وواحدة بابتسامة، وواحدة بزاوية خفيفة.",
                    "Ракурсы и выражения я сам проверить не могу: убедитесь, что есть хотя бы одно фото анфас, одно с улыбкой и одно немного сбоку."),
    "ready": _five("התמונות טובות ✓ בחרתי את התמונה הקדמית הכי ברורה ({best} מתוך {count}) להיות הפנים של הסרטונים.",
                   "The photos are good ✓ I chose the clearest front photo ({best} of {count}) to be the face of the videos.",
                   "Les photos sont bonnes ✓ J'ai choisi la photo de face la plus nette ({best} sur {count}) comme visage des vidéos.",
                   "الصور جيدة ✓ اخترت الصورة الأمامية الأوضح ({best} من {count}) لتكون وجه الفيديوهات.",
                   "Фото хорошие ✓ Я выбрал самое чёткое фото анфас ({best} из {count}) — это лицо для видео."),
}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or table.get("he") or ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db():
    from agents.client_agent import _db as db
    return db()


# ─── The set across uploads ──────────────────────────────────────────────────

def stored(client_id: int) -> list:
    try:
        rows = _db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}").limit(1) \
            .execute().data or []
    except Exception:
        return []
    value = (rows[0].get("value") if rows else None) or {}
    if str(value.get("at") or "") < (_now() - timedelta(days=KEEP_DAYS)).isoformat():
        return []
    return [str(i) for i in value.get("ids") or [] if i][:MAX_PHOTOS]


def remember(client_id: int, ids: list):
    try:
        _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}",
                                            "value": {"ids": list(ids)[:MAX_PHOTOS], "at": _now().isoformat()},
                                            "updated_at": _now().isoformat()}, on_conflict="key").execute()
    except Exception as e:
        print(f"[avatar_photos] client {client_id}: set not saved ({type(e).__name__})")


def clear(client_id: int):
    """The presenter was made: the next set starts empty."""
    try:
        _db().table("app_settings").delete().eq("key", f"{PREFIX}{int(client_id)}").execute()
    except Exception as e:
        print(f"[avatar_photos] client {client_id}: set not cleared ({type(e).__name__})")


def merge(client_id: int, new_ids: list) -> list:
    """The new photos first, then the ones sent earlier; at most MAX_PHOTOS."""
    out = []
    for file_id in list(new_ids or []) + stored(client_id):
        if file_id and file_id not in out:
            out.append(file_id)
    return out[:MAX_PHOTOS]


# ─── One photo ───────────────────────────────────────────────────────────────

def _quality(thumb: bytes) -> dict:
    """Light, contrast, sharpness and a small fingerprint from a thumbnail.
    {} when Pillow is missing or the bytes can't be read (then those checks
    are simply not made — never a guess)."""
    try:
        from PIL import Image, ImageFilter, ImageStat
        image = Image.open(io.BytesIO(thumb)).convert("L")
        image.thumbnail((256, 256))
        stat = ImageStat.Stat(image)
        # Pillow's 3x3 kernel filters leave the 1-pixel frame UNFILTERED (the
        # source pixels are copied), so on a flat photo the frame alone made
        # the edge variance look sharp (~330 for flat grey). Measure inside it.
        edge_image = image.filter(ImageFilter.FIND_EDGES)
        if edge_image.width > 2 and edge_image.height > 2:
            edge_image = edge_image.crop((1, 1, edge_image.width - 1, edge_image.height - 1))
        edges = ImageStat.Stat(edge_image)
        small = image.resize((8, 8))
        pixels = list(small.getdata())
        mean = sum(pixels) / len(pixels)
        return {"brightness": round(stat.mean[0], 1), "contrast": round(stat.stddev[0], 1),
                "sharpness": round(edges.var[0], 1),
                "fingerprint": "".join("1" if p > mean else "0" for p in pixels)}
    except Exception:
        return {}


def assess(meta: dict, thumb: bytes = b"") -> dict:
    """{"id", "issues": [...], "usable", "score"} for one photo."""
    issues = []
    if not str(meta.get("mimeType") or "").startswith("image/"):
        return {"id": meta.get("id"), "issues": ["not_image"], "usable": False, "score": 0}
    image = meta.get("imageMediaMetadata") or {}
    width, height = int(image.get("width") or 0), int(image.get("height") or 0)
    short = min(width, height) if width and height else 0
    if short and short < HARD_MIN_SHORT_SIDE:
        issues.append("too_small")
    elif short and short < MIN_SHORT_SIDE:
        issues.append("low_res")
    quality = _quality(thumb) if thumb else {}
    if quality:
        if quality["brightness"] < DARK:
            issues.append("dark")
        elif quality["brightness"] > BRIGHT:
            issues.append("bright")
        elif quality["contrast"] < FLAT:
            issues.append("flat")
        if quality["sharpness"] < BLURRY:
            issues.append("blurry")
    hard = {"not_image", "too_small", "dark", "bright"}
    # The thresholds are not yet calibrated on real client photos: only a CLEARLY
    # soft photo blocks; a borderline one is a note and simply ranks lower.
    very_soft = quality and quality["sharpness"] < BLURRY / 3
    usable = not (set(issues) & hard) and not very_soft
    # The face every clip starts from: big, sharp, well and evenly lit
    score = (min(short, 2000) / 2000 if short else 0.3) + (min(quality.get("sharpness", 0), 600) / 600) \
        + (1 - abs(quality.get("brightness", 135) - 135) / 135) - 0.5 * len(issues)
    return {"id": meta.get("id"), "issues": issues, "usable": usable, "score": round(score, 3),
            "md5": meta.get("md5Checksum") or "", "fingerprint": quality.get("fingerprint", ""), "short_side": short}


def _near(a: str, b: str) -> bool:
    return bool(a and b) and sum(x != y for x, y in zip(a, b)) <= 3


def review_assessed(photos: list) -> dict:
    """The set's verdict from assessed photos (assess), in the order sent."""
    seen = []
    for photo in photos:
        if any((photo["md5"] and photo["md5"] == other["md5"]) or _near(photo["fingerprint"], other["fingerprint"])
               for other in seen):
            photo["issues"].append("duplicate")
            photo["usable"] = False
        seen.append(photo)
    good = [p for p in photos if p["usable"]]
    best = max(good, key=lambda p: p["score"]) if good else None
    return {"count": len(photos), "good": len(good), "needed": max(0, MIN_PHOTOS - len(good)),
            "ready": len(good) >= MIN_PHOTOS, "best": best["id"] if best else "",
            "best_position": (photos.index(best) + 1) if best else 0, "photos": photos}


def review(client_id: int, ids: list) -> dict:
    """Read each photo's Drive metadata + a small thumbnail and judge the set.
    A photo Drive can't read is left out (and counted as not good)."""
    from core import drive_service, drive_storage
    photos = []
    with drive_storage.scope(client_id):
        for file_id in list(ids)[:MAX_PHOTOS]:
            try:
                meta = drive_service.get_meta(file_id, "id,name,mimeType,trashed,md5Checksum,"
                                                       "imageMediaMetadata(width,height),thumbnailLink")
            except Exception:
                continue
            if meta.get("trashed"):
                continue
            thumb = b""
            link = str(meta.get("thumbnailLink") or "")
            if link:
                try:
                    thumb, _type = drive_service.fetch_thumbnail(link.split("=s")[0] + "=s400", 2 * 1024 * 1024)
                except Exception:
                    thumb = b""
            photos.append(assess(meta, thumb))
    return review_assessed(photos)


def message(result: dict, lang: str) -> str:
    """The plain words for the client: why, how far along, what to fix."""
    lang = lang if lang in LANGS else "he"
    if result["ready"]:
        return pick(TEXT["ready"], lang).replace("{best}", str(result["best_position"])) \
            .replace("{count}", str(result["count"]))
    parts = [pick(TEXT["why"], lang).replace("{min}", str(MIN_PHOTOS)).replace("{ideal}", str(IDEAL_PHOTOS))]
    if result["count"]:
        parts.append(pick(TEXT["progress"], lang).replace("{count}", str(result["good"])).replace("{min}", str(MIN_PHOTOS)))
    for i, photo in enumerate(result["photos"], 1):
        if photo["issues"]:
            words = ", ".join(pick(ISSUE_TEXT[c], lang) for c in photo["issues"] if c in ISSUE_TEXT)
            parts.append(pick(TEXT["issue_line"], lang).replace("{i}", str(i)).replace("{issues}", words))
    parts.append(pick(TEXT["angles"], lang))
    parts.append(pick(TEXT["more"], lang).replace("{n}", str(max(1, result["needed"]))))
    return "\n".join(parts)
