"""What counts as a conversion for THIS client (2026-11-02).

A per-client choice, never one hardcoded default replacing another:
  form_submit    — a form sent on the site (the existing lead tracking, unchanged)
  whatsapp_click — a click on a WhatsApp link (wa.me, api.whatsapp.com, whatsapp://)
  phone_click    — a click on a phone link (tel:)
  store_sales    — orders in the client's online store, read from the store
                   itself (agents/store_agent), not from Tag Manager

Stored on clients.conversion_types (migrations/2026-11-02-conversion-types.sql)
with conversion_types_confirmed_at = when the client approved the choice on
its card. Existing clients default to form_submit. Before the migration runs
every read answers form_submit and nothing is "confirmed", so behaviour stays
exactly what it was.

A click is an INDICATION of an enquiry — someone pressed the button — not a
conversation that took place. Every screen that shows clicks says so.
"""
from datetime import datetime, timezone

TYPES = ("form_submit", "whatsapp_click", "phone_click", "store_sales")
DEFAULT = ("form_submit",)
GTM_TYPES = ("form_submit", "whatsapp_click", "phone_click")
LABELS_HE = {"form_submit": "פניות מטפסים", "whatsapp_click": "פניות בוואטסאפ",
             "phone_click": "שיחות טלפון", "store_sales": "מכירות"}
CLICK_NOTE_HE = "לחיצה היא סימן לפנייה — מישהו לחץ על הכפתור — ולא אישור שהשיחה התקיימה בפועל."

# How customers reach the business (the interview's words) -> the type
CHANNEL_TO_TYPE = {"form": "form_submit", "whatsapp": "whatsapp_click", "phone": "phone_click",
                   "store": "store_sales"}


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def clean(values) -> list:
    """Known types only, in TYPES order, no duplicates. Raises on none."""
    if isinstance(values, str):
        values = [v for v in values.replace(";", ",").split(",")]
    chosen = {str(v or "").strip().lower() for v in (values or [])}
    out = [t for t in TYPES if t in chosen]
    if not out:
        raise ValueError("no known conversion type")
    return out


def get(client_id: int) -> dict:
    """{"types": [...], "confirmed": bool}. Never raises."""
    try:
        rows = (_db().table("clients").select("conversion_types,conversion_types_confirmed_at")
                .eq("id", client_id).limit(1).execute().data or [])
    except Exception:
        return {"types": list(DEFAULT), "confirmed": False, "stored": False}
    row = rows[0] if rows else {}
    try:
        types = clean(row.get("conversion_types") or DEFAULT)
    except ValueError:
        types = list(DEFAULT)
    return {"types": types, "confirmed": bool(row.get("conversion_types_confirmed_at")), "stored": True}


def types_for(client_id: int) -> list:
    return get(client_id)["types"]


def set_types(client_id: int, values) -> dict:
    """The client's approved choice (the set_conversion_types card). Only
    THIS client's row; raises ValueError on an empty/unknown choice and
    RuntimeError before the migration has run."""
    types = clean(values)
    try:
        _db().table("clients").update({
            "conversion_types": types,
            "conversion_types_confirmed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", int(client_id)).execute()
    except Exception as e:
        raise RuntimeError(f"conversion_types not stored ({type(e).__name__})") from None
    try:
        from core import client_state
        client_state.forget(int(client_id))
    except Exception:
        pass
    return {"types": types, "labels": [LABELS_HE[t] for t in types]}


def from_channels(channels) -> list:
    """The interview's "how do customers reach you" → proposed types."""
    out = []
    for c in channels or []:
        t = CHANNEL_TO_TYPE.get(str(c or "").strip().lower())
        if t and t not in out:
            out.append(t)
    return [t for t in TYPES if t in out]


def labels(types) -> list:
    return [LABELS_HE.get(t, t) for t in types or []]
