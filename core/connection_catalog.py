"""Connections onboarding through the chat (2026-11-01).

The dashboard has ONE "חבר" button per connection. It opens the client's
agent chat with that connection as context, and the flow is:

  agent: one question, two chips — "יש לי חשבון" / "אין לי עדיין"
  "יש לי"  → the existing connect flow (OAuth / the key field on the card)
  "אין לי" → the sign-up link + short plain-Hebrew steps; for a paid tier also
             the plan link, ONE recommended plan, and that the cost is paid
             directly to the vendor, never to uallak
  "סיימתי" → the system checks the connection and confirms in Hebrew; if it
             is not there yet, ONE gentle reminder later (never more)

Included-tier connections (part of the package, nothing for the client to
open) show nothing at all.

LINKS COME ONLY FROM THE TABLE `connection_catalog` (migrations/2026-11-01-
connection-catalog.sql), editable by the owner on /admin/connections/ without
a deploy. This module holds no vendor URL of its own. A row that is disabled,
missing, or has no sign-up link falls back to human support (WhatsApp + a
staff alert) — never to a guessed link.

Keys pasted during the flow go through the card's existing key field
(core/vendor_tools.connect → encrypted client_accounts row): this module never
receives, echoes or logs a key.
"""
import time
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "connection_catalog"
TABLE = "connection_catalog"
TIERS = ("included", "free_account", "paid_account")
FIELDS = ("key", "tier", "signup_url", "pricing_url", "recommended_plan_note", "help_notes", "enabled")
CACHE_SECONDS = 300
REMINDER_PREFIX = "conn_reminder:"
REMINDER_AFTER = timedelta(days=2)

# Which agent's chat a connection opens in (dashboard persona ids)
PERSONA = {
    "google_ads": "google", "tag_manager": "google", "search_console": "google",
    "analytics": "google", "merchant_center": "google",
    "meta": "meta",
    "youtube": "media", "tiktok": "media", "higgsfield": "media", "elevenlabs": "media",
    "creatomate": "media", "uppbeat": "media", "google_drive": "media",
    "website": "website", "store": "website",
    "gmail": "general", "manychat": "general",
}

# How "done" is verified: client_accounts platforms (any active one counts),
# or a vendor_tools tool. A key with neither cannot be checked by the system,
# so "done" goes to the team instead of pretending.
PLATFORMS = {
    "google_ads": ("google_ads",), "meta": ("meta_ads", "meta_page"), "tiktok": ("tiktok",),
    "youtube": ("youtube",), "gmail": ("gmail_send",), "search_console": ("search_console",),
    "analytics": ("google_analytics",), "google_drive": ("google_drive_file",),
    "tag_manager": ("google_tagmanager",), "website": ("wordpress",), "store": ("woocommerce",),
}
VENDOR_TOOLS = ("higgsfield", "elevenlabs")

# The full-website system is billed by uallak (Step 0: InstaWP on our own
# workspace, passed through in PRICING) — "אין לי" offers our build, not links
BUILD_INSTEAD = {"website"}

# No self-service connect: the credentials go to the team (core/automation_service)
TEAM_CONNECT = {"manychat"}

# Names the client reads (Hebrew). Vendor names appear only where the client
# must act on the vendor's own site — which is exactly this flow.
NAMES = {
    "google_ads": "Google Ads", "meta": "פייסבוק ואינסטגרם", "tiktok": "TikTok", "youtube": "YouTube",
    "gmail": "Gmail", "search_console": "Google Search Console", "analytics": "Google Analytics",
    "google_drive": "Google Drive", "tag_manager": "Google Tag Manager",
    "merchant_center": "Google Merchant Center", "website": "האתר שלך", "store": "החנות באתר",
    "higgsfield": "Higgsfield", "elevenlabs": "ElevenLabs", "creatomate": "Creatomate",
    "uppbeat": "Uppbeat", "manychat": "ManyChat",
}

CHIP_HAS = "יש לי חשבון"
CHIP_NONE = "אין לי עדיין"
CHIP_DONE = "סיימתי"

_cache = {"at": 0.0, "rows": None}


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── The table (the ONLY source of links) ────────────────────────────────────

def _clean(row: dict) -> dict:
    out = {k: row.get(k) for k in FIELDS}
    out["key"] = str(out["key"] or "").strip()
    out["tier"] = out["tier"] if out["tier"] in TIERS else "included"
    for k in ("signup_url", "pricing_url", "recommended_plan_note", "help_notes"):
        out[k] = str(out[k] or "").strip()
    for k in ("signup_url", "pricing_url"):
        if out[k] and not out[k].startswith("https://"):
            out[k] = ""   # only https links ever reach a client
    out["enabled"] = bool(out["enabled"])
    return out


