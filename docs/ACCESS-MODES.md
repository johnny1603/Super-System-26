# Access modes — how uallak gets into a client's accounts (Phase 1 design)

Status 2026-10-30: **design document.** Mode A (the client connects what they already
have) is what the code does today. Mode B (uallak opens everything with ownership from day
one) is mostly NOT built; each gap is marked **[not built]**. Nothing here changes a scope,
a consent screen or the privacy policy. If a later phase builds part of Mode B, it must
update `docs/GOOGLE-SCOPES.md` / `docs/TIKTOK-REVIEW.md` and the privacy pages in the same
commit.

## The two modes

**Mode A — the client already has accounts and lets uallak in.** Two ways in, and they are
not the same thing:

1. **OAuth connection by the client** (the dashboard's Connect cards). The client signs in
   to the platform's own consent screen; we store the resulting token encrypted
   (`core/secrets_vault`) on a `client_accounts` row. **This is the only kind of access
   the system's agents can use.** Every automated action (campaigns, uploads, tags,
   reports) runs on that token.
2. **Invite of `johnny_support@uallak.com` as a user** inside the client's account (Google
   Ads user, Business Manager partner/person, GTM user, Search Console user, WordPress
   user). This gives **a human** (Johnny) access through the platform's own web UI. The
   system cannot act on an invite by itself — it never logs in as a person. An invite
   becomes usable by the agents only when Johnny then completes the same OAuth Connect
   **for that client** while signed in as `johnny_support@uallak.com` (the token is then
   Johnny's identity, scoped to what the invite allows). Today that is possible for Google
   Ads, GTM, YouTube (if the channel grants a manager role) and Meta, but there is **no
   admin "connect on behalf of" button [not built]** — it would be done from the admin
   view, which is read-only on purpose, so a staff-connect flow needs its own design
   (consent recorded from the client first, see "Open questions").

**Mode B — uallak opens everything from scratch, with ownership/access from day one.**
uallak creates the account (or the client creates it in a guided session), the CLIENT is
the owner/admin of record, and uallak gets a standing role (manager link, partner, admin
user) from the first day. Never the reverse: uallak must not own a client's ad account,
Page or domain — on closure/transfer (`_offboard_client`) the client must leave with
everything, and `client_deletion` explicitly does not touch external resources.

## Per platform

Legend — **System can**: what the agents do through the stored grant. **Client provides**:
what we need from them. **Missing access →**: what the dashboard shows today.

### Google Ads

| | Mode A (client's account) | Mode B (opened by uallak) |
|---|---|---|
| How | OAuth `adwords` from the Google Ads card (staff-access consent `GOOGLE_ADS_STAFF_CONSENT` first); the client picks which ad account (`/api/google-ads/accounts` → `select-account`). Invite: client adds `johnny_support@uallak.com` as a user, or links uallak's manager (MCC) account. | Create the client's account under uallak's MCC (`CustomerService.CreateCustomerClient`) **[not built]**, client added as admin user, billing on the client's card. The code already sends `login-customer-id` when `GOOGLE_ADS_LOGIN_CUSTOMER_ID` is set (`google_ads_service`). |
| System can | Read campaigns + daily metrics (Performance page), create campaigns (`google_ads_agent`), the internal PAUSED test campaign card, lead-conversion reporting. | Same, plus account creation and the manager link from day one. |
| Client provides | A Google login with access to the ad account; billing set up in Google Ads; the staff-access consent tick. | Their Google login (to be made admin), their payment method in Google Ads. |
| Missing access → | Connect card "לא מחובר" + Connect; Performance channel `not_connected`; a dead grant → `needs_reconnect` everywhere and the `connect_google_ads` chat card (`core/google_ads_health`). | Same. |

### Google Tag Manager (lead tracking on the site)

| | Mode A | Mode B |
|---|---|---|
| How | OAuth (3 tagmanager scopes) from the GTM card; the client picks the container (`/api/gtm/containers`). Invite: client adds `johnny_support@uallak.com` as a container user (Publish). | uallak creates the account + container in the client's Google login during a guided session, or creates it on a site we build and adds the client as admin **[not built — container creation API not used]**. |
| System can | Read the container, check the site loads it (`gtm_connection`), configure + publish the lead conversion tag (`configure_conversion`, client-approved). | Same. |
| Client provides | Admin on the container; the snippet already on the site (or a site we manage). | A Google login. |
| Missing access → | "לא מחובר"; container mismatch / no GTM on site / "צריך לחבר מחדש" (live `containers.get`); `configure_conversion` refuses with its plain reason. | Same. |

### YouTube

| | Mode A | Mode B |
|---|---|---|
| How | OAuth `youtube.upload` + `youtube.readonly` from the YouTube card. Invite: channel permissions (Manager) for `johnny_support@uallak.com`. | Client creates the channel on their own Google account (YouTube channels cannot be created by API); uallak gets Manager in channel permissions. |
| System can | Upload a video from the client's own Drive media folder, always Private, max 3/day, client-approved card. | Same. |
| Client provides | The channel's Google login. | A Google account; 5 minutes to create the channel. |
| Missing access → | "לא מחובר" + Connect; no publish card is offered. | Same. |

### Meta (Facebook Page, Instagram, ad account)

| | Mode A | Mode B |
|---|---|---|
| How | One OAuth (`ads_management`, `ads_read`, pages_*, instagram_*) storing up to three rows (ad account, Page, Instagram). Invite: client adds uallak's Business Manager as a **partner** on the ad account + Page. | The client creates the Business Manager + Page + ad account (Meta requires a real person as owner), uallak added as partner from day one. Creating ad accounts under uallak's BM is possible on Meta's side but would make uallak the owner — **not allowed** by the rule above. |
| System can | Create/draft campaigns, read insights, publish posts, read + reply to comments (`meta_ads_agent`, `meta_content_agent`). | Same. |
| Client provides | Facebook login that admins the Page and ad account; a linked Instagram business account. | Their Facebook login and payment method. |
| Missing access → | "לא מחובר" + Connect; expired token → reconnect (MetaGraphError code); Performance `not_connected` / `needs_attention`. | Same. |

### TikTok

| | Mode A | Mode B |
|---|---|---|
| How | OAuth `user.info.basic` + `video.upload` (Upload-to-Inbox). There is no invite model for a personal/creator account. | Client creates the account on their phone; uallak can only connect it (Mode A). TikTok Ads (Business Center) is **not built** and not offered. |
| System can | Send a video (≤ 64 MB) to the client's TikTok drafts; the client publishes it in the app. Never Direct Post, never paid ads. Engagement reads off (`ENGAGEMENT_READS_ENABLED`). | Same. |
| Client provides | The TikTok login on their phone. | A TikTok account. |
| Missing access → | "לא מחובר"; a grant without `video.upload` or dead → `needs_reconnect` + `connect_tiktok` card; Performance note `engagement_counts_not_available_yet`. | Same. |

### Website (WordPress)

| | Mode A | Mode B |
|---|---|---|
| How | One-click WordPress authorization (Application Password, rotated immediately) or the pasted Application Password fallback. Invite: client creates a WordPress user for `johnny_support@uallak.com` (Administrator). A non-WordPress site → rebuild offer (`assess_existing_site`). | uallak builds the site on InstaWP (`provision_site` / `request_self_provision`) — hosted on uallak's InstaWP account, billed to the client as a monthly line. The DOMAIN stays the client's (client-paid); mapping is a manual InstaWP step today. |
| System can | Publish/update pages and posts, alt text, SEO plugin, lead form auto-wired, tracking install, PHP exposure check (anonymous HEAD). | All of that from day one; the client gets a WordPress admin user on the site. |
| Client provides | WordPress admin login (to approve the one-click screen). | A domain (or they launch on the `*.instawp.xyz` subdomain). |
| Missing access → | Website card "לא מחובר" + one-click Connect; `website_provision_status` requested / failed; `site_security` from the stored PHP check. | Same. |

**Ownership caveat (Mode B site):** the InstaWP hosting account is uallak's. On closure the
client gets the WordPress export / migration help, not the hosting account — this must be
stated in the proposal text before Mode B is sold as "you own everything" **[decision
needed]**.

### Google Drive (media folder)

Neither mode needs the client's Google login: uallak's service account owns the media
root and shares the client's folder with the client's email (`media_agent`,
`drive_mirror`). Missing → the Media tab says why (`folder_status`: not_configured / error).

### Search Console and Gmail (Google round 2 — built, OFF)

Mode A only: the client connects with `webmasters.readonly` / `gmail.send` behind
`SEARCH_CONSOLE_ENABLED` / `GMAIL_SEND_ENABLED` (`docs/GOOGLE-ROUND2.md`). Search Console
invite: the client can add `johnny_support@uallak.com` as a user on the property (Restricted
is enough to read). Gmail has no invite model and must never have one — mail goes out only
from the client's own grant, each email approved by the client. While the flags are off the
cards are hidden.

### CRM (HubSpot, Pipedrive) and automation (ManyChat, Make)

- CRM: Mode A only — the client pastes their own API key (`crm_service.VENDORS`). Missing →
  the CRM card shows not connected; leads stay in `client_leads` regardless.
- ManyChat / Make: deliberately manual — Johnny stores the client's login in the admin
  drawer after a WhatsApp conversation (`automation_service`). Invite (team member) is the
  better Mode A variant where the vendor supports it.

## What the client sees when access is missing (today)

- Connection cards: "לא מחובר" + Connect; reconnect states for Google Ads, GTM, TikTok.
- `client_journey` connection checklist: the next required connection from the PURCHASED
  package, one at a time; it fails closed when the package can't be read.
- Performance page: `channel_statuses()` per channel — not_in_plan / not_connected /
  connected_no_data / active / needs_attention. Never a fake zero.
- Chat: `interview_agent.connection_nudge` at login names the real next step; the
  concierge offers the matching `connect_*` card; agents answer from `client_state`
  (`ACCOUNT_STATE_RULE`), never "I can't see it".
- **[not built]** an "invited, waiting for us to accept/connect" state. Today an invite is
  invisible to the system until someone completes OAuth.

## Admin view ("enter as client") — built 2026-10-30

Staff can open any client's dashboard from the admin client list ("כניסה כלקוח") to see
exactly what the client sees. `core/admin_view.py` holds the rules:

- Admin session only; a separate signed cookie bound to that admin session, 2 hours max.
  No client session is created; the client's password, login codes and session version
  are never read or written.
- Read-only by allowlist (`READ_ENDPOINTS`): chat sends, uploads, connects, OAuth starts,
  exports, settings and account closure answer 403 `ERR_ADMIN_VIEW_READ_ONLY`. Two reads
  that would write on the client's behalf are neutralised: the Drive media-folder link
  (creates + shares the folder on first ask) is not fetched, and Drive downloads are logged
  as `admin`. The connection checklist may still stamp the 90-day start — the same
  idempotent fact the client's own next load would stamp.
- Approving/cancelling a card: only with the explicit staff mark (`staff_action`) and only
  for actions staff may already approve in the Control Room
  (`agent_actions.staff_may_decide`); recorded as `decided_by = 'staff'`. Client-consent
  actions (Gmail send, YouTube/TikTok publish, …) show "only the client can approve".
- Audit: start, stop, every staff decision (requested + result) → `alerts` source
  `admin_view` (status resolved, protected like deletion audit entries). "Who" is the admin
  session fingerprint + IP, because the admin login is one shared password. The start and
  each staff decision are refused if the audit row cannot be written.

## Client logout

The client dashboard had **no** logout control — only `/profile` had one. It now has a
"התנתקות" button in the top bar (all five languages), next to the profile avatar, calling
the existing `POST /api/logout` and returning to `/login/`. Under 640 px the top bar wraps
instead of overflowing, so the button stays on screen on phones. In the admin view it is
hidden (the banner's "יציאה" ends the view instead). Verified by code reading and a JS parse
only — check on a real phone after deploy (see the release note).

## Open questions for Phase 2

1. Staff-connect on behalf of a client (Mode A invite → OAuth by `johnny_support@`):
   needs a recorded client consent and must not reuse the read-only admin view.
2. Mode B Google Ads: create under uallak's MCC — needs the MCC's developer token level
   and a client-admin invite step.
3. Mode B website ownership wording (InstaWP hosting is uallak's).
4. An "invite pending" state per platform on the dashboard.
