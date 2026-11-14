"""How long things usually take — MEASURED, said the way a person says it
(2026-11-15, docs/CHAT-SPEED.md §Time estimates).

Two readers, both from rows we already store, no migration:

- `action_seconds(action)`: the median run time of the last finished cards of
  that action (agent_actions decided_at -> finished_at, status done).
- `chat_seconds()`: the median length of the client chat turns that were long
  (over LONG_TURN_MS) in the last week (chat_turn_timings) — the number behind
  the chat's "still working" line, which only shows on a long turn.

Rules:
- A number is shown only with enough history (MIN_SAMPLES). No history = no
  number; the fixed calm text stays. Never a guess presented as a measurement.
- Said in plain words, rounded: "about 2 minutes", never "117 seconds".
- Cached in memory (CACHE_SECONDS) — a chat turn never waits for this read,
  and every failure returns None.
"""
import time
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "task_eta"
MIN_SAMPLES = 5
SAMPLE_ROWS = 40
CACHE_SECONDS = 1800
LONG_TURN_MS = 15000          # the dashboard's "still working" line appears after this
CHAT_DAYS = 7
MAX_SECONDS = 3 * 3600        # a run longer than this is a stuck row, not a duration

LANGS = ("he", "en", "fr", "ar", "ru")
_cache = {}                   # key -> (time, seconds | None)

_ABOUT = {
    "seconds": {"he": "כמה שניות", "en": "a few seconds", "fr": "quelques secondes", "ar": "بضع ثوانٍ",
                "ru": "несколько секунд"},
    "half": {"he": "כחצי דקה", "en": "about half a minute", "fr": "environ trente secondes",
             "ar": "نحو نصف دقيقة", "ru": "около полминуты"},
    "one": {"he": "כדקה", "en": "about a minute", "fr": "environ une minute", "ar": "نحو دقيقة",
            "ru": "около минуты"},
    "two": {"he": "כשתי דקות", "en": "about 2 minutes", "fr": "environ 2 minutes", "ar": "نحو دقيقتين",
            "ru": "около 2 минут"},
    "many": {"he": "כ-{n} דקות", "en": "about {n} minutes", "fr": "environ {n} minutes", "ar": "نحو {n} دقائق",
             "ru": "около {n} минут"},
    "hour": {"he": "כשעה", "en": "about an hour", "fr": "environ une heure", "ar": "نحو ساعة", "ru": "около часа"},
}

# "Usually {about}." — appended to a running card's progress text
USUALLY = {"he": "בדרך כלל זה לוקח {about}.", "en": "It usually takes {about}.",
           "fr": "Cela prend généralement {about}.", "ar": "يستغرق ذلك عادةً {about}.",
           "ru": "Обычно это занимает {about}."}
# The chat's long-wait line with the measured time (dashboard i18n carries the
# same sentence without a number for when there is no history)
CHAT_LONG = {"he": "עובדים על זה ביסודיות — תשובה כזו לוקחת בדרך כלל {about}.",
             "en": "Working on it properly — an answer like this usually takes {about}.",
             "fr": "On s'en occupe sérieusement — une réponse comme celle-ci prend généralement {about}.",
             "ar": "نعمل على ذلك بعناية — إجابة كهذه تستغرق عادةً {about}.",
             "ru": "Готовим как следует — такой ответ обычно занимает {about}."}


def _lang(lang: str) -> str:
    return lang if lang in LANGS else "en"


def about(seconds, lang: str = "he") -> str:
    """'about 2 minutes' in the reader's language ('' for no / bad input)."""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    lang = _lang(lang)
    if s < 20:
        return _ABOUT["seconds"][lang]
    if s < 50:
        return _ABOUT["half"][lang]
    if s < 100:
        return _ABOUT["one"][lang]
    minutes = int(round(s / 60.0))
    if minutes == 2:
        return _ABOUT["two"][lang]
    if minutes >= 50:
        return _ABOUT["hour"][lang]
    if minutes > 10:
        minutes = int(round(minutes / 5.0)) * 5      # "about 15 minutes", not "about 13"
    return _ABOUT["many"][lang].replace("{n}", str(minutes))


def _median(values: list):
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _parse(stamp):
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _cached(key, compute):
    hit = _cache.get(key)
    if hit and hit[0] > time.time() - CACHE_SECONDS:
        return hit[1]
    try:
        value = compute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] {key}: no estimate ({type(e).__name__})")
        value = None
    _cache[key] = (time.time(), value)
    return value


def durations(rows: list) -> list:
    """Seconds between decided_at and finished_at of each row that has both."""
    out = []
    for row in rows or []:
        start, end = _parse(row.get("decided_at")), _parse(row.get("finished_at"))
        if start and end:
            seconds = (end - start).total_seconds()
            if 0 < seconds <= MAX_SECONDS:
                out.append(seconds)
    return out


def action_seconds(action: str):
    """Median run time of this action's recent finished cards, or None."""
    def compute():
        rows = (_db().table("agent_actions").select("decided_at,finished_at")
                .eq("action", action).eq("status", "done").order("finished_at", desc=True)
                .limit(SAMPLE_ROWS).execute().data or [])
        values = durations(rows)
        return _median(values) if len(values) >= MIN_SAMPLES else None
    return _cached(("action", str(action)), compute)


def chat_seconds():
    """Median length of the LONG client chat turns of the last week, or None."""
    def compute():
        since = (datetime.now(timezone.utc) - timedelta(days=CHAT_DAYS)).isoformat()
        rows = (_db().table("chat_turn_timings").select("total_ms,outcome").gte("created_at", since)
                .gte("total_ms", LONG_TURN_MS).order("created_at", desc=True).limit(400).execute().data or [])
        values = [int(r.get("total_ms") or 0) / 1000.0 for r in rows if r.get("outcome") != "covered"]
        return _median(values) if len(values) >= MIN_SAMPLES else None
    return _cached(("chat",), compute)


def usually(action: str, lang: str = "he") -> str:
    """'It usually takes about 2 minutes.' for a card, '' without history."""
    seconds = action_seconds(action)
    return USUALLY[_lang(lang)].replace("{about}", about(seconds, lang)) if seconds else ""


def chat_pace(lang: str = "he") -> dict:
    """What the dashboard needs for its long-wait line: the sentence with the
    measured time, or {} when there is not enough history."""
    seconds = chat_seconds()
    if not seconds:
        return {"measured": False}
    return {"measured": True, "usual_seconds": int(seconds),
            "long_wait_text": CHAT_LONG[_lang(lang)].replace("{about}", about(seconds, lang))}
