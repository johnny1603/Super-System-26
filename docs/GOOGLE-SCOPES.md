# Google scopes — what uallak requests and why

Source of truth for Google OAuth verification: the final scope list, the
Console diff, the scope justifications and the demo-video shot list. Every
statement here is taken from the code; file and function names are given so
each one can be checked. **If a scope list changes in code, change this file in
the same commit** — and the public privacy policy (`dashboard/privacy/index.html`
+ `dashboard/privacy/he/index.html`, served at `/privacy/`), whose "Google user
data" sections are written from this file.

## Every scope string in the code (grep, 2026-10-21)

The ONLY places a Google OAuth scope string exists (one constant per flow; every
consent URL is built from its constant with `" ".join(...)`, never by hand):

| File:line | Constant | Scope string |
|---|---|---|
| `core/client_auth.py:77` | `GOOGLE_LOGIN_SCOPES` | `openid` |
| `core/client_auth.py:77` | `GOOGLE_LOGIN_SCOPES` | `https://www.googleapis.com/auth/userinfo.email` |
| `core/google_ads_service.py:38` | `ADS_SCOPES` | `https://www.googleapis.com/auth/adwords` |
| `core/youtube_service.py:51` | `YOUTUBE_SCOPES` | `https://www.googleapis.com/auth/youtube.upload` |
| `core/youtube_service.py:52` | `YOUTUBE_SCOPES` | `https://www.googleapis.com/auth/youtube.readonly` |
| `core/gtm_service.py:63` | `GTM_SCOPES` | `https://www.googleapis.com/auth/tagmanager.edit.containers` |
| `core/gtm_service.py:64` | `GTM_SCOPES` | `https://www.googleapis.com/auth/tagmanager.edit.containerversions` |
| `core/gtm_service.py:65` | `GTM_SCOPES` | `https://www.googleapis.com/auth/tagmanager.publish` |
| `core/drive_service.py:43` | `DRIVE_SCOPES` | `https://www.googleapis.com/auth/drive` — **service account only, never a user consent; not on the consent screen** |

Consent-URL builders: `client_auth.google_login_url`,
`google_ads_service.build_consent_url`, `youtube_service.build_consent_url`,
`gtm_service.build_consent_url`. None sets `include_granted_scopes`, so a grant
never silently carries scopes from another flow.

**The full `https://www.googleapis.com/auth/youtube` scope has never been in
the code** — not today and not in any commit (`git log -G"auth/youtube[^.a-z]"`
finds only the `.upload`/`.readonly` lines). The YouTube flow has always asked
for `youtube.upload` + `youtube.readonly`. The callbacks now log the scopes
Google actually granted (`[youtube oauth] client N: granted scopes [...]`,
same for `[google_ads oauth]`; the GTM callback already stored them), so this
can be shown from Cloud Run logs.

**Removed 2026-10-21:** `https://www.googleapis.com/auth/content` (Merchant
Center). It had no feature a client could see: no screen showed feed status,
the feed scan was never scheduled, and `core/performance.py` could only ever say
"connected". The connect flow, `core/merchant_center_service.py` and the
dashboard card are gone. Grants made before that are revoked at Google and
deleted by `POST /api/admin/merchant-center/retire` (admin key, safe to re-run).

**Changed 2026-10-21:** sign-in requests
`https://www.googleapis.com/auth/userinfo.email` instead of the `email` alias.
Google treats them as the same scope (same consent text, same token); the full
URL is what the Console lists, so code and Console now match string-for-string.

## Final list — exactly what the code requests

```
openid
https://www.googleapis.com/auth/userinfo.email
https://www.googleapis.com/auth/adwords
https://www.googleapis.com/auth/youtube.upload
https://www.googleapis.com/auth/youtube.readonly
https://www.googleapis.com/auth/tagmanager.edit.containers
https://www.googleapis.com/auth/tagmanager.edit.containerversions
https://www.googleapis.com/auth/tagmanager.publish
```

## Console diff (against the 7 registered on 2026-10-21)

Registered: `content`, `tagmanager.publish`, `tagmanager.edit.containerversions`,
`tagmanager.edit.containers`, `adwords`, `youtube.readonly`, `youtube`.

