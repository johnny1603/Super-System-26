# Chat parity — the dashboard and the manager chat

**Rule (2026-11-02):** everything a client can do in the dashboard, they can do by
asking their account manager in the chat. The client never has to find a screen.

**Where the rule is enforced**
- `core/chat_parity.py` (`PARITY`) maps every session-gated client route in
  `core/api_server.py` to one of:
  - a chat action card (`core/agent_actions.py`);
  - an `account_state` read (`core/client_state.py`);
  - a chat output field;
  - an explicit **not in chat** reason;
  - a prioritised **gap**.
- `tests/test_chat_parity.py` fails in these cases:
  - a new route, or a new `/api/` call in `dashboard/client/index.html`, has no entry;
  - an entry names an action or section that doesn't exist, or one the client chat can't propose;
  - a not-in-chat entry has no real reason;
  - this file misses a gap or an action.
- Add a dashboard button and its chat path (or its honest reason) in the same change.

**How a chat action works:** the agent proposes a card, the client clicks it, and
the action runs. The run calls **the same function the dashboard button calls**;
the chat never has a second implementation.

## What the chat says when it can't (the "team" fallback is gone)

**Team requests** (`open_team_request`) exist ONLY for five decisions: `legal`,
`tax`, `contract`, `billing` (refunds included), and `package_exception`.

**Anything else the chat can't do yet:**
- The agent says honestly, in one sentence, that it can't do this from the chat
  yet, then offers the closest thing it can do.
- The system logs a `capability_gap` alert, from source `capability_gap`, deduped
  per client and question for 7 days. The alert appears:
  - in the alerts table and the Control Room;
  - to איציק, via `recent_alerts(source="capability_gap")`.

  The client is never shown a "request". This alert is how the gaps below get
  found and prioritised.

**Older team requests** on a retired topic (`manual_task`, `complaint`,
`action_not_offered`, `other`) still display and can be closed by staff.

## Nothing that isn't there

- Before mentioning a page, draft, campaign, file or request as existing, the
  agent must find it in `account_state`. This is a prompt rule in
  `ACCOUNT_STATE_RULE`.
- A code guard, `support_agent._guard_invented`, backs the rule:
  - Trigger: the landing section was read, it holds no page, and the reply speaks
    of the client's landing page or draft as if it exists.
  - Result: the reply is replaced with the plain truth plus an offer to write one,
    and the admin side is alerted.
  - Origin: client 27, who was told about a draft that never existed.

## Landing pages — full parity

| ask in the chat | card | runs (same as the dashboard) |
|---|---|---|
| "make me a landing page" | `create_landing_page`, with a concrete title and goal built from the client's real data (at most one question) | `landing_page_agent.create_page`, the "+ דף נחיתה חדש" builder; it writes a **draft**, then a `publish_landing_page` preview card follows in the same thread |
| "publish it" | `publish_landing_page` | `landing_page_agent.publish_page`; this second click is the only way a page goes live |
| "change it: …" | `edit_landing_page` | `landing_page_agent.regenerate_copy` writes a new version; what is live stays live until the preview card is approved |
| "put my photos on the page" / "use the new pictures from my folder" | `edit_landing_page` (or `create_landing_page`) with `images`: the manager picks photos from `account_state.media_folder.images` (or the ones the client named) and proposes where each goes — hero, gallery, a section, background; the card shows the photos | `landing_images.import_selection`: each photo proven to be in THIS client's media folder, COPIED into `landing_page_images` (resized, WebP, metadata dropped, ≤ 350 KB each, ≤ 1.8 MB per page, alt text written by a model that looks at it), served from `/lp-media/…` — never a Drive link; a new version, so revert brings back exactly the earlier photos |
| "go back to version N" | `revert_landing_page` | `landing_page_agent.revert_page` |
| "take it down" | `unpublish_landing_page` | `landing_page_agent.unpublish_page` |
| "delete it" | `delete_landing_page` | `landing_pages.delete_page` |
| "use my domain lp.x.co.il" / "I added the record" | `connect_landing_domain` / `check_landing_domain` | `landing_page_agent.request_domain` / `verify_domain` (the same message text) |
| "which pages do I have, how are they doing?" | — | read from `account_state.landing_pages`: status, address, 30-day leads, views, calls and WhatsApp clicks, versions |
| a page beyond the package | team request `package_exception` | — |

