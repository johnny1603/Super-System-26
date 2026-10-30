# Chat as the one surface — uploads + resizable windows

Built 2026-10-31. Code: `core/chat_uploads.py`, the resumable helpers in `core/drive_service.py`
(`start_resumable`, `put_chunk`, `resumable_status`), the routes `POST /api/client/chat-upload` and
`POST /api/client/chat-upload/status` in `core/api_server.py`, and the chat windows in
`dashboard/client/index.html` (`buildWindow`, `wireWindowResize`, `wireWindowDrop`, `uploadOneFile`).
Tests: `tests/test_chat_uploads.py`. **No Google OAuth scope changed.**

## Upload from the chat

- A "+" next to every chat input, and drag-and-drop onto a chat window. On a phone the "+" is a
  plain `<input type=file>`, so iOS / Android offer the gallery, the camera and files with no extra
  integration (no `capture` attribute — that would force the camera).
- The type comes from the file's **first bytes**, not its name: video (MP4/MOV/3GP/WebM/MKV/AVI/
  WMV/FLV/MPEG/TS), image (JPEG/PNG/GIF/WebP/**HEIC/HEIF**/AVIF/TIFF incl. camera RAW/BMP/SVG),
  audio (MP3/M4A/WAV/WebM/Ogg/Opus/FLAC/AAC/AMR/CAF/AIFF), documents (PDF, Word/Excel/PowerPoint
  old and new, OpenDocument, RTF, text/CSV).
- Refused, with ONE plain Hebrew sentence (`chat_uploads.PLAIN_HE`) and an admin alert
  (`chat_fallback.alert_once`, deduped per day): executables and scripts, macro-enabled Office
  files, archives (ZIP/RAR/7z/gz…), anything unrecognised.
- **There is no antivirus engine.** We refuse what can run code; Google Drive scans files itself
  when they are downloaded. If a real scanner is wanted, it is a separate decision (cost, latency).
- A file name that looks like a credential (`core/redaction`) is replaced with a generic name
  (`video-2026-10-31-120000.mp4`) — it is never stored, not even in Drive. Control and bidi
  characters are stripped; the extension is always the REAL type's.

## No size limit of ours

The browser sends fixed chunks of `video_limits.UPLOAD_CHUNK_BYTES` (8 MiB; the JS constant
`CHAT_UPLOAD_CHUNK` must stay equal — a test checks it). Each request forwards ONE chunk into a
Drive resumable session, so memory is one chunk and no request is anywhere near Cloud Run's ~300 s
timeout. The only ceiling is Google Drive's own 5 TB per file.

- **Resume**: on a network drop / 5xx the browser asks `/chat-upload/status`, Drive says how many
  bytes it holds, and it continues from there (and after a reload, when the same file is picked
  again — the sealed token is kept in localStorage per file name/size/date).
- **Session token**: Drive's session URI is a capability, so the browser only ever gets it AES-GCM
  sealed (key derived from `SESSION_SECRET_KEY` + ":chat-upload", associated data = client id).
  Another client's token does not open. Nothing is stored server-side → any instance can take the
  next chunk. `SECRETS_ENCRYPTION_KEY` is not used.
- Limits that are REAL for a destination are explained **after** the upload, next to the action:
  YouTube `VIDEO_UPLOAD_MAX_MB` (512), TikTok drafts `TIKTOK_INBOX_MAX_MB` (64), voice tools
  `voice_recordings.MAX_MB` (25). The file is stored either way.

## Where it lands

The client's media folder (`media_agent._subfolder`; uallak's Shared Drive, shared read-only with
the client's email), in a first-level subfolder by type: `videos`, `images`, `voice` (the folder
`core/voice_recordings` lists — audio is tagged `uallak_kind=voice_recording`, with the browser-
measured duration when it has one), `documents`. First level on purpose: `client_state` reads only
first-level subfolders, so the agents see a new video at once. After the last chunk the Media tab's
listing cache (`drive_mirror._list_cache`) for that client and the `client_state` snapshot are
cleared, and the dashboard re-lists the Media tab with `refresh=true` if it was open.

## After the upload — the agent proposes

The window's agent writes one line into its own thread and offers approval cards
(`agent_actions.propose`, origin `client_chat`), decided by the REAL availability rules run on the
snapshot plus this file's facts:

- video → `publish_youtube_from_media` (private) and/or `send_tiktok_to_inbox` (draft); a plain line
  for each destination the file is too large for; a plain line when neither is connected.
- audio → it is now a voice recording; `clone_voice` only where it is really available (it is
  `internal_only` until the live test passes, and needs the cloning consent). Too large for the
  voice tools → said plainly.
- image / document → no action exists yet for chat-uploaded images/documents: said plainly, no
  fake card. ("Footage for an avatar video" has no executable action for clients yet — not offered.)

Nothing is published or sent without the client's click on a card.

## Resizable windows

Top edge, right edge and top-right corner (windows sit bottom-left and grow up/right). Minimum
280×360, maximum the viewport. The size is kept per window, per client (localStorage — a new device
starts at the default). Dragging changes only two CSS variables (`--cw-w`, `--cw-h`); no node is
added, moved or removed (see `focusWindow` and the CLAUDE.md trap). The phone layout ignores the
variables and hides the handles. Tests check the drag handlers contain no DOM moves and that
dl / new / min / close / send are still wired.

## Deliberately NOT built — a LATER Google verification round

Picking files **from the client's own Google Drive or Google Photos** (Drive Picker / Google Photos
Picker API) needs new Google OAuth scopes and therefore a new verification round. It is not part
of this feature; nothing here requests a scope. Do not add it by accident.

## Not verified live

Nothing here ran against real Google Drive: the resumable protocol is tested against a fake session
that follows Google's documented behaviour (308 + Range, 200/201, 404/410). First real check: upload
a large video (> 300 MB) from a phone and from a desktop, interrupt the network mid-way, and confirm
it resumes and appears in the Media tab and on YouTube/TikTok cards.

Known edges: the agent's line after an upload is Hebrew for every client (the UI strings around it
are in 5 languages). If the response to the very last chunk is lost and the browser then asks
`/status`, the "saved" line can be logged twice in the thread (the TikTok card is reused for the
same file; the YouTube card goes through `youtube_metadata.prepare_publish`, which may produce a
fresh card that supersedes the first).
