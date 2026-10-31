"""The two videos a filmed presenter is made from, uploaded from the
dashboard — never "name the file 'consent' in a Drive folder".

The dashboard's presenter card has two clearly labelled slots:

    training  the 2-3 minute video of the client talking to camera
    consent   the short clip of the client saying CONSENT_SENTENCES — shown on
              screen in the client's language and recorded in the browser
              (or uploaded)

Both go through the SAME chunked upload as the chat (core/chat_uploads, the
`X-Avatar-Slot` header): the file lands in the client's `avatar-source`
folder and the slot is written ON the file as an appProperty (SLOT_PROP), so
the system knows which video is which from the slot the client used, never
from a filename. The newest file in a slot wins.

Fallback for files that were already in avatar-source before the slots
existed (the old Drive-folder flow): a video named with "consent" is the
consent clip and the largest other video is the training footage — `pick`
uses that only for a slot the client has not filled from the dashboard.
"""
from core import drive_storage

SLOTS = ("training", "consent")
SLOT_PROP = "uallak_avatar_slot"
KIND = "avatar_footage"
FOLDER = "avatar-source"

# What the client says on camera. The tool validates consent by meaning, not
# word for word, and accepts any language — so it is in the client's language
# and names no vendor (vendor names are never client-facing).
# TODO(owner-verify): confirm on a live key that the tool accepts this
# wording; if it insists on its own text, change it HERE only.
CONSENT_SENTENCES = {
    "he": "אני, {name}, מאשר/ת ליצור דמות דיגיטלית מצולמת שלי מהסרטונים שהעליתי, ולהשתמש בה בסרטונים עבור העסק שלי.",
    "en": "I, {name}, give my permission to create a filmed digital likeness of me from the videos I uploaded, and to use it in videos for my business.",
    "fr": "Moi, {name}, j'autorise la création d'un double numérique filmé de moi à partir des vidéos que j'ai envoyées, et son utilisation dans des vidéos pour mon entreprise.",
    "ar": "أنا، {name}، أوافق على إنشاء نسخة رقمية مصوَّرة مني من الفيديوهات التي رفعتها، واستخدامها في فيديوهات لعملي.",
    "ru": "Я, {name}, разрешаю создать мой цифровой образ по загруженным мной видео и использовать его в видео для моего бизнеса.",
}

PLAIN_HE = {
    "missing_training": "חסר סרטון האימון — מעלים אותו בכרטיס \"הדמות שבסרטונים\".",
    "missing_consent": "חסר סרטון ההסכמה — מקליטים אותו בכרטיס \"הדמות שבסרטונים\", עם המשפט שמופיע שם.",
}


# The client's own full name is said aloud in its place (we store a business
# name, not the person's)
NAME_PLACEHOLDER = {"he": "[השם המלא שלך]", "en": "[your full name]", "fr": "[votre nom complet]",
                    "ar": "[اسمك الكامل]", "ru": "[ваше полное имя]"}


def consent_sentence(lang: str, name: str = "") -> str:
    lang = lang if lang in CONSENT_SENTENCES else "he"
    return CONSENT_SENTENCES[lang].replace("{name}", (name or "").strip() or NAME_PLACEHOLDER[lang])


@drive_storage.scoped
def folder(client_id: int) -> str:
    from agents.media_agent import _subfolder
    return _subfolder(client_id, FOLDER)


def _props(f: dict) -> dict:
    return f.get("appProperties") or {}


def _is_video(f: dict) -> bool:
    return str(f.get("mimeType") or "").startswith("video/")


def pick(files: list) -> dict:
    """{"training": file|None, "consent": file|None, "source": {slot: "slot"|"drive_folder"}}.
    A dashboard slot always wins; the filename convention is only the
    fallback for files that predate the slots."""
    videos = [f for f in files or [] if _is_video(f)]
    chosen, source = {}, {}
    for slot in SLOTS:
        tagged = [f for f in videos if _props(f).get(SLOT_PROP) == slot]
        if tagged:
            chosen[slot] = max(tagged, key=lambda f: _props(f).get("uploaded_at") or f.get("createdTime") or "")
            source[slot] = "slot"
    untagged = [f for f in videos if not _props(f).get(SLOT_PROP)]
    if "consent" not in chosen:
        legacy = next((f for f in untagged if "consent" in (f.get("name") or "").lower()), None)
        if legacy:
            chosen["consent"], source["consent"] = legacy, "drive_folder"
    if "training" not in chosen:
        rest = [f for f in untagged if f is not chosen.get("consent")
                and "consent" not in (f.get("name") or "").lower()]
        if rest:
            chosen["training"] = max(rest, key=lambda f: int(f.get("size") or 0))
            source["training"] = "drive_folder"
    return {"training": chosen.get("training"), "consent": chosen.get("consent"), "source": source}


def _row(f: dict, how: str) -> dict:
    if not f:
        return None
    return {"id": f.get("id"), "name": f.get("name", ""), "size": int(f.get("size") or 0),
            "uploaded_at": _props(f).get("uploaded_at") or f.get("createdTime") or "", "source": how}


def view(files: list, lang: str) -> dict:
    """What the dashboard card shows: each slot's file (or None), the exact
    sentence to say, and whether both videos are there."""
    chosen = pick(files)
    return {"training": _row(chosen["training"], chosen["source"].get("training", "")),
            "consent": _row(chosen["consent"], chosen["source"].get("consent", "")),
            "sentence": consent_sentence(lang),
            "ready": bool(chosen["training"] and chosen["consent"])}