```
REMOVE from Console: https://www.googleapis.com/auth/youtube
REMOVE from Console: https://www.googleapis.com/auth/content
ADD to Console:      https://www.googleapis.com/auth/youtube.upload
ADD to Console:      openid                                          (non-sensitive)
ADD to Console:      https://www.googleapis.com/auth/userinfo.email  (non-sensitive)
KEEP:                https://www.googleapis.com/auth/adwords
KEEP:                https://www.googleapis.com/auth/youtube.readonly
KEEP:                https://www.googleapis.com/auth/tagmanager.edit.containers
KEEP:                https://www.googleapis.com/auth/tagmanager.edit.containerversions
KEEP:                https://www.googleapis.com/auth/tagmanager.publish
```

Sensitive scopes to submit for verification (6): `adwords`, `youtube.upload`,
`youtube.readonly`, `tagmanager.edit.containers`,
`tagmanager.edit.containerversions`, `tagmanager.publish`.

## Least privilege, scope by scope

| Scope | User-facing feature | Exact API calls | Narrower scope possible? |
|---|---|---|---|
| `openid` + `userinfo.email` | "Sign in with Google" on `/login` | OpenID userinfo (`sub`, `email`, `email_verified`) | No — the minimum for sign-in. `profile` was dropped 2026-10-17. |
| `adwords` | Account picker, performance page + chat answers (reads); search-campaign creation, always PAUSED (write) | `customers:listAccessibleCustomers`, `googleAds:search` (GAQL), `googleAds:mutate` | No — `adwords` is the Google Ads API's only OAuth scope; no read-only variant exists. |
| `youtube.upload` | Publishing a video to the client's channel, always Private — one uallak produced, or one from the client's media folder that the client approved in chat | `videos.insert` (resumable) | Already the narrowest for uploads. The full `youtube` scope (manage/delete all videos, playlists, comments) is NOT requested. |
| `youtube.readonly` | Channel identification on connect; view/like/comment counts on the performance page | `channels.list mine=true`, `playlistItems.list`, `videos.list part=snippet,statistics,status` | No — `youtube.upload` cannot call `channels.list`/`videos.list`, and our uploads are Private, so the public API key cannot see them. |
| `tagmanager.edit.containers` | Lead-conversion tracking: find the site's container, check the live version, add a form-submit trigger + GA4 `generate_lead` tag | `accounts.list`, `containers.list`, `workspaces.list`, `versions.live`, `workspaces.triggers.create`, `workspaces.tags.create` | No — `tagmanager.readonly` cannot create triggers or tags. |
| `tagmanager.edit.containerversions` | Same feature: turn the workspace change into a version | `workspaces.create_version` | No — creating a version requires exactly this scope. |
| `tagmanager.publish` | Same feature: make the version live so leads are counted | `versions.publish` | No — publishing requires exactly this scope. Without it the tag would sit unpublished and nothing would be measured. |

## Justifications (paste into the Console, one per scope)

**https://www.googleapis.com/auth/adwords** — uallak is a managed digital-marketing service: small businesses connect their own Google Ads account so that we can report on and run their campaigns. The app reads the Google Ads accounts the user's login can access (customers:listAccessibleCustomers), so the user picks which account we manage, and reads that account's campaigns and their performance (impressions, clicks, cost, conversions) with googleAds:search, which it shows on the user's performance page and in the dashboard assistant's answers. It also creates search campaigns in that account (googleAds:mutate: budget, campaign, ad group, keywords and one responsive search ad). A campaign is always created paused, so nothing is spent until the user enables it. The Google Ads API offers only this one OAuth scope; there is no narrower or read-only alternative.

**https://www.googleapis.com/auth/youtube.upload** — uallak produces marketing videos for the user's business. With this scope the app uploads a finished video to the user's own YouTube channel (videos.insert), always with privacy status Private, so the user reviews it in YouTube Studio and decides whether to publish it. Uploads happen when uallak's team publishes a video it produced, or when the user asks in the dashboard chat to upload a video from their own uallak media folder and approves it with one click on a confirmation card that names the file; in every case the video is uploaded as Private. This is the narrowest scope that allows uploading; we deliberately do not request the full youtube scope, because the app never edits, deletes or manages existing videos, playlists or comments.

**https://www.googleapis.com/auth/youtube.readonly** — When the user connects YouTube, the app identifies the user's own channel (channels.list with mine=true) and its uploads playlist, and afterwards shows the view, like and comment counts of the user's recent uploads on their performance page (playlistItems.list, videos.list). The videos we upload are Private, so their statistics are visible only through the owner's authorization, not through a public API key. youtube.upload cannot call these read methods, and the read-only scope is the narrowest that can. Comment text, subscribers and watch history are never read.

