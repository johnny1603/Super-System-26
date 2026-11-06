"""The client's online store (WooCommerce), READ-ONLY (2026-11-02, docs/STORE.md).

What אורי (the website specialist) and the manager answer orders and sales
questions from. Every number comes from a live read of the client's own store
through core/woocommerce_service (GET only) — never a guess, never "go look
in your site's admin".

Connection (client_accounts, platform "woocommerce", encrypted like every
client secret by agents.client_agent.upsert_account):
- method "key" (RECOMMENDED): account_id = the store address, the secret =
  "<consumer key>:<consumer secret>" of a REST key with Permissions "Read".
- method "site": the existing website connection's application password is
  used (an administrator's password — more access than reading needs, so the
  chat recommends the key). Nothing new is stored for it: the row's secret is
  the marker SITE_MARKER and the password stays in the website row only.
Both are verified with a live read BEFORE anything is stored.

Personal data — the rules (tests/test_store_reads.py proves them):
- Lists, summaries, status counts and refunds never fetch a customer's name,
  address, phone or e-mail (named `_fields` without billing/shipping).
- One order's customer details are fetched only when the client explicitly
  asks for them, for that one order, and answered by CODE (personal_answer) —
  they never enter a model prompt. The copy stored in the chat history is
  masked; the client sees the full line once, in their own session.
- Cached: aggregates only (sales_summary). Logged: ids, counts, status codes.
"""
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from core import woocommerce_service as wc
from core.agent_base import log_step

AGENT_NAME = "store_agent"
PLATFORM = "woocommerce"
SITE_MARKER = "use-website-connection"
SUMMARY_TTL = 600            # seconds an aggregate stays cached
RECENT_LIMIT = 15

PERIODS = ("today", "yesterday", "this_week", "last_7_days", "this_month",
           "last_month", "last_30_days", "last_90_days")
PERIOD_HE = {"today": "היום", "yesterday": "אתמול", "this_week": "השבוע",
             "last_7_days": "7 הימים האחרונים", "this_month": "החודש",
             "last_month": "החודש שעבר", "last_30_days": "30 הימים האחרונים",
             "last_90_days": "90 הימים האחרונים"}
STATUS_HE = {"pending": "ממתינה לתשלום", "processing": "בטיפול", "on-hold": "בהמתנה",
             "completed": "הושלמה", "cancelled": "בוטלה", "refunded": "הוחזרה",
             "failed": "נכשלה", "checkout-draft": "טיוטה", "trash": "בפח"}

ERRORS_HE = {
    "store_auth": "החנות לא אישרה את המפתח — כדאי לוודא שהעתקתם את שני הערכים במלואם (Consumer key ו-Consumer secret), ושההרשאה היא Read.",
    "store_not_woocommerce": "לא מצאנו חנות WooCommerce פעילה בכתובת הזו — כדאי לוודא שהכתובת נכונה ושהתוסף של החנות פעיל.",
    "store_needs_https": "כתובת החנות צריכה להתחיל ב-https — בלי חיבור מאובטח לא נשלח את המפתח.",
    "store_busy": "החנות ביקשה מאיתנו להאט רגע — ננסה שוב בעוד דקה.",
    "store_not_found": "לא מצאנו הזמנה כזו בחנות.",
    "store_error": "לא הצלחנו לקרוא את החנות כרגע — ננסה שוב בעוד רגע.",
    "bad_key": "המפתח לא נראה שלם: Consumer key מתחיל ב-ck_ ו-Consumer secret מתחיל ב-cs_.",
    "no_site": "חסרה כתובת החנות.",
    "no_website": "כדי להשתמש בחיבור האתר הקיים, האתר צריך להיות מחובר קודם.",
}

_KEY = re.compile(r"^ck_[A-Za-z0-9]{20,64}$")
_SECRET = re.compile(r"^cs_[A-Za-z0-9]{20,64}$")


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


# ─── Connection ──────────────────────────────────────────────────────────────

def _row(client_id: int) -> dict:
    from core.secrets_vault import hydrate
    result = (_db().table("client_accounts").select("*")
              .eq("client_id", client_id).eq("platform", PLATFORM).eq("status", "active")
              .order("id", desc=True).limit(1).execute())
    return hydrate(result.data[0]) if result.data else {}


def _website_auth(client_id: int) -> tuple:
    from agents import website_agent
    site, user, password = website_agent._creds(website_agent._get_connection(client_id))
    return site, user, password


