# Client onboarding — the compact hub (2026-11-15)

## The flow, as reviewed (nothing in it was changed)

1. Sales chat → proposal → checkout creates the `clients` row → first login.
2. `GET /api/dashboard` → once per page load `runConnectionFlow`:
   `GET /api/client/connection-checklist` (`core/client_journey.connection_status`) gives the
   REQUIRED steps from the purchased package, in order, with `done` / `ready` per step,
   `next_step`, `done_count` / `total_count`, `complete`, `not_ready`, `not_in_plan`.
3. Steps missing → on a first login the connection nudge (`POST /api/client/connection-nudge`)
   opens the chat naming the real next step. Every card has ONE "חבר" button → the chat asks
   "I have an account / I don't yet" (`docs/CONNECTIONS_ONBOARDING.md`).
4. All required steps connected → `note_connections_complete()` stamps the 90-day clock and
   the deep interview starts in the chat.
5. The journey panel (`GET /api/client/journey`) shows milestones derived from existing rows.

**What is required, its order and what counts as done are decided on the server exactly as
before.** One decision per card is unchanged: the cards and their buttons are the same nodes
with the same handlers.

## What was wrong with the screen

- The connections area was one long grid of up to 17 cards (more on a phone, one column):
  required, optional, already connected and not-in-package cards all at the same weight. The
  old guide dimmed everything except the next card, but the client still scrolled past all
  of it.
- Progress was a thin bar and a "2 of 5" badge.
- The journey track was a horizontally scrolling row of nine milestones.
- Nothing marked the moment a step was completed.
- (Bug) The guide's title and text stayed in the old language after a language switch,
  because the connection flow runs once per page load.

## The new design

**The hub** (the same `#connGuide` element, restyled):

- a **progress ring** with `done/total` of the required steps;
- **one chip per required step**, in the server's order — done ✓ (green), the next one lit
  (accent), later ones grey. A chip takes the client to its card;
- **one next-best-action**: "השלב הבא: …", its text, and the one button "קחו אותי לשלב הבא";
- the "connected but can't run yet" notice, as before.

**The grid, grouped** (same cards, never moved — each gets `data-ob`, CSS `order` groups them):

| Group | What is in it | Shown |
|---|---|---|
| השלב הבא | the card(s) that complete the next required step, with the accent frame | always |
| אחר כך | required steps that come later | always |
| מחוברים (n) ✓ | completed required steps + anything else already connected | **folded**, one tap |
| עוד חיבורים (n) | optional and not-in-package cards | **folded**, one tap |

- A folded card is never a dead end: everything that sends the client to a card
  (`connReveal`: the hub button, the chips, help buttons, chat cards' "focus" target, the
  Performance page's "connect", the tool cards) opens its group first.
- The grouping follows live changes: the grid is watched for a status change or a card being
  shown / hidden and regrouped on the next frame; when the number of connected cards changes
  after the page has loaded, the checklist is fetched again so the ring, chips and next step
  follow without a reload.
- **Package unreadable / nothing required** → no hub and NO grouping: the plain full grid,
  exactly as before. The server's fail-closed rule is not second-guessed.

**The journey**: one line by default — "הושלם לאחרונה: X · הבא בתור: Y" — with "כל אבני הדרך"
opening the full track.

**A step completes**: the ring pulses, one line says "שלב הושלם — יפה! ✓" (or "הכל מחובר! 🎉"),
and the same one-per-key confetti burst as a finished task rises from the ring
(`docs/AGENT-LIFE.md`). The count is remembered per client in the browser; the first visit
only records it, so nothing is celebrated that the client did not just do. Off under reduced
motion and when the client switched motion off; never for staff viewing as the client.

All texts in he / en / fr / ar / ru; RTL-safe (logical properties, the ring number is LTR);
on a phone the ring shrinks and the grid is one column.

Guard: `tests/test_onboarding_ui.py`.

## Not done / limits

- `/onboarding/` (the business questionnaire page) was read and left as is.
- The celebration memory is per browser.
- Not tested in a real browser — see docs/SYSTEM-REVIEW.md checkpoint 5.
