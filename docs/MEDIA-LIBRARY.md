# Media library ("מאגר המדיה") — 2026-11-23

`core/media_inventory.py`. Client dashboard: INSIDE the Media tab (2026-11-23 — the separate screen
and its side-nav item are gone): the storage summary is the line right under the tab title, and the
section "כל המדיה — סינון, בחירה ומחיקה" (`#mediaLib`, above the Drive folder list) holds the filters,
"בחירת הכול" / "ניקוי הבחירה", the selection bar (count + total size + both delete actions), the one
confirmation, the inline result and the grid (a checkbox and both delete buttons on every item).
Control Room → client → "🗂 מאגר המדיה — כלל שמירה ויומן מחיקות" (unchanged).

## One grid (2026-11-24)

The Media tab has ONE grid of files (`#libGrid`, `libCard`): the old "מה יש בתיקייה שלך ב-Drive" list
(`/api/client/media-library`, `renderDriveMirror`) is gone from the tab, and "כל מה שהמערכת יצרה עבורך"
lists only items with no Drive file. Its hint, "רענון מ-Drive" (`#libRefresh`) and add-by-link moved into
the section head. Every card carries its own actions, shown only where they apply (`libCan`): view
(images), play (videos the player takes), download (non-video, under the cap; Google files as PDF),
open in Drive, send to TikTok / YouTube (videos in OUR folder: `sendable` — agent_actions only takes
those), and both delete modes. The selection bar offers download, TikTok, YouTube (exactly one video)
and both deletes for whatever is selected.

Selection: explicit ids, or "בחירת הכול" = everything matching the filter MINUS what was unchecked
after it (`excluded` → `exclude` on /preview and /delete). Every item stays toggleable; the bar's count
and size update live. Shift-click on a checkbox selects the range from the previous one. Drag selection
(mouse only; never on a touch-only device; never from a checkbox / button / link / image / field): a
rectangle, every card it touches is selected on release, Ctrl / Cmd adds instead of replacing, the
window scrolls near its top / bottom edge.

Folder list (`#mediaFolders`, `media_inventory.folder_list` + the system folder link from
/api/client/media-assets): the system folder first, then every connected ROOT folder — its own Drive
name, role, connected / not reachable (with the stored plain-Hebrew reason and what to do), what the
index holds from it (indexed items, images, videos — pending ones not counted) and an open-in-Drive
link. Tests: `tests/test_media_tab_library.py`.

## What it lists

Every media item the system knows about, read live (nothing copied):

| source | from |
|---|---|
| generated_image / generated_video | our media folder; file ids from media_agent's `media_*_created` activity rows |
| slide | our media folder; a Social-queue item with source `slide`, or the renderer's `…-vN.png` name |
| real_copy | our media folder; `media_*_real_asset` rows — copies made before 2026-11-23 (real assets are referenced now, core/asset_refs) |
| uploaded | anything else in our media folder |
| client_asset | the client's connected Drive folders (`drive_assets` index) |

Per item: thumbnail (`/api/client/media-inventory/thumb/{id}`), source, folder, created date, size,
format, usage. Usage = Social queue posts (a copied real asset also counts for its source file) and
the current content plan (proposals): `used` / `unused` / `rejected` (every post that used it was
rejected).

Filters: source, folder (a folder includes its subfolders), usage, date range, size (MB), older than
N days, search in name/description/tags. Sort: newest / oldest / largest / smallest. The summary at
the top: total items and size, by source.

Quick filters: all rejected generations; unused generated media older than N days (N starts from the
manager's `unused_days`, set in the Control Room; no number in code); everything from one folder.

## Deleting

Single item or bulk (checked items, or "all N matching the filter"). Two actions, both always there:

- **הסרה מהמאגר** (`mode: index`): out of the index, agents stop using it, the file stays.
  Media-folder items go into `media_library:<id>.removed`, which `drive_mirror.catalog` filters out
  (so the Media tab, `newest`, `find` and every agent stop seeing them). Connected-folder items get
  `state: removed` in the drive_assets index; the scan keeps them out (`_upsert_item`), `find_assets`
  never returns them and the folder counts leave them out.
- **מחיקת הקובץ** (`mode: file`): `drive_service.delete_file` — a permanent delete, or the Drive trash
  when Drive refuses a permanent delete (a Shared Drive Content manager may only trash; the trash
  empties itself after 30 days) — then out of the index. Works in every connected folder; there is no
  role restriction. **A failed delete still takes the item out of the library** (same as `index`:
  the agents stop using it) and is listed per item with `file_deleted: false` and a reason CODE
  (`media_inventory.REASONS`): `owner_only` (a regular Drive where the client owns the file — only the
  owner may delete or trash it; checked with `ownedByMe` / `driveId` after a 401/403), `no_permission`,
  `not_found` (404), `drive_unavailable` (429/5xx/network), `unknown`. The screen shows the plain Hebrew
  line for the code (`LIB_T.reason_*`); the technical detail is only in the server log and the
  deletion log entry (`failures[].detail`).

Before it runs, ONE preview (`/preview`): mode in plain words, item count, total size, folders, and
the waiting/approved posts that use the items. Shown, never blocking. Then it runs (`/delete`). The
result stays inline under the selection bar: how many went (and how much), then every file that was
not deleted with its plain Hebrew reason, one line per file.

## Log

Every action → one entry in app_settings `media_deletions:<id>`: at, by (client / admin / retention),
mode, count, size, failed, folders, affected posts, and every item (id, name, size, source, folder,
how). Shown in the Control Room (`GET /api/admin/media-inventory/{id}`).

## Retention (OFF by default)

`media_library:<id>`: `retention_enabled` (false) + `retention_days`. Only the manager switches it on,
in the Control Room, and only with a day count. The daily `drive-assets-scan` run calls
`run_retention()` first: rejected generated media (generated_image / generated_video / slide) older
than N days, mode `file`, logged by `retention`.

Both keys are cleared with the client (`client_deletion`, `deletion_guard`, admin_service skip list).
Tests: `tests/test_media_inventory.py`.

## Drag selection: the root cause seen in a real browser (2026-11-24)

Reproduced in Chrome on the deployed dashboard.
- **A press on the page beside the grid** was refused, because only `#libGrid` was accepted. The grid's cards fill it (12 px gaps), and pictures and buttons are excluded by design, so the empty space next to the cards (the `<body>`) is where people press.
- **A press on a picture** starts the browser's own image drag. This exclusion is kept, as specified.

Fix: `libDragZone` also accepts a press on the empty background of any element that CONTAINS the grid (body, the view, the section), at the grid's height, while the tab is visible. Text selection is suppressed during a drag.

Verified with real mouse drags in Chrome. Ctrl was checked with dispatched events, because the extension's drag can't hold Ctrl.