def credentials(client_id: int) -> dict:
    """{"site", "auth", "method"} for THIS client only, or {} when the store
    isn't connected. The secret never leaves this module's callers."""
    row = _row(client_id)
    if not row or int(row.get("client_id") or client_id) != int(client_id):
        return {}
    token = row.get("access_token") or ""
    if token == SITE_MARKER:
        site, user, password = _website_auth(client_id)
        if not (site and user and password):
            return {}
        return {"site": row.get("account_id") or site, "auth": (user, password), "method": "site"}
    key, _, secret = token.partition(":")
    if not (key and secret and row.get("account_id")):
        return {}
    return {"site": row["account_id"], "auth": (key, secret), "method": "key"}


def is_connected(client_id: int) -> bool:
    try:
        return bool(credentials(client_id))
    except Exception:
        return False


def status(client_id: int) -> dict:
    """For screens, prompts and client_state: never a secret."""
    try:
        creds = credentials(client_id)
    except Exception as e:
        log_step(AGENT_NAME, "status", f"client {client_id}: unreadable ({type(e).__name__})")
        return {"connected": False, "readable": False}
    if not creds:
        return {"connected": False}
    return {"connected": True, "method": creds["method"],
            "site": creds["site"].replace("https://", ""),
            "recommend_key": creds["method"] == "site"}


def _error(code: str) -> dict:
    return {"success": False, "code": code, "error": ERRORS_HE.get(code, ERRORS_HE["store_error"])}


def _forget(client_id: int):
    for key in [k for k in _summary_cache if k[0] == client_id]:
        _summary_cache.pop(key, None)
    try:
        from core import client_state
        client_state.forget(client_id)
    except Exception:
        pass


def connect(client_id: int, consumer_key: str, consumer_secret: str, site_url: str = "") -> dict:
    """The recommended path: a READ key. Verified live, then stored encrypted."""
    from agents.client_agent import upsert_account
    consumer_key, consumer_secret = (consumer_key or "").strip(), (consumer_secret or "").strip()
    if not (_KEY.match(consumer_key) and _SECRET.match(consumer_secret)):
        return _error("bad_key")
    site_url = (site_url or "").strip() or _website_auth(client_id)[0]
    if not site_url:
        return _error("no_site")
    try:
        site = wc.normalize_site(site_url)
        wc.verify(site, (consumer_key, consumer_secret))
    except wc.StoreError as e:
        log_step(AGENT_NAME, "connect", f"client {client_id}: key rejected ({e.code}, {e.status})")
        return _error(e.code)
    upsert_account(client_id, PLATFORM, site, f"{consumer_key}:{consumer_secret}", "active")
    _forget(client_id)
    log_step(AGENT_NAME, "connect", f"client {client_id}: store connected with a read key")
    return {"success": True, "method": "key", "site": site.replace("https://", "")}


def connect_with_site(client_id: int) -> dict:
    """The fallback: the website connection's application password, if it
    can read /wc/v3 on THIS site (checked live — unverified in general)."""
    from agents.client_agent import upsert_account
    site, user, password = _website_auth(client_id)
    if not (site and user and password):
        return _error("no_website")
    try:
        site = wc.normalize_site(site)
        wc.verify(site, (user, password))
    except wc.StoreError as e:
        log_step(AGENT_NAME, "connect_with_site", f"client {client_id}: site password can't read the store ({e.code})")
        return _error(e.code)
    upsert_account(client_id, PLATFORM, site, SITE_MARKER, "active")
    _forget(client_id)
    log_step(AGENT_NAME, "connect_with_site", f"client {client_id}: store connected through the website connection")
    return {"success": True, "method": "site", "site": site.replace("https://", "")}


def disconnect(client_id: int) -> dict:
    from agents.client_agent import remove_accounts
    removed = remove_accounts(client_id, [PLATFORM])
    _forget(client_id)
    return {"success": True, "removed": removed}


# ─── Periods (Israel time) ───────────────────────────────────────────────────

def period_bounds(period: str, now: datetime = None) -> tuple:
    """(start, end) as aware datetimes in Israel time, end exclusive (the
    shared helper, with its DST fallback when tz data is missing)."""
    from core.chat_rollover import israel_now
    now = israel_now(now)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return midnight, now
    if period == "yesterday":
        return midnight - timedelta(days=1), midnight
    if period == "this_week":
        # The Israeli week starts on Sunday
        return midnight - timedelta(days=(now.weekday() + 1) % 7), now
    if period == "this_month":
        return midnight.replace(day=1), now
    if period == "last_month":
        first = midnight.replace(day=1)
        return (first - timedelta(days=1)).replace(day=1), first
    days = {"last_7_days": 7, "last_30_days": 30, "last_90_days": 90}.get(period)
    if not days:
        raise ValueError("unknown period")
    return midnight - timedelta(days=days - 1), now


