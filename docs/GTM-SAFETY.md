# Tag Manager safety: preview, card-only publish, rollback (2026-11-11)

Code:
- `core/gtm_plan.py`: the naming rule and the stored plan's shape.
- `core/gtm_service.py`: `plan_entities`, `foreign_changes`, `publish_workspace` guard,
  `get_version`, `publish_version`, `ours_in`, `references_to_ours`, `remove_ours`.
- `agents/website_agent.py`: `_gtm_preflight`, `plan_lead_conversion`,
  `configure_lead_conversion`, `plan_revert_conversion`, `revert_conversion`,
  `gtm_history`, `gtm_rollback_point`.
- `core/agent_actions.py`: the `configure_conversion` and `revert_conversion` cards.
- `core/api_server.py`: `dry_run` on both configure routes, `/api/admin/gtm/*`.
- `dashboard/admin/connections/` (Tag Manager section) and the Control Room button.

Tests: `tests/test_gtm_safety.py`, plus the existing `test_google_scope_flows.py` and
`test_store_purchase.py`.

Nothing new is needed. There is no new scope, no Google Console change, no env var, no
Cloud Run change, and no migration: everything is stored as `client_activity` rows.

Why: the flow publishes to a LIVE site container, for example Shin Sekai `GTM-NZRNDQN`
"shin-se-kai.co.il 2023". Before this change there was no preview, no rollback, and the
card text was generic.

## 1. The dry run (`plan_lead_conversion`)

It runs the same guards as the real run, from one shared function, `_gtm_preflight`:
- conversion types;
- connection;
- scopes;
- dead grant;
- container mismatch;
- GA4 id;
- already live;
- store order page.

After the guards it only reads:
- `versions.live`;
- `workspaces.list`;
- `workspaces.getStatus` of our leftover workspace, if there is one.

The plan holds:
- `container`: the path, the GTM-XXXX id, the name and the account. These come from
  `gtm_connection.connection_view` (cached); the account id is taken from the path when
  no name is known.
- `workspace`: "uallak lead tracking", new or reused.
- `entities`: every trigger, tag and variable with `create`, `update` or `reuse`. They
  are computed against the live version with the same body builders and the same
  "already right?" matchers the write uses (`gtm_service.plan_entities`). The built-in
  Click URL switch is listed as `enable`.
- `skipped`: each wanted type that is already live, and the existing live tag names that
  caused the skip.
- `version_name`: "uallak conversion tracking".
- `rollback_point`: the live version id and name right now.
- `signature`: sha256 over the container, GA4 id, types, entities with their actions,
  and the rollback point.

**The card IS the plan.** `configure_conversion` gets a `prepare` hook that runs the dry
run, and the card's text (`what_for`) is the plan written out in he / en / fr / ar / ru.
It shows exact entity names, no tool names and no raw errors, and ends with one
"Approve?".
- **Refused dry run:** the chat shows the plain refusal line instead of a card. A grant
  problem shows the `connect_tag_manager` card instead.
- **Same plan again:** the same card is shown.
- **Different plan:** it supersedes the open card.

**Admin dry run:** `POST /api/website/configure-conversion` (X-Admin-Key) and
`POST /api/admin/control-room/{id}/configure-conversion` both accept
`{"dry_run": true}`. They return `{"plan": ...}` and write nothing.

## 2. The card is the client's only publish path

- **Card click.** `_run_configure_conversion` passes `expected_plan` (the stored plan)
  and `via="card"`. The run recomputes the plan. If the signature differs, it refuses
  with `gtm_plan_changed` before any write: the container or the live version moved since
  the card. The client is asked to request a fresh card. A card with no plan is refused
  the same way.
- **Admin routes still work** (`via="admin_key"` or `"control_room"`). They first log a
  `gtm_publish_without_card` row (container, types, plan signature, entities).
  - Both routes accept `expected_plan_signature`.
  - The Control Room button now runs the dry run first, shows the plan in its confirm
    dialog, and publishes only that plan.
- **Direct calls** from code or tests (`via="direct"`) are logged the same way.

## 3. Rollback

**Saving the rollback point.** The publish row (`website_conversion_configured`) now also
carries:
- `via`;
- `container_path`, `container_public_id`, `container_name`;
- `previous_version_id` and `previous_version_name`: what was live before, which is the
  rollback point;
