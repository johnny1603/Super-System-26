"""Code checks on a chat reply, AFTER the model and before the client sees it
(2026-11-22, client 27 / ליאור). Each one removes whole sentences only — it
never writes new claims into the reply.

1. scope (`drop_plan_talk`) — a SPECIALIST answers the request it was asked.
   "I want a post of 3 reasons why custom design is a special gift… 3 posts,
   one reason each" was answered with the weekly content plan. When the
   client's message did not ask for a plan / the week / a calendar, sentences
   that pitch the weekly content plan are dropped (only the manager
   orchestrates), and `plan_card_off_request` tells the caller to drop a plan
   card the model proposed.
2. repeats (`dedupe_sentences`) — the same sentence twice in ONE reply
   ("outside this would cost thousands of shekels", twice, 2026-11-16 02:33):
   the later copies are dropped.
3. connected channels (`drop_connected_offers`) — "Let's connect TikTok. Do you
   already have an account?" while TikTok IS connected (client 27, nine times):
   a sentence offering to connect a channel whose LIVE state is connected is
   dropped (core/connection_catalog.live_state).

Patterns live in per-language tables (he, en, fr, ar, ru) and are matched in
every language at once: a client may write in one language and be answered in
another.
"""
import re

# The client asked for a plan / the week / a calendar → plan talk is on-request
PLAN_ASK = {
    "he": r"תוכני(?:ת|ות)|תכני(?:ת|ות)|לוח (?:ה)?תוכן|גאנט|שבוע|שבועי|חודשי|לחודש|יומן (?:ה)?תוכן",
    "en": r"\bplan\b|\bplans\b|\bplanning\b|\bweek|\bmonth|calendar|schedule",
    "fr": r"\bplan\b|planning|semaine|hebdo|mois|calendrier",
    "ar": r"خطة|خطط|أسبوع|اسبوع|أسبوعي|شهر|تقويم|جدول",
    "ru": r"план|недел|месяц|календар|расписан",
}
# A sentence that pitches the weekly content plan
PLAN_MENTION = {
    "he": (r"תוכני(?:ת|ות) (?:ה)?תוכן|תכני(?:ת|ות) (?:ה)?תוכן|(?:ה)?תוכנית (?:ה)?שבועית|(?:ה)?תכנית (?:ה)?שבועית"
           r"|תוכן (?:ה)?שבועי|תוכנית ל(?:ה)?שבוע|תכנית ל(?:ה)?שבוע|לוח (?:ה)?תוכן|(?:ה)?פוסטים של השבוע"
           r"|[מבל]התוכנית|[מבל]התכנית"),
    "en": (r"(?:weekly|content) plan|plan for (?:the|this|next) week|week'?s (?:content|posts)|content calendar"
           r"|(?:from|in|into|of) the plan\b"),
    "fr": r"plan (?:de contenu|hebdomadaire)|planning (?:de contenu|de la semaine)|calendrier (?:de|éditorial)",
    "ar": r"خطة (?:ال)?محتوى|(?:ال)?خطة (?:ال)?أسبوعية|محتوى (?:ال)?أسبوع|جدول (?:ال)?محتوى",
    "ru": r"контент[- ]план|план (?:контента|на неделю|публикаций)|недельн\w* план",
}
PLAN_CARDS = ("build_content_plan", "approve_content_plan")

# An offer to connect <name> / "do you have an account there?"
CONNECT_OFFER = {
    "he": r"(?:נחבר|לחבר|שנחבר|תחברו|חברו)(?: עכשיו)? (?:את )?(?:ה)?{name}",
    "en": r"\bconnect (?:your |the )?{name}",
    "fr": r"connect(?:er|ons|ez) (?:votre |le |la )?{name}",
    "ar": r"(?:ربط|نربط|اربطوا) (?:حساب )?{name}",
    "ru": r"подключ(?:им|ить|ите) (?:ваш |ваш аккаунт )?{name}",
}
# "Do you already have an account there?" — dropped with the offer it follows
ACCOUNT_QUESTION = {
    "he": r"יש לכם (?:כבר )?חשבון",
    "en": r"\bhave an account\b",
    "fr": r"avez-vous (?:déjà )?un compte",
    "ar": r"لديكم حساب",
    "ru": r"есть (?:там )?аккаунт",
}
# The names a reply may use for each connection (core/connection_catalog keys)
CHANNEL_NAMES = {
    "tiktok": ("TikTok", "טיקטוק", "تيك توك", "تيك_توك"),
    "youtube": ("YouTube", "יוטיוב", "يوتيوب"),
    "meta": ("Facebook", "Instagram", "פייסבוק", "אינסטגרם", "فيسبوك", "إنستغرام"),
    "google_ads": ("Google Ads",),
    "search_console": ("Search Console",),
    "analytics": ("Google Analytics",),
    "google_drive": ("Google Drive",),
    "gmail": ("Gmail",),
}

