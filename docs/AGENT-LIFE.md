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

- **Arms do not move.** Real gestures (nails, hair, folded arms, a wave) need layered artwork
  per character: separate arm, hand and hair pieces, or a short sprite sequence. That is new
  art, not code.
- Not checked: a real phone, Safari / iOS, Firefox, the dashboard itself with a logged-in
  client (the demo page copies the dashboard's figure styles), and how smooth it feels over
  minutes of watching.
- The bubbles are emoji, drawn by the device's own emoji font — they look slightly different
  on each platform.
- Admin pages (Itzik's figure) are not animated — client dashboard only.
- The switch is per browser, not per account.