def rows(fresh: bool = False) -> list:
    if not fresh and _cache["rows"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["rows"]
    try:
        data = _db().table(TABLE).select(",".join(FIELDS)).order("key").limit(500).execute().data or []
        result = [_clean(r) for r in data]
    except Exception as e:
        print(f"[{SERVICE_NAME}] table read failed: {type(e).__name__}")
        result = []   # table not created yet: every flow falls back to human support
    _cache.update(at=time.time(), rows=result)
    return result


def forget():
    _cache.update(at=0.0, rows=None)


def entry(key: str) -> dict:
    key = (key or "").strip()
    return next((r for r in rows() if r["key"] == key), {})


def admin_update(key: str, changes: dict) -> dict:
    """Owner edit from /admin/connections/. Only the editable fields; links must
    be https. Raises ValueError on a bad value or unknown key."""
    if not entry(key) and not any(r["key"] == key for r in rows(fresh=True)):
        raise ValueError("unknown_key")
    clean = {}
    for field in ("signup_url", "pricing_url", "recommended_plan_note", "help_notes", "tier", "enabled"):
        if field not in changes:
            continue
        value = changes[field]
        if field in ("signup_url", "pricing_url"):
            value = str(value or "").strip()
            if value and (not value.startswith("https://") or len(value) > 500 or any(c.isspace() for c in value)):
                raise ValueError(field)
        elif field == "tier":
            if value not in TIERS:
                raise ValueError(field)
        elif field == "enabled":
            value = bool(value)
        else:
            value = str(value or "").strip()[:1500]
        clean[field] = value
    if not clean:
        raise ValueError("nothing_to_change")
    clean["updated_at"] = _now().isoformat()
    _db().table(TABLE).update(clean).eq("key", key).execute()
    forget()
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, [f"connection_catalog '{key}' edited: {', '.join(sorted(k for k in clean if k != 'updated_at'))}"])
    return entry(key)


# ─── Status ──────────────────────────────────────────────────────────────────

def can_verify(key: str) -> bool:
    return key in PLATFORMS or key in VENDOR_TOOLS


def is_connected(client_id: int, key: str) -> bool:
    if key in VENDOR_TOOLS:
        from core import vendor_tools
        return vendor_tools.is_connected(client_id, key)
    platforms = PLATFORMS.get(key)
    if not platforms:
        return False
    from agents.client_agent import get_accounts
    return any(a.get("platform") in platforms and a.get("status") == "active"
               for a in (get_accounts(client_id) or []))


# ─── The flow ────────────────────────────────────────────────────────────────

def _name(key: str) -> str:
    return NAMES.get(key, key)


def _support_link() -> str:
    try:
        from core import admin_service
        from core.client_leads import whatsapp_link
        return whatsapp_link(admin_service.support_whatsapp_number()) or ""
    except Exception:
        return ""


def _human_fallback(client_id: int, key: str, why: str) -> dict:
    """No usable link in the table: a person takes it from here."""
    try:
        from core import chat_fallback
        chat_fallback.alert_once("connection_onboarding", client_id, f"{why}:{key}",
                                 f"client {client_id} wants to open '{key}' but the catalog has no usable "
                                 f"sign-up link ({why}) — fill it on /admin/connections/ and help them", days=3)
    except Exception:
        pass
    link = _support_link()
    text = (f"את הפתיחה של {_name(key)} נעשה איתכם יחד — מישהו מהצוות שלנו יחזור אליכם בקרוב."
            + (f" אפשר גם לכתוב לנו עכשיו בוואטסאפ: {link}" if link else ""))
    return {"messages": [text], "chips": [], "support": True}


# The online store (agents/store_agent): included in the website package, no
# vendor account to open — the steps create a READ key on the client's own
# store. Menu path and field names checked in WooCommerce's docs
# (woocommerce.com/document/woocommerce-rest-api/, 2026-11-02): WooCommerce >
# Settings > Advanced > REST API > Add Key; Description, User, Permissions
# (Read / Write / Read/Write); then "Generate API key" shows the Consumer key
# and Consumer secret once. Vendor words appear because the client must act
# on that screen.
CHIP_STORE_HAS = "יש לי מפתח"
CHIP_STORE_HOW = "איך יוצרים מפתח?"
CHIP_STORE_MADE = "יצרתי מפתח"
STORE_STEPS = (
    "1. נכנסים לניהול האתר (wp-admin) עם משתמש מנהל.",
    "2. בתפריט: WooCommerce ← Settings (הגדרות) ← Advanced (מתקדם) ← REST API.",
    "3. לוחצים Add Key (או Create an API key).",
    "4. ממלאים: Description — uallak. User — המשתמש שלכם. Permissions — Read (קריאה בלבד). חשוב: רק Read.",
    "5. לוחצים Generate API key. מופיעים Consumer key (מתחיל ב-ck_) ו-Consumer secret (מתחיל ב-cs_) — הם מוצגים פעם אחת בלבד, אז מעתיקים את שניהם.",
    "6. מדביקים את שניהם בכרטיס \"החנות באתר\" שנפתח כאן (לא בצ׳אט) ולוחצים \"חיבור\".",
)