**https://www.googleapis.com/auth/tagmanager.edit.containers** — uallak measures the leads that the user's website produces. The app finds the Google Tag Manager container installed on the user's site (accounts.list, containers.list, workspaces.list), checks whether a lead-conversion tag is already live (versions.live), and if not, adds one form-submission trigger and one Google Analytics 4 generate_lead event tag to the container's workspace (workspaces.triggers.create, workspaces.tags.create). tagmanager.readonly cannot create triggers or tags, so this is the narrowest scope that can perform the setup. Existing tags are never changed or removed.

**https://www.googleapis.com/auth/tagmanager.edit.containerversions** — After adding the trigger and tag, the app creates a container version from the workspace (workspaces.create_version) so the change can be published. Creating a version requires exactly this scope; no narrower scope allows it.

**https://www.googleapis.com/auth/tagmanager.publish** — The app publishes that container version (versions.publish) so the lead-conversion tag goes live on the user's website and the user's leads are actually counted. Without publishing, the setup would sit unused in a workspace. Publishing requires exactly this scope; no narrower scope allows it.

**openid, https://www.googleapis.com/auth/userinfo.email** (non-sensitive) — "Sign in with Google" for existing uallak customers: the app reads the user's Google account identifier and verified email address once, to match them to their existing account. The first time a Google account is used with a uallak account, the user also enters a one-time code we email to that account's address before the two are linked. It never creates an account and requests no profile data.

## Demo video — shot list (one continuous recording)

Record after deploying this commit, on `https://app.uallak.com`, with an
INTERNAL (billing-exempt, admin "New Client") test client — the two demo
buttons in step 8 and 12 refuse any other client — whose package includes
Google Ads, YouTube and website measurement, a
Google account that owns a test Google Ads account, a test YouTube channel and
a Tag Manager container installed on the test client's website. Keep the
browser address bar visible throughout (Google checks the `client_id=` in the
consent URL). Speak in English.

**Opening**
1. Show `https://app.uallak.com/privacy/` for a few seconds. Say: "This is uallak, a managed marketing service for small businesses. This video shows every Google scope the app requests and what it does with each."
2. Sign out, open `/login`, click "Sign in with Google". On Google's consent screen, zoom on the app name and the requested permissions (email only). Say: "Sign-in uses openid and userinfo.email only, to match an existing customer account." If this Google account was never used to sign in to the test client before, the login page asks for the code emailed to the client's address — show the email arriving and enter it ("the first Google sign-in is confirmed with a code sent to the account's email"). Land on the dashboard.

**Google Ads — `adwords`**
3. In the dashboard, scroll to Connections → Google Ads. Tick the staff-access consent box (read it aloud), click "Connect now".
4. On Google's consent screen, zoom on the address bar (`scope=https://www.googleapis.com/auth/adwords`, `client_id=...`) and the permission line. Approve.
5. Back in the dashboard: if the login reaches several accounts, pick the test account in the account picker. Say: "We list the accounts this login can access so the user chooses which one we manage."
6. Open the Performance page. Show the Google Ads card's spend, clicks and conversions. Say: "Read access: campaign performance shown to the user."
7. Open the dashboard chat and ask "How are my Google Ads campaigns doing?". Show the answer quoting the same numbers.
8. Open the admin dashboard (`/admin`) → Control Room → the test client → click **"יצירת קמפיין בדיקה ב-Google Ads"** and confirm. Show the success panel with the campaign name ("uallak verification test …"), campaign ID and account. Say: "uallak staff create campaigns in the user's account through the same code path as the real feature; a campaign is always created paused." (The button exists only for internal test clients; it runs `google_ads_agent.create_search_campaign`, the function behind real campaign creation.)
9. Open `ads.google.com` → the test account → Campaigns. Show the new campaign, its name and status Paused. Say: "The write is reflected in the user's Google Ads account."
10. Nothing else to show for `adwords`: the justification and the privacy policy claim only the reads in steps 5–7 and the paused-campaign creation in steps 8–9. Auto-pausing disapproved campaigns and keyword/ad-copy optimization are NOT claimed; both run only when `GOOGLE_ADS_OPTIMIZATION_MODE=live`, and production must stay on the default `dry_run` (decide + log, no write). Switching it to `live` means updating the justification, the policy and this video first.

