"""Approval cards in the client chat: free-language yes / stop / undo (2026-11-17).

Client 27, 2026-11-17: the agent kept saying "I've put a card right below" while
the card sat higher up the thread (or did not exist), and "ok upload it", "i
approve", "תעלה" did nothing, because only a click on the card ran anything.

What is true now (docs/CHAT-APPROVALS.md):
- TYPING AGREEMENT == PRESSING THE BUTTON. The chat model reads the message and
  sets `card_decision` {verdict, card_id} (SUPPORT RULE below) — never a phrase
  list as the condition. A short keyword match is only a FAST PATH for the
  obvious cases (an explicit "approve" with exactly one card open, or "stop"
  while one run is in its stop window). The run goes through the SAME
  agent_actions.decide() a click calls: every check is redone from the stored
  row, compare-and-set claims it, a double "yes" runs it once.
- Several open cards and no clear one → ONE question listing them; one card,
  or a clear reference → it runs.
- Before a heavy (slow) or paid action: one line — what is about to happen, the
  cost if there is one, whether it can be undone. Never a demand to click.
- STOP, at ANY stage (2026-11-19): every running card has a Stop button and a
  typed "stop" works the whole time. Inside the first STOP_WINDOW_SECONDS
  nothing has run yet (cancelled completely). After it, agent_actions.stop_action
  decides by the action's stop mode: drafts/research end at once and nothing
  more is shown; a TikTok upload stops between chunks; a step that can't be
  cut (publish, site update, paid render) finishes and the card says so. One
  line always says what did and did not happen.
- UNDO: where it is genuinely possible (UNDO below) the finished action offers
  ONE undo card; where it is not, that is said BEFORE it runs (the card's
  `undo_note` and the heads-up line).
- NEVER A CARD THAT ISN'T THERE: a reply that speaks of a card while none was
  attached re-surfaces the open card (bottom of the thread) or says plainly
  that no card was made and what happens next (claim_guard).
- NO LOOPS: the third near-identical "it's waiting on the card" reply is
  replaced by the exact thing that blocks it (loop_guard).
tests/test_chat_approvals.py.
"""
import difflib
import re
from datetime import datetime, timedelta, timezone

LANGS = ("he", "en", "fr", "ar", "ru")
VERDICTS = ("approve", "cancel", "stop", "undo")


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table["he"]


def _aa():
    from core import agent_actions
    return agent_actions


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(stamp):
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except Exception:
        return None


# ─── What can be undone, what costs money ────────────────────────────────────
# action → the ONE undo proposal it offers when done (built from the stored row)
UNDO = {
    "configure_conversion": lambda row: {"name": "revert_conversion", "params": {}},
    "publish_landing_page": lambda row: {"name": "unpublish_landing_page",
                                         "params": {"page_id": (row.get("params") or {}).get("page_id")}},
}
# action → how it can be reversed by hand (no automatic undo, but not lost)
UNDO_BY_HAND = {
    "publish_youtube_from_media": _five(
        "הסרטון עולה כפרטי. אם תתחרטו, אפשר למחוק אותו ב-YouTube Studio.",
        "The video goes up as private. If you change your mind, you can delete it in YouTube Studio.",
        "La vidéo est mise en ligne en privé. Si vous changez d'avis, vous pouvez la supprimer dans YouTube Studio.",
        "يُرفع الفيديو كخاص. إن غيّرتم رأيكم يمكن حذفه في YouTube Studio.",
        "Видео загружается как приватное. Если передумаете, его можно удалить в YouTube Studio."),
    "send_tiktok_to_inbox": _five(
        "הסרטון מגיע לטיוטות שלכם בטיקטוק. אם תתחרטו, פשוט מוחקים את הטיוטה שם.",
        "The video lands in your TikTok drafts. If you change your mind, just delete the draft there.",
        "La vidéo arrive dans vos brouillons TikTok. Si vous changez d'avis, supprimez simplement le brouillon.",
        "يصل الفيديو إلى مسوداتكم في تيك توك. إن غيّرتم رأيكم احذفوا المسودة هناك.",
        "Видео попадёт в ваши черновики TikTok. Если передумаете, просто удалите черновик там."),
}
# Spent or sent for good — said BEFORE it runs
IRREVERSIBLE = {"send_outreach_email", "send_outreach_followup", "delete_landing_page", "approve_production_quote",
                "clone_voice", "make_presenter_video", "create_presenter_from_photos", "send_tiktok_batch",
                "produce_requested_content"}
