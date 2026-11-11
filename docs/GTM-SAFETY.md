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

## 6. Store sales next to existing purchase tags (follow-up, 2026-11-11)

Case: Shin Sekai `GTM-NZRNDQN` already has purchase tags that are not ours. They include
"Purchase" (Google Ads Conversion Tracking), "Google Ads - checkout", and GA4
add-to-cart / checkout tags on gtm4wp triggers.

**What "live" means for store sales.** `gtm.purchase_live` checks OUR trigger, found by
name, plus our variables and a tag on that trigger. Someone else's purchase tag never
makes store sales "live", so it never skips our set. This was already true before this
change. `tests/test_gtm_safety.py` `test_old_purchase_tags_never_skip_store_sales` runs
the real status check against such a container to prove it. The store_sales skip reason
in the plan now names only our own tags.

**What we add.** All names start with "uallak — ":
- the four variables;
- the trigger "uallak — purchase (order received)";
- the tag "uallak — purchase record", which posts order number / total / currency only to
  our `/api/store/purchase/` endpoint;
- "uallak — GA4 purchase", **only if** no unpaused GA4 `purchase` tag of someone else's
  is live (`gtm.purchase_ga4_tag_wanted`). If one is live, the plan is `record_only`:
  no second GA4 purchase, so no double counting in GA4 or in an Ads import.
  - We never create a Google Ads conversion tag.
  - A paused tag of theirs does not count.
  - If our own GA4 purchase tag is already there, it is kept and reused, never
    duplicated.

**Live check in record_only mode.** Our trigger, our variables, and our record tag on
the trigger. Their GA4 tag carries the GA4 event.

**What the preview shows** (`plan.purchase`):
- `existing`: the purchase tags found and left untouched:
  - `ga4_purchase`;
  - `ga4_shop`: add_to_cart, begin_checkout, …;
  - `ads_conversion`: type `awct`;
  - `other`: anything named like purchase / checkout / order / conversion.
- `mode`: `ga4` or `record_only`.
- The exact entities to add.

The card says all of this in 5 languages. The done line in record_only mode says "our
order record is live, your existing purchase tags stay as they are". It never claims we
record where the buyer came from.

**Never touched:** any entity not named "uallak — ". The guards of §4 enforce this:
`ensure_entity`, and the `getStatus` check before every publish. Rollback (§3) is
unchanged.

**Flagged, never deleted** (`gtm.tags_needing_review`): a Google / GA4 tag that is not
ours whose id looks like a placeholder (`G-XXXXXXX`) or a date (`G-20240613`). It is
shown as "not created by the platform / needs review":
- in the card;
- in the Control Room confirm dialog;
- in `/admin/connections/` → History. The history endpoint now includes `live_review`:
  ours, the existing purchase tags, and the tags needing review.

**Could our code have created "Google Analytics G-XXXXXXX" or "Google Analytics
G-20240613"? No.**
- Since its first commit (caf0da0), `gtm_service` has named every entity "uallak — …".
- No commit in the history ever wrote a `googtag` / `gaawc` tag, or a name starting
  with "Google Analytics" (`git log -S`).
- The only `G-XXXXXXX` strings in the repo are usage hints in a script, a doc and a
  validator message. None of them is a tag body.

## 5. Not tested against a real Tag Manager container

- **§6 assumptions:**
  - that Shin Sekai's tags have the types assumed here: `awct` for Ads conversions,
    `gaawe` with `eventName` for GA4 events, `googtag` with `tagId` for the Google tag;
  - that their purchase tags really fire on gtm4wp custom events;
  - that their GA4 purchase tag, if there is one, is unpaused.

  The preview lists exactly what it finds, so the first dry run on the real container
  shows whether these hold.

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
