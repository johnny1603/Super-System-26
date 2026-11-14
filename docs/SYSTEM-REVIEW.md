# System review and upgrade — 2026-11-15

One run, seven parts. Each part ends with a checkpoint report appended at the bottom
(what was found, what changed, what was NOT tested, cost so far).

**How this review was done.** From the code only. This machine has no production
credentials: no database, no logs, no `chat_turn_timings` rows, no admin session. Every
verdict below says whether it rests on code that was read, or on an inference that the owner
can confirm with one command. Tests: the repo's own stdlib suite (`scripts/run_tests.py`),
run locally before every commit.

**Cost cap (proposal).** This run's own cost cannot be metered from inside the session — the
real figure is `/cost` in the terminal. Rough estimates are given per checkpoint. Proposed
cap: **$40**; the run stops and asks before the estimate passes it. No production model, paid
vendor or Google call is made by this run.

---

## 1. Bugs, breakages and fragile spots

Verdict key: **real** = a true problem that needs an action · **false** = the alert is wrong
or misleading · **stuck** = it was true once and never clears.

### 1.1 The recurring alerts

| # | Alert | Verdict | Cause (code read) | Status |
|---|---|---|---|---|
| A | `invalid_grant` in the Performance check, client 27 | **real cause + stuck alert** | `performance._tracking_card` reads Tag Manager with the stored grant; Google answers `invalid_grant`. The card correctly says "reconnect". The ALERT re-fires every day while it stays that way (`alert_once(..., days=1)`), and nothing closes it after a reconnect. | Fixed: one open alert per key; closes itself when the grant works. |
| B | `tagmanager 401` | **real, mis-labelled** | A 401 from `tagmanager.googleapis.com` = Google rejects the access (revoked while our 1-hour access token was still cached). The write path already treats 401 as "reconnect" (`website_agent.gtm_failure_reason`); the READ path (`performance._classify_google_failure`, `gtm_connection._failure`) called it `sync_failed` / `unavailable` — so the client saw a vague failure, no health was recorded, and the alert repeated daily. | Fixed: 401 = reconnect on the card and in the health record. |
| C | Duplicate "no sales lead linked" | **real condition, false duplicate** | The condition is true (client 27 has no `leads` row with its `client_id`). The duplicate is a race: since 2026-11-11 a chat turn reads its context sources in parallel, several of them call `lead_tracking.client_sales_lead`, and each checked the dedup marker before any of them wrote it. It also re-alerts weekly by design. | Fixed: check + marker under one lock, and one open alert per key. The condition itself is the owner's (1.3). |
| D | Stale / duplicate grants | **real** | `client_agent.upsert_account` picked the row to update with `limit(1)` and NO order — the database chose. Every reader takes the NEWEST row (`order("id", desc=True)`). Where two rows exist for one platform, a reconnect could be written into the row nobody reads. | Fixed: the writer now updates the newest row, the same one the readers take. Old duplicate rows are NOT deleted (1.3). |
| E | "Reconnect" nudges although a working grant exists | **stuck** | Consequence of D (the new grant sat in the unread row) plus A (the alert never closed). Google Ads health is bound to the grant fingerprint and clears on a new grant; Tag Manager health clears on the next successful read; but neither closed the admin alert, and the morning briefing / Control Room read open alerts. | Fixed by D + auto-resolve. |

### 1.2 Fixed in this part (safe, separate commit)

- **One open alert per key** — `core/alert_hygiene.open_duplicate`, called by the one alert
  sink (`master_agent._persist_alert`). Key = source + text with timestamps and long ids
  removed. Audit sources are exempt. A resolved alert never blocks a new one.
- **Alerts close themselves when the connection works** — `alert_hygiene.connection_works`,
  called on the dead→ok transition in `google_ads_health.record_ok`,
  `gtm_connection._record_health` and `tiktok_grant._write`. Only alerts that name
  "client <id>" from that connection's sources; audit rows never.
