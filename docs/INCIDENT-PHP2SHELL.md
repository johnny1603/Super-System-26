# Incident: PHP2Shell (CVE-2026-109618, CVE-2026-109619)

Recorded 2026-10-24. Defensive response only: **no exploit or probe request is ever sent to
any site** — see "The check" below for exactly what is sent.

## What

Two critical flaws in the **PHP runtime itself**: remote code execution, and memory
disclosure that can expose data from *other* requests. Fixed upstream in **8.5.12, 8.4.27,
8.3.36, 8.2.35**. **PHP 8.1 and older get no official release** — only unreleased source
backports, and distro/vendor builds that may carry them. Exploitation attempts reported since
2026-10-21. Updating WordPress, plugins or themes does **not** fix it; only updating PHP does.
The fixed versions live in one place: `core/php_advisory.py`.

Why it matters to us beyond "the client's site could be taken over": every request we make
to a client's WordPress REST API carries our Application Password in a Basic-auth header. A
memory-disclosure bug on that server can leak it. Hence the password rotation below.

## Who is affected

**Not our platform.** Checked 2026-10-24, from the repo:

| Where | What runs | PHP? |
|---|---|---|
| `Dockerfile` (service `super-system`) | `python:3.12-slim`, `pip install -r requirements.txt`, `uvicorn` | No |
| `requirements.txt` | anthropic, fastapi, uvicorn, httpx, python-dotenv, supabase, google-cloud-pubsub, google-auth, requests, pydantic, pillow, cryptography | No |
| `proxy/Dockerfile` (`app.uallak.com` proxy) | `python:3.12-slim`, httpx + uvicorn + starlette | No |
| `Procfile` | `uvicorn core.api_server:app` | No |
| Sidecars | none defined in the repo (no service YAML, no compose file); deploy is `gcloud run deploy --source .` = one container | No |
| Repo files | no `.php`, `.phar`, `composer.json/lock`; the only "php" strings are WordPress URLs we *call* (`authorize-application.php`, `post.php`) | No |

The repo can't prove what the deployed revision is. To check that it is one container (no
sidecar was ever added by hand), run in Cloud Shell:
`gcloud run services describe super-system --region me-west1 --format="value(spec.template.spec.containers[].image)"`
It should print one image.

**Affected: the WordPress sites we build and connect to** (PHP runs on their hosts):

- **InstaWP sites we provisioned** (`website_agent.provision_site`) — we are the host's
  customer, so updating PHP is our job.
- **Client sites connected to us** (client_accounts `wordpress`, one-click or manual connect)
  — the client's hosting provider must update PHP. We hold an Application Password for these.
- **Existing sites a client asked us to assess or migrate** (`website_existing_assessed` with
  is_wordpress, `website_migration_requested`) — we hold nothing for these, but they are the
  client's site and the client should know.

