# Client chat: timing, one turn at a time, frustration alerts (2026-11-09)

Code: `core/chat_timing.py`, `core/chat_turns.py`, `core/frustration.py`,
`core/explicit_instruction.py`, `dashboard/assets/frustration-widget.js`.
Migration: `migrations/2026-11-09-chat-timing-frustration.sql` (idempotent).
Tests: `tests/test_chat_turns.py`.

## 1. Explicit instructions ("just do it, as is")

`explicit_instruction.is_explicit(message)` is a regex, with no model call, in 5 languages. When it
matches, the payload carries `explicit_instruction: true`. `support_agent.HANDOFF_RULE`
("EXPLICIT INSTRUCTIONS"), shared by the manager and every persona, then says:

- act now, with no content questions;
- resolve the file by name;
- if something truly blocks the action, give one sentence naming the blocker and one fix;
- never repeat a question the client already answered or brushed aside.

The media rules allow ONE question, and only to pick between several files when no name was
given. `youtube_metadata` never asks a question any more. An unclear subject gets general
metadata about the business. If the model still asks, it gets one re-draft; asking twice is a
`MetadataError`.

## 2. Per-message timing (measurement only)

Every `/api/client-chat` request is timed. One row in `chat_turn_timings` is written after the
reply (BackgroundTasks). The stages are:

| stage | what |
|---|---|
| queue | waiting for the conversation's lock + the collection window |
| guard | chat_guard, storing the inbound message, the code-only store answers |
| context | inside the agent, everything that is not a model call: DB reads, client_state, store/platform reads, prompt building |
| model | every Claude API call (router, main turn, web search, retries): model, tokens (input / cache read / output), ms |
| post | vendor scrub + storing the reply |

Model calls are timed in `claude_json._complete` and `claude_web_search_call` through a
ContextVar. A call outside a chat turn records nothing. The router's thread copies the
context, so its calls are counted.

The view is at `/admin/models/` under "זמני תגובה בצ'אט". It shows:

- the average and p90 overall;
- the average per stage, and how often each stage was the dominant one;
- per client: average, p90 and dominant stage;
- the slowest recent messages, with each model call.

Without the table, each turn is one `[chat_timing]` log line (ids and numbers only).

### Suspected causes of delay (from reading the code — confirm with the data first)

1. **The manager is locked to Opus 5.5** (`model_routing.ROLE_OVERRIDES["manager"]`). Every
   manager turn pays the strongest model's latency, even for "thanks".
2. **Context reads are serial** on a cache miss: Google/Meta/website performance, suggestions,
   store, then the `client_state.snapshot` sections, each a network round trip.
3. **A second model call for web search** on manager turns that ask for it. It runs on the
   same Opus model with up to N searches.
4. **The specialists' router** is a Haiku call bounded at 4 s, before the main call.
5. **The collection window** (new, 3 s by default) adds up to 3 s to every message. That is
   the price of merging. Set it to 0 to switch it off.
6. Several small DB writes and reads per message: inbound log, outbound log, the turn checks.

### Proposed fixes (none applied yet — decide from the measurement)

- No cost: run the independent context reads in parallel (a thread pool, as the router does).
- No cost: skip the web-search stage unless the model asked for it. This is already the case;
  check how often it fires.
- Saves money: route simple manager turns (greetings, thanks, yes/no) to a cheaper tier, as
  the specialists do. This changes the "manager is always Opus" rule, so it needs your
  approval.
- Saves money: shorter outputs (`CHAT_TURN_MAX_TOKENS`) where the data shows long replies.
- Costs money: Cloud Run min instances, which removes cold starts. **Not enabled. Ask first.**
  Only worth it if the data shows `other` / first-message turns that are slow because of cold
  starts.

### Typing indicator and streaming

- The dashboard shows ONE typing indicator per chat window, at once, kept while any send of
  that window is waiting.
- The reply is **not streamed**. The model's answer is structured JSON, followed by
  post-processing guards: vendor scrub, invented-item guards, action validation and cards.
  Streaming would show text that the guards may still change or remove.
- Streaming would mean splitting the reply out of the JSON contract. That is not a cheap
  change.

## 3. One conversation, one turn at a time (`core/chat_turns`)

- **Serialized** per client × channel: a threading lock, plus a cross-instance lease row in
  `chat_turn_locks`. Without the table it serializes per instance only.
- **Collection window:** the `chat_collect_window_seconds` setting, in app_settings, editable
  from the admin settings. The default is 3, capped at 8; 0 switches it off. The total wait
  is at most 10 s. Every message still unanswered after the window is answered as ONE
  combined message.
- **`covered`:** a request whose message was answered by another turn returns
  `{"covered": true}`. The window shows no second reply.
- **A message arriving mid-turn:**
  - if no card and no background job came out of the turn, the turn restarts ONCE with
    everything combined;
  - otherwise the reply is stored, and the new message gets its own turn next.

  There are never duplicate or interleaved replies.
- Messages are stored before any of this, and every failure degrades to "answer my own
  message".

## 4. Frustration (`core/frustration`)

- **Rules (free, every message).** They look for:
  - phrases: "how many times", "why not", "I already said";
  - slowness complaints, a request for a human, a threat to leave or cancel, profanity;
  - shouting or a run of "!!!" or "???";
  - the same request repeated, or failed twice by the agent;
  - a long wait (≥ 45 s).

  A message is flagged on one strong sign or two weak ones. A flag sets
  `client_frustrated: true`, and the prompt then gives one short line of empathy plus ONE
  concrete next step, in the chosen manager's or persona's own voice. It never mentions the
  team and never mentions an alert.
- **Confirmation (paid, only when flagged).** It runs after the reply, in the background:
  one Haiku call with cost category `claude_frustration`, recorded in `client_costs`. The
  messages are classified as data and never followed as instructions.
- **Incident:**
  - one `client_frustration_alerts` row per client × channel while it is open, and seen in
    the last 6 h;
  - later messages update it: message count, reasons, and medium can rise to high;
  - **one email** to ADMIN_EMAIL per incident, plus one more on escalation to high;
  - the **bell** on every admin page (`frustration-widget.js`) shows the open count and a
    list with client, severity, reasons, last messages and the suggested action, plus a
    "טופל" button.
- Without the table: one `alerts` row (source `frustration`) per incident window, plus the
  email.

Client deletion clears all three tables (`client_deletion.DATA_TABLES`).
