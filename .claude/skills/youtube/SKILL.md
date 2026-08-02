---
name: youtube
description: How uallak's YouTube integration works — separate Google OAuth consent, private-by-default publishing, engagement tracking, and the decoupled pricing model (management fee, not a generation-cost bucket). Use when touching agents/youtube_content_agent.py, core/youtube_service.py, the /api/oauth/youtube endpoints, or any /api/youtube-content/* endpoint.
---

# YouTube integration (organic publishing — paid add-on, decoupled from generation cost)

## Why a separate agent with its own consent

Mirrors `agents/tiktok_content_agent.py`'s structure exactly: media_agent/
avatar_agent generate the actual video; this agent is purely the pipe from
already-generated media (a Drive `file_id`) to the client's YouTube channel.
No content generation, no LLM calls. Own OAuth consent (not appended to
Google Ads) for the same reason as GTM/Merchant Center: a grant's scopes are
fixed at consent time, so bundling would force every Ads client to
reconnect for scopes only content-publishing clients need.

## Pricing model — DECOUPLED, a business decision (2026-07-23, CONFIRMED same day)

The YouTube fee (`PRICING["platform_management_fees"]["youtube"]`, **150₪/mo,
confirmed**) covers ONLY ongoing management: connection, uploads, engagement
tracking. It is deliberately NOT a generation-cost bucket:

- Media generation for YouTube content (podcasts, repurposed shorts/reels,
  YouTube-specific videos) stays entirely inside the client's EXISTING
  media/avatar tier system. A client wanting bigger/more complex YouTube
  content upgrades THAT tier (billed via their own Higgsfield/HeyGen
  subscription) — never a second, YouTube-specific generation charge.
  `youtube_content_agent.py` has no generation path to charge for at all.
- Justification for charging anything despite the API costing us nothing
  (see cost research below): the fee reflects the connection/upload/
  engagement OPERATIONAL work, same principle as every other platform
  management fee in PRICING.

**Cost research behind the number (the handoff's explicit ask — verify,
don't assume it mirrors Higgsfield's per-generation billing)**: the
YouTube Data API v3 has **zero monetary billing** — quota units only, not
a per-request charge. Default project quota is 10,000 units/day;
`videos.insert` dropped from ~1,600 to ~100 units per call in Dec 2025,
so the default free quota alone supports ~100 uploads/day — far beyond
uallak's realistic volume across every client combined. **There is no real
API cost to price into the fee at all.** `DAILY_UPLOAD_LIMIT = 90` in
`core/youtube_service.py` is a safety brake under the free cap, not a
cost-avoidance measure. 150₪/month sits roughly at avatar-setup scale, well
below the 350₪ platform-management tiers (which include real ads/content-
strategy work YouTube publishing doesn't).

**Now fully wired into the live sales-chat proposal flow** (2026-07-23,
confirmed): `PRICING["platform_management_fees"]["youtube"] = 150` sits
alongside meta/google/tiktok; `build_proposal`'s BUDGET PYRAMID #1 prompt
text names all four platform groups explicitly (YouTube's lower rate and
the reasoning for it spelled out to the model), `recommended_services` and
the monthly_breakdown line-naming rule both include "youtube"/"ניהול
יוטיוב", and a YOUTUBE RELEVANCE GUARD line tells the model to only include
it when the package already produces video content to publish (a media/
avatar tier, or organic content that includes video) — recommending
YouTube management with nothing to upload would be nonsensical. Deliberately
NOT added to the self-service upgrade-panel ladder (`get_upgrade_tiers`) —
an existing client adding YouTube goes through the chat's upgrade-request
path instead, which reuses this same `build_proposal` (upgrade mode) and
now prices it correctly.

## Auth model — one consent, one stored row

`/api/oauth/youtube/start` → Google consent → `/api/oauth/youtube/callback`
→ `platform='youtube'`, `account_id`=channel id, `access_token`=refresh
token. Scopes: `youtube.upload`, `youtube.readonly` — both Google SENSITIVE
scopes requiring OAuth app-verification resubmission (same class of process
as the GTM/Merchant Center scopes; "unverified app" warning + 100 test-user
cap until approved — a real timeline gate, flagged as a business decision
same as the others this week). YouTube API Services additionally reserves
a compliance-audit right at scale — noted, not currently a blocker.

## No-channel flow (guided self-creation + auto-detection, 2026-07-23)

Channel creation via API was deliberately NOT attempted — same reasoning as
`meta_content_agent`'s no-Page flow and why `website_agent.provision_site`
exists for WordPress but there's no equivalent here: there's no reliable
"create a YouTube channel on the user's behalf" API for a standard app,
while the native flow is a genuinely quick minute the client does once.

When the OAuth callback finds no channel:
1. The refresh token is parked on a `youtube_pending` row (`account_id=
   'pending_channel'`) — without parking it, a no-channel client would need
   a full reconnect once their channel exists. `youtube_pending` is in
   `_DISCONNECT_GROUPS["youtube"]`.
2. `send_channel_creation_guide()` sends step-by-step instructions via
   dashboard chat — STATIC text in the client's stored language preference
   (the native YouTube flow is identical for everyone; no LLM call), deduped
   3 days (`channel_guide_sent` activity rows). Steps: youtube.com signed in
   with the same Google account → profile picture → "Create a channel" (or
   studio.youtube.com, which offers this automatically) → name the channel
   → done.
3. Redirect is `?connected=youtube_no_channel` → the dashboard shows the
   shared pre-action popup (`preactExplain('youtube_channel')`).
4. `run_channel_detection_scan()` (`GET /api/youtube-content/scan` — no
   existing YouTube scan to piggyback on, unlike Meta's inbox scan, so this
   NEEDS its own scheduler job) watches `channels.list(mine=true)` for
   every guided client (`channel_guide_sent` rows are the ONLY scan
   population) until the channel appears, then connects it exactly like the
   callback would, deletes the parked row, and notifies the client via chat.

```
gcloud scheduler jobs create http youtube-channel-scan --schedule="0 8,14,20 * * *" \
  --uri="{SERVICE_URL}/api/youtube-content/scan" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}
```

**Real constraint sharper here than for Meta**: while this app sits in
Google's "Testing" OAuth publishing status (unverified — see the scope
flag below), granted refresh tokens expire after just **7 days**, not the
usual long lifetime. A client who takes longer than a week to create their
channel will hit `invalid_grant` on the next scan attempt — handled (the
parked row is deleted and the team is alerted to ask them to reconnect),
but worth knowing this failure mode is more likely here than it would be
once the app is verified/in production.