_PERIOD_WORDS = (
    ("yesterday", ("אתמול", "yesterday")),
    ("today", ("היום", "today")),
    # Longer phrases first: "החודש שעבר" and "החודש האחרון" both contain "החודש"
    ("last_month", ("חודש שעבר", "החודש הקודם", "בחודש הקודם", "last month")),
    ("last_90_days", ("3 חודשים", "שלושה חודשים", "רבעון", "90 יום", "90 ימים", "90 days")),
    ("last_30_days", ("30 יום", "30 ימים", "חודש האחרון", "30 days")),
    ("last_7_days", ("7 ימים", "שבעה ימים", "שבוע האחרון", "last week", "7 days")),
    ("this_month", ("החודש", "this month")),
    ("this_week", ("השבוע", "this week")),
)


def detect_period(message: str, default: str = "last_30_days") -> str:
    text = (message or "").lower()
    for period, words in _PERIOD_WORDS:
        if any(w in text for w in words):
            return period
    return default


def _gmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _orders_in(client_id: int, creds: dict, period: str, now: datetime = None, fields: str = None) -> dict:
    start, end = period_bounds(period, now)
    return wc.get_all(creds["site"], wc.API_PREFIX + "orders", creds["auth"], {
        "after": _gmt(start), "before": _gmt(end), "dates_are_gmt": "true",
        "orderby": "date", "order": "desc", "_fields": fields or wc.LIST_FIELDS})


# ─── Reads ───────────────────────────────────────────────────────────────────

_summary_cache = {}   # (client_id, period, day) -> (time, aggregates) — aggregates ONLY
_cache_lock = threading.Lock()


def sales_summary(client_id: int, period: str = "last_30_days", now: datetime = None) -> dict:
    """Revenue, orders, average order value, top products and status counts
    for a period. Cached (aggregates only) for SUMMARY_TTL."""
    creds = credentials(client_id)
    if not creds:
        return {"connected": False}
    start, end = period_bounds(period, now)
    key = (int(client_id), period, start.date().isoformat())
    with _cache_lock:
        hit = _summary_cache.get(key)
    if hit and hit[0] > time.time() - SUMMARY_TTL:
        return hit[1]
    listing = _orders_in(client_id, creds, period, now)
    summary = {"connected": True, "period": period, "period_he": PERIOD_HE.get(period, period),
               "from": start.date().isoformat(), "to": (end - timedelta(seconds=1)).date().isoformat(),
               **wc.summarize(listing["items"]), "complete": not listing["truncated"],
               "as_of": datetime.now(timezone.utc).isoformat()}
    with _cache_lock:
        _summary_cache[key] = (time.time(), summary)
    log_step(AGENT_NAME, "sales_summary", f"client {client_id}: {period}, {summary['orders']} orders")
    return summary


ORDER_RECEIVED_SETTING = "settings/advanced/woocommerce_checkout_order_received_endpoint"


def order_received_endpoint(client_id: int) -> dict:
    """The store's own "order received" endpoint slug — where the purchase
    event (gtm_service.PURCHASE) listens. One GET of the store's setting.
    {"slug", "source": "store"|"default", "problem": ""|"endpoint_empty"|
    "endpoint_unusable"}. Not connected or unreadable → WooCommerce's default
    ("source": "default"); a value we can't use safely → a problem, never a
    guess. The value is DATA: only [a-z0-9-] ever reaches Tag Manager."""
    from core import conversion_types
    default = {"slug": conversion_types.DEFAULT_ORDER_RECEIVED_ENDPOINT, "source": "default", "problem": ""}
    try:
        creds = credentials(client_id)
    except Exception:
        return default
    if not creds:
        return default
    try:
        body, _ = wc.get(creds["site"], wc.API_PREFIX + ORDER_RECEIVED_SETTING, creds["auth"])
    except wc.StoreError as e:
        log_step(AGENT_NAME, "order_received_endpoint", f"client {client_id}: setting unreadable ({e.code})")
        return default
    value = str((body or {}).get("value") if isinstance(body, dict) else "").strip()
    if not value:
        return {"slug": "", "source": "store", "problem": "endpoint_empty"}
    try:
        return {"slug": conversion_types.clean_endpoint(value), "source": "store", "problem": ""}
    except ValueError:
        return {"slug": "", "source": "store", "problem": "endpoint_unusable"}