- **Health checks and reconnects use the newest grant** — `upsert_account` orders by id.
- **401 from Tag Manager = reconnect** (card + health), as the write path already said.
- **`alert_once` race** closed with a lock and an in-process memo.
- Guard: `tests/test_alert_hygiene.py`.

### 1.3 Left for the owner (riskier, or needs production access) — NOT done

1. **Client 27's duplicate `client_accounts` rows.** Deleting a stored grant is destructive,
   so nothing was deleted. Run `python3 scripts/diagnose_google_grants.py 27` in Cloud Shell
   (read-only; the script was written in an earlier session and is committed now). It lists
   every Google row and flags duplicates. If an older duplicate exists, removing it is a
   one-line SQL — ask and it will be written for the exact ids.
2. **Client 27 has no linked sales lead.** True condition. Fix = link the row
   (`migrations/2026-10-18-link-leads-by-client-id.sql`) or, for an internal test client that
   never went through the sales chat, accept it: the alert is now a single open row; resolve
   it once.
3. **Is client 27's Tag Manager grant really dead?** After the deploy: Itzik → "רענן חיבור
   gtm ללקוח 27" (or the diagnose script §3). Dead = the client reconnects once from the
   card; alive = the alerts close by themselves on the next read.
4. **Scheduler coverage is unknown from here.** `scripts/schedule_jobs.sh` manages 12 jobs.
   These cron-style routes exist in the code and are NOT in its table: `/api/monitor/scan`,
   `/api/paypal/invoice-aging-scan`, `/api/google-ads/scan`, `/api/google-ads/weekly-report`,
   `/api/meta-ads/scan`, `/api/meta-ads/weekly-report`, `/api/meta-content/scan`,
   `/api/youtube-content/scan`, `/api/website/scan`, `/api/media/weekly-checkin`,
   `/api/avatar/scan`, `/api/website/php-exposure-scan`, `/api/budget/scan`,
   `/api/pricing/monitor-scan`. Either they were scheduled by hand long ago (then they are
   unmanaged and can drift), or they never run. `bash scripts/schedule_jobs.sh --audit` shows
   which. Adding jobs changes what runs in production and costs model calls, so it was not
   done alone.

### 1.4 Other fragile spots found (not changed unless a later part says so)

| Spot | Why it is fragile | Suggested fix |
|---|---|---|
| `monitor_agent` memory in `data/` | `monitor_memory.json` and `alert_history.json` live on the container disk, wiped every deploy: the deep scan re-reports "already reported" issues after each deploy, and its reviewer-issue counts restart from zero. | Move both to `app_settings` (no migration). Small; candidate for Itzik's first self-proposed fix. |
| Alerts have no `client_id` column | Matching is by the text "client 27" (`control_room`, purge, and now auto-resolve). A message that forgets the convention is invisible per client. | A nullable `client_id` column + backfill — a migration; owner's call. |
| Time-window dedup everywhere (`alert_once(days=N)`) | A condition that persists re-alerts every N days forever. Now masked by one-open-per-key, but only while the owner leaves the alert open. | Fine as is; noted. |
| `youtube_content_agent.run_channel_detection_scan` | Raw `agent_alert` on an expired parked token, and detection by `"invalid_grant" in str(e)`. Fires once (it deletes the row), so not a repeat offender. | Leave. |
| Google access-token cache (`google_ads_service._access_token_cache`) | Keyed by refresh token, per instance, ~59 min. After a revoke the cached token gives 401 until it expires — now classified correctly (B), but the cache entry is not dropped on a 401. | Drop the entry on 401 — touches every Google caller; proposed, not done. |
| YouTube and Meta have no grant-health record | Google Ads, Tag Manager and TikTok record "dead grant" once and stop calling. YouTube and Meta read failures still go through per-call `alert_once`. | Same model for both; medium work, no migration. |
| Many `async def` routes call blocking code | Known trap in CLAUDE.md: one long call blocks the event loop for everyone. | Part 2 looks at the chat routes only. |
| `operator_agent.history()` deletes old rows on every read | A read that writes; harmless but surprising (30-day prune). | Leave. |
| Client chat fixed technical line says "העברתי לצוות" | `chat_fallback._TECHNICAL_OPENING` still tells the client it was "passed to the team" in all five languages — against the rule "no passing to the team". | Reworded in Part 2 (client-facing text, same meaning: "I'm on it"). |