- `new_version_id` and `new_version_name`;
- `entities` and `plan_signature`.

**Undo my tracking: the `revert_conversion` card.** It is one approval. Its dry run
(`plan_revert_conversion`) lists exactly what is removed. It then works one of two ways:

1. **republish.** This applies when the live version is exactly the one a logged publish
   made for this container, that publish saved a previous version, and every entity NOT
   ours is identical in both versions (kind, name, fingerprint).
   - The saved version is published again (`versions/{id}:publish`) and GTM edits
     nothing.
   - The card lists the uallak entities that disappear, and the ones that go back to
     their earlier form.
   - Undo again and the next older publish is undone in turn, because each publish row
     knows its own previous version.
2. **remove.** This applies to anything else: someone published after us, or nothing was
   live before.
   - Our own workspace, "uallak tracking removal", is created, based on the live version.
   - Only entities named "uallak — …" are deleted: tags first, then triggers, then
     variables. Each name is re-checked right before its delete.
   - It is published as "uallak tracking removed".
   - Refused (`gtm_revert_blocked`, plus an admin alert) when an entity that is not ours
     fires on or is blocked by our trigger, or reads a `{{uallak — …}}` variable.
     Deleting ours would break it.
   - Refused (`gtm_workspace_busy`) when no workspace of our own can be created. Deletions
     are never made inside someone's default workspace.
   - The built-in Click URL switch is left on. It is a GTM toggle, not a named entity,
     and another tag may use it.

**Logs.**
- Every undo writes `gtm_conversion_reverted`, holding: method, the version before and
  after, removed / restored, and the publish row it undid.
- Every publish writes `website_conversion_configured`.
- Every path that bypasses the card writes `gtm_publish_without_card`.
- The client's activity feed has labels for all three, in 5 languages.

**Admin, read-only.**
- `GET /api/admin/gtm/history/{client_id}`: publishes, undos and bypasses, newest first,
  plus the current rollback point.
- `GET /api/admin/gtm/overview`: one line per client with an active Tag Manager
  connection: the container (name + GTM-XXXX, match with the site), the last publish
  version, and the saved rollback point.
- Shown on `/admin/connections/` under "Tag Manager". It never returns a token or a tag
  body.
- Not built in this block: the admin container picker and the cross-client sharing guard.

## 4. Guards

The existing guards are unchanged: scopes, dead grant, container mismatch, already live,
busy workspace. This block adds one more: **nothing not named "uallak — " is touched.**

- `ensure_entity` refuses a body without the prefix before any call.
- The plan refuses (`gtm_foreign_entity`, plus an admin alert) when any planned entity
  lacks the prefix, or when our leftover workspace (which would be reused) holds a change
  to another name.
- `publish_workspace` reads the workspace's own change list (`workspaces.getStatus`)
  right before `create_version`. Any change to an entity not ours stops the publish:
  `GtmForeignEntity`, the drafts are undone, and an admin alert is sent.
  - This covers every path, including the default-workspace fallback and the undo.
  - The only exception is the built-in Click URL variable.
  - An unreadable change counts as foreign.
- The client sees one calm line ("this would also touch items we didn't create — I
  stopped, nothing changed, the team was told"), never the entity names.

## 5. Not tested against a real Tag Manager container

Everything above was tested against the in-memory fake only. These assumptions were not
checked against a real container:

- **The `workspaces.getStatus` `workspaceChange` shape:** `{tag|trigger|variable|folder|...:
  {name}}` per change, `builtInVariable` for a built-in, and deletions listed with their
  entity name. If the real shape differs, the guard refuses every publish (fail closed)
  rather than letting one through.
- **`versions.get` and `versions/{id}:publish` on an older version:** that republishing
  an earlier version is accepted with the `tagmanager.publish` scope and returns
  `containerVersion`.
- **Entity fingerprints across versions:** that an unchanged entity keeps the same
  `fingerprint` in two versions. If it does not, every undo takes the "remove" path,
  which is also safe.
- **Deleting a variable another variable still references:** the purchase variables are
  deleted newest first, and this order was not checked against the real API.
- **Whether `create_version` from a stale default workspace** (the fallback, no changes)
  could republish an older base. This risk predates this change and is not changed by it.
- **The Shin Sekai container itself:** no read and no write was made from this machine.
  Run the dry run there first: Control Room button, or `dry_run: true`.
