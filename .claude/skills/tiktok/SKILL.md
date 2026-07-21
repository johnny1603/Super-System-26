---
name: tiktok
description: How uallak's TikTok integration works — OAuth + composite token storage, why publishing uses Upload-to-Inbox instead of Direct Post, the access-tier/content-audit reality, and engagement-tracking limits. Use when touching agents/tiktok_content_agent.py, core/tiktok_service.py, the /api/oauth/tiktok endpoints, or any /api/tiktok-content/* endpoint.
---

# TikTok integration (organic content — no paid ads)

## Why a separate agent, not an extension of Meta's

TikTok's Content Posting API is a different product with different auth,
different scopes, and a different review process from Meta's Graph API —
there's no shared HTTP layer worth factoring out. Division of labor mirrors
Meta's organic side exactly:

- `core/tiktok_service.py` — HTTP only: OAuth flow, token exchange/refresh,
  Content Posting API (`init_inbox_upload`, `init_direct_post`,
  `upload_video_chunk`, `get_post_status`), `user/info`, `video/list` +
  `video/query` for stats. `TikTokAPIError` carries TikTok's `code`/`log_id`;
  `is_token_error()` spots the two expiry error codes.
- `agents/tiktok_content_agent.py` — the pipe from already-generated media
  (a Drive `file_id` from `media_agent`) to the client's TikTok account.
  `publish` (Upload-to-Inbox only — see below), `get_engagement_summary`
  (aggregate counts only). **No content generation, no LLM calls** —
  `media_agent` (Higgsfield) is the only place content gets made.

## Auth model — one consent, one stored row, tokens that rotate constantly

"Connect Now" → `/api/oauth/tiktok/start` → TikTok consent → `/api/oauth/tiktok/callback`.
The callback exchanges the code for an access + refresh token pair and stores
ONE `client_accounts` row:

| platform | account_id | access_token |
|---|---|---|
| `tiktok` | `open_id` | composite `access_token::refresh_token` |

- App credentials: `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` (in
  `keys_agent.KEYS`) — the equivalent of `META_APP_ID`/`SECRET`.