---

## 2. The upgrade requests: what exists, plan, cost, risk

| Part | Exists today | Plan | Run cost / risk |
|---|---|---|---|
| **1 Itzik** | 23 tools in 3 tiers, rule routing (haiku/sonnet/top), per-task budget + continue card, stop between steps, emailed code for PR and deploy, floating chat on admin pages. Missing: integration health in one read, cost and speed reports, alert triage, a live progress line, a memory of the task across messages, a self-description. | New READ tools (integration health for every client, cost report, chat-speed report, alert triage, scheduler/job expectations, errors summary), new SAFE actions (bulk-resolve an alert group — owner tap; re-check one connection — agent), a code-built "what I can do" answer, task memory in `app_settings`, a progress line + time estimate from his own measured history, improvement proposals raised after a diagnosis (as cards — nothing runs). Approvals unchanged. | Medium. Risk: a new tool leaking a secret → every tool returns states/counts only, tested. No permission widened. |
| **2 Speed** | Rule routing, parallel context reads, single-flight, prompt caching, timing table + admin page. No time estimate for slow work; one fixed "still working" line after 15 s. | No timing data reachable → code analysis, labelled. Estimated time per task type from MEASURED history (`agent_actions` durations per action; `chat_turn_timings` per route), shown as "about 2 minutes" + one progress line. Remove remaining serial reads where found. | Low–medium. Risk: wrong estimates → shown only when ≥5 past runs exist, else no number. |
| **3 Animations** | Standing figures on minimised chat windows, one `figureIdle` hook, animated triangle background with reduced-motion handling. | `dashboard/assets/agent-life.js` + css: idle motions with random timing (breath, weight shift, glance, small gesture toward a neighbour), one confetti + balloon burst when a task finishes; CSS + one small canvas; paused on hidden tab, off under reduced motion, a setting to turn off. | Low. Risk: distraction / CPU → transform/opacity only, never while a chat is active. |
| **4 Voice** | No transcription anywhere (searched code + services). ElevenLabs is per-client keys and used for cloning only. Claude takes no audio. | **No new paid service**: the browser's own speech recognition, text into the input box for review, sent as normal text. No audio reaches our server; nothing stored. Hidden where the browser lacks it (Firefox). A server engine (paid) is the owner's later choice — see §4 below. | Low. Risk: browser coverage; the vendor of the browser processes the audio (must be in the privacy policy — TODO below). |
| **5 Onboarding** | Journey panel (`journeyPanel`: bar + track) and a long connection list on the client dashboard; `/onboarding/` is the business questionnaire. | Compact visual: progress ring, ONE highlighted next step, grouped cards, completed collapsed, a small celebration on completion. Same data (`client_journey`), same required logic, one decision per card. | Medium (largest file in the repo). Risk: breaking a connect button → buttons keep their existing handlers. |
| **6 Homepage** | `/` static page, 14 sections, reviewed by Google and TikTok. | A v2 presentation layer (hero motion, scroll reveals, micro-interactions, spacing/type) loaded ONLY when `homepage_v2_enabled` is on (default OFF). Same URL, same text, same links; the HTML Google reads is unchanged. | Low. Risk: Google sees a change → flag off = byte-identical content and no extra file loaded. |

## 3. Recommended order

0 → 1 → 2 → 4 → 3 → 5 → 6 was considered; the run keeps the owner's order (0–6) because no
part blocks another, and 3 (animations) gives 5 (onboarding celebration) its confetti.

## 4. Open decisions for the owner (asked, never decided alone)

