# Agent life — idle motions and one celebration (2026-11-15)

Small signs of life for the agents' standing figures on the client dashboard.
`dashboard/assets/agent-life.js` + `agent-life.css`. No library, no network call, no image.

## What the client sees

- **Agents waiting next to each other** (minimised chat windows show the standing figures):
  every 4–10 seconds ONE of them does one small thing — breathes, shifts its weight, glances
  aside, nods — or leans toward its neighbour, who answers with a small nod a moment later.
  A random figure (never the same one twice in a row when there are several), a random
  motion, a random pause. Most of the time nothing moves.
- **A task finishes**: one burst of confetti and one balloon rise from the agent that did it
  (from its standing figure when the window is minimised — "the agent was waiting" — else
  from the finished card), and the figure gives a small hop. ONCE per task: the task's key is
  remembered before anything is drawn (sessionStorage + memory), so a re-render, a poll or a
  reload in the same session never repeats it.

## Rules (all enforced in code, guarded by `tests/test_agent_life.py`)

| Rule | How |
|---|---|
| Never during an active conversation | no idle motion while any chat window is open, a reply is awaited (`.typing`), or a key was pressed in the last 6 s |
| Never distracting | one motion at a time, a few pixels / under 2.5°, pauses of 4–10 s |
| Light | CSS transforms on the figure only (no layout); the script only adds a class and removes it on `animationend`. The canvas exists only for the 2.6 s of a burst (≤76 pieces, 42 on a phone, DPR ≤ 2) and is removed after. No animation loop runs in between |
| Hidden tab | the timer is cleared on `visibilitychange`; a burst in progress ends |
| `prefers-reduced-motion` | the script does nothing; the CSS keyframes sit inside `no-preference`; the canvas is `display:none` |
| A setting to turn it off | "אזור אישי" → "✨ תנועה ואנימציות: פועלות / כבויות" (5 languages). Stored in `localStorage` (`uallak_life` = `off`) — per browser, like the chat window sizes. Hidden when the device asks for reduced motion |
| Load speed | both files are small, the script is `defer`, nothing runs before the page is interactive, nothing is fetched |
| Chat speed | untouched: no chat code path waits on it; the celebration call is wrapped so a failure can never break a card |
| No DOM moves | figures only gain / lose a class; the canvas is appended to `<body>` and removed (the CLAUDE.md re-parenting trap) |

## Where it hooks in

`dashboard/client/index.html`: `finishActionCard` (a slow card that turned into its result) and
`decideAction` (a card that finished on the click) call `celebrateTask(el, id)` →
`uallakLife.celebrateFor(card, 'action:<id>')`.

## API

`window.uallakLife`: `celebrate(originEl, key)`, `celebrateFor(cardEl, key)`,
`setEnabled(bool)`, `enabled()`, `reduced()`.

## Not done / limits

- Admin pages (Itzik's figure) are not animated — client dashboard only.
- The figures are single flat images, so a "glance" is a small lean of the whole figure, not
  a head turn. Real gestures would need layered artwork per agent.
- The switch is per browser, not per account (no migration, no server call).
- Not tested in a real browser — see docs/SYSTEM-REVIEW.md checkpoint 3.
