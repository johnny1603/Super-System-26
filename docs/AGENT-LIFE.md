# Agent life — idle behaviours per character, and celebrations (2026-11-15)

The agents' standing figures on the client dashboard are alive.
`dashboard/assets/agent-life.js` + `agent-life.css`. No library, no network call, no new image.

## Why it was rebuilt (the same day)

The first version moved the whole flat picture by a pixel or two. The owner's verdict: "the
avatar just grows and shrinks a little". Now the picture is cut into three layers and each
character has its own behaviours.

## How a flat picture moves

Each figure is ONE picture (`/assets/agents/<name>-figure.webp`). The script lays three masked
copies of that same picture over it — legs, upper body, head. The file is already in the
browser's cache, so nothing is downloaded. The head turns on the neck, the upper body leans on
the hips, the whole figure steps and bounces on its feet. At rest the three layers add up to
exactly the original picture.

Where each picture bends (neck, hips, centre line) is a small table in the script (`RIG`), one
row per character.

**What a flat picture cannot do: move an arm.** Hands stay where the artist drew them (mostly
in pockets). So "looking at her nails" is the head going down and to the side, plus a small
bubble over the head showing 💅. "Checking a watch" is the same with ⌚. Folding arms or really
touching hair needs new layered artwork per character — not done, see Limits.

## The characters

| Who | Character | Does most | Also |
|---|---|---|---|
| ג'וני (manager) | composed, watches the time and the team | looks around, checks his watch ⌚ | shifts weight, nods, rocks, stretches, gets an idea 💡 |
| ניסים (manager) | easygoing | stretches, checks his phone 📱 | shrugs, sways, bounces, laughs, steps aside |
| אלינור (manager) | poised | flips her hair ✨, looks at her nails 💅 | shifts her weight, tilts her head, a cool side glance |
| יואב | the analyst, hands in pockets | rocks on his heels, looks up thinking 💭 | tilts, nods slowly, checks numbers 📊, adjusts his glasses 👓 |
| אורי | the builder, cannot stand still | bounces, looks around, gets ideas 💡 | laughs, nods, steps aside, shrugs |
| מאיה | warm and social | checks her phone 📱 💬, flips her hair | tilts, sways, laughs, shifts her weight |
| ליאור | the artist | moves to a tune 🎵 🎧 | sways, looks up for inspiration ✨, bounces, shakes his curls |

The table in the script (`CAST`) is the whole definition: behaviour, weight, bubble. A figure
never plays the same behaviour twice in a row. An unknown figure gets a neutral set.

**Two side by side** (within reach of each other) sometimes do something together: a glance
and a nod back; turning to each other with 💬 and a laugh; leaning in and a shrug; a "hey" 👋
and a nod; a question and a head shake. One alone never gestures at nobody.

## Timing

No loop. Every 1.3–3.4 seconds the script looks for a figure that is free and plays one
behaviour (1.4–4.2 seconds long). After it, that figure rests 1.8–5.2 seconds. With three
agents waiting, something is usually happening, but never all at once.

## Rules (enforced in code, guarded by `tests/test_agent_life.py`)

| Rule | How |
|---|---|
| Never during an active conversation | nothing moves while any chat window is open, a reply is awaited, or a key was pressed in the last 6 s |
| Never while the agent is working | a figure whose own window has a running task or a pending reply stands still |
| Clearly visible | heads turn up to 15°, the upper body leans up to 6°, steps of 12 px; no scale-only pulse |
| Light | transforms and opacity only (no layout). The script adds one class and removes it by timer. Three small layers per figure, built once |
| Phone | the figures are small there, so every distance is scaled down (`--al-u`) and the bubble is smaller |
| Hidden tab | all timers are cleared on `visibilitychange`; a burst in progress ends |
| `prefers-reduced-motion` | nothing is scheduled, no layers are built; every keyframe sits inside `no-preference` |
| The off switch | unchanged: "אזור אישי" → "✨ תנועה ואנימציות" (5 languages), kept per browser |
| Browser without masks | no layers; only the whole-figure behaviours play (shift, bounce, rock, step) |
| No DOM moves | nothing existing is re-parented. Added: the layers inside a minimised figure (new nodes, not focusable) and the celebration canvas |
| Chat speed | untouched: no chat code path waits on any of this |

## Pose frames (2026-11-22, a test on אורי only)

The layered rig cannot move an arm. The test: real drawn poses for ONE character, cross-faded,
to see whether they read as motion before sets are made for the other six. **אורי only** —
the other six agents are unchanged.

