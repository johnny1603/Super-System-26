"""Agent life: idle behaviours per character + celebrations
(dashboard/assets/agent-life.js, docs/AGENT-LIFE.md, docs/SYSTEM-REVIEW.md).

A. Static rules: transforms and opacity only, keyframes behind no-preference,
   nothing loops, no library, no network, deferred, client dashboard only, a
   switch in five languages; every behaviour the script can play exists in the
   stylesheet with the same duration.
B. Characters: each of the seven (ג'וני, ניסים, אלינור, יואב, אורי, מאיה,
   ליאור) has its own set of behaviours and its own picture cut.
C. Behaviour (the real script in Node against a small fake page): a figure
   plays only its own character's behaviours, never the same twice in a row;
   two side by side interact, one alone never does; nothing during a
   conversation or for an agent that is working; nothing under reduced motion,
   on a hidden tab or when switched off; the celebration is once per task.

Run from the tests folder:  python -I -B test_agent_life.py -v
(Node is used for C when installed; without it C is skipped with a reason.)
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
CHARACTERS = ("johnny", "nissim", "elinor", "yoav", "uri", "maya", "lior")
PAIR_ONLY = {"al-turn", "al-lean"}


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
        self.assertEqual(self.css.count("animation:"), frames.count("animation:"))   # and every animation
        blocks = re.findall(r"@keyframes \w+ \{(.*?)\n  \}", frames, re.S)
        self.assertGreaterEqual(len(blocks), 30)
        for block in blocks:
            props = set(re.findall(r"([a-z-]+)\s*:", re.sub(r"\([^)]*\)", "", block)))
            self.assertTrue(props <= {"transform", "opacity"}, block)                # no layout, ever
        self.assertNotIn("infinite", self.css)                                       # nothing loops
        self.assertIn(".al-burst { display: none; }", self.css.split("prefers-reduced-motion: reduce")[1])
        self.assertIn("pointer-events: none", self.css)

    def test_movements_are_clearly_visible_not_a_scale_pulse(self):
        # the owner's complaint: "the avatar just grows and shrinks a little"
        self.assertNotIn("alBreathe", self.css)
        self.assertNotIn("1.006", self.css)
        degrees = [float(d) for d in re.findall(r"(-?\d+(?:\.\d+)?)deg", self.css)]
        self.assertGreaterEqual(max(degrees), 12)                # heads really turn and tilt
        self.assertGreaterEqual(len([d for d in degrees if abs(d) >= 5]), 30)
        for part in (".al-head", ".al-upper", ".al-legs", ".al-torso", ".al-emote", "mask-image"):
            self.assertIn(part, self.css)
        self.assertIn(".al-rig + .agent-figure { visibility: hidden; }", self.css)

    def test_small_figures_on_a_phone_move_in_proportion(self):
        phone = self.css[self.css.index("@media (max-width: 640px)"):][:300]
        self.assertIn("--al-u: 0.45px", phone)

    def test_script_is_light_and_self_contained(self):
        code = self.js.split("*/", 1)[1]
        for banned in ("fetch(", "XMLHttpRequest", "import ", "require(", "setInterval(", "innerHTML",
                       "cdn", "http", "infinite"):
            self.assertNotIn(banned, code, banned)
        self.assertIn("Math.min(window.devicePixelRatio || 1, 2)", code)
        self.assertIn("'(prefers-reduced-motion: reduce)'", code)
        self.assertIn("'visibilitychange'", code)
        self.assertIn("if (window.uallakLife) return;", code)
        self.assertIn("canvas.parentNode.removeChild(canvas)", code)
        # nothing existing is ever re-parented: the only insertBefore places the NEW rig
        self.assertEqual(code.count("insertBefore("), 1)
        self.assertIn("pic.holder.parentNode.insertBefore(rig, pic.holder);", code)

    def test_never_during_a_conversation_or_while_the_agent_works(self):
        fn = self.js[self.js.index("function conversationActive()"):self.js.index("function working(")]
        for needle in ("OPEN_CHAT", "PENDING", "TYPING_QUIET_MS"):
            self.assertIn(needle, fn)
        idle = self.js[self.js.index("function idleOnce()"):self.js.index("function schedule()")]
        self.assertIn("conversationActive()", idle)
        self.assertIn("!working(f)", idle)
        self.assertIn("var WORKING = '.progress, .typing';", self.js)

    def test_loaded_deferred_on_the_client_dashboard_only(self):
        self.assertEqual(self.page.count('<script src="/assets/agent-life.js" defer></script>'), 1)
        self.assertEqual(self.page.count('<link rel="stylesheet" href="/assets/agent-life.css">'), 1)
        for other in ("admin", "landing", "login", "onboarding", "profile", "terms", "privacy"):
            self.assertNotIn("agent-life", _src("dashboard", other, "index.html"), other)

    def test_celebration_rules_in_the_script(self):
        code = self.js.split("*/", 1)[1]
        fn = code[code.index("function celebrate(origin, key, opts)"):code.index("function burst(origin)")]
        self.assertLess(fn.index("remember(key)"), fn.index("burst(origin)"))     # remembered before anything is drawn
        for needle in ("if (!enabled() || reduced()) return false;", "MERGE_MS", "GROUP_MS", "queued = { origin: origin };",
                       "document.hidden"):
            self.assertIn(needle, fn)
        self.assertEqual(code.count("document.body.appendChild(canvas)"), 1)       # one canvas, in one place
        self.assertIn("DURATION = 2300", code)                                    # short

    def test_every_completed_task_on_the_dashboard_celebrates(self):
        page = self.page
        notice = page[page.index("function showNotice(text, kind, done)"):page.index("function connectionDone")]
        self.assertIn("if (done && kind === 'success') celebrateDone(done.key, banner, done.group);", notice)
        self.assertIn("if (ADMIN_VIEW || key == null) return;", notice)           # never for staff viewing as the client
        self.assertIn("try {", notice)
        self.assertIn("'DOMContentLoaded'", notice)                               # the script is deferred
        for hook in ("connectionDone('google_ads')", "connectionDone('gtm_container')", "connectionDone('wordpress')",
                     "connectionDone('site_built')", "connectionDone('site_migrated')", "connectionDone('landing_domain')",
                     "{ key: 'export:' + Date.now() }", "const done = connectionDone(justConnected);",
                     "celebrateDone('upgrade:'", "{ group: 'connection' });"):
            self.assertIn(hook, page, hook)
        # half-way returns are not done yet
        self.assertIn("!/(_pending|_no_page|_no_channel)$/.test(justConnected)", page)
        # notices that only say started / sent / removed stay plain
        for plain in ("showNotice(uallakI18n.t('landing_creating'), 'success');",
                      "showNotice(uallakI18n.t('landing_unpublished'), 'success');",
                      "showNotice(uallakI18n.t('landing_domain_sent'), 'success');",
                      "showNotice(uallakI18n.t('voice_deleted'), 'success');"):
            self.assertIn(plain, page, plain)

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

    def test_every_character_has_a_picture_to_cut(self):
        for name in CHARACTERS:
            for ext in ("webp", "png"):
                self.assertTrue(os.path.exists(os.path.join(ROOT, "dashboard", "assets", "agents",
                                                            f"{name}-figure.{ext}")), name)


HARNESS = r"""
const fs = require('fs'), vm = require('vm');
let log = [], clock = 1e6, timers = [], rigsBuilt = 0, frame = null, perf = 0, shown = 0;
function node(tag) {
  const cls = new Set();
  const n = { tag, children: [], props: {}, style: { setProperty: (k, v) => { n.props[k] = v; } }, textContent: '',
    classList: { add: c => cls.add(c), remove: c => cls.delete(c), contains: c => cls.has(c) },
    setAttribute() {}, appendChild(c) { n.children.push(c); c.parentNode = n; return c; },
    querySelector(sel) { for (const c of n.children) { if ('.' + c.className === sel) return c;
                         const d = c.querySelector && c.querySelector(sel); if (d) return d; } return null; } };
  return n;
}
function figure(name, left, opts) {
  const classes = new Set(['cw-figure-body']);
  const img = { complete: true, naturalWidth: 500, currentSrc: 'https://x.test/assets/agents/' + name + '-figure.webp' };
  const el = { name, offsetParent: {}, isConnected: true, style: { setProperty() {} }, offsetWidth: 1, _rig: null, working: false };
  const holder = { classList: { contains: () => false }, querySelector: () => img,
                   parentNode: { insertBefore: rig => { el._rig = rig; rigsBuilt++; } } };
  el.classList = { add: c => { classes.add(c); log.push([name, c]); }, remove: c => classes.delete(c),
                   contains: c => classes.has(c) };
  el.querySelector = sel => sel === '.al-rig' ? el._rig : sel === '.agent-figure' ? holder
                          : (el._rig ? el._rig.querySelector(sel) : null);
  el.closest = () => ({ querySelector: () => (el.working ? {} : null) });
  el.getBoundingClientRect = () => ({ left, top: 500, width: 80, height: 160 });
  return el;
}
function page(opts) {
  log = []; timers = []; rigsBuilt = 0; frame = null; perf = 0; shown = 0;
  const figures = (opts.figures || []).map((n, i) => figure(n, 100 + i * (opts.spacing || 120), opts));
  const canvases = [];
  const store = () => { const m = {}; return { getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); },
                                               removeItem: k => { delete m[k]; } }; };
  const doc = { hidden: !!opts.hidden, _l: {},
    querySelectorAll: () => figures,
    querySelector: sel => (sel.indexOf(':not(.minimized)') !== -1 ? (opts.openChat ? {} : null)
                          : sel.indexOf('.typing') !== -1 ? (opts.pending ? {} : null) : null),
    addEventListener: (t, f) => { doc._l[t] = f; }, removeEventListener() {},
    createElement: tag => tag === 'canvas'
      ? { setAttribute() {}, getContext: () => new Proxy({}, { get: () => () => {} }), parentNode: null }
      : node(tag),
    body: { appendChild: c => { c.parentNode = { removeChild: () => { canvases.splice(canvases.indexOf(c), 1); } }; canvases.push(c); shown++; } } };
  const win = { document: doc, innerWidth: 1200, innerHeight: 800, devicePixelRatio: 1,
    localStorage: opts.local || store(), sessionStorage: store(),
    CSS: { supports: () => !opts.noMask },
    matchMedia: () => ({ matches: !!opts.reduced, addEventListener() {} }),
    performance: { now: () => perf }, requestAnimationFrame: f => { frame = f; return 1; }, cancelAnimationFrame() {},
    setTimeout: (f, ms) => { timers.push(f); return timers.length; }, clearTimeout() {},
    Math, Date: { now: () => clock }, JSON, console, WeakMap };
  win.window = win;
  if (opts.off) win.localStorage.setItem('uallak_life', 'off');
  vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), win);
  return { life: win.uallakLife, figures, canvases, win, doc, timersAtLoad: timers.length };
}
// every pending timer that is not the scheduler's own tick: the answers of a pair scene
// the burst on screen runs out; whatever waited behind it gets its turn
function endBurst() { perf += 5000; const f = frame; frame = null; if (f) f(perf); const run = timers.slice(); timers.length = 0; run.forEach(g => { try { g(); } catch (e) {} }); }
function flush(skip) { const run = timers.slice(skip); timers.length = skip; run.forEach(f => { try { f(); } catch (e) {} }); }
function idle(opts, times, prepare) {
  const p = page(opts);
  if (prepare) prepare(p);
  const base = timers.length;
  for (let i = 0; i < (times || 60); i++) { clock += 20000; p.life._idleOnce(); if (opts.answers) flush(base); }
  return { log: log.filter(e => e[1] !== 'al-emote-on'), p };
}
const out = { cast: {}, moves: null, solo: {}, rigs: {} };
{ const p = page({ figures: ['maya'] }); out.moves = p.life._moves; out.rig = p.life._rig; out.whole = Object.keys(p.life._whole);
  out.scenes = p.life._scenes;
  for (const k in p.life._cast) out.cast[k] = p.life._cast[k].map(c => ({ move: c[0], weight: c[1], bubble: c[2] || '' })); }
