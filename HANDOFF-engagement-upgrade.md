# First-login interview, login moments, progress journey (2026-08-02)

Commit 1 of 2. Investigation covered all six parts of the handoff; this commit
builds the foundations plus the parts that were pure wins. Commit 2 is listed
at the bottom with what it depends on.

## Investigation — what the engagement engine already did

`agents/engagement_agent.py` had three jobs: **weekly** LLM-generated
suggestions → `client_suggestions` pending rows; a **daily** conversions →
*email* sales alert; **urgent** WhatsApp SOS. Two facts shaped everything here:

1. The *suggestions* are genuinely LLM-authored, but **every chat push in the
   file is a templated f-string**.
2. There was **no login-triggered path at all** — all three jobs are cron.

So the handoff's "dynamic, not robotic" requirement wasn't a matter of
replacing templates; it needed a new *reactive* job, which is what got built.

### Everything else that already existed

| Part | Found |
|---|---|
| 1 | Static 5-step `welcome-modal` tour; `welcome_tour_completed` activity row is the first-login signal; 4 read-only specialist personas with per-thread channels |
| 2 | `client_suggestions` (kind `homework`), `client_leads.source/source_detail`, media/article activity rows — all the raw facts, no consumer |
| 2.4 | **Nothing.** No feedback store anywhere, including `admin_service` |
| 3 | Rich derivable data: `client_activity`, `clients.created_at`, `client_accounts`, `client_leads`, PayPal rows |
| 4 | `client_accounts` active rows = the connections signal |
| 6 | `create_filming_kit` **already does confidence coaching**; `avatar_agent` exists as a paid add-on |

## What was built

### Part 1 — first-login interview (`agents/interview_agent.py`)

Replaces the static tour as the first thing that happens. The tour markup stays
and the "?" button still replays it — it just isn't the opening move.

- **Runs inside the existing general chat window**, not a screen of its own.
  Its turns are ordinary `dashboard_chat` messages, so when the interview ends
  the client's first conversation is simply the top of their chat history
  rather than something that vanished.
- Two jobs at once, and the second never blocks the first: answer whatever they
  ask about the platform, *and* gather what only they know — competitor names
  **with links** above all.
- **Persona routing** reuses `support_agent.PERSONAS` rather than defining a
  second cast; each turn says which specialist is speaking. The specialists
  stay read-only — this agent writes the facts, personas never do.
- Carries the **90-day urgency** (Part 4's dependency, deliberately built
  here): the clock starts only when every integration is connected, so
  connecting today means starting today. Framed as a reason to act, never as a
  deadline or a penalty.
- An LLM failure falls back to a warm message rather than a dead first minute.

### Where captured facts go — the rule that mattered most

Into `client_activity`, read back by **`core/competitor_research.py`**, which
is already the single place the media/website/ads agents get market context
(and already folds in the paid SEO tool's competitor domains). **No new table,
no new silo.** The lens prompts now rank `competitors_named_by_client` above
everything else, because the business owner naming who they lose deals to —
with links — beats both the sales chat (asked while selling them) and keyword
overlap.

### Part 2 — login moments (`engagement_agent.run_login_moment`)

Code evaluates triggers; **one** LLM call writes **one** message. Code decides
*whether* to speak so the model can't invent a reason to; the model decides
*how* so it doesn't calcify into a daily template. Deduped once per client per
day, which is also the spend cap.

Covered here: **2.1** time-of-day greeting (Israel clock, 5 languages),
**2.2** pending items asked about *by name*, **2.4** weekly satisfaction ask +
the new `client_feedback` store, **2.6** delivered work acknowledged
*specifically* ("well done" with nothing attached is worse than silence).

### Part 3 — progress journey (`core/client_journey.py` + dashboard)

Every milestone is **derived at read time** from rows that already exist. There
is no `milestones` table and there must not be one — a parallel tracker would
drift from the activity log the moment an agent changed, invisibly. Same
reasoning as `lead_tracking.dropped_off`.

Ten milestones: signup → subscription → first connection → all connected →
website live → first campaign → first content → first lead → first sale.
`connection_status()` is split out here rather than living in the dashboard
endpoint, so Part 4's cycle logic and this timeline read ONE definition of
"fully connected".

### Part 6 — camera confidence vs avatar

No new code path: the media persona (ליאור) already owns this conversation and
`create_filming_kit` already does confidence coaching. What was missing was the
*instruction* and the *facts*. `_media_reads` now also returns whether the
client already has an avatar and what one costs (pure reads — the persona still
cannot create or buy anything), and the persona is told to encourage first,
then present the avatar as a real second option in the same breath, never as a
consolation prize, and never to re-pitch it to someone who already has one.

## Verified in a real browser

Served `dashboard/` over localhost and loaded it in Chrome:
- No syntax errors — every function (`loadJourney`, `runLoginMoment`,
  `startInterview`, `interviewTurn`, `sendInWindow`, `showView`, …) is defined.
  This is the exact failure mode that produced last session's dead nav button.
- Fed `loadJourney` the real payload shape: 10 steps rendered, 6 marked done,
  bar at 60%, "6 מתוך 10", RTL fill direction correct, overflow scrolls inside
  the track rather than the page body.
- Console clean apart from the three pre-existing "no API backend" harness
  errors (`loadDashboard`/`loadBilling`/`loadSuggestions`).

The Python side is **unrun** — no usable interpreter here, as always.

## Setup

1. `migrations/2026-08-02-client-feedback.sql` in the Supabase SQL editor.
   Until then the weekly ask still happens and `store_feedback` alerts with the
   client's verbatim words instead of dropping them.
2. Nothing else. No new env vars, no new secrets, no new vendor.

## Commit 2 — what's left, and what it depends on

- **Part 4, 90-day goal cycles.** `client_journey.connection_status()` is
  ready and is the intended trigger, but note its `expectation_inferred` flag:
  without an explicit expected-integrations list it *guesses* what a package
  needs, and a wrong guess would start a 90-day promise early. That list has to
  come from the package/proposal before the cycle can be gated on it.
- **Part 5, creator/video reference research.** Extends the
  `competitor_research` "media" lens to return creator names and example video
  links, delivered through chat.
- **Part 2.3, organic vs paid.** Genuinely blocked today and the reason the
  login-moment prompt is forbidden from claiming a traffic source:
  `client_leads.source` is a *channel*, and the capture form never records
  `utm_*`/`gclid`/`fbclid`. Closing this means adding attribution capture to
  both the landing-page form and the WordPress injected form, then storing it.
- **Part 2.5, SEO page-1 milestone — BLOCKED, not merely unbuilt.** There is no
  rank *tracking*: no stored history, no tracked-keyword concept, no milestone
  detection. What exists is a point-in-time `position` read from SEMrush/Ahrefs,
  which needs a client-paid API key the project doesn't have. The seam
  (store position history from `run_market_research`'s cache, detect crossings
  into the top 10) can be built and will stay dormant until a key exists — the
  same "works once connected" pattern as the ads drafting.
