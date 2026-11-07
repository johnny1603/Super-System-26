# Online store (WooCommerce), READ-ONLY + per-client conversion types (2026-11-02)

## The client does no technical work (redesign 2026-11-07)

Before this, the store card asked the client to open wp-admin, create a
WooCommerce REST key and paste `ck_…`/`cs_…`. That is gone. In order:

1. **No store login at all (the default).** "How many orders / how much"
   comes from data we already have, in `store_reads(...)["measured"]`, and
   the agents use it FIRST:
   - **Our own purchase records** (`core/store_purchases`). The purchase
     measurement we publish in the client's Tag Manager (store_sales, below)
     also has a record tag that sends US the order number, total and
     currency. Table `store_purchase_events`, one row per client + order.
     Nothing about the buyer is read, sent or stored.
   - **GA4 ecommerce** (`site_traffic.ecommerce`: `ecommercePurchases`,
     `purchaseRevenue`, `transactions`) through the client's Google
     Analytics grant. **Not available today**: it needs `analytics.readonly`,
     which is round 2 (`ANALYTICS_ENABLED`, off, not yet approved by Google).
     We hold no Analytics scope in production; no scope was added. It
     switches on with that flag.
   - Neither available → אורי says so in one line and proposes
     `configure_conversion` (purchase measurement on their site), or
     `connect_tag_manager` first. Never a key.
