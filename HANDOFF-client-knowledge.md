# HANDOFF — Client "brain": one consolidated knowledge read for the chats (2026-10-14)

Investigated first (sections 1-4), then built with Johnny's decisions:

- Financial answers are left out.
- Competitor research up to 90 days old is included, always dated, and treated as
  dated background.
- The upgrade-quote bug is fixed in a separate commit.

See "What was built" at the end.

## 1. Where client knowledge lives today

| # | Source | Where it lives | What's in it | Freshness | Who reads it today |
|---|---|---|---|---|---|
| 1 | Client row | `clients` | name, business_name, package label, status, language | Live | Everyone. The chat gets name/package/status. |
| 2 | Sales-chat answers | `leads.answers` (newest row matched by email) | `intro` (free-text description of the business), `business_age`, `financial_status`, `revenue_trend`, `recent_revenue`, `marketing_budget`, `existing_digital`, `main_goal`, `biggest_fear`, `media_management`, `camera_comfort`, `media_assets`, `content_creator_collab`, `organic_history`, plus dynamic questions | Frozen at sale time | seo/media/ads/landing agents via `seo_agent._business_context`, engagement weekly (`intro`, `main_goal`). **The chat never sees them.** |
| 3 | Proposal | `leads.proposal` | `business_summary`, `market_reality`, `goals_90_days`, `kpis`, `honest_note`, packages with prices | Frozen at sale time | The chat gets the **whole JSON**, unfiltered, every turn. Budget agent, competitor research. |
| 4 | Interview facts | `client_activity` `interview_agent/client_facts_captured`, read by `interview_agent.captured_facts()` (newest wins, last 20 rows) | `competitors` [{name, url}] and free-form `notes` (best customers, best sellers, seasonality, what flopped before, on-camera willingness) | Written once at first login, rarely updated | `competitor_research` reads **only the competitors**. **Nothing outside the interview itself reads `notes`.** The richest thing the client told us is write-only. |
| 5 | Competitor research | `client_activity` `competitor_research/competitor_research_completed`, one row per lens | Plain-text market summary per lens (ads / website / media), competitor lists, media lens EXAMPLES links | TTL 7 / 30 / 14 days. Refreshed only when a generating agent asks. After the TTL, `_cached()` returns nothing. | media/website/ads/landing agents. **The chat never sees it.** |
| 6 | SEO tool research | `client_activity` `seo_research_completed` | Real competitor domains (SEMrush/Ahrefs) or a Claude fallback summary | Whenever seo_agent last ran | `competitor_research._tool_competitor_domains`. **The SEO strategy routes to Johnny only and must stay out of client chats.** |
| 7 | Journey milestones | Derived by `client_journey.journey()` (no table) | signup, subscribed, first connection, all connected, website live, first campaign, first content, first lead, first won deal | Live (derived on read) | The dashboard timeline only |
| 8 | 90-day clock | `client_journey.connections_completed_at()` | The date the 90-day cycle started | Written once, permanent | Interview gate. **The chat doesn't know where the client is in their 90 days.** |
| 9 | Internal ads goal | `client_activity` `google_ads_agent/ads_goal_set` | The verified projected results for 90 days | Per goal cycle | google_ads_agent. **This is internal ("uallak's own target, not a promise made to the client") and must not go into a client-facing chat.** |
| 10 | Suggestions history | `client_suggestions` | Weekly ideas and homework, with pending / approved / rejected status | Weekly | The chat sees only **pending** ones. **What the client approved or turned down (what's been tried) is never shown.** |
| 11 | Existing-site assessment | `website_agent.latest_assessment()` | migrate vs rebuild, detected platform | Once | The website persona only |
| 12 | Recent activity | `client_activity` (last 15 rows) | Everything agents logged | Live | Every chat payload. It's noisy, and it's a 15-row window, so anything older drops out. |

### Things found on the way

1. **The in-chat upgrade proposal overwrites what every reader thinks the client bought.**
   - Everything picks "the newest `leads` row for this email": `support_agent._latest_lead`, `budget_agent._lead_row`, `seo_agent._business_context`, and through those, competitor research and `client_journey._chosen_package`.
   - `_build_upgrade_proposal` **inserts a new `leads` row** with the same email and an `answers._upgrade_request` marker.
   - After a client asks for an upgrade quote, every one of those readers sees the unpurchased upgrade proposal. That affects its `business_summary`, `goals_90_days`, `market_reality` and packages.
   - `_chosen_package` then looks for the checkout's `package_id` inside the upgrade proposal. It probably won't find it, so it returns `{}`. The connection checklist then fails closed, which means "can't tell what you bought".
   - This bug exists today and is independent of this handoff. The knowledge read has to avoid it, so I propose fixing it in the same change.
2. **Interview `notes` are write-only**, as the table says. The fix costs nothing because the data is already stored.
3. `competitor_research.research()` is **not** a pure read. On a cache miss it runs up to 3 paid web searches. A chat read path must never call it. It needs a cache-only reader.

## 2. Design: `core/client_knowledge.py`, a read-only assembly with no new storage

It follows the "one catalogue, many readers" pattern: the existing modules stay the producers, and this module is the single place a reader asks "what do we know about this business?".

```
profile(client_id, lens="general") -> dict      # never raises, no LLM, no paid calls, no writes
```

What it returns. Every section is optional and simply missing when its source is empty or its read failed. There is no gaps list and no error text, per the data-availability rule.

| Section | From | Notes |
|---|---|---|
| `business` | client row + **the sales lead** (#2, #3) | business_name, purchased package name and services (`client_journey._chosen_package`), business_summary, and their own sales-chat answers, labelled (intro, business_age, main_goal, biggest_fear, existing_digital, media_management, camera_comfort, organic_history, marketing_budget) |
| `in_their_words` | interview notes (#4) | The first reader they have ever had |
| `competitors` | interview-named (with links) + SEO-tool domains (#4, #6) | Merged and deduplicated. Client-named ones are marked as such. |
| `market` | proposal `market_reality` + **cached** competitor research for the lens (#3, #5) | The cache is read up to 90 days old, with `researched_at` shown so the model can say "as of". It never triggers research. The summary is truncated. |
| `plan` | proposal `goals_90_days` + `kpis`, cycle start, and day N of 90 (#3, #8) | Labelled as estimates, not guarantees, matching the sales-chat rule. **#9 (internal goal) is excluded.** |
| `history` | journey milestones that are done (#7) + approved/rejected suggestion titles (#10) + site assessment path (#11) | "What's been tried" |

The lens decides which competitor-research lens is included and how much: ads for google/meta, website for website, media for media, and a short version of all three for the general chat. It's the same lens idea competitor_research already uses.

**Cost:**
- About 7 Supabase reads.
- Cached in memory per (client, lens) for 5 minutes, like the platform getters.
- Capped at about 1.5k tokens. Each section has its own length limit, following the house rule that prompts state length limits.

**Fixing the upgrade-lead bug (part of this build):**
- Add one helper that returns the newest lead row that is **not** an upgrade proposal (no `answers._upgrade_request`).
- `support_agent._latest_lead` and `budget_agent._lead_row` both use it. Everything downstream (seo context, competitor research, `_chosen_package`) then gets the right row.
- The upgrade rows stay where they are, as the admin's record of quotes.

## 3. Feeding it into the chats

- Both `_answer_support_question` and `_answer_persona_question` add `payload["client_knowledge"] = client_knowledge.profile(client_id, lens=persona)`.
- One prompt paragraph is added to `SYSTEM` and `PERSONA_SYSTEM_TEMPLATE`:
  - `client_knowledge` is what we already know about their business. Use it to be specific: name their real competitors, their goal, and what they said about their customers.
  - Never re-ask something it already contains.
  - It's background, not live metrics; numbers about their campaigns come only from the live platform fields.
  - 90-day goals are estimates, never promises.
- The concierge payload currently ships the **whole proposal**, including every package's price breakdown. It keeps the purchased package (needed for package and pricing questions) and drops the duplicated narrative fields now in `client_knowledge`. That makes the prompt smaller, not just bigger.
- **The data-availability fix is untouched:**
  - `data_topic` still covers only the three live platform fields.
  - The knowledge profile has no topic and no failure state.
  - A source that can't be read is left out, exactly like a failed platform read.
- **The read-only invariant holds.** `profile()` only reads, and the personas still can't act. Writes stay with their owners: the interview writes facts, competitor_research writes research.

Optional later: the admin Control Room's `_grounding` could include the same profile, so Johnny's view and the client's chat agree. It would add the internal goal (#9) on the admin side only.

## 4. Does it need new storage?

**No.** Every section is already stored or derived. What this adds:

- one new module (`core/client_knowledge.py`)
- a cache-only reader in `competitor_research` (`latest(client_id, lens, max_age_days)`) that never researches
- the sales-lead helper (bug fix)
- prompt and payload changes in `support_agent`

No migration and no new table.

**Size:** about half a day to a day.

## What was built

### Commit 1 (`4ec04f7`): upgrade quotes no longer read as "what they bought"

`core/lead_tracking.client_sales_lead(client_id=, email=, columns=)` is now the one lookup.

**How it works:**
- It reads the client's newest 20 `leads` rows, newest first, and returns the first row
  that is **not** an upgrade quote.
- An upgrade quote is recognized by the existing `_is_upgrade_row` test:
  `answers._upgrade_request` is set. That's the marker `_build_upgrade_proposal` writes,
  and the admin CRM already uses the same test to hide those rows.
- It matches on `client_id` first, then on email. **Both** passes skip upgrade rows.
- It filters in Python rather than in the query, for the reason `list_leads` gives: a
  filter on the `answers->>_upgrade_request` field can't be verified from this machine.
- It always selects `answers`, even when the caller asks for other columns, so the marker
  can be checked.

**Lookups now routed through it:**
- `support_agent._latest_lead(email)`. This covers:
  - both chat paths (the concierge and the personas)
  - the source answers for an upgrade build
  - `seo_agent._business_context`, and through it competitor research, media, the ads
    agents, landing pages and the avatar agent
  - `competitor_research._market_reality`
  - the Control Room's proposal read
- `budget_agent._lead_row(client_id)`. This covers the budget forecast and
  `client_journey._chosen_package`.
- `engagement_agent._client_context`, the weekly suggestions. It had its own copy of the
  query.

Only these three places queried `leads` by client. The other `leads` queries are the
sales chat's own lifecycle (by `lead_id`), the admin list, and the monitor scan.

**Connection checklist and 90-day clock:**
- Both read the package through `_chosen_package` → `_lead_row` → now
  `client_sales_lead`. `connection_status` → `required_connections` → `_chosen_package`
  now always looks for the checkout's `package_id` in the proposal the client actually
  bought.
- **The checklist:** fixed. With an upgrade quote picked, `package_id` wasn't among the
  quote's packages, so the result was `{}` and `resolved: false`. The checklist showed
  "can't tell what you bought" and never completed.
- **The deep-interview gate (`interview_agent.readiness`):** fixed, because it reads
  `connection_status`.
- **The 90-day clock stamp (`note_connections_complete`):** fixed. It only stamps when the
  status is `resolved` and `complete`, so an affected client's clock could never start.
  An already-stamped date is permanent and unchanged.

**How widespread it was:**
- The `client_id` pass already skipped *new* upgrade quotes, because
  `_build_upgrade_proposal` never sets `leads.client_id`. So `_lead_row`, and with it the
  checklist and the clock, was wrong only in two cases:
  - the client's sales lead was matched by email; or
  - the 2026-07-28 lead-tracking migration back-filled `client_id` onto upgrade rows that
    already existed. It set `client_id` on every row with the client's email.
- `support_agent._latest_lead` and the engagement query were email-only, so they were
  wrong for **every** client who had asked for a quote.

### Commit 2: the knowledge module

- **`core/client_knowledge.py`.** `profile(client_id, persona_id)` returns the sections
  designed above:
  - `business`, `in_their_words`, `competitors`, `market`, `plan` and `history`
  - In-process cache of 5 minutes.
  - Each source is read separately; a failed or empty one is left out without any trace.
  - In `plan`, a failed read of the 90-day stamp says nothing. It never says "not
    started".
  - Sales-chat answers: the base-question allowlist, plus up to 8 dynamic answers. Contact
    keys, `_`-prefixed keys and anything whose key looks financial are skipped
    (revenue / profit / financ / income / loss / debt / turnover / margin / salary / cash).
  - `financial_status`, `revenue_trend` and `recent_revenue` are always excluded.
- **`competitor_research.latest(client_id, lens, max_age_days=90)`.** A cache-only reader
  that never calls `research()`.
- **`support_agent`:**
  - Both chat paths send `client_knowledge`. The specialists get their own research lens
    in full: ads for google/meta, website, media. The concierge gets a short slice of all
    three. The key is left out when it's empty.
  - `KNOWLEDGE_RULE` is shared by `SYSTEM` and `PERSONA_SYSTEM_TEMPLATE`. It tells the
    model to:
    - be specific and never re-ask what's already known;
    - treat it as background, not live metrics;
    - treat research as **dated background, framed as "when we last looked (date)", never
      as how things stand today**;
    - treat goals as estimates;
    - never invent anything missing;
    - leave `data_topic` as "" for questions about it.
  - The raw `proposal` in the chat payload no longer duplicates `business_summary`,
    `market_reality`, `goals_90_days` and `kpis`.
- **Unchanged:**
  - `data_topic`, the "can't pull that up" logic, and the read-only invariant of the
    personas.
  - `ads_goal_set` and the SEO strategy are not read.
  - No failure state enters the prompt.

Not run: there's no Python on this machine, so the code was reviewed by reading only.

**Verify after deploy:**
- A client who did the first-login interview: ask the concierge "who are my competitors?"
  and expect the names they gave.
- Ask "what are we trying to achieve?" and expect the goals as estimates plus the day N of
  90.
- Ask the Meta persona about the market and expect the ads-lens research, framed with its
  date.
- A client who has asked for an upgrade quote: the connection checklist resolves, and the
  chat still describes the package they bought.

## Decisions for Johnny before building (resolved: see the top)

1. **Financial answers** (`financial_status`, `revenue_trend`, `recent_revenue`): include them or not?
   - Proposed: **leave them out** of the chat profile.
   - The client did tell us, but a chat that says "since you're losing money…" is a tone risk with little upside. `marketing_budget` stays in.
2. **Stale competitor research:** show research up to 90 days old, with its date (proposed), or only within the producer's TTL?
3. **Fix the upgrade-lead bug in the same change?** Proposed: yes. Without the fix, the profile would be wrong for any client who has asked for a quote.
