"""WooCommerce REST — READ-ONLY (2026-11-02).

GET is the only HTTP verb this module has. There is no post/put/delete helper
here and nothing in the codebase may add one for /wc/ paths
(tests/test_store_reads.py scans for it). Writing to a client's store is out of
scope by decision, not by accident.

Auth, checked in WooCommerce's own docs (developer.woocommerce.com/docs/apis/
rest-api/, woocommerce.com/document/woocommerce-rest-api/):
- PREFERRED: a dedicated REST key the client creates under
  WooCommerce > Settings > Advanced > REST API > Add Key, with Permissions
  "Read". Sent as HTTP Basic auth over HTTPS (user = consumer key,
  password = consumer secret). Least privilege: it can read the store and
  nothing else, and the client can revoke it on that same screen.
- FALLBACK: the site's existing WordPress application password (the website
  connection). WordPress core authenticates application passwords for every
  REST route, so an administrator's password passes WooCommerce's capability
  checks. NOT verified against a real store from here (no store credentials on
  this machine) — connect_with_site() proves it per site with a live GET before
  anything is stored, and the chat recommends the read-only key instead.

Personal data (docs/STORE.md): customer names, addresses, phones and e-mails
are fetched only when the client asks for one specific order's customer
details. Every list/summary call names its fields (`_fields`) so the store
never even sends billing/shipping objects. Nothing read here is logged: log
lines carry the path, the status and counts — never a body or a query string.
"""
import base64
import threading
import time
from collections import deque

import httpx

SERVICE_NAME = "woocommerce"
API_PREFIX = "wc/v3/"
TIMEOUT = 20
PER_PAGE = 100            # WooCommerce's maximum per_page
MAX_PAGES = 20            # one question reads at most 2,000 orders
RATE_PER_MINUTE = 30      # per store, across every caller in this instance
RATE_WINDOW = 60.0

# What a list / summary call asks for: no billing, no shipping, no customer
# note, no meta_data. line_items are cleaned again in clean_order().
LIST_FIELDS = ("id,number,status,currency,date_created,date_created_gmt,total,"
               "total_tax,shipping_total,discount_total,line_items,refunds")
# One order, no personal data (the default for an order question)
DETAIL_FIELDS = LIST_FIELDS + ",payment_method_title,shipping_lines"
# One order WITH the customer's details — only on the client's explicit ask
PERSONAL_FIELDS = DETAIL_FIELDS + ",billing,shipping,customer_note"

# ─── THE definition of a sale (2026-11-07, docs/STORE.md "What counts as a
# sale"). Every reader — the Performance page, client_state, the manager and
# the website agent's chat — goes through summarize(), so there is one rule:
#   SALE      processing (paid, being handled) + completed (paid, fulfilled).
#   AWAITING  on-hold (WooCommerce: "awaiting payment confirmation", e.g. a
#             bank transfer) + pending (created, not paid). NOT a sale until
#             the store moves it to processing/completed; shown separately.
#   NOT SALES cancelled, failed, refunded (a full refund) — never in revenue,
#             shown separately (count, and the refunded amount).
#   IGNORED   checkout-draft, trash — not orders a customer completed.
# A partial refund on a sale stays a sale; its amount is subtracted for net.
SALE_STATUSES = ("processing", "completed")
AWAITING_PAYMENT_STATUSES = ("on-hold", "pending")
NOT_SALE_STATUSES = ("cancelled", "failed", "refunded")
IGNORED_STATUSES = ("checkout-draft", "trash")
PAID_STATUSES = SALE_STATUSES      # the old name, same tuple — never a second definition
SALE_DEFINITION_HE = ("מכירה = הזמנה בסטטוס 'בטיפול' או 'הושלמה'. הזמנות שממתינות לתשלום מוצגות בנפרד "
                      "ולא נספרות עד שהתשלום מאושר. הזמנות שבוטלו, נכשלו או הוחזרו לא נספרות כמכירה, "
                      "והחזר חלקי יורד מהסכום.")


class StoreError(Exception):
    """A store answer we can't use. `code` is what the chat explains."""
    code = "store_error"

    def __init__(self, message: str = "", status: int = 0):
        super().__init__(message or self.code)
        self.status = status