# action → the plain cost line (the client's own vendor credits, or a quoted price)
COST = {
    "approve_production_quote": _five("זה עולה את המחיר שבהצעה.", "It costs the price on the quote.",
                                      "Cela coûte le prix indiqué dans le devis.", "التكلفة هي السعر المذكور في العرض.",
                                      "Это стоит цену из предложения."),
    "clone_voice": _five("זה משתמש בקרדיטים בחשבון שלכם בכלי הקול.", "It uses credits on your own voice tool account.",
                         "Cela utilise des crédits de votre propre compte de l'outil vocal.",
                         "يستخدم ذلك رصيدًا من حسابكم في أداة الصوت.",
                         "Это расходует кредиты вашего аккаунта голосового инструмента."),
    "make_presenter_video": _five("זה משתמש בקרדיטים בחשבון שלכם בכלי הווידאו.",
                                  "It uses credits on your own video tool account.",
                                  "Cela utilise des crédits de votre propre compte de l'outil vidéo.",
                                  "يستخدم ذلك رصيدًا من حسابكم في أداة الفيديو.",
                                  "Это расходует кредиты вашего аккаунта видеоинструмента."),
    "create_presenter_from_photos": _five("זה משתמש בקרדיטים בחשבון שלכם בכלי הווידאו.",
                                          "It uses credits on your own video tool account.",
                                          "Cela utilise des crédits de votre propre compte de l'outil vidéo.",
                                          "يستخدم ذلك رصيدًا من حسابكم في أداة الفيديو.",
                                          "Это расходует кредиты вашего аккаунта видеоинструмента."),
    "produce_requested_content": _five("זה משתמש בקרדיטים בחשבון שלכם בכלי התמונות והווידאו.",
                                       "It uses credits on your own image and video tool account.",
                                       "Cela utilise des crédits de votre propre compte de l'outil images et vidéo.",
                                       "يستخدم ذلك رصيدًا من حسابكم في أداة الصور والفيديو.",
                                       "Это расходует кредиты вашего аккаунта инструмента изображений и видео."),
}

CAN_UNDO = _five("אם תתחרטו, אפשר לבטל את זה אחר כך: פשוט כתבו לי.",
                 "If you change your mind, it can be undone afterwards: just tell me.",
                 "Si vous changez d'avis, on peut l'annuler ensuite : dites-le-moi.",
                 "إن غيّرتم رأيكم يمكن التراجع عنه لاحقًا: فقط أخبروني.",
                 "Если передумаете, это можно отменить потом: просто напишите мне.")
CANNOT_UNDO = _five("את זה אי אפשר לבטל אחרי שזה רץ.", "This can't be undone once it has run.",
                    "Cela ne peut pas être annulé une fois lancé.", "لا يمكن التراجع عن هذا بعد تنفيذه.",
                    "Это нельзя отменить после выполнения.")
STOP_HINT = _five("אפשר לעצור בכל שלב: כפתור \"עצור\" בכרטיס, או לכתוב לי \"עצור\".",
                  "You can stop it at any stage: the \"Stop\" button on the card, or just write \"stop\".",
                  "Vous pouvez l'arrêter à tout moment : le bouton « Arrêter » sur la carte, ou écrivez « stop ».",
                  "يمكنكم الإيقاف في أي مرحلة: زر \"إيقاف\" في البطاقة، أو اكتبوا \"توقف\".",
                  "Остановить можно на любом этапе: кнопка «Стоп» на карточке или просто напишите «стоп».")
STARTING = _five("מתחיל: {title}.", "Starting: {title}.", "Je lance : {title}.", "أبدأ: {title}.", "Запускаю: {title}.")


def heavy(spec: dict) -> bool:
    return bool(spec.get("slow"))


def undo_note(action: str, spec: dict, lang: str) -> str:
    """The card's line on whether it can be undone — for heavy, paid or
    irreversible actions only ('' for the light ones)."""
    if action in UNDO:
        return pick(CAN_UNDO, lang)
    if action in UNDO_BY_HAND:
        return pick(UNDO_BY_HAND[action], lang)
    if action in IRREVERSIBLE or action in COST or heavy(spec):
        return pick(CANNOT_UNDO, lang)
    return ""


