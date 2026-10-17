"""Per-client operating limits — the knobs that differ between a paying
client and an internal (billing_exempt) account, editable per client from the
admin Control Room.

Storage: ONE app_settings row per client, key "client_settings:<id>", value =
a JSON object holding only the fields Johnny has overridden. No migration and
no new table: app_settings is already the admin-tunable store, and a client
with no row simply gets the defaults for its tier. admin_service.get_settings
skips these keys so they never leak into the global settings screen.

Defaults are by TIER, decided at read time from clients.billing_exempt:
- paying clients keep every brake that existed before (weekly article cap,
  package check, repeat-topic block, scheduled "auto articles");
- billing-exempt clients are Johnny's own accounts: no weekly cap, no package
  check, a repeat topic is a note rather than a refusal, and nothing is ever
  written on a schedule — only on a request in chat.

Readers never raise: a failed read falls back to the tier defaults, and a
failed TIER read falls back to the paying (stricter) defaults — fail closed.
"""
import json
from datetime import datetime, timezone

from core.agent_base import log_step

SERVICE_NAME = "client_settings"
KEY_PREFIX = "client_settings:"

# name -> type, per-tier defaults, bounds, admin label (Hebrew, admin UI only).
# "int_or_none": None means "no limit".
FIELDS = {
    "article_weekly_cap": {
        "type": "int_or_none", "paying": 2, "exempt": None, "min": 0, "max": 50,
        "label_he": "מכסת מאמרים שבועית (ריק = ללא הגבלה)"},
    "article_package_check": {
        "type": "bool", "paying": True, "exempt": False,
        "label_he": "בדיקת חבילה לפני טיוטה מהצ׳אט"},
    "article_block_repeat_topic": {
        "type": "bool", "paying": True, "exempt": False,
        "label_he": "חסימת נושא שנכתב לאחרונה (כבוי = רק הערה עם קישור)"},
    "auto_articles": {
        "type": "bool", "paying": True, "exempt": False,
        "label_he": "מאמרים אוטומטיים (מחזור SEO שבועי, כולל מחקר בתשלום)"},
    "draft_revision_cap": {
        "type": "int", "paying": 10, "exempt": 10, "min": 0, "max": 100,
        "label_he": "מקסימום תיקונים לטיוטה אחת מהצ׳אט"},
}


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _key(client_id: int) -> str:
    return f"{KEY_PREFIX}{int(client_id)}"


def is_exempt(client_id: int) -> bool:
    """clients.billing_exempt. Unknown -> False (paying tier = stricter)."""
    try:
        from agents.client_agent import get_client
        return bool((get_client(client_id) or {}).get("billing_exempt"))
    except Exception as e:
        print(f"[{SERVICE_NAME}] tier lookup failed for client {client_id}: {e}")
        return False


def _overrides(client_id: int) -> dict:
    try:
        rows = (_db().table("app_settings").select("value")
                .eq("key", _key(client_id)).limit(1).execute().data or [])
    except Exception as e:
        print(f"[{SERVICE_NAME}] read failed for client {client_id}: {e}")
        return {}
    if not rows:
        return {}
    value = rows[0].get("value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return {k: v for k, v in (value or {}).items() if k in FIELDS} if isinstance(value, dict) else {}


def defaults(exempt: bool) -> dict:
    tier = "exempt" if exempt else "paying"
    return {name: spec[tier] for name, spec in FIELDS.items()}


def get(client_id: int) -> dict:
    """Effective settings for this client: tier defaults + stored overrides."""
    exempt = is_exempt(client_id)
    return {**defaults(exempt), **_overrides(client_id)}


def view(client_id: int) -> dict:
    """The admin screen's shape: every field with its effective value, the
    tier default, and whether it is overridden."""
    exempt = is_exempt(client_id)
    base, over = defaults(exempt), _overrides(client_id)
    return {
        "billing_exempt": exempt,
        "fields": [{"name": name, "type": spec["type"], "label_he": spec["label_he"],
                    "value": over.get(name, base[name]), "default": base[name],
                    "overridden": name in over,
                    "min": spec.get("min"), "max": spec.get("max")}
                   for name, spec in FIELDS.items()],
    }


def _coerce(name: str, value):
    spec = FIELDS[name]
    if spec["type"] == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError(f"{name} must be true/false")
    if spec["type"] == "int_or_none" and (value is None or value == ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a number")
    number = int(value)
    if not (spec["min"] <= number <= spec["max"]):
        raise ValueError(f"{name} must be between {spec['min']} and {spec['max']}")
    return number


def update(client_id: int, changes: dict, reset: list = None) -> dict:
    """Admin write. Unknown names are rejected (not silently dropped) so a typo
    in the UI is visible; `reset` removes overrides back to the tier default."""
    over = _overrides(client_id)
    for name, value in (changes or {}).items():
        if name not in FIELDS:
            raise ValueError(f"unknown setting: {name}")
        over[name] = _coerce(name, value)
    for name in reset or []:
        over.pop(name, None)
    _db().table("app_settings").upsert(
        {"key": _key(client_id), "value": over,
         "updated_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="key").execute()
    log_step(SERVICE_NAME, "update", f"client {client_id}: {sorted(over)}")
    return view(client_id)
