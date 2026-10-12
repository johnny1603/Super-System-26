# HANDOFF — Media tab Drive button (2026-10-13)

## What exists (investigated first — it was NOT an unbuilt placeholder)

There is no per-client Drive OAuth, by design, and there doesn't need to be:
`agents/media_agent.py` keeps every client's media in a folder **uallak owns**
— service account (`GOOGLE_SERVICE_ACCOUNT_JSON`) → root `DRIVE_MEDIA_FOLDER_ID`
→ `client-{id} — {name}/images|videos|scripts|exports/...`, created on first ask
and shared read-only with the client's email. Generation (images, videos,
filming kits, avatar videos, content docs, Google Doc exports) all upload
there. The client never connects anything.

The Media tab's button is greyed out exactly when the server returns no
`folder_link`, i.e. when `get_client_media_link()` fails or media isn't
configured. The causes, in likelihood order:

1. **The root folder is in a personal My Drive (most likely).** The service
   account owns no storage, so Drive refuses to create files there:
   `403 storageQuotaExceeded — "Service Accounts do not have storage quota"`.
   `drive_service`'s own docstring describes exactly that setup ("a folder in
   Johnny's own Google Drive shared with the service account"). Fix: a
   **Shared Drive** with the service account as Content manager, root folder
   inside it. Every call already sends `supportsAllDrives=true` — no code change.
   The offboarding ARCHIVE (`DRIVE_ARCHIVE_FOLDER_ID`) has the same exposure:
   move it to the Shared Drive too.
2. **The gate demanded the archive var.** Media was gated on
   `drive_service.is_configured()`, which requires `DRIVE_ARCHIVE_FOLDER_ID` as
   well — so a missing archive id disabled every client's media folder.
3. The root isn't shared with the service account / wrong id / bad key.

Failures were invisible: a `print`, a grey button, and alerts that said only
"403 Forbidden" because `raise_for_status()` drops Drive's error body.

## What changed

- `drive_service.is_media_configured()` — service account + media root only.
- `drive_service.classify_error()` → `storage_quota | no_access | auth | other`.
- Every Drive call raises WITH Drive's error body (`_check`), so existing
  media/avatar/content-doc alerts now show the real cause.
- `media_agent.media_folder_status()` — `ok | not_configured | error(reason)`;
  failures alert (throttled 6 h per reason) with the concrete fix.
- `media_agent.drive_self_test()` + `POST /api/admin/control-room/{id}/drive-self-test`
  + a "בדיקת Drive" button in the Control Room: folder → upload → list (file
  visible) → delete → client link, each step reported with its fix.
- Media tab: the link is really blocked when unusable (`disabled` on an `<a>`
  did nothing — it opened `#`), and a line explains why (not set up yet / we're
  on it). `/api/client/media-assets` returns `folder_status`.
- Google Doc export gate uses the media check, not the archive one.

## Do this

1. Google Drive → New → Shared drive (needs Google Workspace). Add the service
   account's email as Content manager. Create the media root (and an archive
   folder) inside it; set `DRIVE_MEDIA_FOLDER_ID` / `DRIVE_ARCHIVE_FOLDER_ID`.
2. Deploy, then Control Room → a client → "בדיקת Drive". All five steps green =
   usable by the media agent.

If there is no Workspace (no Shared Drives), the alternative is OAuth
delegation — uallak's own Google account, one refresh token — not a per-client
connection. That needs the full `drive` scope (restricted: security
assessment), because clients upload avatar source clips into their folder and
`drive.file` can't see files the app didn't create. A decision, not built.

## Google Docs export (the follow-up question)

It is NOT blocked today: `POST /api/client/export/gdoc` already exists — it
writes a native Google Doc into the client's uallak-owned `exports/` folder and
shares it with them as **writer**, so they can "Make a copy" into their own
Drive. What's missing is only a Doc created **in the client's own Drive**,
which needs a per-client consent with `drive.file`. Since no per-client Drive
OAuth was built here, that stays a separate item. It does depend on the same
Shared Drive fix as the media folder — until then it fails the same way.

## Not verified
No Python on this machine and no Drive access from here. JS parsed; admin page
smoke-run. The self-test is the verification, after deploy.
