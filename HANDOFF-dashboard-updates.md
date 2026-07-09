# uallak — Handoff: Client Dashboard Updates

**Context:** `CLAUDE.md` and all prior `HANDOFF-*.md` files are in the repo root. You already built the real, data-driven version of `dashboard/client/index.html` in an earlier session (session cookie auth, live data from `/api/dashboard`, support chat wired to `/api/client-chat`). This handoff covers **new additions on top of that** — I'm providing an updated version of the same mockup file with new sections; treat it as a design/structure reference, not a source of real data (same rule as before — real data or honest zero/empty states, never invented numbers).

## 1. Welcome tour (new)

A 5-step guided walkthrough that appears on a client's first dashboard visit (and should be re-openable later, e.g. from a small "?" or help icon — your call on where). It's a slim fixed top banner (not a blocking modal) that walks through the page top-to-bottom, highlighting each real section with a glow/outline as it goes:
1. Package/stats overview
2. Pending-approvals area (explain it exists, even with nothing in it yet)
3. Connections (mentions estimated time)
4. Recent activity
5. Support chat button

No skip option on step 1 — client should go through it once. Steps 2+ do allow going back. On finishing, scroll back to top.

**Time estimate should be dynamic per package size** — a client on a lighter package (e.g. single-service) shouldn't see the same time estimate as a full multi-platform package. You decide how to calculate this (e.g. based on number of `recommended_services`/platforms in their package).

Only show this automatically once per client (e.g. a flag on the `clients` row, or track via `client_activity`) — don't re-trigger it every login.

## 2. Package upgrade (new) — needs real PayPal wiring, not just UI

A panel (opened from a "שדרוג חבילה" button near the package badge) showing available upgrade tiers with pricing, letting the client select and confirm an upgrade.

**This needs real backend work**: upgrading must actually modify their PayPal subscription (or handle the cancel-old/create-new flow, whichever PayPal's subscriptions API makes more sense for) — not just update a database row. Look at what `core/paypal_service.py` already supports and extend it as needed. Takes effect at next billing cycle (not an immediate mid-cycle change) — confirm this is a reasonable default or adjust if PayPal's API makes something else cleaner.

## 3. Billing section (new)

Real payment history for the client — setup fee, each monthly charge, dates, status. Pull this from whatever record already exists (PayPal subscription/transaction data, and/or a Supabase table if one should be added to track this — your call on the cleanest source of truth, but don't invent a new payment record system if PayPal's API can already answer "what did this client pay and when").

## 4. Layout change

The sidebar nav was removed — it's a single scrolling page now (matches the elevator-style tour). Simpler is fine, no need to preserve the old sidebar structure.

Please read the current real `dashboard/client/index.html`, `core/paypal_service.py`, and `core/api_server.py` before starting, and use your judgment on implementation details throughout, same as prior handoffs.
