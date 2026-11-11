# איציק (Itzik) — the owner's personal operator agent

The owner sees him as **איציק** (`operator_agent.DISPLAY_NAME`): admin menu, page title,
chat header, the approval-code email, PR titles and the audit-log display. Internal names
and routes stay `operator`. One place to say "something is wrong" and have it investigated,
written up, fixed and shipped.

**Where he lives (2026-11-01):** a floating chat on EVERY admin page
(`dashboard/assets/itzik-chat.js`, self-contained, reused later by the mobile app) —
minimised by default as his standing figure in the corner, portrait in the open header,
the same look as the client agent chats. It draws nothing until
`GET /api/admin/operator/presence` answers with the admin session, and only admin pages
load it. Action cards and the Tier 3 approval cards (with the emailed code) appear inside
the chat. `/admin/operator/` is his workspace: production status + one-tap rollback,
approvals waiting, saved reports, the action log, flags.

**Presence** (`core/operator_presence.py`): a badge on the minimised figure for new open
alerts since the chat was last opened, a pending approval, or a post-deploy problem. When
the new deploy looks wrong (`cloud_ops.deploy_watch().suspicious`), ONE proactive line with
the one-tap rollback — at most once per browser session per revision pair.

**Persona** (in `SYSTEM`, shared by any model): a man in his 30s, hi-tech background, calm,
sharp, concise, dry humour, first person as a partner; what he found → the evidence → what
he recommends; owner-only steps as exact numbered steps. Model: routed per message (below);
code / deploy / any change always runs on the locked top tier,
`model_routing.locked_model("operator")`.

## Routing, budget, stop (2026-11-10)

**Routing by request type** (`operator_agent.classify`, rules only, no model call). Doubt
always goes UP:

| kind | when | model (model_routing.TIER_MODELS) | loaded |
|---|---|---|---|
| `simple` | thanks / ok / 👍 (an "ok" after he offered something counts as `write`) | simple (haiku) | `QUICK_SYSTEM`, last 4 rows, **no tools** |
| `status` | ≤ 60 chars, "מה המצב / הכל תקין / יש התראות", no number, no "למה" | simple | `QUICK_SYSTEM` + 3 reads (production status, alerts, stuck cards), ≤ 3 steps |
| `read` | diagnose words (בדוק, למה, לוג, שגיאה, לקוח N, why, check…) | standard (sonnet) | full `SYSTEM` + `READ_NOTE`, every tool **except** `propose_code_change` / `run_reaper` |
| `write` | any change / code / deploy / rollback word, a "yes", > 300 chars, ≥ 3 questions, **anything unclear** | heavy, locked | everything, exactly as before |

Permissions are unchanged: each kind gets a SUBSET of the same `TOOLS`; a tool outside the
kind's subset is refused in the loop. An empty answer from a cheap kind is retried once as
`read` (up, never down).

**Per-task budget.** Admin setting `operator_task_budget_usd` (default 1.5, clamped 0.05–50,
"הגדרות" in /admin/). Before every model call the loop estimates that call (the last call +
the new tool results as uncached input; the first call from the prompt size). If this step
plus a summary would pass the cap, it stops: ONE summary call (no tools, ≤ 700 tokens) if it
still fits, else a code-built line, then ONE card "הגעתי לתקרת המשימה: עלות עד עכשיו $X …
להמשיך?". The paused conversation is saved in app_settings `operator_task:<id>` (pruned after
2 days); the card's "המשך" (`POST /ask` with `continue_task`) resumes it with a NEW cap-sized
slice. Only a `paused` task resumes, once. No tool name or error text is shown.

