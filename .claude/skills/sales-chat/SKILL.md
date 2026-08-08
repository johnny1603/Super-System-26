---
name: sales-chat
description: How uallak's sales chat + proposal pipeline works — question flow (base/dynamic/conditional), new-business branching, market-reality reasoning, the estimates-not-guarantees rule, and which files must stay in sync. Use when touching agents/onboarding_agent.py, agents/question_filter.py, agents/qa_agent_content.py, agents/empathy_agent.py, or dashboard/onboarding/index.html.
---

# Sales chat + proposal pipeline

## Pipeline order (one full run)

1. Client types a free-text **intro** (`answers.intro`) in `dashboard/onboarding/index.html`.
2. Frontend calls `/api/filter-questions` (`question_filter.get_skip_ids`) — drops base
   questions the intro already answered, AND drops past-oriented questions
   (`revenue_trend`, `recent_revenue`, `biggest_fear`) when the intro clearly shows the
   business hasn't started operating yet.
3. Frontend calls `/api/dynamic-questions` (`get_dynamic_questions`) — 4-6 personalized
   questions spliced in RIGHT AFTER the intro, BEFORE the remaining base questions.
   **2 and 3 are fired CONCURRENTLY** (`Promise.all` in `loadDynamicQuestions`) — neither
   reads the other's output. They are still APPLIED in this order: filter removes base
   questions, then the dynamic ones splice in at `currentQ`. This is the client's longest
   wait in the chat, so keep it that way; don't re-serialize them.
4. Remaining base questions run, with client-side `applyConditionalLogic` splices:
   - `financial_status` startsWith "עסק חדש" → removes the past-oriented trio and inserts
     `new_business_expectations` + `new_business_concern` (forward-looking).
   - `organic_interest` affirmative → inserts `organic_budget`.
   - `media_management` self-managed + budget → automation excitement info message.
5. `/api/onboarding` → `run_full_onboarding`: empathy analysis (ONCE, reused — never add a
   second call), `build_proposal`, `qa_check` (numeric, no LLM), `review_and_fix_proposal`
   (content QA — the corrected proposal is what ships).
6. Frontend shows summary / `market_reality` / risk / goals / packages; free text during
   package selection goes to `/api/handle-objection`.

## The intelligence rules (encoded in build_proposal's prompt)

- **Market expertise**: uses Claude's OWN general knowledge of Israeli industry benchmarks
  (CPL/CPC ranges, competition) stated confidently as round ranges — NOT live Google/Meta
  API data. At proposal time the client hasn't paid or connected anything; live platform
  data belongs to the execution agents later. Don't "fix" this by wiring platform APIs in.
- **`market_reality` field** (Hebrew, 2-4 sentences): competitive picture + benchmark range +
  honest budget-vs-goal math ("300 לידים ב-5,000 ₪ לא ריאלי; ריאלי: 40-55"). Shown in the
  chat proposal, in the client report email, and QA-checked (criterion 14).
- **Maturity judgment**: established practice → organic SEO is a real long-term asset;
  brand-new/fragile business → don't push 6-month organic payoff even if budget clears the
  threshold.
- **Thin budget vs competitive market**: recommend real alternatives (niche portals,
  short-form video, social growth, testimonial videos if tenured) instead of a token paid
  campaign.
- **Camera coaching**: `camera_comfort` answer gates script + on-camera coaching offers —
  covered by monthly content work, never a separate fee line.
- **Organic SEO has NO budget floor** (changed 2026-08-08): a `min_monthly_budget_to_recommend`
  of 3,000 NIS used to sit in `PRICING["seo_tiers"]` and made the chat tell under-budget clients
  their organic budget was below the threshold. Removed everywhere — prompt, QA criterion, admin
  pricing screen. The tiers now only pick WHICH tool (SEOptimer / SEMrush / Ahrefs) a budget can
  carry; scope shrinks with the budget, the service is always offered. Don't reintroduce a floor.
- **`cost_disclaimers` replaced `self_help_tips`** (2026-08-08): the client-facing "things you can
  do yourself" list is gone from the chat and the report email; in its place is a short factual
  list of what the client pays for BEYOND uallak's fees — ad spend, their own Higgsfield
  subscription, HeyGen/ElevenLabs for the avatar add-on, the domain on a new-site package. The
  **organic SEO tool is the stated exception**: it comes out of the organic budget and is NOT an
  extra payment, and the copy must make that contrast explicit. Site hosting is not in this list
  either — it is already a `monthly_management_total` line. QA criterion 9 enforces the whole set;
  `_enforce_invariants` strips any leftover `self_help_tips` and guarantees the field is a list.