_SPLIT = re.compile(r"((?<=[.!?…؟])[ \t]+|\n+)")
MIN_REPEAT_CHARS = 12


def _pieces(text: str) -> list:
    """[sentence, separator, sentence, separator, …] — joined back exactly."""
    return _SPLIT.split(text or "")


def _join(pieces: list) -> str:
    out = "".join(pieces)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _drop(text: str, kill) -> tuple:
    """Remove the sentences `kill(sentence)` is true for. (text, dropped)."""
    pieces = _pieces(text)
    dropped = 0
    for i in range(0, len(pieces), 2):
        if pieces[i].strip() and kill(pieces[i]):
            pieces[i] = ""
            if i + 1 < len(pieces) and not pieces[i + 1].startswith("\n"):
                pieces[i + 1] = ""
            dropped += 1
    return (_join(pieces), dropped) if dropped else (text, 0)


def _any(table: dict, text: str, **fmt) -> bool:
    for pattern in table.values():
        if re.search(pattern.format(**fmt) if fmt else pattern, text or "", re.IGNORECASE):
            return True
    return False


# ─── 1. scope ────────────────────────────────────────────────────────────────

def asked_for_plan(message: str) -> bool:
    return _any(PLAN_ASK, message)


def plan_card_off_request(persona_id: str, proposed: str, message: str) -> bool:
    """A specialist proposed the content plan card for a message that did not
    ask for a plan: the caller drops the card. The manager ("general") keeps it."""
    return (persona_id or "general") != "general" and proposed in PLAN_CARDS and not asked_for_plan(message)


def drop_plan_talk(reply: str, message: str) -> tuple:
    """(reply, dropped). Nothing is dropped when the client asked for a plan,
    or when every sentence would go (the reply is left as it was)."""
    if not reply or asked_for_plan(message):
        return reply, 0
    out, dropped = _drop(reply, lambda s: _any(PLAN_MENTION, s))
    return (out, dropped) if out.strip() else (reply, 0)


# ─── 2. repeats ──────────────────────────────────────────────────────────────

def _norm(sentence: str) -> str:
    s = re.sub(r"[\s‏‎]+", " ", sentence or "").strip().casefold()
    return re.sub(r"[^\w ]+", "", s).strip()


def dedupe_sentences(reply: str) -> tuple:
    """(reply, dropped): a sentence already said in this reply is not said again."""
    seen = set()

    def kill(sentence):
        key = _norm(sentence)
        if len(key) < MIN_REPEAT_CHARS:
            return False
        if key in seen:
            return True
        seen.add(key)
        return False
    return _drop(reply, kill)


# ─── 3. connected channels ───────────────────────────────────────────────────

def _offers(sentence: str, names: tuple) -> bool:
    for name in names:
        if name.casefold() not in sentence.casefold():
            continue
        if _any(CONNECT_OFFER, sentence, name=re.escape(name)):
            return True
    return False


def drop_connected_offers(client_id: int, reply: str, is_live=None) -> tuple:
    """(reply, dropped keys). `is_live(client_id, key)` → True when connected
    (default: connection_catalog.live_state == "connected"). Only channels the
    reply names are checked — no read for a reply that names none."""
    if not reply:
        return reply, []
    named = {key: names for key, names in CHANNEL_NAMES.items()
             if any(n.casefold() in reply.casefold() for n in names)}
    if not named:
        return reply, []
    if is_live is None:
        from core import connection_catalog

        def is_live(cid, key):
            return connection_catalog.live_state(cid, key)["state"] == "connected"
    connected = {}
    for key, names in named.items():
        try:
            if is_live(client_id, key):
                connected[key] = names
        except Exception:
            continue   # unreadable: leave the sentence (the connect flow itself checks again)
    if not connected:
        return reply, []
    hit = set()
    last = {"dropped": False}

    def kill(sentence):
        drop = False
        for key, names in connected.items():
            if _offers(sentence, names) or (any(n.casefold() in sentence.casefold() for n in names)
                                            and _any(ACCOUNT_QUESTION, sentence)):
                hit.add(key)
                drop = True
                break
        if not drop and last["dropped"] and _any(ACCOUNT_QUESTION, sentence):
            drop = True   # the question that belonged to the offer just dropped
        last["dropped"] = drop
        return drop
    out, dropped = _drop(reply, kill)
    return (out, sorted(hit)) if dropped and out.strip() else (reply, [])
