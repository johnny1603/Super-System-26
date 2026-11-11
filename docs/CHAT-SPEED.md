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

## 5. Not tested against a real model or a real chat

- Haiku's answer quality and voice on simple turns (only stubs in tests).
- Real provider latency under parallel load, and rate limits when ~20 reads fire at once.
- Thread safety of the shared Supabase / httpx clients under concurrency (they are used from
  threads already by the router; not stress-tested).
- The real before/after numbers: run `scripts/chat_timing_report.sql` before the deploy, and
  look at `/admin/models/` (48 h) after it.

Tests: `tests/test_chat_speed.py`, `tests/test_model_routing.py`.