## Gap list (what the chat can't do yet), by priority

Trivial gaps closed in this change:
- `change_manager`: the same `manager_identity.change` as the settings picker.
- `connect_website`: opens the website card; the address and approval stay on that card.
- `connect_analytics`: read-only Google approval screen.

| priority | dashboard route | what to build |
|---|---|---|
| P1 | `POST /api/client/leads/{lead_id}/status` | update_lead_status card (needs lead ids in account_state.leads) |
| P1 | `POST /api/client/website/rebuild` | build_site card (the same request_self_provision gate) |
| P1 | `POST /api/website/self-provision` | build_site card (the same request_self_provision gate) |
| P2 | `GET /api/client/billing` | a billing section in account_state (plan, next charge, last invoice) |
| P2 | `POST /api/client/disconnect` | disconnect_platform card (revokes one connection; destructive, so its own explicit card) |
| P2 | `POST /api/client/export/{dataset}/google-doc` | export_to_google_doc card (returns the Doc link in chat) |
| P2 | `POST /api/client/leads/{lead_id}/note` | add_lead_note card |
| P2 | `POST /api/client/outreach/mark` | mark_outreach_reply card (replied / asked to be removed) |
| P2 | `POST /api/client/profile` | update_business_profile card (business details the client corrects in words) |
| P2 | `POST /api/client/voice/delete` | delete_voice_recording card |
| P2 | `POST /api/client/website/assess` | assess_site card (the same assess_existing_site check) |
| P2 | `POST /api/client/website/migrate-request` | request_site_migration card (the same request_migration) |
| P3 | `GET /api/client/external-costs` | external tool costs in account_state |
| P3 | `GET /api/client/ownership` | an ownership section in account_state (what they own, where) |
| P3 | `GET /api/oauth/google-drive/start` | connect_google_drive link card |
| P3 | `POST /api/client/crm/disconnect` | disconnect_crm card |
| P3 | `POST /api/client/store/disconnect` | disconnect_store card |
| P3 | `POST /api/client/feedback` | store feedback the client gives in the chat (engagement_agent.store_feedback) |
| P3 | `POST /api/client/outreach/delete` | delete_outreach_record card |
| P3 | `POST /api/client/tools/provider` | choose_video_provider card |
| P3 | `POST /api/client/tools/{tool}/disconnect` | disconnect_creative_tool card |
| P3 | `POST /api/client/tools/{tool}/test` | test_creative_tool card |

## Every client-dashboard route

Generated from `core/chat_parity.PARITY`. "Answers from account_state" means the chat states it from the snapshot it reads every turn.

