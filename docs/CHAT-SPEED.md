# Client chat speed (2026-11-11)

The owner reported 2-3 minutes of waiting for every small answer, with no difference
between easy and hard questions. This is the analysis and what changed.

## 1. Measurement: what could and could not be measured

- **`chat_turn_timings` was NOT queried.** The dev machine has no production credentials.
  The read-only query for the last 48 h is `scripts/chat_timing_report.sql`: paste it into the
  Supabase SQL editor. It gives avg / median / p90, the queue/context/model/post breakdown,
  model calls, models, the dominant stage and the % of short (<40 chars) messages over 60 s,
  per persona, per length and per route.
- Everything below is a **code-level estimate**, labelled as such, not a measurement.
- After the deploy, `/admin/models/` (period "48 שעות") shows the same groups live, including
  the new route/model per turn, so before vs after can be compared.

## 2. A tiny message ("תודה") end to end, before this change

| Step | Before (from the code) |
|---|---|
| Guard (chat_guard, store/connect regexes) | in memory + 1-2 DB reads, ms |
| Collect window + lock (`chat_turns`) | up to 3 s (queue) |
| Thread context + history | 1-2 DB reads |
| Manager context | lead, client_knowledge (~10 sources), recent activity, suggestions, then Google Ads (20 s timeout), Meta (30 s), website (30 s), store reads — **one after another** |
| `client_state.snapshot` | ~20 sections **one after another**, several of them live provider calls (GTM 30 s, WordPress 30 s, WooCommerce 20 s, Drive 60 s timeouts). Section cache 120 s, per instance: a message more than 2 min after the last one misses it entirely, and so does every new instance |
| Model | manager **locked to Opus 5.5** (adaptive thinking, up to 2000 tokens), for "thanks" too; specialists first ran a Haiku router call (up to 4 s) when the rules did not decide |
| Web search | a second Opus call, only when the model asked for it |
| Post | vendor scrub, logs, a synchronous cost row + route row per model call |

### Top causes

1. **Serial context reads**. About 25 reads in a row on every turn, regardless of the question,
   many of them live calls to Google / Meta / WordPress / GTM / Drive. Verified in code; the
   size is ASSUMED (no measurement). With a few providers at 5-20 s each, this alone reaches the
   reported minutes, and it explains "no difference between easy and hard questions": the reads
   do not depend on the question.
2. **The manager on the strongest model for everything.** Verified in code. Opus with thinking
   on a large payload is several seconds to tens of seconds per call (ASSUMED magnitude).
3. **Fixed overhead before the model**: the 3 s collect window and, on specialists, the router
   call of up to 4 s. Verified in code.

Also seen (not changed, see §4): synchronous cost/route DB writes per model call; cold starts
emptying the in-memory caches (ASSUMED, not measured).

## 3. What changed

- **Routing by request type** (`core/chat_route.py`, rules only, no model call):
  - `simple` (thanks, hi, ok with nothing pending) → `simple` tier (Haiku);
  - `read` (a short question, ≤140 chars, no action words) → `standard` (Sonnet);
  - `work` (do / change / publish / build / spend / connect / a "yes" / an "ok" to a question we
    asked / frustration / explicit instruction / multi-step / long / **anything unclear**) →
    `heavy` (Opus).
  - Doubt always goes UP. The manager and the specialists both use it (`model_routing.CHAT_ROLES`);
    the specialists no longer call the Haiku router. The interview and איציק stay as they were.
  - `MODEL_ROUTING_ENABLED` off → every chat turn is back on the heavy tier.
- **A simple turn reads no account context**: last 4 messages + a short note (1-2 lines, no
  account facts, no actions, no search, never "I can't see" or "the team"). Anything it proposes
  (card, upgrade, search, article, revision) is dropped in code: it can only answer.
- **Full turns read everything at the same time** (`core/parallel.gather`): lead, knowledge,
  activity, suggestions, platform reads, store reads and the account snapshot. Inside the
  snapshot the ~20 sections also run in parallel (tracking / budget / leads after channels).
  A full turn now waits for the slowest read, not the sum of them. `@single_flight` on the
  Google / Meta / website readers makes concurrent asks for the same client share ONE live call.
  No deadline was added: a slow provider is still waited for (see §4).
- **Safety checks unchanged**: guard, frustration rules, `_guard_invented`, vendor scrub, the
  failed-topic line, agent_actions validation — all still run before the client sees a reply.
- **Typing indicator**: after 15 s the existing "writing you a reply…" line becomes one calm line
  ("עובדים על זה ביסודיות — עוד רגע זה אצלך."). Text only, no spinner, no tool names, no DOM move.
- **Measurement**: each timing row carries `route`, `route_tier`, `route_model`, `route_reason`
  and `message_chars` in `stages.flags` (the existing jsonb: **no migration**). `/admin/models/`
  shows them by route, by persona and short vs long, plus the % of short messages over a minute.

## 4. Proposed, NOT done (waiting for the owner's decision)

| Proposal | Gain | Cost / risk |
|---|---|---|
| Per-read deadline (e.g. 8 s) on full turns, then answer with what arrived | caps a hanging provider | an answer may miss a section → the fixed "can't pull that up" line more often |
| Longer section cache (e.g. 10 min) or a shared cache (Supabase row) | fewer live reads between messages | slightly older numbers in answers; shared cache = DB writes |
| Cost / route logging in the background | removes 2 DB writes per model call | a crash can lose a cost row |
| Cloud Run min instances | removes cold starts | monthly cost; **not done, per the rule** |
| Streaming the reply | first words sooner | larger change to the chat UI + the JSON contract |

