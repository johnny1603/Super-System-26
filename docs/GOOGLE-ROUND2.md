# Google verification — ROUND 2 (Gmail send, Search Console read, Analytics read, client-owned Drive)

Status (2026-10-31): **built, OFF, never run against Google.** Round 1 (sign-in, Google Ads,
YouTube, Tag Manager — `docs/GOOGLE-SCOPES.md`) is under review and its scope lists are
unchanged; `tests/test_google_round2.py` (`Round1Frozen`) and
`tests/test_google_scope_flows.py` fail if any of them moves. Round 2 has not been submitted;
all four scopes below go into the SAME review round.

**Later round, not this one** (unless decided otherwise): Merchant Center, and the Google
Drive / Google Photos pickers in the chat "+" (`docs/CHAT-UPLOADS.md`). Neither is built.

Round 2 adds four separate consents, each behind its own flag:

| Flag (env var) | Default | Scope requested when ON | Consent route |
|---|---|---|---|
| `GMAIL_SEND_ENABLED` | off | `openid`, `https://www.googleapis.com/auth/userinfo.email`, `https://www.googleapis.com/auth/gmail.send` | `/api/oauth/gmail/start` |
| `SEARCH_CONSOLE_ENABLED` | off | `https://www.googleapis.com/auth/webmasters.readonly` | `/api/oauth/search-console/start` |
| `ANALYTICS_ENABLED` | off | `https://www.googleapis.com/auth/analytics.readonly` | `/api/oauth/analytics/start` |
| `DRIVE_FILE_ENABLED` | off | `https://www.googleapis.com/auth/drive.file` | `/api/oauth/google-drive/start` |

Analytics off: no consent URL, the Analytics routes 404, the card is hidden, the Performance
page has no `website_traffic` channel and no traffic block, client_state has no
`website_traffic` section, and the morning briefing has no visitors fact. Drive off: no
consent URL, the Drive routes 404 (including the admin migration route), the card is hidden,
and `core/drive_storage.location` answers "our Shared Drive" for every client without reading
anything — every file goes exactly where it goes today. The Shared Drive usage scan
(`POST /api/admin/drive/usage-scan`) works whatever the flag says: it is about OUR bill.

While a flag is off: the consent URL builder raises, both OAuth routes and the Search Console
endpoints answer 404 before reading the session, the dashboard card stays hidden
(`features` in the dashboard payload is false), no connect card is offered, the rank provider
reports "not connected", and `gmail_outreach.send_approved` refuses before reading anything.
One exception, on purpose (2026-10-30): a client whose package includes the Gmail agent may
still get a `send_outreach_email` DRAFT card in chat — it shows the exact email and appears on
the "פניות לעסקים" page — but its Approve button is disabled ("sending is not switched on
yet") and `agent_actions.decide` refuses the approve BEFORE claiming the card
(`approve_blocked` → status `blocked`, code `send_disabled`), so the draft stays open and no
Google call can happen. The same block answers `needs_reconnect` while the flag is on but
Gmail is not connected. Nothing about this touches a scope.
The scope therefore never appears on any consent screen until the flag is set. Neither flow
uses `include_granted_scopes`, so a round-2 grant never widens a round-1 token or vice versa.

## Justifications

### `https://www.googleapis.com/auth/gmail.send`

uallak runs marketing for small Israeli businesses. One service is partner outreach: the
client's agent finds a specific local business that would plausibly benefit from working with
the client (a bridal shop for a bridal-shoe maker), reads that business's own public website,
and drafts ONE personal email from the client to the address the business itself publishes
there. The client reads the exact email in a card in their dashboard chat and presses
Approve; only then does uallak call `users.messages.send` with the client's Gmail. The email
must come from the client's own mailbox — it is the client's business introducing itself, and
replies must reach the client directly. The app never reads, lists, searches, labels, modifies
or deletes mail: the Gmail module contains exactly one Gmail API call (`messages.send`), and
the test suite fails if a read path is added. Limits enforced in code: at most 10 sends per
client per day (counted from the send record, fails closed) and a 20/day brake across all
clients, one message per recipient business ever, a
do-not-contact list checked first (fails closed), an unsubscribe line in every email, a
cost-budget guard per client, and a stored record of every send (recipient, subject, body,
Gmail message id, time).

### `openid` + `https://www.googleapis.com/auth/userinfo.email` (in the Gmail consent)

Used once, at the callback, to learn which address granted `gmail.send`, so the email's From
header is the real, verified address (stored as the connection's account and in every send
record).
Without it we would have to ask the client to type an address and could not prove it matches
the grant. These are the same non-sensitive scopes round 1 already uses for sign-in.

### `https://www.googleapis.com/auth/webmasters.readonly`

The client's Performance page has an organic (SEO) section listing the search phrases the
client asked us to rank for. Search Console is the only source of the client's real Google
search data for their own site: average position, clicks and impressions per query. After
consent we list the client's verified properties, pick the one matching their website
(the client can change it), and once per 6 hours run one `searchAnalytics.query` (last 28
days of final data, dimension = query). Only the client's target phrases are shown. Nothing is
written to Search Console — no sitemaps, no URL inspection submissions, no property changes.

### `https://www.googleapis.com/auth/analytics.readonly`

The client's Performance page shows whether their website is working for them. Until now it
could only say whether a conversion tag is live; it could not say how many people visit the
site. Google Analytics 4 is where the client's own site already records that. After consent
we list the GA4 properties the client's own login can read (Admin API
`accountSummaries.list`); the client picks the one that is their site (stored like their Tag
Manager container choice). Then, at most once an hour per client, we run five
`properties.runReport` reads: users and sessions for today, yesterday, the last 7 / 30 / 90
days and the previous period of each, sessions per default channel group (organic, paid,
direct, social, referral) for the last 30 days, and the first day the property recorded any
session — so "no data yet" is never shown as zero visitors. The client sees these numbers on
the Performance page; their agents and the morning greeting can say "X visitors yesterday, up
vs the same day last week" — the comparison is computed in code, never by a model, and stated
only when both periods have data. The numbers are not stored: they live in a one-hour memory
cache. Nothing is written to Analytics: no property, stream, audience, key event or setting.

