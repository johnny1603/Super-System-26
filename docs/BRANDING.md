# Branding — "how would your business look under its best branding" (2026-11-15)

ליאור shows a client mockups of a branding direction. The client approves or rejects. Nothing
is printed, ordered or built by a mockup.

## What the client sees

1. **The button.** Media tab, top: "להראות לי את המיתוג". It opens ליאור's chat with the request
   already said. The same request typed to any agent works the same way.
2. **One clear line before starting** (fixed text, 5 languages, `branding.TERMS`): 5 attempts are
   free; after that each extra attempt costs a small symbolic amount shown before it runs; after
   approval and production there are no more attempts. Plus one value line (`branding.VALUE`):
   outside this costs thousands of shekels, here it is free up to the approval point. Nothing
   beyond that is claimed.
3. **A short characterisation.** The agent offers a ready-made direction the client only reacts to
   ("warm colours and a minimal logo, shall I?"). Never an empty question, never a form.
4. **Card 1 — `create_brand_mockups`.** Says the direction, the attempt number and the terms line.
   The click runs one attempt (about a minute).
5. **Card 2 — `approve_branding`.** A link to the mockup page and the button "זה הכיוון שלי". The
   text says plainly that branding is the owner's to feel, unlike marketing media where we lead.
6. **The mockup page.** Logo, business card, shop sign, flyer, poster, and how a website could
   look. Under each: what it takes to actually get it. The logo line offers a human designer.
   The website line says branding never includes building a site.
7. **Not connecting with the logo:** card `request_logo_designer` → the team is alerted, the
   designer contacts the client and gives the price.
8. **After approval:** the palette, fonts, style and logo mark are the client's **brand kit**.
   Real production is quoted: card `approve_production_quote`, price shown before anything is made.

## How it is built

| Part | Where |
|---|---|
| Mockups | `core/brand_mockups.py` — SVG drawn in code. Every letter and logo is composed there. No image model, no network, no stored file: the page is rebuilt from the stored concept on each view |
| The concept | `core/branding.create_attempt` — ONE model call returns palette, a font pair, a logo mark and short copy, each from a fixed list. `clean_concept` enforces every value; the business name and contact details come from our record, never the model |
| Attempts, approval, kit, quote | `core/branding.py` |
| Cards | `core/branding_actions.py`, registered into `agent_actions.ACTIONS` |
| Agent rules | `support_agent.STUDIO_RULE` (in the manager's and the specialists' prompts) + `branding` in the turn's data |
| Routes | `GET /api/client/branding`, `GET /api/client/branding/view/{n}`, admin: `GET /api/admin/branding/{id}`, `POST …/quote`, `POST …/grant` |
| Storage | two `app_settings` rows per client: `branding:<id>`, `brand_kit:<id>`. No migration. Cleared with the client |

The mockup page is served with a sandbox CSP that has no script source at all.

## Money

- **Our cost per attempt** is one model call, capped: `ATTEMPT_COST_CAP_ILS` (₪1.5). The model
  is chosen so the worst case fits under the cap BEFORE the call; no retry is paid for. The
  measured cost is a `client_costs` row (category media, action `branding_attempt`) and is kept
  on the attempt. A failed call uses no attempt.
- **Owner's decision (2026-11-16):** the charge stays off until another payment system is in.
  Meanwhile the owner opens more attempts by hand: Control Room → the client → "מיתוג" (two
  buttons, +1 / +3; also shows attempts used, left and what they cost us).
- **Extra attempts: the charge is OFF** (`EXTRA_ATTEMPT_CHARGE_ENABLED = False`). The system has
  no per-attempt charge today — only the monthly subscription and e-mailed invoices. Past the 5
  free attempts nothing runs: the client gets one plain line, the team gets one alert, and staff
  can grant more (`POST /api/admin/branding/{id}/grant`). The price a charge would use is ready:
  measured cost + 25%, never under ₪5 (`extra_attempt_price_ils`). **Owner's decision.**
- **Production quote:** staff enter what each item costs us
  (`POST /api/admin/branding/{id}/quote`, body `{"costs": {"logo": 400, "signage": 1500}}`). The
  client's price is cost × 2 (about half cost, half margin). The client sees prices only. On
  approval the team is alerted to send the payment request; production starts after payment.
  A website is never a quote item.

## The brand kit's readers

`branding.kit_for_prompt` / `kit_palette_list`: generated media (`media_agent._brand_palette`,
kit first) and designed landing pages (`landing_creative`, "client_approved_brand_kit").

## Existing live websites

The system never redesigns a client's live site. The agents explain warmly that this is done only
by human hands, because an unsupervised change to a live site is risky, and that a price comes
only after a characterisation with a person. No automatic price. A tracked team request is opened.
A non-WordPress site: a new site on a new domain, or a landing page connected to the existing one.

## Limits

- Never run against a real model or a real client. The sample page was checked in Chrome on
  desktop (Hebrew, right-to-left); the last alignment fix on the business card was not re-checked.
- The mockups are schematic (flat shapes), not photographs of printed goods.
- The logo is a mark from ten shapes plus the name in a chosen font. It is a direction, not a
  finished logo — which is why the human designer is offered.
- No admin screen yet for the quote and the grant: they are two admin endpoints.
- Posts published by מאיה do not read the kit yet (the weekly plan does, in the team round).

Tests: `tests/test_branding.py`.
