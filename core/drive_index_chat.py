"""The conversational trigger for the real-asset index (2026-11-23,
docs/DRIVE-FOLDERS.md §Change detection).

When a client tells an agent in chat that they have uploaded files ("I put
the photos in the folder", after the system asked them to photograph items
for an avatar, say), the relevant connection is indexed NOW — not at the next
scheduled change check:

- detected from the message by code (an upload verb + a files/folder word, in
  the client's languages; "not yet" / "didn't" never counts);
- one connected folder (or the message names one) → indexed at once, and the
  reply gains one line saying so;
- several and none named → ONE short question naming them; the next message
  that names one (or "all") indexes it. The question waits 30 minutes.

The walk is free (listing); describing new files stays inside the client's
allowance like any other run. Nothing here sends anything to the model.
"""
import re
from datetime import datetime, timedelta, timezone

PICK_KEY = "chat_pick"
PICK_TTL_MIN = 30
ANSWER_MAX_CHARS = 80          # an answer to "which folder?" is short; longer turns aren't read for it

VERBS = {
    "he": r"(העליתי|העלתי|העלינו|העלנו|העלאתי|הוספתי|הוספנו|שמתי|שמנו|הכנסתי|הכנסנו|גררתי|סיימתי להעלות|"
          r"סיימנו להעלות|צילמתי והעליתי)",
    "en": r"\b(uploaded|added|put|dropped|copied|moved|saved|placed)\b",
    "fr": r"(j'ai|j’ai|nous avons|on a)\s+(t[ée]l[ée]vers|t[ée]l[ée]charg|ajout|mis|d[ée]pos|import|copi)",
    "ar": r"(رفعت|رفعنا|أضفت|اضفت|أضفنا|حملت|نزلت|وضعت)",
    "ru": r"(загрузил|загрузила|загрузили|добавил|добавила|добавили|закинул|закинула|закинули|положил|положила)",
}
OBJECTS = {
    "he": r"(תמונ|צילומ|קבצ|קובץ|סרטונ|סרטון|וידאו|תיקי|דרייב|אלבום)",
    "en": r"\b(photos?|pictures?|pics|images?|files?|videos?|clips?|folders?|drive|shots)\b",
    "fr": r"(photos?|images?|fichiers?|vid[ée]os?|dossiers?|drive)",
    "ar": r"(صور|صورة|ملف|ملفات|فيديو|مجلد|درايف)",
    "ru": r"(фото|снимк|файл|видео|папк|драйв|диск)",
}
NOT_YET = {
    "he": r"(לא העל|עוד לא|טרם|לא הוספ|לא שמתי|איך מעל|איך להעלות|אפשר להעלות|צריך להעלות|אעלה)",
    "en": r"\b(not yet|haven'?t|have not|didn'?t|did not|how do i|how to|should i|will upload|going to)\b",
    "fr": r"(pas encore|n'ai pas|n’ai pas|comment)",
    "ar": r"(لم |ليس بعد|كيف)",
    "ru": r"(ещё не|еще не|не загруз|как загруз)",
}
ALL_WORDS = {
    "he": r"(כולן|כל התיקיות|בכולן|לכולן|בכל התיקיות)",
    "en": r"\b(all of them|all folders|all the folders|every folder|both)\b",
    "fr": r"(toutes|tous les dossiers|les deux)",
    "ar": r"(كلها|كل المجلدات)",
    "ru": r"(все папки|во все|обе)",
}
T = {
    "indexing": {
        "he": "קיבלתי — אני סורק עכשיו את התיקייה \"{folder}\", והקבצים החדשים יהיו זמינים לי בעוד כמה דקות.",
        "en": "Got it — I'm scanning the folder \"{folder}\" now; the new files will be ready for me in a few minutes.",
        "fr": "C'est noté — j'analyse maintenant le dossier « {folder} » ; les nouveaux fichiers seront prêts "
              "dans quelques minutes.",
        "ar": "تم — أفحص الآن المجلد \"{folder}\"، وستكون الملفات الجديدة جاهزة لي خلال دقائق.",
        "ru": "Понял — сейчас сканирую папку «{folder}»; новые файлы будут доступны мне через несколько минут.",
    },
    "which": {
        "he": "לאיזו תיקייה העליתם — {options}?",
        "en": "Which folder did you upload to — {options}?",
        "fr": "Dans quel dossier les avez-vous mis — {options} ?",
        "ar": "إلى أي مجلد رفعتم الملفات — {options}؟",
        "ru": "В какую папку вы загрузили — {options}?",
    },
    "or": {"he": " או ", "en": " or ", "fr": " ou ", "ar": " أو ", "ru": " или "},
    "and": {"he": " ו", "en": " and ", "fr": " et ", "ar": " و", "ru": " и "},
}
LANGS = ("he", "en", "fr", "ar", "ru")


