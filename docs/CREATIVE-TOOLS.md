# Creative tools — separate connections, provider choice, voice recordings

Built 2026-10-30. Code: `core/vendor_tools.py`, `core/avatar_video.py`,
`core/voice_recordings.py`, `core/elevenlabs_service.py`, `agents/avatar_agent.py`,
the `/api/client/tools/*` and `/api/client/voice*` routes, the dashboard cards
`toolCard-heygen` / `toolCard-elevenlabs` / `toolCard-higgsfield`, `providerCard`,
`voiceCard`. Tests: `tests/test_creative_tools.py`. No Google OAuth scope changed.

## 1. Audit — where several tools shared one connection

| Place | Before | Now |
|---|---|---|
| Dashboard "אווטאר דיגיטלי" card | ONE form: HeyGen key + ElevenLabs key + both consents | Removed. One card per tool |
| `_DISCONNECT_GROUPS["avatar"]` | heygen + elevenlabs disconnected together | `heygen` and `elevenlabs` groups of their own; `avatar` kept only for an old open tab |
| `avatar_agent.connect_accounts` / `POST /api/avatar/connect` | both keys in one call, HeyGen required | LEGACY: each key given is connected on its own via `vendor_tools.connect` |
| Higgsfield card | already its own card | moved onto the same per-tool card (status, test, disconnect, help) |
| Creatomate, Uppbeat | no connect flow at all (inactive help entries only) | unchanged — nothing to separate |
| SEO tool | ONE shared uallak account, no client card | unchanged — not client-paid |

## 2. Each tool on its own

- Own key field, **Connect**, **Test connection** (HeyGen, ElevenLabs), status, **Disconnect**
  (deletes only that tool's `client_accounts` row and health), help button
  (`connection_help` entries `heygen`, `elevenlabs`, `higgsfield`, now active).
- Status: `connected` · `connected_unverified` (stored, not checkable or vendor unreachable —
  the first real job is the check) · `key_rejected` (vendor said invalid: at connect, test or
  during a job) · `not_connected`.
- Consent: **likeness → HeyGen** (required to connect it); **voice cloning → ElevenLabs**
  (optional; without it a recording is only an audio track). Recorded with the existing
  `avatar_agent.record_consent` rows.
- A key the vendor rejects at Connect is **never stored**. Keys are pasted only in the card —
  chat_guard already refuses key-looking chat messages; no key is logged or returned (the card
  shows at most the last 4 characters; `client_state` not even those).
- Health + video provider choice: ONE `app_settings` row per client, `creative_tools:<id>`
  (no secrets). Cleared by `client_deletion`, guarded by `deletion_guard`, skipped by
  `admin_service.get_settings`.
- Backward compatible: existing `client_accounts` rows are the same rows — a client connected
  before shows `connected_unverified` with no re-entry and no data loss.
- A feature that needs a missing tool returns `vendor_tools.missing_block`: plain Hebrew naming
  exactly that tool + one button per missing tool that opens only its card
  (`toolCard-<tool>`). In chat the agent offers the `connect_creative_tool` card (focus kind).
- Cost stays per tool: `cost_tracker.record_client_paid_media(..., tool=…)` — HeyGen per video
  (estimate from `THIRD_PARTY_PRICING`, ₪0 for a paying client whose own account pays),
  ElevenLabs per clone (samples) and per TTS (characters), Higgsfield per job (credits).

## 3. Provider choice (`core/avatar_video.make_video`)

Agents ask for "an avatar video with this audio/script"; the provider is per video
(`provider=`) > the client's default (`providerCard` / `POST /api/client/tools/provider`)
> HeyGen. Voice sources are the same for either provider:

- `recording` — a file from the client's voice folder, used as the audio track as is
  (no cloning, so no cloning consent needed). The file id is re-checked against THIS
  client's folder listing.
- `clone` — the script read by the client's ElevenLabs clone (needs ElevenLabs + the
  voice-cloning consent + a clone).
- none — HeyGen only: a HeyGen stock voice id.

## 4. What works, what is prepared, what needs a live test

**Nothing in this feature has run against a live HeyGen, ElevenLabs or Higgsfield account.**
Everything below was tested only with stubs (`tests/test_creative_tools.py`).

| Path | State |
|---|---|
| Per-tool connect / disconnect / status / consent, missing-tool message | Built + unit-tested |
| HeyGen key check `GET /v2/user/remaining_quota` (X-Api-Key) | Written from HeyGen's public docs — **needs a live test** |
| ElevenLabs key check `GET /v1/user` (xi-api-key; `missing_permissions` = real key) | Written from public docs — **needs a live test** |
| ElevenLabs clone (`/v1/voices/add`), TTS, delete voice, error codes → Hebrew | Written from public docs — **needs a live test** (error `detail.status` codes included) |
| HeyGen avatar video with an external audio URL | Existing code path (`heygen_service`), never run live — **needs a live test** |
| **Higgsfield talking character (image + audio → video)** | **PREPARED ONLY.** The existing Higgsfield integration (`media_gen_service`) supports text→image and text→video only; there was NO character/influencer or external-audio path. `generate_talking_character` posts to `MEDIA_SPEAK_PATH` (default `speak`) with optional `MEDIA_SPEAK_MODEL` — both are guesses kept as env settings, not confirmed. The provider is OFF behind `HIGGSFIELD_AVATAR_VIDEO_ENABLED` and answers "not available yet". |
| Higgsfield key check | None exists we could verify — a stored key stays `connected_unverified` |
| Voice upload (raw body, format sniffed from bytes, 25 MB, 50 files, WAV duration exact, browser duration bounded), list, delete, delete clone | Built + unit-tested; Drive calls are the existing `drive_service` helpers |
| Browser recording (MediaRecorder → webm / Safari m4a) | Built; not tested on a phone |

