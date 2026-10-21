"""Graceful "I don't know" / "I can't right now" for every client-facing chat.

House rule (2026-10-13): no chat a client or prospect sees — the sales chat,
the dashboard concierge, any specialist persona, the first-login interview —
ever shows a generic error/malfunction line that ends the conversation. When
the system can't answer, it answers like a knowledgeable person admitting a
limit: warm, direct, in that speaker's lane, and it ACTUALLY routes the
question to the team (an admin alert), then lets the conversation move on.

Two situations, deliberately kept apart:

  OUT OF SCOPE — the model is working; the question is outside what this
    speaker may answer (legal, tax/accounting, medical, anything beyond the
    agency's authority). The MODEL writes the in-character reply (prompt rules
    in support_agent / onboarding_agent) and flags `out_of_scope`; this module
    only alerts. Fixed text is used only if the model flagged it but wrote
    nothing.

  TECHNICAL — something actually failed mid-turn (LLM timeout/overload, a
    downstream read, a DB error). There is no model to write the reply, so it
    is FIXED text per persona and language below: no codes, no "system error",
    no stack trace, never a plea to "try again" as the only option.

Alerts are DEDUPED so a client hitting the same wall repeatedly doesn't flood
Johnny's alert list — same durable idiom as the ads/website scans'
ISSUE_DEDUP_DAYS (a client_activity row carrying details.issue_key). Prospects
in the sales chat have no client row yet, so theirs dedupe in process memory
(per Cloud Run instance — at worst one extra alert per instance per window).
"""
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone

from core.agent_base import agent_alert, log_step

AGENT_NAME = "chat_fallback"
HANDOFF_ACTION = "chat_handoff_alerted"

KIND_OUT_OF_SCOPE = "out_of_scope"
KIND_TECHNICAL = "technical"
KIND_FOLLOWUP = "needs_followup"   # in scope, but a human has to act (a change request, an unclear ask)

# Same question / same failure from the same client re-alerts at most this
# often. Technical is shorter: a NEW outage tomorrow is news; the same question
# asked five times this week is not.
CHAT_ALERT_DEDUP_DAYS = {KIND_OUT_OF_SCOPE: 3, KIND_FOLLOWUP: 3, KIND_TECHNICAL: 1}
SALES_DEDUP_SECONDS = 6 * 60 * 60

SUPPORTED_LANGUAGES = ("he", "en", "fr", "ar", "ru")

_LABELS = {
    KIND_OUT_OF_SCOPE: "OUT OF SCOPE — answer personally",
    KIND_FOLLOWUP: "needs human follow-up",
    KIND_TECHNICAL: "TECHNICAL FAILURE mid-chat",
}


# ─── Fixed replies (technical failures — no model available to write them) ───
# Hebrew is gender-neutral first person ("יש לי קושי", "העברתי", "אוכל") on
# purpose: the general concierge's gender is the client's choice, and a fixed
# line must fit every speaker.

_TECHNICAL_OPENING = {
    "he": "יש לי כרגע קושי לשלוף את זה — כבר העברתי לצוות שיבדקו, ויחזרו אליכם אם צריך.",
    "en": "I'm having trouble pulling that up right now — I've already passed it to the team to check, and they'll get back to you if needed.",
    "fr": "J'ai du mal à récupérer cela pour le moment — j'ai déjà transmis à l'équipe pour vérification, et elle reviendra vers vous si besoin.",
    "ar": "أواجه صعوبة في جلب ذلك حاليًا — لقد حوّلت الأمر للفريق للتحقق، وسيعودون إليكم إذا لزم الأمر.",
    "ru": "Сейчас не получается это достать — вопрос уже у команды, они проверят и вернутся к вам, если нужно.",
}

_OUT_OF_SCOPE_OPENING = {
    "he": "זה לא תחום שאוכל לעזור בו ישירות — העברתי את השאלה לצוות, ויחזרו אליכם.",
    "en": "That's not something I can help with directly — I've passed your question to the team and they'll get back to you.",
    "fr": "Ce n'est pas un sujet sur lequel je peux vous aider directement — j'ai transmis votre question à l'équipe, qui reviendra vers vous.",
    "ar": "هذا ليس مجالًا أستطيع المساعدة فيه مباشرة — حوّلت سؤالكم للفريق وسيعودون إليكم.",
    "ru": "С этим я не могу помочь напрямую — ваш вопрос уже у команды, и с вами свяжутся.",
}

