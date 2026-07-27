# HANDOFF — CRM: lead tracking with source attribution

A leads section inside the existing admin dashboard, plus the tracking that
makes it possible to answer "where did this lead come from".

## ⚠️ Run the migration BEFORE deploying

`migrations/2026-07-28-lead-tracking.sql`, in the Supabase SQL editor. Every
statement is idempotent. The code degrades rather than corrupts if you forget
(the CRM tab shows an empty state, `/api/lead/start` errors and the chat falls
back to the old proposal-time-only lead row) — but nothing works until it runs.

## What the investigation found (the reason this is shaped the way it is)

- A `leads` row was only created **when a proposal finished building**. Anyone
  who left mid-chat left no trace at all, so "dropped off" was not a status
  that could be computed — it was a row that never existed.
- **No UTM, click-id or referrer capture existed anywhere in the codebase.**
  Confirmed by grep, not assumed.
- `language` was already being sent to `/api/onboarding` and then thrown away.
- The sales-chat transcript lived **only in the browser DOM** —
  `downloadTranscript()` scrapes it off the screen because the chat has no
  account behind it. Nothing was ever sent to the server.
- `leads.client_id` did not exist in Supabase, and two code paths already
  assumed it did and silently swallowed the failure.
- Checkout linked a purchase to a lead by finding *the newest lead with an
  empty email*. Under two concurrent chats that attaches the sale to the wrong
  person — which is exactly the join an attribution CRM depends on.

## What now happens, end to end

1. **Arrival.** The chat page reads `utm_*`, `gclid`/`gbraid`/`wbraid`,
   `fbclid`, `ttclid`, `msclkid` and `document.referrer` off its own URL and
   POSTs them to `/api/lead/start`, which opens the lead row and returns a
   random `lead_id` the browser holds in `sessionStorage`.
2. **Conversation.** Every message rendered by `addMessage()` is mirrored to
   `/api/lead/message`, fire-and-forget. This is what makes a transcript and a
   real "last activity" timestamp exist.
3. **Proposal.** `/api/onboarding` receives the `lead_id` and folds the
   proposal into *that* row, so the source stays attached.
4. **Checkout.** `/api/checkout` receives the `lead_id` and links the lead to
   the client it became — exactly, by key.
5. **CRM.** `/api/admin/leads` renders the table; clicking a row opens the
   drawer with full attribution, the transcript, the answers, and a button
   through to the client record.

Every tracking call swallows its own errors. A blocked or failed tracking
request costs the attribution, never the conversation or the proposal.

## Source attribution — and how much each answer is worth

`core/lead_tracking.classify_source` reports a platform **and a confidence**,
and the UI always shows the confidence next to the platform. They are not the
same claim:

| confidence | meaning |
|---|---|
| `click_id` | The platform stamped its own click id on the URL. A paid click provably happened. |
| `utm` | A tagged link said so. Trusted, but anyone can type `utm_source=google` into a URL. |
| `referrer` | Inferred from where the browser came from. |
| `none` | Nothing to go on. Reported as `direct` — **not** guessed at. |

Historical leads keep `source_platform` NULL and render as "לא ידוע"
(unknown), not "direct". We genuinely do not know where those people came
from, and saying "direct" would be inventing attribution.

## Ad-level attribution — what is now automatic, and what isn't

Knowing "this came from Meta" is easy. Knowing *which ad* is not symmetric
across platforms, so this was solved at the write side rather than the read
side: our own agents now stamp tracking templates on the campaigns they
create (`GOOGLE_ADS_FINAL_URL_SUFFIX` / `META_URL_TAGS`, both defined in
`core/lead_tracking.py` so the scheme lives in one place).

- **Google** — `finalUrlSuffix` on the campaign; ValueTrack expands
  `{campaignid}`, `{creative}`, `{keyword}` at click time.
