# Client Media tab + the agents' file lookup (2026-11-11)

Code: `core/media_search.py`, `core/media_library.py`, `core/drive_mirror.py`
(`catalog`, `find`), `core/drive_download.py` (player section),
`core/agent_actions.py` (`find_media_file` lookup mode, `send_tiktok_batch`),
`dashboard/client/index.html` (Media tab), `scripts/i18n_report.py`.
Tests: `tests/test_media_tab.py`, `tests/test_drive_pull.py` (`FindCard`).

No new scope, no Google Console change, no env var, no migration (the visit
stamp is one `app_settings` row per client). Never min instances or
--no-cpu-throttling.

## 1. Diagnosis: why "test 3" was refused with `invalid_params`

The manager chat (client 27, channel general) proposed `find_media_file` for
"do you see a video in my Drive called test 3". `agent_actions.propose`
returns `invalid_params` in exactly two places: `validate_params` (unknown
key, missing required param, a validator raising) and `validate_stored` after
`prepare`.

- **Verified in code:** the old spec made `for` REQUIRED, and `_v_find_for`
  raised on anything other than `tiktok` / `youtube` / `presenter`. A pure
  "do you see it?" question has no purpose, so whatever the model put there
  (nothing, "", "check", "look") was refused before any Drive call.
- **Assumed:** the exact payload the model sent (no production logs here).
  Cloud Logging would show it:
  `textPayload:"proposal find_media_file refused"` on service `super-system`.
  An extra unknown key (for example `kind`) would have failed the same way —
  `kind` is now a real optional param.
- **Fix:** `for` is optional; anything that is not a known purpose means
  LOOK. A lookup answers with a plain line (`clarify`): the file, its folder
  and date (dd/mm/yyyy); several files → one line each (folder + date tell
  duplicates apart); none → the existing ONE `media_file_not_found` card. A
  lookup is never stored as a card (`_validate_find_stored` refuses it).
  Unknown keys are still refused — the safety check is unchanged.
- **Replay test:** `FindCard.test_the_test_3_question_is_answered_not_refused`
  (five payload shapes, the general channel, client_chat origin).

The second bug in the same path: the old search used Drive's `name contains`,
which matches the START of words. "test 3" never matched `test_3.mp4`, a lone
"3" was dropped, Thai could not match at all. Replaced by `core/media_search`.

## 2. Why files dropped into Drive were not visible — status after 60f6236

