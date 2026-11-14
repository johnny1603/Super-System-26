"""Which kind of work ONE client chat message is (2026-11-11, docs/CHAT-SPEED.md).

The same idea as איציק's routing (core/operator_agent.classify): rules only,
no model call, and DOUBT GOES UP.

  simple  a greeting / thanks / "ok" that answers nothing we offered
          → the cheapest tier, the last few messages, NO account reads
  read    a short question about their account or how something works
          → the middle tier, the full account context
  work    anything that asks to do, change, publish, send, build, spend or
          connect something; a "yes" to what we offered; a frustrated
          client; a long or multi-part message; anything unclear
          → the strongest tier, the full account context

The tier names map to model_routing.TIER_MODELS; nothing here names a
model. The decision is recorded per message (chat_timing flags `route`,
`route_reason`, `message_chars`) so /admin/models/ can compare before and after.
"""
import re

KINDS = ("simple", "read", "work")
TIER_FOR = {"simple": "simple", "read": "standard", "work": "heavy"}
SIMPLE_HISTORY_TURNS = 4          # "last few messages only"
READ_MAX_CHARS = 140              # a quick question is short; longer is real work
WORK_MIN_CHARS = 300

ACK_RX = re.compile(
    r"^(היי|הי|שלום|בוקר טוב|צהריים טובים|ערב טוב|לילה טוב|תודה( רבה)?( לך)?|תנקס|אחלה|סבבה|מעולה|יופי|"
    r"מצוין|נהדר|אוקיי?|אוקי|טוב|הבנתי|קיבלתי|מגניב|👍|🙏|❤️|"
    r"hi|hey|hello|good (morning|evening)|thanks?( you)?( so much)?|thx|ok(ay)?|great|cool|got it|perfect|nice|"
    r"merci|bonjour|salut|شكرا|مرحبا|спасибо|привет)[\s!.,🙏😊👍❤️]*$", re.I)
YES_RX = re.compile(
    r"^(כן|כן בבקשה|כן תעשה|כן כן|יאללה|תעשה|תעשה את זה|קדימה|לך על זה|בטח|מאשר|מאשרת|אישור|בסדר גמור|"
    r"yes|yep|yeah|sure|go|go ahead|do it|approved?|oui|نعم|да)[\s!.,👍]*$", re.I)
WORK_RX = re.compile(
    r"תפרסם|לפרסם|פרסם|תעלה|להעלות|העלה|תשלח|לשלוח|שלח|תבנה|לבנות|בנה|תיצור|ליצור|צור|תכין|להכין|הכן|"
    r"תכתוב|לכתוב|כתוב|תשנה|לשנות|שנה את|תעדכן|לעדכן|עדכן|תוסיף|להוסיף|הוסף|תמחק|למחוק|מחק|תסיר|להסיר|"
    r"תבטל|לבטל|בטל|תחבר|לחבר|חבר את|תנתק|לנתק|תפעיל|להפעיל|תכבה|לכבות|תעצור|לעצור|תשהה|להשהות|"
    r"תאשר|לאשר|אשר|תזמין|להזמין|לקנות|לשדרג|שדרוג|תקציב|לשלם|תשלום|החזר|קמפיין חדש|מודעה חדשה|"
    r"דף נחיתה|סרטון|וידאו|טיקטוק|יוטיוב|מאמר|פוסט|תמונה|מגיש|קול|דומיין|"
    r"\b(publish|post|upload|send|build|create|make|write|change|update|edit|add|delete|remove|cancel|"
    r"connect|disconnect|launch|start|stop|pause|resume|approve|buy|order|upgrade|budget|pay|refund|video|"
    r"landing|article|campaign|tiktok|youtube|presenter|voice|domain)\b", re.I)
QUESTION_RX = re.compile(
    r"\?|^(מה|איך|כמה|למה|מתי|איפה|האם|מי|יש לי|יש לנו|יש עוד)\b|מה המצב|מה נשמע|מה חדש|"
    r"\b(what|how|when|where|why|which|who|is there|are there|do i|did|status)\b", re.I)