# The "and we carry on" tail, in each speaker's own lane — this is what keeps
# the line in character and the conversation moving instead of dwelling.
_TAILS = {
    "general": {
        "he": "בינתיים אפשר להמשיך איתי בכל נושא אחר.",
        "en": "Meanwhile, feel free to carry on with anything else.",
        "fr": "En attendant, on peut continuer sur n'importe quel autre sujet.",
        "ar": "في هذه الأثناء، يمكننا المتابعة في أي موضوع آخر.",
        "ru": "А пока можем продолжить о чём угодно другом.",
    },
    "google": {
        "he": "בינתיים אפשר לשאול אותי כל דבר אחר על הקמפיינים בגוגל.",
        "en": "Meanwhile, ask me anything else about your Google campaigns.",
        "fr": "En attendant, posez-moi toute autre question sur vos campagnes Google.",
        "ar": "في هذه الأثناء، اسألوني أي شيء آخر عن حملاتكم في Google.",
        "ru": "А пока спрашивайте что угодно ещё о ваших кампаниях в Google.",
    },
    "meta": {
        "he": "בינתיים אפשר לשאול אותי כל דבר אחר על פייסבוק ואינסטגרם.",
        "en": "Meanwhile, ask me anything else about Facebook and Instagram.",
        "fr": "En attendant, posez-moi toute autre question sur Facebook et Instagram.",
        "ar": "في هذه الأثناء، اسألوني أي شيء آخر عن فيسبوك وإنستغرام.",
        "ru": "А пока спрашивайте что угодно ещё о Facebook и Instagram.",
    },
    "website": {
        "he": "בינתיים אפשר לשאול אותי כל דבר אחר על האתר וה-SEO.",
        "en": "Meanwhile, ask me anything else about your website and SEO.",
        "fr": "En attendant, posez-moi toute autre question sur votre site et le SEO.",
        "ar": "في هذه الأثناء، اسألوني أي شيء آخر عن موقعكم وتحسين محركات البحث.",
        "ru": "А пока спрашивайте что угодно ещё о сайте и SEO.",
    },
    "media": {
        "he": "בינתיים אפשר לשאול אותי כל דבר אחר על התוכן והמדיה.",
        "en": "Meanwhile, ask me anything else about your content and media.",
        "fr": "En attendant, posez-moi toute autre question sur vos contenus et médias.",
        "ar": "في هذه الأثناء، اسألوني أي شيء آخر عن المحتوى والوسائط.",
        "ru": "А пока спрашивайте что угодно ещё о контенте и медиа.",
    },
    # Sales chat: the way forward is the packages on screen
    "sales": {
        "he": "בינתיים, אם אחת האפשריות שלמעלה מדברת אליכם — אפשר לבחור בה, או לשאול אותי משהו אחר.",
        "en": "Meanwhile, if one of the options above feels right, you can pick it — or ask me something else.",
        "fr": "En attendant, si l'une des options ci-dessus vous convient, vous pouvez la choisir — ou me poser une autre question.",
        "ar": "في هذه الأثناء، إذا كان أحد الخيارات أعلاه مناسبًا، يمكنكم اختياره — أو اسألوني شيئًا آخر.",
        "ru": "А пока, если один из вариантов выше вам подходит, можно выбрать его — или спросите меня о другом.",
    },
}


def detect_language(text: str, default: str = "he") -> str:
    """The fixed replies must match the language the client is WRITING in —
    the same rule the models follow (LANGUAGE_RULE). Script is enough to tell
    our five apart, except English vs French, where the client's stored UI
    language breaks the tie."""
    sample = text or ""
    if re.search(r"[֐-׿]", sample):
        return "he"
    if re.search(r"[؀-ۿ]", sample):
        return "ar"
    if re.search(r"[Ѐ-ӿ]", sample):
        return "ru"
    if re.search(r"[A-Za-z]", sample):
        return default if default in ("en", "fr") else "en"
    return default if default in SUPPORTED_LANGUAGES else "he"


def _compose(opening: dict, persona_id: str, language: str) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "he"
    tails = _TAILS.get(persona_id) or _TAILS["general"]
    return f"{opening[lang]} {tails[lang]}"


def technical_reply(persona_id: str = "general", language: str = "he") -> str:
    return _compose(_TECHNICAL_OPENING, persona_id, language)


# The client asked about live data whose read failed THIS turn. Code decides
# this (support_agent: the model's data_topic label + a same-turn read
# failure), never the model — so the line is fixed text, like a technical one.
DATA_TOPICS = ("google_ads", "meta_ads", "website", "tracking", "leads", "account")

# Says WHAT could not be read this moment (never "I have no access" — the
# agents do see the account; this is one read that failed just now)
_DATA_UNAVAILABLE_OPENING = {
    "he": "לא הצלחתי לשלוף ממש עכשיו את הנתונים של {p} — הצוות כבר בודק את זה.",
    "en": "I couldn't pull up your {p} data just now — the team is already checking.",
    "fr": "Je n'ai pas pu récupérer les données {p} à l'instant — l'équipe vérifie déjà.",
    "ar": "لم أتمكن من جلب بيانات {p} الآن — الفريق يتحقق من الأمر بالفعل.",
    "ru": "Не удалось только что загрузить данные {p} — команда уже проверяет.",
}

