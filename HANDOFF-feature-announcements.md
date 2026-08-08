# Feature announcements + keeping the interview current (2026-08-08)

Two halves of one problem: the product keeps growing, and two places that talk
about the product were not growing with it. Both now read **one** list.

## Investigation — what was actually there

### Part 1, the login-moments engine

`engagement_agent.run_login_moment` existed and worked, but nothing in it could
carry an announcement:

| | login moment | feature announcement |
|---|---|---|
| dedup | once per **day** | once per **announcement**, forever |
| audience | every client | only clients the feature applies to |
| delivered to | always the concierge's thread | the **owning specialist's** thread |

Three differences, all load-bearing — so this is a sibling function
(`run_feature_announcement`), not another fact inside `_collect_login_facts`.

**Storage.** `migrations/2026-08-03-operating-costs.sql` already argued this
case: `app_settings` is "a flat key→value store whose writable keys are
whitelisted in code", the wrong shape for a list that grows. Announcements are
exactly that, so they got a table on the same precedent.

### Part 2, the interview — worse than stale

`INTERVIEW_SYSTEM` instructed the model to *"Answer whatever they ask about the
platform, honestly and in plain words"* while giving it **zero facts about the
platform**. There was no feature list in the prompt at all. It was not that the
content had aged — there was no content, so the model answered from generic
knowledge or invented.

The only place features were described was the static 5-step welcome tour:
billing, the approvals area, connections, the activity feed, the support button.

**The gap**, measured against what is live today — none of this was explained
anywhere: leads + the auto-installed capture form, landing pages, external CRM,
the media hub, the personal area, exports, the specialist chat team, content
docs (Google Docs), the 90-day journey, YouTube, the avatar add-on, and
ManyChat/Make.

## The approach — one catalogue, two readers

`core/feature_catalog.py` — `FEATURES`, one entry per client-facing capability
(23 today). Each carries what it is, where it lives, **how the client gets it**
(`self_serve` / `manual` / `automatic`), the persona who owns it, and the
relevance rule.

- The interview renders `catalog_for_prompt(client_id)` — filtered to that
  client's package, so it never describes a YouTube add-on to someone without
  one.
- Announcement targeting calls `is_relevant(feature_key, client_id)` on the
  same entries.

**Adding a feature is one dict.** The interview starts explaining it, it becomes
announceable, and it appears in the admin dropdown — with no other edit. That is
the anti-staleness mechanism: there is nowhere else to update, so there is
nowhere else to forget.

The `access` field is the one that prevents a specific, embarrassing error: the
interview used to be free to describe ManyChat/Make as something you connect.
`manual` renders as "לא מפעילים לבד — מדברים איתנו קודם", and the prompt is told
what that means.

## How an announcement travels

1. Johnny opens **הגדרות → עדכוני פיצ׳רים ללקוחות**, picks the feature from the
   catalogue dropdown (which shows its audience and persona), writes a note in
   his own words, and saves as draft or live.
2. At each client's next dashboard load, `run_feature_announcement` takes the
   **oldest live, relevant, not-yet-sent** announcement — one per login, so a
   client returning after a quiet month is not met with five product messages.
3. One LLM call rewrites the note for that client in the owning persona's voice.
   `note` is never sent verbatim; twenty clients do not receive the same paste.
4. It is logged to that **persona's** channel (`support_agent.persona_channel`),
   so it appears as an unread dot on אורי/ליאור/… rather than in the concierge
   window. The frontend re-runs `loadChatUnread()` to surface the dot — unless
   that window is already open, in which case it is appended directly, because
   an unread badge on an open window is a message nobody sees.
5. Dedup is a `client_activity` row carrying `announcement_id`.

### Failure behaviour, deliberately chosen

- **LLM call fails → NOT marked as sent.** The announcement stays pending and
  the next login retries. Losing an announcement silently is worse than a delay.
- **Dedup read fails → say nothing** (`already_sent_ids` returns `None`). If we
  cannot prove what a client already received, repeating ourselves is the worse
  outcome.
- **Unknown `feature_key` → reaches nobody**, and the admin list flags it in
  yellow. A renamed/removed feature must not broadcast to everyone by default.
- **Migration not run → nothing happens.** The admin panel names the file;
  logins, the interview and every other login moment are untouched.

## Setup

`migrations/2026-08-08-feature-announcements.sql` in the Supabase SQL editor.
Nothing else — no env var, no vendor, no scheduler job (this is login-triggered,
like the other login moments).

## Verified in a real browser

Admin: the panel renders draft/live/archived with the right action per state,
flags an orphaned feature key, refuses an empty note, and create → publish →
stop → delete all issue the right calls. The pre-migration state names the
migration file instead of rendering an empty list that reads as "nothing yet".

Client: with the persona's window closed the message surfaces as an unread dot
on that specialist and never leaks into the concierge window; with it open the
message is appended directly; "nothing to announce" and an outright failure are
both silent, with no console error and no stray window.

Python is unrun as always. Structural checks stood in: every catalogue entry has
a valid persona (cross-read from `support_agent.PERSONAS`), a valid access value
and exactly one relevance rule; every prompt template's `.format()` placeholders
match its call site.
