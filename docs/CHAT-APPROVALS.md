# Approval cards in the client chat (2026-11-17)

Code: `core/chat_approval.py`, plus hooks in `core/agent_actions.py`, `agents/support_agent.py`,
`core/api_server._client_chat_answer` and `dashboard/client/index.html`.
Tests: `tests/test_chat_approvals.py`.

## The incident

Client 27 uploaded test3.mp4. The agent said "I've put a card right below", but the card was higher up the
thread, or did not exist. Then "ok upload it", "i approve" and "תעלה" did nothing, and the client went around
in a loop.

## Root causes

All three were verified by reading the code.

1. **Card placement.** `appendActionCard` returned early when the thread already showed that card's id.
   `propose()` returns the existing open card when the same action is offered again. That card stayed
   where it was first drawn, above the new reply that said "below".
2. **Typed approvals were ignored.** Only `POST /api/client/actions/{id}/decide` (the button) called
   `decide()`. The model's schema had no decision field, and its prompt said to "press the approve
   button on the card below".
3. **A card claimed but never made.** A reply could talk about a card even when nothing was proposed.

## The rules now

- **A typed yes is the button.**
  - The model gets `pending_cards` and `running_cards` in its payload, including on light turns.
  - It returns `card_decision: {verdict: approve|cancel|stop|undo|none, card_id}`.
  - `chat_approval.from_result` calls the same `decide()` as the button, so every check is redone and the
    claim is compare-and-set. A double yes runs the action once.
  - This applies to paid and irreversible actions too, by the owner's decision.
- **Fast path, used only for the obvious cases.**
  - `FAST_APPROVE` covers explicit "approve" verbs in 5 languages, and only when exactly one card is open.
  - `FAST_STOP` covers "stop", but only while a run is open.
  - Everything else ("יאללה", "סבבה", "ok upload it") is the model's reading.
- **Which card.**
  - With several open cards and no clear one, the agent asks one question listing them.
  - With one card, or a clear `card_id`, it runs.
  - Cards in the current window come first.
- **Before a heavy or paid action**, `heads_up` gives one line: what happens, the cost (`COST`), and whether
  it can be undone (`UNDO`, `UNDO_BY_HAND`, `IRREVERSIBLE`). Slow runs add the stop hint. The card's
  `undo_note` says the same thing before the client approves.
- **Stop.**
  - A slow run is scheduled as `_execute_after_window`. It waits `STOP_WINDOW_SECONDS` (8) and re-reads the
    row once a second before it starts.
  - `stop_run` compare-and-sets the row to `cancelled` with outcome `stopped`, so nothing runs.
  - After the window, the run continues to the end and the client reads that it continues (`TOO_LATE`).
  - This is the operator agent's pattern: a stop signal on shared state that the worker re-checks. Here the
    row is the signal.
  - "Stop" with nothing running cancels the open card.
- **Undo.**
  - When `configure_conversion` or `publish_landing_page` finishes, it offers one undo card (`UNDO`). A
    run that finishes in the background hands it over through `status_card`'s `next_card`.
  - A typed "undo" offers that card. When there is no automatic undo, it says how to reverse it by hand.
- **Progress bar.** `card.progress` gains:
  - `fraction`: real progress, from the TikTok chunked upload;
  - `expected_seconds`: `task_eta.action_seconds`, only with at least 5 past runs;
  - `stop_until`.

  The dashboard shows the real percentage first. Otherwise it shows elapsed time against the expected
  time, capped at 95%. Otherwise it shows a "working" bar with no number. A stalled run shows a frozen
  amber bar, and the bar is removed when the run finishes or fails.
- **Never a card that isn't there.** `claim_guard` runs after every chat reply. If the reply speaks of a card
  and none is attached, it re-surfaces the open card. If there is no open card, it drops the claim and says
  plainly that no card was made. `_route_proposal`'s own refusal line is flagged `no_card`.
- **No loops.** `loop_guard` checks for a near-identical reply about a waiting card. On the third time, it
  replaces the reply with the exact blocker: the block's reason, or "it is waiting only for your OK".
- **Placement in the dashboard.**
  - `appendActionCard` removes an existing copy and builds the card again at the bottom.
  - `sendInWindow` waits for the window's history before it appends the reply, so the reply never lands
    above it.
  - `next_card` (for example an undo card, or a follow-up card) is appended under the reply.

## Unchanged

- The agent proposes and the client approves. There is one decision per card, and the click route and
  every safety check are the same.
- No new key, env var, settings key or table, so no migration.
