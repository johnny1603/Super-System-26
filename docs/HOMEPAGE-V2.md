# Homepage v2 — a new look behind a switch (2026-11-15)

`https://app.uallak.com/` is the page Google's OAuth verification (and TikTok's review) read.
So the new look is shipped **dark**: deployed, but not shown to anyone until the owner turns
it on.

## The guarantee while it is OFF (the default)

- Setting `homepage_v2_enabled` = `false` in `admin_service.DEFAULT_SETTINGS`. No env var, no
  Console change, no Cloud Run setting.
- `dashboard/landing/index.html` was **not edited**: it has no reference to the v2 files.
- The `/` route hands the request to the same static-file handler class, on the same folder,
  that served it before — the response is the same file, byte for byte. If anything in the
  route fails, it falls back to that same file.
- So: **deploying changes nothing a reviewer sees.** Same URL, same title and description,
  same app name, same purpose statement, same services text, same privacy and terms links.

## What turning it ON does

The same HTML with two tags added — `<link rel="stylesheet" href="/assets/home-v2.css">` in
`<head>` and `<script src="/assets/home-v2.js" defer></script>` before `</body>`. Nothing else
in the document changes; `tests/test_homepage_v2.py` strips the two tags and compares the
result with the original, and compares the readable text of both versions.

What the visitor notices:

| Area | Change |
|---|---|
| Hero | the headline, sub-line and purpose statement arrive in three beats; the accent line carries a slow moving ink gradient; a soft light follows the pointer (desktop) |
| Navigation | settles into a tighter floating bar after the first scroll; a thin reading-progress line on top |
| Scroll reveals | softer travel with a touch of scale; cards in a row arrive one after the other; section headings resolve eyebrow first, then title |
| Micro-interactions | cards lift with a light that follows the pointer, icons spring slightly, buttons press, FAQ rows nudge; the three commitment figures pop once |
| Type and spacing | balanced headings, calmer paragraph measure and line height, clear keyboard focus rings |

What it never does: add, remove, hide or rewrite any text, link, section or image. The
commitment figures ("72h", "24/7", "100%") are animated with a class, never re-typed.

- **All five languages and RTL** follow automatically: no text is added, positions use
  logical properties, the progress line flips in RTL.
- **Mobile-first**: hover and pointer effects exist only on devices that hover; the phone
  gets the reveals, the nav and the progress line.
- **Reduced motion**: every animation sits inside `prefers-reduced-motion: no-preference`,
  and the script exits at once under `reduce` — a still, complete page.
- **Speed**: two small files (under 12 KB each, unminified), the script deferred, transforms
  and opacity only, scroll and pointer work passive and coalesced into one animation frame,
  no library, no font, no image, no network call.

## How to use it

1. **Preview without turning it on** (only you, only that request):
   `https://app.uallak.com/?home=v2`
2. **Turn it on after Google has answered**: Admin → הגדרות → "דף הבית — העיצוב החדש" → tick
   "פעיל" → "שמור הגדרות". It reaches every visitor within about a minute (each server
   instance re-reads the switch at most once a minute).
3. **Turn it off**: untick and save. The v2 response is sent with `Cache-Control: no-store`,
   so nobody keeps an old copy.

The switch is read without ever making the public page wait: the answer comes from memory
(off until proven otherwise) and is refreshed in the background. A slow or unreachable
database leaves the homepage on its current look, at full speed.

## Before turning it on

- Look at the preview on a phone and a desktop, in Hebrew and in English.
- Nothing needs to be re-submitted to Google for a look change, but the verification rules
  still hold afterwards: same URL, privacy and terms links, app name and purpose statement —
  all untouched by v2.

## Not done / limits

- Not seen in a real browser (see docs/SYSTEM-REVIEW.md checkpoint 6). The preview link
  exists exactly so that the first look happens before it is switched on.
- The page's own inline animations (orbs, existing reveals, the before/after slider) are
  unchanged; v2 layers on top of them.
- The route itself was not executed locally (no FastAPI on the dev machine); it is covered by
  source checks and by a direct-file fallback.

Files: `core/homepage_v2.py`, `dashboard/assets/home-v2.css`, `dashboard/assets/home-v2.js`,
the `/` route in `core/api_server.py`, the switch in `dashboard/admin/index.html`.
