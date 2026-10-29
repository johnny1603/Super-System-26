# Google scopes — what uallak requests and why

Source of truth for Google OAuth verification: the final scope list, the
Console diff, the scope justifications and the demo-video shot list. Every
statement here is taken from the code; file and function names are given so
each one can be checked. **If a scope list changes in code, change this file in
the same commit** — and the public privacy policy (`dashboard/privacy/index.html`
+ `dashboard/privacy/he/index.html`, served at `/privacy/`), whose "Google user
data" sections are written from this file.

## Every scope string in the code (grep, 2026-10-30)

The ONLY places a Google OAuth scope string exists (one constant per flow; every
consent URL is built from its constant with `" ".join(...)`, never by hand):

| File:line | Constant | Scope string |
|---|---|---|
| `core/client_auth.py:77` | `GOOGLE_LOGIN_SCOPES` | `openid` |
| `core/client_auth.py:77` | `GOOGLE_LOGIN_SCOPES` | `https://www.googleapis.com/auth/userinfo.email` |
| `core/google_ads_service.py:38` | `ADS_SCOPES` | `https://www.googleapis.com/auth/adwords` |
| `core/youtube_service.py:51` | `YOUTUBE_SCOPES` | `https://www.googleapis.com/auth/youtube.upload` |
| `core/youtube_service.py:52` | `YOUTUBE_SCOPES` | `https://www.googleapis.com/auth/youtube.readonly` |
| `core/gtm_service.py:65` | `GTM_SCOPES` | `https://www.googleapis.com/auth/tagmanager.edit.containers` |
| `core/gtm_service.py:66` | `GTM_SCOPES` | `https://www.googleapis.com/auth/tagmanager.edit.containerversions` |
| `core/gtm_service.py:67` | `GTM_SCOPES` | `https://www.googleapis.com/auth/tagmanager.publish` |
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

**Round 2 (built 2026-10-30, OFF):** `gmail.send` (`core/gmail_service.py`) and
`webmasters.readonly` (`core/search_console_service.py`) exist in the code behind
`GMAIL_SEND_ENABLED` / `SEARCH_CONSOLE_ENABLED`, both off by default. While off they
are never requested (consent builders raise, routes 404) and are NOT part of this
round. They are documented separately in `docs/GOOGLE-ROUND2.md`; the list below
is unchanged.

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

## Evidence matrix

One row per scope (audit 2026-10-30). "Requested in" is the only place the
string exists; every consent URL is built from that constant. Client-chat
flows are `core/agent_actions` actions: the agent proposes, the client
approves on the card, the server runs the named function.

