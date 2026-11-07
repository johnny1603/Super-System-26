"""Our OWN purchase records (2026-11-07, docs/STORE.md "Without any store login").

The default way אורי and the manager answer "how many orders / how much"
for a client who sells online, with ZERO work from the client and no store
credentials: the purchase measurement we already publish in the client's
Tag Manager (core/gtm_service PURCHASE, conversion type store_sales) also
fires a small record tag (PURCHASE_BEACON) that tells US the order id, total
and currency — the same three values it sends to the client's GA4.

What is stored (table store_purchase_events, one row per client + order):
  client_id, order_ref (the store's order NUMBER, digits only), value,
  currency, created_at. NOTHING about the buyer: the tag never reads a name,
  e-mail, phone or address, and the endpoint drops any other field.

What it means — said this way to the client, never more:
- "orders placed, as our purchase measurement counted them". The thank-you
  page also shows for an order still awaiting payment, and a buyer with
  blocked scripts is not counted, so this is NOT the sale definition
  (woocommerce_service.SALE_STATUSES = processing + completed). With the
  store connected (the optional one-click connection), the store's own
  numbers are the sales; these stay the measurement.
- Once per order: the tag fires once per browser (first_seen) and the table
  is unique on (client_id, order_ref), so a refresh never counts twice.

Abuse: the token is public (it is in the site's container). It can only add
rows for this client, at most MAX_PER_DAY a day and RATE_PER_MINUTE per
address; a duplicate order number is ignored. Logged: client id + counts only.
"""
import re
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "store_purchases"
TABLE = "store_purchase_events"
MAX_BODY_BYTES = 1024
MAX_PER_DAY = 1000            # per client — a busy small store is far below this
RATE_PER_MINUTE = 30          # per sender address
MAX_VALUE = 10_000_000
SOURCE = "purchase_measurement"

_ORDER = re.compile(r"^\d{1,20}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")

MEANING_HE = ("הזמנות שבוצעו לפי מדידת הרכישות באתר (עמוד התודה אחרי הזמנה). "
              "זו מדידה, לא דוח המכירות של החנות: היא כוללת גם הזמנות שעדיין ממתינות לתשלום.")


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _log(message: str):
    try:
        from core.agent_base import log_step
        log_step(SERVICE_NAME, "record", message)
    except Exception:
        print(f"[{SERVICE_NAME}] {message}")


# ─── The record (public endpoint) ────────────────────────────────────────────

def clean(payload) -> dict:
    """{"order_ref", "value", "currency"} from the tag's body, or {} when the
    order number isn't usable. Every other field is dropped unread."""
    if not isinstance(payload, dict):
        return {}
    order = str(payload.get("o") if payload.get("o") is not None else "").strip().lstrip("#")
    if not _ORDER.match(order):
        return {}
    value = payload.get("v")
    try:
        value = round(float(value), 2) if value is not None and value != "" else None
    except (TypeError, ValueError):
        value = None
    if value is not None and not (0 <= value <= MAX_VALUE):
        value = None
    currency = str(payload.get("c") or "").strip().upper()
    return {"order_ref": order, "value": value, "currency": currency if _CURRENCY.match(currency) else None}


_rate = {}
_rate_lock = threading.Lock()


def rate_ok(sender: str, now: float = None) -> bool:
    now = time.time() if now is None else now
    key = (sender or "?")[:64]
    with _rate_lock:
        hits = [t for t in _rate.get(key, []) if t > now - 60]
        if len(hits) >= RATE_PER_MINUTE:
            _rate[key] = hits
            return False
        hits.append(now)
        _rate[key] = hits
        if len(_rate) > 5000:              # never grows without bound
            for k in list(_rate)[:2500]:
                _rate.pop(k, None)
    return True


def _today_count(client_id: int) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = (_db().table(TABLE).select("id", count="exact").eq("client_id", client_id)
              .gte("created_at", start.isoformat()).limit(1).execute())
    return int(getattr(result, "count", None) or len(result.data or []))


def record(client_id: int, payload) -> bool:
    """Store one order. False (never raises) when unusable, over the daily
    cap, a duplicate, or the table isn't there yet (migration not run)."""
    row = clean(payload)
    if not row:
        return False
    try:
        if _today_count(client_id) >= MAX_PER_DAY:
            _log(f"client {client_id}: daily cap reached, record dropped")
            return False
        _db().table(TABLE).insert({"client_id": int(client_id), **row,
                                   "created_at": datetime.now(timezone.utc).isoformat()}).execute()
    except Exception as e:
        text = str(e).lower()
        if "duplicate" in text or "23505" in text or "unique" in text:
            return False                    # this order was already counted
        _log(f"client {client_id}: record not stored ({type(e).__name__})")
        return False
    forget(client_id)
    return True