| capability | dashboard | chat action | gap |
|---|---|---|---|
| account | `GET /api/client/account` | — (allowlisted) | the dashboard page's own data load; the chat has the same facts in account_state |
| password | `POST /api/client/password` | — (allowlisted) | a password is typed only on the password screen, never in a chat |
| dashboard | `GET /api/dashboard` | reads `channels`, `google_campaigns`, `meta_campaigns`, `budget`, `leads`, `approvals` | — |
| performance | `GET /api/client/performance` | reads `google_campaigns`, `meta_campaigns`, `budget`, `leads`, `website_traffic` | — |
| profile | `POST /api/client/profile` | **none yet** | **P2: update_business_profile card (business details the client corrects in words)** |
| manager | `GET /api/client/manager` | — (allowlisted) | the manager's name and face ARE the chat (manager_identity.prompt_line) |
| manager | `POST /api/client/manager` | `change_manager` | — |
| connection help | `GET /api/client/connection-help` | `request_connection_help` | — |
| connection help | `GET /api/client/connection-help/{connection_id}` | `request_connection_help` | — |
| connection help assist | `POST /api/client/connection-help/{connection_id}/assist` | `request_connection_help` | — |
| connect chat | `POST /api/client/connect-chat` | — (allowlisted) | the chat's own plumbing (this IS the chat or its card), not a client capability |
| client chat | `POST /api/client-chat` | — (allowlisted) | the chat's own plumbing (this IS the chat or its card), not a client capability |
| client chat history | `GET /api/client-chat/history` | — (allowlisted) | the chat's own plumbing (this IS the chat or its card), not a client capability |
| client chat unread | `GET /api/client-chat/unread` | — (allowlisted) | the chat's own plumbing (this IS the chat or its card), not a client capability |
| client chat new | `POST /api/client-chat/new` | — (allowlisted) | the chat's own plumbing (this IS the chat or its card), not a client capability |
| chat upload | `POST /api/client/chat-upload` | — (allowlisted) | the chat's own plumbing (this IS the chat or its card), not a client capability |
| chat upload status | `POST /api/client/chat-upload/status` | — (allowlisted) | the chat's own plumbing (this IS the chat or its card), not a client capability |
| actions | `GET /api/client/actions` | — (allowlisted) | the chat's own plumbing (this IS the chat or its card), not a client capability |
| actions | `GET /api/client/actions/{proposal_id}` | — (allowlisted) | the chat's own plumbing (this IS the chat or its card), not a client capability |
| actions decide | `POST /api/client/actions/{proposal_id}/decide` | — (allowlisted) | the chat's own plumbing (this IS the chat or its card), not a client capability |
| upgrade options | `GET /api/client/upgrade-options` | `upgrade_request` | — |
| upgrade | `POST /api/client/upgrade` | — (allowlisted) | the purchase itself is confirmed on the billing screen with the payment provider; the chat builds the priced offer (upgrade_request) |
| billing | `GET /api/client/billing` | **none yet** | **P2: a billing section in account_state (plan, next charge, last invoice)** |
| external costs | `GET /api/client/external-costs` | **none yet** | **P3: external tool costs in account_state** |
| interview | `POST /api/client/interview` | — (allowlisted) | a first-login / onboarding screen event the dashboard sends by itself, not a client request |
| interview state | `GET /api/client/interview/state` | — (allowlisted) | a first-login / onboarding screen event the dashboard sends by itself, not a client request |
| connection checklist | `GET /api/client/connection-checklist` | reads `channels` | — |
| connection nudge | `POST /api/client/connection-nudge` | — (allowlisted) | a first-login / onboarding screen event the dashboard sends by itself, not a client request |
| login moment | `POST /api/client/login-moment` | — (allowlisted) | a first-login / onboarding screen event the dashboard sends by itself, not a client request |
| feature announcement | `POST /api/client/feature-announcement` | — (allowlisted) | a first-login / onboarding screen event the dashboard sends by itself, not a client request |
| journey | `GET /api/client/journey` | — (allowlisted) | a first-login / onboarding screen event the dashboard sends by itself, not a client request |
| tour completed | `POST /api/client/tour-completed` | — (allowlisted) | a first-login / onboarding screen event the dashboard sends by itself, not a client request |
| feedback | `POST /api/client/feedback` | **none yet** | **P3: store feedback the client gives in the chat (engagement_agent.store_feedback)** |
| disconnect | `POST /api/client/disconnect` | **none yet** | **P2: disconnect_platform card (revokes one connection; destructive, so its own explicit card)** |
| data export | `GET /api/client/data-export` | — (allowlisted) | a file download; the chat cannot hand over a file |
| close account | `POST /api/client/close-account` | — (allowlisted) | closing the account is irreversible and confirmed on its own screen and by e-mail; in the chat it is a billing matter for the team |
| transfer out | `POST /api/client/transfer-out` | — (allowlisted) | the transfer is confirmed by e-mail with the data attached; in the chat it is a contract matter for the team |
| oauth google ads start | `GET /api/oauth/google-ads/start` | `connect_google_ads` | — |
| google ads accounts | `GET /api/google-ads/accounts` | — (allowlisted) | the account picker Google returns to after the client's own consent; it only exists on that screen |
| google ads select account | `POST /api/google-ads/select-account` | — (allowlisted) | the account picker Google returns to after the client's own consent; it only exists on that screen |
| gtm connection | `GET /api/gtm/connection` | reads `tracking` | — |
| gtm containers | `GET /api/gtm/containers` | — (allowlisted) | the account picker Google returns to after the client's own consent; it only exists on that screen |
| gtm select container | `POST /api/gtm/select-container` | — (allowlisted) | the account picker Google returns to after the client's own consent; it only exists on that screen |
| oauth gtm start | `GET /api/oauth/gtm/start` | `connect_tag_manager` | — |
| oauth youtube start | `GET /api/oauth/youtube/start` | `connect_youtube` | — |
| oauth gmail start | `GET /api/oauth/gmail/start` | `connect_gmail_send` | — |
| oauth search console start | `GET /api/oauth/search-console/start` | `connect_search_console` | — |
| search console sites | `GET /api/client/search-console/sites` | — (allowlisted) | the account picker Google returns to after the client's own consent; it only exists on that screen |
| search console site | `POST /api/client/search-console/site` | — (allowlisted) | the account picker Google returns to after the client's own consent; it only exists on that screen |
| oauth analytics start | `GET /api/oauth/analytics/start` | `connect_analytics` | — |
| analytics properties | `GET /api/client/analytics/properties` | — (allowlisted) | the account picker Google returns to after the client's own consent; it only exists on that screen |
| analytics property | `POST /api/client/analytics/property` | — (allowlisted) | the account picker Google returns to after the client's own consent; it only exists on that screen |
| oauth google drive start | `GET /api/oauth/google-drive/start` | **none yet** | **P3: connect_google_drive link card** |
| google drive status | `GET /api/client/google-drive/status` | reads `connections` | — |
| oauth meta start | `GET /api/oauth/meta/start` | `connect_meta` | — |
| oauth tiktok start | `GET /api/oauth/tiktok/start` | `connect_tiktok` | — |
| website connect | `POST /api/website/connect` | `connect_website` | — |
| website authorize start | `POST /api/website/authorize/start` | `connect_website` | — |
| website assess | `POST /api/client/website/assess` | **none yet** | **P2: assess_site card (the same assess_existing_site check)** |
| website migrate request | `POST /api/client/website/migrate-request` | **none yet** | **P2: request_site_migration card (the same request_migration)** |
| website rebuild | `POST /api/client/website/rebuild` | **none yet** | **P1: build_site card (the same request_self_provision gate)** |
| website self provision | `POST /api/website/self-provision` | **none yet** | **P1: build_site card (the same request_self_provision gate)** |
| media connect | `POST /api/media/connect` | — (allowlisted) | the client's own key or password is pasted ONLY in its card, never in a chat (the chat offers that card) |
| avatar connect | `POST /api/avatar/connect` | — (allowlisted) | the client's own key or password is pasted ONLY in its card, never in a chat (the chat offers that card) |
| avatar consent | `POST /api/avatar/consent` | — (allowlisted) | a likeness/voice consent is a deliberate tick on its own card (the chat offers that card, never ticks it) |
| tools | `GET /api/client/tools` | reads `creative_tools` | — |
| tools provider | `POST /api/client/tools/provider` | **none yet** | **P3: choose_video_provider card** |
| tools connect | `POST /api/client/tools/{tool}/connect` | `connect_creative_tool` | — |
| tools test | `POST /api/client/tools/{tool}/test` | **none yet** | **P3: test_creative_tool card** |
| tools disconnect | `POST /api/client/tools/{tool}/disconnect` | **none yet** | **P3: disconnect_creative_tool card** |
| voice | `GET /api/client/voice` | reads `voice` | — |
| voice upload | `POST /api/client/voice/upload` | — (allowlisted) | a recording is uploaded as a file; the chat takes files through its own upload (chat-upload) |
| voice delete | `POST /api/client/voice/delete` | **none yet** | **P2: delete_voice_recording card** |
| avatar footage | `GET /api/client/avatar-footage` | — (allowlisted) | retired 2026-10-31 (filmed twin); kept only for old open tabs |
| avatar footage create | `POST /api/client/avatar-footage/create` | — (allowlisted) | retired 2026-10-31; the chat's create_presenter_from_photos replaced it |
| media folder | `GET /api/client/media-folder` | reads `media_folder` | — |
| media drive | `GET /api/client/media-drive` | reads `media_folder` | — |
| media drive file | `GET /api/client/media-drive/file/{file_id}` | — (allowlisted) | streams a file to the browser; not a capability |
| media drive thumb | `GET /api/client/media-drive/thumb/{file_id}` | — (allowlisted) | streams a thumbnail to the browser; not a capability |
| media assets | `GET /api/client/media-assets` | reads `media` | — |
| leads | `GET /api/client/leads` | reads `leads` | — |
| leads status | `POST /api/client/leads/{lead_id}/status` | **none yet** | **P1: update_lead_status card (needs lead ids in account_state.leads)** |
| leads note | `POST /api/client/leads/{lead_id}/note` | **none yet** | **P2: add_lead_note card** |
| lead capture | `GET /api/client/lead-capture` | — (allowlisted) | the form-snippet setup screen for their developer; the chat explains it from account_state.tracking |
| crm | `GET /api/client/crm` | reads `leads` | — |
| crm connect | `POST /api/client/crm/connect` | — (allowlisted) | the client's own key or password is pasted ONLY in its card, never in a chat (the chat offers that card) |
| crm disconnect | `POST /api/client/crm/disconnect` | **none yet** | **P3: disconnect_crm card** |
| store | `GET /api/client/store` | reads `store` | — |
| store connect | `POST /api/client/store/connect` | — (allowlisted) | the client's own key or password is pasted ONLY in its card, never in a chat (the chat offers that card) |
| store connect site | `POST /api/client/store/connect-site` | `connect_store` | — |
| store disconnect | `POST /api/client/store/disconnect` | **none yet** | **P3: disconnect_store card** |
| landing pages | `GET /api/client/landing-pages` | reads `landing_pages` | — |
| landing pages | `POST /api/client/landing-pages` | `create_landing_page` | — |
| landing pages | `POST /api/client/landing-pages/{page_id}` | `edit_landing_page` | — |
| landing pages publish | `POST /api/client/landing-pages/{page_id}/publish` | `publish_landing_page` | — |
| landing pages versions | `GET /api/client/landing-pages/{page_id}/versions` | reads `landing_pages` | — |
| landing pages revert | `POST /api/client/landing-pages/{page_id}/revert` | `revert_landing_page` | — |
| landing pages unpublish | `POST /api/client/landing-pages/{page_id}/unpublish` | `unpublish_landing_page` | — |
| landing pages preview | `GET /api/client/landing-pages/{page_id}/preview` | — (allowlisted) | the preview page itself; the chat's cards link to it |
| landing pages regenerate | `POST /api/client/landing-pages/{page_id}/regenerate` | `edit_landing_page` | — |
| landing pages | `DELETE /api/client/landing-pages/{page_id}` | `delete_landing_page` | — |
| landing pages request extra | `POST /api/client/landing-pages/request-extra` | `handoff_topic=package_exception` | — |
| landing domain | `POST /api/client/landing-domain` | `connect_landing_domain` | — |
| landing domain verify | `POST /api/client/landing-domain/verify` | `check_landing_domain` | — |
| support | `GET /api/client/support` | — (allowlisted) | the support page's static content; the chat IS the support |
| export | `GET /api/client/export/{dataset}` | — (allowlisted) | a file download; the chat cannot hand over a file |
| export google doc | `POST /api/client/export/{dataset}/google-doc` | **none yet** | **P2: export_to_google_doc card (returns the Doc link in chat)** |
| ownership | `GET /api/client/ownership` | **none yet** | **P3: an ownership section in account_state (what they own, where)** |
| ownership handover | `GET /api/client/ownership/handover` | — (allowlisted) | a document download; the chat cannot hand over a file |
| export all | `GET /api/client/export-all` | — (allowlisted) | a file download; the chat cannot hand over a file |
| outreach | `GET /api/client/outreach` | reads `outreach` | — |
| outreach mark | `POST /api/client/outreach/mark` | **none yet** | **P2: mark_outreach_reply card (replied / asked to be removed)** |
| outreach delete | `POST /api/client/outreach/delete` | **none yet** | **P3: delete_outreach_record card** |
| outreach export | `POST /api/client/outreach/export` | — (allowlisted) | a file download; the chat cannot hand over a file |
| suggestions | `GET /api/client/suggestions` | reads `approvals` | — |
| suggestions decide | `POST /api/client/suggestions/{suggestion_id}/decide` | `approve_suggestion` | — |