**Stop.** "⏹ עצור" in the chat while he works, or a message that is ONLY a stop word (`STOP_RX`:
עצור / חכה / רגע / תפסיק / stop / wait…, same list in the browser) → `POST /api/admin/operator/stop`.
The signal is checked before every model call and before every tool — never inside one. The
reply is one line: "עצרתי. בוצע: … לא בוצע: …" (plain labels, `TOOL_LABELS_HE`). An irreversible
tool that started (`IRREVERSIBLE_TOOLS`: the reaper) finishes and the line says "הושלם עד הסוף".
The loop itself never deploys, writes to GitHub or sends email; those are the owner-approved
routes (code email, open PR, merge+deploy), which are marked with `irreversible_step` while
they run: a stop never interrupts them and the line says "כבר התחיל ומסתיים עד הסוף". The
signal: a counter on this instance + the app_settings row `operator_stop:signal` for other
instances (a stop older than the task never touches it). The conversation stays open.

**Measurement.** Every reply row's `meta.metrics` (no migration — `operator_messages.meta`
already exists): kind, reason, model, seconds, USD, calls, outcome (done / stopped / paused /
failed / quick), task id, cap. Cost rows in `client_costs` (`claude_operator`, INTERNAL) carry
`details.operator_task` + `operator_kind`. `GET /api/admin/operator/metrics` → the "מדידה"
card on /admin/operator/ (per-kind averages and maxima + the latest 40). Tune the cap from it.
No Cloud Run setting changed (no min instances, no --no-cpu-throttling).
Tests: `tests/test_operator_budget.py`.

## Reply text — the findings are always in the message (2026-11-11)

**The bug.** The owner asked twice and both times got only "סיימתי לבדוק — הפרטים בכרטיסים למטה",
with no card under it.
- When the last model step came back with no text, `ask` replaced the empty text with that fixed
  line. It did so whether or not a card existed. An empty step can be thinking only, or the
  findings may have been written next to a tool call such as `write_report`.
- The line was then stored as Itzik's own answer and fed back as history, so the next run repeated it.

**Now** (`compose_reply`, `strip_card_pointers`, `_Task._findings` in `core/operator_agent.py`):
- **Tools ran but the final text is empty:** ONE more call with no tools asks for the findings in
  the message itself. It runs only if it fits under the task cap.
- **Still empty:** the text is built from what is there, in this order:
  1. the report's own content;
  2. the text the model wrote next to its tool calls;
  3. each card's content;
  4. an honest line that says what was checked and that no card is attached.
- **Card types:** a card whose type is not one the chat draws (`RENDERED_CARD_TYPES`), or whose
  payload is broken, goes into the text and is not sent as a card. The chat (`itzik-chat.js`
  `renderCard`) also draws any card it cannot render as plain text, so a card never vanishes silently.
- **Pointers:** a sentence like "the details are in the card(s) below" is removed unless a card the
  chat can draw is attached. Old replies like that are no longer fed back as history.
- **The prompt:** it now says the report is a saved copy; the final message states the findings itself.

Tests: `tests/test_operator_reply.py`. It replays the exact case: two asks, an empty final step,
and the old line in history.

The rest of this page says "the operator" for the same agent.
Built 2026-11-01. Phone-first page; the API under `/api/admin/operator/*` is plain JSON so a
phone app can use it later (it will need its own auth — see "Recommended next").

## The three tiers — never blurred

| Tier | What | Who triggers | Approval | Code |
|---|---|---|---|---|
| 1 — reads | Cloud Run revisions + deploy times, logs (redacted), alerts, `client_state`, Control Room, git history, CI results, the deployed code, **clients' action cards** (`list_action_cards` by client/status/age, `action_card` with params + the alerts carrying its error, `stuck_actions`), **landing pages with versions** (`landing_pages`) | the agent, freely | none | `core/operator_agent.py` (read tools), `core/agent_actions.operator_*`, `core/cloud_ops.py`, `core/operator_github.py` |
| 2 — safe, reversible | health check, token refresh, cache clear, `run_reaper` (the agent may run these); **rollback / return to latest, flag flips, re-send a failed WhatsApp, `fail_action` (end a STUCK running card with a reason), `retry_action` (re-offer a failed/expired/cancelled card to the CLIENT as a new card), `resolve_alert`, `mark_request_handled` (a team request → "טופל")** (agent only OFFERS a one-tap button) | owner tap (or agent for the diagnostic four) | none — runs at once, audited first | `core/operator_actions.py`, `core/operator_flags.py` |
| 3 — code + deploy | the agent proposes edits → **approval 1** opens its own `operator/<id>` branch + PR → **approval 2** merges (bound to the exact head SHA) and starts the deploy workflow | owner only | each approval = a one-time code emailed to `OPERATOR_APPROVAL_EMAIL` | `core/operator_changes.py`, `core/operator_gate.py`, `.github/workflows/deploy.yml` |