def heads_up(row: dict, lang: str) -> str:
    """One line before a heavy or paid action runs: what, the cost, undo, the
    stop window. '' for a light action."""
    aa = _aa()
    action = row.get("action") or ""
    spec = aa.ACTIONS.get(action) or {}
    if not (heavy(spec) or action in COST or action in IRREVERSIBLE):
        return ""
    title = pick(spec.get("title") or {"he": action}, lang)
    parts = [pick(STARTING, lang).replace("{title}", title)]
    if action in COST:
        parts.append(pick(COST[action], lang))
    note = undo_note(action, spec, lang)
    if note:
        parts.append(note)
    if heavy(spec) and not spec.get("inline_start") and aa.STOP_WINDOW_SECONDS:
        parts.append(pick(STOP_HINT, lang))
    return " ".join(parts)


# ─── The open and running cards of this chat ─────────────────────────────────

def _client_rows(client_id: int, statuses: tuple) -> list:
    aa = _aa()
    return [r for r in aa._open_rows(client_id, statuses)
            if r.get("origin") == "client_chat" and not aa._expired(r)]


def pending(client_id: int, persona: str = None) -> list:
    """The cards waiting for THIS client's yes, newest first: this window's
    first; when it has none, every window's (the client may answer anywhere)."""
    aa = _aa()
    rows = [r for r in _client_rows(client_id, aa.OPEN_STATUSES)
            if r.get("approver") == "client" and r.get("status") == "proposed"
            and r.get("action") != "team_request"]
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    here = [r for r in rows if persona and (r.get("persona") or "general") == persona]
    return here or rows


def running(client_id: int, persona: str = None) -> list:
    rows = [r for r in _client_rows(client_id, ("running",))]
    rows.sort(key=lambda r: str(r.get("decided_at") or ""), reverse=True)
    here = [r for r in rows if persona and (r.get("persona") or "general") == persona]
    return here or rows


def for_model(client_id: int, persona: str, lang: str) -> dict:
    """{"pending_cards": [...], "running_cards": [...]} for the chat payload;
    {} when there is nothing (the payload then carries no block)."""
    aa = _aa()
    out = {}
    try:
        cards = pending(client_id, persona)
        runs = running(client_id, persona)
    except Exception:
        return {}
    if cards:
        out["pending_cards"] = [{"card_id": r.get("id"), "action": r.get("action"),
                                 "title": pick((aa.ACTIONS.get(r.get("action")) or {}).get("title")
                                               or {"he": r.get("action", "")}, lang),
                                 "created_at": str(r.get("created_at") or "")[:16]} for r in cards[:6]]
    if runs:
        out["running_cards"] = [{"card_id": r.get("id"), "action": r.get("action"),
                                 "title": pick((aa.ACTIONS.get(r.get("action")) or {}).get("title")
                                               or {"he": r.get("action", "")}, lang)} for r in runs[:3]]
    return out


# ─── Fast path (obvious cases only — never the condition) ────────────────────
FAST_APPROVE = re.compile(
    r"^\s*(?:i\s+)?(?:approve[ds]?|approved|אשר|אישור|מאשר|מאשרת|מאושר|j'approuve|approuvé|одобряю|подтверждаю|"
    r"أوافق|موافق)\s*[.!✓✔️👍]*\s*$", re.IGNORECASE)
FAST_STOP = re.compile(
    r"^\s*(?:stop|wait|עצור|עצרי|תעצור|רגע|חכה|חכי|תפסיק|stop+e?|arrête|attends|стоп|подожди|توقف|انتظر)\s*[.!]*\s*$",
    re.IGNORECASE)


# ─── The texts ───────────────────────────────────────────────────────────────
ASK_WHICH = _five("על איזה מהם? {list}", "Which one do you mean? {list}", "Lequel voulez-vous dire ? {list}",
                  "أيّها تقصدون؟ {list}", "Какую из них? {list}")
STOPPED = _five("עצרתי ✓ שום דבר לא רץ, ולא השתנה כלום.", "Stopped ✓ Nothing ran and nothing changed.",
                "Arrêté ✓ Rien n'a été lancé, rien n'a changé.", "أوقفته ✓ لم يُنفَّذ شيء ولم يتغير شيء.",
                "Остановлено ✓ Ничего не запустилось и ничего не изменилось.")
