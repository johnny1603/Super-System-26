# Market & partnership research ("prospecting") — Phase 1 design

Status: **DRAFT — awaiting Johnny's approval. Nothing below is built yet.**
Written 2026-10-22. Every external fact is marked **VERIFIED** (with the source and the
date it was read) or **UNVERIFIED** (from memory or a secondary source — check before
relying on it).

---

## 1. What Phase 1 is

Three capabilities for the agents, for ONE client at a time:

1. **Area analysis** — where the client's business is, the competitors around it, and
   complementary businesses nearby (a bridal-shoe maker → dress shops, photographers,
   event halls).
2. **Partner / prospect research** — a scored list of businesses worth approaching, each
   with OUR reason for the match.
3. **Personalized draft messages** — one draft per business, written for that business's
   name and character, never a template blast. Each draft is a confirmation card in the
   action layer (propose → approve → execute), and the **SEND step is disabled** until
   Google approves `gmail.send` (§7).

Exposed through `core/client_state` so every agent answers from it (§9).

**Not in Phase 1:** any automatic sending, WhatsApp automation, buying lists, guessing
e-mail addresses, exporting lists out of the app, the client's own-customer messaging.

---

## 2. Source: Google Places API (New), with an API key

- **API key, not OAuth.** No user consent, no scope — **no impact on the Google OAuth
  verification under review.** A new env var `GOOGLE_PLACES_API_KEY` (registered in
  `keys_agent.KEYS`), restricted in the Cloud Console to *Places API (New)* only. It cannot
  be IP-restricted (Cloud Run egress has no fixed IP), so the API restriction and a quota
  cap in the Console are the guards.
- **Which Cloud project bills it:** UNDECIDED — `super-system-500410` or the OAuth project
  `plasma-canyon-229222`. Maps Platform needs a billing account on that project. (Q1, §12)

### What Places returns — and what it does not

Field → billing tier, **VERIFIED** on Google's *Place Data Fields (New)* page, last
updated 2026-10-21:

| Field | Text / Nearby Search | Place Details |
|---|---|---|
| `id` (place_id) | Essentials (IDs only) / Nearby: Pro | Essentials (IDs only) |
| `displayName`, `primaryType`, `businessStatus`, `googleMapsUri` | Pro | Pro |
| `formattedAddress`, `location`, `types` | Pro | Essentials |
| `nationalPhoneNumber`, `websiteUri`, `rating`, `userRatingCount`, `regularOpeningHours`, `priceLevel` | **Enterprise** | **Enterprise** |
| `editorialSummary`, `reviews` | Enterprise + Atmosphere | Enterprise + Atmosphere |

A request is billed at the **highest** tier of any field in its field mask (VERIFIED,
*Usage and Billing*, 2026-10-21). So the field mask is a cost control, not a detail.

**Places does NOT return e-mail addresses** — there is no such field. E-mail comes only
from the business's own website (§5).

---

## 3. What we may store — Google's terms

| Rule | Status |
|---|---|
| "You must not pre-fetch, cache, or store Places API content beyond the allowed exceptions." | **VERIFIED** — *Places API Policies*, last updated 2026-10-21 |
| Place IDs are exempt: "You can therefore store place ID values indefinitely." | **VERIFIED** — same page |
| Displaying Places data **without a Google Map** requires the **Google logo**. | **VERIFIED** — same page |
| When Google returns third-party attributions, showing "Google Maps" / the logo alone is not enough — show those attributions too. Reviews/photos must credit the author. | **VERIFIED** — same page |
| Latitude/longitude from Places may be cached up to **30 consecutive calendar days**, then deleted. | **UNVERIFIED for today** — present in the archived *Service Specific Terms* of 2025-03-31 and 2025-05-01; the current live page could not be read by my tools |
| Maps Platform ToS §3.2.3 forbids scraping/exporting Maps content for use outside the Services, caching except as allowed, and creating content from Maps content. | **UNVERIFIED wording** — seen only via secondary sources quoting it |

### The storage design that follows

- **Stored permanently in our DB:** `place_id` + data WE create — our score (0-100), our
  reason **code** (`complementary_category`, `same_area`, `audience_overlap`…), our
  qualification notes, the client's decisions (approved / rejected / contacted), dates.
- **Never stored:** names, addresses, phones, websites, ratings, opening hours, reviews,
  photos, coordinates. They are **fetched on demand by place_id** when a person looks at
  the list, held in memory for that one response only, and shown **with Google
  attribution** (logo + any third-party attribution).