Never: a push to master, a deploy without approval 2, a change to `.github/`, the gate, the
change lifecycle, sessions, the vault, the keys registry, env/secret files, or any text with
`SECRETS_ENCRYPTION_KEY` / a secret-looking value (`operator_changes.PROTECTED*`). The agent
never sees env values.

**Audit:** every Tier 2 action and Tier 3 step writes an `alerts` row with source `operator`
BEFORE it has any effect (who = admin session fingerprint + IP, what, when) and another with
the result. No audit row → nothing runs. The source is protected from bulk delete and the
Google-data purge, like the other audit sources.

**Rollback:** traffic-only PATCH (no new revision, template and env untouched) to the newest
READY revision older than the serving one; "return to latest" undoes it. The page shows the
button at all times and a red banner when a deploy < 3 h old has ≥ 5 ERROR log entries.
⚠ After a rollback traffic is *pinned*: a manual `gcloud run deploy` will NOT receive traffic
until you tap "חזרה לגרסה האחרונה" (or add `--to-latest`). The deploy workflow always ends with
`update-traffic --to-latest`.

**Flags:** an override row in `app_settings` (`flag_override:<NAME>`) wins over the env value; no
redeploy. The round-2 Google flags can be switched OFF here (kill switch) but never ON — ON
requests OAuth scopes Google has not approved; that stays the owner's step.

**Re-send:** only failed urgent WhatsApp notifications (the one notification whose failure is
recorded). Rows from before 2026-11-01 kept only 200 characters and are not re-sent. Failed
emails are not recorded anywhere today, so they can't be re-sent (deliberately — a login code
must never be re-sent by an admin).

## Clients' action cards — stuck, failed, re-offered (2026-11-06)

