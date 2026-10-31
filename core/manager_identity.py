"""The client's account manager — the ONE source of truth for who the main
concierge is (2026-10-31).

The concierge has three identities: ג'וני, ניסים and אלינור. The CLIENT picks
one: a client whose choice is still NULL is shown the picker when they open
the dashboard (existing clients included), and can switch at any time in
their settings (/profile/ and the dashboard's personal area); staff can set
it from the Control Room. That choice is THE identity everywhere
the concierge speaks or signs: the chat window, its cards, the daily login
greeting (the "morning briefing"), emails, WhatsApp notifications and the
concierge's own system prompt. Everything reads it from here — nothing else
stores, infers or hard-codes a manager name.

Only the NAME and the FACE differ. Personality, tone, rules and abilities are
identical whichever is chosen: `prompt_line` is the only thing a prompt adds,
and it says exactly that.

Storage: `clients.manager_persona` (migrations/2026-10-31-manager-persona.sql),
one of MANAGERS' keys or NULL. NULL means the client never chose. It is NEVER
filled in silently — nothing writes the default back. Until they pick, it
only RENDERS as `default_key`: a client who had picked the old female companion ("דנה",
stored as owner_gender='female') keeps a woman — אלינור — and everyone else
gets ג'וני. The specialists (יואב, מאיה, אורי, ליאור) keep their own names;
their faces are mapped in SPECIALIST_FACES.

Faces live in dashboard/assets/agents/<key>-portrait.{webp,png} (square) and
<key>-figure.{webp,png} (full body, transparent — the minimised chat window).
"""

LANGS = ("he", "en", "fr", "ar", "ru")

MANAGERS = {
    "johnny": {"gender": "male",
               "name": {"he": "ג'וני", "en": "Johnny", "fr": "Johnny", "ar": "جوني", "ru": "Джонни"}},
    "nissim": {"gender": "male",
               "name": {"he": "ניסים", "en": "Nissim", "fr": "Nissim", "ar": "نسيم", "ru": "Ниссим"}},
    "elinor": {"gender": "female",
               "name": {"he": "אלינור", "en": "Elinor", "fr": "Elinor", "ar": "إلينور", "ru": "Элинор"}},
}
ORDER = ("johnny", "nissim", "elinor")
DEFAULT_KEY = "johnny"

# Specialist persona id (support_agent.PERSONAS) -> face asset key
SPECIALIST_FACES = {"google": "yoav", "meta": "maya", "website": "uri", "media": "lior"}

ASSET_BASE = "/assets/agents/"

_ROLE = {"he": "מנהל/ת הלקוח שלך ב-uallak", "en": "your account manager at uallak",
         "fr": "votre responsable de compte chez uallak", "ar": "مدير حسابك في uallak",
         "ru": "ваш менеджер в uallak"}
_ROLE_GENDERED_HE = {"male": "מנהל הלקוח שלך ב-uallak", "female": "מנהלת הלקוח שלך ב-uallak"}


def _lang(lang: str) -> str:
    return lang if lang in LANGS else "he"


def valid(key) -> bool:
    return key in MANAGERS


def default_key(client: dict = None) -> str:
    """A client who never chose. The old picker's female companion maps to
    the female manager so nobody's companion changes gender overnight."""
    if (client or {}).get("owner_gender") == "female":
        return "elinor"
    return DEFAULT_KEY


def key_for(client: dict = None) -> str:
    stored = (client or {}).get("manager_persona")
    return stored if valid(stored) else default_key(client)


def assets(key: str) -> dict:
    return {"portrait": f"{ASSET_BASE}{key}-portrait.webp",
            "portrait_png": f"{ASSET_BASE}{key}-portrait.png",
            "figure": f"{ASSET_BASE}{key}-figure.webp",
            "figure_png": f"{ASSET_BASE}{key}-figure.png"}


def name(key: str, lang: str = "he") -> str:
    entry = MANAGERS.get(key) or MANAGERS[DEFAULT_KEY]
    return entry["name"][_lang(lang)]


