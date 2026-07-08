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
  `pause_campaign` / `resume_campaign` (audit-logged to `client_activity`).
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
- Errors nest the useful message in `error.details[0].errors[0].message` —
  `_ads_error_message()` in the service extracts it.

## Phase 2 (needs Basic Access approval — not built)

Campaign creation, budget/bidding changes. Also pending: refresh-token
encryption at rest (currently plaintext in Supabase — flagged, accepted for MVP),
account-picker for multi-account users, Meta/TikTok equivalents (copy this
skill's auth pattern when building those).