for (const name of ['johnny', 'nissim', 'elinor', 'yoav', 'uri', 'maya', 'lior', 'stranger']) {
  const r = idle({ figures: [name] }, 150);
  out.solo[name] = r.log.map(e => e[1]);
  out.rigs[name] = { built: rigsBuilt, props: r.p.figures[0]._rig ? r.p.figures[0]._rig.props : null,
                     layers: r.p.figures[0]._rig ? r.p.figures[0]._rig.children.map(c => c.className) : [] };
}
out.two = idle({ figures: ['maya', 'lior'], answers: true }, 200).log;
out.far = idle({ figures: ['maya', 'lior'], answers: true, spacing: 900 }, 200).log;
out.none = idle({ figures: [] }).log;
out.openChat = idle({ figures: ['maya', 'lior'], openChat: true }).log;
out.pending = idle({ figures: ['maya', 'lior'], pending: true }).log;
out.working = idle({ figures: ['maya', 'lior'], answers: true }, 120, p => { p.figures[1].working = true; }).log;
{ const r = idle({ figures: ['maya', 'elinor'], noMask: true }, 120); out.noMask = r.log; out.noMaskRigs = rigsBuilt; }
out.reducedTimers = page({ figures: ['maya'], reduced: true }).timersAtLoad;
out.hiddenTimers = page({ figures: ['maya'], hidden: true }).timersAtLoad;
out.offTimers = page({ figures: ['maya'], off: true }).timersAtLoad;
out.normalTimers = page({ figures: ['maya'] }).timersAtLoad;
{ const p = page({ figures: ['maya'] }); out.enabledDefault = p.life.enabled();
  out.offNow = p.life.setEnabled(false); out.storedOff = p.win.localStorage.getItem('uallak_life');
  out.onAgain = p.life.setEnabled(true); out.storedOn = p.win.localStorage.getItem('uallak_life'); }
{ const p = page({ figures: ['maya'] }); const f = p.figures[0];
  out.first = p.life.celebrate(f, 'action:7'); out.canvasesAfterFirst = p.canvases.length;
  out.second = p.life.celebrate(f, 'action:7'); out.hop = log.some(e => e[1] === 'al-hop'); }
{ const p = page({ figures: ['maya'], reduced: true }); out.reducedBurst = p.life.celebrate(p.figures[0], 'action:8');
  out.reducedCanvases = p.canvases.length; out.reducedRemembered = p.life.celebrate(p.figures[0], 'action:8'); }
{ const p = page({ figures: ['maya'], off: true }); out.offBurst = p.life.celebrate(p.figures[0], 'action:9'); }
{ const p = page({ figures: ['maya'], hidden: true }); out.hiddenBurst = p.life.celebrate(p.figures[0], 'action:10');
  out.hiddenCanvases = p.canvases.length; p.doc.hidden = false; p.doc._l.visibilitychange();
  const run = timers.slice(); timers.length = 0; run.forEach(g => { try { g(); } catch (e) {} });
  out.backCanvases = p.canvases.length; out.backAgain = p.life.celebrate(p.figures[0], 'action:10'); }
{ const p = page({ figures: [] }); out.noOrigin = p.life.celebrate(null, 'export:1'); out.noOriginCanvases = p.canvases.length;
  out.hiddenOrigin = (endBurst(), p.life.celebrate({ getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0, bottom: 0 }) }, 'export:2'));
  out.storedKeys = JSON.parse(p.win.localStorage.getItem('uallak_life_done')); }
{ // several finish at once: ONE burst
  const p = page({ figures: [] }); const got = [];
  got.push(p.life.celebrate(null, 't:1')); clock += 300; got.push(p.life.celebrate(null, 't:2')); clock += 300; got.push(p.life.celebrate(null, 't:3'));
  out.together = { got, canvases: p.canvases.length, shown }; endBurst(); out.together.after = p.canvases.length; out.together.shownAfter = shown; }
{ // one finishes while another's burst is on screen: queued, never stacked
  const p = page({ figures: [] }); const got = [], most = [];
  got.push(p.life.celebrate(null, 'q:1')); most.push(p.canvases.length);
  clock += 2000; got.push(p.life.celebrate(null, 'q:2')); most.push(p.canvases.length);
  clock += 100; got.push(p.life.celebrate(null, 'q:3')); most.push(p.canvases.length);
  endBurst(); most.push(p.canvases.length); const afterFirst = shown;
  endBurst(); most.push(p.canvases.length);
  endBurst();
  out.queue = { got, most, afterFirst, total: shown, left: p.canvases.length }; }
{ // the same event reported twice (a connection + the onboarding step it completes)
  const p = page({ figures: [] }); const got = [];
  got.push(p.life.celebrate(null, 'connected:meta:2026-11-15', { group: 'connection' })); endBurst();
  clock += 5000; got.push(p.life.celebrate(null, 'ob:27:3', { group: 'connection' }));
  clock += 30000; got.push(p.life.celebrate(null, 'ob:27:4', { group: 'connection' }));
  out.group = { got, shown }; }
{ // a reload: the browser remembers
  const m = {}; const local = { getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: k => { delete m[k]; } };
  const a = page({ figures: [], local }); const first = a.life.celebrate(null, 'action:500');
  const b = page({ figures: [], local }); out.reload = { first, again: b.life.celebrate(null, 'action:500'), other: b.life.celebrate(null, 'action:501') }; }
{ const p = page({ figures: ['maya'] });
  const card = { getBoundingClientRect: () => ({ left: 10, top: 10, width: 200, height: 80 }),
                 closest: () => ({ classList: { contains: () => true }, querySelector: () => p.figures[0] }) };
  out.fromFigure = p.life.celebrateFor(card, 'action:11'); out.figureHopped = log.some(e => e[0] === 'maya' && e[1] === 'al-hop'); }
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed — the browser script can't run here")
class Behaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run([NODE, "-e", HARNESS, JS_PATH], capture_output=True, text=True, encoding="utf-8",
                             timeout=120)
        if run.returncode != 0:
            raise AssertionError(run.stderr[-2000:])
        cls.out = json.loads(run.stdout.strip().splitlines()[-1])
        cls.css = _src("dashboard", "assets", "agent-life.css")

    def moves_of(self, name):
        return {"al-" + c["move"] for c in self.out["cast"][name]}

    # ── characters ──
    def test_all_seven_characters_have_their_own_set(self):
        cast = self.out["cast"]
        for name in CHARACTERS:
            self.assertIn(name, cast)
            self.assertGreaterEqual(len(cast[name]), 7, name)
            self.assertTrue(any(c["bubble"] for c in cast[name]), name)
            self.assertNotIn("turn", [c["move"] for c in cast[name]], name)       # pair-only behaviours
        sets = [frozenset(self.moves_of(n)) for n in CHARACTERS]
        self.assertEqual(len(set(sets)), len(CHARACTERS))                         # no two characters alike
        # what each one does MOST is its own
        favourites = {n: max(cast[n], key=lambda c: c["weight"])["move"] for n in CHARACTERS}
        self.assertEqual(favourites["elinor"], "hairflip")
        self.assertEqual(favourites["lior"], "groove")
        self.assertEqual(favourites["yoav"], "rock")
        self.assertEqual(favourites["uri"], "bounce")
        self.assertEqual(favourites["nissim"], "stretch")
        self.assertIn(("lookdown", "⌚"), [(c["move"], c["bubble"]) for c in cast["johnny"]])     # checks his watch
        self.assertIn(("lookdown", "💅"), [(c["move"], c["bubble"]) for c in cast["elinor"]])     # looks at her nails
        self.assertIn("hairflip", [c["move"] for c in cast["maya"]])

    def test_each_picture_has_its_own_cut(self):
        for name in CHARACTERS:
            neck, hip, centre = self.out["rig"][name]
            self.assertTrue(18 <= neck <= 28 and 42 <= hip <= 56 and 44 <= centre <= 56, name)
            rig = self.out["rigs"][name]
            self.assertEqual(rig["built"], 1, name)                               # built once, reused
            self.assertEqual(rig["props"], {"--al-neck": f"{neck}%", "--al-hip": f"{hip}%", "--al-px": f"{centre}%"})
            self.assertEqual(rig["layers"], ["al-legs", "al-upper", "al-emote"])

    def test_every_behaviour_exists_in_the_stylesheet_with_the_same_duration(self):
        for move, ms in self.out["moves"].items():
            rules = re.findall(r"\.cw-figure-body\.al-%s(?: \.al-\w+)? \{ animation: \w+ ([\d.]+)s" % move, self.css)
            self.assertTrue(rules, move)
            for seconds in rules:
                self.assertEqual(round(float(seconds) * 1000), ms, move)
        used = {c["move"] for cast in self.out["cast"].values() for c in cast}
        for scene in self.out["scenes"]:
            used |= {scene["a"][0], scene["b"][0], scene["then"][1]}
        self.assertTrue(used <= set(self.out["moves"]))
        self.assertTrue(set(self.out["whole"]) <= set(self.out["moves"]))

    # ── playing ──
    def test_a_figure_plays_only_its_own_character(self):
        for name in CHARACTERS:
            played = self.out["solo"][name]
            self.assertGreaterEqual(len(played), 100, name)
            self.assertTrue(set(played) <= self.moves_of(name), name)
            self.assertGreaterEqual(len(set(played)), 6, name)                    # varied
            self.assertFalse(any(a == b for a, b in zip(played, played[1:])), name)   # never twice in a row
        self.assertTrue(set(self.out["solo"]["stranger"]) <= self.moves_of("any"))
        self.assertTrue(self.out["solo"]["stranger"])

    def test_two_side_by_side_interact(self):
        played = self.out["two"]
        self.assertEqual({name for name, _cls in played}, {"maya", "lior"})
        self.assertTrue({cls for _n, cls in played} & PAIR_ONLY)                  # they turn / lean to each other
        # an answer follows: the neighbour plays right after the one who started
        turns = [i for i, (_n, cls) in enumerate(played) if cls == "al-turn"]
        self.assertTrue(any(i + 1 < len(played) and played[i + 1][0] != played[i][0] for i in turns))

    def test_alone_or_far_apart_nobody_gestures_at_nobody(self):
        for name in CHARACTERS:
            self.assertFalse(set(self.out["solo"][name]) & PAIR_ONLY, name)
        self.assertFalse({cls for _n, cls in self.out["far"]} & PAIR_ONLY)
        self.assertEqual(self.out["none"], [])

    def test_never_during_an_active_conversation(self):
        self.assertEqual(self.out["openChat"], [])
        self.assertEqual(self.out["pending"], [])

    def test_an_agent_that_is_working_stands_still(self):
        self.assertTrue(self.out["working"])
        self.assertEqual({name for name, _cls in self.out["working"]}, {"maya"})

    def test_without_masks_only_whole_figure_behaviours(self):
        self.assertEqual(self.out["noMaskRigs"], 0)
        self.assertTrue(self.out["noMask"])
        self.assertTrue({cls for _n, cls in self.out["noMask"]} <= {"al-" + m for m in self.out["whole"]})

    def test_nothing_is_scheduled_when_motion_is_unwanted(self):
        self.assertEqual(self.out["reducedTimers"], 0)
        self.assertEqual(self.out["hiddenTimers"], 0)
        self.assertEqual(self.out["offTimers"], 0)
        self.assertEqual(self.out["normalTimers"], 1)

    def test_the_switch_is_kept(self):
        self.assertTrue(self.out["enabledDefault"])
        self.assertFalse(self.out["offNow"])
        self.assertEqual(self.out["storedOff"], "off")
        self.assertTrue(self.out["onAgain"])
        self.assertIsNone(self.out["storedOn"])

    # ── celebration ──
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

    def test_finished_while_the_tab_was_hidden_celebrates_once_on_return(self):
        self.assertTrue(self.out["hiddenBurst"])
        self.assertEqual(self.out["hiddenCanvases"], 0)       # nothing is drawn on a hidden tab
        self.assertEqual(self.out["backCanvases"], 1)         # one burst when they are back
        self.assertFalse(self.out["backAgain"])

    def test_anywhere_not_only_from_a_waiting_agent(self):
        self.assertTrue(self.out["noOrigin"])                 # no element at all: still celebrated
        self.assertEqual(self.out["noOriginCanvases"], 1)
        self.assertTrue(self.out["hiddenOrigin"])             # a hidden element: from the bottom of the screen
        self.assertEqual(self.out["storedKeys"], ["export:1", "export:2"])

    def test_several_finishing_at_once_are_one_burst(self):
        t = self.out["together"]
        self.assertEqual(t["got"], [True, False, False])
        self.assertEqual((t["canvases"], t["shown"]), (1, 1))
        self.assertEqual((t["after"], t["shownAfter"]), (0, 1))                   # and nothing follows

    def test_one_finishing_during_a_burst_is_queued_never_stacked(self):
        q = self.out["queue"]
        self.assertEqual(q["got"], [True, True, True])
        self.assertLessEqual(max(q["most"]), 1)                                   # never two canvases
        self.assertEqual(q["afterFirst"], 2)                                      # the queued one plays after
        self.assertEqual((q["total"], q["left"]), (2, 0))                         # three tasks, two bursts

    def test_a_connection_and_its_onboarding_step_are_one_celebration(self):
        self.assertEqual(self.out["group"]["got"], [True, False, True])
        self.assertEqual(self.out["group"]["shown"], 2)

    def test_never_again_after_a_reload(self):
        self.assertEqual(self.out["reload"], {"first": True, "again": False, "other": True})

    def test_it_rises_from_the_waiting_agent(self):
        self.assertTrue(self.out["fromFigure"])
        self.assertTrue(self.out["figureHopped"])


if __name__ == "__main__":
    unittest.main()