# ─── Reads (aggregates only) ─────────────────────────────────────────────────

_cache = {}         # (client_id, period, day) -> (time, summary)
CACHE_TTL = 300


def forget(client_id: int):
    for key in [k for k in list(_cache) if k[0] == client_id]:
        _cache.pop(key, None)


def _rows(client_id: int, start: datetime, end: datetime) -> list:
    rows, offset = [], 0
    while True:
        chunk = (_db().table(TABLE).select("order_ref,value,currency,created_at")
                 .eq("client_id", client_id)
                 .gte("created_at", start.astimezone(timezone.utc).isoformat())
                 .lt("created_at", end.astimezone(timezone.utc).isoformat())
                 .order("created_at").range(offset, offset + 999).execute().data or [])
        rows += chunk
        if len(chunk) < 1000 or len(rows) >= 20000:
            return rows
        offset += 1000


def summarize(rows: list) -> dict:
    """Pure: orders, revenue (orders that carried a total), per currency."""
    by_currency = Counter()
    with_value = 0
    for r in rows:
        if r.get("value") is not None:
            with_value += 1
            by_currency[r.get("currency") or ""] += float(r["value"])
    currency = by_currency.most_common(1)[0][0] if by_currency else ""
    return {"orders": len(rows), "orders_with_total": with_value,
            "revenue": round(by_currency.get(currency, 0.0), 2) if by_currency else 0.0,
            "currency": currency or None,
            "other_currencies": {c: round(v, 2) for c, v in by_currency.items() if c != currency} or None}


def summary(client_id: int, period: str = "last_30_days", now: datetime = None) -> dict:
    """{"available", "period", ...summarize} for one period (Israel time, the
    store's own period words). available False = no table / unreadable."""
    from agents.store_agent import PERIOD_HE, period_bounds
    start, end = period_bounds(period, now)
    key = (int(client_id), period, start.date().isoformat())
    hit = _cache.get(key)
    if hit and hit[0] > time.time() - CACHE_TTL:
        return hit[1]
    # A period that ends "now" (today, this month…) includes a record stamped
    # this very instant — the end bound is exclusive
    query_end = end + timedelta(minutes=1) if abs((datetime.now(timezone.utc) - end).total_seconds()) < 5 else end
    try:
        rows = _rows(client_id, start, query_end)
    except Exception as e:
        _log(f"client {client_id}: read failed ({type(e).__name__})")
        return {"available": False}
    out = {"available": True, "source": SOURCE, "period": period, "period_he": PERIOD_HE.get(period, period),
           "from": start.date().isoformat(), "to": (end - timedelta(seconds=1)).date().isoformat(),
           **summarize(rows)}
    _cache[key] = (time.time(), out)
    return out


def has_records(client_id: int) -> bool:
    """Any record in the last 90 days — the measurement is working."""
    try:
        since = datetime.now(timezone.utc) - timedelta(days=90)
        rows = (_db().table(TABLE).select("id").eq("client_id", client_id)
                .gte("created_at", since.isoformat()).limit(1).execute().data or [])
        return bool(rows)
    except Exception:
        return False


def for_agent(client_id: int, period: str = "last_30_days") -> dict:
    """What the prompts get: the asked period + the last 30 days, with the
    meaning in words. {"available": False} when nothing was ever recorded."""
    if not has_records(client_id):
        return {"available": False, "why": "no_records"}
    asked = summary(client_id, period)
    if not asked.get("available"):
        return {"available": False, "why": "unreadable"}
    out = {"available": True, "source": SOURCE, "meaning_he": MEANING_HE, "asked_period": asked}
    if period != "last_30_days":
        out["last_30_days"] = summary(client_id, "last_30_days")
    return out


def windows(client_id: int) -> list:
    """The Performance page's 7 / 30 / 90-day rows (orders + revenue)."""
    out = []
    for days, period in ((7, "last_7_days"), (30, "last_30_days"), (90, "last_90_days")):
        s = summary(client_id, period)
        if not s.get("available"):
            return []
        out.append({"days": days, "orders": s["orders"], "revenue": s["revenue"], "currency": s["currency"],
                    "orders_with_total": s["orders_with_total"]})
    return out