# ─── Small talk (2026-11-15, docs/CHAT-SPEED.md §Small talk) ─────────────────
# "מה קורה?", "היי", "תודה", "אחלה" and their equivalents in the five languages
# are answered like a person answers them: at once, on the cheapest tier, with
# no account reads, no collection wait (core/chat_turns) and no card. Still
# RULES only. The whole message must be made of these phrases — "היי, תעלה לי
# את הסרטון" has a request in it, matches nothing here and takes the full path.
#   greeting  hello / how are you — never a "yes" to anything
#   ack       thanks / great / ok — after an offer of ours it may be a yes: UP
SMALL_TALK_MAX_CHARS = 70
_GREETING = (
    # he
    r"הי+|היוש|שלום( לך| לכם)?|אהלן|הלו|בוקר טוב|בוקר אור|צהריים טובים|ערב טוב|לילה טוב|שבוע טוב|שבת שלום|"
    r"חג שמח|מה קורה|מה הולך|מה נשמע|מה שלומך|מה שלומכם|מה העניינים|מה איתך|מה נהיה|איך הולך|"
    r"מה קורה איתך|מה נשמע איתך|הכו?ל טוב|"
    # en
    r"hi+|hey+|hello|hiya|yo|good (morning|afternoon|evening|night)|how are (you|u)( doing| today)?|"
    r"how('s| is) it going|how are things|what'?s up|wh?assup|sup|how do you do|"
    # fr
    r"bonjour|bonsoir|salut|coucou|(comment )?[çc]a va|comment vas[- ]tu|comment allez[- ]vous|tu vas bien|"
    # ar
    r"مرحبا|[أا]هلا( وسهلا)?|هلا|السلام عليكم|سلام|صباح الخير|مساء الخير|كيف حالك|كيفك|كيف الحال|"
    r"شو ال[أا]خبار|شو [أا]خبارك|شلونك|[إا]يش [أا]خبارك|"
    # ru
    r"привет(ик)?|здравствуй(те)?|добрый день|доброе утро|добрый вечер|как дела|как ты|как жизнь|"
    r"как поживаешь|как поживаете")
_ACK = (
    # he
    r"תודה+( רבה)?( לך| לכם)?|תנקס|אחלה|סבבה|מעולה|יופי|מצוין|נהדר|אוקיי?|אוקי|טוב|הבנתי|קיבלתי|מגניב|"
    r"מושלם|תותח|תותחים|אלוף|אלופה|אלופים|כל הכבוד|וואלה|יפה|מדהים|אין עליך|אין עליכם|ביי|להתראות|"
    r"יום טוב|ח{2,}|"
    # en
    r"thanks?( you)?( so much| a lot)?|thx|ty|ok(ay)?|great|cool|got it|perfect|nice|awesome|amazing|"
    r"love it|well done|good job|bye|goodbye|see you|cheers|lol|(ha){2,}|"
    # fr
    r"merci( beaucoup| bien)?|super|g[ée]nial|parfait|d'accord|top|bravo|[àa] bient[ôo]t|au revoir|"
    # ar
    r"شكرا( جزيلا| لك)?|شكرًا|شكراً|تمام|ممتاز|رائع|حلو|يسلمو|ماشي|مع السلامة|"
    # ru
    r"спасибо( большое)?|спс|отлично|супер|класс|круто|понял|поняла|понятно|хорошо|ок|окей|ладно|пока|"
    r"молодец|"
    r"👍|🙏|❤️|👌|💪|🔥")
# Who they are talking to, or a friendly word — changes nothing
_VOCATIVE = (r"ג'וני|ניסים|אלינור|יואב|אורי|מאיה|ליאור|אחי|גבר|חבר|חברים|מותק|יקירי|יקירתי|"
             r"johnny|nissim|elinor|yoav|uri|maya|lior|bro|man|guys|team|mon ami|друг")