Every approved action runs under a **hard deadline** (`agent_actions.run_deadline`: a slow
action's `slow.minutes`, otherwise 4 minutes) in a worker thread; past it the row is ended as
`failed` / outcome `timeout`. The **reaper** (`agent_actions.reap_stuck`, Cloud Scheduler job
`action-reaper`, every 5 minutes, `GET /api/actions/reap`) ends any row still `running` past
`reap_after()` — the case where the process itself died (proposal 16, client 27, 2026-11-02: a
background task after the response, lost or throttled on Cloud Run). Both end a row the same way
(`finalize_stuck`): compare-and-set (once only), the alerts that name the proposal resolved, a
resolved history row, and the client told in chat what happened and the next step ("כתבו לי
'נסה שוב'") — never "the team". A result that arrives after that is never posted.

What איציק does with it: reads (`list_action_cards`, `action_card`, `stuck_actions`,
`landing_pages`), runs `run_reaper` himself, and OFFERS `fail_action`, `retry_action`,
`resolve_alert`, `mark_request_handled` as one-tap buttons. **Hard line:** none of these
approves, publishes or executes a client's proposal — `retry_action` creates a new card the
CLIENT approves. Code, PR and deploy stay Tier 3 (one-time codes + the GitHub reviewer).
He never advises routing a client's ask to a manual team request: team requests are only
legal / tax / contract / billing / package exception; anything else the chat can't do is a
`capability_gap` alert — a product fix (Tier 3), not a person's job.
## Security — the second gate (implemented)

The admin dashboard is one shared password (`ADMIN_PASSWORD`). With it alone you can read,
and do Tier 2 (reversible) — but you can **never ship code**:

- Each Tier 3 step needs a 6-digit code emailed to `OPERATOR_APPROVAL_EMAIL` (set it to your
  own mailbox, NOT `johnny_support@` which the app sends from).
- The code is bound to that exact step + change (+ PR head SHA for the merge) + the admin
  session that asked; 10 minutes; 5 wrong tries burn it; single-use (consumed by a delete).
- The email names exactly what it approves; a code you didn't ask for = someone has the admin
  password → rotate `ADMIN_PASSWORD`.
- The check lives inside `operator_changes` (a protected file), not in the route.
- Tier 3 is OFF unless `OPERATOR_CODE_ENABLED=1` + the approval email + mail credentials exist.
- The GitHub token has no Workflows permission; WIF trusts only `deploy.yml` on master.

## Recommended beyond it

1. **GitHub environment `production` → Required reviewers → you.** Every deploy then also needs
   your tap in the GitHub app (your GitHub account + its 2FA) — a third, independent factor.
2. **Branch protection on master:** require the "Tests" check and a PR — so even a leaked PAT
   with Contents:write can't push to master directly.
3. **Replace the shared admin password** with Google sign-in for the admin (your account only)
   or Cloud IAP in front of `/admin` — the password is today's weakest link.
4. **A short admin session** (today 7 days) and a "log out everywhere" switch.
5. For the phone app: a per-device token (revocable) instead of the cookie, plus the same
   emailed code (or a TOTP app) for Tier 3.

## Setup (once)

1. Supabase SQL editor: `migrations/2026-11-01-operator.sql` (conversation memory + reports).
2. GitHub → Settings → Developer settings → Fine-grained token → only this repo:
   Contents R/W, Pull requests R/W, Actions R/W, Metadata R, and READ-ONLY Variables +
   Environments (for the "בדיקת חיבור GitHub" check). **Not** Workflows, **not** Administration.
3. Cloud Shell: `git pull && bash scripts/setup_operator.sh` — grants the service its reads +
   rollback role, creates the deployer SA + WIF, and (when you answer y) stores the token in
   Secret Manager and sets `OPERATOR_APPROVAL_EMAIL` + `OPERATOR_CODE_ENABLED=1`.
4. GitHub → Settings → Secrets and variables → Actions → **Variables** (repository variables,
   not secrets — neither value is secret). `bash scripts/print_github_variables.sh` prints both:

   | Name | Value |
   |---|---|
   | `GCP_WIF_PROVIDER` | `projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-actions` |
   | `GCP_DEPLOY_SA` | `github-deployer@super-system-500410.iam.gserviceaccount.com` |
5. Recommended #1 and #2 above.
6. Check: the operator page → "בדיקת חיבור GitHub" (Tier 1, `GET /api/admin/operator/github-check`,
   also the agent's `github_check` tool). Plain Hebrew lines: token valid, repo reachable, may
   create a branch / open a PR / start the deploy — each tested WITHOUT doing it (a commit that
   cannot exist; a branch confirmed absent first, so GitHub answers 422 when permitted, 403 when
   not) — the deploy workflow, both Variables (set / missing, never the value), the `production`
   environment, and as recommendations its required reviewer and master protection.
   Required reviewers can't be written in deploy.yml (it only names `environment: production`);
   they are a repo setting: Settings → Environments → production → Required reviewers.

Without steps 2–4 the operator still works for everything that reads the database (alerts,
clients, client state, code); Cloud Run/logs answer "no permission yet" and Tier 3 says it is off.

## Limits (honest)

- The agent turn is synchronous (plain `def`, ≤ ~200 s budget, ≤ 14 tool steps). If the phone
  drops, the answer is still stored — reload.
- A stop lands between steps: a model call already in flight (up to tens of seconds) finishes
  first. The budget check is an ESTIMATE made before each call; a call that costs far more than
  the previous one can overshoot the cap by that difference. The "in progress" note for an
  owner-approved PR / deploy is seen only by the instance running it.
- Routing, budget and stop have been tested with a scripted fake model only — never against
  the real API.
- Cache clear is per instance; other instances expire theirs by TTL.
- `running_commit` is known only for deploys made by the workflow (`BUILD_COMMIT` file);
  manual Cloud Shell deploys show "unknown".
- Nothing here has run against live GCP/GitHub yet — the API shapes are from the docs; the
  likeliest first-run fix is an IAM permission the script missed.