class StoreAuthError(StoreError):
    code = "store_auth"


class StoreNotFound(StoreError):
    code = "store_not_found"


class StoreNotWooCommerce(StoreError):
    code = "store_not_woocommerce"


class StoreRateLimited(StoreError):
    code = "store_busy"


class StoreNeedsHttps(StoreError):
    code = "store_needs_https"


_rate_lock = threading.Lock()
_rate = {}   # store key -> deque of request times


def _check_rate(key: str):
    now = time.monotonic()
    with _rate_lock:
        calls = _rate.setdefault(key, deque())
        while calls and now - calls[0] > RATE_WINDOW:
            calls.popleft()
        if len(calls) >= RATE_PER_MINUTE:
            raise StoreRateLimited("too many store reads this minute")
        calls.append(now)


def _reset_rate():
    with _rate_lock:
        _rate.clear()
    _shape.clear()


def normalize_site(site_url: str) -> str:
    site = (site_url or "").strip().rstrip("/")
    if not site:
        raise StoreError("no site address", 0)
    if site.startswith("http://"):
        # Basic auth over plain HTTP would send the key in the clear —
        # WooCommerce itself only accepts it over HTTPS
        raise StoreNeedsHttps("the store must be served over https")
    if not site.startswith("https://"):
        site = "https://" + site
    return site


def _path(path: str) -> str:
    path = (path or "").lstrip("/")
    if not path.startswith(API_PREFIX) or ".." in path or "?" in path or "#" in path:
        raise ValueError("only wc/v3 read paths")
    return path


def _headers(auth: tuple) -> dict:
    user, secret = auth
    token = base64.b64encode(f"{user}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Accept": "application/json",
            "User-Agent": "uallak-store-reader/1"}


def _log(message: str):
    # Path + status + counts only (never a body, a query string or a name)
    print(f"[{SERVICE_NAME}] {message}", flush=True)


# How each store answers, learned on the first read (per instance, no secret):
#   "route"  "pretty" = /wp-json/<path> | "plain" = /?rest_route=/<path> — a
#            store with plain permalinks (or unflushed rewrite rules) has no
#            /wp-json/ and answers 404 HTML there.
#   "auth"   "header" = Basic auth | "query" = consumer_key/consumer_secret as
#            query parameters, WooCommerce's documented alternative for
#            servers that drop the Authorization header (Apache CGI/FastCGI
#            hosts → 401 for a valid key). Only for a REST key (ck_…), only
#            over HTTPS; the URL is never logged.
_shape = {}


def _is_json(response) -> bool:
    """A REST answer (WordPress always sends JSON there), not a theme's 404 page."""
    if "json" in ((response.headers or {}).get("content-type") or "").lower():
        return True
    try:
        return isinstance(response.json(), (dict, list))
    except Exception:
        return False


def _send(site: str, path: str, auth: tuple, params: dict, route: str, auth_mode: str):
    query = dict(params or {})
    headers = _headers(auth)
    if auth_mode == "query":
        headers = {k: v for k, v in headers.items() if k != "Authorization"}
        query.update({"consumer_key": auth[0], "consumer_secret": auth[1]})
    if route == "plain":
        url = f"{site}/"
        query = {"rest_route": "/" + path, **query}
    else:
        url = f"{site}/wp-json/{path}"
    return httpx.get(url, headers=headers, params=query, timeout=TIMEOUT, follow_redirects=False)


def _fetch(site: str, path: str, auth: tuple, params: dict):
    shape = _shape.get(site) or {}
    route, auth_mode = shape.get("route", "pretty"), shape.get("auth", "header")
    response = _send(site, path, auth, params, route, auth_mode)
    if response.status_code == 404 and not _is_json(response) and route == "pretty":
        # No REST API at /wp-json/ — the plain-permalink address of the same API
        retry = _send(site, path, auth, params, "plain", auth_mode)
        if _is_json(retry):
            route, response = "plain", retry
            _log(f"GET {path}: store answers on ?rest_route= (plain permalinks)")
    if (response.status_code == 401 and auth_mode == "header"
            and str(auth[0]).startswith("ck_")):
        retry = _send(site, path, auth, params, route, "query")
        if retry.status_code < 400:
            auth_mode, response = "query", retry
            _log(f"GET {path}: store drops the auth header, key sent as query parameters")
    if response.status_code < 400:
        _shape[site] = {"route": route, "auth": auth_mode}
    return response


