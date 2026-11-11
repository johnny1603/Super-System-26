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
    if ACK_RX.match(text):
        if _asked_something(last_reply):
            return out("work", "acknowledges an offer")      # may be a yes: go up
        return out("simple", "greeting or acknowledgement")
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
SIMPLE_NOTE = ("This message is a short greeting or acknowledgement. Answer in 1-2 short lines, in your own voice. "
               "The account data was NOT loaded for this message: state no facts about the account, propose no "
               "action, start no search. If it turns out they want something, invite them to say what — warmly, "
               "never \"I can't see\" and never \"passing it to the team\".")