**YouTube — `youtube.upload`, `youtube.readonly`**
11. Dashboard → Connections → YouTube → "Connect now". On the consent screen, zoom on the address bar (`youtube.upload` and `youtube.readonly`, NOT `youtube`) and the two permission lines. Approve. Show the card turning "Connected" with the channel.
12. Before recording, put a short video (up to 50 MB) in the test client's Drive media folder (Control Room → "מדיה ב-Drive"). In the Control Room click **"העלאת סרטון בדיקה ל-YouTube"** and confirm. Show the success panel with the title ("uallak verification test …"), video ID and the YouTube Studio link. Say: "uallak uploads a video it produced, always as Private." (It runs `youtube_content_agent.publish`, the real publishing path, with the smallest video in the client's media folder.)
13. Click the YouTube Studio link from the success panel (or `studio.youtube.com` → Content). Show the new video, its title, and visibility **Private**. Say: "The upload is reflected in the user's YouTube Studio; the user decides whether to publish."
14. Back in the dashboard → Performance → YouTube card: show the view, like and comment counts. Say: "Read access: counts for the user's own, private uploads."

**Tag Manager — the three `tagmanager` scopes**
15. Dashboard → Connections → measurement card → connect Google Tag Manager. On the consent screen, zoom on the address bar (the three `tagmanager.*` scopes) and the permission lines. Approve. Show the card connected with the container id (GTM-XXXX).
16. In Tag Manager (`tagmanager.google.com`), show the container's current published version: no `generate_lead` tag yet.
17. Open the admin dashboard (`/admin`) → Control Room → the test client → "configure conversion" button. Run it and show the success result. Say: "This adds one form-submission trigger and one GA4 generate_lead tag, creates a version and publishes it."
18. Back in Tag Manager: refresh. Show Versions → the new version published by the app, and Tags → the new GA4 event tag `generate_lead` with its form-submission trigger. Say: "edit.containers created the tag and trigger, edit.containerversions created the version, publish made it live."
19. Back in the client dashboard → Performance → website measurement card: show it reporting the lead tag as live.

**Disconnect**
20. Dashboard → each Google card → "Disconnect" (click twice to confirm). Then open `myaccount.google.com/permissions` and show uallak no longer listed. Say: "Disconnecting revokes the token at Google and deletes it from our database."

**Merchant Center:** not in the video — the `content` scope was removed from the app and must be removed from the Console.

## Storage and revocation

Refresh tokens are stored per client in `client_accounts` (one row per
connection), encrypted (`core/secrets_vault.py`). Disconnecting a Google
connection from the dashboard calls `api_server._disconnect_platform`, which
revokes the refresh token at Google (`google_ads_service.revoke_token`,
`https://oauth2.googleapis.com/revoke`) and deletes the stored rows. Only our
access is removed; nothing in the client's own Google accounts is changed.

**OAuth client.** The four user-consent flows (sign-in, Ads, YouTube, Tag
Manager) all use one OAuth client: `GOOGLE_OAUTH_CLIENT_ID` /
`GOOGLE_OAUTH_CLIENT_SECRET` (`agents/keys_agent.py`). Each flow is a SEPARATE
consent with only its own scopes, on its own callback
(`/api/oauth/<flow>/callback`), so a client is only ever asked for what the
feature they are connecting needs. Ads, YouTube and Tag Manager request
`access_type=offline` + `prompt=consent` (a refresh token is stored); sign-in
does not (the access token is used once and discarded).

---

## Google sign-in — `openid`, `userinfo.email`

Feature: a client who already has a uallak account can sign in to their
dashboard with Google instead of an emailed code or a password. It never
creates an account.

- `core/client_auth.py` · `google_login_url` builds the consent URL with
  `GOOGLE_LOGIN_SCOPES`; `google_identity` exchanges the code and calls the
  OpenID userinfo endpoint once (`GOOGLE_USERINFO_URL`).
- **Read:** `sub`, `email`, `email_verified` — used by `find_client_for_google`
  (match by stored `sub`, else by the VERIFIED email) and `link_google`
  (stores `sub` on the client row).
- **First sign-in to an account (2026-10-22):** an email match that is not yet
  linked is NOT signed in. uallak emails the account's standard 6-digit login
  code; only after it is entered does `complete_google_link` store `sub` and
  set the session. Already-linked `sub` → straight in; a different `sub` →
  refused. No extra scope or consent — the gate is our own email code.
- **Written:** nothing at Google. Stored by us: `clients.google_sub`.
- `profile` (name, picture) was removed on 2026-10-17 because nothing reads it.