| Scope | Requested in (code) | API calls that use it (code) | User-facing feature | Why a narrower scope is not enough |
|---|---|---|---|---|
| `openid` | `core/client_auth.py` `GOOGLE_LOGIN_SCOPES` → `google_login_url` | OpenID userinfo `sub` (`client_auth.google_identity`) | "Sign in with Google" on `/login` for an existing customer | Already the minimum: the stable account id is what links a Google login to one uallak account. `profile` was dropped 2026-10-17. |
| `userinfo.email` | `core/client_auth.py` `GOOGLE_LOGIN_SCOPES` | OpenID userinfo `email`, `email_verified` (`client_auth.google_identity` → `find_client_for_google`) | Same sign-in: the verified email finds the account the first time; a one-time emailed code confirms the link | Non-sensitive and the narrowest that returns a verified address; `openid` alone has no email. |
| `adwords` | `core/google_ads_service.py` `ADS_SCOPES` → `build_consent_url` (start route `/api/oauth/google-ads/start`, which refuses without the staff-access consent tick) | `customers:listAccessibleCustomers` (`list_accessible_customers`); `googleAds:search` GAQL (`google_ads_agent.get_campaign_performance` and the reads listed under Google Ads below); `googleAds:mutate` (`google_ads_agent.create_search_campaign` → `atomic_mutate`) | Account picker; Performance page + chat answers with the account's numbers; chat card **"Create a paused test campaign in Google Ads"** (`google_ads_test_campaign`, internal test clients) and staff-drafted campaigns for real clients — every campaign is created PAUSED | The Google Ads API has exactly one OAuth scope. No read-only or per-campaign variant exists. |
| `youtube.upload` | `core/youtube_service.py` `YOUTUBE_SCOPES` → `build_consent_url` | `videos.insert` resumable (`youtube_service.upload_video_stream` ← `youtube_content_agent.publish`) | Chat card **"Upload a video to YouTube (private)"** (`publish_youtube_from_media`): the card shows the file, title, description, hashtags and tags; the upload is always Private | Already the narrowest scope that can upload. The full `youtube` scope (edit/delete videos, playlists, comments) is not requested and never was. |
| `youtube.readonly` | `core/youtube_service.py` `YOUTUBE_SCOPES` | `channels.list mine=true` (`get_own_channel`), `playlistItems.list` (`list_recent_uploads`), `videos.list part=snippet,statistics,status` (`get_video_stats`) | Shows which channel is connected; view/like/comment counts of the client's own uploads on the Performance page | `youtube.upload` cannot call any read method, and our uploads are Private, so a public API key cannot see them. Read-only is the narrowest scope that can. |
| `tagmanager.edit.containers` | `core/gtm_service.py` `GTM_SCOPES` → `build_consent_url` | `accounts.list`, `containers.list`, `containers.get`, `workspaces.list`, `workspaces.getStatus`, `versions.live` (reads); `workspaces.create`, `workspaces.triggers.create`, `workspaces.tags.create`, and `workspaces.delete` / `triggers.delete` / `tags.delete` only for what this flow just created when a run fails (`gtm_service.lead_workspace`, `create_form_submit_trigger`, `create_ga4_lead_event_tag`, `website_agent._undo_gtm_drafts`) | Chat card **"Turn on lead tracking on your website"** (`configure_conversion`): adds one form-submission trigger and one GA4 `generate_lead` tag, so every lead from the site is counted | `tagmanager.readonly` cannot create a workspace, trigger or tag; `edit.containers` is the narrowest write scope. |
| `tagmanager.edit.containerversions` | `core/gtm_service.py` `GTM_SCOPES` | `workspaces.create_version` (`gtm_service.publish_workspace`) | Same card: the new trigger and tag become a container version | Versioning is excluded from `edit.containers` by design, and only this scope allows `create_version`. |
| `tagmanager.publish` | `core/gtm_service.py` `GTM_SCOPES` | `versions.publish` (`gtm_service.publish_workspace`) | Same card: the version goes live, so the tag actually fires on the site | Only this scope can publish. Without it the tag sits in a draft, and the site counts nothing. |

## Audit findings (2026-10-30)

- **Requested but unused:** none. Every scope above has at least one call,
  and each call is reachable from a client-facing flow.
- **Used but not requested:** none. `drive` belongs to uallak's own service
  account and never appears on a consent screen (see Drive below).
- **Defined but uncalled helpers:** `gtm_service.list_workspace_tags` and
  `list_workspace_triggers`, plus `google_ads_service.update_campaign_budget`
  and `set_campaign_bid_ceiling`. They need no extra scope and are not
  claimed in the justifications.
- **Not yet run against a live container:** the Tag Manager writes have only
  been exercised against tests (`tests/test_google_scope_flows.py`, an
  in-memory Tag Manager API). Before recording, run
  `bash scripts/gtm_lead_tracking_check.sh 27` (read only), then
  `bash scripts/gtm_lead_tracking_check.sh 27 G-XXXXXXX apply`, and confirm in
  `tagmanager.google.com` that the tag and version are live. If it can't be
  made to work reliably, remove the three `tagmanager.*` scopes from
  `GTM_SCOPES`, this file and the Console instead of showing them.
- `tests/test_google_scope_flows.py` `ScopeAudit` fails if a scope string
  appears in `core/` or `agents/` that is not on the final list, or if a
  scope loses its row here.

## Justifications (paste into the Console, one per scope)

**https://www.googleapis.com/auth/adwords** — uallak is a managed digital-marketing service: small businesses connect their own Google Ads account so that we can report on and run their campaigns. The app reads the Google Ads accounts the user's login can access (customers:listAccessibleCustomers), so the user picks which account we manage, and reads that account's campaigns and their performance (impressions, clicks, cost, conversions) with googleAds:search, which it shows on the user's performance page and in the dashboard assistant's answers. It also creates search campaigns in that account (googleAds:mutate: budget, campaign, ad group, keywords and one responsive search ad). A campaign is always created paused, so nothing is spent until the user enables it. The Google Ads API offers only this one OAuth scope; there is no narrower or read-only alternative.

