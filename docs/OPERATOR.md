# The operator — the owner's personal agent

`/admin/operator/` (admin session only; linked from the admin menu as "המפעיל"). One place
to say "something is wrong" and have it investigated, written up, fixed and shipped.
Built 2026-11-01. Phone-first page; the API under `/api/admin/operator/*` is plain JSON so a
phone app can use it later (it will need its own auth — see "Recommended next").

## The three tiers — never blurred

| Tier | What | Who triggers | Approval | Code |
|---|---|---|---|---|
| 1 — reads | Cloud Run revisions + deploy times, logs (redacted), alerts, `client_state`, Control Room, git history, CI results, the deployed code | the agent, freely | none | `core/operator_agent.py` (read tools), `core/cloud_ops.py`, `core/operator_github.py` |
| 2 — safe, reversible | health check, token refresh, cache clear (the agent may run these); **rollback / return to latest, flag flips, re-send a failed WhatsApp** (agent only OFFERS a one-tap button) | owner tap (or agent for the diagnostic three) | none — runs at once, audited first | `core/operator_actions.py`, `core/operator_flags.py` |
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
- Cache clear is per instance; other instances expire theirs by TTL.
- `running_commit` is known only for deploys made by the workflow (`BUILD_COMMIT` file);
  manual Cloud Shell deploys show "unknown".
- Nothing here has run against live GCP/GitHub yet — the API shapes are from the docs; the
  likeliest first-run fix is an IAM permission the script missed.
