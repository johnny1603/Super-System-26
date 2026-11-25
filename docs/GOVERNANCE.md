# Client governance — how the doctrine is wired (2026-11-26)

`knowledge/client-governance.md` is policy. `core/governance.py` is where the code holds it.
Storage: one `app_settings` row per client, `governance:<client_id>` (no migration; cleared with the
client; covered by the deletion guard).

## 1. The doctrine in every agent

- `governance.with_rules(system)` appends the wiring rule + the file IN FULL to the manager's and every
  specialist's system prompt (`support_agent._with_governance`), inside the cached prefix. A light turn
  (thanks / hi) reads no account data and sets nothing in motion, so it stays light.
- The content path (`post_brief.load_context`) carries it as `client_governance` in all four media_agent
  payloads. The file missing = `ContextMissing`, the same loud refusal as the craft library.

## 2. Authority (§1 of the doctrine)

`governance.authority(client_id)` → `decide` only when the brand brief's status is `approved`; anything
else (draft, missing, unreadable) → `ask`. It reaches every agent as `governance.authority` in the data.
This is a prompt rule plus data — code does not block a model that ignores it.

## 3. Lanes (§2)

- Every Social-queue item (`social_queue.add`, `add_post`, `add_design`) stores `lane` = `system` |
  `client` AT CREATION: what the creator declared, else the lane of the work in progress
  (`governance.lane_scope`), else by origin (a plan item = system, anything else = asked for).
- Creators: the weekly plan (`content_team.produce`) = system; `produce_requested_content` and a client's
  pose request = client; a post from the chat = the lane the agent set on the post (`lane`), default client;
  a post's assets and slides inherit the post's lane; a fix inherits the lane of what it fixes.
- Costs: `cost_tracker.record_cost` stamps `details.lane` from the same scope.
- Items and cost rows from before 2026-11-26 have no lane. They are reported as "unrecorded" — never
  guessed into a lane.
- `governance.lanes_report` returns the lanes separately and has no total. The client's main page, the
  weekly sync and the Control Room show them on separate lines.
- Targets: `quarter_goals._posts_week` subtracts client-lane pieces, so the system's output target is
  measured on the system lane only.

## 4. Override log (§3)

The chat model, on the turn it complies, sets `override_log` {topic, recommendation, decision}.
`support_agent._note_override` → `governance.note_override` stores it (date, agent) and appends ONE fixed
line the client reads ("recorded: our recommendation was …"). Not stored = no line, an alert.
`governance.settled` goes back to every agent: topics already decided are never argued again.
Shown in the weekly sync, on the client's main page and in the Control Room.

## 5. Weekly sync (§4)

- The client picks Thursday / Friday / Saturday evening on the main page (default Thursday).
- Job `governance-weekly-sync` (09:05 and 18:05 daily, `GET /api/governance/weekly-sync`): on the
  client's evening it builds the week from what exists — planned items with their reasons, the two
  lanes, credits by lane, the week's overrides, targets hit / miss, campaigns — stores it as `waiting`
  and posts it in the manager's chat. No model call.
- The client approves on the main page. Approving publishes nothing; every item keeps its own gate.
- Still `waiting` after 24 hours → ONE alert + ONE email to the owner. Nothing is approved, published or
  frozen by silence. The owner can approve on the client's behalf in the Control Room (audited; the
  client's page says it was approved for them).
- Exempt from the escalation: `TRAINING_ACCOUNTS` (27, Shin Sekai) and any client whose setting
  `weekly_sync_escalation` is off (default off for internal accounts).

## 6. Credits (§6)

`governance.credits`: this month's generation credits by lane (from `client_costs.details.credits`),
what is left and how long it lasts at the last 14 days' rate. The package size is the manager's setting
`package_credits`; unset = the page says the size is not set and shows no balance (never zero).
There is no ceiling and no gate: nothing in this module refuses anything.

## Not built / open

- No package size existed anywhere in the code before this; `package_credits` must be set per client.
- The "met" rule for targets stays the terms' 90% (`quarter_goals.MET_RATIO`); the doctrine's 15% band
  is the same number as the internal buffer but a different rule. Not changed here.
- The sync's campaign lines are numbers only; judging a campaign as "failing" and apologising is the
  agent's job in the chat, not this code's.
- Approving the week from the chat (typing "yes") is a listed parity gap, not built.

Tests: `tests/test_governance.py`.
