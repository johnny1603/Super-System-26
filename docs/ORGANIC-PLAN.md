# Organic in the professional order, and goals with a dated baseline (2026-11-16)

Code: `core/organic_plan.py`, `core/organic_plan_actions.py`, `core/quarter_goals.py` (baseline + monthly
report), `core/quarter_goals_actions.py` (the goals card), `core/dataforseo_connector.py` (the switch).
Tests: `tests/test_organic_plan.py`, `PaidConnector` in `tests/test_seo_report.py`.

## The order

No keyword is proposed in conversation before research. This is a prompt rule in `support_agent.STUDIO_RULE`.
The client's own phrases from the sales chat reach the model only as `client_phrases_for_research_only`.

| # | Step | How | Client click |
|---|------|-----|--------------|
| 1 | Current state | The free organic report (`seo_report`), plus the market-research competitors. Positions only where a tracking source measured them, otherwise "not measured yet". | `advance_organic_plan` |
| 2 | Keyword research | The client's phrases, Google's related searches (one web search) and competitor terms. Each has intent, volume and competition. | same click |
| 3 | The choice | 1-5 phrases taken **only** from the researched candidates. Only these are measured. | `approve_organic_keywords` |
| 4 | Backlinks | The earned-link opportunities from the report. Real referring domains and the competitor link gap when real data is on. | same click |
| 5 | Strategy | Up to 6 steps (what, in what order and why), plus a time estimate. | same click |
| — | Goals | `agree_quarter_goals`, offered only at stage `ready` for an organic package. Its keywords are the approved set and it shows the estimate and the terms. | the goals click |

The time estimate:
- **internal** is the model's honest estimate in months;
- **committed** is internal × 1.15, rounded **up** to the half month (4 → 5, 3 → 3.5).

Both are labelled estimates. A line that promises a ranking, a position or "first page" is removed in code.

## The baseline and the monthly report

The goals click starts the 3-month clock (`agreed_at`) and saves `baseline`. Each figure in it is a
measurement, or None, which is shown as "not measured yet":
- `captured_at`;
- `leads_30d` (client_leads);
- `sales_30d` (net store revenue, when a store is connected);
- `visitors_30d` (Analytics, round 2, currently off);
- `positions` of the agreed keywords (rank tracking, Search Console, currently off).

The home goal path shows the starting point and the estimate. Each month (days 30, 60 and 90), the
first visit of the client to home posts ONE report into the manager's chat.
- **Content:** leads now vs. the start, and each agreed keyword now vs. the start, on the agreed
  keywords and metric only.
- **Order:** the month is stored before the post, so a retry never posts twice. Staff viewing
  never trigger it.
- **No scheduler job and no Cloud Run change:** a client who never opens home gets the report on
  their next visit.

## What our Google Ads access permits

The developer token is at **Explorer access** (`google_ads_service`: 2,880 ops/day). Google's access-level
page lists **KeywordPlanIdeaService as restricted** at that level, along with KeywordPlanService and the
other planning services. So today:
- **no keyword ideas and no search volumes from Google Ads**, for any client;
- nothing was requested and no scope was added.

Keyword Planner needs **Basic access**, which is an application to Google (brand verification first), not
a paid service. It is the owner's decision whether to apply. Until then, volume and competition are
judgment-based tiers, and the client sees that line.

## DataForSEO: the switch (OFF)

Turning it on is a setting plus the owner's own login. Both are required:
1. the admin setting `dataforseo_enabled` (default **False**, `admin_service.DEFAULT_SETTINGS`);
2. the env vars `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`.
   - Added by the owner (Secret Manager → Cloud Run).
   - Not registered in `keys_agent`, so startup never asks for them.
   - Not created by us.

While either is missing, every connector function raises `Disabled` before any request. Callers check
`enabled()` first (a test enforces it), and the work is labelled judgment-based.

When on, it adds:
- real monthly searches and competition (Israel) for the candidates;
- the site's referring-domain totals;
- the competitors' link gap.

It is a paid service, so turning it on is the owner's call.

## The commitment: where it appears, and what is missing

The promise is "if we don't hit the target, no management fee". It appears in exactly two places:
- `dashboard/landing/index.html`: the "commit" section (lines ~913 and ~966) and the i18n key
  `commit_h2` (~1301, ~1313), in 5 languages;
- now also, in measurement terms only, on the `agree_quarter_goals` card (`TERMS` in
  `core/quarter_goals_actions.py`). It says how we measure, against what and how often, and that
  the commitment is measured on these only "under the service terms".

What is missing:
- **The terms of service (`dashboard/terms/`) do not mention it.** §6 says cancellation gives no refund
  for a paid period.
- The sales-chat proposal and the report email do not state it either.
- The public promise and the legal text therefore disagree. This is a lawyer's decision; nothing was
  changed (the landing and terms pages are frozen until Google answers).

Proposed wording for the lawyer to work from. This is not legal text:
- **What is measured:** the goals agreed on the client's dashboard (the "agree goals" approval, with its
  date), and only the keywords and the metric listed there.
- **Against what:** the starting point saved automatically on that date and shown on the dashboard.
- **When:** at the end of the committed period shown on the card (an estimate given at agreement, already
  including a buffer).
- **What counts as missed:** state the test plainly, for example "the agreed metric did not improve
  against the starting point". Decide whether rankings count at all. We recommend they do not, because
  no one controls Google's rankings.
- **What is returned:** the management fee for which months, and how it is requested.
- **What suspends it:** e.g. the client removed our access, did not approve the planned work, or changed
  the site outside the plan.

## Not tested against a real model or client

- The quality of the keyword research and the strategy.
- How the real model follows "no keyword before research".
- The monthly post on a real account.