**https://www.googleapis.com/auth/youtube.upload** — uallak produces marketing videos for the user's business. With this scope the app uploads a finished video to the user's own YouTube channel (videos.insert), always with privacy status Private, so the user reviews it in YouTube Studio and decides whether to publish it. Uploads happen when uallak's team publishes a video it produced, or when the user asks in the dashboard chat to upload a video from their own uallak media folder and approves it with one click on a confirmation card that shows the file and the exact title, description, tags and hashtags that will be sent; in every case the video is uploaded as Private. This is the narrowest scope that allows uploading; we deliberately do not request the full youtube scope, because the app never edits, deletes or manages existing videos, playlists or comments.

**https://www.googleapis.com/auth/youtube.readonly** — When the user connects YouTube, the app identifies the user's own channel (channels.list with mine=true) and its uploads playlist, and afterwards shows the view, like and comment counts of the user's recent uploads on their performance page (playlistItems.list, videos.list). The videos we upload are Private, so their statistics are visible only through the owner's authorization, not through a public API key. youtube.upload cannot call these read methods, and the read-only scope is the narrowest that can. Comment text, subscribers and watch history are never read.

**https://www.googleapis.com/auth/tagmanager.edit.containers** — uallak measures the leads that the user's website produces. The app finds the Google Tag Manager container installed on the user's site (accounts.list, containers.list, workspaces.list) and checks whether a lead-conversion tag is already live (versions.live). When the user asks in the dashboard chat to turn on lead tracking and approves the confirmation card, the app creates a workspace of its own (workspaces.create), so no unpublished change of anyone else is ever published, and adds one form-submission trigger and one Google Analytics 4 generate_lead event tag to it (workspaces.triggers.create, workspaces.tags.create). If a step fails, the app deletes only the workspace, trigger or tag it just created. tagmanager.readonly cannot create workspaces, triggers or tags, so this is the narrowest scope that can perform the setup. Existing tags are never changed or removed.

**https://www.googleapis.com/auth/tagmanager.edit.containerversions** — After adding the trigger and tag, the app creates a container version from the workspace (workspaces.create_version) so the change can be published. Creating a version requires exactly this scope; no narrower scope allows it.

**https://www.googleapis.com/auth/tagmanager.publish** — The app publishes that container version (versions.publish) so the lead-conversion tag goes live on the user's website and the user's leads are actually counted. Without publishing, the setup would sit unused in a workspace. Publishing requires exactly this scope; no narrower scope allows it.

**openid, https://www.googleapis.com/auth/userinfo.email** (non-sensitive) — "Sign in with Google" for existing uallak customers: the app reads the user's Google account identifier and verified email address once, to match them to their existing account. The first time a Google account is used with a uallak account, the user also enters a one-time code we email to that account's address before the two are linked. It never creates an account and requests no profile data.

## Demo video v3 — shot list and narration (one continuous recording)

Google's second reply (round 1) asked for: every scope justified by a
user-facing feature, why no narrower scope works, the consent screen with
the description of every scope, and Tag Manager API calls that are actually
seen. So, for EVERY scope the video shows the same three beats:

1. **Consent:** Google's consent screen, zoomed in on that scope's line and
   its description (also the address bar: `client_id=` and `scope=`).
2. **Feature:** the user-facing feature using it, from the client's own chat
   (agent proposes → the client presses Approve → it runs), then the result
   inside Google's own product.
3. **Narrower scope:** one spoken sentence on why a narrower scope would not
   work (the last column of the evidence matrix above).

Nothing is shown that does not really execute. Speak in English. Keep the
address bar visible throughout. The descriptions below are Google's wording
for each scope as of this writing; read whatever the screen actually shows.

### Before recording

1. **Deploy this commit.** The chat cards for the test campaign and the lead
   tag, and the Tag Manager cleanup, are new in it.
2. **Test client 27** (internal, `billing_exempt`; nothing is billed). Its
   package must include Google Ads, YouTube and website measurement.
3. **Language.** The cards follow the client's stored `clients.language`.
   Client 27 is Hebrew, and the dashboard's language switch does not change
   it. For an English video, set client 27's `clients.language` to `en` in
   the Supabase table editor (back to `he` afterwards).
