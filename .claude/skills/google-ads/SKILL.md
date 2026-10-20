---
name: google-ads
description: How uallak's Google Ads integration works — OAuth client-connect flow, REST/GAQL patterns, token storage, rate limits, and the gotchas that waste sessions. Use when touching agents/google_ads_agent.py, core/google_ads_service.py, the OAuth endpoints, or when adding any new platform OAuth integration (the same patterns apply).
---

# Google Ads integration (and the template for future platform OAuth)

## Auth model — one decision, three credentials

Each client connects THEIR OWN Google Ads account via OAuth2 from the dashboard
("Connect Now" → `/api/oauth/google-ads/start` → Google consent →
`/api/oauth/google-ads/callback`). Every API call then carries three credentials:

1. **Developer token** (`GOOGLE_ADS_DEVELOPER_TOKEN`) — ours, one for the whole system
2. **OAuth client id/secret** (`GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`)
   — ours, a "Web application" OAuth client in Google Cloud Console
3. **Refresh token** — the client's, stored per-client in Supabase
   `client_accounts` (`platform='google_ads'`, `account_id`=customer ID,
   `access_token` column holds the REFRESH token)

**Service accounts do NOT work with the Google Ads API** except via Workspace
domain-wide delegation, which can't reach a client-owned ad account — so there is
no service-account path here and one must not be built. A vestigial
`GOOGLE_ADS_SERVICE_ACCOUNT_JSON` key was registered in keys_agent (never read by
anything) and was **removed on 2026-08-20**, env var and key deleted. If you see
it referenced anywhere, it's stale. **Do not confuse it with
`GOOGLE_SERVICE_ACCOUNT_JSON`** (no "ADS") — that one is LIVE and unrelated:
`core/drive_service.py` uses it for the offboarded-client Drive archive and the
media agent's Drive folders. Dev-testing uses the same OAuth flow against a
Google Ads *test* account.

## THE OPTIMIZATION DOCTRINE — read this before changing anything in this agent

This is the complete decision framework the agent follows on its own. It is the
source of truth: if code and this section disagree, one of them is a bug. Work on
this agent starts here rather than being re-explained.

### What the Google Ads API actually permits (these constraints SHAPED the doctrine)

Three findings from the API investigation are load-bearing — the doctrine is
built around them, so don't "fix" the design without re-checking these:

1. **There are no ad-group or keyword budgets.** Budget exists only at CAMPAIGN
   level. "Reallocate budget between ad groups" is not a thing that can be built.
   Reallocation therefore requires MULTIPLE CAMPAIGNS — which is the entire
   reason State 2 exists.
2. **Maximize Clicks (`targetSpend`) sets bids automatically.** `cpcBidMicros`
   does not apply to it; the only bid lever is `cpcBidCeilingMicros` (a ceiling,
   not a bid). Per-keyword bidding would require Manual CPC, which this system
   deliberately refuses — see State 1.
3. **RSA headlines/descriptions are effectively immutable.** Copy iteration is
   create-new-ad + retire-old, never an in-place text update. Every new ad enters
   Google's policy review.

### STATE 0 — Onboarding / goal-setting

- The client's **fixed monthly budget** is explicit human input, captured at
  onboarding. **It is the only number a human supplies for the life of the
  campaign**, and it is never inferred.
- The system does the groundwork itself: `core/competitor_research.py` `"ads"`
  lens + market data → a 90-day performance goal.
- **The goal is self-verified before it is ever shown.** Cross-check the
  projection against the gathered research and a realistic cost-per-result for
  the client's industry; the arithmetic must survive its own sanity check
  (budget ÷ plausible CPA). Same rigor as `qa_agent_content` on proposals. A
  number that fails verification is not presented — it is recomputed or the
  goal is withheld and Johnny is alerted.
