---
name: meta
description: How uallak's Meta (Facebook + Instagram) integration works — one OAuth flow feeding TWO agents (paid ads + organic content), token storage and refresh, Graph API patterns, access-tier reality, and the gotchas. Use when touching agents/meta_ads_agent.py, agents/meta_content_agent.py, core/meta_service.py, the /api/oauth/meta endpoints, or any /api/meta-* endpoint.
---

# Meta integration (Facebook + Instagram — paid AND organic)

## Why two agents, one service

Meta genuinely splits into two APIs with different permissions and review
processes, so business logic is split accordingly — but they share one OAuth
consent, one app credential pair, and one HTTP layer:

- `core/meta_service.py` — HTTP only: OAuth flow, token exchange/introspection,
  asset discovery, `graph_get`/`graph_post`/`graph_delete` primitives (agents
  pass RELATIVE paths — no host/version — so bumping `GRAPH_API_VERSION` is a
  one-line change), Marketing API helpers, error extraction (`MetaGraphError`
  with Meta's numeric code; `is_token_error()` spots dead tokens).
- `agents/meta_ads_agent.py` — **Marketing API** (paid): `is_connected`,
  `get_campaign_performance` (5-min cache, FB+IG combined — Meta is ONE bundled
  platform group in our pricing), `pause_campaign`/`resume_campaign`,
  `create_link_campaign` (sequential create + delete-campaign cleanup on
  failure, always PAUSED — human activates), `run_health_scan` (token refresh,
  account status, auto-pause of policy-flagged campaigns, performance alerts,
  `meta_`-prefixed issue keys in the shared `ads_issue_detected` dedup),
  `run_weekly_report` (WoW + organic engagement garnish + LLM summary).
- `agents/meta_content_agent.py` — **Pages/Instagram Graph API** (organic): the
  pipe from already-generated media (public URLs) to the client's Page/IG.
  `publish` (FB: text/link/photo/video; IG: photo/reel/story via the container
  flow), `get_inbox` + `run_inbox_scan` (surface new comments/DMs to the team,
  durable dedup via `content_inbox_surfaced` activity rows), `reply_to_comment`
  (human-triggered — NO autonomous replies on client brand pages),
  `get_engagement_summary`. No LLM calls at all.

## Auth model — one consent, three stored rows

"Connect Now" → `/api/oauth/meta/start` → Meta consent → `/api/oauth/meta/callback`.
The callback exchanges code → short-lived token → **long-lived user token
(~60 days)** and stores up to three `client_accounts` rows:

| platform | account_id | access_token |
|---|---|---|
| `meta_ads` | ad account id (`act_...`, keep prefix) | long-lived USER token |
| `meta_page` | Page id | PAGE token (no expiry when derived from a long-lived user token) |
| `meta_instagram` | IG business account id | same PAGE token |

- App credentials: `META_APP_ID` / `META_APP_SECRET` (in keys_agent `KEYS`) —
  the equivalent of `GOOGLE_OAUTH_CLIENT_ID/SECRET`. No developer-token
  equivalent — Meta has no third credential. Optional `META_LOGIN_CONFIG_ID`
  (plain env var, not in `KEYS`): the Facebook Login for Business
  Configuration id — when set, `build_consent_url` sends `config_id=` instead
  of `scope=` (Meta's recommended param for Business-type apps).
- Redirect URI must be registered in the Meta App's **Facebook Login → Valid
  OAuth Redirect URIs**: `{PUBLIC_APP_URL}/api/oauth/meta/callback`.
- The user token EXPIRES (~60 days). The daily ads health scan introspects it
  (`debug_token`) and re-exchanges it when <10 days remain — re-exchanging a
  still-valid long-lived token returns a fresh 60-day one. If it's already dead
  (error code 190), the scan alerts "client must reconnect".
- A user may have a Page but no ad account (or vice versa) — the callback
  connects whatever exists; error only when there's neither
  (`connect_error=no_meta_assets`).

## Access-tier reality (why testing is on our own Pages)

- **Limited Access** (default, instant): works ONLY on assets where WE are
  admins — uallak's own Page/ad account. Client assets fail with permission
  errors until Full Access.
- **Full Access**: needs App Review + Business Verification, and 500+ Marketing
  API calls in the trailing 15 days just to QUALIFY to apply. Review takes weeks.
- The code path is identical either way — build/test against uallak's own
  assets now (this also accumulates the 500-call threshold; persisted in
  Supabase as a genuine rolling 15-day sum, logged every 25 calls — see the
  `api-quotas` skill), and client accounts start working the moment Full
  Access lands. No rework.

## Gotchas

- **Never store the short-lived token** — the callback immediately exchanges it
  for the long-lived one. A short token silently dies in ~2 hours.
- **Numeric metrics arrive as JSON strings** (`spend`, `impressions`,
  `clicks`, action values) — cast them. `spend` is already in whole currency
  units (ILS), unlike Google's micros.
- **Budgets go the other way**: ad set `daily_budget` is in MINOR units
  (agorot) — `int(ils * 100)`.
- **"Conversions" don't exist as one number** — insights return an `actions`
  list of `{action_type, value}`. `CONVERSION_ACTION_TYPES` in meta_ads_agent
  defines which types we sum into a Google-comparable number.
- **Campaign status is `ACTIVE`, not `ENABLED`** (Google's term). Insights
  rows don't carry status at all — join with `/campaigns`.
- **No atomic mutate.** Campaign → ad set → creative → ad are sequential
  calls; on failure `create_link_campaign` deletes the campaign (cascades to
  ad sets/ads) and the orphan creative. Don't "improve" this into fire-and-
  forget sequential creation.
- **`special_ad_categories` is mandatory** on campaign creation (send `[]`).
  EU-targeted ad sets would also need DSA beneficiary/payer fields — we default
  to Israel-only targeting, which doesn't.
- **Link ads must be published "as" a Page** (`object_story_spec.page_id`) —
  campaign creation fails cleanly if the client has no `meta_page` row.
- **IG publishes via containers**: `POST /{ig}/media` → poll
  `status_code=FINISHED` (videos take minutes; images are ~instant) →
  `POST /{ig}/media_publish`. Media must be a PUBLIC URL — Meta fetches it
  server-side; localhost/signed-private URLs fail with a cryptic container
  ERROR. IG feed video IS a reel now (`media_type=REELS`).
- **Placements stay automatic (Advantage+)** — one ad set serves across FB and
  IG, matching our bundled pricing. Don't split placements per network.
- Blocking httpx calls → every endpoint touching Meta must be plain `def`
  (threadpool), never `async def`. IG publish can block ~2 minutes (polling).
- Tokens ride the `Authorization: Bearer` header, never query params — keeps
  them out of URLs and logs.

## Endpoints

Client-facing: `/api/oauth/meta/start`, `/api/oauth/meta/callback`.
Admin/scheduler (X-Admin-Key): `POST /api/meta-ads/create-campaign`,
`GET /api/meta-ads/scan` (daily), `GET /api/meta-ads/weekly-report` (weekly),
`POST /api/meta-content/publish`, `POST /api/meta-content/reply`,
`GET /api/meta-content/inbox?client_id=`, `GET /api/meta-content/scan`
(few times daily), `GET /api/meta-content/engagement?client_id=`.

Cloud Scheduler jobs (same pattern as google-ads-scan):

```
gcloud scheduler jobs create http meta-ads-scan --schedule="15 7 * * *" \
  --uri="{SERVICE_URL}/api/meta-ads/scan" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}
gcloud scheduler jobs create http meta-weekly-report --schedule="15 8 * * 0" \
  --uri="{SERVICE_URL}/api/meta-ads/weekly-report" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}
gcloud scheduler jobs create http meta-content-scan --schedule="0 8,13,18 * * *" \
  --uri="{SERVICE_URL}/api/meta-content/scan" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}
```

## Deferred / not built

**Phase-1 scope cut (2026-07):** `business_management`, `pages_messaging`,
`read_insights`, `instagram_manage_insights` were REMOVED from `OAUTH_SCOPES` —
requesting a scope the Limited-Access app can't use makes Meta error on its own
consent screen ("Invalid Scopes") and never redirect back to the callback.
Consequence: Page Messenger DMs are NOT surfaced in Phase 1 (`get_inbox`
returns a `messages_note` instead of alerting); comments still work. Re-add
those scopes (plus `pages_manage_metadata`, `pages_messaging`'s dependency)
in the Advanced Access / App Review application.

**"Invalid Scopes" follow-up (2026-07-15):** even after the cut, the dialog
rejected the six content scopes (`pages_manage_posts`, `pages_manage_engagement`,
`pages_read_user_content`, `instagram_basic`, `instagram_content_publish`,
`instagram_manage_comments`) and accepted only the app-default four. All six
are still valid names per Meta's Permissions Reference and all six are used by
the agents (`instagram_manage_comments` powers `reply_to_comment`'s
`{comment_id}/replies` edge — it is not leftover). The real cause: Business-type
apps use **Facebook Login for Business** — a permission is only requestable
after it's added to the app in the App Dashboard (use case → Customize →
Permissions, plus the Instagram product), and Meta replaced `scope=` with
`config_id=`. Fix: add the six permissions in the dashboard, create an FLB
"User access token" Configuration with all ten scopes, set its id as
`META_LOGIN_CONFIG_ID` on Cloud Run. Do NOT swap to the `instagram_business_*`
scopes — those belong to "Instagram API with Instagram Login" (instagram.com
OAuth, `graph.instagram.com` tokens), a different product from our
Facebook-Login flow.

Facebook Reels publishing (needs the resumable-upload flow; FB `video` kind
covers regular video posts), IG DM reading/sending (IG DMs need
`instagram_manage_messages` + platform param), sending
DM replies (24-hour messaging-window rules — surface-only for now), autonomous
LLM comment replies (deliberate: wrong public reply on a client's brand page
is worse than a slow one), asset picker for users with multiple ad
accounts/Pages (first-asset MVP, same as Google), token encryption at rest
(same accepted MVP debt as Google), creative image upload by hash
(`image_url`/`picture` only for now).

TikTok is now built separately (`agents/tiktok_content_agent.py`,
`core/tiktok_service.py` — see the `tiktok` skill) — it's its own OAuth/API,
not an extension of this one.