_FILL = r"\s,!.?؟،…~\-–️🙏😊👍❤🙂😄😁👋💪🔥😀🤝👌✨😎"
_SEP, _END = f"[{_FILL}]+", f"[{_FILL}]*"


def _only(words: str):
    return re.compile(rf"^{_END}(?:(?:{words}){_SEP})*(?:{words}){_END}$", re.I)


GREETING_RX = _only(f"{_GREETING}|{_VOCATIVE}")
SMALL_TALK_RX = _only(f"{_GREETING}|{_ACK}|{_VOCATIVE}")
_VOCATIVE_ONLY_RX = _only(_VOCATIVE)


def small_talk(message: str) -> str:
    """"greeting" / "ack" when the WHOLE message is small talk, else ""."""
    text = (message or "").strip()
    if not text or len(text) > SMALL_TALK_MAX_CHARS or "\n" in text:
        return ""
    if _VOCATIVE_ONLY_RX.match(text):
        return ""                      # a name alone says nothing: doubt goes up
    if GREETING_RX.match(text):
        return "greeting"
    return "ack" if SMALL_TALK_RX.match(text) else ""


def _asked_something(last_reply: str) -> bool:
    """Did our last reply end on a question or an offer? Then "ok" may be a yes."""
    t = (last_reply or "").strip()
    return t.endswith("?") or bool(re.search(
        r"רוצה ש|רוצים ש|שאציע|שאכין|שאשלח|שאעלה|לאשר|להמשיך|\bshall i\b|\bwant me to\b|\bshould i\b", t, re.I))


def classify(message: str, last_reply: str = "", frustrated: bool = False,
             explicit: bool = False) -> dict:
    """{"kind", "tier", "reason"} for one (possibly merged) client message."""
    from core.model_routing import HEAVY_RULES
    text = (message or "").strip()

    def out(kind, reason):
        return {"kind": kind, "tier": TIER_FOR[kind], "reason": reason}

    if not text:
        return out("simple", "empty")
    if frustrated:
        return out("work", "frustrated client")
    for reason, pattern in HEAVY_RULES:
        if pattern.search(text):
            return out("work", reason)
    if explicit:
        return out("work", "explicit instruction")
    if YES_RX.match(text):
        return out("work", "approves what was offered")
    social = small_talk(text)
    if social == "greeting":
        return dict(out("simple", "small talk"), small_talk=True)   # never a yes to anything
    if social == "ack" or ACK_RX.match(text):
        if _asked_something(last_reply):
            return out("work", "acknowledges an offer")      # may be a yes: go up
        return dict(out("simple", "greeting or acknowledgement"), small_talk=bool(social))
    if text.count("?") >= 3 or len(re.findall(r"(?m)^\s*\d+[.)]\s", text)) >= 2:
        return out("work", "multi-step request")
    if len(text) > WORK_MIN_CHARS or text.count("\n") >= 3:
        return out("work", "long or multi-part")
    if WORK_RX.search(text):
        return out("work", "asks for something to be done")
    if len(text) <= READ_MAX_CHARS and QUESTION_RX.search(text):
        return out("read", "short question")
    return out("work", "unclear — top tier")


def last_reply(history: list) -> str:
    """Our newest message in the thread (history is oldest first)."""
    for turn in reversed(history or []):
        if isinstance(turn, dict) and turn.get("from") != "client":
            return turn.get("text") or ""
    return ""


# What a SIMPLE turn's model reads instead of the account (no data was loaded)
SIMPLE_NOTE = ("This message is small talk: a greeting, a \"what's up\", a thanks or a short acknowledgement. Answer "
               "the way a person answers a colleague — ONE short line, at most two, in your own voice and in the "
               "client's language: say how you are and ask back, or take the thanks lightly. It is NOT a status "
               "report: no numbers, no summary of the account or of your work, no list of what you can do, no "
               "offer of tasks, no emoji pile. "
               "The account data was NOT loaded for this message: state no facts about the account, propose no "
               "action, start no search. If it turns out they want something, invite them to say what — warmly, "
               "never \"I can't see\" and never \"passing it to the team\".")