**The frames** (`dashboard/assets/agents/uri/`, 8 palette PNGs, 188×336, **124 KB in all**):
`arms-folded`, `watch`, `thinking`, `wave`, `glance-left-neutral`, `glance-left-smile`,
`glance-right-neutral`, `glance-right-smile` (left / right = the SCREEN side he looks to). The
resting pose is today's `uri-figure.webp` itself — at rest he looks exactly as before.

**Making a set** (repeat for any character):

1. Generate the poses (transparent PNG, full figure, feet visible).
2. `powershell -File scripts/normalize_pose_frames.ps1 -Src <raw> -Ref dashboard/assets/agents/<name>-figure.png -Out <tmp> -OutHeight 336`
   — one common canvas in the resting picture's shape; feet on the resting pose's feet row,
   scale by head-to-feet height, centre on the legs' centre line. A frame whose STANCE differs
   (feet wide apart) goes in `-CentreOnTorso <file>` so its head and body land where the
   resting pose's are. The script prints what it corrected per frame.
3. `node scripts/pack_pose_frames.js <tmp> dashboard/assets/agents/<name>` — palette PNG
   (255 colours + transparent, no dithering: a dither pattern would shimmer in a cross-fade).
   About 4× lighter (502 KB → 124 KB for אורי). There is no WebP encoder on the dev machine.
4. Add the character to `POSES` in `agent-life.js` (frames + nudges, idle sequences, greet).

**What the normaliser corrected for אורי** (source px of the 688×1024 frames; "vs set
median" = how tight the generation itself was):

| Frame | Scale | Shift x | Shift y | Height vs median | Centre vs median | Feet vs median |
|---|---|---|---|---|---|---|
| arms-folded | ×1.290 | −49.5 (torso) | +12.1 | −0.7% | −22.6 (stance) | −4.5 |
| glance-left-neutral | ×1.286 | −50.5 | +9.3 | −0.4% | +0.5 | +1.5 |
| glance-left-smile | ×1.258 | −28.3 | +26.6 | +1.9% | −15.4 | +6.5 |
| glance-right-neutral | ×1.264 | −47.3 | +21.3 | +1.3% | +2.1 | +6.5 |
| glance-right-smile | ×1.280 | −51.6 | +19.6 | +0.1% | +2.8 | −4.5 |
| thinking | ×1.282 | −49.4 | +16.5 | −0.1% | +0.3 | −2.5 |
| watch | ×1.279 | −46.6 | +15.6 | +0.2% | −1.8 | +0.5 |
| wave | ×1.283 | −49.2 | +13.5 | −0.2% | −0.3 | −0.5 |

- **The set against itself was tight**: height within −0.7…+1.9 %, feet within ±6.5 px of
  1024, centre within ±3 px for six of eight frames.
- **The set against today's picture was not**: every frame had to grow ~1.28× and move ~49 px
  sideways and 9–27 px down — the frames were generated on a different canvas (688×1024) and
  framing than the standing picture (752×1344). A spec that says "same canvas and framing as
  the reference figure" would remove that step.
- **Two outliers.** `glance-left-smile` was really drawn off: ~2 % bigger, ~21 px to the side,
  feet 6.5 px lower. `arms-folded` was not: he stands with his feet wider apart, so the legs'
  centre is not his body's centre — aligned on the legs, his head and torso sat 7 px (of 336)
  to the side, so it is aligned on the torso instead (its feet now differ by 6.8 px, which is
  the wider stance itself).
- **After the correction**, on the 188×336 canvas against the resting pose: feet 0 px on every
  frame, top of the head within 1 px, legs' centre within 0.5 px (arms-folded: head and torso
  within 0.1 px). Head offsets of 1–5 px on the glances and thinking are the head turning.

**Motion.** The same scheduler, rules and timing as every behaviour (no loop, random figure /
pause). A behaviour of אורי is a SEQUENCE, then back to the resting pose:

| Sequence | Frames |
|---|---|
| folded-glance | arms folded → glance (neutral) → glance (smile) |
| glance | glance (neutral) → glance (smile) |
| think | thinking |
| watch | watch |
| folded | arms folded |
| look | glance (neutral) |
| greet (arrival only) | wave |

Holds of 1.3–3.2 s (±20 % random), the side of a glance random, never the same sequence twice
in a row. **The wave is never a random idle**: it plays when his figure ARRIVES — the dashboard
opened, or the client minimised his window — at most once per 10 minutes.

**The blend.** One cross-fade is 0.28 s: the new frame fades in over the old one, which stays
solid for 0.1 s and then fades (so his body never turns see-through half-way), and the new frame
settles from a small nudge (0.5–1.5 px, up to 0.8°: a glance arrives from its side, thinking
from slightly above, the wave lifting). Opacity and transform only.