- **Our reason text must not restate Google content.** "Bridal-dress shop 400 m away, its
  customers buy shoes for the same event" is ours; "rated 4.8 with 312 reviews" is Google's
  and is rendered live, never written into a stored note. (Code enforces it by storing
  reason CODES + our own short text, validated not to contain the fetched fields.)
- **No export.** No CSV/Sheet of prospects with Google fields — §3.2.3 (UNVERIFIED
  wording) is read conservatively. Lists live only inside the app, rendered live.
- **Distance / "where the business is":** computed live from coordinates fetched in the
  same request, never stored (the 30-day lat/lng rule is UNVERIFIED for today, so we
  don't rely on it).

**Consequence (cost):** every time someone views a list, the names cost Place Details
requests. That is what the terms require; §8 caps it.

---

## 4. Search layer

| Need | Tool |
|---|---|
| Discover businesses (competitors, complementary, prospects) | **Places API (New)**: Text Search / Nearby Search with a minimal **Pro** field mask (`id, displayName, primaryType, types, formattedAddress, location, businessStatus`) |
| Shortlist detail (website, phone) for the top N only | **Place Details, Enterprise** — never in the discovery call |
| Read a business's OWN site / contact page, judge relevance | the existing `claude_json.claude_web_search_call` + a polite direct fetch (§5) |
| ~~Google Custom Search JSON API~~ | **Not used.** Closed to new customers; Google's stated discontinuation date for existing users is **2027-01-01** (VERIFIED via several sources quoting Google's notice; Google's own page not fetched) |

The client's own location comes from what they told us (sales chat, `client_knowledge`)
or a Places Text Search for their own business name + city. The client confirms it once.
We store only the `place_id`.

---

## 5. E-mail discovery — only from the business's own public site

- **Only** the business's own website (the `websiteUri` fetched on demand), and only its
  homepage plus at most 2 pages linked as "contact"/"צור קשר"/"about". Never guessed
  (`info@…` patterns), never bought, never taken from directories or social networks.
- **Polite fetching:** `robots.txt` respected (stdlib `urllib.robotparser`), an honest
  User-Agent naming uallak with a contact URL, ≤ 1 request per domain every 10 s, ≤ 3
  pages per domain, 30-day cache of "checked, nothing found" so we don't re-crawl.
- **What we keep:** the address, the **exact page URL it was found on**, the date found,
  and the business's `place_id`. Only role addresses or addresses the site presents as
  the business's contact; personal-looking addresses are flagged for the human, not
  dropped silently. (Whether a sole-trader address is "personal data" is Q7.)
- The site's URL itself is a Google field. We store the **page URL where the address was
  found** as evidence of source — that is our own observation of a public page. Whether
  that is acceptable under §3.2.3 is UNVERIFIED (Q3).

---

## 6. Israel — the Communications Law ("spam law", §30A)

What I could confirm (secondary legal sources, read 2026-10-22 — **not legal advice**):

- An **advertisement** ("דבר פרסומת") by **e-mail, SMS, fax or automatic dialer** needs the
  recipient's **prior explicit consent**, in writing. Silence is not consent; the sender
  carries the burden of proving consent.
- **One exception that matters here:** a **one-time approach to a business** recipient
  that **offers it to agree** to receive advertising is not a violation (§30A(b) as
  quoted). It is ONE message, and it must be a request for consent, not an ad.
- Exemplary damages without proof of damage; courts weigh scale, refusals ignored, good
  faith. The commonly cited ceiling of **₪1,000 per message is UNVERIFIED** here.
- From memory, **UNVERIFIED:** the message must be marked as an ad ("פרסומת" in the subject
  line), identify the sender with contact details, and offer a free, simple way to refuse.
  After a refusal nothing more may be sent.

### The compliance design (Phase 1)

1. **No automatic sending.** Phase 1 produces drafts only. Even after `gmail.send` is
   approved, **every message needs Johnny's explicit approval, per message** — an
   `admin`-approver action card, one card = one message. No bulk approve.
2. **Opt-out line is mandatory and code-enforced**, in Hebrew (and the draft's language):
   how to refuse, and that refusing stops all contact. A draft without it cannot be stored.
3. **Suppression list ("do not contact")**, checked by code before ANY send, from any path:
   e-mail address, domain, phone and `place_id`. It is **never deleted with a lead or a
   client** — so its table has **no integer `client_id` column** (client deletion clears
   every table with one, by schema discovery). It records who asked, when, and the source.
   Entries are only ever added (removal = a human, with a reason, audited).
4. **Source and date kept** for every address (§5) and every message.
5. **Tiny caps:** at most 5 approved messages per client per day and 20 in total per day,
   **one message per business ever** unless it replied — matching the "one-time approach"
   exception.
6. **First message is a request for consent**, not an ad: who we are on behalf of, why this
   business, a one-line value proposition, "may we send you details?", the opt-out line.
7. **Audit:** each proposal/approval/send/refusal is already an `agent_actions` row; the
   send step will add the message id and the suppression check result.

### What was built on top of this (2026-10-30)

- **The client approves, not Johnny**, per message: the Gmail agent's `send_outreach_email`
  card (client approver, one card = one e-mail, from the client's own Gmail; hard cap 10 per
  client per day from the send record). See `docs/GOOGLE-ROUND2.md`.
- **ONE follow-up per business, ever — owner's decision, needs the lawyer (Q2 below).** The
  agent may PROPOSE it (card `send_outreach_followup`, client approves the exact text) only
  when the first message is at least 7 days old and the client marked no reply, removal or
  bounce; never to a suppressed address; inside the same daily cap; with the opt-out line.
  Rules in `outreach_policy.followup_refusal`, re-checked at the click. This goes beyond the
  strict "one-time approach" reading of point 5 above, so it must be cleared with the lawyer
  before `GMAIL_SEND_ENABLED` is switched on.
- **The Outreach page** ("פניות לעסקים", `core/outreach_tracker`): one row per business,
  status derived at read time. We hold `gmail.send` only and never read mail, so a reply or
  a removal request is known only when the client presses "ענו" / "ביקשו להסיר". The latter
  writes the suppression list FIRST (address; the business's own domain when the address is
  on it — never a free-mail domain; `place_id` when known), then cancels any open card.
- **Places terms (§3) on that page and its exports:** no Places content is stored or
  exported. The business name is as written on the business's own site / in our draft, the
  area is the search area WE chose, and "Google Maps" appears only as the label of how a
  business was found. Only `place_id` is kept.

### Questions to ask the lawyer

1. Does a **B2B partnership proposal** ("let's refer customers to each other") count as a
   "דבר פרסומת" under §30A at all?
2. Does our first message fit the **one-time approach to a business** exception as drafted
   in §6.6? What must it contain, and what must it NOT contain, to stay inside it? **Is ONE
   follow-up after 7 days without an answer (client-approved, with the opt-out line) still
   inside it, or does it need prior consent?** (built, off — see "What was built" above)
3. Who is the **"מפרסם"** — our client (the business on whose behalf we write), uallak (who
   drafts and sends), or both? Who is liable for a breach?
4. Is a **sole proprietor** (עוסק פטור/מורשה with a personal name) a "business" recipient
   for the exception, or a private person?
5. Is a role address like `info@` found on the business's **own public site** usable for
   the one-time approach? Does publishing it imply anything?
6. Exact current **marking and identification** requirements ("פרסומת" in the subject,
   sender details, refusal mechanism) and the current **damages ceiling**.
7. **Privacy Protection Law (incl. Amendment 13):** is a list of businesses with contact
   e-mails a "database" we must register or protect at a given level? Can we keep the
   suppression list indefinitely (it holds e-mail addresses)?
8. Does **WhatsApp** to a business number fall under §30A (it is not listed as e-mail/SMS/
   fax/dialer), and under which other rules?
9. Retention: how long may we keep the source URL / date evidence after the client leaves?

---

## 7. Sending and WhatsApp

- **E-mail sending** needs the Gmail `gmail.send` scope on the client's (or our) Google
  account: a **sensitive** scope (not restricted) per vendor docs — **UNVERIFIED against
  Google's own scope list** — so it requires OAuth verification but not a security
  assessment. Adding it changes the verification under review, so **it is not requested in
  Phase 1.** The send action exists in the registry but its runner refuses
  (`send_disabled`), and the card says so plainly.
- **WhatsApp: no automation of cold outreach.** Meta requires prior **opt-in** before
  business-initiated messages (VERIFIED in substance via Meta's developer docs as quoted by
  several providers). For businesses that did not opt in, the agent only prepares lists
  and drafts; **a human sends manually** from their own phone. (A prefilled `wa.me` link
  the human taps is still a manual send.)
- **WhatsApp to a client's own registered customers** (explicit opt-in) is a separate,
  later feature — not designed here.

---

## 8. Costs

First-tier list prices per 1,000 requests and free monthly allowance per SKU, from Google's
pricing page as returned by search (**re-check on the live pricing page before launch**):

| SKU | Price / 1,000 | Free / month |
|---|---|---|
| Text Search Pro | $32 | 5,000 |
| Nearby Search Pro | $32 | 5,000 |
| Place Details Pro | $17 | 5,000 |
| Text / Nearby Search Enterprise | $35 | 1,000 |
| Place Details Enterprise | $20 | 1,000 |
| Enterprise + Atmosphere | higher (e.g. Text Search $40) | — |

The free allowances are **per billing account, shared by all clients.** The old $200
monthly credit ended 2025-02-28 (VERIFIED, *Usage and Billing*).

**Design:**
- Every Places request is recorded with `cost_tracker.record_cost` **against the client**
  (`client_id`, category `places_api`, the SKU in details), at list price — even inside the
  free allowance, so the true marginal cost is visible.
- **Per-client daily cap** through `api_call_counters` (`places:<client_id>`, e.g. 60
  requests/day) plus a **global daily brake** (e.g. 600/day), the same idiom as the
  YouTube search brake. Over the cap, the agent says so plainly and the list waits.
- Discovery uses Pro field masks only; Enterprise fields are fetched only for the shortlist
  someone opens.
- **"The credits model":** there is **no internal credits model** for our own spend in the
  codebase today. The only "credits" are clients' own Higgsfield plan credits. Options for
  you (Q5): (a) research is **included** in the package with a monthly allowance per client,
  visible to you in the Control Room costs (like other internal costs); (b) a new
  client-facing **research credits** balance (a real new product feature); (c) a paid
  add-on priced in `PRICING`. A new meter bucket `research` in `cost_tracker` is proposed
  either way.

---

## 9. How the agents use it

- **`client_state.prospecting`** (agent-readable, no Google fields): last area analysis
  date, counts by group (competitors / complementary / prospects), the top matches as
  `{place_id, score, reason_code, our_reason}`, drafts waiting, suppressed count, today's
  usage vs cap. **No names** — they are Google content and can't be stored or cached.
- To talk about specific businesses, the agent **opens the list**: a list card/panel that
  fetches names live by `place_id` (Place Details Pro), with Google attribution. That is
  one Details request per business shown, capped (§8).
- Actions (all through `core/agent_actions`):
  - `research_area` / `research_partners` — client approves (they cost money); runs the
    search, stores place_ids + our scores.
  - `draft_outreach` — writes ONE personalized draft for ONE place_id (its name and
    character fetched live; the draft text is ours and may name the business — that is our
    message, not stored Google content).
  - `send_outreach_email` — **admin approver, per message, disabled** (`send_disabled`)
    until `gmail.send` is approved; suppression check + caps + opt-out check run first
    regardless, so they are tested now.

## 10. Data model (Phase 1)

| Table | Holds | Notes |
|---|---|---|
| `prospect_research` | client_id, kind (`area`/`partners`), created_at, our query params, results `[{place_id, group, score, reason_code, our_reason}]` | no Google fields |
| `prospect_contacts` | client_id, place_id, email, source_url, found_at | e-mail is from the business's own site, not Google |
| `outreach_suppression` | key_type (email/domain/phone/place_id), key_value, reason, source, requested_at, added_by, `requested_for` (text, NOT an integer client_id) | never deleted with a lead or client |
| `agent_actions` (existing) | drafts and send attempts as proposals | audit trail |

Client deletion clears the first two through schema discovery (integer `client_id`); the
suppression table survives by design.

## 11. What Phase 1 will build (after approval)

`core/places_service.py` (HTTP, field masks, caps, cost recording, attribution data),
`agents/prospecting_agent.py` (area analysis, scoring with reasons, drafts),
`core/outreach_policy.py` (opt-out line, suppression check, caps — pure, tested),
the migration for §10, `client_state.prospecting`, the four actions, a list panel with
Google attribution, and stdlib tests (field-mask tiers, no Google fields persisted,
suppression before send, send disabled, caps, opt-out enforced, robots.txt respected,
no guessed e-mails, cost recorded per client).

## 12. Decisions I need from you

1. **Billing project** for the Places key: `super-system-500410` or `plasma-canyon-229222`?
2. **Default radius / city scope** for area analysis (e.g. 2 km in a city, 10 km rural)?
3. Accept that the **page URL where an e-mail was found** is stored as evidence (§5)?
4. **Daily caps**: 60 Places requests per client / 600 global, 5 messages per client / 20
   global per day — OK?
5. **Credits model** (§8): option (a), (b) or (c)?
6. **Who sends** when sending is enabled: from the **client's** Gmail (they connect
   `gmail.send`) or from a uallak address on their behalf? This changes Q3 for the lawyer.
7. Should the client see prospect lists in their dashboard in Phase 1, or only through the
   chat and the Control Room?