JOBS_STOPPED = _five("עצרתי ✓ ההעלאות לטיקטוק שעוד לא הסתיימו נעצרו ({n}). שום דבר מהן לא הגיע לטיוטות, והקבצים נשארים בתיקיית המדיה.",
                     "Stopped ✓ The TikTok uploads that hadn't finished were stopped ({n}). None of them reached your drafts, and the files stay in your media folder.",
                     "Arrêté ✓ Les envois TikTok non terminés ont été arrêtés ({n}). Aucun n'est arrivé dans vos brouillons, les fichiers restent dans votre dossier médias.",
                     "أوقفته ✓ توقفت عمليات الرفع إلى تيك توك التي لم تنتهِ ({n}). لم يصل أيٌّ منها إلى المسودات، وتبقى الملفات في مجلد الوسائط.",
                     "Остановлено ✓ Незавершённые загрузки в TikTok остановлены ({n}). Ни одна не попала в черновики, файлы остаются в папке с медиа.")
TOO_LATE = _five("זה כבר התחיל ולא נעצר באמצע: {title} ממשיך עד הסוף, ואכתוב לכם כאן מה יצא. {state}",
                 "It has already started and can't stop halfway: {title} carries on to the end, and I'll write here what came of it. {state}",
                 "C'est déjà lancé et ne s'arrête pas en route : {title} va jusqu'au bout, je vous écris ici le résultat. {state}",
                 "لقد بدأ بالفعل ولا يتوقف في المنتصف: {title} يستمر حتى النهاية وسأكتب هنا ما نتج. {state}",
                 "Это уже началось и не останавливается на полпути: {title} дойдёт до конца, я напишу здесь, что получилось. {state}")
NOTHING_RUNNING = _five("כרגע לא רץ שום דבר, אז אין מה לעצור.", "Nothing is running right now, so there's nothing to stop.",
                        "Rien ne tourne en ce moment, il n'y a rien à arrêter.", "لا يعمل شيء الآن، فلا يوجد ما أوقفه.",
                        "Сейчас ничего не выполняется, останавливать нечего.")
UNDO_OFFER = _five("אפשר לבטל את זה. הכרטיס כאן אומר בדיוק מה יחזור אחורה.",
                   "This can be undone. The card here says exactly what goes back.",
                   "On peut l'annuler. La carte ici dit exactement ce qui revient en arrière.",
                   "يمكن التراجع عن ذلك. البطاقة هنا تقول بالضبط ما سيعود.",
                   "Это можно отменить. В карточке здесь точно указано, что вернётся.")
UNDO_NONE = _five("את {title} אי אפשר לבטל אוטומטית. {how}", "{title} can't be undone automatically. {how}",
                  "{title} ne peut pas être annulé automatiquement. {how}", "لا يمكن التراجع عن {title} تلقائيًا. {how}",
                  "«{title}» нельзя отменить автоматически. {how}")
UNDO_NOTHING = _five("לא מצאתי פעולה שהסתיימה לאחרונה שאפשר לבטל.", "I found no recently finished action that can be undone.",
                     "Je ne trouve aucune action récente qui puisse être annulée.", "لم أجد إجراءً انتهى مؤخرًا يمكن التراجع عنه.",
                     "Я не нашёл недавно завершённого действия, которое можно отменить.")
NO_CARD_MADE = _five("לא הצלחתי ליצור את הכרטיס הפעם, אז אין כאן עדיין כרטיס לאשר. כתבו לי שוב מה לעשות, ואכין אותו מחדש.",
                     "I couldn't create the card this time, so there is no card here to approve yet. Tell me again what to do and I'll prepare it again.",
                     "Je n'ai pas pu créer la carte cette fois : il n'y a donc pas encore de carte à valider. Redites-moi ce qu'il faut faire et je la prépare à nouveau.",
                     "لم أتمكن من إنشاء البطاقة هذه المرة، فلا توجد بطاقة للموافقة بعد. أخبروني مجددًا بما تريدون وسأجهّزها من جديد.",
                     "В этот раз не удалось создать карточку, поэтому подтверждать пока нечего. Напишите ещё раз, что сделать, и я подготовлю её заново.")