def get(site_url: str, path: str, auth: tuple, params: dict = None) -> tuple:
    """(json body, headers) of ONE read (with at most one retry per fallback
    above). GET only — the only network call in this module."""
    site = normalize_site(site_url)
    path = _path(path)
    _check_rate(site)
    try:
        response = _fetch(site, path, auth, params or {})
    except Exception as e:
        _log(f"GET {path} failed: {type(e).__name__}")
        raise StoreError(f"network: {type(e).__name__}") from None
    status = response.status_code
    if status in (401, 403):
        _log(f"GET {path} -> {status}")
        raise StoreAuthError("the store refused the key", status)
    if status == 404:
        _log(f"GET {path} -> 404")
        # The whole REST namespace missing = not a WooCommerce store (or the
        # plugin is off); one missing order is a plain not-found
        if path.count("/") <= 2:
            raise StoreNotWooCommerce("no wc/v3 on this site", status)
        raise StoreNotFound("not found", status)
    if status == 429:
        raise StoreRateLimited("the store asked us to slow down", status)
    if status in (301, 302, 307, 308):
        _log(f"GET {path} -> redirect")
        raise StoreError("the store redirected the API call", status)
    if status >= 400:
        _log(f"GET {path} -> {status}")
        raise StoreError(f"store answered {status}", status)
    try:
        body = response.json()
    except Exception:
        raise StoreNotWooCommerce("the answer was not JSON", status) from None
    return body, response.headers


def get_all(site_url: str, path: str, auth: tuple, params: dict = None) -> dict:
    """Every page of a list, up to MAX_PAGES. {"items", "total", "truncated"}."""
    params = {**(params or {}), "per_page": PER_PAGE}
    items, total, truncated = [], None, False
    for page in range(1, MAX_PAGES + 1):
        body, headers = get(site_url, path, auth, {**params, "page": page})
        if not isinstance(body, list):
            raise StoreNotWooCommerce("a list was expected")
        items.extend(body)
        try:
            total = int(headers.get("X-WP-Total") or headers.get("x-wp-total") or 0) or total
            pages = int(headers.get("X-WP-TotalPages") or headers.get("x-wp-totalpages") or 0)
        except (TypeError, ValueError):
            pages = 0
        if len(body) < PER_PAGE or (pages and page >= pages):
            break
    else:
        truncated = True
    _log(f"GET {_path(path)}: {len(items)} items over {page} page(s){' (truncated)' if truncated else ''}")
    return {"items": items, "total": total if total is not None else len(items), "truncated": truncated}


def verify(site_url: str, auth: tuple) -> bool:
    """The cheapest read that needs order access: one order id."""
    body, _ = get(site_url, API_PREFIX + "orders", auth, {"per_page": 1, "_fields": "id"})
    if not isinstance(body, list):
        raise StoreNotWooCommerce("orders endpoint did not answer with a list")
    return True


# ─── Shaping (pure — what leaves this module never carries personal data
#     unless personal=True was asked for one order) ────────────────────────────

def _money(value) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def clean_items(order: dict) -> list:
    """Product lines without meta_data (a personalised product can carry the
    buyer's name or a note in its meta)."""
    out = []
    for item in order.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        out.append({"product_id": item.get("product_id"),
                    "name": str(item.get("name") or "")[:120],
                    "quantity": int(item.get("quantity") or 0),
                    "total": _money(item.get("total"))})
    return out


def refunds_of(order: dict) -> list:
    out = []
    for r in order.get("refunds") or []:
        if isinstance(r, dict):
            out.append({"id": r.get("id"), "total": abs(_money(r.get("total"))),
                        "reason": str(r.get("reason") or "")[:120]})
    return out