4. **Google account:**
   - Owns a test Google Ads account and a test YouTube channel.
   - Has **Publish** permission on the Tag Manager container installed on
     client 27's website.
   - Know the site's GA4 measurement ID (`G-…`).
5. **Tag Manager container:**
   - No `generate_lead` tag live yet.
   - Fewer than 3 workspaces (the flow creates its own), or a default
     workspace with no unpublished changes.
6. **Media folder:** a short video (a few MB) in client 27's Drive media
   folder (Control Room → "מדיה ב-Drive").
7. **Disconnect first: Google Ads, YouTube and Tag Manager.**
   - Dashboard → each card → "Disconnect" (click twice). This revokes the
     token at Google.
   - Then check that `myaccount.google.com/permissions` no longer lists
     uallak. This makes every consent screen in the video a fresh one.
8. **Dry-run the real Tag Manager API** in Cloud Shell, before
   disconnecting:
   - `bash scripts/gtm_lead_tracking_check.sh 27` must answer
     `gtm_api_connected: True`.
   - If a full live run has never been done, do one, then roll it back
     (see "After approval"): `bash scripts/gtm_lead_tracking_check.sh 27
     G-XXXXXXX apply`.
   - Do not record the Tag Manager part until that apply has succeeded once.
     If it can't, remove the three `tagmanager.*` scopes from code, this
     file and the Console instead.
9. Signed out of uallak, in a fresh browser window, at `https://app.uallak.com`.

### Opening

1. Show `https://app.uallak.com/` and then `/privacy/` for a few seconds.
   Say: "uallak is a managed marketing service for small businesses. This
   video shows each Google permission the app asks for, the feature that
   uses it, and why a narrower permission would not work."

### Sign-in — `openid`, `userinfo.email` (non-sensitive)

2. **Consent.**
   - Sign out → `/login` → "Sign in with Google".
   - Zoom on the app name and the two lines: "Associate you with your
     personal info on Google" and "See your primary Google Account email
     address".
3. **Feature.**
   - The dashboard opens for the existing test account. If this Google
     account was never linked, enter the code emailed to the account's
     address first.
   - Say: "Sign-in matches an existing customer by their verified email."
4. **Narrower scope.** Say: "These two are the minimum for sign-in; no
   profile data is requested."

### Google Ads — `adwords`

5. **Feature start (chat).**
   - Open the dashboard chat with the Google specialist and type "Connect my
     Google Ads".
   - The card **"Connect Google Ads"** appears. Press **Approve**.
   - The dashboard scrolls to the Google Ads card.
6. **Staff-access consent.**
   - Zoom on the checkbox text (authorized uallak staff see the account and
     campaign performance and manage it). Read it aloud, tick it, click
     "Connect now".
   - The connect button refuses to start without it.
7. **Consent.**
   - Zoom on the address bar
     (`scope=https://www.googleapis.com/auth/adwords`) and on the permission
     line "See, edit, create, and delete your Google Ads accounts and data".
     Approve.
   - If the login reaches several accounts, pick the test account in the
     picker.
   - Say: "We list the accounts this login can access so the user picks the
     one we manage."
8. **Feature — read.**
   - Performance page → Google Ads card: spend, clicks, conversions.
   - Then ask in chat "How are my Google Ads campaigns doing?" and show the
     same numbers in the answer.
9. **Feature — write.**
   - In the chat type "Create a test campaign in my Google Ads".
   - The card **"Create a paused test campaign in Google Ads"** says it is
     created PAUSED, 5 ILS/day, and spends nothing unless enabled. Hold on
     it, press **Approve**.
   - The chat answers with the campaign name, ID and `Status: PAUSED`.
10. **Proof.**
    - `ads.google.com` → the test account → Campaigns. Show
      "uallak verification test …" with status Paused.
11. **Narrower scope.** Say: "The Google Ads API offers only this one scope;
    there is no read-only or narrower alternative."

### YouTube — `youtube.upload`, `youtube.readonly`

12. **Feature start (chat).** Ask the media specialist "Connect my YouTube
    channel". Card **"Connect your YouTube channel"** → **Approve**.
13. **Consent.**
    - Zoom on the address bar (`youtube.upload` and `youtube.readonly`, not
      `youtube`).
    - Zoom on each line: "Manage your YouTube videos" (upload) and "View your
      YouTube account" (readonly). Approve.
    - The YouTube card shows the channel name. Say: "readonly identifies the
      user's own channel."