## Google Ads — `https://www.googleapis.com/auth/adwords`

Feature: uallak manages the client's own Google Ads account — reporting, and
creating search campaigns (always created PAUSED). That is everything the
justification and the privacy policy claim. Automatic pausing of disapproved
campaigns and keyword/ad-copy optimization exist in code but are gated on
`GOOGLE_ADS_OPTIMIZATION_MODE=live`; on the default `dry_run` they only decide,
log and alert, and make no write. `adwords` is the Google Ads API's only OAuth
scope. All calls: `core/google_ads_service.py` (REST,
`googleads.googleapis.com/v25`); business logic: `agents/google_ads_agent.py`.

**Read**
| Function | Call | Data |
|---|---|---|
| `google_ads_service.list_accessible_customers` | `customers:listAccessibleCustomers` | ids of the accounts the client's login can reach (account picker) |
| `google_ads_service.describe_customers` | GAQL on `customer` | id, descriptive name, status, manager flag, currency |
| `google_ads_agent.get_campaign_performance` | GAQL on `campaign` | campaign id, name, status; impressions, clicks, cost, conversions (last 30 days) — dashboard, chat and reports |
| `google_ads_agent.get_conversions_yesterday` | GAQL on `campaign` | conversions, yesterday — sales alert emails |
| `google_ads_agent.run_health_scan` | GAQL on `customer`, `ad_group_ad`, `campaign` | account status; ad policy approval status and policy topics; campaign primary status and reasons |
| `google_ads_agent._fetch_two_window_metrics` | GAQL on `campaign` by `segments.date` | daily impressions, clicks, cost, conversions (weekly report, problem detection) |
| `google_ads_agent._keyword_performance` | GAQL on `keyword_view` | keyword text, ids, clicks, conversions, cost |
| `google_ads_agent.optimize_ad_copy` | GAQL on `ad_group_ad` | responsive search ad headlines and descriptions |
| `google_ads_agent._final_url_for` | GAQL on `ad_group_ad` | ads' final URLs |

**Written**
| Function | Call | What changes |
|---|---|---|
| `google_ads_agent.create_search_campaign` → `google_ads_service.atomic_mutate` | `googleAds:mutate` | one campaign budget, one search campaign created **PAUSED**, location/language criteria, one ad group, keywords, one responsive search ad (admin-triggered: `POST /api/google-ads/create-campaign`, admin key; for the verification video also `core/verification_demo.create_test_campaign`, reached from the Control Room demo button or an approved Control Room chat card, internal test clients only) |

**Gated off — not claimed to Google** (write only when
`GOOGLE_ADS_OPTIMIZATION_MODE=live`; production stays on the default `dry_run`):
| Function | Call | What it would change |
|---|---|---|
| `google_ads_agent.pause_campaign` → `set_campaign_status` (← `run_health_scan`, disapproved ads) | `campaigns:mutate` | campaign status PAUSED. On `dry_run` the scan only alerts ("NOT paused"). `resume_campaign` has no caller. |
| `google_ads_agent.optimize_keywords` → `mutate_ad_group_criteria` | `adGroupCriteria:mutate` | status PAUSED on a keyword with ≥ threshold clicks and zero conversions |
| `google_ads_agent.optimize_ad_copy` → `mutate_ad_group_ads` | `adGroupAds:mutate` | creates a new responsive search ad variant |

Defined but not called by any code path today:
`google_ads_service.update_campaign_budget`, `set_campaign_bid_ceiling`.

## YouTube — `youtube.upload`, `youtube.readonly`

Feature: uploads videos uallak produced for the client to the client's own
channel — always as **PRIVATE** (the client makes them public in YouTube
Studio) — and reports views/likes/comment counts. Calls:
`core/youtube_service.py`; logic: `agents/youtube_content_agent.py`.

| Scope | Function | Call | Data |
|---|---|---|---|
| `youtube.readonly` | `youtube_service.get_own_channel` | `channels.list` `mine=true` | channel id, title, uploads playlist id (on connect, `run_channel_detection_scan`, engagement) |
| `youtube.readonly` | `youtube_service.list_recent_uploads` | `playlistItems.list` (uploads playlist) | ids of the channel's newest uploads |
| `youtube.readonly` | `youtube_service.get_video_stats` | `videos.list` `part=snippet,statistics,status` | title, publish date, privacy status, view/like/comment COUNTS. Needed with OAuth because the uploads are private. Comment text is never read. |
| `youtube.upload` | `youtube_service.upload_video` (← `youtube_content_agent.publish`, reached from: admin `POST /api/youtube-content/publish` (admin key); the CLIENT's own click on a chat confirmation card, `core/agent_actions` `publish_youtube_from_media` — a video from their own Drive media folder, proven by Drive's parents chain, ≤ 50 MB, max 3 uploads per client per day; and, for the verification video, the Control Room demo on internal test clients) | `videos.insert` (resumable) | **writes** one video with title, description, `privacyStatus=private` (no caller can change it — `publish()` forwards no privacy field), `selfDeclaredMadeForKids=false` |

