"""Client chat protection — checks that run BEFORE a message is stored or
reaches a model.

Three guards, in this order, all with fixed per-language replies (never an
LLM, never a generic error):

1. SECRETS — a message that looks like an API key, token or password is NOT
   stored and NOT sent to any model. The client is told not to share it in
   chat and where to put it instead. A placeholder is logged so the thread
   still reads as a conversation.
2. LENGTH — over `chat_max_message_chars` (core/client_settings, default
   2000): a warm "please shorten it" reply.
3. DAILY CAP — over `chat_daily_message_cap` inbound messages today (default
   40, counted across every chat thread, Israeli calendar day): the warm
   "נמשיך מחר, או שאפשר לפנות לצוות בוואטסאפ".

The secret check runs even when the other limits are off, because it
protects the client, not our costs.
"""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.agent_base import log_step

SERVICE_NAME = "chat_guard"
_TZ = ZoneInfo("Asia/Jerusalem")
CHAT_CHANNEL_PREFIX = "dashboard_chat"

# Stored instead of a message that looked like a secret — the raw text is
# never written anywhere
REDACTED_PLACEHOLDER = "[הודעה הוסתרה — נראתה כמו סיסמה או מפתח API]"

# Well-known credential shapes, plus a generic "long random token" rule
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),            # OpenAI/Anthropic-style keys
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),           # Google API keys
    re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}"),         # Google OAuth access tokens
    re.compile(r"\b1//[0-9A-Za-z_\-]{20,}"),            # Google refresh tokens
    re.compile(r"\bEAA[A-Za-z0-9]{40,}"),               # Meta access tokens
    re.compile(r"\b(?:ghp|gho|xox[abp])[-_][A-Za-z0-9\-]{16,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
    # WordPress Application Passwords: six groups of four, with a digit in
    # them (six ordinary 4-letter words must not match)
    re.compile(r"\b(?=[A-Za-z0-9 ]{29}\b)(?=[A-Za-z ]*\d)(?:[A-Za-z0-9]{4} ){5}[A-Za-z0-9]{4}\b"),
]
# A 28+ char run of letters AND digits with no spaces reads as a key, not a word
_LONG_TOKEN = re.compile(r"(?<![\w/.])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{28,}(?![\w/.])")
# "password: x" / "הסיסמה היא x" in our five languages
_PASSWORD_PHRASE = re.compile(
    r"(?:password|passwd|pwd|סיסמ[הא]|הסיסמ[הא]|mot de passe|пароль|كلمة\s*(?:ال)?(?:مرور|سر)|api[\s_-]?key|מפתח)"
    r"\s*(?:[:=]|\bis\b|היא|הוא|est|это|هي)\s*\S{4,}",
    re.IGNORECASE)

