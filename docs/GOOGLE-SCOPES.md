# Google scopes — what uallak requests and why

Source of truth for Google OAuth verification (scope justifications and the
demo-video script). Every statement here is taken from the code; file and
function names are given so each one can be checked. **If a scope list changes
in code, change this file in the same commit** — and the public privacy policy
(`dashboard/privacy/index.html` + `dashboard/privacy/he/index.html`, served at
`/privacy/`), whose "Google user data" sections are written from this file.

## Summary

| Flow | Constant (the only place the list lives) | Scopes |
|---|---|---|
| Google sign-in | `core/client_auth.py` · `GOOGLE_LOGIN_SCOPES` | `openid`, `email` |
| Google Ads | `core/google_ads_service.py` · `ADS_SCOPES` | `https://www.googleapis.com/auth/adwords` |
| Merchant Center | `core/merchant_center_service.py` · `MERCHANT_SCOPES` | `https://www.googleapis.com/auth/content` |
| YouTube | `core/youtube_service.py` · `YOUTUBE_SCOPES` | `https://www.googleapis.com/auth/youtube.upload`, `https://www.googleapis.com/auth/youtube.readonly` |
| Tag Manager | `core/gtm_service.py` · `GTM_SCOPES` | `https://www.googleapis.com/auth/tagmanager.edit.containers`, `https://www.googleapis.com/auth/tagmanager.edit.containerversions`, `https://www.googleapis.com/auth/tagmanager.publish` |
| Drive (service account — no user consent) | `core/drive_service.py` · `DRIVE_SCOPES` | `https://www.googleapis.com/auth/drive` |

No other Google scope is requested anywhere (no Analytics, Gmail, Calendar,
Docs or Business Profile scope). YouTube niche search
(`youtube_service.search_videos`) uses a plain API key, not OAuth.

**OAuth client.** The five user-consent flows (sign-in, Ads, Merchant Center,
YouTube, Tag Manager) all use one OAuth client: `GOOGLE_OAUTH_CLIENT_ID` /
`GOOGLE_OAUTH_CLIENT_SECRET` (`agents/keys_agent.py`). Each flow is a SEPARATE
consent with only its own scopes, on its own callback
(`/api/oauth/<flow>/callback`), so a client is only ever asked for what the
feature they are connecting needs. Ads, Merchant Center, YouTube and Tag
Manager request `access_type=offline` + `prompt=consent` (a refresh token is
stored); sign-in does not (the access token is used once and discarded).
*Observed, not in code (2026-10-17): the live client id is
`554652696367-27u4icsuvm7bpmtrovg28ll0gj193hqi.apps.googleusercontent.com`,
i.e. Cloud project number 554652696367 — where the consent screen and
verification for these scopes are managed.*

**Storage and revocation.** Refresh tokens are stored per client in
`client_accounts` (one row per connection). Disconnecting a Google connection
from the dashboard calls `api_server._disconnect_platform`, which revokes the
refresh token at Google (`google_ads_service.revoke_token`,
`https://oauth2.googleapis.com/revoke`) and deletes the stored rows. Only our
access is removed; nothing in the client's own Google accounts is changed.

---

## Google sign-in — `openid`, `email`

Feature: a client who already has a uallak account can sign in to their
dashboard with Google instead of an emailed code or a password. It never
creates an account.

- `core/client_auth.py` · `google_login_url` builds the consent URL with
  `GOOGLE_LOGIN_SCOPES`; `google_identity` exchanges the code and calls the
  OpenID userinfo endpoint once (`GOOGLE_USERINFO_URL`).
- **Read:** `sub`, `email`, `email_verified` — used by `find_client_for_google`
  (match by stored `sub`, else by the VERIFIED email) and `link_google`
  (stores `sub` on the client row; logs the Google email in the activity log).
- **Written:** nothing at Google. Stored by us: `clients.google_sub`.
- `profile` (name, picture) was removed on 2026-10-17 because nothing reads it.