def _store_flow(client_id: int, step: str) -> dict:
    base = {"show": True, "key": "store", "persona": "website", "connect": "", "support": False}
    if step == "start":
        return {**base, "messages": [
            "נחבר את החנות באתר כדי שאוכל לענות על הזמנות ומכירות מהנתונים האמיתיים. "
            "החיבור הוא לקריאה בלבד — אנחנו לא משנים שום דבר בחנות. "
            "הדרך המומלצת היא מפתח קריאה ייעודי שאתם יוצרים בחנות (ואפשר לבטל אותו שם בכל רגע). יש לכם כבר מפתח כזה?"],
            "chips": [{"label": CHIP_STORE_HAS, "step": "has"}, {"label": CHIP_STORE_HOW, "step": "none"}]}
    if step == "has":
        return {**base, "connect": "store", "chips": [],
                "messages": ["מעולה. מדביקים את Consumer key ואת Consumer secret בכרטיס \"החנות באתר\" שנפתח (לא בצ׳אט) ולוחצים \"חיבור\" — אני בודק שהמפתח עובד לפני שהוא נשמר."]}
    if step == "none":
        return {**base, "connect": "store", "chips": [{"label": CHIP_DONE, "step": "done"}],
                "messages": ["ככה יוצרים מפתח קריאה, בערך שתי דקות:", "\n".join(STORE_STEPS),
                             "כשסיימתם, לחצו \"סיימתי\" ואבדוק שהחנות מחוברת."]}
    if step == "done":
        if is_connected(client_id, "store"):
            _clear_reminder(client_id, "store")
            return {**base, "chips": [], "connected": True,
                    "messages": ["החנות מחוברת ✓ — מעכשיו אפשר לשאול אותי על הזמנות, מכירות והחזרים."]}
        _schedule_reminder(client_id, "store")
        return {**base, "connect": "store", "chips": [], "connected": False,
                "messages": ["עוד לא רואה את החנות מחוברת. מדביקים את שני הערכים בכרטיס \"החנות באתר\" ולוחצים \"חיבור\" — ואני אבדוק שוב."]}
    raise ValueError("bad_step")