- **Voice transcription engine.** Built now on the browser's own recognition (free, no key).
  Works in Chrome, Edge, Safari (incl. iOS); not in Firefox, where the button stays hidden.
  A paid server engine would work everywhere and is usually better at Hebrew — a new paid
  service + key, so it is NOT added. Say the word and it is one module behind the same button.
- **Scheduler jobs** not in the managed table (1.3 item 4).
- **Client 27 duplicates / missing lead link** (1.3 items 1–2).

## TODO (deferred on purpose)

- **Privacy policy must mention voice recordings** before the voice button is promoted:
  that the microphone is used only while the client holds the record button, that the audio
  is turned into text by the client's own browser/OS speech service (Google for Chrome,
  Apple for Safari, Microsoft for Edge) and is not sent to or stored by uallak, and that only
  the text the client chooses to send is kept as a normal chat message. NOT edited now — the
  policy text is frozen until Google answers the verification.

---

# Checkpoint reports

## Checkpoint 0 — scan + safe alert fixes

- **Found:** the five recurring alerts traced to code (table 1.1); a reconnect write that
  could land in an unread duplicate row; a parallel-read race in `alert_once`; 401 from Tag
  Manager mis-labelled as a sync failure; 14 cron-style routes outside the managed scheduler
  table; ephemeral monitor memory; a "passed to the team" line still shown to clients.
- **Changed:** `core/alert_hygiene.py` (new), `agents/master_agent.py`,
  `core/chat_fallback.py`, `agents/client_agent.py`, `core/google_ads_health.py`,
  `core/gtm_connection.py`, `core/tiktok_grant.py`, `core/performance.py`,
  `tests/test_alert_hygiene.py` (new), `scripts/diagnose_google_grants.py` (committed).
- **Tests:** 68/68 test files pass locally.
- **NOT tested:** against the real `alerts` table (the duplicate check and auto-resolve use
  PostgREST filters that only a fake table exercised); against Google (no live 401 /
  invalid_grant was reproduced); client 27's real rows were not read.
- **Migration:** none. **Deploy:** needed for the fixes to take effect.
- **Estimated cost so far:** about $6 (rough; `/cost` is the real number). Cap proposed: $40.

## Checkpoint 1 — Itzik (operator agent)

- **Found:** he could read one thing at a time (a log window, one client, the newest alerts)
  but had no wide view: no "all connections", no alert grouping, no cost or speed report, no
  list of what should be scheduled. Nothing was remembered between messages except the chat
  text. A long task showed one fixed line ("up to about 3 minutes"). "What can you do?" cost
  a model call and could drift from the real tool list.
- **Changed:** `core/operator_insight.py` (new): 7 wide reads, his notebook (task note +
  improvement ideas), the code-built self-description. `core/operator_agent.py`: 10 new tools
  wired in, prompt sections for wide reads / notebook / ideas / long tasks, the progress line
  with a measured time estimate, the notebook block after the cached prompt.
  `core/operator_actions.py`: `check_client_connections` (agent, reads only) and
  `resolve_alert_group` (owner tap only). `core/api_server.py`: 3 admin-only routes
  (progress, ideas, idea status). `dashboard/assets/itzik-chat.js`: progress polling, idea
  card. Docs: `docs/OPERATOR.md` section "Insight, notebook, progress".
- **Tiers:** unchanged. Code + deploy still need the emailed code at each step; the budget
  cap, the stop control and routing by request type are untouched. The only new write the
  agent can make on its own is to its own notebook rows.
- **Tests:** 69/69 test files pass (new: `tests/test_operator_insight.py`, 29 tests).
- **NOT tested:** with a real model (whether he picks the new tools well, and whether the
  longer prompt changes his answers); the real `client_costs` / `chat_turn_timings` /
  `alerts` tables; Cloud Logging for `error_summary`; the chat in a real browser (the JS was
  parse-checked only); two Cloud Run instances serving one task (the progress row is shared
  through app_settings by design, not exercised).
- **Migration:** none (app_settings rows only). **Deploy:** needed.
- **Estimated cost so far:** about $13 (rough). Cap proposed: $40.