## Every chat action (`core/agent_actions.ACTIONS`)

| action | approved by | kind | limits | what it does |
|---|---|---|---|---|
| `configure_conversion` | client click | execute | — | Adds ONE form-submission trigger and ONE GA4 generate_lead event tag to the client's Tag Manager container, creates a version and PUBLISHES it, so leads from their site are counted. |
| `demo_google_ads_campaign` | staff (Control Room) | execute | internal clients only | Google verification demo: creates ONE test search campaign in the connected Google Ads account, created PAUSED with a 5 ILS/day budget (spends nothing unless enabled). |
| `google_ads_test_campaign` | client click | execute | internal clients only | Creates ONE test search campaign in the client's connected Google Ads account, created PAUSED with a 5 ILS/day budget, so it spends nothing unless they enable it in Google Ads. |
| `demo_youtube_upload` | staff (Control Room) | execute | internal clients only | Google verification demo: uploads the smallest video (within the upload size limit) from the client's Drive media folder to their YouTube channel as PRIVATE. |
| `rotate_wordpress_password` | staff (Control Room) | execute | — | Replaces the WordPress Application Password we store for the client's site: mints a new one on the site, checks it works, stores it encrypted, then deletes the old one there. |
| `update_site_items` | client click | execute | — | Updates the client's site plugins/themes listed in account_state.site_updates.updates, one at a time: a full site backup before each, a check that the site and key pages load after each, and an automatic restore from that backup if anything breaks. |
| `update_site_items_without_backup` | client click | execute | opened by code only | The remaining site updates after a backup could not be taken, run WITHOUT a backup on the client's explicit approval. |
| `publish_landing_page` | client click | execute | — | Puts ONE of the client's landing pages live (account_state.landing_pages), at the version shown in the card's preview. |
| `create_landing_page` | client click | execute | — | Writes a NEW landing-page DRAFT for the client (never live): the click runs our page writer, and a second card with a preview then asks whether to publish it. |
| `edit_landing_page` | client click | execute | — | Changes ONE of the client's landing pages (account_state.landing_pages) — its text according to the change they asked for in their own words, and/or the client's OWN photos from their media folder placed as hero, gallery, section images or background. |
| `revert_landing_page` | client click | execute | — | Brings back an EARLIER version of one landing page (account_state.landing_pages[].versions). |
| `unpublish_landing_page` | client click | execute | — | Takes ONE live landing page down (instant; visitors stop seeing it). |
| `delete_landing_page` | client click | execute | — | Deletes ONE landing page for good (it frees a place in the package). |
| `connect_landing_domain` | client click | execute | — | Starts moving the client's landing pages to their OWN subdomain (e.g. |
| `check_landing_domain` | client click | execute | — | Checks whether the client's landing-page domain record is live now (after they say it was added). |
| `connect_tag_manager` | client click | link | — | Opens Google's approval screen to (re)connect the client's Tag Manager — the fix when tracking is not connected, expired, lost access or lacks permissions. |
| `connect_youtube` | client click | link | — | Opens Google's approval screen to (re)connect the client's YouTube channel. |
| `connect_meta` | client click | link | — | Opens Meta's approval screen to (re)connect the client's Facebook/Instagram. |
| `connect_google_ads` | client click | focus | — | Opens the Google Ads connection card (it carries the staff-access consent the client must tick) so the client can (re)connect. |
| `connect_tiktok` | client click | link | — | Opens TikTok's approval screen to (re)connect the client's TikTok account — the fix when TikTok is not connected or the connection needs renewing (also offered automatically when a TikTok send can't go through). |
| `connect_creative_tool` | client click | focus | — | Opens the connection card of ONE creative tool (elevenlabs | higgsfield) so the client pastes that tool's key there — the fix when a feature needs a tool that account_state.creative_tools shows as not connected or key_rejected. |
| `request_connection_help` | client click | execute | — | Asks the team to contact the client and connect ONE connection together with them — ONLY when the client says they are stuck, tried and failed, or asks for a person to help them connect it. |
| `create_presenter_from_photos` | client click | execute | internal clients only | Makes the client's presenter character (the face in their presenter videos) from ONE image in their media folder (account_state.media_folder.images): a clear photo of them, or an AI image we generated for them that they liked. |
| `team_request` | staff (Control Room) | execute | opened by code only | A tracked request for the team: legal, tax, contract, billing or a package exception only (a licensed-professional or commercial decision). |
| `clone_voice` | client click | execute | internal clients only | Clones the client's voice from the recordings in account_state.voice, on THEIR own voice tool account. |
| `make_presenter_video` | client click | execute | internal clients only | Makes ONE short presenter clip — AT MOST 15 SECONDS of speech (about 35 words) — in which the client's presenter character says a script, in their own voice. |
| `approve_suggestion` | client click | execute | — | Approves ONE of the client's own pending suggestions (approvals.waiting_for_client) — the same as their approve tap in the dashboard; approving is what lets the team start. |
| `publish_youtube_from_media` | client click | execute | — | Uploads ONE video from the client's Drive media folder (account_state.media_folder.videos, uploadable=true) to their YouTube channel as PRIVATE — never public or unlisted; they publish it themselves in YouTube Studio when they like. |
| `send_tiktok_to_inbox` | client click | execute | — | Sends ONE video from the client's Drive media folder (account_state.media_folder.videos, tiktok_uploadable=true) to THEIR OWN TikTok inbox as a draft. |
| `connect_gmail_send` | client click | link | — | Opens Google's approval screen so the client's own Gmail can SEND the outreach e-mails they approve one by one (send only — the mailbox is never read). |
| `send_outreach_email` | client click | execute | — | Sends ONE outreach e-mail from the client's own Gmail to ONE business. |
| `send_outreach_followup` | client click | execute | — | Sends the ONE follow-up e-mail to a business that got the first e-mail 7+ days ago and has not answered (only businesses listed in account_state.outreach.followup_due). |
| `connect_search_console` | client click | link | — | Opens Google's approval screen for READ-ONLY access to the client's Google Search Console, so the organic section shows their real keyword positions, clicks and impressions. |
| `connect_analytics` | client click | link | — | Opens Google's approval screen for READ-ONLY access to the client's Google Analytics, so their site traffic shows in the dashboard and the chat. |
| `connect_website` | client click | focus | — | Opens the website connection card (one-click WordPress approval, or 'build me a site') so the client can connect their site. |
| `connect_store` | client click | focus | — | Opens the online-store connection card: the chat then walks the client through creating a READ-ONLY store key, pasted in the card. |
| `set_conversion_types` | client click | execute | — | Saves what counts as a conversion for this client — any of form_submit (a form on the site), whatsapp_click, phone_click, store_sales — exactly as the client described how customers reach them. |
| `change_manager` | client click | execute | — | Switches who the client's account manager is (the name and face in this chat, emails and messages). |