def role(key: str, lang: str = "he") -> str:
    lang = _lang(lang)
    if lang == "he":
        return _ROLE_GENDERED_HE[(MANAGERS.get(key) or MANAGERS[DEFAULT_KEY])["gender"]]
    return _ROLE[lang]


def view(key: str, lang: str = "he") -> dict:
    """What a page needs to show one manager: name in every language (the UI
    switches language without a reload), gender, faces."""
    key = key if valid(key) else DEFAULT_KEY
    return {"key": key, "name": name(key, lang), "names": dict(MANAGERS[key]["name"]),
            "gender": MANAGERS[key]["gender"], **assets(key)}


def for_client(client: dict, lang: str = "he") -> dict:
    """The resolved identity, plus whether it was actually CHOSEN (the
    dashboard offers the picker until it was)."""
    out = view(key_for(client), lang)
    out["chosen"] = valid((client or {}).get("manager_persona"))
    return out


def for_client_id(client_id: int, lang: str = "he") -> dict:
    """Never raises: an unknown client or a failed read is the default
    manager, never a missing name in a sent message."""
    try:
        from agents.client_agent import get_client
        return for_client(get_client(client_id) or {}, lang)
    except Exception as e:
        print(f"[manager_identity] client {client_id} lookup failed (default used): {e}")
        return for_client({}, lang)


def options(lang: str = "he") -> list:
    return [view(k, lang) for k in ORDER]


def prompt_line(identity: dict) -> str:
    """The only manager-specific text in any prompt — the name, never a
    different personality."""
    names = identity.get("names") or MANAGERS[DEFAULT_KEY]["name"]
    he = "male" if identity.get("gender") == "male" else "female"
    return (f"\n\nYOUR NAME: you are {names['en']} ({names['he']} in Hebrew; in other languages: "
            f"{names['fr']} / {names['ar']} / {names['ru']}), the client's account manager on the "
            f"uallak team — the person they chose to work with. Refer to yourself in the "
            f"{he} grammatical gender in Hebrew. Use the name naturally (a sign-off, \"זה {names['he']}\") "
            f"and never more than once a message. The name changes NOTHING else: same voice, same "
            f"rules, same abilities as any other account manager here.")


def signoff(identity: dict, lang: str = "he") -> str:
    """One line under a client email or a WhatsApp notification."""
    return f"{identity.get('name') or name(DEFAULT_KEY, lang)} — {role(identity.get('key'), lang)}"


def save_choice(client_id: int, key: str) -> dict:
    """The client's own choice. Fails loudly (never silently drops it) when
    the column hasn't been migrated yet."""
    if not valid(key):
        return {"success": False, "code": "ERR_MANAGER_UNKNOWN"}
    from agents.client_agent import _db
    try:
        _db().table("clients").update({"manager_persona": key}).eq("id", client_id).execute()
    except Exception as e:
        print(f"[manager_identity] save failed for client {client_id} (column missing?): {e}")
        return {"success": False, "code": "ERR_MANAGER_NOT_SAVED"}
    return {"success": True, "manager": view(key)}


def change(client_id: int, key: str, by: str = "client") -> dict:
    """Set — or later switch — the client's manager. `by` is "client" or
    "staff:<who>" (the Control Room, for support cases). Nothing is cached
    anywhere: every chat turn, card, email, WhatsApp line and morning
    briefing reads the stored value through for_client / for_client_id, so
    the new name and face apply from the next thing said. Chat history is
    kept by persona channel, not by manager, so the open thread stays.
    Logged as a client_activity row (from → to, by whom)."""
    if not valid(key):
        return {"success": False, "code": "ERR_MANAGER_UNKNOWN"}
    from agents.client_agent import get_client, log_activity
    try:
        before = (get_client(client_id) or {}).get("manager_persona")
    except Exception:
        before = None
    saved = save_choice(client_id, key)
    if not saved.get("success"):
        return saved
    try:
        log_activity(client_id, "manager_identity", "manager_changed" if valid(before) else "manager_chosen",
                     {"from": before if valid(before) else None, "to": key, "by": by})
    except Exception as e:
        print(f"[manager_identity] change log failed for client {client_id}: {e}")
    return saved