WAITING_ONLY_FOR_YOU = _five(
    "\"{title}\" מחכה רק לאישור שלכם, ושום דבר אחר לא חוסם אותו. כתבו \"כן\" בכל צורה, ואתחיל מיד.",
    "\"{title}\" is waiting only for your OK; nothing else is blocking it. Say yes in any words and I'll start right away.",
    "« {title} » n'attend que votre accord ; rien d'autre ne bloque. Dites oui comme vous voulez et je lance tout de suite.",
    "\"{title}\" ينتظر موافقتكم فقط ولا شيء آخر يعيقه. قولوا نعم بأي صيغة وسأبدأ فورًا.",
    "«{title}» ждёт только вашего согласия, больше ничего не мешает. Скажите «да» как угодно, и я сразу начну.")
BLOCKED_BY = _five("\"{title}\" לא יכול להתחיל כרגע, וזו הסיבה: {why}", "\"{title}\" can't start right now, and this is why: {why}",
                   "« {title} » ne peut pas démarrer pour l'instant, voici pourquoi : {why}",
                   "لا يمكن أن يبدأ \"{title}\" الآن، والسبب: {why}", "«{title}» сейчас не может начаться, причина: {why}")

TEXT = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, dict) and set(v) == set(LANGS)}


def _title(row: dict, lang: str) -> str:
    spec = _aa().ACTIONS.get(row.get("action")) or {}
    return pick(spec.get("title") or {"he": row.get("action", "")}, lang)


def ask_which(rows: list, lang: str) -> str:
    listed = "; ".join(f"{i + 1}. {_title(r, lang)}" for i, r in enumerate(rows[:5]))
    return pick(ASK_WHICH, lang).replace("{list}", listed)


# ─── Acting on a decision ────────────────────────────────────────────────────

def _card_out(row_id: int, client_id: int, lang: str):
    aa = _aa()
    try:
        row = aa._get(row_id)
    except Exception:
        return None
    return aa.card(row, "client", lang) if row else None


def approve(client_id: int, row: dict, background_tasks, lang: str) -> dict:
    """The typed yes: exactly the click's decide(). {"reply", "action"}."""
    aa = _aa()
    result = aa.decide(int(row["id"]), "client", client_id, "approve", background_tasks=background_tasks)
    if not result.get("success"):
        text = result.get("message") or pick(aa._FAILED, lang)
        return {"reply": text, "action": _card_out(row["id"], client_id, lang)}
    lines = []
    if result.get("status") == "running":
        lines.append(heads_up(row, lang) or (result.get("message") or ""))
    elif result.get("status") == "blocked":
        lines.append(pick(BLOCKED_BY, lang).replace("{title}", _title(row, lang))
                     .replace("{why}", result.get("message") or ""))
    else:
        note = heads_up(row, lang)
        lines += [note, result.get("message") or ""]
    out = {"reply": "\n".join(l for l in lines if l).strip() or (result.get("message") or ""),
           "action": _card_out(row["id"], client_id, lang)}
    if result.get("next_card"):
        out["next_card"] = result["next_card"]
    return out


def cancel(client_id: int, row: dict, lang: str) -> dict:
    aa = _aa()
    result = aa.decide(int(row["id"]), "client", client_id, "cancel")
    return {"reply": result.get("message") or pick(aa._CANCELLED, lang),
            "action": _card_out(row["id"], client_id, lang)}


def _loose_jobs(client_id: int) -> list:
    """TikTok uploads running without a card of their own (a batch, an owner-
    approved large file) — a typed stop reaches them too."""
    try:
        from core import tiktok_uploads
        return tiktok_uploads.loose_active(client_id)
    except Exception:
        return []


def stop(client_id: int, persona: str, lang: str, card_id=None) -> dict:
    """A typed stop, at ANY stage — exactly the card's Stop button
    (agent_actions.stop_action): one line on what did and did not happen.
    Several runs and no clear one → ONE question. {} = nothing is running."""
    aa = _aa()
    runs = running(client_id, persona)
    if not runs:
        jobs = _loose_jobs(client_id)
        if not jobs:
            return {}
        from core import tiktok_uploads
        done = tiktok_uploads.stop_client_jobs(client_id, "client")
        if done.get("stopped"):
            line = pick(JOBS_STOPPED, lang).replace("{n}", str(done["stopped"]))
            if done.get("processing"):
                line += " " + pick(aa.FINISHING_JOB, lang)
            return {"reply": line}
        return {"reply": pick(aa.FINISHING_JOB, lang)}
    row, ask = _target(card_id, runs)
    if ask:
        return {"reply": ask_which(runs, lang)}
    out = aa.stop_action(row, by="client", lang=lang)
    reply = out.get("message") or pick(NOTHING_RUNNING, lang)
    return {"reply": reply, "action": _card_out(row["id"], client_id, lang)}