def clean_order(order: dict, personal: bool = False) -> dict:
    """The order as agents see it. personal=False (the default) has no name,
    address, phone, e-mail or note at all."""
    out = {"id": order.get("id"), "number": str(order.get("number") or order.get("id") or ""),
           "status": order.get("status") or "", "currency": order.get("currency") or "",
           "date": order.get("date_created") or "", "date_gmt": order.get("date_created_gmt") or "",
           "total": _money(order.get("total")), "items": clean_items(order),
           "refunded": round(sum(r["total"] for r in refunds_of(order)), 2)}
    if order.get("shipping_lines") is not None:
        out["shipping_method"] = ", ".join(str(s.get("method_title") or "")[:60]
                                           for s in order.get("shipping_lines") or [] if isinstance(s, dict))
    if order.get("payment_method_title") is not None:
        out["payment_method"] = str(order.get("payment_method_title") or "")[:60]
    if personal:
        billing = order.get("billing") or {}
        shipping = order.get("shipping") or {}
        name = " ".join(x for x in (shipping.get("first_name") or billing.get("first_name"),
                                    shipping.get("last_name") or billing.get("last_name")) if x)
        address = ", ".join(x for x in (shipping.get("address_1"), shipping.get("address_2"),
                                        shipping.get("city"), shipping.get("postcode"))
                            if x) or ", ".join(x for x in (billing.get("address_1"), billing.get("address_2"),
                                                           billing.get("city"), billing.get("postcode")) if x)
        out["customer"] = {"name": name, "address": address,
                           "phone": shipping.get("phone") or billing.get("phone") or "",
                           "email": billing.get("email") or "",
                           "note": str(order.get("customer_note") or "")[:300]}
    return out


def summarize(orders: list) -> dict:
    """Aggregates only — the one shape that may be cached. Revenue counts
    SALE_STATUSES only; refunds on those are subtracted for net. Orders
    awaiting payment and the cancelled / failed / refunded ones are counted
    SEPARATELY (never in revenue) — see the definition above."""
    status_counts, products = {}, {}
    gross, refunded, paid, currency = 0.0, 0.0, 0, ""
    awaiting = {"orders": 0, "total": 0.0}
    not_sales = {s: {"orders": 0, "total": 0.0} for s in NOT_SALE_STATUSES}
    for order in orders or []:
        status = order.get("status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        currency = currency or order.get("currency") or ""
        order_refunds = sum(r["total"] for r in refunds_of(order))
        if status in AWAITING_PAYMENT_STATUSES:
            awaiting["orders"] += 1
            awaiting["total"] = round(awaiting["total"] + _money(order.get("total")), 2)
        elif status in NOT_SALE_STATUSES:
            not_sales[status]["orders"] += 1
            # a refunded order's amount is what went back; the others what was never paid
            amount = order_refunds if status == "refunded" and order_refunds else _money(order.get("total"))
            not_sales[status]["total"] = round(not_sales[status]["total"] + amount, 2)
        if status in SALE_STATUSES:
            paid += 1
            gross += _money(order.get("total"))
            refunded += order_refunds
            for item in clean_items(order):
                key = item["product_id"] or item["name"]
                entry = products.setdefault(key, {"name": item["name"], "quantity": 0, "revenue": 0.0})
                entry["quantity"] += item["quantity"]
                entry["revenue"] = round(entry["revenue"] + item["total"], 2)
        # A fully refunded order (status "refunded") is not a sale: it is in
        # status_counts only, never in revenue
    top = sorted(products.values(), key=lambda p: (-p["revenue"], -p["quantity"], p["name"]))[:5]
    gross, refunded = round(gross, 2), round(refunded, 2)
    return {"orders": len(orders or []), "paid_orders": paid, "gross_revenue": gross,
            "refunded": refunded, "net_revenue": round(gross - refunded, 2),
            "average_order_value": round(gross / paid, 2) if paid else 0.0,
            "currency": currency, "status_counts": status_counts, "top_products": top,
            "awaiting_payment": awaiting, "not_sales": not_sales,
            "sale_statuses": list(SALE_STATUSES)}
