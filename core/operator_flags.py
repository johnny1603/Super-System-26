"""Feature-flag overrides the operator can flip (Tier 2, docs/OPERATOR.md).

A flag's default still comes from the environment, exactly as before. An
override is one app_settings row (`flag_override:<NAME>`, {"on": bool}) that
wins over the environment while it exists; deleting it ("back to default")
undoes the flip. No new deploy, no revision, no env var — and so no secret
value is ever read or rewritten to flip a flag.

Only flags listed in FLAGS can be overridden. `can_turn_on: False` marks a
flag whose ON state needs something only the owner can do first (the
round-2 Google flags request OAuth scopes Google has not approved yet): those
can be switched OFF here as a kill switch, never ON — an ON override for them
is refused when written AND ignored when read.

A flag listed with `"default": True` (the chat routing switch)
is ON when its env var is unset — their OFF is the operator's kill switch.

Reads are cached CACHE_SECONDS per instance; an unreadable table means "no
override" (the environment decides), never an error in the flag's caller.
"""
import os
import time
from datetime import datetime, timezone

SERVICE_NAME = "operator_flags"
PREFIX = "flag_override:"
CACHE_SECONDS = 30
_TRUE = ("1", "true", "yes", "on")

_GOOGLE_ROUND2 = ("הדלקה מבקשת מלקוחות הרשאת Google שעוד לא אושרה בבדיקת Google (סבב 2). "
                  "רק אתה, אחרי אישור Google — docs/GOOGLE-ROUND2.md. כאן אפשר רק לכבות.")
FLAGS = {
    # ON unless the env says otherwise — the operator page's OFF is the
    # instant "everything on the strongest model" switch (core/model_routing)
    "MODEL_ROUTING_ENABLED": {
        "label_he": "ניתוב מודלים בצ'אט (מודל זול לשאלות פשוטות). כבוי = הכול על המודל החזק",
        "can_turn_on": True, "default": True,
        "note_he": "כיבוי משפיע תוך 30 שניות בכל השרתים."},
    "PRESENTER_VIDEO_ALL_CLIENTS": {
        "label_he": "סרטוני מציג פתוחים לכל הלקוחות (לא רק לחשבונות פנימיים)",
        "can_turn_on": True,
        "note_he": "המסלול עוד לא רץ מול הספק בחיים — מומלץ להדליק רק אחרי בדיקה חיה."},
    "GMAIL_SEND_ENABLED": {"label_he": "שליחת מיילים יוצאים מחשבון Gmail של הלקוח",
                           "can_turn_on": False, "note_he": _GOOGLE_ROUND2},
    "SEARCH_CONSOLE_ENABLED": {"label_he": "קריאת Search Console", "can_turn_on": False,
                               "note_he": _GOOGLE_ROUND2},
    "ANALYTICS_ENABLED": {"label_he": "קריאת Google Analytics (תנועה לאתר)", "can_turn_on": False,
                          "note_he": _GOOGLE_ROUND2},
    "DRIVE_FILE_ENABLED": {"label_he": "קבצים ב-Drive של הלקוח עצמו", "can_turn_on": False,
                           "note_he": _GOOGLE_ROUND2},
}

_cache = {}   # name -> (time, override or None)


class FlagError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_value(name: str) -> bool:
    """The env var; unset/empty -> the flag's listed default (False unless listed)."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return bool(FLAGS.get(name, {}).get("default", False))
    return raw in _TRUE


def override(name: str):
    """True / False while an override exists, None otherwise."""
    hit = _cache.get(name)
    if hit and hit[0] > time.time() - CACHE_SECONDS:
        return hit[1]
    try:
        rows = (_db().table("app_settings").select("value").eq("key", PREFIX + name).limit(1)
                .execute().data or [])
        value = rows[0].get("value") if rows else None
        value = value.get("on") if isinstance(value, dict) and isinstance(value.get("on"), bool) else None
    except Exception:
        return None          # not cached: the next call tries again
    _cache[name] = (time.time(), value)
    return value


def enabled(name: str) -> bool:
    """THE reader for a listed flag: override (within what it may do), else env."""
    if name not in FLAGS:
        return env_value(name)
    o = override(name)
    if o is False:
        return False
    if o is True and FLAGS[name]["can_turn_on"]:
        return True
    return env_value(name)


def view() -> list:
    return [{"name": n, "label_he": f["label_he"], "note_he": f["note_he"], "can_turn_on": f["can_turn_on"],
             "env": env_value(n), "override": override(n), "effective": enabled(n)} for n, f in FLAGS.items()]


def set_flag(name: str, state: str) -> dict:
    """state: "on" | "off" | "default" (remove the override). Returns the
    before/after so the audit line and the undo are exact."""
    if name not in FLAGS:
        raise FlagError("ERR_FLAG_UNKNOWN")
    if state not in ("on", "off", "default"):
        raise FlagError("ERR_FLAG_STATE")
    if state == "on" and not FLAGS[name]["can_turn_on"]:
        raise FlagError("ERR_FLAG_OWNER_ONLY")
    before = enabled(name)
    if state == "default":
        _db().table("app_settings").delete().eq("key", PREFIX + name).execute()
    else:
        _db().table("app_settings").upsert({"key": PREFIX + name, "value": {"on": state == "on"},
                                            "updated_at": _now()}, on_conflict="key").execute()
    _cache.pop(name, None)
    return {"flag": name, "before": before, "after": enabled(name), "state": state,
            "undo": "default", "note": "other instances see it within %d seconds" % CACHE_SECONDS}