- **Composite token storage**: `client_accounts` has no dedicated
  refresh_token column. Reuses the same pattern `website_agent` already uses
  for `username:app_password`, but with a `::` delimiter instead of `:`
  (safer given no certainty a TikTok token can't itself contain a colon).
  `tiktok_service.split_tokens()`/`join_tokens()` are the only places that
  should touch this encoding.
- **The access token expires in 24 hours** — much shorter-lived than Meta's
  ~60-day Page token. `_live_access_token()` in the agent refreshes on
  *every* real use, not just on failure; this is the normal path here, not
  error recovery. TikTok also **rotates the refresh token itself** on every
  refresh call — both new tokens are re-stored before the fresh access token
  is returned, or the next call would refresh with a dead refresh token.
- Redirect URI must be registered in the TikTok app's settings:
  `{PUBLIC_APP_URL}/api/oauth/tiktok/callback`.
- Scopes requested: `user.info.basic`, `video.publish`, `video.list`.

## Publishing: Upload-to-Inbox, deliberately never Direct Post

**This is the business-decision-relevant part.** TikTok's Content Posting
API gates Direct Post behind a content audit: until an app passes it, every
Direct Post is forced to `SELF_ONLY` visibility (private, visible only to
the posting account) — a client's "published" video would silently not be
public. Passing the audit is a separate, additional review beyond basic app
approval, timeline not in our control.

The agent sidesteps this by using **Upload to Inbox** (`init_inbox_upload`)
exclusively: the video lands in the *client's own* TikTok inbox and they tap
publish themselves inside the app. `init_direct_post` exists in
`tiktok_service.py` for a possible future decision but the agent never calls
it. Two independent reasons this is the right default, not just a
workaround:

1. It has **no SELF_ONLY restriction at all** — a client publishing this way
   is genuinely public the moment they tap publish, audit or no audit.
2. It matches house policy already established everywhere else — PAUSED ad
   campaigns (human activates), WordPress drafts (human publishes),
   Drive-review-first media (nothing auto-published). A human always makes
   the final publish tap; TikTok is no exception.

**Source is FILE_UPLOAD, not PULL_FROM_URL.** `PULL_FROM_URL` requires the
media URL's domain to be pre-verified as ours in the TikTok developer
dashboard — a `drive.google.com` link can never satisfy that (we don't own
the domain). Instead `publish()` downloads the video privately via
`core.drive_service.download_file` (service-account credentials, file never
made public) and uploads the raw bytes to TikTok directly. Practical
consequence: **single-chunk upload only** (`total_chunk_count=1`) — fine at
current content sizes, but a genuinely large video would need real chunking
support that isn't built.

**Caption is not applied.** Upload-to-Inbox has no caption/title field at
all — the client writes their own when they finish publishing in the app.
If media_agent generated a caption, `publish()` sends it to the client via
dashboard chat as a ready-to-paste suggestion instead of silently dropping it.

## Access-tier / review reality (flagged as requested)

Three real gates, layered:

1. **Basic app review/approval** is required before any real (non-sandbox)
   client account can connect at all — comparable in spirit to Meta's
   Limited vs Full Access split, though structured differently.
2. **Content audit** (separate, after basic approval) is required to lift
   the SELF_ONLY restriction on Direct Post — moot for us today since we
   don't use Direct Post, but relevant if a future business decision moves
   toward it.
3. **Comment CONTENT is not available via the public API at all** — it
   lives behind the separately-gated Research API, which isn't realistically
   obtainable for a commercial SaaS tool. Only aggregate counts
   (`video.list`/`video.query`) are available; this is a genuine platform
   gap versus Meta, not an oversight.

## Engagement tracking — via video.list, not a tracked video_id

Upload-to-Inbox never returns a durable video_id for the eventual published
post (the client controls if/when it's actually posted). So
`get_engagement_summary()` doesn't try to track specific IDs — it calls
`video/list` (the account's own public videos, newest first), filters by
`create_time` within the window, and sums like/comment/share/view counts.
This mirrors `meta_content_agent.get_engagement_summary()`'s own approach
(sums recent posts in a window rather than tracking specific IDs).

There is **no comment inbox / `reply_to_comment` equivalent** here, unlike
`meta_content_agent` — not an oversight, there's no API surface for it
(see gate #3 above).

## Gotchas

- **`fields` is a query-string param even on POST endpoints** — unlike a
  pure JSON-body API. `tiktok_service.api_post`/`api_get` both take a
  `params` dict for this; `list_videos`/`query_videos` pass
  `params={"fields": VIDEO_STAT_FIELDS}` alongside their JSON body.
- Blocking httpx calls + polling (`_wait_for_inbox_delivery`) → every
  endpoint touching TikTok must be plain `def` (threadpool), never `async def`.
- Tokens ride the `Authorization: Bearer` header, never query params.
- No dedicated Cloud Scheduler job for TikTok: there's no comment inbox to
  scan (unlike Meta's `meta-content-scan`), so nothing runs on a fixed
  cadence today. Publish and engagement pulls are both triggered by the
  weekly media pull-point pattern (media_agent → this agent), the same as
  Meta's own publish/reply endpoints are admin/pull-triggered rather than
  scheduled.

## Endpoints

Client-facing: `/api/oauth/tiktok/start`, `/api/oauth/tiktok/callback`.
Admin (X-Admin-Key): `POST /api/tiktok-content/publish`,
`GET /api/tiktok-content/engagement?client_id=`.

## Deferred / not built

Direct Post (implemented in `tiktok_service.init_direct_post`, unused —
needs the business decision on pursuing the content audit), comment
reading/replying (no public API surface — genuine platform gap), asset
picker for multiple TikTok accounts per client (first-asset MVP, same as
Google/Meta), token encryption at rest (same accepted MVP debt as
Google/Meta), multi-chunk video upload (single-chunk only — fine at current
sizes, would need real chunking for large files), scheduled engagement scan
(no cadence exists yet — pull-triggered only, see Gotchas).

**Paid TikTok Ads — entirely out of scope, a real separate future initiative.**
Everything above is organic content only (mirrors `meta_content_agent.py`).
There is no TikTok equivalent of `meta_ads_agent.py`/`google_ads_agent.py`.
Paid campaign management would need TikTok's separate Marketing API
(`business-api.tiktok.com`) — its own TikTok Business Center account, its
own app registration/scopes, its own review process — none of which this
integration touches. Don't assume paid reach is covered just because
`agents/tiktok_content_agent.py` exists.

**Upload-to-Inbox cannot pre-fill caption/hashtags — a hard API limit, not a
choice.** `post/publish/inbox/video/init/`'s request schema has no
`post_info`/`title` field at all (unlike Direct Post's `video/init/`, which
does accept `title`). The client always types their own caption when they
open the app to review/publish; the generated caption is sent as a
copy-paste suggestion via chat instead (see `publish()`). This is a real
trade-off against the SELF_ONLY restriction Direct Post carries until the
content audit — worth re-weighing if that audit is ever pursued.
