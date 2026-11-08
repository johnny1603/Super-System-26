# TikTok app review — scopes, review text, demo video

Submission state after the 2026-10-29 rejection. Code and this file must match:
`core/tiktok_service.OAUTH_SCOPES` is exactly the two scopes below, and
`tests/test_tiktok_scopes.py` fails if it drifts.

| Product | Scope | Used by | Shown in demo |
|---|---|---|---|
| Login Kit | `user.info.basic` | `get_user_info`: OAuth callback + the TikTok card's "Connected as <name>" with the avatar (`tiktok_content_agent.get_profile`, `GET /api/client/tiktok/profile`, live, never stored) | step 3 |
| Content Posting API — Upload to Inbox | `video.upload` | `init_inbox_upload`, `get_post_status` — behind the video tile's "Send to TikTok" button and the chat card (same `send_tiktok_to_inbox` approval) | steps 3, 4, 5, 6 |

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
> user.info.basic only to read the account's open_id, display name and avatar,
> so the dashboard can show which TikTok account is connected ("Connected as
> <name>" with the profile picture) and link it to that business. We do not
> store the avatar and do not read any other profile data.

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

**The consent screen must show.** An account that already approved the app is
sent straight back by TikTok, so the permissions never appear. Before recording:
- set `TIKTOK_FORCE_CONSENT=1` on the Cloud Run service. `build_consent_url`
  then adds `disable_auto_auth=1`. TikTok's Login Kit for Web (v2 authorize)
  documents it as "When set to 1, always displays the authorization page".
  Nothing else in the URL changes (scopes, redirect URI, state). Remove the
  variable after the review;
- and/or revoke the app on the test account: TikTok app → Profile → ☰ →
  Settings and privacy → Security & permissions → Apps and services
  permissions → uallak → Remove access (menu names vary a little by app
  version). The next connect then asks again.

1. **Website, signed out.** Open `https://app.uallak.com/` in a browser set to
   English. Scroll to "Our services" → the TikTok card ("uallak never posts on
   your behalf"), then the footer with Terms and Privacy Policy.
2. **Sign in.** Click "Sign in" and log in to the uallak dashboard as the test
   business.
3. **Connect TikTok + consent screen.** On the dashboard's Connections, click
   **Connect** on the TikTok card → the chat asks "Do you already have an
   account there?" → **I have an account** → the short explanation → TikTok's
   consent screen. Hold on it long enough to read both permissions it lists
   (user.info.basic and video.upload) — and that nothing else is listed.
   Approve; back on the dashboard the TikTok card says **Connected as <your
   TikTok name>** with the account's profile picture (user.info.basic, read
   live). Hold on the card.
4. **Approval card.** Open the **Media** tab: each video tile has a **Send to
   TikTok** button. Click it on the test video → the media agent's chat opens
   with the card **"Send a video to your TikTok drafts"**, naming the video and
   saying it goes to the TikTok inbox as a draft and nothing is posted, with
   **Approve** / **Cancel**. Hold on it, then press **Approve**. (Typing "Send
   promo-reel.mp4 to my TikTok" in that chat gives the same card.)
5. **Sent to inbox.** The card works for a few seconds (upload + TikTok's
   status check), then the chat says the video was sent to the TikTok drafts
   (TikTok status `SEND_TO_USER_INBOX`) and tells the owner to open TikTok and
   publish it from there.
6. **Draft arrives in TikTok.** On the phone, open the TikTok app on the same
   account → the inbox notification / drafts. Open the draft, show the caption
   field is empty and the owner taps Post themselves (or leaves it as a draft).

Optional, if the reviewer asks how a broken connection behaves: with a grant
that lacks `video.upload` (e.g. an account connected before 2026-10-29),
asking to send shows a Hebrew/English line plus a **Connect your TikTok
account** card instead of the send card; its button opens TikTok's consent
screen with exactly the two scopes.

Before recording:
- **Sandbox:** the service's `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` must
  be the SANDBOX app's pair (the sandbox has its own key), the sandbox app must
  list the same redirect URI (`https://app.uallak.com/api/oauth/tiktok/callback`)
  and both products (Login Kit, Content Posting API with Upload), and the
  recording TikTok account must be added as a sandbox target user. Nothing in
  the code switches between sandbox and production — only the key pair.
- Use the internal test client (27, billing-exempt — nothing is billed on
  this path) and connect TikTok in step 3 so its grant is a new one.
- If client 27 was already connected, Disconnect on the TikTok card first so
  the consent screen appears in step 3.
- Card and outcome texts follow the client's stored `language` (client 27 is
  Hebrew). It is set at checkout only — the dashboard's language switch does
  NOT change it. For an English demo, set `clients.language` = `en` for
  client 27 in the Supabase table editor before recording (and back to `he`
  after), switch the dashboard's language flag to English, and write to the
  chat in English. With `en`, the connect chat (question, buttons, "connected"
  line), the send card, its success line and its refusals are all English.
- Put a short video (**64 MB or less** — TikTok's single-upload limit, see
  `core/video_limits.TIKTOK_INBOX_MAX_MB`) in the client's Drive media folder.
- The TikTok account must be allowed by the app (sandbox target user while
  unaudited), and its inbox must not already hold too many unpublished drafts
  (TikTok refuses more; the chat explains that with its own line).

Staff fallback (not for the demo): `bash scripts/tiktok_send_to_inbox.sh
CLIENT_ID DRIVE_FILE_ID` runs the same `tiktok_content_agent.publish` from
Cloud Shell via the admin endpoint.

## How the card works (for whoever maintains it)

`core/agent_actions` action `send_tiktok_to_inbox` (approver: client), same
propose → approve → execute shape as `publish_youtube_from_media`:
- **propose**: the model names a `drive_file_id` from
  `account_state.media_folder.videos` (`tiktok_uploadable`); the validator
  re-proves it is a video in the client's own Drive folder, ≤ 64 MB. The
  prepare step checks the grant (`tiktok_content_agent.check_grant`: the
  record in `core/tiktok_grant`, or one token refresh when there is none). Not
  connected / dead / no `video.upload` → the `connect_tiktok` card with a
  fixed line instead.
- **approve**: re-validated from the stored row, claimed once, then
  `tiktok_content_agent.publish` → `init_inbox_upload` → one-chunk upload →
  status polling until `SEND_TO_USER_INBOX`. A scope/dead-grant refusal at the
  click posts the reconnect line and returns the `connect_tiktok` card.
