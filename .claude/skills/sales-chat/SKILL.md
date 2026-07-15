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
- **Estimates, not commitments**: every goals_90_days/kpis number is a range/approximation
  ("כ-40-55"), never an exact promised figure — protects against "you promised 300" cancel
  claims. Enforced in the build prompt AND QA criterion 13.
- **No position labels on questions**: the dynamic-questions prompt forbids "שאלה אחרונה" /
  numbering — flow length varies, labels read as broken scripting. This was never hardcoded;
  it was LLM-generated, so the fix lives in the prompt.

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
- Business/pricing rules live ONLY in `PRICING` + build_proposal's prompt (CLAUDE.md rule).

## Latency guardrails

Full pipeline target < 2 minutes; response LENGTH is the main driver. Every prompt carries
hard output-length limits — keep them when editing, and never add a sequential LLM round-trip
to `run_full_onboarding` without asking whether it can run in parallel or merge into an
existing call (the empathy reuse and the merged QA review both exist for this reason).
