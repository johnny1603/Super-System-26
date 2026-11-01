# Plugin and theme updates (website agent) — 2026-11-02

Code: `core/site_updates.py` (engine), `core/instawp_service.py` (host calls),
`core/agent_actions.py` (`update_site_items`, `update_site_items_without_backup`),
`core/client_state.py` (`site_updates` section), `GET /api/admin/site-updates/{client_id}`.
Tests: `tests/test_site_updates.py`.

**Status: never run against a real site or a real InstaWP account.** See "First live run".

## What the client sees

The website agent reads `account_state.site_updates` and, when updates are waiting on a site
we host, says the code-built line, e.g. "יש 3 עדכונים לתוספים, רוצה שאעדכן? אני אגבה לפני
ואבדוק אחרי." On yes it proposes `update_site_items`. The card lists every item with its
version from → to. One click approves it, and the card then shows progress (a slow action)
until the result arrives.

## Status view

| Field | Source |
|---|---|
| installed plugins/themes, active state, installed version | the site's own REST API (`wp/v2/plugins`, `wp/v2/themes`) |
| newest version, its required WordPress / PHP | public wordpress.org directory (`plugins/info/1.2`, `themes/info/1.2`), cached 12 h |
| site WordPress + PHP version | on a site we host: the host's WP-CLI (`wp eval` prints both). Elsewhere: the homepage generator tag and the PHP header from the last PHP2Shell check |
| flags | `php_incompatible`, `wp_incompatible` (never offered), `compat_unknown` (offered, flagged), `not_in_public_directory` (premium, never offered) |

The status is cached 6 h, both in-module and as the client_state section, and is forgotten after a run.
WordPress core shows only as a version, because it is never updated automatically.

## The backup — the real mechanism

WordPress's REST API has **no** update endpoint and **no** backup endpoint. On a site the
client hosts elsewhere we cannot back up or update without their server access. Those
sites get the status view only, and the agent offers a team request instead of a card.

On sites **we provisioned on InstaWP** (`website_provisioned` row with `site_id`, same URL as
the live connection), InstaWP's API has everything we need. The calls and bodies below come from InstaWP's own
open-source CLI (github.com/InstaWP/cli: `CLAUDE.md`, `src/commands/versions.ts`,
`src/lib/api-exec.ts`):

| Step | Call |
|---|---|
| backup = "site version" (files + database) | `POST /api/v2/site-versions {site_id}` → `{id, task_id}`, then poll `GET /tasks/{task_id}/status` until `completed` |
| verify the backup | the version id must appear in `GET /api/v2/site-versions?site_id=` |
| update one item | `POST /api/v2/sites/{id}/run-cmd {"commands": ["wp plugin update <slug> --no-color"], "timeout_seconds": 300}` |
| rollback | `PUT /api/v2/sites/{id}/restore-versions/{versionId}` → poll the task (overwrites files + DB in place) |

A backup counts only when its task completed **and** it is listed. Otherwise nothing is
updated, and the remaining items move to `update_site_items_without_backup`. Only code can open
that card (a model never can, and staff cannot decide it for the client). Its title is
"עדכון בלי גיבוי — רק באישור שלך", and nothing proceeds without that explicit click.

The fallback the brief named (a DB export plus a copy of the plugin folder) would run over the
same `run-cmd`. It is not built, because the site version already covers files + DB and can be
restored with one call.

## The run (one item at a time, stops at the first problem)

1. Re-read the item from the site: is it still installed, still behind, and still compatible?
2. Health check BEFORE: if the site is already failing, nothing is touched (`site_unhealthy`, admin alert).
3. Take a verified backup.
4. Run `wp plugin|theme update <slug>`. The command is built only from a slug matching
   `^[a-z0-9][a-z0-9_.-]{0,99}$`, and the only types are plugin and theme. There is no core path.
5. Health check AFTER:
   - the home page and up to 3 published pages (same host only) return 200 with no critical-error or fatal page;
   - the checkout page loads when WooCommerce is active;
   - `wp eval 'echo "ok";'` boots WordPress.
6. On failure:
   - restore that item's own backup, then re-check health;
   - alert admin (`rolled_back`, or URGENT `rollback_failed`);
   - plain Hebrew to the client, never the error.

   An update that changed nothing and broke nothing is reported as `not_updated`, with no restore.

Each item writes a `client_activity` row (`website_agent` / `site_update`) with these fields:
- `who`: the decider — client, admin or staff;
- `proposal_id`, `type`, `slug`, `name`;
- `from` and `to` versions;
- `backup_ref` (the site version id) and `with_backup`;
- `result`: updated / already_current / not_installed / incompatible:… / not_updated / backup_failed /
  rolled_back / rollback_failed / failed_no_backup / site_unhealthy_before.

## First live run (needs a real InstaWP site we host)

1. Deploy. Then, in the admin, call `GET /api/admin/site-updates/<client_id>?refresh=true` and
   compare the result with wp-admin → Updates.
2. In the client's website chat, ask about updates. Approve the card for ONE small plugin.
3. Check in InstaWP that a new site version exists, check the activity row, and confirm the site still works.
4. Only then trust rollback, by testing it on a disposable InstaWP site, never on a client's.

Unverified until that run:
- the exact `run-cmd` answer shape on our plan (three shapes are handled, as in the CLI);
- whether `site-versions` is included in our InstaWP plan;
- that WP-CLI's PHP matches the web PHP.
