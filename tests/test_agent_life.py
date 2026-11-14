"""Agent life: idle motions + one celebration (dashboard/assets/agent-life.js,
docs/AGENT-LIFE.md, docs/SYSTEM-REVIEW.md Part 3).

A. Static rules: transforms only, keyframes behind no-preference, no library,
   no network, deferred, client dashboard only, a switch in five languages.
B. Behaviour (the real script in Node against a small fake page): an idle
   motion plays only when agents stand waiting and nobody is talking; never
   under reduced motion, on a hidden tab or when switched off; the celebration
   happens once per task and never again.

Run from the tests folder:  python -I -B test_agent_life.py -v
(Node is used for B when installed; without it B is skipped with a reason.)
"""
import json
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
JS_PATH = os.path.join(ROOT, "dashboard", "assets", "agent-life.js")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Static(unittest.TestCase):
    def setUp(self):
        self.js = _src("dashboard", "assets", "agent-life.js")
        self.css = _src("dashboard", "assets", "agent-life.css")
        self.page = _src("dashboard", "client", "index.html")

    def test_css_moves_only_transforms_and_respects_reduced_motion(self):
        frames = self.css[self.css.index("@media (prefers-reduced-motion: no-preference)"):
                          self.css.index("@media (prefers-reduced-motion: reduce)")]
        self.assertEqual(self.css.count("@keyframes"), frames.count("@keyframes"))   # every keyframe is inside
        for block in re.findall(r"@keyframes \w+ \{(.*?)\n  \}", frames, re.S):
            props = set(re.findall(r"([a-z-]+)\s*:", re.sub(r"\([^)]*\)", "", block)))
            self.assertEqual(props, {"transform"}, block)
        self.assertNotIn("infinite", self.css)                                       # nothing loops
        self.assertIn(".al-burst { display: none; }", self.css.split("prefers-reduced-motion: reduce")[1])
        self.assertIn("pointer-events: none", self.css)

    def test_script_is_light_and_self_contained(self):
        code = self.js.split("*/", 1)[1]
        for banned in ("fetch(", "XMLHttpRequest", "import ", "require(", "setInterval(", "innerHTML",
                       "insertBefore", "cdn", "http"):
            self.assertNotIn(banned, code, banned)
        self.assertIn("Math.min(window.devicePixelRatio || 1, 2)", code)
        self.assertIn("'(prefers-reduced-motion: reduce)'", code)
        self.assertIn("'visibilitychange'", code)
        self.assertIn("if (window.uallakLife) return;", code)
        # the canvas is the only node ever added, and it is removed
        self.assertEqual(code.count("appendChild("), 1)
        self.assertIn("canvas.parentNode.removeChild(canvas)", code)
        # remembered BEFORE drawing, so a task can never celebrate twice
        body = code[code.index("function celebrate(origin, key)"):]
        self.assertLess(body.index("remember(key)"), body.index("document.createElement('canvas')"))

    def test_never_during_a_conversation(self):
        fn = self.js[self.js.index("function conversationActive()"):self.js.index("function pick(")]
        for needle in ("OPEN_CHAT", "PENDING", "TYPING_QUIET_MS"):
            self.assertIn(needle, fn)
        idle = self.js[self.js.index("function idleOnce()"):self.js.index("function schedule()")]
        self.assertIn("conversationActive()", idle)

    def test_loaded_deferred_on_the_client_dashboard_only(self):
        self.assertEqual(self.page.count('<script src="/assets/agent-life.js" defer></script>'), 1)
        self.assertEqual(self.page.count('<link rel="stylesheet" href="/assets/agent-life.css">'), 1)
        for other in ("admin", "landing", "login", "onboarding", "profile", "terms", "privacy"):
            self.assertNotIn("agent-life", _src("dashboard", other, "index.html"), other)

    def test_hooks_and_switch(self):
        finish = self.page[self.page.index("function finishActionCard"):self.page.index("function celebrateTask")]
        self.assertIn("if (c.status === 'done') celebrateTask(el, c.id);", finish)
        self.assertIn("if (r.status === 'done' && decision === 'approve') celebrateTask(el, id);", self.page)
        wrap = self.page[self.page.index("function celebrateTask"):self.page.index("function lifeToggleLabel")]
        self.assertIn("try {", wrap)                         # decoration can never break a card
        self.assertIn('id="lifeToggle"', self.page)
        for key in ("life_on", "life_off"):
            line = next(ln for ln in self.page.splitlines() if ln.strip().startswith(key + ":"))
            for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
                self.assertIn(lang, line, key)


