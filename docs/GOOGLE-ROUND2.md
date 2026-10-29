# Google verification — ROUND 2 (Gmail send, Search Console read)

Status (2026-10-30): **built, OFF, never run against Google.** Round 1 (sign-in, Google Ads,
YouTube, Tag Manager — `docs/GOOGLE-SCOPES.md`) is under review and its scope lists are
unchanged; `tests/test_google_round2.py` (`Round1Frozen`) and
`tests/test_google_scope_flows.py` fail if any of them moves.

Round 2 adds two separate consents, each behind its own flag:

| Flag (env var) | Default | Scope requested when ON | Consent route |
|---|---|---|---|
| `GMAIL_SEND_ENABLED` | off | `openid`, `https://www.googleapis.com/auth/userinfo.email`, `https://www.googleapis.com/auth/gmail.send` | `/api/oauth/gmail/start` |
| `SEARCH_CONSOLE_ENABLED` | off | `https://www.googleapis.com/auth/webmasters.readonly` | `/api/oauth/search-console/start` |

While a flag is off: the consent URL builder raises, both OAuth routes and the Search Console
endpoints answer 404 before reading the session, the dashboard card stays hidden
(`features` in the dashboard payload is false), no chat card is offered, the rank provider
reports "not connected", and `gmail_outreach.send_approved` refuses before reading anything.
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

## Evidence matrix

| Scope | Where requested (code) | API methods called | Where the client sees it | Approval before any write | Test |
|---|---|---|---|---|---|
| `gmail.send` | `core/gmail_service.py` `GMAIL_SCOPES` / `build_consent_url` | `users.messages.send` only | Dashboard → Gmail card (connect); chat card `send_outreach_email` showing the exact email; activity log | Yes — client presses Approve on each email (`agent_actions.decide`) | `tests/test_google_round2.py` `ConsentScopes`, `Outreach`, `OutreachCard` |
| `webmasters.readonly` | `core/search_console_service.py` `SEARCH_CONSOLE_SCOPES` / `build_consent_url` | `sites.list`, `searchAnalytics.query` | Dashboard → Search Console card (connect + site picker); Performance → organic table (position, clicks, impressions) | Read only — nothing is written | `tests/test_google_round2.py` `ConsentScopes`, `SearchConsoleRanks` |

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

## Switching it on

1. Apply `migrations/2026-10-30-gmail-outreach.sql` in the Supabase SQL editor. Until then
   outreach fails closed (suppression/contacted unreadable → refuse; send record unreadable →
   daily cap reached).
2. Add the two redirect URIs to the OAuth client: `https://app.uallak.com/api/oauth/gmail/callback`
   and `https://app.uallak.com/api/oauth/search-console/callback`.
3. Add the scopes to the consent screen config and submit round 2 — only after round 1 is
   approved.
4. Set `GMAIL_SEND_ENABLED=true` / `SEARCH_CONSOLE_ENABLED=true` on Cloud Run (env vars, read
   at call time; no code change).