## Checkpoint 2 — speed of all chats

- **Data:** `chat_turn_timings` was NOT reachable (no production credentials on this
  machine). Everything here is code analysis and is labelled ESTIMATE in
  `docs/CHAT-SPEED.md` section 4a. The real numbers: Itzik "why is the chat slow" (his new
  speed report) or `/admin/models/`.
- **Found:** the earlier pass already routes by request type and reads context in parallel.
  What a full turn still waits for: the 3 s collect window, the slowest live context read
  whenever the per-instance cache has expired (120 to 600 s), and the model. The context
  reads do not depend on the message, so they can start before it is sent. No time estimate
  was shown anywhere except one fixed line after 15 s.
- **Changed:** `core/chat_prewarm.py` (new) + `POST /api/client-chat/prewarm`: the context is
  read when the client focuses a chat box or starts typing. `core/task_eta.py` (new) +
  `GET /api/client-chat/pace`: measured time in plain words, 5 languages, for running action
  cards and for the chat long-wait line. `dashboard/client/index.html`: the two triggers and
  the measured line. `core/chat_fallback.py`: the fixed technical line no longer says
  "passed to the team". Internal chat (Itzik): the progress line from Part 1.
- **Answer quality:** unchanged by construction. No prompt, model tier, token limit or
  safety check was touched; nothing is skipped; the same readers run, only earlier.
- **Before / after (ESTIMATE):** a turn after a pause longer than about 2 minutes loses most
  of its context wait when the client typed for longer than the reads take. A turn inside
  the cache window, the collect window and the model time are unchanged.
- **Tests:** 70/70 test files pass (new: `tests/test_task_eta.py`, 18 tests).
- **NOT tested:** real latency before and after; vendor rate limits when a prewarm and a
  turn overlap (single-flight covers the Google, Meta and website readers, by code only);
  the estimates against real `agent_actions` rows; the dashboard in a real browser (inline
  JS parse-checked only); more than one Cloud Run instance.
- **Left for the owner:** the out-of-scope fixed line (`chat_fallback._OUT_OF_SCOPE_OPENING`)
  still says the question was passed on and someone will get back. That is true today (it
  raises an "answer personally" alert to the owner), so removing it changes what the client
  is promised: a product decision, not done. The collect window (3 s) is an existing admin
  setting.
- **Migration:** none. **Deploy:** needed.
- **Estimated cost so far:** about $19 (rough). Cap proposed: $40.

## Checkpoint 3 — humanizing: agent motions and the celebration

- **Found:** the standing figures of minimised chat windows already existed with an unused
  animation hook; nothing moved, and a finished task changed a card's text only.
- **Changed:** `dashboard/assets/agent-life.js` + `agent-life.css` (new), loaded deferred by
  the client dashboard only. Idle motions (breathe, weight shift, glance, nod, lean toward a
  neighbour who nods back) with random figure, motion and pause; one confetti + balloon burst
  per finished task, from the waiting agent. Hooks in `finishActionCard` / `decideAction`.
  A switch in "My account" (5 languages). `docs/AGENT-LIFE.md`.
- **Rules kept:** never during an active conversation; transforms only; no library; no loop
  between motions; paused on a hidden tab; nothing at all under `prefers-reduced-motion`;
  once per task; chat speed and load path untouched (deferred, no fetch).
- **Tests:** 71/71 test files pass (new: `tests/test_agent_life.py`, 12 tests; the behaviour
  half runs the real script in Node against a small fake page).
- **NOT tested:** a real browser. Not seen with real eyes: whether the motions read as
  natural on the real figure images, the look of the confetti, iOS Safari, RTL placement of
  the burst. CPU cost was reasoned (one class toggle every few seconds, a 2.6 s canvas), not
  profiled.