def flow(client_id: int, key: str, step: str) -> dict:
    """One turn of the connect conversation. Pure decision from the table row
    (+ the connection status on "done"); the caller posts `messages` into the
    persona's thread. Returns {"show": False} for included / unknown keys —
    except the store, whose steps are on the client's own site (_store_flow)."""
    if key == "store":
        return _store_flow(client_id, step)
    row = entry(key)
    if row and row["tier"] == "included":
        return {"show": False}
    if not row and key not in PERSONA:
        return {"show": False}
    persona = PERSONA.get(key, "general")
    base = {"show": True, "key": key, "persona": persona, "connect": "", "support": False}
    name = _name(key)

    if step == "start":
        return {**base, "messages": [f"נחבר את {name}. יש לכם כבר חשבון שם?"],
                "chips": [{"label": CHIP_HAS, "step": "has"}, {"label": CHIP_NONE, "step": "none"}]}

    if step == "has":
        if key in TEAM_CONNECT:
            return {**base, "connect": key, "chips": [],
                    "messages": [f"מעולה. את החיבור של {name} עושים יחד עם הצוות שלנו — "
                                 "נפתחת לכם שיחת וואטסאפ איתנו, ומשם ממשיכים."]}
        if key == "website":
            return {**base, "connect": key, "chips": [],
                    "messages": ["מעולה. כתבו את כתובת האתר בכרטיס שנפתח — נבדוק בעצמנו במה הוא בנוי ונמשיך מכאן."]}
        return {**base, "connect": key, "chips": [],
                "messages": [f"מעולה. פותח לכם את החיבור של {name} — מאשרים במסך שייפתח, וזהו."
                             if key not in VENDOR_TOOLS else
                             f"מעולה. מדביקים את המפתח בשדה שנפתח בכרטיס של {name} (לא בצ׳אט) ולוחצים \"חיבור\"."]}

    if step == "none":
        if key in BUILD_INSTEAD and row and row["enabled"]:
            note = row["recommended_plan_note"] or row["help_notes"]
            return {**base, "connect": "website_create", "chips": [],
                    "messages": ["אין בעיה — אנחנו מקימים ומארחים לכם את האתר." + (f" {note}" if note else "")]}
        if not row:
            return {**base, **_human_fallback(client_id, key, "missing_row")}
        if not row["enabled"] or not row["signup_url"]:
            return {**base, **_human_fallback(client_id, key, "disabled" if not row["enabled"] else "no_signup_url")}
        lines = [f"בואו נפתח חשבון ב-{name}. ההרשמה כאן: {row['signup_url']}"]
        if row["help_notes"]:
            lines.append(row["help_notes"])
        if row["tier"] == "paid_account":
            plan = (f"המסלולים כאן: {row['pricing_url']}" if row["pricing_url"] else "")
            rec = (f" ההמלצה שלנו: {row['recommended_plan_note']}" if row["recommended_plan_note"] else "")
            lines.append((plan + rec).strip())
            lines.append(f"התשלום על {name} עובר ישירות מכם אליהם — לעולם לא אלינו.")
        lines.append("כשסיימתם, לחצו \"סיימתי\" ואבדוק שהכול מחובר.")
        return {**base, "messages": [l for l in lines if l],
                "chips": [{"label": CHIP_DONE, "step": "done"}]}

    if step == "done":
        if not can_verify(key):
            try:
                from core import chat_fallback
                chat_fallback.alert_once("connection_onboarding", client_id, f"done:{key}",
                                         f"client {client_id} says they opened '{key}' — check and continue with them",
                                         days=1)
            except Exception:
                pass
            return {**base, "chips": [],
                    "messages": [f"תודה! הצוות שלנו יבדוק את {name} וימשיך איתכם מכאן."]}
        if is_connected(client_id, key):
            _clear_reminder(client_id, key)
            return {**base, "chips": [], "connected": True,
                    "messages": [f"{name} מחובר ✓ — מכאן אנחנו ממשיכים."]}
        _schedule_reminder(client_id, key)
        return {**base, "connect": key, "chips": [], "connected": False,
                "messages": [f"עוד לא רואה את {name} מחובר. אחרי שהחשבון פתוח, מאשרים את החיבור במסך שנפתח — ואני אבדוק שוב."]}

    raise ValueError("bad_step")


# ─── One gentle reminder, never more ─────────────────────────────────────────

def _settings():
    return _db().table("app_settings")


def _schedule_reminder(client_id: int, key: str):
    marker = f"{REMINDER_PREFIX}{int(client_id)}:{key}"
    try:
        existing = _settings().select("key").eq("key", marker).limit(1).execute().data or []
        if existing:
            return   # one reminder per connection, ever
        _settings().upsert({"key": marker, "value": {"due": (_now() + REMINDER_AFTER).isoformat(), "sent": False},
                            "updated_at": _now().isoformat()}, on_conflict="key").execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] reminder schedule failed: {type(e).__name__}")


def _clear_reminder(client_id: int, key: str):
    marker = f"{REMINDER_PREFIX}{int(client_id)}:{key}"
    try:
        rows_ = _settings().select("value").eq("key", marker).limit(1).execute().data or []
        if rows_ and not (rows_[0].get("value") or {}).get("sent"):
            _settings().upsert({"key": marker, "value": {"sent": True, "resolved": True},
                                "updated_at": _now().isoformat()}, on_conflict="key").execute()
    except Exception:
        pass


def send_due_reminders() -> dict:
    """Daily job (GET /api/connections/reminders). Each marker sends at most
    one chat line, then stays as sent forever."""
    from core.agent_actions import _post_client_message
    sent = resolved = 0
    try:
        markers = _settings().select("key, value").like("key", f"{REMINDER_PREFIX}%").limit(2000).execute().data or []
    except Exception:
        return {"sent": 0, "resolved": 0}
    now = _now()
    for marker in markers:
        value = marker.get("value") or {}
        if value.get("sent"):
            continue
        try:
            due = datetime.fromisoformat(value.get("due"))
        except (TypeError, ValueError):
            continue
        if due > now:
            continue
        try:
            client_id, key = marker["key"][len(REMINDER_PREFIX):].split(":", 1)
            client_id = int(client_id)
        except ValueError:
            continue
        connected = is_connected(client_id, key)
        if not connected:
            _post_client_message(client_id, PERSONA.get(key, "general"),
                                 f"רק תזכורת קטנה: {_name(key)} עוד לא מחובר. כשנוח לכם, לחצו \"חבר\" בכרטיס ונמשיך משם.")
            sent += 1
        else:
            resolved += 1
        _settings().upsert({"key": marker["key"], "value": {**value, "sent": True, "resolved": connected},
                            "updated_at": now.isoformat()}, on_conflict="key").execute()
    return {"sent": sent, "resolved": resolved}