| Case | Status |
|---|---|
| Two code paths resolving the client root differently (a cached "" / stale hint) | Fixed in 60f6236 — verified in code + tests |
| Files in subfolders (videos/tiktok …) not in the agents' list | Fixed in 60f6236 (whole tree, depth 6, ≤ 200 folders) — verified |
| Shortcuts (a file dragged in that you don't own) | Listed as their own kind since 60f6236; still cannot be SENT (the file lives outside the folder) — the owner uses Pull from Drive. Verified |
| The Media tab's 180 s listing cache hid a new file | Fixed now: opening the tab silently re-reads Drive (`refresh=true`) and re-renders only on a change; "Refresh from Drive" stays. Verified in code; NOT tried on a real Drive |
| Name matching (underscores, digits, Thai) | Fixed now (`media_search`). Verified by tests |
| Deeper than 6 levels, > 200 folders, > 2000 files | Still not covered (limits of `folder_tree` / `catalog`); the tab says "showing the first files only". Verified in code |
| Two folders named `client-<id> …` under the media root | Not fixed: the system uses one; the admin diagnostic (`/api/admin/drive/diagnose/<id>`) names the other. Verified in code |
| Files the client adds to THEIR OWN Drive under `drive.file` | Assumed invisible (drive.file sees only files the app created); the flag is OFF, so no client is on that path today |

## 3. One matcher: `core/media_search`

Used by the Media tab's search box (`media_library.view`) and the agents'
`find_media_file` (`drive_mirror.find`) — one implementation.

- NFKC + casefold; Latin/Hebrew/Arabic combining marks dropped ("café" =
  "cafe", niqqud ignored); Thai/Indic/kana marks KEPT (they are vowels).
- Separators don't count: "test 3" = "test_3" = "Test-3" = "TEST3".
- The extension is ignored for an exact match.
- Scores: exact name 300 > whole query inside 200 > every word 100+n > some
  words (a lone character alone never matches). Request words ("the video",
  "הסרטון") are dropped unless nothing else is left.
- `search(items, query, kind, folder, sort, best_only)`; a shortcut counts as
  the kind it points to; folder "" = the main folder only, a path includes its
  subfolders.

## 4. The tab (`GET /api/client/media-library`)

- The WHOLE tree in one list (`drive_mirror.catalog`: every folder, paged,
  ≤ 2000 items, cached 120 s), with search (debounced), type chips with counts
  (all / videos / images / documents), folder select, sort (newest / oldest /
  name), 60 per page + "load more".
- On open: `POST /api/client/media-library/seen` returns the PREVIOUS visit
  (stored as `app_settings` `media_seen:<id>`), which decides the **New**
  badges (`createdTime` after it). First visit ever: nothing is new. The admin
  read-only view cannot stamp it (not a READ endpoint).
- **Sent to TikTok / YouTube** badges: agent_actions rows that ended `done`
  plus finished `tiktok_uploads` jobs (60 s cache, cleared on a send).
- **Duplicates:** same name (extension and separators ignored) → folder +
  date emphasised on the card.
- Document cards use a neutral icon, never Drive's (often blank white)
  thumbnail. Names are in `<bdi dir="auto">` (RTL names no longer reversed).
- The system-made index below hides itself when the list above already holds
  the files ("Nothing created yet" never shows under a full list).
- One calm line says where files go to appear (the chat "+" or the Drive
  folder).

## 5. Built-in player (`GET /api/client/media-drive/play/<id>`)

- Videos up to the admin setting **`media_video_play_max_mb`** (default 100,
  1–2000, admin settings page) play in a lightbox; bigger ones, and types a
  browser can't play, stay "Open in Drive". Downloads of videos still never go
  through us (`plan()` unchanged).
- Ownership: the same fresh parents walk as downloads.
- Memory: every answer is a 206 with Content-Length, at most 4 MiB per range,
  streamed in 256 KiB pieces from Drive's own Range request — flat under the
  512 Mi limit whatever the file size. Own rate bucket (`play:<by>:<id>`).
  Each piece is logged like a download (`purpose=play`).

## 6. Multi-select (`POST /api/client/media/send`)

- Pick files → bar with Send to TikTok / Send to YouTube / Download / Clear.
- TikTok: 1 video → the existing `send_tiktok_to_inbox` card; 2–10 → ONE
  `send_tiktok_batch` card (code-only, client approver). Prepare: no / old
  grant → the `connect_tiktok` card; a bad container is named and left out;
  above the owner threshold → owner approval as for one video ("אני מטפל
  בזה"); one left → the single card. The click creates one durable
  `tiktok_uploads` job per video (no proposal_id: each job posts its own done
  line).
- YouTube: exactly one video per card (per-video title/description approval
  and no durable YouTube job yet). A YouTube batch is a PROPOSAL, not built.
- Download: one file after another through the existing download path;
  videos are told to download in Drive. Browsers may ask once to allow
  several downloads.
- Non-videos in a send selection are left out with one line saying so.

## 7. Translations

- Supported languages: **he, en, fr, ar, ru** (`dashboard/assets/i18n.js`
  SUPPORTED, `agent_actions.LANGS`).
- `t()` now falls back to **English**, then Hebrew, when a key lacks the
  reader's language. No client's language setting is changed by this.
- `python scripts/i18n_report.py` — per client page (client dashboard,
  profile, login, onboarding, landing): keys missing a language, translations
  still holding Hebrew, Hebrew that bypasses the table. All zero after this
  change; `tests/test_media_tab.py` keeps them there.
- Moved into the tables: the internal-account package badge (`pkg_internal`,
  matched against the server's stored Hebrew value), the whole outreach page
  (`or_*`, status labels mapped client-side from the status code), persona
  names (`persona_name_*`), aria labels (support button, scroll, download).
- Kept Hebrew on purpose (marked `i18n-ok` / `data-i18n-skip`): the admin
  "view as client" bar and staff-decision texts (staff-only, Hebrew like the
  admin pages), the Hebrew-only gendered greeting branch, typed confirm
  phrases and "other" answers accepted in every language, the Israeli form
  names in ask_tax_id.
- Known gap: some server-built lines (for example outreach `followup_label`,
  card texts from the server) arrive in the client's chat language from the
  server, not from the page table.

## 8. Not tested here

- A real Drive: paging past 1000 items, Range responses for the player,
  `createdTime` on uploaded vs copied files, Shared Drive quirks.
- A real browser: the player (seeking, iOS Safari), RTL rendering of mixed
  names, the phone layout (checked in CSS only: wrapping toolbar, 44 px
  targets, 2-column grid under 640 px), multi-file download prompts.
- A real client chat: that the model now omits `for` or sends `look` for a
  lookup question.
- The TikTok batch against the live TikTok API.