def stop_info(row: dict, spec: dict) -> dict:
    """What the running card's Stop button needs: {"can", "mode", "requested"}."""
    aa = _aa()
    requested = row.get("outcome") == "stop_requested"
    return {"can": row.get("status") == "running" and not requested,
            "mode": aa.stop_mode(row.get("action") or ""), "requested": requested}


def _recent_done(client_id: int, persona: str = None, days: int = 7) -> list:
    aa = _aa()
    since = (_now() - timedelta(days=days)).isoformat()
    rows = (aa._db().table(aa.TABLE).select("*").eq("client_id", int(client_id)).eq("status", "done")
            .eq("origin", "client_chat").gte("finished_at", since).order("finished_at", desc=True)
            .limit(10).execute().data or [])
    here = [r for r in rows if persona and (r.get("persona") or "general") == persona]
    return here or rows


def undo_card(client_id: int, row: dict, lang: str = None) -> dict:
    """The ONE undo card a finished action offers, or {} when it has none."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    build = UNDO.get(row.get("action") or "")
    if not build:
        return {}
    aa = _aa()
    try:
        out = aa.propose(client_id, "client_chat", row.get("persona") or "general", build(row), by_code=True,
                         context={"language": lang})
    except Exception:
        return {}
    return out.get("card") or {}


def undo(client_id: int, persona: str, lang: str) -> dict:
    try:
        rows = _recent_done(client_id, persona)
    except Exception:
        rows = []
    for row in rows:
        if row.get("action") in UNDO:
            card = undo_card(client_id, row, lang)
            if card:
                return {"reply": pick(UNDO_OFFER, lang), "action": card}
    if rows:
        row = rows[0]
        how = pick(UNDO_BY_HAND[row["action"]], lang) if row.get("action") in UNDO_BY_HAND else ""
        return {"reply": pick(UNDO_NONE, lang).replace("{title}", _title(row, lang)).replace("{how}", how).strip()}
    return {"reply": pick(UNDO_NOTHING, lang)}


def _target(card_id, rows: list):
    """(row, ask) — the card meant: the one named, else the only one."""
    try:
        wanted = int(card_id or 0)
    except (TypeError, ValueError):
        wanted = 0
    if wanted:
        named = [r for r in rows if int(r.get("id") or 0) == wanted]
        if named:
            return named[0], False
    if len(rows) == 1:
        return rows[0], False
    return None, bool(rows)


def decide_from_chat(client_id: int, persona: str, verdict: str, card_id, background_tasks, lang: str) -> dict:
    """The model's (or the fast path's) verdict → what runs and what the client
    reads. {} = nothing to do (the normal reply stands)."""
    verdict = str(verdict or "").strip().lower()
    if verdict not in VERDICTS:
        return {}
    if verdict == "stop":
        out = stop(client_id, persona, lang, card_id)
        if out:
            return out
        verdict = "cancel"            # "stop" with nothing running = drop the open card
    if verdict == "undo":
        return undo(client_id, persona, lang)
    rows = pending(client_id, persona)
    if not rows:
        return {"reply": pick(NOTHING_RUNNING, lang)} if verdict == "cancel" else {}
    row, ask = _target(card_id, rows)
    if ask:
        return {"reply": ask_which(rows, lang)}
    if not row:
        return {}
    return approve(client_id, row, background_tasks, lang) if verdict == "approve" else cancel(client_id, row, lang)


def fast_turn(client_id: int, persona: str, message: str, background_tasks, lang: str) -> dict:
    """No model needed: an explicit "approve" with exactly ONE card open, or a
    "stop" while a run is open. Anything else → {} (the model decides)."""
    text = str(message or "")
    if len(text) > 40:
        return {}
    try:
        if FAST_STOP.match(text) and (running(client_id, persona) or _loose_jobs(client_id)):
            return stop(client_id, persona, lang)
        if FAST_APPROVE.match(text):
            rows = pending(client_id, persona)
            if len(rows) == 1:
                return approve(client_id, rows[0], background_tasks, lang)
    except Exception as e:
        print(f"[chat_approval] fast path skipped for client {client_id}: {type(e).__name__}")
    return {}


def from_result(client_id: int, persona: str, result: dict, background_tasks, lang: str) -> dict:
    decision = (result or {}).get("card_decision")
    if not isinstance(decision, dict):
        return {}
    try:
        return decide_from_chat(client_id, persona, decision.get("verdict"), decision.get("card_id"),
                                background_tasks, lang)
    except Exception as e:
        print(f"[chat_approval] decision not applied for client {client_id}: {type(e).__name__}: {e}")
        return {}


# ─── Never claim a card that isn't there; never loop ─────────────────────────
# A reply that points at a card IN THE CHAT ("the card below", "here's the card") — not the
# dashboard's connection cards ("the Google Ads card on your home page")
CARD_WORDS = re.compile("|".join((
    r"כרטיס\w*\s+(?:\S+\s+){0,3}?(?:למטה|כאן|פה|מתחת|שמתחת|שלמטה)",
    r"(?:למטה|מתחת\s+להודעה|כאן\s+למטה|שמתי\s+(?:לכם\s+|לך\s+)?כרטיס|הכנתי\s+(?:לכם\s+|לך\s+)?כרטיס)",
    r"\bcard\s+(?:right\s+)?(?:below|here|under)", r"below\s+this\s+message", r"(?:put|placed|added)\s+a\s+card",
    r"\bcarte\s+(?:ci-dessous|ici|juste\s+en\s+dessous)", r"البطاقة\s+(?:أدناه|هنا|بالأسفل)",
    r"карточк\w*\s+(?:ниже|здесь|внизу)")), re.IGNORECASE)


def mentions_card(reply: str) -> bool:
    return bool(CARD_WORDS.search(reply or ""))


def _drop_card_sentences(reply: str) -> str:
    parts = re.split(r"(?<=[.!?\n])\s+", reply or "")
    return " ".join(p for p in parts if not mentions_card(p)).strip()


def claim_guard(client_id: int, persona: str, reply: str, action, lang: str) -> dict:
    """A reply sent WITHOUT a card that speaks of one: the open card comes back
    (at the bottom) — or, when there is none, the claim goes and the client
    reads that no card was made and what to do. {} = the reply is fine."""
    if action or not mentions_card(reply):
        return {}
    try:
        rows = pending(client_id, persona)
    except Exception:
        rows = []
    if rows:
        return {"reply": reply, "action": _card_out(rows[0]["id"], client_id, lang)}
    kept = _drop_card_sentences(reply)
    return {"reply": f"{kept}\n\n{pick(NO_CARD_MADE, lang)}".strip()}


def _similar(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, (a or "").strip(), (b or "").strip()).ratio() >= 0.8


def loop_guard(client_id: int, reply: str, action, previous_replies: list, lang: str) -> str:
    """The THIRD near-identical reply about the same waiting card is replaced
    by what exactly blocks it (the block's reason, or: only the client's yes).
    Returns the reply to send."""
    if not action or not action.get("can_decide"):
        return reply
    repeats = sum(1 for p in (previous_replies or [])[-4:] if _similar(p, reply))
    if repeats < 2:
        return reply
    title = action.get("title") or ""
    if action.get("blocked"):
        return pick(BLOCKED_BY, lang).replace("{title}", title).replace("{why}", action["blocked"])
    return pick(WAITING_ONLY_FOR_YOU, lang).replace("{title}", title)


# ─── The progress the card shows ─────────────────────────────────────────────

JOB_RETRYING = _five("טיקטוק לא ענתה בניסיון האחרון. אני מנסה שוב אוטומטית, ושום דבר לא אבד.",
                    "TikTok didn't answer the last try. I'm retrying automatically, and nothing is lost.",
                    "TikTok n'a pas répondu au dernier essai. Je réessaie automatiquement, rien n'est perdu.",
                    "لم يستجب تيك توك في المحاولة الأخيرة. أعيد المحاولة تلقائيًا ولم يضع شيء.",
                    "TikTok не ответил на последнюю попытку. Повторяю автоматически, ничего не потеряно.")
JOB_PROCESSING = _five("הסרטון הועלה במלואו. טיקטוק מעבדת אותו עכשיו, וזה בדרך כלל לוקח כמה דקות.",
                       "The video is fully uploaded. TikTok is processing it now, which usually takes a few minutes.",
                       "La vidéo est entièrement envoyée. TikTok la traite maintenant, cela prend en général quelques minutes.",
                       "تم رفع الفيديو بالكامل. يعالجه تيك توك الآن، وعادةً يستغرق ذلك بضع دقائق.",
                       "Видео загружено полностью. TikTok сейчас его обрабатывает, обычно это занимает несколько минут.")
TEXT.update(JOB_RETRYING=JOB_RETRYING, JOB_PROCESSING=JOB_PROCESSING)


def _job(row: dict) -> dict:
    if row.get("action") != "send_tiktok_to_inbox":
        return {}
    try:
        from core import tiktok_uploads
        return next((j for j in tiktok_uploads.load(int(row["client_id"]))
                     if j.get("proposal_id") == row.get("id")), {})
    except Exception:
        return {}


def real_fraction(row: dict, job: dict = None):
    """0..1 where the action itself reports progress (the chunked TikTok
    upload), else None. While TikTok processes it: None (no number to show)."""
    job = _job(row) if job is None else job
    if not job.get("chunks") or job.get("status") == "processing":
        return None
    return max(0.0, min(1.0, int(job.get("next") or 0) / int(job["chunks"])))


def job_text(row: dict, lang: str, job: dict = None) -> str:
    """The line a TikTok card shows while its job retries or TikTok processes it."""
    job = _job(row) if job is None else job
    if job.get("status") == "processing":
        return pick(JOB_PROCESSING, lang)
    if job.get("status") in ("queued", "uploading") and int(job.get("attempts") or 0) > 0:
        return pick(JOB_RETRYING, lang)
    return ""


def progress_extra(row: dict, spec: dict) -> dict:
    """{"fraction", "expected_seconds", "stop_until"} for a running card. A
    measured expectation only with enough past runs (core/task_eta), else None
    — the card then shows work running, never a made-up number."""
    job = _job(row)
    out = {"fraction": real_fraction(row, job), "expected_seconds": None, "stop_until": None,
           "stop": stop_info(row, spec)}
    note = job_text(row, (job or {}).get("lang") or "he", job) if job else ""
    if note:
        out["text"] = note          # replaces the generic line on the card
    try:
        from core import task_eta
        seconds = task_eta.action_seconds(row.get("action") or "")
        out["expected_seconds"] = int(seconds) if seconds else None
    except Exception:
        pass
    aa = _aa()
    started = _parse(row.get("decided_at"))
    if started and spec.get("slow") and not spec.get("inline_start") and aa.STOP_WINDOW_SECONDS:
        until = started + timedelta(seconds=aa.STOP_WINDOW_SECONDS)
        if until > _now():
            out["stop_until"] = until.isoformat()
    return out


# ─── The chat model's rule ───────────────────────────────────────────────────
SUPPORT_RULE = """

