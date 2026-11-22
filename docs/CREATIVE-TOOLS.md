# Creative tools — separate connections, the presenter, voice recordings

Built 2026-10-30; **HeyGen retired, Higgsfield is the one presenter path 2026-10-31** (§7).
Code: `core/vendor_tools.py`, `core/avatar_video.py`, `core/media_gen_service.py`
(`generate_talking_character`), `core/voice_recordings.py`, `core/elevenlabs_service.py`,
`agents/avatar_agent.py`, the `/api/client/tools/*` and `/api/client/voice*` routes, the
dashboard cards `toolCard-higgsfield` / `toolCard-elevenlabs` / `voiceCard`.
Tests: `tests/test_creative_tools.py`, `tests/test_presenter_higgsfield.py` (the HeyGen guard).
No Google OAuth scope changed.

## 1. Audit — where several tools shared one connection (2026-10-30)

| Place | Before | Now |
|---|---|---|
| Dashboard "אווטאר דיגיטלי" card | ONE form: HeyGen key + ElevenLabs key + both consents | Removed. One card per tool |
| `_DISCONNECT_GROUPS["avatar"]` | heygen + elevenlabs disconnected together | groups of their own; `heygen` kept so an old row can still be removed |
| `avatar_agent.connect_accounts` / `POST /api/avatar/connect` | both keys in one call | LEGACY: each key given is connected on its own; a HeyGen key is refused (retired) |
| Higgsfield card | already its own card | the per-tool card (status, test, disconnect, help) + the likeness consent |
| Creatomate, Uppbeat | no connect flow at all (inactive help entries only) | unchanged |
| SEO tool | ONE shared uallak account, no client card | unchanged — not client-paid |

## 2. Each tool on its own