def recent_orders(client_id: int, period: str = "last_7_days", limit: int = RECENT_LIMIT,
                  now: datetime = None) -> dict:
    """The newest orders of a period — number, status, date, total, items.
    No customer data. Never cached."""
    creds = credentials(client_id)
    if not creds:
        return {"connected": False}
    listing = _orders_in(client_id, creds, period, now)
    orders = [wc.clean_order(o) for o in listing["items"]]
    for o in orders:
        o["status_he"] = STATUS_HE.get(o["status"], o["status"])
        o.pop("date_gmt", None)
    return {"connected": True, "period": period, "period_he": PERIOD_HE.get(period, period),
            "count": len(orders), "orders": orders[:max(1, min(int(limit), 50))],
            "complete": not listing["truncated"]}


def refunds(client_id: int, period: str = "last_30_days", now: datetime = None) -> dict:
    """Refunds on orders created in the period (amount, reason, order number)."""
    creds = credentials(client_id)
    if not creds:
        return {"connected": False}
    listing = _orders_in(client_id, creds, period, now)
    rows = []
    for order in listing["items"]:
        for r in wc.refunds_of(order):
            rows.append({"order": str(order.get("number") or order.get("id")), "amount": r["total"],
                         "reason": r["reason"], "order_status": order.get("status")})
    return {"connected": True, "period": period, "period_he": PERIOD_HE.get(period, period),
            "count": len(rows), "total": round(sum(r["amount"] for r in rows), 2),
            "refunds": rows[:30], "complete": not listing["truncated"]}


def _find_order(creds: dict, number: str, fields: str) -> dict:
    number = str(number or "").strip().lstrip("#")
    if not number.isdigit():
        raise wc.StoreNotFound("not an order number")
    try:
        body, _ = wc.get(creds["site"], f"{wc.API_PREFIX}orders/{int(number)}", creds["auth"],
                         {"_fields": fields})
        if isinstance(body, dict) and str(body.get("number") or body.get("id")) == number:
            return body
    except wc.StoreNotFound:
        pass
    # Stores with custom order numbers: the number differs from the id
    body, _ = wc.get(creds["site"], wc.API_PREFIX + "orders", creds["auth"],
                     {"search": number, "per_page": 20, "_fields": fields})
    for order in body if isinstance(body, list) else []:
        if str(order.get("number")) == number:
            return order
    raise wc.StoreNotFound("no such order")


def order_detail(client_id: int, number: str, personal: bool = False) -> dict:
    """One order. personal=True only for the client's explicit ask
    (personal_answer); the default carries no customer data."""
    creds = credentials(client_id)
    if not creds:
        return {"connected": False}
    order = _find_order(creds, number, wc.PERSONAL_FIELDS if personal else wc.DETAIL_FIELDS)
    detail = wc.clean_order(order, personal=personal)
    detail["status_he"] = STATUS_HE.get(detail["status"], detail["status"])
    detail.pop("date_gmt", None)
    return {"connected": True, "order": detail}


# ─── The chat ────────────────────────────────────────────────────────────────

_STORE_WORDS = ("הזמנ", "מכירות", "מכרנו", "מכרתי", "נמכר", "הכנסות", "פדיון", "החזר", "זיכוי", "חנות",
                "מוצר", "קניות", "רכישות", "order", "sale", "revenue", "refund", "store", "product")
_REFUND_WORDS = ("החזר", "זיכוי", "refund")
_ORDER_NO = re.compile(r"(?:#|הזמנה\s*(?:מספר|מס['׳]?)?\s*#?|order\s*#?)\s*(\d{1,10})", re.IGNORECASE)
_PERSONAL_WORDS = ("כתובת", "משלוח", "טלפון", "מייל", "אימייל", "שם הלקוח", "שם של הלקוח", "פרטי הלקוח",
                   "פרטי לקוח", "מי הזמין", "לאן לשלוח", "address", "phone", "email", "customer name",
                   "shipping details", "who ordered")


def is_store_question(message: str) -> bool:
    text = (message or "").lower()
    return any(w in text for w in _STORE_WORDS)


def order_number_in(message: str) -> str:
    match = _ORDER_NO.search(message or "")
    return match.group(1) if match else ""


