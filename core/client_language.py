"""THE client's language (2026-11-19, docs/LANGUAGES.md).

One place answers "which language does this client get?":

- `of(client_id)`       the client's language SETTING (clients.language), cached
                        60 s; anything unreadable or unsupported -> Hebrew.
- `resolve(client_id, lang)`  an explicit supported `lang` wins, else `of()`.
                        Every server-built line (follow-ups, failures, status
                        lines, notifications, emails) goes through this — never
                        the server's language and never a hard-coded "he".
- `for_message(client_id, message)`  the language of a REPLY: the language the
                        client is writing in when the script says so, else the
                        setting (English vs French: the setting breaks the tie).
- `payload(lang)`       what every chat model call gets as `reply_language`.
- `REPLY_LANGUAGE_RULE` the prompt rule that goes with it: everything the model
                        writes for the client is in that language, whatever
                        language the history, the data or the instructions are in.
- `direction`, `fmt_number`, `fmt_money`, `fmt_date` — RTL for he/ar, numbers,
  currency and dates per language.
- `HEBREW_ONLY_NOTE`    the one line shown in the client's language next to a
  deliberately Hebrew-only item (Israeli tax forms, the Hebrew-authoritative terms).

Stdlib only; the DB read is lazy so tests and light readers import it freely.
"""
import re
import time
from datetime import date, datetime

LANGS = ("he", "en", "fr", "ar", "ru")
RTL = ("he", "ar")
NAMES = {"he": "Hebrew", "en": "English", "fr": "French", "ar": "Arabic", "ru": "Russian"}
LOCALES = {"he": "he-IL", "en": "en-GB", "fr": "fr-FR", "ar": "ar", "ru": "ru-RU"}
_TTL = 60
_cache = {}


def _stored(client_id: int) -> str:
    from agents.client_agent import get_client
    return (get_client(int(client_id)) or {}).get("language") or "he"


def of(client_id) -> str:
    """The client's language setting; Hebrew when it can't be read."""
    if client_id in (None, ""):
        return "he"
    key = int(client_id)
    hit = _cache.get(key)
    if hit and hit[0] > time.time() - _TTL:
        return hit[1]
    try:
        lang = _stored(key)
    except Exception:
        return "he"                      # not cached: the next read tries again
    lang = lang if lang in LANGS else "he"
    _cache[key] = (time.time(), lang)
    return lang


def forget(client_id) -> None:
    """After the client changes the setting."""
    try:
        _cache.pop(int(client_id), None)
    except (TypeError, ValueError):
        pass


def resolve(client_id, lang=None) -> str:
    return lang if lang in LANGS else of(client_id)


def detect(text: str):
    """The language a message is written in, by script; None when unclear
    (no letters, or Latin letters: English and French look alike)."""
    sample = text or ""
    if re.search(r"[֐-׿]", sample):
        return "he"
    if re.search(r"[؀-ۿ]", sample):
        return "ar"
    if re.search(r"[Ѐ-ӿ]", sample):
        return "ru"
    if re.search(r"[A-Za-z]", sample):
        return "latin"
    return None


def for_message(client_id, message: str = "", stored: str = None) -> str:
    """The reply language: the one the client writes in, else their setting."""
    stored = stored if stored in LANGS else of(client_id)
    seen = detect(message)
    if seen == "latin":
        return stored if stored in ("en", "fr") else "en"
    return seen or stored


def direction(lang: str) -> str:
    return "rtl" if lang in RTL else "ltr"


def payload(lang: str) -> dict:
    lang = lang if lang in LANGS else "he"
    return {"code": lang, "name": NAMES[lang], "direction": direction(lang)}


REPLY_LANGUAGE_RULE = """

REPLY LANGUAGE — "reply_language" in your data is THE language of everything you write for the client:
the reply, every card title and body, proposals, plans, item names, reports, explanations and progress
lines. It is the language the client writes in, else their language setting. Earlier messages, your
data and these instructions may be in Hebrew or another language: that never changes the reply
language. Never switch back to Hebrew because the history is in Hebrew. Quote names (a business, a
file, a search phrase) exactly as they are; everything around them is in reply_language. Amounts stay
in their currency (₪)."""


def _group(n: int, sep: str) -> str:
    s = str(abs(int(n)))
    out = ""
    while len(s) > 3:
        out = sep + s[-3:] + out
        s = s[:-3]
    return ("-" if n < 0 else "") + s + out


_SEP = {"he": (",", "."), "en": (",", "."), "ar": (",", "."), "fr": (" ", ","), "ru": (" ", ",")}


def fmt_number(value, lang: str = "he", decimals: int = None) -> str:
    """1234567.5 -> 1,234,567.5 (he/en/ar), 1 234 567,5 (fr/ru)."""
    if value is None:
        return ""
    thousands, point = _SEP.get(lang, _SEP["he"])
    value = float(value)
    if decimals is None:
        decimals = 0 if value == int(value) else 1
    whole = int(abs(value))
    text = _group(whole, thousands)
    if decimals:
        frac = f"{abs(value):.{decimals}f}".split(".")[1]
        text += point + frac
    return ("-" if value < 0 else "") + text


def fmt_money(value, lang: str = "he", currency: str = "₪") -> str:
    """₪1,200 in he/en/ar; 1 200 ₪ in fr/ru."""
    num = fmt_number(value, lang)
    return f"{num} {currency}" if lang in ("fr", "ru") else f"{currency}{num}"


def fmt_date(value, lang: str = "he") -> str:
    """dd/mm/yyyy (he/fr/ru/ar) or dd/mm/yyyy too for en-GB; ISO strings accepted."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return str(value)
    sep = "." if lang == "ru" else "/"
    return f"{value.day:02d}{sep}{value.month:02d}{sep}{value.year}"


HEBREW_ONLY_NOTE = {
    "he": "",
    "en": "This document is in Hebrew only, as required in Israel. Ask us in the chat for an explanation in English.",
    "fr": "Ce document existe uniquement en hébreu, comme l'exige la réglementation israélienne. Demandez-nous une explication en français dans le chat.",
    "ar": "هذه الوثيقة بالعبرية فقط كما هو مطلوب في إسرائيل. اطلبوا منا شرحًا بالعربية في الدردشة.",
    "ru": "Этот документ доступен только на иврите, как того требуют правила Израиля. Попросите нас в чате объяснить его по-русски.",
}


def hebrew_only_note(lang: str) -> str:
    return HEBREW_ONLY_NOTE.get(lang, HEBREW_ONLY_NOTE["en"])