- **`goals_90_days` is mostly CONCRETE ACTIONS**, not projections: scheduled/automatic post
  publishing, campaign launch + ongoing optimization, the research/audit deliverable, article and
  keyword work, lead capture, weekly reporting. 5-7 items, at most 1-2 pure projections. Marketing
  automation (ManyChat/Make) may be named here as a POSSIBILITY only — never priced, never in
  `recommended_services` or any total (see `HANDOFF-marketing-automation.md`).
- **Estimates, not commitments**: every goals_90_days/kpis number is a range/approximation
  ("כ-40-55"), never an exact promised figure — protects against "you promised 300" cancel
  claims. Enforced in the build prompt AND QA criterion 13.
- **No position labels on questions**: the dynamic-questions prompt forbids "שאלה אחרונה" /
  numbering — flow length varies, labels read as broken scripting. This was never hardcoded;
  it was LLM-generated, so the fix lives in the prompt.
- **Avatar/digital-twin add-on** (wired 2026-07-20, BUDGET PYRAMID #9): PRICING["avatar"] is no
  longer excluded from the prompt payload. Offered ONLY when genuinely relevant (personal-brand
  services, or a client already comfortable on camera per `camera_comfort`) — never a default
  line. Setup fee stacks on the setup floor; the recommended monthly tier is its own
  monthly_breakdown line INCLUDED in monthly_management_total (same treatment as the new-site
  hosting line — the one exception to "no non-platform monthly lines"). An explicit client
  request (sales chat OR support_agent's upgrade path) always overrides the relevance filter —
  see the avatar skill for the full pricing/consent picture.

## build_proposal has an EXISTING-CLIENT UPGRADE MODE

The support chat (agents/support_agent.py) reuses `build_proposal` for in-chat upgrade
proposals via the optional `upgrade_context` parameter — it appends an override block to the
prompt (new TOTAL configuration, setup fee = only genuinely new one-time work, empty
scarcity_note, next-billing-cycle honest_note). `upgrade_context=None` keeps the onboarding
path byte-identical. When changing build_proposal's rules, check the upgrade block still
makes sense; upgrade proposals run numeric `qa_check` but deliberately SKIP the content-QA
LLM pass (chat latency) and are recorded as lead rows with `_upgrade_request` in answers.

## Files that must stay in sync (change one → check the others)

- Question IDs: frontend `BASE_QUESTIONS` ↔ `question_filter.BASE_QUESTION_IDS` ↔ any prompt
  that references answer keys by name (`build_proposal` reads `organic_interest`,
  `organic_budget`, `camera_comfort`, `marketing_budget`, `financial_status`).
- Proposal JSON shape: `build_proposal` output ↔ frontend `displayProposal` ↔
  `send_client_report` email ↔ `qa_agent_content` criteria ↔ `qa_agent.qa_check` numeric rules.
  Renaming or adding a client-facing field means touching all five — `cost_disclaimers` needed
  the prompt, the JSON schema, the chat renderer + its i18n key, the email template + its i18n
  key, and two QA criteria plus `_enforce_invariants`.
- Business/pricing rules live ONLY in `PRICING` + build_proposal's prompt (CLAUDE.md rule).

## Latency guardrails

Full pipeline target < 2 minutes; response LENGTH is the main driver. Every prompt carries
hard output-length limits — keep them when editing, and never add a sequential LLM round-trip
to `run_full_onboarding` without asking whether it can run in parallel or merge into an
existing call (the empathy reuse and the merged QA review both exist for this reason).

**Measured 2026-08-08** against the live service, first turn after the intro: filter 3.9s,
dynamic questions 26.6s, 30.4s total sequential. It was not a cold start, a blocking call or
a missing cache — the questions call is almost entirely output-token generation (1,550 chars
of Hebrew JSON). Hence the two fixes: run the pair concurrently, and hard-cap the generated
question/option lengths in `get_dynamic_questions`'s prompt. If this ever regresses, measure
before theorising — the two endpoints are public, so a plain POST times them.
