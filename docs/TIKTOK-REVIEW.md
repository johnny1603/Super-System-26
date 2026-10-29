# TikTok app review — scopes, review text, demo video

Submission state after the 2026-10-29 rejection. Code and this file must match:
`core/tiktok_service.OAUTH_SCOPES` is exactly the two scopes below, and
`tests/test_tiktok_scopes.py` fails if it drifts.

| Product | Scope | Used by | Shown in demo |
|---|---|---|---|
| Login Kit | `user.info.basic` | `get_user_info` (OAuth callback) | step 3 |
| Content Posting API — Upload to Inbox | `video.upload` | `init_inbox_upload`, `get_post_status` | steps 3, 5, 6 |

Not requested: `video.publish` (Direct Post — `init_direct_post` is unreachable
until a content audit), `video.list` (Display API — behind
`ENGAGEMENT_READS_ENABLED`, off).

Submission fields:
- Website URL: `https://app.uallak.com/` — no trailing comma, no spaces. Paste it
  from a plain-text editor to avoid invisible characters.
- Terms of Service: `https://app.uallak.com/terms/`
- Privacy Policy: `https://app.uallak.com/privacy/`

## Review text

**Login Kit — `user.info.basic`**

> uallak is a marketing service for small businesses. A signed-in business owner
> connects their own TikTok account from their uallak dashboard. We use
> user.info.basic only to read the account's open_id and display name, so the
> dashboard can show which TikTok account is connected and link it to that
> business. We do not read any other profile data.

**Content Posting API — `video.upload`**

> When the business owner has approved a short video we produced for them,
> uallak uploads it to the owner's own TikTok inbox as a draft (Upload to
> Inbox). The owner opens TikTok, adds their caption, and decides whether and
> when to publish it. uallak never posts on the owner's behalf and does not use
> Direct Post. We use video.upload only for this upload and to check that the
> draft was delivered.

## Demo video — 6 shots

Record one continuous screen capture with a real test business account and a
real TikTok account added as a sandbox/target user.

1. **Website, signed out.** Open `https://app.uallak.com/` in a browser set to
   English. Scroll to "Our services" → the TikTok card ("uallak never posts on
   your behalf"), then the footer with Terms and Privacy Policy.
2. **Sign in.** Click "Sign in" and log in to the uallak dashboard as the test
   business.
3. **Connect TikTok + consent screen.** On the dashboard, click Connect on the
   TikTok card. Hold on TikTok's consent screen long enough to read both
   permissions it lists (user.info.basic and video.upload) — and that nothing
   else is listed. Approve; the
   dashboard shows TikTok connected.
4. **Approve a video.** Open the dashboard's Media tab and show the finished
   video in the business's media folder; the owner confirms in the dashboard
   chat that this video should go to TikTok. (See "Gap" below.)
5. **Send to inbox.** The video is sent to TikTok as a draft (today: uallak's
   team runs `bash scripts/tiktok_send_to_inbox.sh CLIENT_ID DRIVE_FILE_ID`
   in Cloud Shell; the script prints the delivery status).
6. **Draft arrives in TikTok.** On the phone, open the TikTok app on the same
   account → the inbox notification / drafts. Open the draft, show the caption
   field is empty and the owner taps Post themselves (or leaves it as a draft).

## Gap — step 4 has no dedicated button yet

There is no client-facing "send this video to TikTok" approval in the dashboard
today. `tiktok_content_agent.publish` is reachable only via the admin endpoint
`POST /api/tiktok-content/publish`, and `core/agent_actions.py` has no TikTok
action. YouTube already has the pattern (`publish_youtube_from_media`: an agent
proposes, the client clicks Approve on a card, the same runner executes).

So the demo as written shows approval in chat plus a staff-triggered send,
which is accurate to the product but is not one click in the UI. If the
reviewer should see the owner press an Approve button, build a
`publish_tiktok_from_media` action mirroring the YouTube one before recording.