def _t(key: str, lang: str) -> str:
    table = T[key]
    return table.get(lang) or table["en"]


def _any(patterns: dict, text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns.values())


def says_uploaded(message: str) -> bool:
    text = str(message or "")
    return bool(text.strip()) and _any(VERBS, text) and _any(OBJECTS, text) and not _any(NOT_YET, text)


def _roots(client_id: int) -> list:
    from core import drive_assets, drive_folders
    return [r for r in drive_folders.load(client_id) if not r.get("parent_connection_id")
            and r.get("role") != drive_assets.NOT_INDEXED_ROLE]


def named(message: str, roots: list) -> list:
    """The roots the message names (by folder name, else by role words);
    every root for "all of them"."""
    from core import drive_folders, media_search
    if _any(ALL_WORDS, str(message or "")):
        return [r["id"] for r in roots]
    text = media_search.compact(message)
    hits = [r["id"] for r in roots if len(media_search.compact(r.get("display_name", ""))) >= 2
            and media_search.compact(r.get("display_name", "")) in text]
    if hits:
        return hits
    by_role = [r["id"] for r in roots
               if media_search.compact(drive_folders.ROLE_TEXT.get(r.get("role", ""), "")) not in ("",)
               and media_search.compact(drive_folders.ROLE_TEXT.get(r.get("role", ""), "")) in text]
    return by_role


def _now():
    return datetime.now(timezone.utc)


def _pending(state: dict) -> list:
    pick = state.get(PICK_KEY) or {}
    try:
        fresh = _now() - datetime.fromisoformat(pick.get("at", "")) < timedelta(minutes=PICK_TTL_MIN)
    except (TypeError, ValueError):
        fresh = False
    return list(pick.get("roots") or []) if fresh else []


def _set_pending(client_id: int, roots: list):
    from core import drive_assets
    state = drive_assets._load(client_id)
    if roots:
        state[PICK_KEY] = {"at": _now().isoformat(), "roots": roots}
    else:
        state.pop(PICK_KEY, None)
    drive_assets._save(client_id, state)


def _start(client_id: int, root_ids: list, background_tasks=None):
    from core import drive_assets
    drive_assets.index_now(client_id, root_ids, mode="chat")
    if background_tasks is not None:
        background_tasks.add_task(drive_assets.step, client_id, 60.0)    # the rest: the every-minute tick


def _names(roots: list, ids: list, lang: str) -> str:
    names = [f"\"{r.get('display_name', '')}\"" if lang != "fr" else f"« {r.get('display_name', '')} »"
             for r in roots if r["id"] in ids]
    if len(names) <= 1:
        return "".join(names).strip("\"«» ")
    return ", ".join(names[:-1]) + _t("and", lang) + names[-1]


def turn(client_id: int, message: str, lang: str = "he", background_tasks=None) -> dict:
    """{"ask": question} (the reply IS this one question), {"line": text}
    (append to the reply) or {} (nothing to do). Never raises."""
    try:
        from core import drive_assets
        uploaded = says_uploaded(message)
        if not uploaded and len(str(message or "")) > ANSWER_MAX_CHARS:
            return {}                                   # no read on an ordinary chat turn
        state = drive_assets._load(client_id)
        waiting = _pending(state)
        if not uploaded and not waiting:
            return {}
        roots = _roots(client_id)
        if not roots:
            return {}
        pool = [r for r in roots if r["id"] in waiting] if (waiting and not uploaded) else roots
        chosen = named(message, pool)
        if not chosen and uploaded and len(roots) == 1:
            chosen = [roots[0]["id"]]
        if chosen:
            _start(client_id, chosen, background_tasks)
            if waiting:
                _set_pending(client_id, [])
            return {"line": _t("indexing", lang).replace("{folder}", _names(roots, chosen, lang)), "roots": chosen}
        if not uploaded:
            return {}                                   # a question is waiting; this message isn't its answer
        _set_pending(client_id, [r["id"] for r in roots])
        options = [r.get("display_name", "") for r in roots][:6]
        joined = ", ".join(options[:-1]) + _t("or", lang) + options[-1] if len(options) > 1 else options[0]
        return {"ask": _t("which", lang).replace("{options}", joined)}
    except Exception as e:
        print(f"[drive_index_chat] client {client_id}: {type(e).__name__}: {e}")
        return {}
