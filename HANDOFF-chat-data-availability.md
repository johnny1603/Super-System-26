# HANDOFF — Stop pre-loading "unavailable" status into every chat turn (2026-10-13)

Investigated first (sections 1-3), then built as recommended in section 3. See
"What was built" at the end.

## 1. How `unavailable_right_now` works today

All of it lives in `agents/support_agent.py`.

**Concierge (`_answer_support_question`, ~L474-496).** Every turn, whatever the
message says, it eagerly calls all three getters:
`google_ads_agent.get_campaign_performance`, `meta_ads_agent.get_campaign_performance`
and `website_agent.get_site_overview`. A getter that throws is turned into
`{"connected": True, "error": ...}`. The Google and Meta getters don't throw. They
already *return* that same error dict (`google_ads_agent.py:104-106`,
`meta_ads_agent.py:130`). `_split_unavailable` then removes every error dict from the
payload and adds `payload["unavailable_right_now"] = [names]`.

**Personas (`_answer_persona_question`, ~L839-849).** This works the same way but only
for that persona's `reads`. A persona's reads that throw become `{persona}_data: {error}`,
which then becomes a name in the same list.

**So the answer to the question is yes.** A client with any persistently broken
connection gets `unavailable_right_now` in **every** concierge prompt, whatever the topic.
Example: Google Ads read fails on every call because there's no API access yet. The
same happens in every prompt to that platform's persona. Three prompt passages then ask
the model to work out when the list matters: `HANDOFF_RULE`'s "DATA THAT DIDN'T LOAD",
`SYSTEM`'s platform/website paragraphs, and each persona's `data_notes`. Any phrasing it
judges wrongly triggers the "can't pull that up" line.

**Side findings (these also need fixing, whichever design we pick):**
- **Failures are never cached.** `_perf_cache` is written only on success (`google_ads_agent.py:134`).
  A broken account therefore gets a fresh failing Google Ads API call on every chat message.
- **Every one of those failures also sends a raw, un-deduplicated `agent_alert`**
  (`google_ads_agent.py:105`; Meta has the same shape). That breaks the CLAUDE.md rule
  "don't add a raw `agent_alert` to a chat path". The deduplicated `_alert_degraded`
  runs on top of it.
- `consult_platform_agent` (L84) is never called. It is dead code.

## 2. The proposed alternative: real tool calls, fail at fetch time

The model would get read tools (`get_google_ads_performance`, ...). It calls one only
when the question needs that data. If the call raises, code returns the fixed
"can't pull that up right now" line. Health-state context would no longer exist.

What that requires:
- **A new LLM helper.** `core/claude_json.py` has no loop for client-defined tools.
  `claude_web_search_call` handles only the server-side web_search tool. We'd need a
  `tool_use` → execute → `tool_result` loop that still ends in the strict JSON contract,
  plus truncation and retry handling.
- **Splitting `PERSONAS[..]["reads"]` into separate tools** while keeping the
  read-only-by-construction rule. `core/control_room.py:347` also calls `reads`
  directly for the admin chat, so it would need changes too.
- **A second round trip on every data question.** Today the concierge answers "how are
  my campaigns?" in one call because the data is fetched in advance. With tools it
  takes at least two, and latency is already the main complaint (CLAUDE.md).
- **Touching five chat surfaces** (concierge + 4 personas) and the interview, which
  routes through `PERSONAS`.

**Rough size:** 2-3 days, with the most regression risk in the chat. None of it can be
run on this machine.

**The honest catch:** tool use doesn't remove model judgment. It moves it. The model
still decides *whether to call* the tool. If it calls a broken tool on a borderline
message, we get the same misfire. The approach is more robust for one reason only: the
model never sees failure state while it decides. That property is what fixes this class
of bug, and we can get it much more cheaply.

## 3. Recommendation: the model only labels the question, code decides

This reuses the pattern the concierge already has for `web_search_query` and
`upgrade_request`: the model *declares*, and code *acts*. It's the same split as the
health scan, where code decides when an issue is raised.

1. **The model never sees failure.** A read that fails is simply left out of the
   payload. No `unavailable_right_now`, no error block, no prompt rule about broken
   data. Delete `_split_unavailable` and the "DATA THAT DIDN'T LOAD" rule, and rewrite
   the "listed in unavailable_right_now" sentences in `SYSTEM` and in each persona's
   `data_notes`.
2. **"Not connected" is still a positive fact.** The getters already return
   `{"connected": false}` without making an API call. Keep that in the payload, so
   the "press חבר עכשיו" rule keeps working off a true statement rather than a
   missing field.
3. **New JSON field `data_topic`.** It names which live data the question is about:
   `""` / `google_ads` / `meta_ads` / `website`. The model fills it on every turn,
   for healthy and broken clients alike. That means we can check its accuracy on
   any account, and it can't be influenced by failure state it never sees.
