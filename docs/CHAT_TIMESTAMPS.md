# Chat timestamps (2026-11-07)

Every message in the dashboard chat (client, manager, the agents, system
lines) and every action card shows when it happened, in the client's own
local time.

## Storage: always UTC
- Messages: `client_communications.created_at` (timestamptz, default now()).
  It already existed; every chat read orders by it.
- Cards: `agent_actions.created_at`, `decided_at`, `decided_by` (since
  `migrations/2026-10-21-agent-actions.sql`). `card()` now also returns
  `decided_at` and `decided_by` as the chat may say it: `client`, `team`
  (admin in the Control Room, or staff from the admin view) or `system`.
- Zone: `clients.timezone` (IANA name, default `Asia/Jerusalem`),
  `migrations/2026-11-07-chat-timezone.sql`. `/api/dashboard` sends it as
  `client.timezone` after a shape check (`_client_timezone`; "" when the
  value is missing or doesn't look like an IANA name).

## Display: ONE module, `dashboard/assets/chat-time.js`
- Zone order: the client's setting → the browser's zone → Asia/Jerusalem.
  IANA names only: a fixed offset such as `+03:00` is refused, because it
  would be wrong for half the year. Daylight saving follows each zone's own
  rules through `Intl.DateTimeFormat`.
- Every message: `HH:MM` (24h) under the text. The full `dd/mm/yyyy
  HH:MM:SS` shows on hover (title), on long-press on touch screens, or on
  a tap.
- Day separators: "היום", "אתמול", then `dd/mm/yyyy` (i18n in all five
  languages). They are recomputed from the messages each time, so prepending
  older threads or a live reply never leaves a wrong one. "Yesterday" is
  calendar arithmetic, not now minus 24 h, so it holds across a DST night.
- Live messages are stamped with the browser's clock the moment they
  appear. After a reload they show the stored server time.
- Cards: "נוצר dd/mm/yyyy HH:MM · הוחלט dd/mm/yyyy HH:MM על ידך / על ידי
  הצוות", with the full times on hover.

## The existing download ("הורדת השיחה")
This is not a new export. It is the same TXT, the same session-gated history
read and the same button. Each message line now reads `[dd/mm/yyyy HH:MM]`
in the client's zone, and the header names the zone once. There is no UTC
column because the file is not a CSV. The full data export (`export-all`)
keeps its formats and stores UTC as before.

## Before the migration runs
The chat uses the browser's zone. Nothing else changes.

## Tests
`tests/test_chat_timestamps.py`:
- runs the real module under Node: Israel's DST start and end in 2026,
  New York's rules, separators and "yesterday" across midnight in different
  zones, Supabase timestamp formats, zone choice and fixed-offset refusal;
- runs the shipped `downloadWindowConversation` with browser stand-ins;
- checks the card fields and the zone check on the server.

Node is on the CI runner. Without it, those tests are skipped and the reason
is stated.