_DATA_TOPIC_NAMES = {
    "google_ads": {"he": "Google Ads", "en": "Google Ads", "fr": "Google Ads",
                   "ar": "Google Ads", "ru": "Google Ads"},
    "meta_ads": {"he": "פייסבוק ואינסטגרם", "en": "Facebook and Instagram",
                 "fr": "Facebook et Instagram", "ar": "فيسبوك وإنستغرام",
                 "ru": "Facebook и Instagram"},
    "website": {"he": "האתר", "en": "website", "fr": "de votre site",
                "ar": "موقعكم", "ru": "вашего сайта"},
    "tracking": {"he": "המדידה באתר", "en": "website tracking", "fr": "de suivi du site",
                 "ar": "قياس الموقع", "ru": "аналитики сайта"},
    "leads": {"he": "הלידים", "en": "leads", "fr": "de prospects",
              "ar": "العملاء المحتملين", "ru": "по заявкам"},
    "account": {"he": "החשבון", "en": "account", "fr": "du compte",
                "ar": "الحساب", "ru": "аккаунта"},
}


def data_unavailable_reply(persona_id: str, language: str, topic: str) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "he"
    names = _DATA_TOPIC_NAMES.get(topic) or _DATA_TOPIC_NAMES["account"]
    opening = {lang: _DATA_UNAVAILABLE_OPENING[lang].format(p=names[lang])}
    return _compose(opening, persona_id, lang)


def out_of_scope_reply(persona_id: str = "general", language: str = "he") -> str:
    return _compose(_OUT_OF_SCOPE_OPENING, persona_id, language)


# ─── Deduped admin alerts ────────────────────────────────────────────────────

def question_key(text: str) -> str:
    """Stable key for "the same question": case, whitespace and punctuation
    don't make a question new."""
    normalized = re.sub(r"[^\w\s]", "", (text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()[:300]
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _recently_alerted(client_id: int, issue_key: str, days: int) -> bool:
    from agents.client_agent import _db
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = (_db().table("client_activity").select("id")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", HANDOFF_ACTION)
            .eq("details->>issue_key", issue_key)
            .gte("created_at", cutoff).limit(1).execute().data)
    return bool(rows)


def alert_once(source_agent: str, client_id: int, issue_key: str, message: str,
               days: int) -> bool:
    """One admin alert per (client, issue_key) per `days`, durably deduped by a
    hidden chat_fallback marker row (the client feed and the chat payloads
    both skip agent_name='chat_fallback'), the same dedup alert_handoff uses
    for clients. For a read that runs on every chat turn and dashboard load
    (the ads performance getters) and would otherwise alert once per call.
    Never raises."""
    try:
        if _recently_alerted(client_id, issue_key, days):
            log_step(AGENT_NAME, "alert_once", f"client {client_id}: deduped {issue_key}")
            return False
        agent_alert(source_agent, [message])
        from agents.client_agent import log_activity
        log_activity(client_id, AGENT_NAME, HANDOFF_ACTION,
                     {"issue_key": issue_key, "source_agent": source_agent})
        return True
    except Exception as e:
        print(f"[{AGENT_NAME}] could not raise alert ({issue_key}): {e}")
        return False


_sales_alerted = {}  # issue_key -> monotonic time of last alert (prospects have no client row)


def alert_handoff(kind: str, source_agent: str, detail: str, *, client_id: int = None,
                  persona: str = "general", issue_key: str = "", lead_id: str = "") -> bool:
    """Raise ONE admin alert for a chat handoff, deduped. Returns True when an
    alert was actually sent. Never raises — it runs on error paths, and a
    failed alert must not turn a graceful reply into a broken one.

    issue_key defaults to kind + persona + the question's key, so "the same
    question again" is quiet and a different question is not."""
    key = issue_key or f"{kind}:{persona}:{question_key(detail)}"
    who = f"client {client_id}" if client_id else (f"prospect lead {lead_id}" if lead_id else "prospect")
    message = f"[{_LABELS.get(kind, kind)}] {who} ({persona} chat): {detail[:500]}"
    try:
        if client_id:
            if _recently_alerted(client_id, key, CHAT_ALERT_DEDUP_DAYS.get(kind, 3)):
                log_step(AGENT_NAME, "alert_handoff", f"{who}: deduped {key}")
                return False
            agent_alert(source_agent, [message])
            from agents.client_agent import log_activity
            log_activity(client_id, AGENT_NAME, HANDOFF_ACTION,
                         {"issue_key": key, "kind": kind, "persona": persona,
                          "source_agent": source_agent})
            return True
        scoped = f"{lead_id or '-'}:{key}"
        now = time.monotonic()
        if now - _sales_alerted.get(scoped, -SALES_DEDUP_SECONDS - 1) < SALES_DEDUP_SECONDS:
            return False
        _sales_alerted[scoped] = now
        agent_alert(source_agent, [message])
        return True
    except Exception as e:
        print(f"[{AGENT_NAME}] could not raise handoff alert ({key}): {e}")
        return False