4. **Code decides.** If `data_topic` names a read that actually failed **this turn**
   (a caught exception or error result, tracked in a local set), the reply is replaced
   with a fixed in-voice line. That's a new
   `chat_fallback.data_unavailable_reply(persona, lang, platform)` in he/en/fr/ar/ru,
   with the same per-persona tails as `technical_reply`. In every other case the
   model's reply is used unchanged. A healthy client and an unrelated question can no
   longer produce the fallback line, whatever the phrasing. Both conditions are needed:
   a real failure and a matching topic.
5. **Side fixes (same change):**
   - Cache failed reads for a short TTL (e.g. 5 min) so a broken account isn't
     re-queried on every message.
   - Drop the raw `agent_alert` in the Google and Meta getters' error branch, because
     `_alert_degraded` already sends a deduplicated alert. Check first that no scan
     depends on the raw alert.
   - Delete `consult_platform_agent`.

**Size:** about half a day. Two JSON contracts gain one field, one new fixed-text
function, two small override branches, prompt deletions, and the getter cache/alert
fix. No new LLM path and no added latency.

**Tradeoffs I accept:**
- A mixed message, like "how are my campaigns, and what should I post for Hanukkah?",
  sent while Google is broken gets the fixed line. The second half goes unanswered.
  The client can ask again, and it never produces a false alarm.
- If the model *misses* the tag on a real data question, it answers without data. The
  existing rule "never invent numbers" makes it say it doesn't have those numbers in
  front of it. That's acceptable, and it's the opposite of today's failure.
- The model still classifies the question. Some relevance judgment can't be avoided:
  even the tool design needs it. What changes is that the judgment no longer depends
  on hidden failure state, and code makes the final decision.

**Why not keyword matching in code (option 4 in the brief):** it would be brittle in
the other direction. It would miss Hebrew inflections, slang, and English and Hebrew
mixed in one message. And "only on a caught exception" alone can't work while we fetch
in advance: the exception has already happened before we know what the client asked.
Something has to link the failure to the question. The `data_topic` label is that link,
and it is produced without knowing about any failure.

**Verdict:** go with section 3. Keep tool use for later, if we ever need data fetched on
demand for another reason, such as expensive reads we don't want to pre-fetch. It is not
worth doing just for this bug.

## What was built

- **`agents/support_agent.py`**
  - `_split_unavailable`, `_degraded_reads` and `_alert_degraded` are gone. They're
    replaced by:
    - `_add_reads`: leaves failed reads out and returns the failed topics.
    - `_data_unavailable`: code's decision.
    - `_recent_activity`: see the extra leak below.
  - The "DATA THAT DIDN'T LOAD" rule is replaced by a DATA TOPIC labelling rule in
    `HANDOFF_RULE`.
  - Both JSON contracts gain `data_topic`.
  - The concierge now passes `{"connected": false}` for unconnected platforms.
    Before, such a platform's field was just absent, which looked the same as a
    failed read.
  - `consult_platform_agent` is deleted.
- **`core/chat_fallback.py`**
  - `data_unavailable_reply(persona, lang, topic)`: fixed text in 5 languages, with
    the persona tails.
  - `alert_once(...)`: the durable client dedup, usable outside a chat handoff.
- **Google/Meta getters**
  - Failed reads are cached for 120 s (`PERF_FAILURE_CACHE_TTL_SECONDS`).
  - The raw `agent_alert` is replaced by `alert_once`, keyed
    `<platform>:performance_read_failed`, once a day per client. That also covers
    the getters' non-chat callers: dashboard, budget, lead volume, engagement.
  - `ads_issue_detected` (`_raise_issue`) was deliberately not used. That row shows
    in the client's activity feed as "campaign issue detected", and it would have
    reached the chat payload.
  - The website getter already cached failures for 5 min and doesn't alert, because
    its daily health scan does. Unchanged apart from its comment.
- **Alerts now**
  - One alert per broken integration per day, from the getter. The website is
    covered by its scan.
  - Plus one "a client asked about X and couldn't be answered" alert, with the
    question, per client+topic per day. It's shared by the concierge and the
    personas (`technical:read:<topic>`).
  - Before, every chat turn re-alerted per persona.
- **Extra leak found and fixed**
  - `recent_activity` (15 rows) in every chat payload included the hidden
    `chat_fallback` dedup rows, such as `technical:general:read:google_ads_performance`.
  - That was standing failure context in front of the model too.
  - Both chat paths now filter `agent_name='chat_fallback'` out, as the client feed
    already does.

Not run: there's no Python on this machine, so the code was reviewed by reading only.

## Verify after building (not runnable here)
- Google Ads broken (e.g. revoke the token on a test client):
  - "אפשר לדבר איתך על העסק שלי?" → normal answer.
  - "ספר לי על השירותים שלכם" → normal answer.
  - "מה קורה עם הקמפיינים בגוגל?" → fixed line, plus one deduplicated alert.
- Same questions on a healthy client → never the fixed line.
- A client with no Google account connected, asking about Google → "חבר עכשיו".
