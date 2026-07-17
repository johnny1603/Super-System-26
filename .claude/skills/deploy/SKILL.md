---
name: deploy
description: How to commit, push, and deploy uallak's Super-System-26 to Cloud Run. Use whenever a change needs to be shipped, and BEFORE suggesting any git, gcloud, or python command on this machine.
---

# Deploying uallak (Super-System-26)

## The one rule

This Windows machine has **no git, no gcloud, and no usable Python** on PATH (only the
Microsoft Store stub). Never run `gcloud`, `py`, or `python` locally — it will fail.
Don't rediscover this by trial and error.

**Git exception:** GitHub Desktop's bundled git works, credentials included:
`C:\Users\johni\AppData\Local\GitHubDesktop\app-3.6.2\resources\app\git\cmd\git.exe`
(the `app-*` version segment changes on GitHub Desktop updates — glob for it if that path 404s).
Default remains user-reviewed commit/push via GitHub Desktop; only commit/push directly with
the bundled git when the user explicitly asks for it.

## Workflow — three hops, two of them done by the user

1. **Edit locally** — Claude Code edits files in the working copy at
   `C:\Users\johni\OneDrive\מסמכים\GitHub\Super-System-26`.
2. **Commit + push via GitHub Desktop** — the *user* does this. Repo:
   `johnny1603/Super-System-26` (private). Ask them to review the diff and push;
   you cannot commit or push for them.
3. **Deploy via Google Cloud Shell** (browser) — the *user* runs gcloud there, never
   locally. Typical flow: pull latest in Cloud Shell, then
   `gcloud run deploy super-system --source . --region me-west1`
   (project `super-system-500410`).

## Cloud Run service facts

- Service `super-system`, region `me-west1` (Tel Aviv), project `super-system-500410`
- Container: Dockerfile → `uvicorn core.api_server:app` on port 8080
- Cloud Scheduler hits `GET /api/monitor/scan` twice a day — after API-level changes,
  confirm that route still works
- `uallak.com` is **not connected**: `me-west1` doesn't support direct
  `gcloud run domain-mappings`; connecting it needs an HTTPS Load Balancer or a region move

## Required env vars on the service

`ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `PAYPAL_CLIENT_ID`,
`PAYPAL_CLIENT_SECRET`, `SESSION_SECRET_KEY`, `GMAIL_APP_PASSWORD`
(optional: `GMAIL_USER`, `ADMIN_EMAIL`, `PAYPAL_WEBHOOK_ID`, `PUBLIC_APP_URL`;
`GOOGLE_SERVICE_ACCOUNT_JSON` + `DRIVE_ARCHIVE_FOLDER_ID` for the offboarded-client
Drive archive — without them, closure/transfer still works but records stay in the
DB and an alert asks for a manual archive retry).
`validate_keys()` prints a `⚠️ Missing keys` warning at startup — check the Cloud Run logs
right after a deploy.

## Testing reality

Nothing runs locally (no Python), so changes cannot be executed before deploy. Verify by
careful reading, then smoke-test the deployed URL: `/health` first, then the flow you touched.

## Known deploy gotchas

- **PayPal `401 invalid_client` is not necessarily a code regression** — a stale/deleted
  Sandbox app on PayPal's own side caused exactly this once. Check the PayPal developer
  dashboard app status before debugging code.
- **PayPal is Sandbox-only**: `BASE_URL` is hardcoded to `api-m.sandbox.paypal.com` in
  `core/paypal_service.py`. Going live requires Live credentials AND changing that constant.
- **`data/` inside the container is ephemeral** — every deploy/restart wipes
  `alert_history.json`, `monitor_memory.json`, `agents_status.json`, `agent_proposals.json`.
- **Static mount order in `core/api_server.py`**: the root `/` mount must stay the LAST
  registration in the file, or it swallows `/api/*` and every other route (this caused a
  real outage once).