## Google Ads — `https://www.googleapis.com/auth/adwords`

Feature: uallak manages the client's own Google Ads account — reporting,
creating search campaigns (always created PAUSED), pausing campaigns whose ads
are disapproved, and optimizing keywords and ad copy. `adwords` is the Google
Ads API's only OAuth scope. All calls: `core/google_ads_service.py` (REST,
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
| `google_ads_agent.create_search_campaign` → `google_ads_service.atomic_mutate` | `googleAds:mutate` | one campaign budget, one search campaign created **PAUSED**, location/language criteria, one ad group, keywords, one responsive search ad (admin-triggered: `POST /api/google-ads/create-campaign`) |
| `google_ads_agent.pause_campaign` / `resume_campaign` → `set_campaign_status` | `campaigns:mutate` | campaign status PAUSED / ENABLED (the health scan auto-pauses a campaign with disapproved ads) |
| `google_ads_agent.optimize_keywords` → `mutate_ad_group_criteria` | `adGroupCriteria:mutate` | status PAUSED on a keyword with ≥ threshold clicks and zero conversions |
| `google_ads_agent.optimize_ad_copy` → `mutate_ad_group_ads` | `adGroupAds:mutate` | creates a new responsive search ad variant |

Defined but not called by any code path today:
`google_ads_service.update_campaign_budget`, `set_campaign_bid_ceiling`.

## Merchant Center — `https://www.googleapis.com/auth/content`

Feature: shows the health of the client's product feeds for Google Shopping.
`content` is the Merchant API's only OAuth scope (there is no read-only one).
Calls: `core/merchant_center_service.py` (Merchant API v1); logic:
`agents/google_ads_agent.py`. The client types their own Merchant Center id;
the grant is stored as `merchant_center_pending` until that id is verified.

**Read**
| Function | Call | Data |
|---|---|---|
| `merchant_center_service.get_account` | `GET accounts/v1/accounts/{id}` | account name — verifies access before the connection is saved (`POST /api/merchant-center/link`), and feed status |
| `merchant_center_service.list_data_sources` | `GET datasources/v1/accounts/{id}/dataSources` | each data source's display name and fetch state (`google_ads_agent.get_feed_status`, `run_merchant_center_scan`) |

**Written**
| Function | Call | What changes |
|---|---|---|
| `merchant_center_service.register_gcp` | `POST accounts/v1/accounts/{id}/developerRegistration:registerGcp` | ONE-TIME, admin-only (`POST /api/merchant-center/register-gcp`): links uallak's Cloud project to **uallak's own** Merchant Center account, as Merchant API v1 requires. Never called on a client's account. |

Nothing in a client's Merchant Center account (products, feeds, settings) is
written.

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
| `youtube.upload` | `youtube_service.upload_video` (← `youtube_content_agent.publish`, admin-triggered `POST /api/youtube-content/publish`) | `videos.insert` (resumable) | **writes** one video with title, description, `privacyStatus=private`, `selfDeclaredMadeForKids=false` |

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

All writes run from `website_agent.configure_lead_conversion` (admin-triggered:
`POST /api/website/configure-conversion`), which refuses if a lead-event tag is
already live and, since 2026-10-17, if the stored grant lacks any of the three
scopes (`website_agent.gtm_missing_scopes`). The callback records the scopes
Google actually granted (`gtm_api_connected` activity, `scopes`); a grant with
no record predates `tagmanager.edit.containerversions` and the dashboard card
asks the client to reconnect.

Defined but not called today: `gtm_service.list_workspace_tags`,
`list_workspace_triggers`.

## Drive — `https://www.googleapis.com/auth/drive` (service account)

Not a user consent: `GOOGLE_SERVICE_ACCOUNT_JSON` authenticates uallak's own
service account (`drive_service._access_token`), which works only in Shared
Drive folders that were shared with it (`DRIVE_ARCHIVE_FOLDER_ID`,
`DRIVE_MEDIA_FOLDER_ID`). It never sees a client's personal Drive.

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