_REPLIES = {
    "secret": {
        "he": "רגע, נראה שזו סיסמה או מפתח — בבקשה אל תשתפו אותם בצ׳אט, מטעמי אבטחה לא שמרנו את ההודעה. "
              "מכניסים אותם רק בשדה החיבור בדשבורד, ואם משהו לא מסתדר — הצוות זמין בוואטסאפ: {wa}",
        "en": "Hold on — that looks like a password or key. Please don't share these in chat; for your security "
              "we didn't save that message. Enter it only in the connection field in your dashboard, and if "
              "something doesn't work, the team is on WhatsApp: {wa}",
        "fr": "Un instant — cela ressemble à un mot de passe ou à une clé. Merci de ne pas les partager dans le "
              "chat ; par sécurité, nous n'avons pas enregistré ce message. Saisissez-le uniquement dans le "
              "champ de connexion du tableau de bord, et si ça ne marche pas, l'équipe est sur WhatsApp : {wa}",
        "ar": "لحظة — يبدو أن هذه كلمة مرور أو مفتاح. من فضلك لا تشاركها في الدردشة؛ لأمانك لم نحفظ هذه الرسالة. "
              "أدخلها فقط في حقل الربط في لوحة التحكم، وإن لم ينجح الأمر فالفريق متاح على واتساب: {wa}",
        "ru": "Минутку — похоже на пароль или ключ. Пожалуйста, не отправляйте их в чат; ради вашей безопасности "
              "мы не сохранили это сообщение. Вводите его только в поле подключения в кабинете, а если что-то не "
              "получается — команда на связи в WhatsApp: {wa}",
    },
    "too_long": {
        "he": "ההודעה ארוכה מדי בשבילי — אפשר לקצר אותה ולשלוח שוב, או לפנות לצוות בוואטסאפ: {wa}",
        "en": "That message is a bit too long for me — you can shorten it and send again, or reach the team on WhatsApp: {wa}",
        "fr": "Ce message est un peu trop long pour moi — vous pouvez le raccourcir et le renvoyer, ou écrire à l'équipe sur WhatsApp : {wa}",
        "ar": "الرسالة طويلة قليلًا بالنسبة لي — يمكنك اختصارها وإرسالها مجددًا، أو التواصل مع الفريق على واتساب: {wa}",
        "ru": "Это сообщение для меня слишком длинное — можно сократить и отправить снова или написать команде в WhatsApp: {wa}",
    },
    "daily_cap": {
        "he": "נמשיך מחר, או שאפשר לפנות לצוות בוואטסאפ: {wa}",
        "en": "Let's continue tomorrow — or you can reach the team on WhatsApp: {wa}",
        "fr": "On continue demain — ou vous pouvez écrire à l'équipe sur WhatsApp : {wa}",
        "ar": "نكمل غدًا، أو يمكنك التواصل مع الفريق على واتساب: {wa}",
        "ru": "Продолжим завтра — или можно написать команде в WhatsApp: {wa}",
    },
}


def looks_like_secret(message: str) -> bool:
    text = message or ""
    if any(p.search(text) for p in _SECRET_PATTERNS):
        return True
    if _PASSWORD_PHRASE.search(text):
        return True
    # A URL legitimately carries long tokens (UTM ids, Drive ids) — strip
    # URLs before the generic long-token rule
    without_urls = re.sub(r"https?://\S+", " ", text)
    return bool(_LONG_TOKEN.search(without_urls))


def _reply(kind: str, message: str) -> str:
    from core import admin_service, chat_fallback
    lang = chat_fallback.detect_language(message)
    table = _REPLIES[kind]
    return (table.get(lang) or table["he"]).replace(
        "{wa}", admin_service.display_phone(admin_service.support_whatsapp_number()))


def _sent_today(client_id: int) -> int:
    from agents.client_agent import _db
    local_midnight = datetime.now(_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (_db().table("client_communications").select("id", count="exact")
            .eq("client_id", client_id).eq("direction", "inbound")
            .like("channel", f"{CHAT_CHANNEL_PREFIX}%")
            .gte("created_at", local_midnight.astimezone(timezone.utc).isoformat())
            .limit(1).execute())
    return rows.count or 0


def check(client_id: int, message: str) -> dict:
    """None when the message may proceed; otherwise {"code", "reply",
    "store_as"} — the caller stores `store_as` (never the raw secret) as the
    inbound row, stores `reply` as the outbound one, and calls no model."""
    if looks_like_secret(message):
        log_step(SERVICE_NAME, "secret_blocked", f"client {client_id}: message looked like a credential")
        return {"code": "secret", "reply": _reply("secret", message), "store_as": REDACTED_PLACEHOLDER}

    from core import client_settings
    settings = client_settings.get(client_id)
    max_chars = settings.get("chat_max_message_chars")
    if max_chars and len(message or "") > max_chars:
        log_step(SERVICE_NAME, "too_long", f"client {client_id}: {len(message)} > {max_chars} chars")
        return {"code": "too_long", "reply": _reply("too_long", message),
                "store_as": (message or "")[:max_chars]}

    cap = settings.get("chat_daily_message_cap")
    if cap:
        try:
            sent = _sent_today(client_id)
        except Exception as e:
            # Fail OPEN: a broken count must not lock a client out of their chat
            print(f"[{SERVICE_NAME}] daily count failed for client {client_id} ({e}) - not capping")
            return None
        if sent >= cap:
            log_step(SERVICE_NAME, "daily_cap", f"client {client_id}: {sent}/{cap} messages today")
            return {"code": "daily_cap", "reply": _reply("daily_cap", message), "store_as": message}
    return None
