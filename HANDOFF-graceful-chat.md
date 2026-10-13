# HANDOFF — Graceful "I don't know": no dead-end chat errors (2026-10-13)

## What existed before (investigated first)

Every place a failure reached a client or prospect:

| Chat | Path | What the client saw | Team alerted? |
|---|---|---|---|
| Dashboard concierge + 4 specialist personas | `support_agent` LLM returns bad JSON | `_FALLBACK`: "מצטערים, הייתה תקלה קטנה מהצד שלנו…" — same line in every persona's voice | yes (no dedup) |
| same | LLM **API error** (timeout/overload/5xx) — `except ClaudeJSONError` didn't catch it | bare 500 → dashboard "מצטערים, הייתה תקלה קטנה — אפשר לנסות שוב? 🙏" | **no** |
| same | any DB/plumbing error in the turn | same 500 → same line | **no** |
| Concierge | web search failed | stage-1 HOLDING line "שנייה, בודקים את זה לעומק" — promises a check that never comes | yes |
| Concierge | upgrade build failed | HOLDING line "רגע, מכינים לך הצעה" — promises a proposal that never comes | yes |
| Concierge | a platform read threw | field dropped → model told a CONNECTED client to "press חבר עכשיו" | no |
| First-login interview | non-JSON failure | 500 → dashboard silently left interview mode, no message | **no** |
| Sales chat | objection reply failed | "מצטערים, הייתה תקלה קטנה — אפשר לנסות שוב? 🙏" | **no** |
| Sales chat | proposal build failed (after minutes of answering) | 500 JSON → `displayProposal(undefined)` threw inside a timeout → **chat silently frozen**; network failure → "אירעה שגיאה — נסה שוב…" with nothing to tap | **no** |
| Sales chat | checkout failed (ready-to-pay prospect!) | "אירעה שגיאה בהכנת התשלום — צור קשר בוואטסאפ" | **no** |
| Sales chat | reactions / dynamic questions / filter failed | nothing — silently skipped (already fine, unchanged) | no |

`agent_alert` has no dedup anywhere; the dedup idiom exists only in the scans
(`ISSUE_DEDUP_DAYS` + a `client_activity` row with `details.issue_key`).

## What was built

`core/chat_fallback.py` — the one module every chat path uses:

- **OUT OF SCOPE** (legal, tax, medical, contracts, decisions only the team can
  make): the model is working, so the MODEL answers in its own voice
  (`support_agent.HANDOFF_RULE`, shared by concierge + all personas; the closer's
  prompt in `onboarding_agent.handle_objection`) and flags `"out_of_scope": true`.
  The server alerts. Fixed text only if it flagged but wrote nothing. A
  persona's redirect to the main chat for another uallak topic is NOT this.
- **TECHNICAL**: no model to write the reply, so it is fixed text — persona-lane
  tails ("…כל דבר אחר על הקמפיינים בגוגל") in he/en/fr/ar/ru, gender-neutral
  Hebrew first person (the concierge's gender is the client's choice), language
  detected from the message script. No codes, no "system error".
- **Move on**: the fixed line always ends with what you can do next; the prompt
  rule says name the limitation once and don't revisit a handoff already in
  `conversation_history`.
- **Dedup** (`alert_handoff`): clients → `client_activity` row
  (`agent_name='chat_fallback'`, `action_type='chat_handoff_alerted'`,
  `details.issue_key`), 3 days for out-of-scope/follow-up, 1 day for technical;
  key = kind + persona + normalized-question hash, so the SAME question is quiet
  and a different one isn't. Prospects (no client row) dedupe in process memory,
  6 h, per lead.

Per path:
- `answer_support_question` / `answer_persona_question` never raise, never
  return empty; catch `Exception`. Failed web search / upgrade build → technical
  handoff instead of the stale holding line. Failed platform reads are marked
  `{"error": …}` (not dropped) and alerted (`_alert_degraded`).
- `/api/client-chat` catch-all returns an in-voice reply (logged to the thread),
  never a 500. The dashboard's `chat_error` now only means "didn't reach me" —
  and puts the text back in the box.
- Interview: catches everything, deduped alert; dashboard shows the not-delivered
  line instead of silence.
- Sales chat: objection endpoint always returns a reply (out-of-scope alert with
  lead id); proposal failure alerts with the lead id and the page offers a
  **one-tap retry with the same answers**; checkout failure alerts **with the
  prospect's name/phone/email/package** and tells them the team will reach out
  (no auto-retry — every attempt creates a client row); a dropped connection at
  checkout offers a retry.
- Client activity feed hides the `chat_fallback` rows (and the
  `website_authorize_*` rows from the one-click connect work, which also
  showed raw).

## Regression fix (2026-10-13, same day)

Ordinary questions ("can I talk to you about my business?") were getting the
"having trouble pulling that up, passed to the team" line. What changed in
this handoff that caused it:

1. `HANDOFF_RULE`'s data rule handed the model a ready-made sentence ("אני נתקל
   בקושי לשלוף את זה כרגע, אני כבר מעביר לצוות שיבדקו") triggered by "a field
   you would need carries an error" — and the model applied it loosely.
2. Failed reads were kept IN the payload as `{"connected": true, "error": …}`
   blocks (this handoff made thrown reads do that too). A client whose Google
   Ads read fails on every call had an error block in front of the model on
   every message.
3. Older prompt lines pushed the same way: "answer… using ONLY the client data"
   and "unrelated to their account → team follow-up"; the sales closer listed
   "guarantee of results" and discounts as out of scope.

Narrowed, not rebuilt: failed reads now leave the payload and become a bare
`unavailable_right_now` name list (`_split_unavailable`), relevant ONLY when
the client asks about that data; `HANDOFF_RULE` opens with "answer by default"
(ordinary conversation listed explicitly; exceptions are rare); follow-up is
for change requests/complaints/billing/missing account facts; the sales
out-of-scope list is legal/tax/medical/contracts only. Fixed technical text
still fires only on a real exception or empty reply — unchanged.

**Superseded the same day:** the narrowing still misfired on new phrasings, so
the name list was removed entirely. The model now never sees a failed read; it
labels each message's `data_topic` and code decides. See
`HANDOFF-chat-data-availability.md`.

Verify after deploy (not runnable here — no API key on this machine):
ordinary → direct answer: "אפשר לדבר איתך פה על העסק שלי?", "ספר לי על
השירותים שלכם", "how does this work?", "do you guarantee results?";
still hands off: "האם החוזה הזה מחייב אותי משפטית?" (out_of_scope + one alert),
"how are my Google campaigns doing?" while Google Ads fails to load (says it
can't pull them now), and a revoked API key (fixed technical line).

## Limits

- Prospect alert dedup is per Cloud Run instance (memory) — worst case one
  extra alert per instance per 6 h.
- The interview's fixed fallback is still Hebrew-only (pre-existing).
- Not run: no Python on this machine. JS parsed with Node; Python reviewed by
  reading. First test: ask a persona a legal question (expect an in-voice
  handoff + ONE alert, none on the repeat), and temporarily break
  `ANTHROPIC_API_KEY` on a dev revision (expect the fixed line, not a 500).