Token exchange/refresh reuse `google_ads_service`'s scope-agnostic OAuth
helpers directly (same pattern as `gtm_service.py`) — not a third copy.

## Publishing — private by default, human publishes

`publish(client_id, {drive_file_id, title, description?})` downloads the
video from Drive (private, service-account creds) and uploads the raw bytes
via YouTube's resumable upload — no domain-verification requirement at all
(unlike TikTok's PULL_FROM_URL constraint), so a Drive file_id needs no
public-URL step either way. Every upload lands with `privacyStatus:
'private'` — a human flips it public/unlisted in YouTube Studio. Same
final-tap principle as PAUSED ad campaigns, WordPress drafts, and TikTok's
Upload-to-Inbox: never auto-published anywhere.

## Engagement

`get_engagement_summary(client_id, window_days)` — real view/like/comment
totals for videos published in the window, via the uploads-playlist +
videos.list combo (2 quota units total, never `search.list`'s 100).
Comment CONTENT is NOT read (the `commentThreads` endpoint is a bigger
scope ask than `youtube.readonly` cleanly covers) — aggregate counts only,
the same honest limit as `tiktok_content_agent`'s own engagement summary.

## Public niche search — API KEY, no OAuth (2026-08-03)

A second, separate use of the Data API lives at the bottom of
`core/youtube_service.py`: `search_videos(query)`, authenticated by a plain
project key (`YOUTUBE_DATA_API_KEY`, registered in `keys_agent.KEYS`) through
`_get_with_key()` — **never** a client's OAuth token. Everything above that
section acts AS a client on their own channel; this one reads public YouTube as
an anonymous user, which is why it needs no connection, no consent screen and no
Google verification.

Consumed by `core/competitor_research.py` as another grounding source for the
**media lens only** (`_youtube_niche_videos`) — it supplements the web search,
never replaces it, and both feed the same single prompt. See
`HANDOFF-social-trend-research.md`.

**What it returns: real video titles, channel names, publish dates, real URLs
(built from API-returned ids). What it does NOT return: view counts, subscriber
counts, trending rank** — `search.list` exposes none of them. `order=viewCount`
ranks the result set without revealing any number, so it is not a measurement
anyone may quote. The lens prompt states this explicitly; never let this get
framed as trend analytics in client-facing copy.

**Quota is the real constraint, and it is SHARED with uploads:**

| | cost | brake |
|---|---|---|
| `search.list` (niche discovery) | 100 units | `DAILY_SEARCH_LIMIT = 40` → 4,000 units |
| `videos.insert` (upload) | ~100 units | `DAILY_UPLOAD_LIMIT = 90` → 9,000 units |
| `playlistItems` / `videos.list` (engagement) | 1-2 units | none needed |

One project quota of **10,000 units/day** covers all of them. The two brakes
deliberately overlap (13,000 units of nominal headroom) because real upload
volume is ~zero today — but **if uploads become routine, lower
`DAILY_SEARCH_LIMIT` first**: research degrades silently, a failed upload is a
client deliverable. Counted through `core/api_call_counters` under platform
`youtube_search`, separate from uploads' `youtube` row.

**Headroom in practice**: the media lens caches 14 days per client and spends at
most 2 searches per uncached pass, so 40 searches/day ≈ 20 client research passes
per day ≈ **~280 media clients** before the brake binds. Not a near-term
bottleneck. When it becomes one, a **quota increase is free** — a Google audit
form (YouTube API Services compliance review), not a billing change. Do that
before considering caching search results separately; the research cache already
absorbs most of the repeat load.

## Endpoints

Client-facing: `/api/oauth/youtube/start`, `/api/oauth/youtube/callback`.
Admin (X-Admin-Key): `POST /api/youtube-content/publish`,
`GET /api/youtube-content/engagement?client_id=`,
`GET /api/youtube-content/scan` (channel-detection — see above, needs its
own scheduler job, unlike everything else in this file).

Publish/engagement themselves stay admin/pull-triggered through the weekly
media pull-point pattern (media_agent → this agent), same reasoning as
TikTok — only the no-channel detection above needs a real recurring job.

## Deferred / not built

Comment reading/replying (scope-limited, see above), multi-channel picker
(first-channel MVP, same as every other platform), token encryption at rest
(same accepted MVP debt as Google/Meta/TikTok), thumbnail upload (title/
description only in v1), Shorts-specific handling (a video is a video to
the API — vertical/short-form framing is a content-generation concern for
media_agent, not this agent), scheduled/timed publishing (uploads land
private immediately; scheduling a future public time is a YouTube Studio
manual step for now).