CARDS WAITING FOR THE CLIENT ("pending_cards" in your data: cards already shown that wait for THEIR
yes; "running_cards": actions running right now). TYPING AGREEMENT IS THE SAME AS PRESSING THE
BUTTON. Read the client's message the way a person would understand it, in any language:
- They agree to a pending card ("יאללה", "סבבה", "קדימה", "למה לא", "אני סומך עליך", "כן תעלה",
  "תעלה", "ok upload it", "go for it", "i approve", "vas-y", "давай", "يلا", or any other natural yes):
  set "card_decision" to {"verdict": "approve", "card_id": its card_id}. Asking AGAIN for exactly
  what a pending card does is agreement too. Costly or irreversible actions are no exception.
- They drop a pending card ("בטל", "לא משנה", "cancel", "forget it"): verdict "cancel".
- They want a RUNNING action stopped ("עצור", "רגע", "stop", "wait", "תפסיק את זה"), at ANY stage: verdict "stop"
  with that running card's card_id (0 when several run and it is not clear which).
- They want a FINISHED action reversed ("אחורה", "תחזיר את זה", "undo"): verdict "undo".
- Several pending cards and it is not clear which one they mean: card_id 0 — the system asks ONCE.
  One pending card, or a clear reference to one: its card_id.
- A question about a card ("what title will it get?") or anything else: verdict "none".
When you set a verdict, the system runs it exactly like the button and writes what the client reads:
keep "reply" to one short line and propose nothing else in that turn. NEVER write that a card is
"below" or "here" unless you are proposing one in this very reply — the system shows a card only when
one was really created. Never ask the client to press a button when they already said yes."""