14. **Feature — upload.**
    - In the chat type "Upload <file name> to my YouTube".
    - The card **"Upload a video to YouTube (private)"** shows the file, the
      title, description, hashtags and tags. Hold on it, press **Approve**.
    - The chat says it was uploaded as private, with the Studio link.
15. **Proof.** Open the link: the video in YouTube Studio with visibility
    **Private**.
16. **Feature — read.** Performance → YouTube card: views, likes and comment
    counts of the user's own uploads.
17. **Narrower scope.** Say: "youtube.upload is the narrowest scope that can
    upload; we don't ask for full YouTube management. youtube.readonly is
    the narrowest that can read the channel and the statistics of private
    uploads, which the upload scope cannot read."

### Tag Manager — `tagmanager.edit.containers`, `tagmanager.edit.containerversions`, `tagmanager.publish`

18. **Feature start (chat).**
    - Ask the website specialist "Connect my Tag Manager".
    - Card **"Reconnect your website tracking"** → **Approve**.
19. **Consent.**
    - Zoom on the address bar (the three `tagmanager.*` scopes) and on each
      line:
      - "Manage your Google Tag Manager container and its subcomponents,
        excluding versioning and publishing" (edit.containers).
      - "Manage your Google Tag Manager container versions"
        (edit.containerversions).
      - "Publish your Google Tag Manager container versions" (publish).
    - Approve.
    - The measurement card shows the connected container (GTM-XXXX).
20. **Before.** In `tagmanager.google.com` → the container: show Versions
    (the current live version) and Tags (no `generate_lead` tag).
21. **Feature.**
    - In the chat type "Track the leads from my website".
    - The card **"Turn on lead tracking on your website"** says one
      form-submission tracker (`generate_lead`) is added and published, and
      existing tags stay. Hold on it, press **Approve**.
    - The chat answers "Lead tracking on your site is live ✓" with the tag
      name and the version.