- **Client-facing framing stays an ESTIMATE**, per the standing
  estimates-not-guarantees rule (`proposal_goals` i18n: "הערכות מבוססות ניסיון,
  לא הבטחות מדויקות"). The committed number is an internal optimization target
  and the 90-day review baseline — not a promise made to an Israeli SMB. Changing
  that is a legal decision, not an engineering one.

### STATE 1 — Default: single campaign, Maximize Clicks

Every new client starts here and most will stay here.

**Automated bidding is deliberate and is not to be "upgraded" to Manual CPC.**
A fresh SMB account has no conversion tracking configured, so manual bids would
be guessed blind — precisely what this system's design avoids. Maximize Clicks
lets Google set bids from real auction data we do not have.

Autonomous in State 1, **no per-action approval**:
- Keyword optimization — pause underperformers, add winners
- Ad copy iteration — generate and test new RSA variants
- Campaign daily budget, **within the approved monthly total**
- Bid ceiling (`cpcBidCeilingMicros`)

Not available in State 1, by construction: per-keyword bids (automated strategy)
and budget reallocation (only one campaign exists).

### STATE 2 — Multi-campaign, triggered by real conversion volume

**Transition trigger (recommended default — see the threshold rationale below):**
all three must hold on a daily-scan evaluation:
- **≥ 30 conversions in a rolling 30-day window**
- **campaign age ≥ 45 days** (don't restructure something still finding its feet)
- **no restructure already performed in the current 90-day goal cycle** (churn guard)

On trigger the system **automatically** splits the account into multiple
campaigns along whatever the client's business naturally divides by (product
line, service, audience segment), **capped at 3 campaigns initially** so each
inherits ≥ ~10 conversions/month of signal rather than noise.

**Bidding stays Maximize Clicks.** Conversion volume sufficient to justify
splitting structure is NOT the same bar as trusting manual bids; that is a
separate, higher bar and is out of scope.

State 2 unlocks the real lever: **budget reallocation between campaigns**,
shifting spend toward whichever converts better — still strictly inside the same
already-approved monthly total. No new human approval for the restructure or for
subsequent reallocation.

### STATE 3 — Ongoing autonomous operation (States 1 and 2 alike)

- Runs inside the existing daily health scan (`run_health_scan`), after the
  compliance and eligibility steps.
- **Every action is logged with what changed and why** — an `ads_optimization`
  activity row per action, forming a full audit trail Johnny can read at any
  time. This is visibility, **not** a pre-approval gate.

### HARD GUARDRAILS — never automated, in any state

1. **Raising the total monthly budget.** The system never lifts its own ceiling,
   even when it believes more spend would hit the goal. It may recommend an
   increase to Johnny; it may never execute one.
2. **Policy-compliance auto-pause** on a Google ad disapproval — unchanged from
   the existing implementation. Compliance, not a business decision.
3. **Initial campaign activation** (PAUSED → ENABLED at creation) stays a human
   step, unchanged.

### 90-day checkpoint

At the 90-day mark, actual results are compared against the State 0 goal and fed
into the existing 90-day goal-cycle infrastructure. A **review point, not a new
approval gate** — optimization keeps running through it.

### Threshold rationale (why 30 / 45 / once-per-cycle)

30 conversions per 30 days is the long-standing industry anchor for "enough
conversion signal to act on" and is the level below which reallocation is
chasing noise. Splitting at that floor with a 3-campaign cap keeps roughly 10
conversions/month/campaign — thin but directionally usable. The 45-day age gate
stops a campaign being restructured before its own learning has settled, and the
once-per-cycle guard stops the account being reshaped repeatedly inside one goal
window. **These are tunable constants, not physics** — they live at the top of
`agents/google_ads_agent.py` and are meant to be revised once there is real data.

### Operational kill switch

`OPTIMIZATION_MODE` in `agents/google_ads_agent.py` is a module constant with
three values: `"off"`, `"dry_run"` (decide and log everything, mutate nothing),
and `"live"`. It is an operational switch, **not** a per-action approval gate —
the doctrine's no-approval rule is unchanged. It exists because no write path
beyond campaign creation had ever run against a live account when this shipped.

## File map

- `core/google_ads_service.py` — HTTP only: consent URL, code exchange,
  access-token refresh (in-memory cache), `search()` (GAQL), `set_campaign_status()`,
  `list_accessible_customers()`, daily op-limit guard. `ADS_API_VERSION` constant
  at the top is the single place to bump versions. **Google moved to MONTHLY
  releases in January 2026 — a major version now lives ~6 months, not ~a year**,
  so this needs checking several times a year. Always jump to the NEWEST version,
  not the next one up: it resets the clock. Every non-200 goes through `_check()`,
  which raises `AdsApiVersionSunsetError` when the version itself is dead —
  Google sunsets in two phases (400 "Version vNN is deprecated", then 404 with an
  empty body once the path is removed) and it detects both. **v21 sunset
  2026-08-05 and the constant sat on it for 13 days**, because every call site
  reported the outage as that individual client's failure; that's what the
  dedicated exception type and the admin alert in the OAuth callback exist to
  prevent. Migrating: only the small surface we use matters (listAccessibleCustomers,
  googleAds:search, googleAds:mutate, campaigns:mutate) — the v21→v25 review found
  zero breaking changes touching it.
- `agents/google_ads_agent.py` — business logic per house blueprint:
  `is_connected`, `get_campaign_performance` (5-min in-memory cache),
  `pause_campaign` / `resume_campaign`, `create_search_campaign` (validated
  spec → ONE atomic mutate, always created PAUSED — human activates),
  `run_health_scan` (daily: auto-pauses campaigns with disapproved ads,
  alerts on account/eligibility/performance problems, durable dedup via
  `client_activity` `ads_issue_detected` rows), `run_weekly_report`
  (WoW metrics + optional LLM summary → email to ADMIN_EMAIL). All
  mutations audit-logged to `client_activity`. Alert thresholds and the
  budget safety cap (`MAX_DAILY_BUDGET_ILS`) are module constants at the top.
- `core/session.py` — `create_oauth_state_token` / `verify_oauth_state_token`:
  HMAC state with a DERIVED secret so a state param can never be replayed as a
  session cookie. Reuse for any future platform OAuth.
- `core/api_server.py` — the two OAuth endpoints. Callback identity comes from
  the signed state token, not the session cookie.
- `agents/support_agent.py` — injects `google_ads_performance` into the LLM
  payload when connected; prompt tells the model to cite real numbers only.

## Gotchas (each of these cost someone a debugging session)

- **Refresh token only arrives with `access_type=offline&prompt=consent`.**
  Without `prompt=consent` Google returns one only on the FIRST authorization ever.
- **Explorer Access developer token works only against TEST Ads accounts** (2,880
  ops/day). Production accounts fail until Basic Access is approved. A weird
  `DEVELOPER_TOKEN_NOT_APPROVED` / permission error on a real account = this.
- **Redirect URI must match EXACTLY** what's registered on the OAuth client,
  including scheme and path: `{PUBLIC_APP_URL}/api/oauth/google-ads/callback`.
  `PUBLIC_APP_URL` defaults to `https://app.uallak.com` — the app's own
  subdomain under the domain split (`uallak.com` root is the WordPress
  marketing site and serves NO app routes). Until `app.uallak.com` DNS is
  live it must be set to the real Cloud Run URL in the env, and whichever
  URL is in use must be the one registered in Google Cloud Console.
- **MCC (manager) accounts need the `login-customer-id` header** — set the
  optional `GOOGLE_ADS_LOGIN_CUSTOMER_ID` env var. Directly-owned accounts don't.
- **Costs come back as `costMicros` strings** — divide by 1,000,000; currency is
  the ad account's own (ILS for our clients). All numeric metrics arrive as
  JSON strings; cast them.
- **`listAccessibleCustomers` returns ALL accounts the user can touch** —
  suspended, cancelled, unfinished and manager (MCC) accounts too, in no useful
  order. The callback used to take the first one; that stored the wrong account
  for a client whose login reached six. Now `google_ads_agent.connect_from_oauth`
  describes each (`google_ads_service.describe_customers`: one GAQL op per
  account) and auto-connects ONLY when exactly one is ENABLED and non-manager.
  Several → row parked as `pending_selection` (token kept) and the dashboard's
  Google Ads card shows a picker (`/api/google-ads/account-choices` +
  `/select-account`). None enabled → `connect_error=google_ads_no_enabled` +
  admin alert. The admin can switch the stored account from the Control Room
  (`/api/admin/control-room/{id}/google-ads/accounts|account`) without a new
  consent, including to a not-yet-enabled account on purpose.
- Blocking httpx calls → OAuth callback and anything calling the Ads API must be
  plain `def` endpoints (threadpool), never `async def`.

## REST cheatsheet (no client library — httpx only, keep it that way)

- GAQL search: `POST {base}/customers/{cid}/googleAds:search` `{"query": "..."}`
- Mutate status: `POST {base}/customers/{cid}/campaigns:mutate` with
  `{"operations":[{"update":{"resourceName":"customers/{cid}/campaigns/{id}","status":"PAUSED"},"updateMask":"status"}]}`
- Accessible accounts: `GET {base}/customers:listAccessibleCustomers`
- **Multi-resource creation**: `POST {base}/customers/{cid}/googleAds:mutate` with
  `mutateOperations` — atomic, and operations reference each other via temporary
  resource IDs (negative numbers): budget `-1` → campaign `-2` → ad group `-3`.
  Never create budget/campaign/adgroup/ads as separate sequential calls — a
  mid-sequence failure strands a half-built campaign.
- Errors nest the useful message in `error.details[0].errors[0].message` —
  `_ads_error_message()` in the service extracts it.

## Research-grounded campaign drafting (2026-08-01)

Before this, `create_search_campaign` took a spec an ADMIN TYPED BY HAND — the
agent could execute a campaign but had no view of the market it was launching
into, which is exactly how generic campaigns get built.

`draft_campaign_spec(client_id, daily_budget_ils, final_url="", goal="")` →
`POST /api/google-ads/draft-campaign` closes that: it runs the `"ads"` lens of
`core/competitor_research.py` (competitors advertising, recurring OFFERS,
MESSAGING, AUDIENCE SIGNALS, SATURATION, GAP), then a JSON call drafts keywords
/ headlines / descriptions FROM that research, plus `targeting_rationale`,
`messaging_rationale` and `notes_for_johnny`.

**Two independent human gates before a shekel moves**, and this is the design
rule to preserve:
1. Drafting **creates nothing** — it returns the spec `create_search_campaign`
   takes, for review.
2. Creation still lands the campaign **PAUSED**, unchanged.

**Budget is never inferred.** `daily_budget_ils` is a required argument;
guessing someone's ad spend from a stored proposal is not a guess this system
makes. `MAX_DAILY_BUDGET_ILS` applies to a drafted spec exactly as to a typed one.

**The draft is validated by the SAME `_validate_campaign_spec` the create path
uses**, with one repair round that feeds the named violations back (the pattern
`seo_agent.write_article` uses) — character limits are what usually trip an LLM.
A draft that wouldn't pass creation is not shown to a human.

**Refuses to draft when research fails** — a spec built with no market view is
the generic campaign this function exists to prevent.

Prompt rules that must survive edits: inform-never-copy, never name a competitor
in ad copy (Google restricts it and it wastes budget), never invent business
facts (prices/guarantees/awards/"#1" — Israeli consumer-protection rules and
Google policy both bite), Hebrew ad text, and prefer few high-intent keywords
over broad reach on an SMB budget.

## Campaign-creation gotchas

- RSA limits: 3–15 headlines ≤30 chars, 2–4 descriptions ≤90 chars, keywords
  ≤80 chars. `_validate_campaign_spec` enforces these BEFORE the API call.
- `containsEuPoliticalAdvertising` is a mandatory declaration on new campaigns
  (v20+) — we always send `DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING`.
- `amountMicros` is a STRING in REST JSON (int64). Metrics also come back as strings.
- Bidding default is `targetSpend` (maximize clicks) — works without conversion
  tracking, which fresh SMB accounts don't have.
- Geo/language defaults: Israel (`geoTargetConstants/2376`), Hebrew + English.

## Scheduled endpoints (Cloud Scheduler, X-Admin-Key header required)

- `GET /api/google-ads/scan` — daily health scan (pause broken, alert problems)
- `GET /api/google-ads/weekly-report` — WoW digest emailed to ADMIN_EMAIL
- `POST /api/google-ads/create-campaign` — manual/admin campaign creation
  (`{client_id, name, daily_budget_ils, final_url, keywords, headlines, descriptions}`)
- `POST /api/google-ads/draft-campaign` — research-grounded DRAFT of that spec
  (`{client_id, daily_budget_ils, final_url?, goal?}`); creates nothing, see above

Scheduler jobs follow the monitor_agent pattern (`/api/monitor/scan`), e.g.:
`gcloud scheduler jobs create http google-ads-scan --schedule="0 7 * * *" --uri="{SERVICE_URL}/api/google-ads/scan" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}` (weekly report: `--schedule="0 8 * * 0"`).

## Merchant Center (2026-07-23 — priced INSIDE the core Google Ads fee, not a separate add-on)

> **RETIRED 2026-10-21.** The connection, `core/merchant_center_service.py`, the
> `content` scope and the dashboard card were removed: no feature showed the client
> anything, so the scope could not pass Google's least-privilege review. Old grants are
> revoked by `POST /api/admin/merchant-center/retire`. The text below is history — if
> Merchant Center comes back, it must ship WITH a client-visible feed-status screen and a
> scheduled scan, and the scope goes back into `docs/GOOGLE-SCOPES.md` + the Console.

Extends this agent (not a new one): same "Google-side platform management"
business bucket as Ads, covered by `PRICING["platform_management_fees"]
["google"]` — no separate line item, same principle as Meta bundling FB+IG.

**Auth — a SEPARATE consent from Ads, and NOT programmatic account
discovery.** `.../auth/content` is a different scope from `.../auth/adwords`
entirely (never shared, so this was never a "should we bundle it" choice),
and rather than trust an uncertain "list accounts this grant can see"
endpoint, the client TYPES their own numeric Merchant Center id (visible in
their own account settings) — the flow is genuinely two steps
(`/api/oauth/merchant-center/start` → Google consent → parked on a
`merchant_center_pending` row → `POST /api/merchant-center/link
{merchant_id}` VERIFIES the grant can read that exact id before storing the
real `merchant_center` row and deleting the pending one). Same "park token,
verify/link later" shape as `meta_content_agent`'s no-Page flow. Gets the
pre-action popup (two real steps + finding an id in their own settings is
genuinely more than 1-2 clicks) — unlike the plain-redirect Ads/Meta/TikTok/
YouTube connects.

**THE DEPENDENCY THIS WAS EXPLICITLY ASKED TO INVESTIGATE (not assumed)**:
Merchant Center does NOT depend on `website_agent`'s WooCommerce gap.
A product feed can come from many sources Google supports directly (a
connected Shopify store, a manually-maintained Google Sheet, a scheduled
URL fetch, or direct API insertion) — our job is CONNECT + MONITOR an
EXISTING account, never to BE the feed source. So: a client who already
sells somewhere with a real feed can use this today, fully independent of
our own e-commerce tooling; a client with NO product feed anywhere and no
e-commerce presence genuinely can't use Merchant Center — but that's an
eligibility gap (nothing to advertise), not a feature blocked by our stack.
Building WooCommerce support would let US become a feed source for clients
whose site we build — a real future enhancement, not what blocks this.

**v1 scope — account + feed HEALTH, not per-product issue detail**:
`get_feed_status`/`GET /api/google-ads/merchant-status` reports data-source
(feed) presence and processing state via the Merchant API's Data Sources
service; `run_merchant_center_scan`/`GET /api/google-ads/merchant-scan`
alerts on issues across every connected account. Deliberately NOT built:
per-product issue-level reporting (the Reports API's query language wasn't
confident enough here to build against without a live account to verify
against first — a real follow-up once one exists).

**HIGHEST VERIFICATION RISK in this codebase**: the Merchant API (Content
API for Shopping's successor) was built against v1beta; moved to **v1** on
2026-10-13 because Google shut v1beta down 2026-02-28 (the two paths used —
`accounts/v1/accounts/{id}`, `datasources/v1/accounts/{id}/dataSources` —
checked against the v1 reference). Still never run against a live account.
Smoke-test against a real Merchant Center account before relying on any of it.

**Token exchange gotcha (fixed 2026-10-13)**: GTM, YouTube and Merchant Center
reuse `google_ads_service.exchange_code`, which used to always send the
GOOGLE ADS redirect_uri — Google requires the exchange's redirect_uri to equal
the consent's, so all three failed with `400 redirect_uri_mismatch` after a
successful consent. Each service now wraps it with its own `redirect_uri()`.
Any NEW Google consent on our OAuth client must do the same. Failed exchanges
now log Google's error body (`[google oauth] token exchange failed: …`).

**Scope/verification flag (business decision, same class as GTM/YouTube)**:
`.../auth/content` is a sensitive scope requiring OAuth app-verification
resubmission — "unverified app" warning + 100 test-user cap until approved.

## Deferred / not built

Auto-creating campaigns from `build_proposal` output (proposal lacks keywords/ad
copy — needs a generation step first), refresh-token encryption at rest (plaintext in Supabase — flagged, accepted for
MVP), budget/bidding changes on live campaigns. Conversion-tracking IMPORT
into Google Ads itself stays manual (website_agent's GTM work verifies/
configures the GA4-side event; importing it as a Google Ads conversion
action has no API in our scopes — see the website skill). Merchant Center
per-product issue reporting (see above). TikTok equivalent is built (own
skill). YouTube equivalent is built (own skill — separate consent, not this
agent, since it's a content-publishing platform like Meta/TikTok, not an
Ads-family product).