HARNESS = r"""
const fs = require('fs'), vm = require('vm');
function figure(name, left) {
  const classes = new Set(['cw-figure-body']);
  const el = { name, offsetParent: {}, isConnected: true, style: { setProperty() {} }, offsetWidth: 1, _l: {},
    classList: { add: c => { classes.add(c); log.push([name, c]); }, remove: c => classes.delete(c),
                 contains: c => classes.has(c) },
    addEventListener: (t, f) => { el._l[t] = f; }, removeEventListener() {},
    getBoundingClientRect: () => ({ left, top: 500, width: 80, height: 160 }) };
  return el;
}
let log = [];
function page(opts) {
  log = [];
  const figures = (opts.figures || []).map((n, i) => figure(n, 100 + i * 120));
  const canvases = [];
  const store = () => { const m = {}; return { getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); },
                                               removeItem: k => { delete m[k]; } }; };
  const doc = { hidden: !!opts.hidden, _l: {},
    querySelectorAll: sel => sel.indexOf('minimized') !== -1 ? figures : figures,
    querySelector: sel => (sel.indexOf(':not(.minimized)') !== -1 ? (opts.openChat ? {} : null)
                          : sel.indexOf('.typing') !== -1 ? (opts.pending ? {} : null) : null),
    addEventListener: (t, f) => { doc._l[t] = f; }, removeEventListener() {},
    createElement: () => { const c = { setAttribute() {}, getContext: () => new Proxy({}, { get: () => () => {} }),
                                       parentNode: null }; return c; },
    body: { appendChild: c => { c.parentNode = { removeChild: () => { canvases.splice(canvases.indexOf(c), 1); } }; canvases.push(c); } } };
  const win = { document: doc, innerWidth: 1200, innerHeight: 800, devicePixelRatio: 1,
    localStorage: store(), sessionStorage: store(),
    matchMedia: () => ({ matches: !!opts.reduced, addEventListener() {} }),
    performance: { now: () => 0 }, requestAnimationFrame: () => 1, cancelAnimationFrame() {},
    setTimeout: () => 0, clearTimeout() {}, Math, Date, JSON, console };
  win.window = win;
  if (opts.off) win.localStorage.setItem('uallak_life', 'off');
  vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), win);
  return { life: win.uallakLife, figures, canvases, win };
}
function idle(opts, times) {
  const p = page(opts);
  for (let i = 0; i < (times || 30); i++) p.life._idleOnce();
  return log.slice();
}
const out = {};
out.two = idle({ figures: ['a', 'b'] });
out.one = idle({ figures: ['a'] });
out.none = idle({ figures: [] });
out.openChat = idle({ figures: ['a', 'b'], openChat: true });
out.pending = idle({ figures: ['a', 'b'], pending: true });
{ const p = page({ figures: ['a'] }); out.enabledDefault = p.life.enabled();
  out.offNow = p.life.setEnabled(false); out.storedOff = p.win.localStorage.getItem('uallak_life');
  out.onAgain = p.life.setEnabled(true); out.storedOn = p.win.localStorage.getItem('uallak_life'); }
{ const p = page({ figures: ['a'] }); const f = p.figures[0];
  out.first = p.life.celebrate(f, 'action:7'); out.canvasesAfterFirst = p.canvases.length;
  out.second = p.life.celebrate(f, 'action:7'); out.hop = log.some(e => e[1] === 'al-hop'); }
{ const p = page({ figures: ['a'], reduced: true }); out.reducedBurst = p.life.celebrate(p.figures[0], 'action:8');
  out.reducedCanvases = p.canvases.length; out.reducedRemembered = p.life.celebrate(p.figures[0], 'action:8'); }
{ const p = page({ figures: ['a'], off: true }); out.offBurst = p.life.celebrate(p.figures[0], 'action:9'); }
{ const p = page({ figures: ['a'], hidden: true }); out.hiddenBurst = p.life.celebrate(p.figures[0], 'action:10'); }
{ const p = page({ figures: ['a'] });
  const card = { getBoundingClientRect: () => ({ left: 10, top: 10, width: 200, height: 80 }),
                 closest: () => ({ classList: { contains: () => true }, querySelector: () => p.figures[0] }) };
  out.fromFigure = p.life.celebrateFor(card, 'action:11'); out.figureHopped = log.some(e => e[0] === 'a' && e[1] === 'al-hop'); }
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed — the browser script can't run here")
class Behaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run([NODE, "-e", HARNESS, JS_PATH], capture_output=True, text=True, encoding="utf-8",
                             timeout=60)
        if run.returncode != 0:
            raise AssertionError(run.stderr[-2000:])
        cls.out = json.loads(run.stdout.strip().splitlines()[-1])

    def test_idle_motions_vary_between_waiting_agents(self):
        played = self.out["two"]
        self.assertGreaterEqual(len(played), 30)
        self.assertEqual({name for name, _cls in played}, {"a", "b"})
        self.assertGreaterEqual(len({cls for _name, cls in played}), 3)          # variety
        known = {"al-breathe", "al-shift", "al-glance", "al-nod", "al-lean"}
        self.assertTrue({cls for _name, cls in played} <= known)

    def test_a_single_agent_never_leans_at_nobody(self):
        self.assertTrue(self.out["one"])
        self.assertNotIn("al-lean", {cls for _name, cls in self.out["one"]})
        self.assertEqual(self.out["none"], [])

    def test_never_during_an_active_conversation(self):
        self.assertEqual(self.out["openChat"], [])
        self.assertEqual(self.out["pending"], [])

    def test_the_switch_is_kept(self):
        self.assertTrue(self.out["enabledDefault"])
        self.assertFalse(self.out["offNow"])
        self.assertEqual(self.out["storedOff"], "off")
        self.assertTrue(self.out["onAgain"])
        self.assertIsNone(self.out["storedOn"])

    def test_one_celebration_per_task(self):
        self.assertTrue(self.out["first"])
        self.assertEqual(self.out["canvasesAfterFirst"], 1)
        self.assertFalse(self.out["second"])
        self.assertTrue(self.out["hop"])

    def test_nothing_is_drawn_when_motion_is_unwanted(self):
        self.assertFalse(self.out["reducedBurst"])
        self.assertEqual(self.out["reducedCanvases"], 0)
        self.assertFalse(self.out["reducedRemembered"])     # and it does not fire later either
        self.assertFalse(self.out["offBurst"])
        self.assertFalse(self.out["hiddenBurst"])

    def test_it_rises_from_the_waiting_agent(self):
        self.assertTrue(self.out["fromFigure"])
        self.assertTrue(self.out["figureHopped"])


if __name__ == "__main__":
    unittest.main()