### `https://www.googleapis.com/auth/drive.file`

Today every client file — generated media, chat uploads, voice recordings, avatar source
material, exports — lives in uallak's own Shared Drive, reached by uallak's service account.
That bills uallak's storage for the client's own content and contradicts what we promise
clients: that what we make for them is theirs. With `drive.file` the client connects their own
Google Drive once; we create ONE clearly named folder, "uallak — {business}", in their My
Drive with the same subfolders as today (images, videos, voice, avatar-source, scripts,
exports, content-docs, website-media, documents), and from then on every file we create for
them is created there, owned by them. **`drive.file` lets the app manage only the files the
app itself creates; it can never list, open or change anything else in the client's Drive** —
we say exactly that on the card and the consent explanation. The flip side, also said to the
client: a file they drop into the folder by hand is invisible to us (they upload through the
chat instead, which creates the file through the app). Existing files are moved by an admin
action that copies each file, verifies size + MD5 checksum (native Google files: same type) of
every copy, only then points the client's storage at their Drive, and only on a typed
confirmation — after one more full verification — moves our copy to the trash. Disconnecting
revokes our grant; the folder and every file stay in the client's Drive.

## Evidence matrix

| Scope | Where requested (code) | API methods called | Where the client sees it | Approval before any write | Test |
|---|---|---|---|---|---|
| `gmail.send` | `core/gmail_service.py` `GMAIL_SCOPES` / `build_consent_url` | `users.messages.send` only | Dashboard → Gmail card (connect); chat card `send_outreach_email` showing the exact email; activity log | Yes — client presses Approve on each email (`agent_actions.decide`) | `tests/test_google_round2.py` `ConsentScopes`, `Outreach`, `OutreachCard` |
| `webmasters.readonly` | `core/search_console_service.py` `SEARCH_CONSOLE_SCOPES` / `build_consent_url` | `sites.list`, `searchAnalytics.query` | Dashboard → Search Console card (connect + site picker); Performance → organic table (position, clicks, impressions) | Read only — nothing is written | `tests/test_google_round2.py` `ConsentScopes`, `SearchConsoleRanks` |
| `analytics.readonly` | `core/analytics_service.py` `ANALYTICS_SCOPES` / `build_consent_url` | Admin API `accountSummaries.list`; Data API `properties.runReport` | Dashboard → Google Analytics card (connect + property picker); Performance → "מבקרים באתר" card + "תנועה לאתר" table (7/30/90 days vs previous, today, yesterday, sources); agents via client_state; morning greeting | Read only — nothing is written | `tests/test_round2_analytics_drive.py` `AnalyticsFlagOff`, `TrafficComparison`, `AnalyticsConsent` |
| `drive.file` | `core/drive_file_service.py` `DRIVE_FILE_SCOPES` / `build_consent_url` | `files.create` (folders, uploads incl. resumable), `files.list` / `files.get` (metadata and content) of app-created files, `permissions.create` only to give ONE file a link when a vendor must fetch it (Meta publishing, the avatar video provider), `files.update` (trash) for a file the client deletes | Dashboard → "הקבצים שלך ב-Google Drive שלך" card; the uallak folder in the client's Drive; Media tab; ownership page ("תיקיית uallak ב-Drive שלך") | Each write is the client's own action (upload, generate, delete in the dashboard) or an approved chat card; the migration is admin-run and never deletes before verification | `tests/test_round2_analytics_drive.py` `DriveFlagOff`, `StorageSeam`, `Migration`, `DriveConsent` |

