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
domain-wide delegation — do not build a service-account path.
`GOOGLE_ADS_SERVICE_ACCOUNT_JSON` is registered in keys_agent but intentionally
unused. Dev-testing uses the same OAuth flow against a Google Ads *test* account.

## File map

- `core/google_ads_service.py` — HTTP only: consent URL, code exchange,
  access-token refresh (in-memory cache), `search()` (GAQL), `set_campaign_status()`,
  `list_accessible_customers()`, daily op-limit guard. `ADS_API_VERSION` constant
  at the top is the single place to bump versions (Google releases ~3/year, each
  lives ~a year — a 404 on every call usually means sunset version).
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
  `PUBLIC_APP_URL` defaults to `https://uallak.com`, which is NOT connected to
  the service — it must be set to the real Cloud Run URL in the env, and the
  same URL registered in Google Cloud Console.
- **MCC (manager) accounts need the `login-customer-id` header** — set the
  optional `GOOGLE_ADS_LOGIN_CUSTOMER_ID` env var. Directly-owned accounts don't.
- **Costs come back as `costMicros` strings** — divide by 1,000,000; currency is
  the ad account's own (ILS for our clients). All numeric metrics arrive as
  JSON strings; cast them.
- **`listAccessibleCustomers` returns ALL accounts the user can touch** — the
  callback currently takes the first one. Multi-account clients need an
  account-picker step (known Phase 1 limitation).
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

Scheduler jobs follow the monitor_agent pattern (`/api/monitor/scan`), e.g.:
`gcloud scheduler jobs create http google-ads-scan --schedule="0 7 * * *" --uri="{SERVICE_URL}/api/google-ads/scan" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}` (weekly report: `--schedule="0 8 * * 0"`).

## Deferred / not built

Auto-creating campaigns from `build_proposal` output (proposal lacks keywords/ad
copy — needs a generation step first), account-picker for multi-account users,
refresh-token encryption at rest (plaintext in Supabase — flagged, accepted for
MVP), conversion-tracking setup, budget/bidding changes on live campaigns,
Meta/TikTok equivalents (copy this skill's auth pattern when building those).