## Tag Manager — `tagmanager.edit.containers`, `tagmanager.edit.containerversions`, `tagmanager.publish`

Feature: conversion measurement — on the client's own GTM container, uallak
adds a form-submission trigger and a GA4 `generate_lead` event tag and
publishes them, so leads from the client's site are counted. Calls:
`core/gtm_service.py` (Tag Manager API v2); logic: `agents/website_agent.py`.

| Scope (API method that requires it) | Function | Call | Data |
|---|---|---|---|
| `tagmanager.edit.containers` | `gtm_service.list_accounts`, `list_containers`, `find_container_by_public_id` | `accounts.list`, `accounts.containers.list` | account/container paths and public ids (GTM-XXXX) — on connect, to find the container installed on the client's site |
| `tagmanager.edit.containers` | `gtm_service.default_workspace_path` | `workspaces.list` | the container's workspaces (first one is used) |
| `tagmanager.edit.containers` | `gtm_service.get_live_version` (← `website_agent.get_conversion_tracking_status`) | `versions.live` | the published version's tags and triggers — is a lead event already live? |
| `tagmanager.edit.containers` | `gtm_service.create_form_submit_trigger` | `workspaces.triggers.create` | **writes** one trigger, type `formSubmission` |
| `tagmanager.edit.containers` | `gtm_service.create_ga4_lead_event_tag` | `workspaces.tags.create` | **writes** one GA4 event tag (`gaawe`, event `generate_lead`, the client's G- measurement id) |
| `tagmanager.edit.containerversions` | `gtm_service.publish_workspace` | `workspaces.create_version` | **writes** a container version from the workspace |
| `tagmanager.publish` | `gtm_service.publish_workspace` | `versions.publish` | **publishes** that version (makes it live) |

All writes run from `website_agent.configure_lead_conversion` — from the admin
Control Room's "configure conversion" button
(`POST /api/admin/control-room/{client_id}/configure-conversion`) or
`POST /api/website/configure-conversion` (admin key). It refuses if a lead-event
tag is already live and if the stored grant lacks any of the three scopes
(`website_agent.gtm_missing_scopes`). The callback records the scopes Google
actually granted (`gtm_api_connected` activity, `scopes`); a grant with no
record predates `tagmanager.edit.containerversions` and the dashboard card asks
the client to reconnect.

Defined but not called today: `gtm_service.list_workspace_tags`,
`list_workspace_triggers`.

## Drive — `https://www.googleapis.com/auth/drive` (service account)

Not a user consent: `GOOGLE_SERVICE_ACCOUNT_JSON` authenticates uallak's own
service account (`drive_service._access_token`), which works only in Shared
Drive folders that were shared with it (`DRIVE_ARCHIVE_FOLDER_ID`,
`DRIVE_MEDIA_FOLDER_ID`). It never sees a client's personal Drive. **Do not add
it to the consent screen.**

- **Read:** `list_files`, `list_children`, `get_meta`, `fetch_thumbnail`,
  `open_download_stream`, `download_file`, `find_files_by_prefix`,
  `find_child_folders_by_prefix` — the per-client media folders (Media tab,
  `core/drive_mirror.py` / `core/drive_download.py`; video source for YouTube
  publishing).
- **Written:** `ensure_folder` (per-client folders), `upload_bytes` (generated
  media, filming kits, avatar source files), `upload_json` (offboarding
  archive), `upload_google_doc` (content docs, exports), `share_with_user`
  (shares a doc with the client's email), `make_file_public` (anyone-with-link
  URL for a file handed to a publishing/avatar provider —
  `media_agent.prepare_for_publishing`, `avatar_agent`), `trash_file` (the
  Drive self-test's probe file, `media_agent.drive_self_test`).
- Why not `drive.file`: clients add their own files to their media folder, and
  `drive.file` only sees files the app itself created.
