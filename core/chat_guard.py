"""Client chat protection — checks that run BEFORE a message is stored or
reaches a model.

Three guards, in this order, all with fixed per-language replies (never an
LLM, never a generic error):

1. SECRETS — a message that looks like an API key, token or password is
   stored only REDACTED (core/redaction: each secret -> [REDACTED]) and is
   NOT sent to any model. The client is told warmly not to share it in chat
   and where to put it instead.
2. LENGTH — over `chat_max_message_chars` (core/client_settings, default
   2000): a warm "please shorten it" reply.
3. DAILY CAP — over `chat_daily_message_cap` inbound messages today (default
   40, counted across every chat thread, Israeli calendar day): the warm
   "נמשיך מחר, או שאפשר לפנות לצוות בוואטסאפ".

The secret check runs even when the other limits are off, because it
protects the client, not our costs.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.agent_base import log_step
from core.redaction import looks_like_secret, redact

SERVICE_NAME = "chat_guard"
_TZ = ZoneInfo("Asia/Jerusalem")
CHAT_CHANNEL_PREFIX = "dashboard_chat"

_REPLIES = {
    "secret": {
        "he": "רגע, נראה שזו סיסמה או מפתח — בבקשה אל תדביקו מפתחות או סיסמאות בצ׳אט; לביטחונכם הסתרנו אותם "
              "מההודעה. מכניסים אותם רק בשדה החיבור המאובטח בדשבורד, ואם משהו לא מסתדר — הצוות זמין בוואטסאפ: {wa}",
        "en": "Hold on — that looks like a password or key. Please don't paste keys or passwords in chat; for your "
              "security we hid it from the message. Use the secure connection field in your dashboard, and if "
              "something doesn't work, the team is on WhatsApp: {wa}",
        "fr": "Un instant — cela ressemble à un mot de passe ou à une clé. Merci de ne pas coller de clés ni de mots "
              "de passe dans le chat ; par sécurité, nous l'avons masqué dans le message. Utilisez le champ de "
              "connexion sécurisé du tableau de bord, et si ça ne marche pas, l'équipe est sur WhatsApp : {wa}",
        "ar": "لحظة — يبدو أن هذه كلمة مرور أو مفتاح. من فضلك لا تلصق مفاتيح أو كلمات مرور في الدردشة؛ لأمانك أخفيناها "
              "من الرسالة. استخدم حقل الربط الآمن في لوحة التحكم، وإن لم ينجح الأمر فالفريق متاح على واتساب: {wa}",
        "ru": "Минутку — похоже на пароль или ключ. Пожалуйста, не вставляйте ключи и пароли в чат; ради вашей "
              "безопасности мы скрыли это в сообщении. Используйте защищённое поле подключения в кабинете, а если "
              "что-то не получается — команда на связи в WhatsApp: {wa}",
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


def secret_reply(message: str) -> str:
    """The warm "please don't paste keys here" reply, in the client's language
    (also used by the sales chat's free-text turn)."""
    return _reply("secret", message)


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
        return {"code": "secret", "reply": _reply("secret", message), "store_as": redact(message)}

    from core import client_settings
    settings = client_settings.get(client_id)
    max_chars = settings.get("chat_max_message_chars")
    if max_chars and len(message or "") > max_chars:
        log_step(SERVICE_NAME, "too_long", f"client {client_id}: {len(message)} > {max_chars} chars")
        return {"code": "too_long", "reply": _reply("too_long", message),
                "store_as": redact((message or "")[:max_chars])}

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