**Rules kept:** nothing while a chat window is open, a reply is awaited or the client typed in
the last 6 s; nothing while his own window has a task running (a sequence already playing goes
back to rest at its next step); a hidden tab stops it on the resting pose at once; reduced
motion and the "✨ תנועה ואנימציות" switch: nothing is built or downloaded. (The figure only
exists while his window is minimised — that is when he "waits".)

**Page weight.** Nothing at page load. The 8 frames (124 KB) are requested the first time his
standing figure is visible while motion is allowed, and a sequence starts only when all its
frames have loaded. A frame that fails to load = today's layered animation for the rest of the
page.

**The switch.** Admin setting `uri_pose_frames_enabled` (Settings tab, default ON) →
`/api/dashboard` `pose_frames.uri` → `window.uallakPoseFrames`. Off = today's layered
animation for him. To compare without saving: add `?poses=off` or `?poses=on` to the dashboard
address (it beats the setting, this browser tab only).

**Not checked in a real browser** (only in Node against a fake page, and the frames by eye as
images): how the cross-fade actually looks and whether it reads as motion; the nudge sizes; the
light fringe the generator left on the hair edges against the dark page (visible at 3× zoom
on a dark background); the phone size; Safari / Firefox.

## Celebration

One short burst of confetti and one balloon (2.3 seconds) for **every task that completes
successfully, anywhere on the dashboard** — not only when an agent stands waiting.

| What completed | Where it is hooked | Rises from |
|---|---|---|
| A card the client approved, finished (a page built, a video sent, a post published — anything that runs as a card) | `decideAction`, `finishActionCard` | the agent's figure if it stands waiting, else the card |
| A slow job that finished (the card was "running") | `finishActionCard`, from the card's own polling | same |
| A connection completed (coming back from the provider's consent screen) | the `?connected=` return | the notice line |
| A connection completed on the page (ads account chosen, tag container saved, site connected, site built, site moved, landing domain verified) | `showNotice(..., 'success', connectionDone(...))` | the notice line |
| An onboarding step done | `obCelebrateProgress` | the progress ring |
| A paid upgrade | the `?upgrade=success` return | the notice line |
| An export ready | `showNotice(..., { key })` | the notice line |

Notices that only say "started", "sent", "saved" or "removed" are not tasks completing and
stay plain. Half-way returns (a page or a channel still to be created) do not celebrate.

**The rules** (in `celebrate`, guarded by tests):

- **Once per task.** Each task has a key. It is remembered before anything is drawn, in this
  browser (`localStorage`), so a re-render, a poll or a reload never repeats it.
- **Several at once = one burst.** Tasks finishing within 1.5 seconds of a burst's start
  share it.
- **Never stacked.** A task finishing while a burst is on screen gets one burst right after
  it — however many arrive, there is never more than one on screen and one waiting.
- **The same event reported twice = one.** A connection and the onboarding step it completes
  are one celebration (a 20-second group).
- **Finished while the tab was hidden:** one burst when the client comes back.
- **Skipped for good** under `prefers-reduced-motion` and when the motion switch is off.
- **Never for staff** viewing the dashboard as the client.
- The flight is timed by the clock, not by the frame, so it is the same on a slow phone.

`window.uallakLife`: `celebrate(originEl, key, { group })`, `celebrateFor(cardEl, key)`,
`setEnabled(bool)`, `enabled()`, `reduced()`. On the page: `celebrateDone(key, el, group)`.

**Not celebrated, on purpose:** a message an agent posts later in the chat (for example an
article draft that took minutes) — the page cannot tell a success from an apology there. The
pre-signup pages (the proposal chat and payment) do not load this script.

## Checked in a real browser

Chrome on desktop, with a local demo page that uses the real pictures, stylesheet and script:
the layered figure is identical to the original at rest for all seven; the head and
upper-body poses show no visible seam at 168 px and at 420 px; bubbles render; the scheduler
runs, including a pair scene; no console errors.

## Limits

- **Arms do not move** in the layered rig. Real gestures need drawn poses — being tested on
  אורי (§Pose frames); the other six still use the rig.
- Not checked: a real phone, Safari / iOS, Firefox, the dashboard itself with a logged-in
  client (the demo page copies the dashboard's figure styles), and how smooth it feels over
  minutes of watching.
- The bubbles are emoji, drawn by the device's own emoji font — they look slightly different
  on each platform.
- Admin pages (Itzik's figure) are not animated — client dashboard only.
- The switch is per browser, not per account.
