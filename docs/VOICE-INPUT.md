# Voice messages in the client chat (2026-11-15)

The client taps the microphone in a chat window, speaks, and the words appear in the input
box as text. They read it, fix what they like, and press send. The agents receive an ordinary
text message.

`dashboard/assets/voice-input.js` (one self-contained file), wired in
`dashboard/client/index.html` `buildWindow`.

## What was checked first

There is **no transcription capability anywhere in the codebase or the connected services**:

| Looked at | Result |
|---|---|
| The code (`transcri`, `speech`, `whisper`, `MediaRecorder`, `getUserMedia`) | nothing |
| Claude (the only model provider) | takes no audio input |
| ElevenLabs (`core/elevenlabs_service`) | voice CLONING and text-to-speech only, and on each client's OWN key — not a uallak service |
| `core/voice_recordings` | stores uploaded recordings for cloning; never transcribes |
| YouTube metadata | deliberately context-only ("watching the video" is a future premium feature) |

A server-side engine would be a **new paid service and a new key** — a stop-and-ask item, so it
was NOT added. What was built needs neither.

## How the speech becomes text

The browser's own speech recognition (`SpeechRecognition` / `webkitSpeechRecognition`).

- **uallak never receives the audio and stores nothing.** The script does not open the
  microphone itself (no `getUserMedia`, no `MediaRecorder`), holds no audio stream and uploads
  nothing. A test fails if any of those appear in the file.
- The audio is handled by the **browser's speech service** (Google in Chrome, Apple in Safari,
  Microsoft in Edge) under the browser's own microphone permission. That is a third party
  hearing the client's voice, so the privacy policy must say it — see the TODO in
  `docs/SYSTEM-REVIEW.md` (the policy text is frozen until Google answers the verification).
- The server sees only the text the client chose to send, through the ordinary
  `POST /api/client-chat` with every existing check (chat_guard length and daily caps, the
  key/password filter, redaction).

## Browser coverage (from documentation — NOT tested on devices)

| Browser | Works? |
|---|---|
| Chrome, Edge (desktop) | yes |
| Chrome (Android) | yes — one phrase per session, restarted quietly |
| Safari (macOS 14.1+, iOS 14.5+) | yes, when Dictation / Siri is enabled on the device; sessions end on a pause and are restarted quietly |
| Firefox | **no** — the microphone button is not drawn |
| In-app browsers (Instagram, Facebook, some WebViews) | usually no — the button is not drawn |

Hebrew, English, French, Arabic and Russian are requested as `he-IL`, `en-US`, `fr-FR`,
`ar-IL` (falling back to `ar-SA`, then `ar`) and `ru-RU`, following the dashboard language.

## Behaviour

- **Recording strip**: a dot, the elapsed time, **ביטול** (Cancel) and **סיימתי** (Done). Words
  appear in the box as they are recognised.
- **Cancel** restores the box to exactly what it held before the recording.
- **Done** keeps the text, puts the cursor in the box and shows one line: "Here is what I
  heard. Fix the text if needed, then send." Nothing is ever sent automatically.
- **Length**: at most 2 minutes (`MAX_SECONDS`); then it stops by itself and says so.
- **Microphone refused / missing / service unreachable / nothing heard**: one calm line above
  the box for 7 seconds, in the client's language. The button stays; typing always works. No
  error code, no vendor name.
- **Stops at once** when the message is sent, the window is minimised or closed, or the tab is
  hidden. Late results never land in the next message.
- One recording at a time across all chat windows.
- Staff "view as client" never get the button (read-only).
- The button and strip are built with the window and only shown / hidden with a class — no
  node is moved (the CLAUDE.md re-parenting trap).

## Limits and the open decision

- Accuracy is whatever the browser's engine gives; Hebrew in Chrome is good, mixed
  Hebrew/English less so. The review step exists for exactly this.
- No Firefox. A paid server engine (one module behind the same button) would cover every
  browser and usually improves Hebrew — **the owner's decision**; it needs a new service, a
  key, an env var and a privacy-policy change.
- Not available in the sales chat (`/chat/`) or in Itzik's chat — client dashboard only.

Tests: `tests/test_voice_input.py`.