## 4a. Second pass (2026-11-15): prewarm + measured time estimates

`chat_turn_timings` was again NOT reachable from the dev machine, so the remaining slow spots
come from **code analysis**, labelled as such. Itzik can now read the real numbers
(`speed_report`), and `/admin/models/` shows them.

**What a full turn still waits for (code analysis):**

| Stage | Where the time goes | Changed now? |
|---|---|---|
| Queue | the 3 s collect window on EVERY message (`chat_collect_window_seconds`) + lease reads | No — it is what merges quick consecutive messages; shortening it is an owner setting already (`0` = off). |
| Context | the slowest of ~25 parallel reads; section cache 120–600 s per instance, so a message after a pause re-reads live | **Yes — prewarm** (below). |
| Model | one call on the routed tier; the biggest share on `work` turns (ASSUMED) | No — quality first; depth is untouched. |
| Post | scrub, logs, 2 synchronous DB writes per model call (~0.1–0.3 s, ASSUMED) | No — small gain, a lost cost row is the risk. |

**Prewarm (`core/chat_prewarm`).** The context reads do not depend on the message, so they
now start when the client FOCUSES a chat box or types the first character
(`POST /api/client-chat/prewarm`, at most once per 90 s per client, never two at once). The
same readers a turn uses (`client_state.snapshot`, `_manager_platform_reads`,
`client_knowledge`), no model call, no write. By the time the message is sent the caches are
warm and the turn goes almost straight to the model. Answers are exactly as deep and as
fresh — only WHEN the reads happen moved. Kill switch: admin setting `chat_prewarm_enabled`.
Not a Cloud Run setting, not a scheduler job, no min instances.

- *Expected effect (ESTIMATE, not measured):* on a turn after a pause of more than ~2
  minutes, the context stage drops from "the slowest live read" (seconds to tens of seconds)
  to roughly cache-read time, whenever the client typed for longer than the reads take. A
  turn sent within the cache TTL of the previous one is unchanged. The model stage is
  unchanged.
- *Honest limit:* caches are per instance. If the message lands on a different instance than
  the prewarm, the turn reads as before — never slower.

**Time estimates in plain words (`core/task_eta`).** Measured, never guessed:

- A running action card's progress text gains "It usually takes about 2 minutes." — the
  MEDIAN of that action's last finished runs (`agent_actions` decided→finished), shown only
  with at least 5 runs, in the client's language.
- The chat's long-wait line (after 15 s) becomes "…an answer like this usually takes about a
  minute." — the median of last week's LONG turns (`GET /api/client-chat/pace`). Without
  enough history the fixed calm line stays. Text only, one line.
- Itzik's progress line uses the same rule from his own history (docs/OPERATOR.md).

**Fixed technical line.** `chat_fallback._TECHNICAL_OPENING` no longer says "passed to the
team" (all five languages): "It's already being looked at — it's worth trying again in a few
minutes." The admin alert behind it is unchanged.

Tests: `tests/test_task_eta.py`.

## 4b. Small talk is not work (2026-11-15)

"מה קורה?", "מה הולך?", "היי", "מה נשמע", "תודה", "אחלה" and their equivalents in the five
languages are answered like a person answers them.

| Step | Small talk | Any other message |
|---|---|---|
| Deciding what it is | rules in `core/chat_route.small_talk` — no model call | same rules |
| Collection wait (`core/chat_turns`, 3 s by default) | **skipped** | unchanged |
| Account reads | none (only the last 4 messages of the thread) | all, in parallel |
| Model | the cheapest tier | by the rules, doubt goes up |
| Card / search / upgrade | never (stripped by code) | as before |
| Typing indicator | the plain one only — never the progress line or a time estimate | as before |
| Safety checks before the client sees it | unchanged | unchanged |

- **The whole message must be small talk.** Greeting words, thanks words, a name ("היי
  ג'וני") and punctuation only, up to 70 characters, one line. "היי, תעלה לי את הסרטון"
  matches nothing and takes the full path on the strongest tier.
- **Two kinds.** A greeting ("היי", "מה קורה?") is never a yes to anything. A thanks or an
  "ok" ("אחלה", "תודה") right after we offered something may be a yes, so it still goes UP,
  as before.
- **Left on the full path on purpose:** "מה המצב?" and "מה חדש?" (a client may mean the
  account), a name alone, anything from a frustrated client.
- **The reply.** The model is told: one short line, like a colleague — not a status report,
  no numbers, no list of what it can do.
- **Sent right after a greeting:** a second message gets its own turn, or replaces the
  greeting's answer through the existing one-restart rule.
- The dashboard keeps a shorter copy of the list for display only; a phrase it misses just
  behaves as before. `tests/test_small_talk.py` runs both lists on the same phrases.
- Timing rows carry `small_talk: true`, so `/admin/models/` can show the difference.

## 5. Not tested against a real model or a real chat

- Haiku's answer quality and voice on simple turns (only stubs in tests).
- Real provider latency under parallel load, and rate limits when ~20 reads fire at once.
- Thread safety of the shared Supabase / httpx clients under concurrency (they are used from
  threads already by the router; not stress-tested).
- The real before/after numbers: run `scripts/chat_timing_report.sql` before the deploy, and
  look at `/admin/models/` (48 h) after it.

Tests: `tests/test_chat_speed.py`, `tests/test_model_routing.py`.