- **Limits:** client dashboard only (Itzik's figure on admin pages is not animated); the
  figures are flat images, so a glance is a small lean, not a head turn; the switch is per
  browser.
- **Migration:** none. **Deploy:** needed.
- **Estimated cost so far:** about $24 (rough). Cap proposed: $40.

## Checkpoint 4 — voice messages

- **Checked first:** no transcription capability exists in the code or in any connected
  service (Claude takes no audio; ElevenLabs is cloning and text-to-speech on each client's
  own key; nothing transcribes). A server engine would be a new paid service and key, so it
  was NOT added and is left as the owner's decision.
- **Built without a new service:** `dashboard/assets/voice-input.js`. A microphone button in
  every client chat window; the browser's own speech recognition writes the words into the
  input box; the client reviews, edits and sends ordinary text. Recording strip with elapsed
  time, Cancel (restores the box) and Done; 2-minute cap; calm lines for a refused or missing
  microphone, silence and an unreachable service; stops on send, minimise, close and a hidden
  tab; 5 languages (Arabic falls back across locales). `docs/VOICE-INPUT.md`.
- **Raw audio:** uallak never receives it and stores nothing. The script does not open the
  microphone itself, records nothing and uploads nothing (a test scans the file for
  getUserMedia, MediaRecorder, fetch and storage calls). The browser vendor's speech service
  processes the audio; that is why the privacy-policy TODO above exists. The policy text was
  not touched.
- **Coverage (from documentation, not from devices):** Chrome, Edge, Safari including iOS
  14.5+ with Dictation enabled. Not Firefox and not most in-app browsers: there the button
  is not drawn.
- **Tests:** 72/72 test files pass (new: `tests/test_voice_input.py`, 16 tests; the
  behaviour half runs the real script in Node with a fake recogniser).
- **NOT tested:** any real browser or phone. Specifically unverified: iOS Safari end to end,
  the quality of Hebrew and Arabic recognition, the Android restart behaviour with real
  audio, the layout of the strip in a narrow chat window, and the microphone permission
  prompt wording per browser.
- **Open decision for the owner:** a paid server transcription engine (works in every
  browser, usually better Hebrew) — needs a new service, key, env var and a policy change.
- **Migration:** none. **Deploy:** needed.
- **Estimated cost so far:** about $29 (rough). Cap proposed: $40.

## Checkpoint 5 — client onboarding

- **Reviewed:** the whole first-login path (checklist from the purchased package, the
  connection nudge, connect-through-chat, the 90-day clock stamp, the interview gate, the
  journey panel). The logic is sound and was not changed. Found: the connections area is one
  long undifferentiated grid of up to 17 cards; progress is a thin bar; the journey is a
  sideways-scrolling row; nothing marks a completed step; and (bug) the guide stayed in the
  old language after a language switch.
- **Changed (`dashboard/client/index.html` only):** the guide is now a hub: progress ring,
  one chip per required step, one highlighted next action. The same cards are grouped (next
  step / after that / connected / more), the last two folded behind one tap. Journey shown
  as one summary line with the full track on request. A completed step pulses the ring,
  shows one line and fires the one-time burst. The language switch repaints the hub; the
  hub also follows a connection made on the page without a reload.
- **Kept:** what is required, its order and what counts as done still come only from the
  server; one decision per card (same nodes, same single connect button, same handlers);
  an unreadable package shows no hub and no grouping, exactly the old plain grid.
- **Tests:** 73/73 test files pass (new: `tests/test_onboarding_ui.py`, 15 tests; the
  grouping function itself runs in Node against a fake grid).
- **NOT tested:** a real browser, a real client account, a phone. Unverified by eye: the
  ring and chips layout in RTL and on a narrow screen, the look of the folded groups, the
  scroll-to-card behaviour after a group opens, and the first-login sequence (nudge, then
  interview) end to end with the new layout.
- **Risk to watch after deploy:** optional cards (voice recordings, CRM, automation, second
  creative tool) now sit behind "More connections". Every in-app path to them opens the
  group first, but a client hunting for one by eye needs one extra tap.
- **Migration:** none. **Deploy:** needed.
- **Estimated cost so far:** about $35 (rough). Cap proposed: $40.
