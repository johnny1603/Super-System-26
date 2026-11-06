"""What counts as a conversion for THIS client (2026-11-02).

A per-client choice, never one hardcoded default replacing another:
  form_submit    — a form sent on the site (the existing lead tracking, unchanged)
  whatsapp_click — a click on a WhatsApp link (wa.me, api.whatsapp.com, whatsapp://)
  phone_click    — a click on a phone link (tel:)
  store_sales    — orders in the client's online store. HOW MANY and HOW MUCH
                   are read from the store itself (agents/store_agent, one sale
                   definition in woocommerce_service); WHERE an order came from
                   is one purchase event on the order-received page, published in
                   the client's Tag Manager (gtm_service PURCHASE, 2026-11-07)

Stored on clients.conversion_types (migrations/2026-11-02-conversion-types.sql)
with conversion_types_confirmed_at = when the client approved the choice on
its card. Existing clients default to form_submit. Before the migration runs
every read answers form_submit and nothing is "confirmed", so behaviour stays
exactly what it was.

A click is an INDICATION of an enquiry — someone pressed the button — not a
conversation that took place. Every screen that shows clicks says so.
"""
import re
from datetime import datetime, timezone

TYPES = ("form_submit", "whatsapp_click", "phone_click", "store_sales")
DEFAULT = ("form_submit",)
# Lead types: a form / a WhatsApp click / a phone click. NEVER set up for a
# client that did not choose them — a store_sales-only client (Shin Sekai, 27)
# gets no lead trigger from any path (tests/test_store_purchase.py).
LEAD_TYPES = ("form_submit", "whatsapp_click", "phone_click")
# Measured in the client's Tag Manager. store_sales (2026-11-07) = ONE purchase
# event on the store's order-received page — to know where an order CAME FROM.
# The sale itself (how many, how much) is still read from the store.
GTM_TYPES = LEAD_TYPES + ("store_sales",)
LABELS_HE = {"form_submit": "פניות מטפסים", "whatsapp_click": "פניות בוואטסאפ",
             "phone_click": "שיחות טלפון", "store_sales": "מכירות"}
# What the Tag Manager card says it adds, per type
GTM_LABELS_HE = {**LABELS_HE, "store_sales": "רכישות באתר (מאיזה מקור הגיעה כל הזמנה)"}
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


def gtm_types_for(client_id: int) -> list:
    """The client's types that Tag Manager measures — for SETTING UP tags.
    An unreadable choice is NOT the form_submit default here: a failed read
    must never put a lead trigger into a store-only client's container, so it
    answers [] (nothing is set up) instead."""
    choice = get(client_id)
    if not choice.get("stored", True):
        return []
    return [t for t in choice["types"] if t in GTM_TYPES]


# The WooCommerce "order received" endpoint (its default slug) — where the
# store_sales purchase event listens (core/gtm_service PURCHASE)
DEFAULT_ORDER_RECEIVED_ENDPOINT = "order-received"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def clean_endpoint(slug) -> str:
    """A store's order-received endpoint slug, or ValueError. It is written
    into Tag Manager JavaScript, so only [a-z0-9-] passes — store data is data."""
    slug = str(slug or "").strip().strip("/").lower()
    if not _SLUG.match(slug):
        raise ValueError("unusable order-received endpoint")
    return slug


def gtm_labels(types) -> list:
    return [GTM_LABELS_HE.get(t, t) for t in types or []]


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
