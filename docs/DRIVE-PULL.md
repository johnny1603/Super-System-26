# Pull from Drive, find by name, and why a dropped file hid (2026-11-11)

Step 1 of getting the owner's own files into a client's media folder.
No new scope, no Google Console change, no env var, no Cloud Run setting, no
Picker (the Picker is step 2, after Google's round-2 review). Tests:
`tests/test_drive_pull.py`.

## Why a dropped file hid

The owner said files he dragged into a client's folder in our Shared Drive
didn't show up. What the code showed:

1. **The writer and the readers picked the folder by different rules.**
   This is verified in the code and fixed.
   - Before the fix, `media_agent.ensure_client_media_root` looked up the EXACT
     name `client-{id} — {current name}`. The readers
     (`drive_mirror.shared_client_root`) used the recorded hint, or else the
     oldest `client-{id} — ` prefix match.
   - So after a client was renamed, the writer created a SECOND folder. Cloud
     Run instances also disagreed: one that had written (memory cache) listed
     the new folder, the others listed the old one.
   - Now the writer first asks `shared_client_root`, and creates a folder only
     when there is none. Every reader in that process then reuses the result
     (`remember_root`).
2. **A "no folder yet" answer was cached for 10 minutes.** This is verified
   and fixed: only a real folder id is cached now.
3. **The agents saw only one level, first page.** This is verified and fixed.
   - `client_state` listed the root plus 8 subfolders, one level deep. Yet
     `media_agent` itself saves TikTok videos in `videos/tiktok/`.
   - Now `drive_mirror.newest()` reads the whole tree: up to 200 folders, 6
     levels deep, newest first.
4. **Shortcuts.** This is verified in the code; whether it happened in his
   case is not known.
   - Drag a file you don't own into a Shared Drive folder and Drive may make a
     shortcut instead of moving the file.
   - A shortcut used to be shown as a plain file that nothing could use. Now
     it is the `shortcut` kind: never offered to agents, and named in the
     diagnostic.
5. **The Media tab caches its listing for 3 minutes.** This is unchanged; the
   diagnostic says when the listing is cached.

**Assumed, not provable from here:** the files went into a folder the system
doesn't use. That could be an old folder in his My Drive from before the
Shared Drive move, or a duplicate folder. The admin diagnostic shows this
directly: `folders_named_like_client`, each marked inside or outside the
media root.

## The admin diagnostic

`GET /api/admin/drive/diagnose/{client_id}` (admin cookie, read-only), shown
at the bottom of `/admin/drive/`. It returns:

- the service account;
- the folder in use and why (`recorded_folder` and `writer_folder_this_instance`);
- every folder named like the client, anywhere;
- the raw Drive listing of the tree, with names, kinds, `parent_id` and
  subfolder path;
- for each file, whether the Media tab's first page shows it and whether the
  agents' list shows it;
- counts, and plain Hebrew findings.

It never returns a token or a file body.

## Pull from Drive (`/admin/drive/`, `core/drive_pull.py`)

1. The owner shares ONE folder with the service-account address. The page
   shows the address, with a copy button, and warns against sharing the
   whole Drive.
2. He pastes the folder (or file) link and clicks "הצג".
   - `POST /api/admin/drive-pull/browse` lists the folder as the service
     account.
   - If we can't open it, the answer is ONE calm line naming the address to
     share with. It is never an error.
3. He picks files, or "the whole folder" (subfolders included), the client,
   and a target: by type → `videos/` / `images/` / `documents/`, or one he
   chooses. `POST /api/admin/drive-pull/start` starts the job.
4. **Copying happens inside Drive**: `drive_service.copy_file`
   (`files.copy`, `supportsAllDrives=true`). No byte goes through our server.
   Each copy carries `appProperties.uallak_pulled_from=<source id>`, so a
   re-run says "כבר היה שם" instead of copying twice.

The job is durable and follows the `core/tiktok_uploads` pattern:

- **Where it lives.** `app_settings` `drive_pull:<id>` holds the jobs and
  `drive_pull_lock:<id>` the lease. No migration is needed.
- **Progress.** It is saved after every file.
- **Retries.**
  - A transient error is retried 3× (20 s / 60 s / 180 s), then the file is
    `failed`.
  - `no_access`, `copy_blocked` (the owner disabled copying),
    `storage_quota`, trashed files and folders are named, not retried.
- **What moves it.**
  - The page's poll: `POST /api/admin/drive-pull/{id}/step`, at most 25 s.
  - The existing every-minute scheduler request `/api/tiktok/uploads/run`,
    with what is left of its budget.
  - There is no new scheduler job.
- **Limits.** Up to 500 files per job, folders up to 6 levels deep.
- **Who it's for.** Only clients whose files live in our Shared Drive
  (`drive_storage.location == shared_drive`).
- **After the job.**
  - The client's mirror and `client_state` caches are cleared.
  - Their Shared Drive usage is shown next to `DRIVE_SHARED_ALERT_GB`, using
    the existing `shared_usage_scan`, which alerts once per status change.
- **Client deletion** clears both keys.

## Find by name (`drive_mirror.find`, card `find_media_file`)

The agents' `account_state.media_folder` lists only the newest files. When a
client names a file that isn't there, the model proposes `find_media_file`
with their words and, optionally, what it is for (`tiktok` / `youtube` /
`presenter`). No purpose (or anything else) = a LOOKUP: the answer is a plain
line (found, folder, date), never a stored card — see docs/MEDIA-TAB.md §1.

- **The search (changed 2026-11-11).** The whole tree is listed once
  (`drive_mirror.catalog`, the same list the Media tab shows) and ranked by
  `core/media_search` — THE matcher, shared with the Media tab's search box.
  Case, spaces, `_` and `-` don't count, the extension is ignored for an exact
  match, every script works (Thai included), and only the best-scoring group is
  kept. A Hebrew prefix letter is also tried without it ("המוצר" → "מוצר").
  Shortcuts are left out. (Drive's `name contains` was dropped: it matched word
  starts only, so "test 3" missed "test_3.mp4".)
- **One match:** the real card (for example `send_tiktok_to_inbox`) with that
  file, plus "מצאתי את …".
- **Several matches:** ONE card with one button per file. The click proposes
  the real card for the picked file, and that card is shown right under it
  (`next_card`). The model never chooses.
- **No match:** ONE card `media_file_not_found` (code-only).
  - **A client** gets their folder link. The text says to upload with the
    chat's +, because a client's folder is shared with them READ-ONLY, so
    they can't drop files into it.
  - **The owner** (Control Room chat) gets `/admin/drive/?client_id=X`.
- **Language rule.** Agents never say "I can't see it" or "passing it to the
  team". No tool names appear in client text, and no raw errors: a failed
  search is one calm "try again in a moment".

## Add by link (2026-11-11)

A pasted Google Drive link is copied INSIDE Drive (`files.copy`, `supportsAllDrives=true`) into the
client's `videos/`, `images/` or `documents/`, by type. Code: `drive_pull.add_by_link`. It uses the
durable job above, with a per-file result.

**Accepted:** `drive.google.com/file/d/ID`, `open?id=`, `uc?id=`, `/drive/u/N/folders/ID`, and
`docs.google.com/{document,spreadsheets,presentation,…}/d/ID`, with or without query parameters or
`#…`, and bare IDs (`drive_pull.parse_ref`). `find_link` finds such a link inside chat text.

**Where it works:**
- **`/admin/drive/`:** the "הוספה ישירה ללקוח" button next to "הצג" (`POST /api/admin/drive-pull/add-link`).
- **The client Media tab:** the "add from link" field (`POST /api/client/media/add-link`). Its poll
  is `…/add-link/step`, and the admin "enter as client" view is read-only.
  - Every line is in the client's 5 languages.
  - A link we can't open shows the service-account address with a copy button.
- **The client chat:** a message holding a Drive link makes code (`support_agent._drive_link_turn`)
  propose ONE card, `add_media_from_drive_link` ("add this to your media?"). This happens whatever
  the model answered.
  - The card's prepare opens the link first. Cases it can't proceed with become one calm line, never
    a card that would fail: not shared (with the address), trash, a bad link, or a client whose files
    live in their own Drive.

**Idempotent:**
- The same source with an active job returns that job.
- A finished link re-pasted is copied as "כבר היה שם" (`appProperties`), so there is no duplicate.

**A client never pulls from our own Shared Drive** (`own_storage`): it holds every client's folder.
The admin may (attach, below).

## Attach an existing file from anywhere (admin only)

`drive_pull.find_anywhere` searches every drive the service account sees: `corpora=allDrives`,
`supportsAllDrives`, `includeItemsFromAllDrives` (`drive_service.search_everywhere`).
- **Name search:** Drive matches the longest word by prefix. The results are then ranked by
  `media_search` (THE matcher), best group only.
- **Link or id:** read directly.
- **Each candidate shows:** name, folder path (`drive_service.folder_path`), date, size, and whether
  it is already in the client's folder.

Where it is used:
- `/admin/drive/` → "צירוף קובץ קיים" (`POST /api/admin/drive/find-anywhere`, then `…/attach`).
- The Control Room card `attach_existing_media`, with approver admin. It is never offered in a client
  chat, because it sees every client's files. Several matches → ONE card with a button per file.

The chosen file is COPIED into the connected folder. The original is never moved or deleted.

## Merge folders (admin only, `core/drive_merge.py`)

`GET /api/admin/drive/merge/{id}` lists every folder named "client-{id} …" with:
- its id;
- whether it is the CONNECTED one (the resolver's answer);
- whether it is under the media root;
- file / sub-folder counts and size.

"להעתיק לתיקייה המחוברת" (`POST …/merge/{id}/start`) starts a `drive_pull` job in mode `merge`:
- **Copied:** every file, into the SAME sub-folder path under the connected folder; missing
  sub-folders are created.
- **Skipped:** a file already there with the same name AND size (`already_same`), and our own earlier
  copies (`appProperties`).
- **Nothing is moved or deleted;** the source folder is untouched.
- **Limits:** up to 2,000 files per job; a re-run continues without duplicates.
- **Refused:** the connected folder itself, a folder not named like the client, and a folder inside
  the connected one.

## One folder per client — the single resolver

`drive_mirror.shared_client_root` is THE rule (the recorded folder if it is under the media root,
else the oldest `client-{id} — ` folder there). Readers and the writer both use it.
- **The writer** (`media_agent.ensure_client_media_root`) calls it with `strict=True`. A Drive error
  during the lookup raises `RootUnknown` (the write fails and is retried) instead of reading as "no
  folder yet" and creating a second one. That was a real path to a duplicate.
- **After a create,** the writer re-resolves by the same rule, so two instances that created at the
  same moment converge on the oldest. The extra empty folder is left alone, never deleted.
- **The other creator:** the only other `ensure_folder("client-{id} — …")` in the code is the
  offboarding ARCHIVE under `DRIVE_ARCHIVE_FOLDER_ID`. `tests/test_drive_link.py` enforces both rules.

## Diagnose with real data: `scripts/diagnose_media.py`

`python3 scripts/diagnose_media.py 27 "test 3"` in Cloud Shell. It is read-only.
- **How it calls:** it reads ADMIN_KEY from the Cloud Run env like `tiktok_diagnose.sh` (never printed)
  and calls `GET /api/admin/drive/media-diagnose` (`drive_merge.media_diagnosis`).
- **What it prints:**
  - the stored folder id and the resolved one;
  - the raw tree of every folder named like the client, connected or not (up to 300 files each);
  - the find result for the name: inside the connected folder (agents + Media tab) and everywhere
    (attach).

## Not tested against a real Drive

This block's calls:
- `corpora=allDrives` name search and its prefix matching on real names;
- the parent walk for folder paths on a Shared Drive root and on a My Drive root;
- the exact-name query with quotes / Hebrew;
- `files.copy` of a Google Docs file;
- `driveId` on a My Drive file.

Earlier pull work:

- `files.copy` from a My Drive into a Shared Drive as the service account,
  and the copy's owner and quota.
- The `appProperties has {…}` query.
- `name contains` tokenisation on real (Hebrew, underscore) names.
- `mimeType contains`.
- `corpora=allDrives` folder search (the diagnostic).
- Shortcut metadata.
- `capabilities.canCopy`.