def asks_personal(message: str) -> bool:
    text = (message or "").lower()
    return bool(order_number_in(message)) and any(w in text for w in _PERSONAL_WORDS)


def store_reads(client_id: int, message: str = "") -> dict:
    """What the website persona's prompt gets for THIS message. No customer
    data in any branch. Not connected → {"connected": False} (the prompt then
    offers connect_store). A failed read → {"error": code} (the fixed
    can't-read line, decided in code)."""
    view = status(client_id)
    if not view.get("connected"):
        return {"connected": False}
    data = {"connected": True, "method": view["method"], "sale_definition_he": wc.SALE_DEFINITION_HE}
    if not is_store_question(message):
        # Nothing about the store was asked: no read at all
        return data
    period = detect_period(message)
    data["asked_period"] = period
    try:
        data["sales_summary"] = sales_summary(client_id, period)
        if any(w in (message or "").lower() for w in _REFUND_WORDS):
            data["refunds"] = refunds(client_id, period)
        number = order_number_in(message)
        if number:
            try:
                data["order"] = order_detail(client_id, number)["order"]
            except wc.StoreNotFound:
                data["order"] = {"number": number, "found": False}
        else:
            data["recent_orders"] = recent_orders(client_id, period)
    except wc.StoreError as e:
        log_step(AGENT_NAME, "store_reads", f"client {client_id}: read failed ({e.code})")
        return {"connected": True, "error": e.code}
    return data


PII_MASK = "•••"


def personal_answer(client_id: int, message: str) -> dict:
    """The client asked for ONE order's customer details. Answered by code,
    never by a model. Returns {} when this isn't such a question.
    {"reply": what the client sees now, "stored_reply": the masked copy for
    the chat history}."""
    if not asks_personal(message):
        return {}
    number = order_number_in(message)
    if not is_connected(client_id):
        return {}
    try:
        order = order_detail(client_id, number, personal=True)["order"]
    except wc.StoreNotFound:
        line = f"לא מצאתי בחנות הזמנה מספר {number}. כדאי לבדוק את המספר ולשלוח שוב."
        return {"reply": line, "stored_reply": line}
    except wc.StoreError as e:
        line = ERRORS_HE.get(e.code, ERRORS_HE["store_error"])
        return {"reply": line, "stored_reply": line}
    customer = order.get("customer") or {}
    parts = [("שם", customer.get("name")), ("כתובת למשלוח", customer.get("address")),
             ("טלפון", customer.get("phone")), ("מייל", customer.get("email")),
             ("הערת הלקוח", customer.get("note"))]
    shown = [(label, value) for label, value in parts if value]
    head = f"הזמנה {order['number']} ({order.get('status_he') or order['status']}, {order['total']} {order['currency']})"
    if order.get("shipping_method"):
        head += f" · משלוח: {order['shipping_method']}"
    if not shown:
        line = head + "\nאין בהזמנה הזו פרטי לקוח."
        return {"reply": line, "stored_reply": line}
    reply = head + "\n" + "\n".join(f"{label}: {value}" for label, value in shown)
    stored = (head + "\n" + "\n".join(f"{label}: {PII_MASK}" for label, _ in shown)
              + "\n(פרטי הלקוח הוצגו לכם פעם אחת ולא נשמרו אצלנו. אפשר לבקש אותם שוב בכל רגע.)")
    log_step(AGENT_NAME, "personal_answer", f"client {client_id}: customer details shown for one order")
    return {"reply": reply, "stored_reply": stored}


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?972[-\s]?|0)(?:[23489]|5\d|7\d)[-\s]?\d{3}[-\s]?\d{4}(?!\d)")


def mask(text: str) -> str:
    """For any log line or trace that might quote a store answer."""
    return _PHONE.sub(PII_MASK, _EMAIL.sub(PII_MASK, str(text or "")))


def section(client_id: int) -> dict:
    """client_state's "store" section: status + the 30-day aggregates."""
    view = status(client_id)
    if not view.get("connected"):
        return view
    summary = sales_summary(client_id, "last_30_days")
    keep = ("period", "orders", "paid_orders", "gross_revenue", "refunded", "net_revenue",
            "average_order_value", "currency", "status_counts", "top_products", "awaiting_payment",
            "not_sales", "complete", "as_of")
    return {**view, "last_30_days": {k: summary.get(k) for k in keep},
            "sale_definition_he": wc.SALE_DEFINITION_HE}
