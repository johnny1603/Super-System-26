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


## Follow-up (2026-11-17): the card order, verified

The harness `tests/js/chat_card_order.js` runs the dashboard's real window functions on a small DOM. Run against
the previous code (e42b4c5), it showed three causes:

1. **A message sent while the history loaded landed ABOVE all of it.** `loadWindowHistory` cleared the window and
   then waited for the fetch. Anything appended meanwhile (the client's message, the typing dots) came first, and
   the history was appended after it.
2. **A waiting card was never kept last.** It was placed at the bottom only when it was drawn. Every later message
   went under it: the client's own message, a reply without a card, a background post.
3. **Two loads at once** (for example, "new chat" during a load) drew the history twice.

The assumed cause, "a reopened window's history pushes the card up", was **not** what happened on a clean
reopen: the cards were drawn after the history. The card moved up because of (2), and the history order broke
because of (1).

The fix is `keepCardsLast`: every waiting card (`.chat-action.awaiting`) is moved to the bottom after every
message, after a history load and after an older page loads, and only the typing dots go below it. The history
also changed:

- it is inserted right under the intro;
- the cards are part of `historyLoaded`;
- only the newest load draws (`loadSeq`).

A decided card is no longer kept at the bottom.

## TikTok endings (what the card says)

Every way a TikTok send can end shows the reason and the next step on the card itself
(`ACTIONS["send_tiktok_to_inbox"]["refusals"]`, in 5 languages). The bar is removed and the card is marked failed.
Every ending after the upload sends one admin alert.

| Code | When | The card says (short) |
|---|---|---|
| `invalid_params` | at approval, the file is gone or changed | no longer in the media folder, pick it again |
| `error` | the send could not start | nothing sent, write "send it again" in a minute |
| `needs_reconnect` | no account, expired or revoked connection, missing permission, `auth_*` fail reason | renew the connection, with the reconnect card attached |
| `pending_limit` | too many unpublished drafts in TikTok | publish or delete some, then send again |
| `bad_format` | the file can't be decoded (container, codec, no video), before sending | save as a regular MP4 and send again |
| `too_big` | TikTok refuses the size | save a shorter or compressed version, tell me |
| `file_unreadable` | Drive read failed, or the file was moved or deleted | check it is still there, tell me again |
| `no_answer` | TikTok timed out after every retry, or the job ran past its time | stopped, nothing posted, "send it again" later |
| `send_failed` (and the older `in_hand`) | API errors after every retry and restart | couldn't get it through, team alerted, "send it again" in a few minutes |
| `rejected_format` | after the upload: format, resolution or frame rate | save as a regular vertical MP4 at 30 fps, send again |
| `rejected_length` | after the upload: duration | trim it and send again |
| `rejected_account` | after the upload: spam or limit, account banned | check the TikTok app, try tomorrow |
| `rejected` | after the upload: any other reason | team alerted, try another video meanwhile |
| `processing_slow` | TikTok still processing after 30 min | may still arrive, "send it again" if it isn't there in an hour |
| `timeout` | the reaper ended the card | `timed_out`: didn't finish in time, "send it again" for a new card |

While the send runs:

- the bar shows the real chunk progress;
- "TikTok didn't answer, retrying" while a retry waits;
- "fully uploaded, TikTok is processing" with no number;
- the plain stalled line past twice the usual time.

## Stop, at any stage (2026-11-19)

Every running card in the client chat has a **Stop** button (`stop-run`, `decision: "stop"` on
`/api/client/actions/{id}/decide`). A typed stop ("עצור", "stop", "wait"…, or the model's
`card_decision` verdict `stop`) does exactly the same thing, at any stage, not only in the first
`STOP_WINDOW_SECONDS`. The team can stop a client's run from the Control Room (the same `decide`,
actor admin).

`agent_actions.stop_action` decides what a stop can still cut. Every slow action is in exactly ONE set,
and `tests/test_stop_anywhere.py` fails otherwise.

| Mode | Actions | What happens | The one line |
|---|---|---|---|
| before start | any slow action inside the window | cancelled, nothing ran | "Stopped ✓ Nothing ran and nothing changed." |
| `STOP_DISCARD` | drafts, reports, research, upgrade offers, branding mockups, a post's production (2026-11-25: checks between steps, submits nothing new, keeps what was already paid as a waiting draft — docs/POST-BRIEF.md §10) | Row → cancelled (`stopped_midway`) at once. The run's thread may keep working, but anything it then posts or proposes is dropped (`_RUNNING_PROPOSAL` + `_run_stopped`), and its result is never shown. | Nothing new is shown, sent or put live. Anything already saved stays a draft only the client sees. |
| `STOP_JOB` | TikTok upload | Stops BETWEEN chunks (`tiktok_uploads.stop_for_proposal`). The chunk loop re-reads the job and the card before each chunk. | Nothing reached the TikTok drafts; the file stays in the media folder. |
| `STOP_FINISH` (and any unknown action) | GTM publish/revert, YouTube upload, site updates, voice clone, presenter video/character | `outcome: stop_requested`. The step finishes, and the card says so at once. The result is prefixed with "You asked to stop, but this step couldn't be cut halfway, so it finished:". The undo card is still offered where one exists. | "This step can't be stopped halfway: … will finish, and nothing more runs after it." |

A fully uploaded TikTok video (TikTok processing) is treated as finish. It lands in the drafts,
where the client can delete it.

TikTok uploads with no card of their own (a batch, an owner-approved file) are stopped by a typed
stop (`stop_client_jobs`).

**Stuck:** a run that makes no progress ends itself.
- **Every action:** the existing hard deadline + reaper. The card shows `stuck: true` and the badge
  "Stuck: stopped automatically".
- **TikTok:** `NO_PROGRESS_S` (20 min without a new chunk) gives the ending `stuck`, and
  `MAX_TOTAL_ATTEMPTS` (8) caps failed steps over the job's whole life, restarts included.

**Itzik (operator chat):** this was already the rule. The Stop button / "עצור" stops between steps,
and a started tool finishes. `stop_line` says what ran and what did not.