`openid` / `userinfo.email` in the Gmail consent: OpenID `userinfo` once at the callback
(`gmail_service.sender_address`); becomes the From address of every email.

## Why no narrower scope works

- **`gmail.send`** is already Gmail's narrowest scope that can send: it cannot read, list or
  modify any mail. `gmail.compose` adds draft read/write, `gmail.modify` / `gmail.readonly` /
  `mail.google.com` add reading — all broader. Sending through our own domain instead would
  make the email not come from the client, and replies would not reach their inbox.
- **`webmasters.readonly`** is Search Console's only read-only scope; the alternative
  (`webmasters`) adds write access we do not need. The Search Analytics data has no other API
  (the public Google search results do not expose clicks or impressions for a site).
- **`analytics.readonly`** is the narrowest scope that can read Analytics reports. The
  Analytics APIs have no per-report, per-metric or per-property scope; the other Analytics
  scopes (`analytics`, `analytics.edit`, `analytics.manage.users`, …) all add write or admin
  access we do not need. The visitor numbers exist nowhere else: Search Console counts only
  Google search clicks, not visits from ads, social, referrals or direct.
- **`drive.file`** is already Drive's narrowest scope that can write: per file, only what the
  app created (or the user explicitly opened with it). Full `drive` — which our own service
  account uses inside OUR Shared Drive and which would read the client's entire Drive — is
  deliberately **not** requested from clients; neither are `drive.readonly` or
  `drive.metadata.readonly`, which would expose the client's other files. `drive.appdata`
  is a hidden app folder the client cannot open, which defeats the point (their files, in
  their Drive, visible to them).

## Demo video

Record only with both flags on in the recording environment, the migration applied, and an
internal test client. Every step below really executes — nothing is mocked.

1. Show the OAuth client id on the consent screen config page (same project as round 1).
2. Log in to the client dashboard → Connections. Show the Gmail card. Click Connect.
3. Google consent screen: show it lists **only** "Send email on your behalf" (+ email
   address). Approve. Back in the dashboard the Gmail card shows connected.
4. In the client chat ask the agent to reach out to a named local business. Show the card:
   recipient address, business name, subject, the full body naming the business and a detail
   from its site, the unsubscribe line at the bottom.
5. Open the business's site in another tab and show the same address published there.
6. Press Approve. Show the "sent" line in chat, then the email arriving in the recipient test
   mailbox (a second account we own) and the same email in the sender's Sent folder.
7. Ask for the same business again → the agent refuses (already contacted). Optional: show the
   daily-cap refusal line.
8. Search Console card → Connect. Consent screen lists only "View Search Console data for your
   verified sites". Approve. Show the site picker with the client's verified property.
9. Performance → organic section: target phrases with average position, clicks, impressions.
   Open Search Console in another tab and show the same query numbers for the same 28 days.
10. Disconnect both cards → show Google's third-party access page no longer lists the app.

Analytics (flag `ANALYTICS_ENABLED` on, a test client whose site has a GA4 property with real
traffic):