- **Meta** — `url_tags` on the ad creative; `{{campaign.name}}`,
  `{{ad.name}}`, `{{adset.name}}` expand at click time, and
  `{{site_source_name}}` distinguishes an Instagram placement from Facebook.
  This matters because **`fbclid` is not resolvable to a campaign through any
  API** — putting the answer in the URL is the only route.

**Three honest limits:**

1. **Existing campaigns are not retrofitted.** These apply from creation
   onward. Retrofitting means mutating live client campaigns, which is a human
   decision, not something a tracking change should do quietly.
2. **TikTok is not covered.** `tiktok_content_agent` publishes organic content;
   there is no TikTok *ads* campaign-creation path in the codebase to hook. A
   `ttclid` still classifies the lead as TikTok at platform level.
3. **Neither template has been run against a live ad account.** Same
   verification status as everything else shipped from this machine — written
   against the platforms' documented fields, never executed. Both are on
   campaign *creation*, so the first real campaign either agent builds is the
   test.

## The cross-domain gap you must close when WordPress goes live

The domain split put marketing on uallak.com and the chat on app.uallak.com.
**Campaign parameters do not survive that hop by themselves.** Without
forwarding, every paid lead arrives clean and records as `direct` — the
feature reports nothing useful for exactly the traffic we pay for.

- The app's own landing page is handled: `uallakGo()` in
  `dashboard/landing/index.html`.
- **The WordPress site is not, until you install
  `marketing-site/forward-campaign-params.html` site-wide.** Installation
  options and a verification procedure are in that file's header. This is the
  single highest-value follow-up item in this handoff.

## Statuses

`in_progress`, `converted`, `declined` are stored. **`dropped_off` is derived
at read time** from `last_activity_at` (6 hours, `DROP_OFF_HOURS`) and is
deliberately never written: a stored value would need a scheduled job to set
it, and would then be wrong the moment a prospect came back the next day.
Deriving it means returning prospects silently go back to `in_progress` with
no job at all. `declined` is the one an admin sets by hand, from the drawer.

## Files

| file | what |
|---|---|
| `core/lead_tracking.py` | attribution classification, lead lifecycle, CRM queries, the outbound tagging scheme |
| `migrations/2026-07-28-lead-tracking.sql` | schema + backfill (run first) |
| `core/api_server.py` | `/api/lead/start`, `/api/lead/message`, `/api/admin/leads*`; `lead_id` threaded through onboarding + checkout |
| `dashboard/onboarding/index.html` | `leadTracker` — capture on arrival, mirror messages |
| `dashboard/landing/index.html` | `uallakGo()` — carry params to the chat |
| `dashboard/admin/index.html` | the לידים tab, filters, and the lead drawer |
| `agents/google_ads_agent.py`, `agents/meta_ads_agent.py` | tracking templates at campaign creation |
| `marketing-site/forward-campaign-params.html` | the WordPress-side forwarding snippet |

## Deliberately not built (scope guardrail, per the brief)

No pipeline stages, no task reminders, no email sequencing, no lead scoring,
no editing lead data from the dashboard. This is a visibility tool.

## Known limits worth stating

- **`/api/lead/start` is public and unauthenticated**, like the sales-chat
  endpoints it sits beside — so it can be spammed into creating rows. It is a
  cheaper target than the existing unauthenticated endpoints that call Claude,
  so this does not open a new class of exposure, but there is no rate limiting
  anywhere in this codebase and that remains true here. Per-lead growth *is*
  bounded (`MAX_MESSAGES_PER_LEAD`, `MAX_MESSAGE_CHARS`).
- **Transcripts of people who never became clients are now stored.** The
  offboarding purge/archive logic covers `clients`, not `leads` — there is no
  retention policy on this data today. Worth a decision before volume grows.
- **Nothing here has been executed.** No Python on the dev machine; verified by
  reading. Smoke path after deploy: open `/chat/?utm_source=test&utm_medium=cpc`,
  send one message, then check the admin לידים tab for a lead reading "לפי
  תגיות הקישור בלבד" with a one-message transcript.