## 5. Live test path — internal client 27

Keys go ONLY into the dashboard cards (never chat, never a script, never logs).

1. Sign in as client 27 → home → **ElevenLabs** card → paste the key → **חיבור**.
   Expect "מחובר ✓" (or the plain Hebrew rejection; a rejected key is not stored).
   Press **בדיקת חיבור**.
2. **HeyGen** card → paste the key, tick the likeness consent → **חיבור** → **בדיקת חיבור**.
3. Disconnect HeyGen only → ElevenLabs must still show connected. Reconnect HeyGen.
4. **הקלטות קול** → upload an mp3 (and try **הקלטה מהדפדפן** on a phone). It must appear with
   date and duration, and in Drive under the media folder → `voice`.
5. Ask the media chat "do you see my recordings?" — it must list them (client_state.voice).
6. Audio-track video (no cloning consent needed), in Cloud Shell:
   `bash scripts/creative_tools_live_test.sh video 27 recording FILE_ID`
   (FILE_ID from the Drive `voice` folder). Needs a ready HeyGen avatar and an avatar tier.
7. ElevenLabs card → tick + **אישור הסכמת שיבוט קול**, then
   `bash scripts/creative_tools_live_test.sh voice 27` and
   `bash scripts/creative_tools_live_test.sh video 27 clone "שלום, זה הקול שלי"`.
   ElevenLabs refusals come back as `message_he` (one plain Hebrew sentence).
8. Costs: Control Room → client 27 → the `client_paid_media` rows carry `details.tool`
   (heygen / elevenlabs). Client 27 is billing-exempt, so the estimate is recorded as ours.
9. **מחיקת כל ההקלטות והקול המשובט** → recordings trashed, clone deleted from the ElevenLabs
   account (an `avatar_voice_deleted` row; `current_voice_id` is then empty).
10. Higgsfield character video: only after confirming the real endpoint/model, set
    `MEDIA_SPEAK_PATH` / `MEDIA_SPEAK_MODEL` and `HIGGSFIELD_AVATAR_VIDEO_ENABLED=true` as Cloud
    Run env vars, then `bash scripts/creative_tools_live_test.sh video 27 recording FILE_ID`
    after choosing Higgsfield in the provider card (and an image in `avatar-source`).
11. Chat cards (UX pass 1): as client 27, ask the media chat to clone the voice, then for a
    presenter video. Each card must answer the click at once with a progress card, keep it
    after a reload, and turn into the result (video: Drive link). Only after this passes,
    set `internal_only` False on `clone_voice` / `make_presenter_video`.

## 6. UX pass 1 (2026-10-30) — staff, not software

Rules: never an empty form, one decision at a time, vendor names invisible to clients,
waiting is never a spinner, provider failures never surface to the client.
Tests: `tests/test_ux_vision.py`.

**Names.** The client reads capabilities, never vendors: ElevenLabs → "הקול שלך"; HeyGen →
"הדמות שבסרטונים — דמות מצולמת שלך"; Higgsfield → "הדמות שבסרטונים — דמות AI"
(`vendor_tools.client_label`, 5 languages; the provider card says "דמות מצולמת שלך" / "דמות AI").
Covered: dashboard + profile i18n, card markup, `missing_block`, `avatar_video` labels and
refusals, connect errors, avatar_agent chat lines, `feature_catalog`, `client_state` labels,
agent_actions card texts, the support prompt, and a code-level `scrub_vendor_names` on every
client chat reply (except while the client is inside that tool's own help flow). Internal
names stay in code, logs, admin UI, cost rows, `elevenlabs_service.plain_he` (staff-facing)
and the onboarding sales cost disclosure. The vendor is named only inside the help guide,
where the client must open an account there.

**Help is the default.** Each tool card's primary button opens a guided flow
(`connection_help.guide_view`, `GET /api/client/connection-help/{id}`): steps with what the
screen looks like and a direct link (only links from the entry), where the key is, then
"כבר יש לי מפתח" (the key field) or "נתקעתי — עשו את זה איתי"
(`POST /api/client/connection-help/{id}/assist` → one deduped admin alert per tool per day).
The HeyGen and ElevenLabs guides link to the vendor homepage/pricing only — their API-key
screen descriptions are from public docs, **not verified on a live account**.

**Waiting is a card.** An action with a `slow` spec (`{"minutes", "eta"}`) answers the Approve
click with `status: running` + a progress card (what runs, how long it usually takes, "we'll
write here when it's ready") and runs in FastAPI BackgroundTasks; the dashboard polls
`GET /api/client/actions/{id}` (8 s, ×1.5 backoff to 30 s) and replaces the same card with
the result. Failure → the fixed `_FAILED` line + admin alert, never the provider's error; a
known refusal → its plain line. A run older than 2× its minutes shows "the team is checking"
and alerts once. Slow today: `configure_conversion`, `publish_youtube_from_media`,
`send_tiktok_to_inbox`, `clone_voice`, `make_presenter_video`.

**New cards `clone_voice` and `make_presenter_video`** (client-approved) are
**internal_only** until step 11 above passes — they have never run live.
