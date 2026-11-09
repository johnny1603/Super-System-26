# Dashboard warm-up (2026-11-10)

The client should never wait for a cold start when opening the dashboard or returning to it.
The warm-up works only through pings from the page itself.

The owner's rules:
- no min instances;
- no `--no-cpu-throttling`;
- no change to any Cloud Run setting;
- no scheduler job.

## What runs

| Where | What |
|---|---|
| `GET /api/warmup/` (`core/warmup.py`, route in `core/api_server.py`) | Public. Returns `{"ok": true, "version": "<K_REVISION>"}` immediately. It touches no Supabase, no Drive and no model, and sends `Cache-Control: no-store`. Rate limit: 30 per IP per minute, kept in memory; above that it returns 429. |
| `GET /api/warmup/config` | Called once per page load. Returns the ping interval from the admin setting `warmup_interval_seconds` (default 240, clamped to 60–3600, read through the 60 s settings cache). Same rate limit. |
| `dashboard/assets/warmup.js` | Runs on the client dashboard and on every admin page (`/admin/`, operator, landing, connections, models). |

## How the page behaves

**Pinging**
- It pings at once on load, before the client types anything.
- While the tab is visible (`document.visibilityState === 'visible'`), it pings every `warmup_interval_seconds`.
- The interval stops when the tab is hidden, and on `pagehide`.

**Coming back to the tab**
- It pings at once and restarts the interval.
- **Version check:** if the reported version differs from the one the page loaded with, the page reloads so the client gets the latest changes.
- **When it reloads:** only when the chat is idle. It never reloads while:
  - a text field is focused (the client is typing);
  - any `<textarea>` holds text (an unsent draft);
  - a chat upload, a voice upload or a message waiting for its reply is running (`window.__uallakBusy`, counted by `busyWhile(...)` in the client dashboard).

  While any of these holds, it checks again every 2 s.
- **Loop guard:** the page reloads at most once per new version per tab (`sessionStorage`). During a rollout, old and new instances answer side by side, and this guard stops the page from reloading over and over.

**Silence**
- No spinner and no message to the client.
- A failed ping is ignored, and the next interval tries again.
- Only `console.debug` logs.

## Version

`version` is Cloud Run's `K_REVISION` environment variable, which is the same on every instance of one deploy. Run locally, it is `"dev"`.

## Limits worth knowing

- **It only works while a page is open.** The first visitor after an idle period still meets a cold start: the login page doesn't load warmup.js.
  - That page could get the same `<script>` if wanted (one line).
- **It does not guarantee a warm instance.** A visible tab keeps the service awake. Cloud Run may still start a new instance under load.
  - It usually keeps one alive between pings, because the default 240 s is well under the idle window seen in practice.

## Tests

`tests/test_warmup.py`:
- The server checks: what the ping touches, the rate limit, the clamping of the setting, the route order, and that no scheduler job or always-on flag was added.
- The page checks run `warmup.js` in Node with a fake tab and fake timers: the ping on load, the interval, stopping while hidden, the ping on return, the reload once idle, never while busy, the loop guard, and silent failures.
