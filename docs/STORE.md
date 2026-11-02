# Online store (WooCommerce), READ-ONLY + per-client conversion types (2026-11-02)

## What it does

- אורי (and the manager, through `account_state.store`) answers orders and
  sales questions from a LIVE read of the client's store: recent orders, one
  order, sales summary (net revenue, paid orders, average order value, top
  products, status counts), refunds. Periods are in Israel time ("אתמול",
  "החודש", "החודש שעבר", ...).
- Not connected → אורי says so and offers `connect_store` (the card's ONE
  "חבר" button; the chat walks the client through creating a read key).
- It never tells the client to "go look in the site admin".

## Code

| Piece | Where |
|---|---|
| HTTP, GET only, `_fields`, pagination, rate limit, shaping, math | `core/woocommerce_service.py` |
| Connection, periods, reads, the chat's reads, the personal-details answer | `agents/store_agent.py` |
| Chat-guided connect steps | `core/connection_catalog._store_flow` |
| Live reads into אורי's prompt | `support_agent._add_store_reads` |
| Customer details answered by code, masked in history | `api_server.client_chat` → `store_agent.personal_answer` |
| 30-day aggregates for every agent | `client_state` section `store` |
| Routes | `GET /api/client/store`, `POST /api/client/store/connect`, `/connect-site`, `/disconnect` |
| Dashboard | card "החנות באתר" (`storeBtn`), Performance "מה נחשב אצלך תוצאה" |
| Tests | `tests/test_store_reads.py`, `tests/test_conversion_types.py`, `tests/test_google_scope_flows.py` |

## Connection — two ways, one recommended

1. **Read key (recommended, least privilege).** From WooCommerce's docs
   (woocommerce.com/document/woocommerce-rest-api/, checked 2026-11-02):
   WooCommerce > Settings > Advanced > REST API > Add Key; Description,
   User, Permissions = **Read**; "Generate API key" shows the Consumer key
   (`ck_…`) and Consumer secret (`cs_…`) once. Sent as HTTP Basic auth over
   HTTPS (developer.woocommerce.com/docs/apis/rest-api/). Verified with one
   live read before it is stored (encrypted, `client_accounts` platform
   `woocommerce`). Plain-HTTP stores are refused (the key would travel in
   the clear).
2. **The website's application password (fallback).** WordPress core
   authenticates application passwords on every REST route, so an
   administrator's password should pass WooCommerce's checks. **Unverified
   against a real store** — `connect_with_site` proves it per site with a
   live read before anything is stored, and stores no new secret (a marker;
   the password stays in the website row). It is an admin credential, so
   the card calls it "less recommended" and אורי suggests the key.

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
skill. Shin Sekai (27) is set to `whatsapp_click` + `store_sales` by the
migration (only if never confirmed).

Performance page: the client's own types with Hebrew labels; store sales
first (revenue + orders per 7/30/90 days); WhatsApp/phone clicks from our
landing-page counters (30 days) with the line that a click is an indication
of an enquiry, not a confirmed conversation.

## Not done, and why

- **Never run against a real store** — no store credentials on this machine.
  Everything is tested against an in-memory store that answers like the
  REST API (including `_fields`).
- **WhatsApp/phone click counts on the WordPress site itself** and **GA4
  purchase events**: reading them needs the round-2 Analytics grant
  (`ANALYTICS_ENABLED`) — skipped, per the no-new-scopes rule. Purchase
  tracking in GA4 would also need a GA4 plugin on the client's store
  (outside read-only).
- Writes to the store (order status, refunds, products) — out of scope.
- `disconnect_store` as a chat card — P3 in docs/CHAT_PARITY.md (the card has
  the button).

## Run once

`migrations/2026-11-02-store-conversions.sql` in Supabase (idempotent). Until
then every client reads as `form_submit`, the selection card cannot save
(it says so), and the store works without the catalog row (the flow is in code).

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
7. Using the admin application password as a fallback gives us more access
   than reading needs — does offering it create liability, and should the
   terms require the read-only key?
8. WhatsApp/phone click tracking on the client's site: does adding these
   Tag Manager events require cookie/consent banner changes on the client's
   site (Amendment 13, ePrivacy for EU visitors), and who is responsible —
   the client or uallak?