22. **Proof — the API calls, seen in Tag Manager.** Refresh
    `tagmanager.google.com` and show, one by one:
    - **Tags:** "uallak — GA4 generate_lead" (GA4 event, `generate_lead`,
      the site's G- id).
    - **Triggers:** "uallak — form submission".
    - **Versions:** a new version "uallak conversion tracking", **Live**,
      published by the connected account, with the note "uallak:
      form-submission trigger + GA4 generate_lead tag".
    Say: "edit.containers created the workspace, trigger and tag;
    edit.containerversions created this version; publish made it live."
23. **After.** Dashboard → Performance → website measurement card: the lead
    event is live.
24. **Narrower scope.** Say: "tagmanager.readonly cannot create a trigger or
    tag, and edit.containers deliberately excludes versioning and publishing.
    Creating a version and publishing it each require their own scope, and
    without publishing the tag would never run."

### Disconnect

25. Dashboard → each Google card → "Disconnect" (click twice). Then
    `myaccount.google.com/permissions`: uallak is no longer listed. Say:
    "Disconnecting revokes the token at Google and deletes it from our
    database."

### After approval (cleanup, after Google approves; not in the video)

- **Google Ads:** `ads.google.com` → Campaigns → "uallak verification test
  …" → Remove.
- **YouTube:** YouTube Studio → Content → the test video → Delete forever.
- **Tag Manager:**
  - Either: Versions → the version before "uallak conversion tracking" → ⋮
    → Publish (rollback).
  - Or: delete the tag "uallak — GA4 generate_lead" and the trigger "uallak
    — form submission", then Submit → Publish.
  - Check the live version no longer has the tag:
    `bash scripts/gtm_lead_tracking_check.sh 27`.
- **Client 27:** `clients.language` back to `he` if it was changed.
- Optionally reconnect the three cards for day-to-day testing.

**Merchant Center:** not in the video. The `content` scope was removed from
the app and must be removed from the Console.

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
| `google_ads_agent.create_search_campaign` → `google_ads_service.atomic_mutate` | `googleAds:mutate` | one campaign budget, one search campaign created **PAUSED**, location/language criteria, one ad group, keywords, one responsive search ad (admin-triggered: `POST /api/google-ads/create-campaign`, admin key; for the verification video also `core/verification_demo.create_test_campaign`, reached from the client's own chat card `google_ads_test_campaign` (the client approves), the Control Room demo button or an approved Control Room chat card — internal test clients only) |

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
| `youtube.upload` | `youtube_service.upload_video` (← `youtube_content_agent.publish`, reached from: admin `POST /api/youtube-content/publish` (admin key); the CLIENT's own click on a chat confirmation card, `core/agent_actions` `publish_youtube_from_media` — a video from their own Drive media folder, proven by Drive's parents chain, ≤ 512 MB (`core/video_limits`), streamed from Drive in 8 MiB chunks, max 3 uploads per client per day; and, for the verification video, the Control Room demo on internal test clients) | `videos.insert` (resumable) | **writes** one video with title, description, tags, categoryId, defaultLanguage/defaultAudioLanguage (for chat uploads: written by `agents/youtube_metadata` from context only and approved by the client on the card), `privacyStatus=private` (no caller can change it — there is no privacy parameter on the path), `selfDeclaredMadeForKids=false`. Same `youtube.upload` scope — no new scope. |

## Tag Manager — `tagmanager.edit.containers`, `tagmanager.edit.containerversions`, `tagmanager.publish`

Feature: conversion measurement — on the client's own GTM container, uallak
adds a form-submission trigger and a GA4 `generate_lead` event tag and
publishes them, so leads from the client's site are counted. Calls:
`core/gtm_service.py` (Tag Manager API v2); logic: `agents/website_agent.py`.

| Scope (API method that requires it) | Function | Call | Data |
|---|---|---|---|
| `tagmanager.edit.containers` | `gtm_service.list_accounts`, `list_containers`, `find_container_by_public_id` | `accounts.list`, `accounts.containers.list` | account/container paths and public ids (GTM-XXXX) — on connect, to find the container installed on the client's site |
| `tagmanager.edit.containers` | `gtm_service.lead_workspace` | `workspaces.create` (fallback: `workspaces.list` + `workspaces.getStatus`) | **writes** a workspace of our own, "uallak lead tracking", based on the latest version, so only our trigger and tag get published. GTM deletes it when the version is created. If one can't be created (free containers allow 3), the default workspace is used, but only when it has no unpublished changes; otherwise the flow stops. |
| `tagmanager.edit.containers` | `website_agent._undo_gtm_drafts` | `workspaces.delete`, `triggers.delete`, `tags.delete` | after a FAILED run only: removes the workspace (ours) or the trigger/tag this run created (shared workspace) — never anything else |
| `tagmanager.edit.containers` | `gtm_service.get_live_version` (← `website_agent.get_conversion_tracking_status`) | `versions.live` | the published version's tags and triggers — is a lead event already live? |
| `tagmanager.edit.containers` | `gtm_service.create_form_submit_trigger` | `workspaces.triggers.create` | **writes** one trigger, type `formSubmission` |
| `tagmanager.edit.containers` | `gtm_service.create_ga4_lead_event_tag` | `workspaces.tags.create` | **writes** one GA4 event tag (`gaawe`, event `generate_lead`, the client's G- measurement id) |
| `tagmanager.edit.containerversions` | `gtm_service.publish_workspace` | `workspaces.create_version` | **writes** a container version from the workspace |
| `tagmanager.publish` | `gtm_service.publish_workspace` | `versions.publish` | **publishes** that version (makes it live) |

All writes run from `website_agent.configure_lead_conversion`. The
user-facing path is the client chat card `configure_conversion` (the client
approves it; since 2026-10-30, before that staff approved it). The same
function backs the Control Room button
(`POST /api/admin/control-room/{client_id}/configure-conversion`),
`POST /api/website/configure-conversion` (admin key) and
`scripts/gtm_lead_tracking_check.sh`. It refuses, before any write, when:
- a lead-event tag is already live;
- the stored grant lacks any of the three scopes
  (`website_agent.gtm_missing_scopes`);
- the grant is dead;
- the site loads a different container;
- no GA4 id is known.

Every failure carries a `reason`, and the chat answers it with a plain line
in the client's language (Hebrew by default; `core/agent_actions`
`configure_conversion` refusals): reconnect (plus the reconnect card),
wrong container, missing G- id, already live, no Publish permission in GTM,
workspace busy. Our own failures (API disabled in the Cloud project, compiler
error, anything else) get the generic line and an alert. The callback records the scopes Google
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
