# TikTok uploads — size policy and the durable job (2026-11-10)

Owner's policy (Johnny). Code: `core/video_limits.py`, `core/tiktok_uploads.py`,
`core/agent_actions.py` (`send_tiktok_to_inbox`), `agents/tiktok_content_agent.py`
(`begin_upload`, `delivery_status`), `core/tiktok_service.py` (`upload_chunk`).
Tests: `tests/test_tiktok_uploads.py`, `tests/test_tiktok_inbox_action.py`.

## The policy

| File size | What happens |
|---|---|
| up to `tiktok_auto_upload_max_mb` (admin setting, default **1536** = 1.5 GB) | The client's card. One click → the upload runs. No size question, no person. |
| above it | **No card, no question to the client, no refusal.** ONE approval request to the owner: an item in the admin bell (`dashboard/assets/frustration-widget.js`) and ONE email to `ADMIN_EMAIL` with the client, file, size and **Approve / Decline** buttons. The client reads only "אני מטפל בזה, אעדכן כשמוכן." |
| owner approves | The upload runs with **no further cap or question**. The approval is final. The done line is posted in the client's media chat. |
| owner declines | The client is told plainly what was decided and the next step (save a shorter or compressed version and tell the agent). |

- There is no 512 MB cap any more. Nothing checks TikTok's own limits (4 GB, 10 minutes, resolution, fps) in a blocking way.
- Only a file TikTok cannot decode at all is refused, with its own plain line (`bad_format`): a container TikTok doesn't take, no video track, or an unplayable codec.
- Everything else from the probe is logged as `probe_warning` and sent anyway.
- A rejection by TikTok at runtime is handled on our side: one admin alert. The client gets a calm line, never the error.
- The setting is in the admin Settings screen ("טיקטוק — סף העלאה אוטומטית").
- The email's buttons only **open** `/admin/?tiktok_upload=<client>:<job>`, where the bell opens focused on that item. The decision itself is a POST behind the admin session (`/api/admin/tiktok-uploads/{client}/{job}/approve|decline`), so a link scanner can never approve anything.

## Why a durable job

- Cloud Run: 512Mi, request-based CPU, ~300 s request timeout (the deploy sets no `--timeout`; the app.uallak.com proxy reads for 290 s).
- A big upload cannot live inside one request.
- A FastAPI background task after the response gets almost no CPU and can die with the instance.
- **No `--no-cpu-throttling` and no min instances** (owner's rule) — the design doesn't need them.

So the upload is a sequence of steps, each one **inside an active request**:

- **Scheduler job `tiktok-uploads`**: every minute, `GET /api/tiktok/uploads/run`, X-Admin-Key, in `scripts/schedule_jobs.sh`. Each run gets a budget of up to `TICK_BUDGET_S` (200 s). A minute with nothing due returns immediately.
- **The card's poll** (`agent_actions.status_card`): a short nudge (20 s) while the client watches.
- **The click** only creates the job, inside the click's request (`inline_start`). It never starts a background task.

## How a job runs

**Storage**
- One `app_settings` row per client: `tiktok_upload:<id>` = `{"jobs": [...]}`.
- No migration is needed.
- While a session is open, the row holds TikTok's `upload_url`. It is never logged, never shown to the admin screen, and dropped once sent. No token is ever stored.

**Lease**
- `tiktok_upload_lock:<id>`. An insert takes it (the primary key makes that atomic), and it expires after `LEASE_S`.
- Only one worker runs per client at a time. At most 2 clients run in parallel per instance.

**Steps**

| Status | What the step does |
|---|---|
| `queued` | `begin_upload` runs: grant → fresh token → Drive size → header probe → TikTok init with the chunk plan. |
| `uploading` | Chunks are sent from `next` until the budget ends. `next` is saved after **every** chunk, so the next request resumes exactly there. |
| `processing` | `delivery_status` is polled until `SEND_TO_USER_INBOX`. |
| `done` | The done line is posted. With a card, it goes through `agent_actions.finish_deferred`; for an owner-approved file, straight into the media chat. |

**Chunks**
- TikTok's FILE_UPLOAD rules (`video_limits.tiktok_chunk_plan`) are applied only so the chunking itself is valid.
- Chunks are 16 MB. They grow, up to 64 MB, only if 16 MB would need more than 1000 chunks.
- Files under 16 MB go whole. The last chunk takes the remainder.
- One chunk is in memory at a time, read from Drive by range. Memory stays flat for any size.

**Retries**
- Each chunk is retried 3 times on 5xx, 429 or a dropped connection (`upload_chunk`).
- If a step still fails, it is retried later with a backoff (30 s, 2 min, 5 min, 10 min), up to `MAX_ATTEMPTS`.
- After that, a broken session starts over with a new init (`MAX_RESTARTS`).
- When everything is exhausted: one admin alert, the job is `failed`, and the client gets a calm "still on it" line (refusal `in_hand`).

**Other limits**
- TikTok's `upload_url` lives 1 hour. A session older than 50 minutes starts over.
- A card's job ends itself after 55 minutes. The card's reaper window is 65 minutes (`slow` = 30 min), so the reaper never races a live job.

**Progress**
- For files of 256 MB and up: "\"file\" בדרך — 25% כבר הועלו." at 25, 50 and 75%, once each.
- The lines carry no tool names and no spinner.

**Explained stops**
- `needs_reconnect` (plus the connect card), `pending_limit` and `bad_format` keep their own plain lines.

## Admin

| Route | What it does |
|---|---|
| `GET /api/admin/tiktok-uploads?status=awaiting_admin` | The bell's list. Other statuses: `open`, `all`, `failed`, `done`, `declined`. |
| `POST /api/admin/tiktok-uploads/{client_id}/{job_id}/approve` or `/decline` | The decision. Returns 409 if the job was already decided. |
| `GET /api/tiktok/uploads/run` | The scheduler's runner (X-Admin-Key). |

`client_deletion` clears both keys, and `admin_service.get_settings` skips them.

## Not tested (as of 2026-11-10)

- Against real TikTok or real Drive: everything is stubbed in the tests.
- A real multi-GB upload's memory profile on Cloud Run.
- How the email renders in Gmail.
- The live Cloud Run request timeout. Check it with:
  `gcloud run services describe super-system --region me-west1 --format='value(spec.template.spec.timeoutSeconds)'`
  The design assumes at least 240 s. The default is 300 s.