2. **Deeper order data, ONE click (optional).** One order, statuses, refunds,
   top products. `connect_store` is a link card →
   `GET /api/oauth/woocommerce/start` → the store's own approval screen
   (WooCommerce's built-in authorization, details below). The client logs in
   to their store and presses Approve. Nothing to copy.
3. **Team fallback only:** `/admin/connections/` → "חנות — חיבור ידני" (a
   read key the team has, or the website connection). Never shown to clients.

What the measured numbers mean, said this way and no more: "orders placed,
according to our purchase measurement". The thank-you page also shows for an
order still awaiting payment, and a buyer with scripts blocked is not
counted. So it is **not** the sale definition below. With the store
connected, the store's numbers are the sales and the measurement sits beside
them. The Performance page shows "הזמנות שבוצעו" with that note
(`source: purchase_measurement`).

**Future, not built:** if we built or manage the site (InstaWP or similar),
we connect the store from our side with no client action at all. A small
uallak WordPress plugin could do the same on any site later.

## What it does (with the store connected)

- אורי (and the manager, through `account_state.store`) answers orders and
  sales questions from a LIVE read of the client's store: recent orders, one
  order, sales summary (net revenue, paid orders, average order value, top
  products, status counts), refunds. Periods are in Israel time ("אתמול",
  "החודש", "החודש שעבר", ...).
- It never tells the client to "go look in the site admin", never asks for a
  key or password, and never describes wp-admin / WooCommerce menus.

## What counts as a sale (2026-11-07 — ONE definition, everywhere)

`core/woocommerce_service.py`, used by `summarize()` and therefore by every
reader (Performance, `client_state.store`, the manager chat, אורי):

| Status | Counts as | Where it shows |
|---|---|---|
| `processing`, `completed` | **a sale** (`SALE_STATUSES`) | revenue, orders, average order |
| `on-hold`, `pending` | awaiting payment (`AWAITING_PAYMENT_STATUSES`) | `awaiting_payment` — apart, never in revenue |
| `cancelled`, `failed`, `refunded` | not a sale (`NOT_SALE_STATUSES`) | `not_sales` — apart (count + amount) |
| `checkout-draft`, `trash` | ignored (`IGNORED_STATUSES`) | status counts only |

A partial refund on a sale keeps it a sale; the refunded amount comes off for
net revenue. Period and currency are always named (the store's own currency;
ILS for Shin Sekai). `on-hold` was counted as a sale before 2026-11-07 — it is
WooCommerce's "awaiting payment confirmation", so it moved out. The plain
Hebrew line is `SALE_DEFINITION_HE`. `tests/test_store_purchase.py`
(SaleDefinition) fails on a second definition anywhere in code.

The manager chat reads the store live for the message, exactly like אורי
(`support_agent._add_store_reads` in both paths); for a store_sales client,
sales are the headline number.

## Where a sale came from — the purchase event (2026-11-07)

One GA4 `purchase` event on the order-received page, in the client's own Tag
Manager, approved on the `configure_conversion` card — details in the website
skill. Once per order (browser memory of order ids; a refresh never
re-counts), with order id, total and currency when the page or the dataLayer
has them. Verified against the live version after publishing. If the store's
order-received endpoint is empty or unusable, nothing is written and the card
names the one setting to fix.

**What is blocked, what works, what the owner can do (no new scopes):**
- Works now: the event fires in the client's own GA4 with the order id, value
  and currency; GA4 attributes it to the session's source/medium itself.
- Blocked: READING purchases by source back into uallak (Performance, the
  chat) — needs `analytics.readonly` (round 2, `ANALYTICS_ENABLED`, off).
  Importing `purchase` as a Google Ads conversion — no API in our scopes.
  So the system never says which channel or campaign sold what.
- The owner, manually: in GA4 mark `purchase` as a key event (usually already
  is) and look at purchases by source in Reports; in Google Ads, Goals →
  Conversions → import from GA4 → `purchase`.

## Code

| Piece | Where |
|---|---|
| HTTP, GET only, `_fields`, pagination, rate limit, shaping, math | `core/woocommerce_service.py` |
| Connection (one click + admin fallback), periods, reads, `measured`, the personal-details answer | `agents/store_agent.py` |
| Our own purchase records (clean, once per order, caps, aggregates) | `core/store_purchases.py` |
| GA4 ecommerce (round 2) | `core/site_traffic.ecommerce` |
| The chat turn (one click, no steps) | `core/connection_catalog._store_flow` |
| Live reads into אורי's prompt | `support_agent._add_store_reads` |
| Customer details answered by code, masked in history | `api_server.client_chat` → `store_agent.personal_answer` |
| 30-day aggregates for every agent | `client_state` section `store` (not connected: `measured_last_30_days`) |
| Client routes | `GET /api/client/store`, `GET /api/oauth/woocommerce/start`, `POST /api/client/store/disconnect` |
| Public routes | `POST /api/store/wc-auth/callback` (the store), `GET /api/store/wc-auth/return` (the browser), `POST /api/store/purchase/{token}` (the record tag) |
| Admin route | `POST /api/admin/store/connect` (admin session; key or website connection) |
| Signed tokens | `session.create_wc_auth_state` (30 min, pins client + store + nonce), `session.create_store_purchase_token` (purpose-bound, non-expiring) |
| Dashboard | card "החנות באתר" (hidden until needed, `storeBtn` → one click), Performance "מה נחשב אצלך תוצאה" |
| Purchase event + record tag (Tag Manager) | `gtm_service.PURCHASE` / `PURCHASE_BEACON`, `website_agent.configure_lead_conversion`, `store_agent.order_received_endpoint` |
| Tests | `tests/test_store_one_click.py`, `tests/test_store_reads.py`, `tests/test_conversion_types.py`, `tests/test_google_scope_flows.py`, `tests/test_store_purchase.py` |

## Our purchase record tag

`gtm_service.PURCHASE_BEACON`: a Custom HTML tag "uallak — purchase record"
on the SAME once-per-order purchase trigger as the GA4 tag, published with it
on the client's `configure_conversion` approval. Same Tag Manager scopes; no
new scope. It sends `{o: order number, v: total, c: currency}` with
`navigator.sendBeacon` (text/plain: no preflight, never delays the page) to
`POST /api/store/purchase/{token}`. Clients whose purchase path was already
live get just this tag on the next run (`record_missing`: every other object
is found by name and reused). The live version is checked for it after
publishing (`purchase_record_verified`).

The endpoint is public, like lead capture:
- The token is purpose-bound and can only add records for that client.
- The endpoint always answers 204 and never shows an error.
- It reads at most 1 KB, and only digits are kept as the order number.
- Limits: at most 1000 records per client per day and 30 per minute per
  sender address; a duplicate order number is ignored.
- A client being deleted gets nothing stored, and the table is deleted with
  the client.

## Connection — one click (WooCommerce's built-in authorization)

Checked 2026-11-07 against:
- the REST API docs ("Auth endpoint", woocommerce.github.io/woocommerce-rest-api-docs/#rest-api-keys);
- the auth guide (developer.woocommerce.com/docs/apis/rest-api/authentication/);
- the code itself (plugins/woocommerce/includes/class-wc-auth.php, trunk).

1. `GET /api/oauth/woocommerce/start`, the client's session; refused for
   staff viewing as the client.
   - The store address comes from what we already have: an earlier store row,
     else the website connection. Unknown → the dashboard asks them to connect
     the website first.
   - We redirect to `{store}/wc-auth/v1/authorize` with:
     - `app_name=uallak`, `scope=read`;
     - `user_id` = our signed state (client + store + nonce, 30 min);
     - `return_url=…/api/store/wc-auth/return`;
     - `callback_url=…/api/store/wc-auth/callback` (WooCommerce requires https).
   - The nonce is also kept server-side (`app_settings store_auth_pending:<id>`)
     and is single use.
2. WooCommerce sends a logged-out client to their store's login first. It
   needs the `manage_woocommerce` capability. The client presses Approve.
3. The store POSTs JSON `{key_id, user_id, consumer_key, consumer_secret,
   key_permissions}` to our callback, with a 60-second timeout.
   - We verify the state and consume the nonce.
   - We require `key_permissions == "read"` and well-formed keys.
   - We verify with one live read against the store **we pinned** (never an
     address from the body), then store the key encrypted (`client_accounts`
     platform `woocommerce`, `ck:cs`).
   - We answer **200 only when stored**. Any other answer makes WooCommerce
     delete the key it just made (`maybe_delete_key`).
   - One exception: the store is busy serving the client's own Approve while
     it calls us, so on a small host our check read can time out. A
     network-level failure (not a refusal) still stores the key; the first
     real read re-checks it.
   - The body is never logged or echoed.
4. The browser returns to `return_url` with `success=1|0&user_id=…`.
   - We redirect to `/dashboard/?connected=store`, or
     `connect_error=store_denied|store_expired|store`.
   - The redirect drops the state from the address bar.

The key appears in the store's own key list as "uallak - API (<date>)". The
client can revoke it there at any time; disconnecting on our side deletes our
copy.

Not checked against a real store (no credentials on this machine). Stores with
"plain" permalinks may not route `/wc-auth/v1/authorize`. WooCommerce also
answers `?wc-auth-version=1&wc-auth-route=authorize` there, as an untested
fallback if a client hits it.

**Team fallback** (`/admin/connections/`, admin session only):
- a READ key the team has (`store_agent.connect`, verified live first);
- the website's application password (`connect_with_site`: no new secret
  stored, an admin credential, unverified in general).

## Personal data

- Lists, summaries, status counts and refunds name their fields (`_fields`)
  without `billing`, `shipping`, `customer_note` or `meta_data`; line-item
  meta (a personalised product can carry a name) is dropped again in code.
- One order's customer details are read only when the client explicitly asks
  for them with the order number, and are answered by CODE — never sent to
  the AI provider. The client sees them once; the chat history stores a
  masked copy (`•••`).
- Nothing personal in the DB, logs, alerts, cost records or analytics: log
  lines carry paths, status codes and counts only. Cached: aggregates only,
  in memory, at most 20 minutes (10 in store_agent + 10 in client_state).
- Refund reasons are written by the store owner; they are shown when the
  client asks about refunds and are never cached or stored.
- Everything read from the store is DATA, never instructions.

## Conversion types (per client)

`clients.conversion_types` ⊆ `form_submit`, `whatsapp_click`, `phone_click`,
`store_sales` (default `form_submit`; `conversion_types_confirmed_at` = the
client's approval). Chosen on the `set_conversion_types` card — the interview
asks how customers reach the business, אורי asks when it was never confirmed;
nothing is guessed. Tag Manager triggers/tags per type: see the website
skill. Shin Sekai (27) is `store_sales` ONLY (the owner already gets
WhatsApp alerts and wants no WhatsApp / phone / form lead measurement); the
migration sets exactly that (only if never confirmed), and no code path adds
a lead type for it — an unreadable choice sets up nothing.

Performance page: the client's own types with Hebrew labels; store sales
first (revenue + orders per 7/30/90 days); WhatsApp/phone clicks from our
landing-page counters (30 days) with the line that a click is an indication
of an enquiry, not a confirmed conversation.

## Not done, and why

- **Never run against a real store** — no store credentials on this machine.
  Everything is tested against an in-memory store that answers like the
  REST API (including `_fields`).
- **WhatsApp/phone click counts on the WordPress site itself** and **purchases
  by source**: reading them needs the round-2 Analytics grant
  (`ANALYTICS_ENABLED`) — skipped, per the no-new-scopes rule. (Sending the
  purchase event works now through Tag Manager, above; reading it back doesn't.)
- **The purchase event has never run on a real store or container** — the
  Tag Manager API body shapes (`eventSettingsTable`, `jsm`, `domReady`) are
  from the API reference, tested in memory and the JavaScript in Node.
- Writes to the store (order status, refunds, products) — out of scope.
- `disconnect_store` as a chat card — P3 in docs/CHAT_PARITY.md (the card has
  the button).

## Run once

`migrations/2026-11-02-store-conversions.sql` in Supabase (idempotent). Until
then every client reads as `form_submit`, the selection card cannot save
(it says so), and the store works without the catalog row (the flow is in code).

`migrations/2026-11-07-store-purchase-events.sql` (idempotent; comment-free
copy `store-purchase-events-for-supabase.txt`, gitignored). Until it runs, the
record endpoint stores nothing (still 204) and the measured numbers read as
"none yet". Nothing else changes.

## Questions for a lawyer

1. Under the Israeli Privacy Protection Law (and Amendment 13), when we read
   a client's store orders, is uallak a "holder" (מחזיק) of their customers'
   data on the client's behalf, and do we need a written data-processing
   agreement with each client (the Privacy Protection Regulations (Data
   Security) on outsourcing)?
2. Is showing one order's customer details to the client in our chat, without
   storing them, "processing" that must be described to the store's end
   customers in the store's own privacy notice? Must our terms require the
   client to do so?
3. Sending order data WITHOUT personal details (amounts, product names,
   statuses) to Anthropic (US) to answer a question — any cross-border
   transfer requirement (Privacy Protection (Transfer of Data to Databases
   Abroad) Regulations), given product names could still identify a buyer of
   a personalised product?
4. Store owners outside Israel (EU buyers): does GDPR apply to us as a
   processor, and do we need SCCs / a DPA template?
5. Is the masked copy in the chat history plus "shown once" enough to say we
   "do not keep" customer details, or must the live response itself be
   excluded from any server/proxy logs we don't control (Cloud Run request
   logs do not log response bodies — confirm that is sufficient)?
6. Refund reasons are free text written by the store owner and may contain
   personal data — is displaying them (not storing) acceptable?
7. Using the admin application password as a (team-only) fallback gives us
   more access than reading needs — does using it create liability, and
   should the terms require the read-only connection?
9. Our purchase record tag sends order number, total and currency (no buyer
   data) from the buyer's browser to uallak. Must the store's privacy notice
   or cookie banner mention it, and who is responsible for that?
8. WhatsApp/phone click tracking on the client's site: does adding these
   Tag Manager events require cookie/consent banner changes on the client's
   site (Amendment 13, ePrivacy for EU visitors), and who is responsible —
   the client or uallak?