The live list is in the Control Room (I can't read the production DB): **חדר בקרה → 🛡️
PHP2Shell exposure** shows every known site with client, hosting origin, whether we hold an
Application Password, and its last check. `GET /api/admin/php-exposure` returns the same JSON.

## What we did

1. **Inventory + exposure check** — `core/php_exposure.py`.
   - `inventory()` merges the three sources above, one row per (client, site); it never
     reads a secret, only whether one is stored (`has_app_password`).
   - **The check**: ONE ordinary anonymous `HEAD /` of the homepage (a plain `GET /` only when
     HEAD is refused or shows no PHP header), our usual `uallak-site-check/1.0` user agent, no
     query string, no body, no credentials, no other path. Only the `X-Powered-By` and `Server`
     response headers are read (redirect hops included). The `_fetch` seam refuses any method
     but HEAD/GET, and tests prove nothing else leaves the process.
   - Classification against `core/php_advisory.py`:
     - `patched` — the site **reports** a fixed release. A header is the host's own claim, not
       a verification; nothing ever calls a site "safe".
     - `vulnerable` — an upstream build older than the fix, or 8.1 and older.
     - `unknown` — no version shown (the common case: most hosts hide it), unreachable, hidden
       patch level, a pre-release (`8.3.36RC1`, `-dev`), conflicting headers, a branch the
       advisory doesn't list, or a **distro/vendor build** of an older release
       (`8.1.2-1ubuntu2.20`, `8.2.7-1+deb12u3`), which may carry a backport. Ask the host.
   - Stored per client in `app_settings` `php_exposure:<id>` (cleared by client deletion,
     guarded by the deletion guard, hidden from global settings).
   - **One admin alert per site per status change**, durable across restarts
     (`alerted_status` in the record); a first check that finds `patched` is quiet. Wording
     starts "client {id} (...)" so the Control Room matches it to the client.
   - Run it: the **"בדוק עכשיו"** button (all sites, or one client from the client's page),
     `POST /api/admin/php-exposure/scan[?client_id=N]` (admin session), or
     `GET /api/website/php-exposure-scan` with X-Admin-Key (for a scheduler, same as
     `/api/website/scan`). Nothing runs it automatically.

2. **Control Room panel** (admin only): the summary on the list page (counts + every site),
   and a per-client panel on the client page. Non-patched sites also appear in "דורש את
   תשומת הלב שלך".

3. **Credential hygiene** — admin action `rotate_wordpress_password` (core/agent_actions,
   approver admin, never client-requestable). Proposed from the panel's button (or the
   Control Room chat), it runs only on the card's **"אשר והרץ"** click.
   `website_agent.rotate_stored_password` reuses one-click connect's mint/delete code
   (`_mint_password` / `_revoke_password`), in the order that can't strand the connection:
   mint a new password → check it works → store it encrypted (`upsert_account`) → then
   delete the old one on the site. If anything fails before the store, the stored password
   is unchanged (and an unusable new one is deleted). The **audit trail** is the
   `agent_actions` row (who approved, when, outcome) plus a structured log line; the record
   gets `password_rotated_at`. Neither password is logged, stored in the row, or returned.
   If the old password could not be deleted, the result says so: delete the older "uallak"
   entry in wp-admin → Users → Profile → Application Passwords.
   **Rotate every connected site** (they all ran a pre-fix PHP until at least
   2026-10-21) — **after** the host has updated PHP; a password rotated on a
   still-vulnerable server can leak the same way.

4. **Agents** — `client_state.site_security` (from the stored record only — no request to
   the site during a chat): per site `php_status` (patched / vulnerable / unknown /
   not_checked), `php_version_reported`, `last_check`, `hosted_by`, and
   `verified_safe: false` always. `ACCOUNT_STATE_RULE` (support_agent) tells every persona:
   never "safe/secure/protected"; for anything but `patched`, say plainly that the hosting
   provider has to update PHP and that we can help (for our InstaWP sites: it is our job).

## InstaWP — how our sites get their PHP version (manual runbook)

`instawp_service.create_site_from_template` sends only `template_slug`, `is_reserved` and
`site_name`. So a new site gets **whatever PHP InstaWP applies to template clones** (the
template's setting or InstaWP's default) — the code never chooses one.

Whether the API can read or set it: InstaWP's documented v2 API (base
`https://app.instawp.io/api/v2`) shows no PHP-version route. Their **CLI** has
`instawp sites php <site>` (view/update) and `instawp create --php <version>`, so a route
exists behind it, but it is undocumented, and its options list major.minor only (7.4–8.3; 8.4
announced separately), which says nothing about the patch level. **We don't guess an
endpoint**, so no API action was added. Do this by hand:

1. In the Control Room run **בדוק עכשיו**. Note every `instawp` site and its status.
2. Ask InstaWP support (or check their status page/changelog) **which exact PHP patch releases
   their 8.2 / 8.3 / 8.4 options run now** and whether the PHP2Shell fixes are deployed. Picking
   "8.3" in a dropdown proves nothing until they confirm it is ≥ 8.3.36.
3. For each InstaWP site: InstaWP dashboard → Sites → the site → PHP settings
   (Edit site / PHP config) → pick a branch InstaWP confirmed as fixed. Never 8.1 or older.
4. Do the same on the **master template** (`WEBSITE_TEMPLATE_SLUG`), so new clones start on it.
5. Re-run **בדוק עכשיו**. InstaWP may hide the version (`unknown`): then InstaWP's own
   confirmation from step 2 is the evidence. Note it, with the date, in the client's
   Control Room chat.
6. Rotate each site's stored password (the panel button → approve the card).

If InstaWP later documents the route, it becomes one more `ACTIONS` entry
(propose → approve → execute) calling a new `instawp_service` function. No other change needed.

## Client-hosted sites — what to tell the host

"Please update PHP on our site's server to 8.2.35, 8.3.36, 8.4.27 or 8.5.12 or later (the
PHP2Shell fixes, CVE-2026-109618 / CVE-2026-109619). If the server runs PHP 8.1 or older,
please move it to a supported version, or confirm in writing that your build includes the
vendor backport." Then rotate the stored password.

## Tests

`tests/test_php_exposure.py` (stdlib, runnable with the embeddable Python): version
comparison (fixed / one-below / numeric-not-text / 8.1-and-older / vendor builds /
pre-releases that *look* patched / hidden patch level / unknown branches / conflicting
headers), no payloads sent (recorded HTTP calls are only HEAD/GET of the homepage with no body
or params, the seam refuses other methods, the source has exactly one httpx call), alert dedupe
(per status change, across restarts, per site, quiet first `patched`), the record key cleared
on deletion, the agent fact never `verified_safe`, the action's guards (admin only,
WordPress connection required, one run per click, audit row), and rotation order
(store before revoke, an unverified password never stored, no password in logs).