- Tools: **Higgsfield** ("הדמות שבסרטונים ותמונות AI" — images, video and the presenter) and
  **ElevenLabs** ("הקול שלך"). Own key field, **Connect**, **Test connection** (ElevenLabs),
  status, **Disconnect** (only that tool's `client_accounts` row and health), help button.
- Status: `connected` · `connected_unverified` (stored, not checkable or vendor unreachable —
  the first real job is the check) · `key_rejected` · `not_connected`.
- Consent: **likeness → the Higgsfield card** (optional to connect; required before a
  presenter clip with a character made from the client's OWN photo — an AI-generated
  character needs none); **voice cloning → ElevenLabs** (optional; without it a recording is
  only an audio track). Recorded with `avatar_agent.record_consent`, `CONSENT_VERSION`
  `2026-10-31.v4` (no vendor named; says the image and recording are opened to the tool only
  while each video is made).
- A key the vendor rejects at Connect is **never stored**; no key is logged or returned.
- Health: ONE `app_settings` row per client, `creative_tools:<id>` (no secrets). Cleared by
  `client_deletion`, guarded by `deletion_guard`, skipped by `admin_service.get_settings`.
- A feature that needs a missing tool returns `vendor_tools.missing_block` (plain Hebrew naming
  exactly that tool + a button that opens only its card). In chat: the `connect_creative_tool`
  card.
- Cost stays per tool: `cost_tracker.record_client_paid_media(..., tool=…)` — Higgsfield per job
  (credits), ElevenLabs per clone (samples) and per TTS (characters).

## 3. The presenter (`core/avatar_video.make_video`)

ONE provider, `higgsfield`; an old stored `heygen` choice maps to it. Inputs:

- **Character** — one still image in the client's `avatar-source` folder, made by
  `avatar_agent.create_avatar` (chat card `create_presenter_from_photos`): the client's own photo
  (`own_photo`, likeness consent) or an image media_agent generated for them (`generated`, found
  in `media_image_created` rows, no likeness consent). Logged as an `avatar_ready` row with
  `provider: "higgsfield"`. Several characters → the client picks (`choose_avatar`).
- **Voice** — `recording` (a file from the client's voice folder, used as is, no cloning consent)
  or `clone` (the script read by the ElevenLabs clone).
- **Model** — `media_gen_service.PRESENTER_MODEL` = **`wan2_7`** (Wan 2.7, "synchronized audio,
  character-consistent video"), the ONE place it is named. Chosen because it takes exactly our
  two inputs — `start_image` (the character) + `audio_references` (the voice) — keeps the face,
  and syncs to the given audio instead of generating its own; 2–15 s, 720p/1080p, 9:16.
  Seedance 2.0/2.5 also take audio references but add generated audio and cost more; Grok
  Video 1.5 is a preview. Env overrides: `MEDIA_PRESENTER_MODEL`, `MEDIA_PRESENTER_PATH`
  (default `generations`), `MEDIA_PRESENTER_RESOLUTION` (default `720p`).
- **Output** — the clip is downloaded and saved to Drive `videos/avatar/` (never auto-published).

Order of checks: gate → voice kind → **length** → character + likeness consent → missing tools
→ key → only then `public_share.briefly_public` opens the character image and the audio, the
render runs, and both are closed and read back. A refusal never shares anything.

**Length.** A clip is at most `MAX_CLIP_SECONDS` (15, = `mg.PRESENTER_MAX_SECONDS`;
`LENGTH_GRACE_SECONDS` 0.5). Recording: its measured `duration_seconds`; unknown → refused
(`length_unknown`). Clone: estimated before (2.5 words/s) and measured after TTS (mp3 at
128 kbps). Too long → `too_long` with an offer to split (`split_script` packs whole sentences;
the chat proposes one card per part). Never truncated. The approval card states the length
("אורך הסרטון: כ-N שניות…").

**Stitching is not built.** Joining parts into one video would need: ffmpeg in the container
(concat demuxer or a re-encode when resolution/fps differ), a short audio crossfade at the
joins, the parts held in Drive until joined, and a background job longer than a chat click
(plus minutes accounting for the joined length).

## 4. The gate

`avatar_video.presenter_allowed(client_id)`: open when env **`PRESENTER_VIDEO_ALL_CLIENTS`**
is `1/true/yes`, otherwise only for internal (`billing_exempt`) clients; an unreadable client
fails closed. The chat cards `create_presenter_from_photos` and `make_presenter_video` are
`internal_only` by the same switch (`agent_actions._presenter_internal_only`, read at import —
a Cloud Run env change restarts the revision anyway). A paying client gets the fixed line
`not_ready_line()`. Replaces the old `HIGGSFIELD_AVATAR_VIDEO_ENABLED`.

## 5. What works, what needs a live test

**Nothing here has run against a live Higgsfield or ElevenLabs account.** Tested with stubs only.

| Path | State |
|---|---|
| Per-tool connect / disconnect / status / consent, missing-tool message | Built + unit-tested |
| ElevenLabs key check `GET /v1/user`, clone, TTS, delete voice, error codes → Hebrew | From public docs — **needs a live test** |
| Presenter `wan2_7` (character + audio → clip) | Model id, roles and limits from Higgsfield's model catalog (2026-10-31); the REST path and the `medias` shape are our reading — **needs a live test**; a mismatch is an env fix |
| Higgsfield key check | None we could verify — a stored key stays `connected_unverified` |
| Voice upload / list / delete, browser recording | Built + unit-tested; browser recording not tested on a phone |

## 6. Live test path — internal client 27

Keys go ONLY into the dashboard cards (never chat, never a script, never logs).

1. Sign in as client 27 → **הקול שלך** card → key → **חיבור** → **בדיקת חיבור**.
2. **הדמות שבסרטונים ותמונות AI** card → key → **חיבור**; tick the likeness consent → save.
3. **הקלטות קול** → upload a recording of **under 15 seconds**; it must show its duration.
4. Send one clear photo in the media chat with "+" → approve the "presenter from photos" card →
   an `avatar_ready` row and a copy in `avatar-source`. (Or approve one with an AI image.)
5. In Cloud Shell: `bash scripts/creative_tools_live_test.sh video 27 recording FILE_ID`.
   Expect `duration_seconds` ≤ 15 and a Drive `link`. If the vendor refuses the request shape,
   fix `MEDIA_PRESENTER_PATH` / `MEDIA_PRESENTER_MODEL` as Cloud Run env vars and retry.
6. ElevenLabs card → voice-cloning consent → `bash scripts/creative_tools_live_test.sh voice 27`
   → `bash scripts/creative_tools_live_test.sh video 27 clone "שלום, זה הקול שלי"`.
7. Try a 60-word script in chat: the agent must refuse with the split offer, never cut.
8. Costs: Control Room → client 27 → `client_paid_media` rows carry `details.tool`.
9. Only after 5–7 pass: set `PRESENTER_VIDEO_ALL_CLIENTS=1` on Cloud Run.

## 7. HeyGen retired (2026-10-31)

Why: avatar creation through HeyGen's API is Enterprise-only, and everything else about it sent
the client out of the product. What changed:

- **Client-facing: gone.** Its card, consent text, provider card, footage card, help entry
  (`active: False, retired: True`), feature-catalog connections, pricing/sales disclosure,
  agent prompts and chat-card hints. Clients are never asked to sign up or paste its key.
- **Kept, unreachable:** `core/heygen_service.py` (docstring marked RETIRED) and
  `avatar_video.HeyGenProvider` in `RETIRED_PROVIDERS` (render raises). `vendor_tools.RETIRED_TOOLS`
  keeps its internal label; `status()` answers `not_connected`; `disconnect` still removes an old
  row. **Stored rows are kept** (internal test clients only) — no data deleted.
- `THIRD_PARTY_PRICING["heygen"]` is `retired: True` (price monitor skips it); budget_agent
  reports `heygen: {connected: False, retired: True}`; `run_readiness_scan` is a no-op
  (the `avatar-scan` scheduler job can be deleted); `POST /api/client/avatar-footage/create` → 410.
- Guard: `tests/test_presenter_higgsfield.py` fails if any module imports `heygen_service`,
  a HeyGen provider/tool/help/catalog entry comes back, or a client page names it.

## 8. UX pass 1 (2026-10-30) — staff, not software

Rules: never an empty form, one decision at a time, vendor names invisible to clients,
waiting is never a spinner, provider failures never surface to the client.
Tests: `tests/test_ux_vision.py`.

**Names.** The client reads capabilities, never vendors: ElevenLabs → "הקול שלך"; Higgsfield →
"הדמות שבסרטונים ותמונות AI" (`vendor_tools.client_label`, 5 languages); the presenter is
"הדמות שלך". Covered: dashboard + profile i18n, card markup, `missing_block`, `avatar_video`
labels and refusals, connect errors, avatar_agent chat lines, `feature_catalog`, `client_state`
labels, agent_actions card texts, the support prompt, and a code-level `scrub_vendor_names` on
every client chat reply (an old "HeyGen" in a reply becomes the presenter's label). Internal
names stay in code, logs, admin UI and cost rows. The vendor is named only inside the help
guide, where the client must open an account there.

**Help is the default.** Each tool card's primary button opens a guided flow
(`connection_help.guide_view`): steps, direct links (only from the entry), where the key is,
then "כבר יש לי מפתח" or "נתקעתי — עשו את זה איתי" (one deduped admin alert per tool per day).

**Waiting is a card.** An action with a `slow` spec answers the Approve click with a progress
card, runs in BackgroundTasks, and the dashboard polls until the same card becomes the result.
Slow today: `configure_conversion`, `publish_youtube_from_media`, `send_tiktok_to_inbox`,
`clone_voice`, `make_presenter_video`.


## 9. Photo set for the presenter (2026-11-16, `core/avatar_photos`)

- **Today the presenter starts from ONE image.** `PRESENTER_MODEL` takes `start_image` plus the
  audio. With own photos, the client sends 5-6, checked before any card:
  - **checked by code:** type, size (short side ≥ 720 px is good, < 400 px is refused), light
    (too dark or too bright is refused, flat is a note), sharpness (only clearly soft is refused),
    and duplicates (same md5 or the same 8x8 fingerprint);
  - **asked of the client:** angles and expressions.
- **Below 5 good photos:** a plain message with the why, the issues per photo and "send N more".
  At 5 or more, the best photo goes first in `photo_ids`, and that is the one `create_avatar`
  uses.
- **Storage:** the set is kept in app_settings `avatar_photos:<id>` (30 days), cleared on
  success and with the client.
- **AI images we generated** skip the set.
- **Multi-reference identity** (a character trained from all photos) is NOT built. It needs a live
  check of what the vendor offers first.

## 10. Higgsfield 401 "Invalid credentials" — fixed 2026-11-22

Client 27: every image (proposals 62, 63) failed with `401 {"detail":"Invalid credentials"}`.

- **Cause.** The generator was written against a guessed API that never ran live:
  - it sent `Authorization: Bearer <key>` to `platform.higgsfield.ai/v1/text2image` and polled `/jobs/{id}`;
  - Higgsfield's docs (docs.higgsfield.ai/docs/authentication) require `Authorization: Key KEY_ID:KEY_SECRET` on `https://api.higgsfield.ai` ("Do not use a Bearer token");
  - the model goes in the URL path, and the status is read from the returned `status_url` (`/requests/{id}/status`).
- **Same record.** The tile (`vendor_tools.connect`) and the generator (`media_agent._generation_key` → `secrets_vault.use_vendor_key`) both use the client's newest active `client_accounts` row with platform `higgsfield`, encrypted and bound to client + platform. There is no global key and no second record.
- **Fix, on our side only.**
  - `media_gen_service` uses the documented base, header, paths, `status_url` polling and result shapes.
  - `normalize_key` cleans the ONE pasted value on save and on every read:
    - whitespace, quotes, and a pasted `Key `/`Bearer `/`Authorization:` prefix are dropped;
    - an ID and secret copied on two lines become `id:secret`.
  - The client is asked for nothing new. Nothing falls back to a key of ours.
- **The tile tells the truth.**
  - Connect and Test run a free authenticated status read (401 = rejected; spends no credits).
  - A job's 401 marks the tile `key_rejected`; a job that ran marks it `connected`.
- **Check client 27 without seeing the key:** `bash scripts/higgsfield_check.sh 27`. It prints the key's shape (lengths, whether it has the id:secret form) and Higgsfield's answer.
- **Still not run live:** the first real generation after deploy. The presenter path (`MEDIA_PRESENTER_PATH`) is not in the docs that were read.