11. Google Analytics card → Connect. Consent screen lists only "See and download your Google
    Analytics data". Approve. If the login reaches several properties, show the picker and
    choose the site's property.
12. Performance → "מבקרים באתר" card and the "תנועה לאתר" table: visitors today, last 7 / 30
    / 90 days next to the previous period with ▲/▼, and the traffic-source split. Open GA4 in
    another tab → Reports → the same date ranges, and show the same user counts.
13. Show a property with no history (or a range before it existed): the cell shows "—", not 0,
    and no arrow.
14. Next morning, open the dashboard: the greeting says "N visitors yesterday" and up/down vs
    the same day last week — the same numbers as the table.

Drive (flag `DRIVE_FILE_ENABLED` on, a test client):

15. "הקבצים שלך ב-Google Drive שלך" card: read the text aloud — only files the app creates,
    never anything else in your Drive. Connect. Consent screen lists only "See, edit, create
    and delete only the specific Google Drive files you use with this app". Approve.
16. Open the client's Google Drive in another tab: one new folder "uallak — {business}" with
    the subfolders. Show that the client's other files are untouched.
17. Upload a video through the chat "+". Show it arriving in the client's own folder, owned by
    the client (Drive → Details → Owner).
18. Admin drawer → Drive migration for a client with older files: Copy → Verify (every file
    checked: size + MD5) → Flip. Show the copied files in the client's Drive. Free asks for the
    typed phrase and re-verifies before moving our copy to the trash.
19. Disconnect the card → Google's third-party access page no longer lists the app; the folder
    and every file are still in the client's Drive.

## Switching it on

1. Apply `migrations/2026-10-30-gmail-outreach.sql` in the Supabase SQL editor. Until then
   outreach fails closed (suppression/contacted unreadable → refuse; send record unreadable →
   daily cap reached).
2. Add the redirect URIs to the OAuth client: `https://app.uallak.com/api/oauth/gmail/callback`,
   `https://app.uallak.com/api/oauth/search-console/callback`,
   `https://app.uallak.com/api/oauth/analytics/callback` and
   `https://app.uallak.com/api/oauth/google-drive/callback`.
3. Enable the **Google Analytics Admin API** and **Google Analytics Data API** in the OAuth
   client's Cloud project (the Drive API is already enabled for the service account).
4. Add the scopes to the consent screen config and submit round 2 — only after round 1 is
   approved.
5. Set `GMAIL_SEND_ENABLED=true` / `SEARCH_CONSOLE_ENABLED=true` / `ANALYTICS_ENABLED=true` /
   `DRIVE_FILE_ENABLED=true` on Cloud Run (env vars, read at call time; no code change).
6. Storage alert while we still pay: `DRIVE_SHARED_ALERT_GB` (default 10) is the per-client
   threshold for our Shared Drive; `POST /api/admin/drive/usage-scan` (admin) walks every
   client's folder there and raises one admin alert per client per 7 days above it. **Not
   scheduled yet** — add a daily Cloud Scheduler job that calls it.

### What drive.file changes, technically

- `core/drive_storage.py` is the ONE seam: `location(client_id)` decides client Drive vs our
  Shared Drive, `scope(client_id)` / `@scoped` make every Drive call for that client run with
  the right credentials. Every reader and writer (media_agent, drive_mirror, drive_download,
  chat_uploads, voice_recordings, avatar_agent, avatar_video, content_docs_agent, exports,
  YouTube/TikTok source reads, agent_actions, verification_demo) goes through it;
  `tests/test_round2_analytics_drive.py` fails if a module reads `DRIVE_MEDIA_FOLDER_ID`
  itself or a known Drive caller loses its scope.
- `core/drive_migration.py`: copy (resumable across runs, ≤ 240 s per request) → verify
  (size + md5Checksum per file; Google-native files by type) → flip (only after a fresh
  verify) → free (typed `FREE <client id>`, another fresh verify, then OUR folder to the
  trash). Admin route: `POST /api/admin/clients/{id}/drive-migration` with `step`.
- Known limits: a chat upload already in progress when the pointer flips must be restarted;
  Google-native files are copied by export + re-import (formatting may differ slightly); a
  Workspace domain that forbids "anyone with the link" sharing blocks the per-file public link
  Meta publishing needs.
